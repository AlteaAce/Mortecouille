#!/usr/bin/env python3
"""
Agrégateur d'événements médiévaux en Suisse et en France voisine.
Génère un fichier events.json lisible par index.html.

Usage :
    python scraper.py                    # toutes les sources
    python scraper.py --only chillon -v  # une source (filtre sur le nom), logs détaillés
    python scraper.py --dry-run          # n'écrit pas events.json

Variables d'environnement :
    OPENAGENDA_KEY   clé publique OpenAgenda (gratuite, compte requis) ; sans elle,
                     la source OpenAgenda est ignorée.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dateparse import find_date_clusters, normalize_text, parse_date_range, parse_iso

log = logging.getLogger("scraper")

# ---------------------------------------------------------------------------
# Configuration générale
# ---------------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (compatible; MedievalEventBot/2.0; +https://mortecouille.ch)"
)
REQUEST_TIMEOUT = 20
DELAY_PER_HOST = 1.5      # secondes minimum entre deux requêtes vers le même site
MAX_DETAIL_PAGES = 40     # pages de détail max. visitées par source
OUTPUT_FILE = Path(__file__).parent / "events.json"

# Thématique médiévale. Les termes sont comparés au texte normalisé
# (minuscules, sans accents). Les noms de lieux ne sont volontairement PAS
# des mots-clés : "Thun" ou "Morges" laissaient passer n'importe quel concert.
THEME_PATTERNS = [
    r"mediev", r"medioev", r"moyen[ -]age", r"mittelalter",
    r"\bchevalier", r"\bchevalerie", r"\britter", r"\btemplier",
    r"\btroubadour", r"\bmenestrel", r"\bfauconn", r"\bfalkner",
    r"\bjoutes?\b(?! nautique)", r"\btjost", r"\btournoi de chevalerie",
    r"\bschwertkampf", r"\bcombat a l'epee", r"\bescrime (?:medievale|ancienne|historique)",
    r"\breconstitution historique", r"\bliving history", r"\bburgfest",
    r"\bcampement", r"\bheraldi", r"\barmoiri", r"\bcotte de mailles",
]
THEME_RE = re.compile("|".join(THEME_PATTERNS))


def is_medieval(text: str) -> bool:
    return bool(THEME_RE.search(normalize_text(text)))


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------
@dataclass
class Event:
    name: str
    date_start: str | None
    date_end: str | None
    location: str
    url: str
    source: str
    country: str = "CH"
    year_guessed: bool = False   # True si l'année n'était pas indiquée sur la page

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["year_guessed"]:
            del d["year_guessed"]
        return d


# ---------------------------------------------------------------------------
# HTTP : session avec retries + politesse par site + cache mémoire
# ---------------------------------------------------------------------------
class Http:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-CH,fr;q=0.9,de;q=0.8,it;q=0.7,en;q=0.5",
        })
        retry = Retry(total=3, backoff_factor=1.5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET",))
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._last_hit: dict[str, float] = {}
        self._cache: dict[str, BeautifulSoup | None] = {}

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        elapsed = time.monotonic() - self._last_hit.get(host, 0)
        if elapsed < DELAY_PER_HOST:
            time.sleep(DELAY_PER_HOST - elapsed)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response | None:
        self._wait(url)
        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log.warning("GET %s → %s", url, e)
            return None

    def soup(self, url: str) -> BeautifulSoup | None:
        if url not in self._cache:
            r = self.get(url)
            # r.content : laisse BeautifulSoup détecter l'encodage (meta charset),
            # plus fiable que r.text quand le serveur n'annonce pas de charset.
            self._cache[url] = BeautifulSoup(r.content, "lxml") if r else None
        return self._cache[url]


# ---------------------------------------------------------------------------
# Extraction : données structurées
# ---------------------------------------------------------------------------
def _iter_jsonld(soup: BeautifulSoup) -> Iterable[dict]:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                stack.extend(v for k, v in node.items() if k in ("@graph", "itemListElement", "item", "subEvent"))


def _is_event_type(node: dict) -> bool:
    types = node.get("@type", [])
    types = [types] if isinstance(types, str) else types
    return any(t.endswith("Event") or t == "Festival" for t in types)


def _jsonld_location(node: dict) -> str:
    loc = node.get("location")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, str):
        return loc
    if isinstance(loc, dict):
        parts = [loc.get("name", "")]
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts.append(addr.get("addressLocality", ""))
        elif isinstance(addr, str):
            parts.append(addr)
        return ", ".join(p for p in parts if p)
    return ""


def jsonld_events(soup: BeautifulSoup) -> list[dict]:
    """Événements schema.org (JSON-LD) présents dans la page."""
    out = []
    for node in _iter_jsonld(soup):
        if _is_event_type(node) and node.get("startDate"):
            out.append({
                "name": str(node.get("name", "")).strip(),
                "date_start": parse_iso(node.get("startDate")),
                "date_end": parse_iso(node.get("endDate")),
                "location": _jsonld_location(node),
                "url": node.get("url", ""),
            })
    return out


def structured_dates(soup: Tag) -> tuple[str | None, str | None]:
    """Dates via microdata (itemprop) ou balises <time datetime>."""
    start = soup.select_one('[itemprop="startDate"]')
    if start:
        end = soup.select_one('[itemprop="endDate"]')
        s = parse_iso(start.get("content") or start.get("datetime") or start.get_text(" ", strip=True))
        e = parse_iso(end.get("content") or end.get("datetime") or end.get_text(" ", strip=True)) if end else None
        if s:
            return s, e or s
    times = [parse_iso(t["datetime"]) for t in soup.select("time[datetime]")]
    times = sorted(t for t in times if t)
    if times:
        return times[0], times[-1]
    return None, None


# ---------------------------------------------------------------------------
# Extraction : dates en texte libre
# ---------------------------------------------------------------------------
# Libellés qui annoncent la date sur une page de détail, par ordre de préférence
DATE_LABELS = [
    r"toutes les dates", r"alle (?:termine|daten)", r"tutte le date", r"all dates",
    r"dates?", r"datum", r"daten", r"termine?", r"wann", r"quand", r"data", r"date e orari",
    r"when", r"prochaine date", r"nachster termin",
]
# Le libellé doit ouvrir un segment court : "Date |", "Dates et horaires |", "Termine |"
_LABEL_RES = [re.compile(rf"(?:^|\|)\s*{lbl}\b[^|]{{0,30}}\|", re.I) for lbl in DATE_LABELS]

# Zones de page qui ne décrivent pas l'événement
NOISE_SELECTORS = (
    "script, style, noscript, nav, header, footer, form, aside, [role=navigation], "
    "[role=search], [class*=cookie], [id*=cookie], [class*=search], [id*=search], "
    "[class*=breadcrumb], [class*=newsletter]"
)


def text_segments(el: Tag) -> str:
    """Texte d'un élément, blocs séparés par ' | ' (utile pour les libellés)."""
    return el.get_text(" | ", strip=True)


def dates_from_text(text: str) -> tuple[str | None, str | None, bool]:
    clusters = find_date_clusters(text)
    if not clusters:
        return None, None, False
    c = clusters[0]
    return c.start.isoformat(), c.end.isoformat(), c.year_guessed


def next_occurrence(text: str, today: date | None = None) -> tuple[str | None, str | None, bool]:
    """
    Pour la page dédiée à un événement récurrent (site d'une fête, page
    d'organisateur) : la prochaine édition, plutôt que la première date venue.
    Ces pages affichent souvent encore l'édition passée, ou des dates de
    programme réparties sur plusieurs week-ends.

      - on ne garde que les groupes de dates à venir ;
      - une date sans année déjà passée cette année est écartée : sur ce type
        de page, « 5 septembre » en novembre décrit l'édition passée, pas la
        suivante (constaté sur plusieurs sites de fêtes) ;
      - on préfère les dates dont l'année est écrite ;
      - on fusionne les groupes proches (≤ 31 jours) : « 15-17 mai … 22-23 mai ».
    """
    today = today or date.today()
    all_future = [c for c in find_date_clusters(text, today) if c.end >= today]
    sure = [c for c in all_future if not c.year_guessed]
    # Année devinée ET reportée à l'an prochain : probablement l'édition passée
    plausible = [c for c in all_future if not (c.year_guessed and c.start.year > today.year)]
    if not plausible:
        return None, None, False
    base = (sure or plausible)[0]
    # Autour d'une date sûre, les dates sans année voisines sont les autres jours
    # de la même édition (« 22-23 mai 2027 … samedis 15 et 22 mai ») : on les garde.
    neighbours = all_future if not base.year_guessed else plausible
    start, end = base.start, base.end
    for c in neighbours:
        if c is not base and c.start <= end + timedelta(days=31) and c.end >= start - timedelta(days=31):
            start, end = min(start, c.start), max(end, c.end)
    return start.isoformat(), end.isoformat(), base.year_guessed


def segment_after_date(text: str) -> str | None:
    """Bloc de texte court qui suit le premier bloc daté (souvent le lieu)."""
    segments = [s.strip() for s in text.split(" | ")]
    for i, seg in enumerate(segments[:-1]):
        if parse_date_range(seg)[0]:
            nxt = segments[i + 1]
            if 2 < len(nxt) <= 80 and not parse_date_range(nxt)[0]:
                return nxt
            return None
    return None


def clean_main_text(soup: BeautifulSoup) -> tuple[BeautifulSoup, str]:
    """Copie nettoyée (sans menus, cookies, recherche…) du contenu principal."""
    # Copie pour ne pas abîmer le cache HTTP
    clean = BeautifulSoup(str(soup.select_one("main") or soup.body or soup), "lxml")
    for el in clean.select(NOISE_SELECTORS):
        el.decompose()
    return clean, text_segments(clean)


def dates_from_detail_page(soup: BeautifulSoup, prefer_future: bool = False
                           ) -> tuple[str | None, str | None, bool]:
    """
    Stratégie sur une page de détail, de la plus fiable à la moins fiable :
      1. JSON-LD Event   2. microdata / <time>   3. texte après un libellé « Date »
      4. premier groupe de dates du contenu principal

    prefer_future=True (page d'un événement récurrent) : on cherche la prochaine
    édition dans tout le contenu au lieu de la première date trouvée.
    """
    today = date.today().isoformat()
    for ev in jsonld_events(soup):
        if ev["date_start"] and (not prefer_future or (ev["date_end"] or ev["date_start"]) >= today):
            return ev["date_start"], ev["date_end"] or ev["date_start"], False

    s, e = structured_dates(soup)
    if s and (not prefer_future or (e or s) >= today):
        return s, e, False

    clean, text = clean_main_text(soup)
    if prefer_future:
        return next_occurrence(text)

    for rx in _LABEL_RES:
        for m in rx.finditer(text):
            s, e, guessed = dates_from_text(text[m.end(): m.end() + 400])
            if s:
                return s, e, guessed

    # Repli : premier groupe de dates autour du titre de la page (la date est
    # souvent juste au-dessus ou en dessous du <h1>), pour ne pas attraper une
    # date de menu, de bandeau ou de teaser de blog placé plus haut.
    h1 = clean.select_one("h1")
    h1_text = h1.get_text(" | ", strip=True) if h1 else ""
    pos = text.find(h1_text) if h1_text else -1
    if pos < 0:
        return dates_from_text(text[:3000])
    return dates_from_text(text[max(0, pos - 120): pos + 3000])


# ---------------------------------------------------------------------------
# Extraction : cartes d'événements dans une page de liste
# ---------------------------------------------------------------------------
GENERIC_LINK_TEXT = re.compile(
    r"^(voir|en savoir plus|plus d'infos?|details?|mehr|» mehr|weiterlesen|read more|more|"
    r"scopri di piu|dettagli|voir l'evenement|zum event|infos?)\W*$", re.I)


def find_cards(soup: BeautifulSoup, base_url: str, link_re: re.Pattern) -> list[tuple[str, Tag]]:
    """
    Repère les "cartes" d'événements sans dépendre des classes CSS (qui changent
    à chaque refonte) : on part de chaque lien vers une page d'événement et on
    remonte au plus grand ancêtre qui ne contient pas d'autre lien d'événement.
    """
    def ev_links(el: Tag) -> set[str]:
        return {
            urljoin(base_url, a["href"]).split("#")[0]
            for a in el.select("a[href]")
            if link_re.search(urljoin(base_url, a["href"]))
        }

    cards: dict[str, Tag] = {}
    for a in soup.select("a[href]"):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if not link_re.search(href) or href in cards:
            continue
        el: Tag = a
        while el.parent is not None and el.parent.name not in ("body", "html", "[document]", "main"):
            parent = el.parent
            # Stop si le parent contient d'autres événements, ou s'il est bien trop
            # gros pour être une carte (page avec un seul événement listé)
            if len(ev_links(parent)) > 1 or len(parent.get_text(" ", strip=True)) > 1200:
                break
            el = parent
        cards[href] = el
    return list(cards.items())


def card_title(card: Tag, url: str) -> str:
    for sel in ("h1, h2, h3, h4, h5", "[class*=title], [class*=titre], [class*=name]", "strong, b"):
        for el in card.select(sel):
            t = el.get_text(" ", strip=True)
            if t and not GENERIC_LINK_TEXT.match(normalize_text(t)) and not parse_date_range(t)[0]:
                return t
    if card.name == "a":
        t = card.get_text(" ", strip=True)
        if t and not GENERIC_LINK_TEXT.match(normalize_text(t)):
            return t
    for a in card.select("a[href]"):
        t = a.get_text(" ", strip=True)
        if t and not GENERIC_LINK_TEXT.match(normalize_text(t)):
            return t
    return ""


def page_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("main h1, article h1, h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        return og["content"].split(" | ")[0].split(" - ")[0].strip()
    return soup.title.get_text(strip=True).split(" | ")[0] if soup.title else ""


# ---------------------------------------------------------------------------
# Sources configurables
# ---------------------------------------------------------------------------
@dataclass
class ListingSource:
    """
    Page(s) d'agenda listant des événements avec un lien vers chaque fiche.
    La date est lue sur la carte ; si elle manque (ou si detail="always"),
    on ouvre la fiche de l'événement.
    """
    name: str
    urls: list[str]
    link_pattern: str                    # regex sur l'URL absolue d'une fiche
    location: str
    country: str = "CH"
    detail: str = "missing"              # "missing" | "always" | "never"
    theme_filter: bool = False           # ne garder que les événements médiévaux
    card_filter: str | None = None       # regex supplémentaire sur le texte de la carte
    drop_undated: bool = False           # écarter les fiches sans date (offres permanentes)
    locations: dict[str, str] = field(default_factory=dict)  # fragment d'URL → lieu
    title_from_detail: bool = False      # titre = <h1> de la fiche (cartes peu fiables)
    prefer_future: bool = False          # fiches « permanentes » d'événements récurrents
    location_after_date: bool = False    # lieu = bloc de texte qui suit la date sur la carte

    def scrape(self, http: Http) -> list[Event]:
        link_re = re.compile(self.link_pattern)
        filter_re = re.compile(self.card_filter, re.I) if self.card_filter else None
        events: list[Event] = []
        seen: set[str] = set()
        details_fetched = 0

        for list_url in self.urls:
            soup = http.soup(list_url)
            if soup is None:
                continue

            # 1. Données structurées sur la page de liste, si le site en publie
            for ev in jsonld_events(soup):
                url = urljoin(list_url, ev["url"]) if ev["url"] else list_url
                if url in seen or not ev["name"]:
                    continue
                seen.add(url)
                events.append(Event(ev["name"], ev["date_start"], ev["date_end"],
                                    ev["location"] or self.location, url, self.name, self.country))

            # 2. Cartes HTML
            for url, card in find_cards(soup, list_url, link_re):
                if url in seen:
                    continue
                seen.add(url)
                card_text = text_segments(card)
                if filter_re and not filter_re.search(card_text):
                    continue
                title = card_title(card, url)

                start = end = loc = None
                guessed = False
                if self.detail != "always":
                    start, end = structured_dates(card)
                    t_start, t_end, t_guessed = dates_from_text(card_text)
                    if not start:
                        start, end, guessed = t_start, t_end, t_guessed
                    elif t_start == start and t_end and t_end > (end or start):
                        end = t_end   # <time> ne donne souvent que le début
                if self.location_after_date:
                    loc = segment_after_date(card_text)

                need_detail = self.detail == "always" or (self.detail == "missing" and (not start or not title))
                if need_detail and details_fetched < MAX_DETAIL_PAGES:
                    details_fetched += 1
                    page = http.soup(url)
                    if page is not None:
                        if not start:
                            start, end, guessed = dates_from_detail_page(page, self.prefer_future)
                        if not title or self.title_from_detail:
                            title = page_title(page) or title
                        ld = jsonld_events(page)
                        if ld and ld[0]["location"]:
                            loc = ld[0]["location"]
                        card_text += " " + text_segments(page.select_one("main") or page)[:2000]

                if not title:
                    log.debug("%s : carte sans titre ignorée (%s)", self.name, url)
                    continue
                if self.theme_filter and not is_medieval(title + " " + card_text):
                    continue
                if self.drop_undated and not start:
                    log.debug("%s : fiche sans date ignorée (%s)", self.name, url)
                    continue
                location = next((v for key, v in self.locations.items() if key in url),
                                loc or self.location)
                events.append(Event(title, start, end, location, url, self.name,
                                    self.country, guessed))
        return events


@dataclass
class TextListSource:
    """
    Page où chaque événement tient sur une ligne, date d'un côté, titre de l'autre :
        « Donnerstag, 26. November 2026 — Der magische Turm » (Schloss Thun)
        « Schloss Laupen: 16.–18. April 2027 »                 (Caligatus Feleus)

    item_selector=None : on découpe le texte de la page en blocs (titres,
    paragraphes…) au lieu de sélectionner des éléments. Deux options s'y ajoutent :
      year_headings          un bloc « 2026 » seul donne l'année des lignes
                             suivantes (« 16 janvier - Inauguration… »)
      location_next_segment  le bloc qui suit la ligne est le lieu
                             (« 25 et 26 avril - L'Éveil… | Martigny »)
    title_template / location_template : {} est remplacé par la partie « titre »
    de la ligne (utile quand la ligne ne donne que le lieu).
    """
    name: str
    url: str
    location: str
    item_selector: str | None = "main li, article li, .entry-content li, main p"
    country: str = "CH"
    theme_filter: bool = False
    title_template: str = "{}"
    location_template: str | None = None
    year_headings: bool = False
    location_next_segment: bool = False

    SEPARATOR = re.compile(r"\s+[—–-]\s+|:\s+")
    YEAR_HEADING = re.compile(r"(?:19|20)\d{2}")

    def _lines(self, soup: BeautifulSoup) -> Iterable[tuple[str, Tag | None, str | None]]:
        """(texte de la ligne, élément HTML ou None, bloc suivant ou None)"""
        if self.item_selector:
            for item in soup.select(self.item_selector):
                yield item.get_text(" ", strip=True), item, None
        else:
            _, text = clean_main_text(soup)
            segments = [s.strip() for s in text.split(" | ")]
            for i, line in enumerate(segments):
                yield line, None, segments[i + 1] if i + 1 < len(segments) else None

    def scrape(self, http: Http) -> list[Event]:
        soup = http.soup(self.url)
        if soup is None:
            return []
        events = []
        heading_year: str | None = None
        for text, item, next_segment in self._lines(soup):
            if self.year_headings and self.YEAR_HEADING.fullmatch(text):
                heading_year = text
                continue
            parts = self.SEPARATOR.split(text, maxsplit=1)
            if len(parts) != 2 or len(text) > 400:
                continue
            # La date peut être avant ou après le séparateur
            for date_part, title_part in ((parts[0], parts[1]), (parts[1], parts[0])):
                start, end, guessed = dates_from_text(date_part)
                if start and guessed and heading_year:
                    # Date sans année : on prend celle du titre de section
                    start, end, guessed = dates_from_text(f"{date_part} {heading_year}")
                if start:
                    break
            else:
                continue
            # Titre : jusqu'au premier point qui termine une phrase
            label = re.split(r"(?<=[a-zà-ÿ)])\.\s", title_part, maxsplit=1)[0].strip(" .")
            if not label or parse_date_range(label)[0]:
                continue
            if self.theme_filter and not is_medieval(text):
                continue
            link = item.select_one("a[href]") if item is not None else None
            url = urljoin(self.url, link["href"]) if link else self.url
            location = self.location_template.format(label) if self.location_template else self.location
            if self.location_next_segment:
                # « Titre | Lieu » sur la même ligne, sinon le bloc suivant
                # (sauf mention du type « - Quête accomplie » / « - complet »)
                label, _, inline_loc = (s.strip() for s in label.partition("|"))
                nxt = (next_segment or "").strip()
                if inline_loc:
                    location = inline_loc
                elif nxt and not nxt.startswith("-") and len(nxt) <= 80 and not dates_from_text(nxt)[0]:
                    location = nxt.strip(" |")
            events.append(Event(self.title_template.format(label), start, end, location,
                                url, self.name, self.country, guessed))
        return events


@dataclass
class WatchedPage:
    """
    Page officielle d'un événement récurrent : on y cherche la prochaine
    édition à chaque passage. Tant que la page n'affiche que l'édition passée
    (ou aucune date), elle ne produit rien : elle reste simplement surveillée,
    et l'événement apparaît dès que les organisateurs publient la nouvelle date.
    """
    name: str
    url: str
    location: str
    country: str = "CH"
    source: str = "Page suivie"

    def scrape(self, http: Http) -> list[Event]:
        soup = http.soup(self.url)
        if soup is None:
            return []
        start, end, guessed = dates_from_detail_page(soup, prefer_future=True)
        if not start:
            log.info("   %s : pas encore de date à venir", self.name)
            return []
        return [Event(self.name, start, end, self.location, self.url, self.source,
                      self.country, guessed)]


@dataclass
class MonthAgendaSource:
    """
    Agenda tenu à la main, découpé en mois, chaque événement en trois blocs :
        « Mars 2026 | 6 - 8 Mars | Marché fantastique - Château d'Oron | Oron | 13 Mars | … »
    Le titre de mois donne l'année des dates qui suivent.
    """
    name: str
    url: str
    country: str = "CH"

    MONTH_HEADING = re.compile(r"[a-z]+ ((?:19|20)\d{2})")
    # Bloc qui n'est qu'une date : chiffres, séparateurs et un nom de mois
    DATE_ONLY = re.compile(r"[\d\s.,&/+-]*(?:et|au|bis|und)?[\d\s.,&/+-]*[a-z]+\.?[\s\d.,&/+-]*")

    def scrape(self, http: Http) -> list[Event]:
        soup = http.soup(self.url)
        if soup is None:
            return []
        _, text = clean_main_text(soup)
        segs = [s.strip() for s in text.split(" | ") if s.strip()]
        events: list[Event] = []
        year: str | None = None
        i = 0
        while i < len(segs):
            seg = segs[i]
            norm = normalize_text(seg)
            heading = self.MONTH_HEADING.fullmatch(norm)
            if heading:
                year = heading.group(1)
            elif (year and len(seg) <= 40 and self.DATE_ONLY.fullmatch(norm)
                  and i + 1 < len(segs)):
                start, end, _ = dates_from_text(f"{seg} {year}")
                if start:
                    title = segs[i + 1]
                    loc = segs[i + 2] if i + 2 < len(segs) else ""
                    # Le lieu est facultatif : si le bloc suivant est déjà une date
                    # ou un titre de mois, il manque
                    if (loc and len(loc) <= 60 and not dates_from_text(f"{loc} {year}")[0]
                            and not self.MONTH_HEADING.fullmatch(normalize_text(loc))):
                        i += 1
                    else:
                        loc = ""
                    # Titres génériques (« Fête médiévale ») : on précise le lieu
                    if loc and normalize_text(loc) not in normalize_text(title):
                        title = f"{title} – {loc}"
                    events.append(Event(title, start, end, loc, self.url, self.name, self.country))
                    i += 2
                    continue
            i += 1
        return events


def scrape_burg_zug(http: Http) -> list[Event]:
    """
    Burg Zug — calendrier du musée. Les dates sont « Sa 12.12.–So 13.12. »
    (« Sat 12/12 » en anglais) sans année ; l'année vient de l'attribut
    data-dateym="2026-12" de chaque entrée. Seuls les événements médiévaux
    sont gardés (le musée propose aussi des visites, ateliers…).
    """
    url = "https://www.burgzug.ch/page/de/veranstaltungen/kalender"
    soup = http.soup(url)
    if soup is None:
        return []
    events = []
    for item in soup.select(".ctritem[data-dateym]"):
        ym = re.fullmatch(r"(\d{4})-(\d{2})", item["data-dateym"])
        date_el = item.select_one(".itemov .date h2, .date h2")
        title_el = item.select_one(".itemov .text h2, .text h2")
        if not (ym and date_el and title_el):
            continue
        ref_year, ref_month = int(ym[1]), int(ym[2])
        dates = []
        for d, m in re.findall(r"\b(\d{1,2})[./](\d{1,2})\b", date_el.get_text()):
            d, m = int(d), int(m)
            # « 24.12.–02.01. » rangé en janvier 2027 : le 24.12 est en 2026
            year = ref_year - (m - ref_month > 6) + (ref_month - m > 6)
            try:
                dates.append(date(year, m, d))
            except ValueError:
                pass
        if not dates:
            continue
        dates.sort()
        title = title_el.get_text(" ", strip=True)
        if not is_medieval(title + " " + item.get_text(" ", strip=True)):
            continue
        link = item.select_one("a[href]")
        events.append(Event(title, dates[0].isoformat(), dates[-1].isoformat(),
                            "Burg Zug, Zoug", urljoin(url, link["href"]) if link else url,
                            "Burg Zug"))
    return deduplicate(events)


# ---------------------------------------------------------------------------
# Liste des sources
# ---------------------------------------------------------------------------
# Sites retirés (vérifié le 2026-11-14) :
#   - myswitzerland.com : 403 (protection anti-bot)
#   - agenda.ch/de/suche : 404, plus de recherche publique
#   - bellinzonese-altoticino.ch : certificat expiré ; bellinzonaevalli.ch charge
#     son agenda en JavaScript (non lisible sans navigateur)
SOURCES: list = [
    ListingSource(
        name="Château de Chillon",
        urls=["https://www.chillon.ch/evenements/"],
        link_pattern=r"chillon\.ch/(?:[a-z]{2}/)?monagenda/[^/]+/?$",
        location="Château de Chillon, Veytaux VD",
        detail="always",   # la liste n'affiche pas les dates
    ),
    ListingSource(
        name="Château de Gruyères",
        urls=["https://www.chateau-gruyeres.ch/expositions-et-evenements/"],
        link_pattern=r"chateau-gruyeres\.ch/(?:expositions|evenements|agenda)/[^/]+/?$",
        location="Château de Gruyères, Gruyères FR",
        detail="always",   # les cartes de la liste mélangent plusieurs dates
        title_from_detail=True,
    ),
    ListingSource(
        name="Château de Grandson",
        urls=["https://chateau-grandson.ch/agenda/"],
        link_pattern=r"chateau-grandson\.ch/agenda/[^/]+/?$",
        location="Château de Grandson, Grandson VD",
    ),
    ListingSource(
        name="Schloss Kyburg",
        urls=["https://schlosskyburg.ch/agenda-und-virtueller-rundgang/"],
        link_pattern=r"schlosskyburg\.ch/veranstaltungen/[^/]+/?$",
        location="Schloss Kyburg, Kyburg ZH",
    ),
    ListingSource(
        name="Museum Aargau (Lenzburg, Hallwyl, Habsburg, Wildegg, Legionärspfad)",
        urls=[f"https://www.museumaargau.ch/{s}/veranstaltungen"
              for s in ("schloss-lenzburg", "schloss-hallwyl", "schloss-habsburg",
                        "schloss-wildegg", "legionaerspfad")],
        link_pattern=r"museumaargau\.ch/(?:schloss-[a-z]+|legionaerspfad)/event/[^/]+$",
        location="Argovie",
        locations={
            "/schloss-lenzburg/": "Schloss Lenzburg, Lenzburg AG",
            "/schloss-hallwyl/": "Schloss Hallwyl, Seengen AG",
            "/schloss-habsburg/": "Schloss Habsburg, Habsburg AG",
            "/schloss-wildegg/": "Schloss Wildegg, Möriken-Wildegg AG",
            "/legionaerspfad/": "Legionärspfad, Windisch AG",
        },
        detail="always",           # dates fiables dans « Termine » sur la fiche
        title_from_detail=True,    # certaines listes n'ont qu'une carte, mal délimitée
        drop_undated=True,         # offres permanentes (« Familienmuseum ») sans date
    ),
    TextListSource(
        name="Schloss Thun",
        url="https://schlossthun.ch/veranstaltungen/agenda/",
        location="Schloss Thun, Thun BE",
    ),
    scrape_burg_zug,

    # --- Organisateurs de plusieurs événements (repérés sur mortecouille.ch) ---
    ListingSource(
        name="Turnei (Hinwil, Winterthur, Wattwil, Laufen, Bubikon…)",
        urls=["https://www.turnei.ch/events-gaesteinfo", "https://www.turnei.ch"],
        link_pattern=r"turnei\.ch/events-gaesteinfo/[^/]+$",
        location="Suisse alémanique",
        location_after_date=True,  # « 19.02.2027–21.02.2027 | Eulachhallen Winterthur »
    ),
    TextListSource(
        name="Caligatus Feleus",
        url="https://www.caligatus-feleus.ch",
        item_selector=None,        # lignes « Schloss Laupen: 16.–18. April 2027 »
        title_template="Marché médiéval Caligatus Feleus – {}",
        location_template="{}",
        location="",
    ),
    ListingSource(
        name="Arbor Living History (Vaulruz…)",
        urls=["https://www.arbor-living-history.ch"],
        link_pattern=r"arbor-living-history\.ch/event-details/[^/]+$",
        location="Fribourg",
        detail="always",           # fiches Wix avec JSON-LD (date + lieu)
        title_from_detail=True,
    ),
    # Agenda médiéval tenu à la main par Arbor : toute la Suisse + France voisine
    MonthAgendaSource(
        name="Agenda médiéval Arbor",
        url="https://www.arbor-living-history.ch/agenda-medieval",
    ),
    TextListSource(
        # Agenda des fêtes où la guilde est présente (Bâtiaz, Grandson, Enigmara…)
        name="La Guilde de Sombre-Sang",
        url="https://www.laguildedesombresang.com",
        item_selector=None,        # « 25 et 26 avril - L'Éveil de la Bâtiaz | Martigny »
        year_headings=True,        # sous un titre « 2026 », « 2027 »…
        location_next_segment=True,
        location="Suisse romande",
    ),
    ListingSource(
        name="Corvus Nidum (Greenfield, Schloss Wyher, Midgard…)",
        urls=["https://www.corvus-nidum.ch/eventing/"],
        link_pattern=r"corvus-nidum\.ch/eventing/(?!events/)[^/]+/?$",
        location="Suisse alémanique",
        detail="always",
        title_from_detail=True,
        prefer_future=True,        # une page par événement récurrent
        drop_undated=True,
    ),
]

# --- Sites officiels d'événements récurrents ------------------------------
# Repérés sur l'agenda de mortecouille.ch (novembre 2026). Chaque page est relue
# à chaque passage ; elle ne produit un événement que lorsqu'une date à venir y
# est publiée (voir WatchedPage).
#   (nom, url, lieu, pays)
WATCHED_PAGES = [
    # Suisse romande
    ("Médiévales de Sainte-Ursanne", "https://medievales.ch/", "Saint-Ursanne JU", "CH"),
    ("MédiéNeuch", "https://www.neuchatelmedieval.ch/", "Neuchâtel NE", "CH"),
    ("Estavayer 1470 – Grande fête médiévale", "https://www.estavayer1470.ch", "Estavayer-le-Lac FR", "CH"),
    ("Les Médiévales d'Attalens", "https://medievales-attalens.ch", "Attalens FR", "CH"),
    ("Fête médiévale au Château de Grandson", "https://www.aacg.ch/", "Château de Grandson VD", "CH"),
    ("Veytaux – Fête & légendes", "https://www.proveytaux.com/marche-medieval", "Veytaux VD", "CH"),
    ("Festival des sorcières", "https://festivaldessorcieres.wixsite.com/festivaldessorcieres", "La Sarraz VD", "CH"),
    ("Fête du Sel à Buttes", "https://www.fetedusel.ch/programme/", "Buttes NE", "CH"),
    ("Médiévales de Compesières", "https://ferus-histoire.ch/infos-pratiques", "Bardonnex GE", "CH"),
    ("Arbor Pagan Festival", "https://www.arbor-pagan-festival.ch", "Fribourg FR", "CH"),
    # URL propre à l'édition : à mettre à jour chaque année (l'accueil liste d'autres dates)
    ("L'éveil de la Bâtiaz", "https://www.laguildedesombresang.com/eveil2027", "Martigny VS", "CH"),
    ("Marchés de l'Échoppe d'Avalon", "https://www.echoppe-avalon.ch/", "Martigny VS", "CH"),
    # Suisse alémanique et Tessin
    ("Fête médiévale et fantastique de Rheinfelden",
     "https://www.tourismus-rheinfelden.ch/fr/fete-medievale-et-fantastique-de-rheinfelden", "Rheinfelden AG", "CH"),
    ("Marché d'artisans historique de Huttwil",
     "https://www.spycher-handwerk.ch/de/anlaesse/historischer-handwerkermarkt-huttwil", "Huttwil BE", "CH"),
    ("Fête médiévale de Gilgenberg", "https://www.mittelalter-gilgenberg.ch", "Zullwil SO", "CH"),
    ("Wums – Spectacle viking et médiéval", "https://www.wums.ch", "Hilfikon/Villmergen AG", "CH"),
    ("Marché médiéval Herrendingen", "https://www.arbor-noctuarum.ch", "Eschenbach LU", "CH"),
    ("Marché médiéval de Bremgarten", "https://mittelalter-bremgarten.ch/", "Bremgarten AG", "CH"),
    ("Mittelalter Markt Enter", "https://mittelaltermarktenter.ch", "Derendingen SO", "CH"),
    ("Fantasy Basel – Marché médiéval", "https://fantasybasel.ch/fr/marche-medieval", "Bâle BS", "CH"),
    ("Greenfield Mittelaltermarkt", "https://www.greenfieldfestival.ch/", "Interlaken BE", "CH"),
    ("Festa di Redde", "https://www.reddevive.ch", "Tesserete TI", "CH"),
    # France voisine
    ("Les Grandes Médiévales d'Andilly", "https://www.grandparc-andilly.com/les-grandes-medievales",
     "Grand Parc d'Andilly (74)", "FR"),
    ("Les Médiévaleries de Saint-Jean-d'Aulps",
     "https://www.valleedaulps.com/temps-fort/medievaleries-haute-savoie/", "Saint-Jean-d'Aulps (74)", "FR"),
    ("Assaut des Remparts",
     "https://www.jura-tourism.com/evenements/evenements-culturels/assaut-des-remparts/", "Nozeroy (39)", "FR"),
]
SOURCES += [WatchedPage(name, url, loc, country) for name, url, loc, country in WATCHED_PAGES]


def scrape_openagenda(http: Http) -> list[Event]:
    """
    OpenAgenda — recherche transversale, Suisse romande/alémanique + départements
    français frontaliers (Ain, Haute-Savoie, Doubs, Jura, Haut-Rhin, Territoire de Belfort).
    Nécessite une clé publique gratuite dans OPENAGENDA_KEY.
    """
    key = os.environ.get("OPENAGENDA_KEY")
    if not key:
        log.info("OpenAgenda ignoré (définir OPENAGENDA_KEY pour l'activer)")
        return []
    params = {
        "key": key,
        "search": "médiéval medieval Mittelalter chevalier",
        "size": 100,
        "relative[]": ["current", "upcoming"],
        "geo[northEast][lat]": 48.1, "geo[northEast][lng]": 10.5,
        "geo[southWest][lat]": 45.7, "geo[southWest][lng]": 5.4,
        "detailed": 1,
    }
    r = http.get("https://api.openagenda.com/v2/events", params=params)
    if not r:
        return []
    events = []
    for ev in r.json().get("events", []):
        def tr(field_: str) -> str:
            v = ev.get(field_) or {}
            return v if isinstance(v, str) else (v.get("fr") or v.get("de") or next(iter(v.values()), ""))
        title = tr("title")
        if not is_medieval(f"{title} {tr('description')} {' '.join(map(str, ev.get('keywords', {}).get('fr', []) or []))}"):
            continue
        timings = ev.get("timings") or []
        loc = ev.get("location") or {}
        events.append(Event(
            name=title,
            date_start=parse_iso(timings[0]["begin"]) if timings else None,
            date_end=parse_iso(timings[-1]["end"]) if timings else None,
            location=", ".join(p for p in (loc.get("name"), loc.get("city")) if p),
            url=f"https://openagenda.com/agendas/{ev.get('originAgenda', {}).get('uid', '')}/events/{ev.get('slug', '')}",
            source="OpenAgenda",
            country=(loc.get("countryCode") or "").upper() or "CH",
        ))
    return events


def manual_events(http: Http) -> list[Event]:
    """
    Événements saisis à la main (non trouvés par le scraper).
    Préférez un WatchedPage quand l'événement a une page officielle : la date
    sera relue automatiquement chaque année.
    """
    # Les événements passés sont filtrés automatiquement : mettre à jour les dates
    # en début de saison (éditions 2026 ci-dessous, à reporter pour 2027).
    data = [
        # (nom, début, fin ou None, lieu, url, pays)
        ("Fête médiévale de Grandson", "2026-05-30", "2026-06-01",
         "Château de Grandson, Grandson VD", "https://chateau-grandson.ch", "CH"),
        ("Mittelalterfest Kyburg", "2026-06-13", "2026-06-15",
         "Schloss Kyburg, Kyburg ZH", "https://schlosskyburg.ch", "CH"),
        ("Marché médiéval de Gruyères", "2026-07-04", "2026-07-06",
         "Château de Gruyères, Gruyères FR", "https://www.chateau-gruyeres.ch", "CH"),
        ("Mittelalterfest Lenzburg", "2026-07-17", "2026-07-20",
         "Schloss Lenzburg, Lenzburg AG", "https://www.schloss-lenzburg.ch", "CH"),
        ("Fête médiévale de Romont", "2026-08-22", "2026-08-24",
         "Château de Romont, Romont FR", "https://www.romont.ch", "CH"),
        ("Tournoi de chevaliers — Château de Spiez", "2026-09-05", "2026-09-07",
         "Schloss Spiez, Spiez BE", "https://www.schlossspiez.ch", "CH"),
    ]
    return [Event(n, s, e, loc, url, "Manuel", c) for n, s, e, loc, url, c in data]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def _slug(text: str) -> str:
    """Nom normalisé pour comparer : sans accents, ponctuation ni année
    (« Fête médiévale de Vaulruz 2026 » = « Fête médiévale de Vaulruz »)."""
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", normalize_text(text))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def is_relevant(ev: Event, today: date) -> bool:
    """Garde les événements à venir ou en cours (fin ≥ aujourd'hui) et ceux sans date."""
    last = ev.date_end or ev.date_start
    if not last:
        return True
    try:
        return date.fromisoformat(last) >= today
    except ValueError:
        return True


def deduplicate(events: list[Event]) -> list[Event]:
    """
    Fusionne les doublons : même URL et même date de début, ou même nom et
    même date de début. (L'URL seule ne suffit pas : une page d'organisateur
    peut annoncer plusieurs événements.)
    """
    by_key: dict[tuple, Event] = {}
    order: list[tuple] = []
    url_index: dict[tuple, tuple] = {}
    for ev in events:
        key = (_slug(ev.name), ev.date_start)
        existing_key = url_index.get((ev.url, ev.date_start)) if ev.url else None
        key = existing_key or key
        if key in by_key:
            cur = by_key[key]
            # Complète les champs manquants plutôt que de perdre l'information
            for f in ("date_start", "date_end", "location", "url"):
                if not getattr(cur, f) and getattr(ev, f):
                    setattr(cur, f, getattr(ev, f))
            # Une source peut ne donner que le premier jour : on garde la plage la plus large
            if cur.date_end and ev.date_end and ev.date_end > cur.date_end:
                cur.date_end = ev.date_end
            continue
        by_key[key] = ev
        order.append(key)
        if ev.url:
            url_index[(ev.url, ev.date_start)] = key
    return [by_key[k] for k in order]


def run_source(source, http: Http) -> list[Event]:
    if callable(source):
        name, fn = source.__name__, source
    else:
        name, fn = source.name, source.scrape
    try:
        events = fn(http)
    except Exception:
        log.exception("%s a planté", name)
        return []
    for ev in events:
        if ev.date_start and not ev.date_end:
            ev.date_end = ev.date_start
        ev.name = re.sub(r"\s+", " ", ev.name).strip()
    undated = sum(1 for e in events if not e.date_start)
    log.info("%-50s → %3d événement(s)%s", name, len(events),
             f" dont {undated} sans date" if undated else "")
    return events


def run(only: str | None = None, dry_run: bool = False) -> list[Event]:
    log.info("=== Démarrage de la collecte ===")
    http = Http()
    sources: list = [scrape_openagenda, *SOURCES, manual_events]
    if only:
        sources = [s for s in sources
                   if only.lower() in (getattr(s, "name", None) or s.__name__).lower()]

    all_events: list[Event] = []
    for source in sources:
        all_events.extend(run_source(source, http))

    today = date.today()
    events = deduplicate([e for e in all_events if is_relevant(e, today)])
    events.sort(key=lambda e: (e.date_start or "9999-12-31", e.name.lower()))

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "count": len(events),
        "events": [e.to_dict() for e in events],
    }
    if dry_run or only:
        # --only : collecte partielle, on n'écrase pas events.json
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif not events and OUTPUT_FILE.exists():
        # Toutes les sources en panne : on ne remplace pas un fichier valide par du vide
        log.error("Aucun événement collecté — %s conservé tel quel", OUTPUT_FILE.name)
    else:
        OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.info("=== %d événements enregistrés dans %s ===", len(events), OUTPUT_FILE)
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="ne lancer que les sources dont le nom contient ce texte (n'écrit pas le fichier)")
    parser.add_argument("--dry-run", action="store_true", help="affiche le JSON sans écrire le fichier")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    run(only=args.only, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
