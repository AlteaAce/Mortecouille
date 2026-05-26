#!/usr/bin/env python3
"""
Agrégateur d'événements médiévaux en Suisse
Génère un fichier events.json lisible par index.html
Usage: python scraper.py
Cron hebdomadaire recommandé: 0 6 * * 1 python3 /chemin/scraper.py
"""

import json
import os
import re
import time
import logging
from datetime import datetime, date, timezone
from pathlib import Path
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Clés API (lues depuis les variables d'environnement — voir README)
# ---------------------------------------------------------------------------
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID  = os.environ.get("GOOGLE_CSE_ID", "")

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

KEYWORDS = list(dict.fromkeys(KEYWORDS))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MedievalEventBot/1.0; "
        "+https://github.com/ton-compte/medieval-events-ch)"
    )
}
REQUEST_TIMEOUT = 15
SLEEP_BETWEEN = 1.5


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def normalize_date(raw: str) -> str | None:
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
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def is_future(date_str: str | None) -> bool:
    if not date_str:
        return True
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
# Source : Google Custom Search API
# ---------------------------------------------------------------------------
def scrape_google_cse() -> list[dict]:
    """
    Google Custom Search — recherche les événements médiévaux en Suisse.
    100 requêtes gratuites/jour, 10 résultats par requête.
    Nécessite GOOGLE_API_KEY et GOOGLE_CSE_ID dans les variables d'environnement.
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        log.warning("Google CSE ignoré : GOOGLE_API_KEY ou GOOGLE_CSE_ID manquant.")
        return []

    events = []
    current_year = date.today().year
    next_year = current_year + 1

    search_queries = [
        f"fête médiévale Suisse {current_year}",
        f"fête médiévale Suisse {next_year}",
        f"marché médiéval Suisse {current_year}",
        f"Mittelalterfest Schweiz {current_year}",
        f"tournoi chevaliers Suisse {current_year}",
        f"joutes médiévales Suisse {current_year}",
        f"Château Chillon événement {current_year}",
        f"Schloss Kyburg Mittelalterfest {current_year}",
        f"Schloss Lenzburg fest {current_year}",
        f"Château Grandson fête {current_year}",
        f"Château Gruyères événement {current_year}",
        f"Schloss Thun Veranstaltung {current_year}",
    ]

    base_url = "https://www.googleapis.com/customsearch/v1"

    for query in search_queries:
        params = {
            "key": GOOGLE_API_KEY,
            "cx":  GOOGLE_CSE_ID,
            "q":   query,
            "num": 10,
            "gl":  "ch",
            "hl":  "fr",
        }
        url = base_url + "?" + "&".join(
            f"{k}={quote_plus(str(v))}" for k, v in params.items()
        )
        r = get(url)
        if not r:
            continue

        try:
            data = r.json()

            # Quota dépassé
            if "error" in data:
                log.warning(f"Google CSE erreur : {data['error'].get('message', '')}")
                break

            for item in data.get("items", []):
                title   = item.get("title", "")
                snippet = item.get("snippet", "") or ""
                link    = item.get("link", "")

                if not contains_keyword(title + " " + snippet):
                    continue

                # Tente d'extraire une date depuis le snippet
                date_match = re.search(
                    r"(\d{1,2}[./]\d{1,2}[./]\d{4})", snippet
                )
                date_str = normalize_date(date_match.group(1)) if date_match else None

                # Essaie aussi le format YYYY-MM-DD dans le snippet
                if not date_str:
                    date_str = normalize_date(snippet)

                events.append({
                    "name":       title,
                    "date_start": date_str,
                    "date_end":   None,
                    "location":   "Suisse",
                    "url":        link,
                    "source":     "Google Search",
                })

        except Exception as e:
            log.warning(f"Google CSE parse error pour '{query}': {e}")

        time.sleep(SLEEP_BETWEEN)

    log.info(f"Google CSE → {len(events)} événements")
    return events


# ---------------------------------------------------------------------------
# Sources scraping direct
# ---------------------------------------------------------------------------
def scrape_openagenda() -> list[dict]:
    events = []
    base = "https://api.openagenda.com/v2/events"
    keywords_query = " OR ".join([
        "médiéval", "medieval", "Mittelalter", "chevalier", "tournoi", "joutes"
    ])
    r = get(
        f"{base}?search={quote_plus(keywords_query)}"
        f"&size=100&relative[]=current&relative[]=upcoming"
        f"&locationRadius=250km,46.8182,8.2275&monolingual=fr"
    )
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
            date_end   = normalize_date(timings[-1].get("end", "")) if timings else None
            slug = ev.get("slug", "")
            agenda_slug = ev.get("agenda", {}).get("slug", "")
            link = (
                f"https://openagenda.com/agendas/{agenda_slug}/events/{slug}"
                if agenda_slug and slug else ""
            )
            city = ev.get("location", {}).get("city", "")
            events.append({
                "name": title, "date_start": date_start, "date_end": date_end,
                "location": city, "url": link, "source": "OpenAgenda",
            })
    except Exception as e:
        log.warning(f"OpenAgenda parse error: {e}")
    log.info(f"OpenAgenda → {len(events)} événements")
    return events


def scrape_ch_tourismus() -> list[dict]:
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
            "name": title, "date_start": normalize_date(raw_date), "date_end": None,
            "location": "Suisse", "url": link, "source": "MySwitzerland",
        })
    log.info(f"MySwitzerland → {len(events)} événements")
    return events


def scrape_agenda_ch() -> list[dict]:
    events = []
    for term in ["médiéval", "Mittelalterfest", "chevalier", "tournoi joutes"]:
        url = f"https://www.agenda.ch/de/suche/?q={quote_plus(term)}&country=CH"
        r = get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for item in soup.select("div.event, article.event, li.event, div[class*='event-item']"):
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
                "name": title, "date_start": normalize_date(raw_date), "date_end": None,
                "location": location, "url": link, "source": "Agenda.ch",
            })
        time.sleep(SLEEP_BETWEEN)
    log.info(f"Agenda.ch → {len(events)} événements")
    return events


def _scrape_generic(url: str, location: str, source: str) -> list[dict]:
    """Scraper générique pour les agendas de châteaux."""
    events = []
    r = get(url)
    if not r:
        return events
    soup = BeautifulSoup(r.text, "lxml")
    selectors = [
        "article", "div[class*='event']", "li[class*='event']",
        "div[class*='agenda']", "div[class*='veranstaltung']", "div[class*='program']",
    ]
    items = soup.select(", ".join(selectors))
    for item in items:
        title_el = item.select_one("h2, h3, h4, [class*='title'], [class*='name']")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        date_el = item.select_one("time, [class*='date'], [class*='datum']")
        raw_date = date_el.get("datetime", "") or (date_el.get_text(strip=True) if date_el else "")
        link_el = item.select_one("a[href]")
        link = urljoin(url, link_el["href"]) if link_el else url
        events.append({
            "name": title, "date_start": normalize_date(raw_date), "date_end": None,
            "location": location, "url": link, "source": source,
        })
    log.info(f"{source} → {len(events)} événements")
    return events


def scrape_chateau_chillon() -> list[dict]:
    return _scrape_generic(
        "https://www.chillon.ch/fr/agenda",
        "Château de Chillon, Veytaux",
        "Château de Chillon",
    )

def scrape_chateau_gruyeres() -> list[dict]:
    return _scrape_generic(
        "https://www.chateau-gruyeres.ch/fr/agenda",
        "Château de Gruyères",
        "Château de Gruyères",
    )

def scrape_schloss_thun() -> list[dict]:
    return _scrape_generic(
        "https://www.schlossthun.ch/de/veranstaltungen",
        "Schloss Thun",
        "Schloss Thun",
    )

def scrape_schloss_lenzburg() -> list[dict]:
    return _scrape_generic(
        "https://www.schlosslenzburg.ch/de/programm",
        "Schloss Lenzburg",
        "Schloss Lenzburg",
    )

def scrape_castelgrande_bellinzona() -> list[dict]:
    return _scrape_generic(
        "https://www.bellinzonese-altoticino.ch/it/eventi",
        "Bellinzona (Castelgrande / Montebello / Sasso Corbaro)",
        "Bellinzona Châteaux",
    )


# ---------------------------------------------------------------------------
# Événements saisis manuellement
# ---------------------------------------------------------------------------
def manual_events() -> list[dict]:
    """
    Événements vérifiés manuellement.
    Ajoute ici les événements que tu as confirmés sur les sites officiels.
    Format des dates : YYYY-MM-DD
    """
    return [
        # Exemple (décommente et remplis avec de vraies dates vérifiées) :
        # {
        #     "name":       "Fête médiévale de Grandson",
        #     "date_start": "2026-XX-XX",
        #     "date_end":   "2026-XX-XX",
        #     "location":   "Château de Grandson, Grandson",
        #     "url":        "https://www.chateau-grandson.ch",
        #     "source":     "Manuel",
        # },
    ]


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------
def run() -> None:
    log.info("=== Démarrage de la collecte ===")
    all_events: list[dict] = []

    scrapers = [
        scrape_google_cse,          # Google Custom Search (si clés disponibles)
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

    future = [ev for ev in all_events if is_future(ev.get("date_start"))]
    unique = deduplicate(future)
    unique.sort(key=lambda e: e.get("date_start") or "9999-99-99")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(unique),
        "events": unique,
    }

    out_path = Path(__file__).parent / "events.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"=== {len(unique)} événements enregistrés dans {out_path} ===")


if __name__ == "__main__":
    run()
