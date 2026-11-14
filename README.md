# ⚔️ Agenda médiéval — Suisse & France voisine

Agrégateur automatique d'événements médiévaux : marchés, fêtes, tournois, spectacles
et animations de châteaux en Suisse et dans les régions françaises frontalières.

Le script parcourt chaque semaine une quarantaine de sites (châteaux, organisateurs,
sites officiels de fêtes, agendas spécialisés), en extrait les événements **avec leurs dates**,
et produit un fichier `events.json` affiché par une page web filtrable.

Projet lié à [mortecouille.ch](https://mortecouille.ch), l'agenda médiéval de Suisse romande :
il sert à repérer les nouveaux événements et les nouvelles éditions à publier.

---

## Sommaire

- [Démarrage rapide](#démarrage-rapide)
- [Structure du dépôt](#structure-du-dépôt)
- [Mise à jour automatique (GitHub Actions)](#mise-à-jour-automatique-github-actions)
- [Sources](#sources)
- [Comment la date est récupérée](#comment-la-date-est-récupérée)
- [Ajouter une source](#ajouter-une-source)
- [Format de `events.json`](#format-de-eventsjson)
- [Règles de filtrage](#règles-de-filtrage)
- [Limites connues](#limites-connues)

---

## Démarrage rapide

Prérequis : Python 3.10 ou plus récent.

```bash
pip install -r requirements.txt

python scraper.py                    # collecte complète → events.json
python scraper.py --dry-run          # affiche le JSON sans écrire le fichier
python scraper.py --only turnei -v   # une seule source, logs détaillés (n'écrit pas le fichier)

python -m pytest test_dateparse.py   # tests du parseur de dates
```

Pour afficher la page, servez le dossier en HTTP (les navigateurs bloquent `fetch()` depuis `file://`) :

```bash
python -m http.server 8000
# puis http://localhost:8000
```

La page `index.html` propose une recherche plein texte, un filtre par période, un tri par
date, nom, lieu ou source, et une **modération** : chaque événement peut être marqué
✅ validé ou ❌ invalide, avec des onglets pour n'afficher que les non contrôlés.
Ces statuts sont enregistrés dans le navigateur (`localStorage`) : ils ne sont pas
partagés entre appareils ni conservés si les données du site sont effacées.

## Structure du dépôt

| Fichier | Rôle |
|---|---|
| `scraper.py` | Collecte : sources, extraction, dédoublonnage, écriture de `events.json` |
| `dateparse.py` | Extraction de dates multilingue (FR/DE/IT/EN) depuis du texte libre |
| `test_dateparse.py` | Tests du parseur de dates, sur des formats réels relevés sur les sites |
| `events.json` | Données générées (mises à jour par le workflow) |
| `index.html` | Tableau interactif : recherche, filtres de dates, tri, modération |
| `requirements.txt` | Dépendances Python |
| `.github/workflows/update.yml` | Workflow GitHub Actions (mise à jour hebdomadaire) |

## Mise à jour automatique (GitHub Actions)

Le workflow `.github/workflows/update.yml` lance le scraper **chaque lundi à 6h UTC** (et à la demande),
exécute d'abord les tests du parseur, puis commit `events.json` s'il a changé.

1. Le workflow est dans `.github/workflows/update.yml` (rien à déplacer).
2. Dans *Settings → Actions → General → Workflow permissions*, cochez **Read and write permissions**
   (le workflow pousse `events.json`).
3. *(Optionnel)* Ajoutez le secret `OPENAGENDA_KEY` (*Settings → Secrets and variables → Actions*)
   pour activer la source OpenAgenda.
4. Lancement manuel : onglet *Actions* → *Mise à jour événements médiévaux* → *Run workflow*.

Pour publier la page : *Settings → Pages* → déployer depuis la branche `main`, dossier racine.
`index.html` lira directement `events.json`.

En local, l'équivalent cron :

```bash
0 6 * * 1 cd /chemin/vers/le/repo && python3 scraper.py
```

## Sources

### Châteaux et musées

| Source | Méthode |
|---|---|
| Château de Chillon | liste + fiches (dates uniquement sur les fiches) |
| Château de Gruyères | liste + fiches |
| Château de Grandson | liste |
| Schloss Kyburg | liste |
| Museum Aargau — Lenzburg, Hallwyl, Habsburg, Wildegg, Legionärspfad | liste + fiches |
| Schloss Thun | lignes « date — titre » |
| Burg Zug | calendrier (événements médiévaux uniquement) |

### Organisateurs et agendas spécialisés

| Source | Événements | Méthode |
|---|---|---|
| [Turnei](https://www.turnei.ch) | Hinwil, Winterthur, Wattwil, Laufen, Bubikon… | liste |
| [Caligatus Feleus](https://www.caligatus-feleus.ch) | Laupen, Allmendingen, Burgdorf, Bienne | lignes « lieu: dates » |
| [Corvus Nidum](https://www.corvus-nidum.ch/eventing/) | Greenfield, Schloss Wyher, Midgard | liste + fiches |
| [Arbor Living History](https://www.arbor-living-history.ch) | Vaulruz… | liste + fiches (JSON-LD) |
| [Agenda médiéval Arbor](https://www.arbor-living-history.ch/agenda-medieval) | toute la Suisse + France voisine | agenda par mois |
| [La Guilde de Sombre-Sang](https://www.laguildedesombresang.com) | fêtes où la guilde est présente | lignes « date - titre », par année |
| OpenAgenda | recherche par mots-clés, Suisse + départements frontaliers | API (optionnelle, `OPENAGENDA_KEY`) |

### Sites officiels de fêtes récurrentes (« pages suivies »)

25 sites, listés dans `WATCHED_PAGES` (`scraper.py`), dont : Médiévales de Sainte-Ursanne,
Estavayer 1470, Médiévales d'Attalens, MédiéNeuch, Médiévales de Compesières, Arbor Pagan Festival,
L'éveil de la Bâtiaz, Rheinfelden, Gilgenberg, Wums, Herrendingen, Bremgarten, Greenfield,
Festa di Redde, et côté France : Grandes Médiévales d'Andilly, Médiévaleries de Saint-Jean-d'Aulps,
Assaut des Remparts (Nozeroy).

Une page suivie ne produit un événement **que lorsqu'une date à venir y est publiée**.
Tant que le site affiche l'édition passée, rien n'est ajouté ; dès que l'édition suivante est
annoncée, elle apparaît dans `events.json`.

La plupart de ces sites ont été repérés sur l'agenda de mortecouille.ch, via son API
(`https://mortecouille.ch/wp-json/tribe/events/v1/events`).

### Saisie manuelle

`manual_events()` dans `scraper.py`, pour ce qu'aucune source ne couvre. Les événements passés
sont filtrés automatiquement : il suffit de mettre à jour les dates en début de saison.

## Comment la date est récupérée

Pour chaque événement, du plus fiable au moins fiable :

1. **Données structurées** : JSON-LD `schema.org/Event`, microdata `itemprop="startDate"`,
   balises `<time datetime>`.
2. **Carte de la page de liste** : premier groupe de dates du texte de la carte.
3. **Page de détail** (si la carte n'a pas de date) : texte qui suit un libellé
   (« Toutes les dates », « Date », « Datum », « Termine », « Quand »…), sinon premier groupe
   de dates autour du titre `<h1>`.
4. **Pages suivies** : prochaine édition à venir dans toute la page (voir ci-dessous).

Le parseur (`dateparse.py`) comprend notamment :

| Exemple | Résultat |
|---|---|
| `Samedi 28 Novembre 2026` | 28.11.2026 |
| `Du 22 octobre 2026 au 25 janvier 2027` | 22.10.2026 → 25.01.2027 |
| `1 au 13 décembre 2026`, `30 mai au 1er juin 2027` | plages |
| `4, 8, 11, 15, 18 et 25 novembre` | 04.11 → 25.11 |
| `Donnerstag, 26. November 2026, 19.00 Uhr` | 26.11.2026 (l'heure est ignorée) |
| `21.11.2026 - 10.01.2027`, `12.-14.06.2027` | plages numériques |
| `vom 3. bis 5. Juli 2027`, `dal 3 al 5 luglio 2027` | allemand, italien |
| `June 12-14, 2027`, `14 août 2027 > 15 août 2027` | anglais, séparateur `>` |

- Une **liste ou plage** de dates donne `date_start` = la première, `date_end` = la dernière.
- **Année manquante** : année courante, ou la suivante si toutes les dates sont déjà passées ;
  l'événement porte alors `"year_guessed": true`.
- Sur les **pages suivies**, une date sans année déjà passée (« 5 septembre » lu en novembre)
  est ignorée : sur ce type de page, c'est presque toujours l'édition passée. Les groupes de dates
  proches (≤ 31 jours) sont fusionnés : « 22-23 mai 2027 … samedis 15 et 22 mai » → 15 → 23 mai.
- Les horodatages UTC (API) sont convertis en heure suisse pour éviter les décalages d'un jour.
- Les années historiques (« bataille de 1476 ») sont écartées.

## Ajouter une source

Toutes les sources sont déclarées dans `SOURCES` et `WATCHED_PAGES` (`scraper.py`).
Testez-la ensuite seule : `python scraper.py --only "nom" -v`.

**Site officiel d'une fête récurrente** — une ligne dans `WATCHED_PAGES` :

```python
("Fête médiévale de Y", "https://www.fete-y.ch", "Y VD", "CH"),
```

**Page d'agenda avec un lien par événement** :

```python
ListingSource(
    name="Château de X",
    urls=["https://www.chateau-x.ch/agenda/"],
    link_pattern=r"chateau-x\.ch/agenda/[^/]+/?$",  # URL d'une fiche événement
    location="Château de X, Ville",
    detail="missing",        # "always" si la liste n'affiche pas les dates
    theme_filter=False,      # True pour un agenda généraliste : ne garde que le médiéval
),
```

Pas besoin de sélecteurs CSS : les cartes sont repérées à partir des liens vers les fiches,
ce qui résiste mieux aux refontes des sites.

Autres options utiles de `ListingSource` : `title_from_detail` (titre = `<h1>` de la fiche),
`location_after_date` (lieu = bloc qui suit la date), `locations` (lieu selon l'URL),
`prefer_future` (fiches permanentes d'événements récurrents), `drop_undated`.

**Page où chaque événement tient sur une ligne** (« date — titre » ou « lieu: dates ») :

```python
TextListSource(
    name="Organisateur Z",
    url="https://www.z.ch",
    item_selector=None,          # découpe la page en blocs de texte
    year_headings=True,          # un bloc « 2027 » donne l'année des lignes suivantes
    location_next_segment=True,  # le bloc suivant la ligne est le lieu
    location="Suisse romande",
),
```

**Agenda découpé par mois** (« Mars 2027 | 6 - 8 Mars | Titre | Lieu ») :

```python
MonthAgendaSource(name="Agenda W", url="https://www.w.ch/agenda"),
```

Pour un site au format vraiment particulier, une simple fonction `def scrape_x(http) -> list[Event]`
peut être ajoutée à `SOURCES` (voir `scrape_burg_zug`).

## Format de `events.json`

```json
{
  "generated_at": "2026-11-14T14:30:42Z",
  "count": 58,
  "events": [
    {
      "name": "Noël médiéval au château",
      "date_start": "2026-11-28",
      "date_end": "2026-12-13",
      "location": "Château de Chillon, Veytaux VD",
      "url": "https://www.chillon.ch/monagenda/noel-medieval-au-chateau/",
      "source": "Château de Chillon",
      "country": "CH"
    }
  ]
}
```

| Champ | Description |
|---|---|
| `name` | Nom de l'événement |
| `date_start`, `date_end` | Dates ISO `AAAA-MM-JJ` (`null` si inconnues) |
| `location` | Lieu |
| `url` | Page de l'événement (ou de la source) |
| `source` | Source qui a trouvé l'événement |
| `country` | `CH` ou `FR` |
| `year_guessed` | Présent (`true`) seulement si l'année a été déduite : à vérifier |

## Règles de filtrage

- Sont conservés les événements **à venir ou en cours** (date de fin ≥ aujourd'hui) et ceux sans date.
- Les agendas généralistes sont filtrés sur des mots-clés médiévaux (`médiéval`, `Mittelalter`,
  `chevalier`, `Ritter`, `joutes`, `fauconnerie`…). Les noms de lieux ne sont volontairement pas
  des mots-clés. Les agendas de châteaux sont gardés en entier.
- **Doublons** fusionnés sur (URL + date de début) ou (nom normalisé + date de début) ; l'année
  dans le nom est ignorée et la plage de dates la plus large est conservée.
- Si toutes les sources échouent, `events.json` n'est pas écrasé.

Le robot s'identifie (`MedievalEventBot`), espace ses requêtes de 1,5 s par site et réessaie
3 fois en cas d'erreur 429/5xx. Il ne contourne pas les sites qui le refusent.

## Limites connues

- Les sites dont le contenu est chargé en JavaScript (certains sites Wix) ne sont pas lisibles :
  Mittelalter Markt Enter, Fantasy Basel, Schloss Wyher… Ils restent suivis, sans résultat garanti.
- La page de L'éveil de la Bâtiaz a une URL par édition (`/eveil2027`) : à mettre à jour chaque année.
- Retirées car hors service (novembre 2026) : MySwitzerland (403 anti-bot), recherche agenda.ch (404),
  bellinzonese-altoticino.ch (certificat expiré), charles-opera.ch (404 pour le robot).
- La source OpenAgenda n'a pas été testée faute de clé.
