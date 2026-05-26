#!/usr/bin/env python3
"""
Agrégateur d'événements médiévaux en Suisse
Génère un fichier events.json lisible par index.html
Usage: python scraper.py
Cron hebdomadaire recommandé: 0 6 * * 1 python3 /chemin/scraper.py
"""

import json
import re
import time
import logging
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mots-clés de recherche
# ---------------------------------------------------------------------------
KEYWORDS = [
    "médiéval", "medieval", "Mittelalterfest", "mittelalter",
    "marché médiéval", "marché medieval",
    "chevalier", "Ritter",
    "fête du château", "fest auf der burg",
    "tournoi", "Turnier",
    "joutes", "Tjost",
    # Châteaux connus en Suisse
    "Château de Chillon", "Chillon",
    "Château de Gruyères", "Gruyères",
    "Château de Grandson", "Grandson",
    "Château d'Yverdon", "Yverdon-les-Bains château",
    "Château de Morges", "Morges",
    "Château de Nyon",
    "Château de Vufflens",
    "Château de Romont",
    "Château de Gleyre",
    "Château de Lucens",
    "Schloss Thun", "Thun",
    "Schloss Spiez", "Spiez",
    "Schloss Burgdorf", "Burgdorf",
    "Schloss Lenzburg", "Lenzburg",
    "Schloss Kyburg", "Kyburg",
    "Schloss Rapperswil", "Rapperswil",
    "Schloss Heidegg",
    "Schloss Hallwyl",
    "Schloss Habsburg",
    "Bellinzona", "Castelgrande", "Montebello", "Sasso Corbaro",
]

# Déduplique en conservant l'ordre
KEYWORDS = list(dict.fromkeys(KEYWORDS))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MedievalEventBot/1.0; "
        "+https://github.com/ton-compte/medieval-events-ch)"
    )
}
REQUEST_TIMEOUT = 15
SLEEP_BETWEEN = 1.5  # secondes entre les requêtes


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def normalize_date(raw: str) -> str | None:
    """Tente de convertir une chaîne de date en ISO 8601 (YYYY-MM-DD)."""
    if not raw:
        return None
    raw = raw.strip()
    patterns = [
        "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y",
        "%d %B %Y", "%d %b %Y",
        "%B %d, %Y", "%b %d, %Y",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in patterns:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Extrait YYYY-MM-DD depuis une chaîne plus longue
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def is_future(date_str: str | None) -> bool:
    """Retourne True si la date est dans le futur ou inconnue."""
    if not date_str:
        return True  # conserve les événements sans date précise
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


def contains_keyword(text: str) -> bool:
    text_low = text.lower()
    return any(kw.lower() in text_low for kw in KEYWORDS)


def deduplicate(events: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for ev in events:
        key = (ev.get("name", "").lower().strip(), ev.get("date_start", ""))
        if key not in seen:
            seen.add(key)
            result.append(ev)
    return result


def get(url: str) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        time.sleep(SLEEP_BETWEEN)
        return r
    except Exception as e:
        log.warning(f"GET {url} → {e}")
        return None


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def scrape_openagenda() -> list[dict]:
    """
    OpenAgenda API publique — recherche en Suisse par mots-clés.
    Doc: https://openagenda.com/agendas/73491888/events.json
    On interroge l'agenda "Suisse" s'il existe, sinon recherche globale.
    """
    events = []
    base = "https://api.openagenda.com/v2/events"
    # Clé publique (pas d'auth nécessaire pour la lecture)
    keywords_query = " OR ".join([
        "médiéval", "medieval", "Mittelalter", "chevalier", "tournoi", "joutes"
    ])
    params = {
        "search": keywords_query,
        "size": 100,
        "sort": "timings.begin",
        "monolingual": "fr",
        "relative": ["current", "upcoming"],
        "locationRadius": "250km,46.8182,8.2275",  # centre Suisse
    }
    url = base + "?" + "&".join(f"{k}={quote_plus(str(v))}" if not isinstance(v, list) else
                                 "&".join(f"{k}[]={quote_plus(vi)}" for vi in v)
                                 for k, v in params.items())
    r = get(f"{base}?search={quote_plus(keywords_query)}&size=100&relative[]=current&relative[]=upcoming&locationRadius=250km,46.8182,8.2275&monolingual=fr")
    if not r:
        return events
    try:
        data = r.json()
        for ev in data.get("events", []):
            title = ev.get("title", {}).get("fr", "") or ev.get("title", {}).get("de", "") or ""
            description = ev.get("description", {}).get("fr", "") or ""
            if not contains_keyword(title + " " + description):
                continue
            timings = ev.get("timings", [{}])
            date_start = normalize_date(timings[0].get("begin", "")) if timings else None
            date_end = normalize_date(timings[-1].get("end", "")) if timings else None
            slug = ev.get("slug", "")
            agenda_slug = ev.get("agenda", {}).get("slug", "")
            link = f"https://openagenda.com/agendas/{agenda_slug}/events/{slug}" if agenda_slug and slug else ""
            location = ev.get("location", {})
            city = location.get("city", "")
            events.append({
                "name": title,
                "date_start": date_start,
                "date_end": date_end,
                "location": city,
                "url": link,
                "source": "OpenAgenda",
            })
    except Exception as e:
        log.warning(f"OpenAgenda parse error: {e}")
    log.info(f"OpenAgenda → {len(events)} événements")
    return events


def scrape_ch_tourismus() -> list[dict]:
    """
    MySwitzerland.com (Suisse Tourisme) — page événements.
    On scrape la liste d'événements filtrés par catégorie culture/traditions.
    """
    events = []
    url = "https://www.myswitzerland.com/fr-ch/experiences/evenements/"
    r = get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    cards = soup.select("article.event-card, div.event-item, div[class*='EventCard'], li[class*='event']")
    for card in cards:
        title_el = card.select_one("h2, h3, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not contains_keyword(title):
            continue
        date_el = card.select_one("time, [class*='date'], [datetime]")
        raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
        link_el = card.select_one("a[href]")
        link = urljoin(url, link_el["href"]) if link_el else url
        events.append({
            "name": title,
            "date_start": normalize_date(raw_date),
            "date_end": None,
            "location": "Suisse",
            "url": link,
            "source": "MySwitzerland",
        })
    log.info(f"MySwitzerland → {len(events)} événements")
    return events


def scrape_agenda_ch() -> list[dict]:
    """
    Agenda.ch — moteur de recherche d'événements suisses.
    """
    events = []
    search_terms = ["médiéval", "Mittelalterfest", "chevalier", "tournoi joutes"]
    for term in search_terms:
        url = f"https://www.agenda.ch/de/suche/?q={quote_plus(term)}&country=CH"
        r = get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        items = soup.select("div.event, article.event, li.event, div[class*='event-item']")
        for item in items:
            title_el = item.select_one("h2, h3, .title, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title or not contains_keyword(title):
                continue
            date_el = item.select_one("time, [class*='date']")
            raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
            link_el = item.select_one("a[href]")
            link = urljoin(url, link_el["href"]) if link_el else url
            location_el = item.select_one("[class*='location'], [class*='city'], [class*='place']")
            location = location_el.get_text(strip=True) if location_el else ""
            events.append({
                "name": title,
                "date_start": normalize_date(raw_date),
                "date_end": None,
                "location": location,
                "url": link,
                "source": "Agenda.ch",
            })
        time.sleep(SLEEP_BETWEEN)
    log.info(f"Agenda.ch → {len(events)} événements")
    return events


def scrape_chateau_chillon() -> list[dict]:
    """Château de Chillon — page agenda officielle."""
    events = []
    url = "https://www.chillon.ch/fr/agenda"
    r = get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    items = soup.select("article, div.event, li.event, div[class*='agenda-item'], div[class*='event']")
    for item in items:
        title_el = item.select_one("h2, h3, h4, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        date_el = item.select_one("time, [class*='date']")
        raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
        link_el = item.select_one("a[href]")
        link = urljoin(url, link_el["href"]) if link_el else url
        events.append({
            "name": title,
            "date_start": normalize_date(raw_date),
            "date_end": None,
            "location": "Château de Chillon, Veytaux",
            "url": link,
            "source": "Château de Chillon",
        })
    log.info(f"Château de Chillon → {len(events)} événements")
    return events


def scrape_chateau_gruyeres() -> list[dict]:
    """Château de Gruyères — agenda."""
    events = []
    url = "https://www.chateau-gruyeres.ch/fr/agenda"
    r = get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    items = soup.select("article, div[class*='event'], li[class*='event'], div[class*='agenda']")
    for item in items:
        title_el = item.select_one("h2, h3, h4, [class*='title'], [class*='name']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        date_el = item.select_one("time, [class*='date']")
        raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
        link_el = item.select_one("a[href]")
        link = urljoin(url, link_el["href"]) if link_el else url
        events.append({
            "name": title,
            "date_start": normalize_date(raw_date),
            "date_end": None,
            "location": "Château de Gruyères",
            "url": link,
            "source": "Château de Gruyères",
        })
    log.info(f"Château de Gruyères → {len(events)} événements")
    return events


def scrape_schloss_thun() -> list[dict]:
    """Schloss Thun — agenda."""
    events = []
    url = "https://www.schlossthun.ch/de/veranstaltungen"
    r = get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    items = soup.select("article, div[class*='event'], div[class*='veranstaltung'], li[class*='event']")
    for item in items:
        title_el = item.select_one("h2, h3, h4, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        date_el = item.select_one("time, [class*='date'], [class*='datum']")
        raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
        link_el = item.select_one("a[href]")
        link = urljoin(url, link_el["href"]) if link_el else url
        events.append({
            "name": title,
            "date_start": normalize_date(raw_date),
            "date_end": None,
            "location": "Schloss Thun",
            "url": link,
            "source": "Schloss Thun",
        })
    log.info(f"Schloss Thun → {len(events)} événements")
    return events


def scrape_schloss_lenzburg() -> list[dict]:
    """Schloss Lenzburg — agenda."""
    events = []
    url = "https://www.schlosslenzburg.ch/de/programm"
    r = get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    items = soup.select("article, div[class*='event'], div[class*='program'], li[class*='event']")
    for item in items:
        title_el = item.select_one("h2, h3, h4, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        date_el = item.select_one("time, [class*='date'], [class*='datum']")
        raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
        link_el = item.select_one("a[href]")
        link = urljoin(url, link_el["href"]) if link_el else url
        events.append({
            "name": title,
            "date_start": normalize_date(raw_date),
            "date_end": None,
            "location": "Schloss Lenzburg",
            "url": link,
            "source": "Schloss Lenzburg",
        })
    log.info(f"Schloss Lenzburg → {len(events)} événements")
    return events


def scrape_castelgrande_bellinzona() -> list[dict]:
    """Castelgrande Bellinzona — agenda."""
    events = []
    url = "https://www.bellinzonese-altoticino.ch/it/eventi"
    r = get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    items = soup.select("article, div[class*='event'], li[class*='event']")
    for item in items:
        title_el = item.select_one("h2, h3, h4, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        date_el = item.select_one("time, [class*='date']")
        raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
        link_el = item.select_one("a[href]")
        link = urljoin(url, link_el["href"]) if link_el else url
        events.append({
            "name": title,
            "date_start": normalize_date(raw_date),
            "date_end": None,
            "location": "Bellinzona (Castelgrande / Montebello / Sasso Corbaro)",
            "url": link,
            "source": "Bellinzona Châteaux",
        })
    log.info(f"Bellinzona → {len(events)} événements")
    return events


def manual_events() -> list[dict]:
    """
    Événements récurrents connus — à compléter manuellement.
    Ces événements ont lieu chaque année à des dates similaires.
    Mets à jour les dates en début de chaque saison.
    """
    return [

    ]


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------
def run() -> None:
    log.info("=== Démarrage de la collecte ===")
    all_events: list[dict] = []

    scrapers = [
        scrape_openagenda,
        scrape_ch_tourismus,
        scrape_agenda_ch,
        scrape_chateau_chillon,
        scrape_chateau_gruyeres,
        scrape_schloss_thun,
        scrape_schloss_lenzburg,
        scrape_castelgrande_bellinzona,
        manual_events,
    ]

    for scraper in scrapers:
        try:
            results = scraper()
            all_events.extend(results)
        except Exception as e:
            log.error(f"{scraper.__name__} a planté : {e}")

    # Filtre événements futurs + déduplique + trie par date
    future = [ev for ev in all_events if is_future(ev.get("date_start"))]
    unique = deduplicate(future)
    unique.sort(key=lambda e: e.get("date_start") or "9999-99-99")

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "count": len(unique),
        "events": unique,
    }

    out_path = Path(__file__).parent / "events.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"=== {len(unique)} événements enregistrés dans {out_path} ===")


if __name__ == "__main__":
    run()
