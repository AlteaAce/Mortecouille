"""
Extraction de dates multilingue (FR / DE / IT / EN) depuis du texte libre.

Formats gérés (exemples réels rencontrés sur les sites de châteaux) :
    "Samedi 28 Novembre 2026"            "Sam. 28 Nov. 2026, Dim. 29 Nov. 2026"
    "Du 22 octobre 2026 au 25 janvier 2027"
    "1 au 13 décembre 2026"              "30 mai au 1er juin 2026"
    "4, 8, 11, 15, 18 et 25 novembre"    "11. September - 13. September"
    "Donnerstag, 26. November 2026, 19.00 Uhr"
    "21.11.2026 - 10.01.2027"            "12.-14.06.2026"
    "14 août 2027 > 15 août 2027"        "June 12-14, 2026"
    "2026-06-12T10:00:00+02:00"

API principale :
    parse_date_range(text)  -> (date_debut, date_fin) en ISO, ou (None, None)
    find_date_clusters(text) -> liste de groupes de dates contiguës
    parse_iso(value)        -> date ISO depuis une valeur structurée (JSON-LD, <time>)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Europe/Zurich")
except Exception:  # tzdata absent (Windows sans le paquet tzdata)
    LOCAL_TZ = None

# ---------------------------------------------------------------------------
# Vocabulaire (sans accents, en minuscules : le texte est normalisé pareil)
# ---------------------------------------------------------------------------
_MONTH_NAMES = {
    1: ["janvier", "janv", "januar", "janner", "jaenner", "gennaio", "genn", "january", "jan"],
    2: ["fevrier", "fevr", "fev", "februar", "febbraio", "febb", "february", "feb"],
    3: ["mars", "marz", "maerz", "mrz", "marzo", "march", "mar"],
    4: ["avril", "avr", "april", "aprile", "apr"],
    5: ["mai", "maggio", "magg", "may"],
    6: ["juin", "juni", "giugno", "giu", "june", "jun"],
    7: ["juillet", "juill", "juil", "juli", "luglio", "lug", "july", "jul"],
    8: ["aout", "august", "agosto", "aug"],
    9: ["septembre", "sept", "september", "settembre", "sep"],
    10: ["octobre", "oktober", "ottobre", "october", "okt", "oct", "ott"],
    11: ["novembre", "november", "nov"],
    12: ["decembre", "dezember", "dicembre", "december", "dez", "dec", "dic"],
}
MONTHS = {name: num for num, names in _MONTH_NAMES.items() for name in names}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

_WEEKDAYS = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
    "lun", "mar", "mer", "jeu", "ven", "sam", "dim",
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag",
    "mo", "di", "mi", "do", "fr", "sa", "so",
    "lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
]
_WEEKDAY_RE = "|".join(sorted(set(_WEEKDAYS), key=len, reverse=True))

# Mots qui peuvent relier deux dates d'un même événement (plage ou liste)
_CONNECTORS = [
    "du", "au", "a", "et", "le", "les", "jusqu'au", "jusqu au", "des",
    "vom", "bis", "und", "am", "ab", "zum",
    "dal", "al", "e", "il", "fino",
    "from", "to", "until", "and", "the", "on",
]
_CONNECTOR_RE = "|".join(sorted(_CONNECTORS, key=len, reverse=True))

# Ce qui peut se trouver entre deux dates d'un même groupe
_GAP_RE = re.compile(
    rf"^(?:[\s|,;:.\-/>&+]|\b(?:{_WEEKDAY_RE}|{_CONNECTOR_RE})\b)*$"
)
# Liste de jours "nus" juste avant une date complète : "4, 8, 11 et " / "du 1 au " / "12.-"
_BARE_DAYS_RE = re.compile(
    rf"((?:(?<![\d:h.])(?:[0-3]?\d)\.?\s*(?:,|-|>|/|&|\b(?:et|und|e|and|au|a|bis|al|to)\b)\s*"
    rf"(?:\b(?:{_WEEKDAY_RE})\b\.?\s*)?)+)$"
)
_BARE_DAY_RE = re.compile(r"(?<![\d:h.])([0-3]?\d)\.?")
_RANGE_WORDS = re.compile(r"-|>|\b(?:au|bis|al|to|a)\b")

# --- Formes de dates complètes --------------------------------------------
_PATTERNS = [
    # ISO : 2026-06-12 (éventuellement suivi d'une heure)
    ("iso", re.compile(r"\b(?P<y>(?:19|20)\d{2})-(?P<m>[01]\d)-(?P<d>[0-3]\d)\b")),
    # Numérique : 12.06.2026, 12/06/2026, 12.06.26
    ("num", re.compile(
        r"(?<![\d.])(?P<d>[0-3]?\d)[./](?P<m>[01]?\d)[./](?P<y>(?:19|20)?\d{2})(?![\d])")),
    # Numérique sans année : 12.06. (point final obligatoire pour éviter les heures)
    ("num", re.compile(r"(?<![\d.])(?P<d>[0-3]?\d)\.(?P<m>[01]?\d)\.(?![\d])")),
    # Textuel jour-mois : 12 juin 2026, 12. Juni, 28 nov. 2026
    ("txt", re.compile(
        rf"(?<![\d])(?P<d>[0-3]?\d)\.?\s*(?:de\s+)?(?P<m>{_MONTH_RE})\b\.?"
        rf"(?:\s*,?\s*(?P<y>\d{{4}})\b)?")),
    # Anglais mois-jour : June 12, 2026 / June 12-14, 2026
    ("en", re.compile(
        rf"\b(?P<m>{_MONTH_RE})\b\.?\s+(?P<d>[0-3]?\d)(?:\s*-\s*(?P<d2>[0-3]?\d))?"
        rf"\s*,\s*(?P<y>\d{{4}})\b")),
]


@dataclass
class _Mention:
    start: int
    end: int
    day: int
    month: int
    year: int | None


def normalize_text(text: str) -> str:
    """Minuscules, sans accents, tirets unifiés, ordinaux simplifiés."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[‐-―−]", "-", text)   # tirets typographiques
    text = text.replace(" ", " ").replace("’", "'")
    text = re.sub(r"(?<=\d)(?:er|eme|e|st|nd|rd|th)\b", "", text)  # 1er, 2e, 3rd
    text = re.sub(r"[ \t\r\n]+", " ", text)
    return text


def _year4(y: str | None) -> int | None:
    if not y:
        return None
    n = int(y)
    return n + 2000 if n < 100 else n


def _valid(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _find_mentions(text: str) -> list[_Mention]:
    """Trouve toutes les dates (complètes ou partielles) dans un texte normalisé."""
    raw: list[_Mention] = []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            month = MONTHS[m["m"]] if kind in ("txt", "en") else int(m["m"])
            day = int(m["d"])
            year = _year4(m["y"])
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            raw.append(_Mention(m.start(), m.end(), day, month, year))
            if kind == "en" and m["d2"]:
                raw.append(_Mention(m.end(), m.end(), int(m["d2"]), month, year))

    # Retire les chevauchements : on garde la correspondance la plus longue
    raw.sort(key=lambda x: (x.start, -(x.end - x.start)))
    mentions: list[_Mention] = []
    for mt in raw:
        if mentions and mt.start < mentions[-1].end and mt.start != mt.end:
            continue
        mentions.append(mt)

    # Ajoute les jours "nus" qui précèdent une date : "4, 8 et 25 novembre"
    extra: list[_Mention] = []
    prev_end = 0
    for mt in mentions:
        before = text[prev_end:mt.start]
        bm = _BARE_DAYS_RE.search(before)
        if bm:
            chunk = bm.group(1)
            chunk_start = prev_end + bm.start(1)
            is_range = bool(_RANGE_WORDS.search(chunk))
            for dm in _BARE_DAY_RE.finditer(chunk):
                d = int(dm.group(1))
                if not 1 <= d <= 31:
                    continue
                month, year = mt.month, mt.year
                # "du 28 au 3 janvier" : le premier jour est dans le mois précédent
                if is_range and d > mt.day:
                    month -= 1
                    if month == 0:
                        month, year = 12, (year - 1 if year else None)
                extra.append(_Mention(chunk_start + dm.start(), chunk_start + dm.end(),
                                      d, month, year))
        prev_end = mt.end
    mentions.extend(extra)
    mentions.sort(key=lambda x: x.start)
    return mentions


def _group(text: str, mentions: list[_Mention]) -> list[list[_Mention]]:
    """Regroupe les dates séparées uniquement par des connecteurs/jours de semaine."""
    groups: list[list[_Mention]] = []
    for mt in mentions:
        if groups:
            prev = groups[-1][-1]
            gap = text[prev.end:mt.start]
            if len(gap) <= 40 and _GAP_RE.match(gap):
                groups[-1].append(mt)
                continue
        groups.append([mt])
    return groups


def _resolve_years(group: list[_Mention], today: date) -> tuple[list[date], bool]:
    """Complète les années manquantes. Retourne (dates, année_devinée)."""
    years = [m.year for m in group]
    # Propage l'année connue la plus proche vers l'arrière ("30 mai au 1er juin 2026")
    nxt = None
    for i in range(len(group) - 1, -1, -1):
        if years[i] is None and nxt is not None:
            yr = nxt[0]
            # "28 déc. au 3 janv. 2027" : on recule d'un an si on passe au-delà
            if (group[i].month, group[i].day) > (nxt[1], nxt[2]):
                yr -= 1
            years[i] = yr
        if years[i] is not None:
            nxt = (years[i], group[i].month, group[i].day)
    # Puis vers l'avant ("12 juin 2026 et 3 juillet")
    prev = None
    for i in range(len(group)):
        if years[i] is None and prev is not None:
            yr = prev[0]
            if (group[i].month, group[i].day) < (prev[1], prev[2]):
                yr += 1
            years[i] = yr
        if years[i] is not None:
            prev = (years[i], group[i].month, group[i].day)

    guessed = False
    if all(y is None for y in years):
        # Aucune année : on prend l'année courante, sauf si tout le groupe
        # est déjà passé, auquel cas il s'agit de l'an prochain.
        guessed = True
        base = today.year
        candidate = []
        for m in group:
            y = base
            if candidate and (m.month, m.day) < (candidate[-1].month, candidate[-1].day):
                y += 1  # passage de décembre à janvier dans le groupe
            if _valid(y, m.month, m.day):
                candidate.append(date(y, m.month, m.day))
        if candidate and max(candidate) < today:
            return _resolve_years_with_base(group, base + 1), guessed
        return candidate, guessed

    out = []
    for m, y in zip(group, years):
        if y and _valid(y, m.month, m.day):
            out.append(date(y, m.month, m.day))
    return out, guessed


def _resolve_years_with_base(group: list[_Mention], base: int) -> list[date]:
    out: list[date] = []
    y = base
    for m in group:
        if out and (m.month, m.day) < (out[-1].month, out[-1].day):
            y += 1
        if _valid(y, m.month, m.day):
            out.append(date(y, m.month, m.day))
    return out


@dataclass
class DateCluster:
    dates: list[date]
    year_guessed: bool

    @property
    def start(self) -> date:
        return min(self.dates)

    @property
    def end(self) -> date:
        return max(self.dates)


def find_date_clusters(text: str, today: date | None = None) -> list[DateCluster]:
    """Retourne les groupes de dates trouvés, dans l'ordre d'apparition."""
    if not text:
        return []
    today = today or date.today()
    norm = normalize_text(text)
    clusters = []
    for group in _group(norm, _find_mentions(norm)):
        dates, guessed = _resolve_years(group, today)
        # Écarte les années absurdes (dates historiques : "bataille de 1476")
        dates = [d for d in dates if today.year - 2 <= d.year <= today.year + 3]
        if dates:
            clusters.append(DateCluster(sorted(set(dates)), guessed))
    return clusters


def parse_date_range(text: str, today: date | None = None) -> tuple[str | None, str | None]:
    """Première plage de dates trouvée dans le texte, au format ISO."""
    clusters = find_date_clusters(text, today)
    if not clusters:
        return None, None
    c = clusters[0]
    return c.start.isoformat(), c.end.isoformat()


def parse_iso(value: str | None) -> str | None:
    """
    Convertit une valeur structurée (JSON-LD startDate, <time datetime>, API)
    en date ISO locale. Les horodatages UTC sont convertis en heure suisse
    pour éviter le décalage d'un jour (ex. 2026-06-12T23:30Z = 13 juin à Zurich).
    """
    if not value:
        return None
    value = str(value).strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None and LOCAL_TZ is not None:
            dt = dt.astimezone(LOCAL_TZ)
        return dt.date().isoformat()
    except ValueError:
        pass
    m = re.search(r"\b((?:19|20)\d{2})-([01]\d)-([0-3]\d)\b", value)
    if m and _valid(int(m[1]), int(m[2]), int(m[3])):
        return m.group(0)
    start, _ = parse_date_range(value)
    return start

