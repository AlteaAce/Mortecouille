# Mortecouille
Agrégateur automatique d'événements médiévaux en Suisse pour Mortecouille.ch

## Fichiers

| Fichier | Rôle |
|---|---|
| `scraper.py` | Script Python qui collecte les événements et génère `events.json` |
| `events.json` | Données brutes (généré automatiquement) |
| `index.html` | Tableau interactif avec filtres par date et recherche |

## Utilisation

### 1. Installation des dépendances

```bash
pip install requests beautifulsoup4 lxml feedparser
```

### 2. Lancer le scraper

```bash
python scraper.py
```

Génère/met à jour `events.json` dans le même dossier.

### 3. Visualiser

Ouvrez `index.html` dans un navigateur **après avoir lancé `scraper.py`**.

> ⚠️ Pour charger `events.json` via `fetch()`, vous devez servir les fichiers via un serveur HTTP local ou héberger sur GitHub Pages — les navigateurs bloquent `fetch()` depuis `file://`.

```bash
# Serveur local rapide :
python -m http.server 8000
# puis ouvrez http://localhost:8000
```

## Automatisation (cron)

Pour mettre à jour chaque semaine automatiquement :

```bash
# Chaque lundi à 6h00
0 6 * * 1 cd /chemin/vers/le/repo && python3 scraper.py
```

### Via GitHub Actions (recommandé)

Créez `.github/workflows/update.yml` :

```yaml
name: Mise à jour événements médiévaux

on:
  schedule:
    - cron: '0 6 * * 1'   # Chaque lundi à 6h UTC
  workflow_dispatch:        # Déclenchement manuel possible

permissions:
  contents: write

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Installer les dépendances
        run: pip install requests beautifulsoup4 lxml feedparser

      - name: Lancer le scraper
        run: python scraper.py

      - name: Commit et push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add events.json
          git diff --cached --quiet || git commit -m "chore: mise à jour événements $(date +%Y-%m-%d)"
          git push
```

## Ajouter des événements manuellement

Dans `scraper.py`, la fonction `manual_events()` contient les événements récurrents saisis à la main. Modifiez-la directement pour ajouter des événements que le scraper ne trouve pas :

```python
{
    "name": "Nom de l'événement",
    "date_start": "2026-07-15",   # format YYYY-MM-DD
    "date_end":   "2026-07-17",   # optionnel
    "location":   "Nom du lieu, Ville",
    "url":        "https://www.site-officiel.ch",
    "source":     "Manuel",
},
```

## Sources scrappées

- **OpenAgenda** — plateforme d'événements culturels
- **MySwitzerland** — Suisse Tourisme
- **Agenda.ch** — agenda événements Suisse
- **Château de Chillon** — chillon.ch/fr/agenda
- **Château de Gruyères** — chateau-gruyeres.ch/fr/agenda
- **Schloss Thun** — schlossthun.ch
- **Schloss Lenzburg** — schlosslenzburg.ch
- **Bellinzona** (Castelgrande, Montebello, Sasso Corbaro)

## Notes

- Certains sites utilisent des protections anti-bot (Cloudflare, etc.) — le scraper retourne alors un avertissement et passe à la source suivante, sans planter.
- Les événements sans date restent affichés (date inconnue).
- Seuls les événements futurs sont conservés dans `events.json`.
- La déduplication se base sur (nom exact + date de début).

## Mots-clés utilisés

`médiéval`, `medieval`, `Mittelalterfest`, `mittelalter`, `marché médiéval`, `chevalier`, `Ritter`, `fête du château`, `tournoi`, `Turnier`, `joutes`, `Tjost`, + noms des principaux châteaux suisses.
