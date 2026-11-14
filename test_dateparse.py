"""Tests du parseur de dates. Lancer : python -m pytest  (ou python test_dateparse.py)"""

from datetime import date

from dateparse import find_date_clusters, parse_date_range, parse_iso

TODAY = date(2026, 11, 14)

CASES = [
    # (texte, début attendu, fin attendue) — exemples relevés sur les sites
    ("Samedi 28 Novembre 2026", "2026-11-28", "2026-11-28"),
    ("Du 22 octobre 2026 au 25 janvier 2027", "2026-10-22", "2027-01-25"),
    ("1 au 13 décembre 2026", "2026-12-01", "2026-12-13"),
    ("30 mai au 1er juin 2027", "2027-05-30", "2027-06-01"),
    ("4, 8, 11, 15, 18 et 25 novembre", "2026-11-04", "2026-11-25"),
    ("2, 9 et 16 décembre", "2026-12-02", "2026-12-16"),
    ("Donnerstag, 26. November 2026, 19.00 Uhr — Der magische Turm", "2026-11-26", "2026-11-26"),
    ("Samstag, 12. Dezember 2026, 16.00–21.00 Uhr", "2026-12-12", "2026-12-12"),
    ("Freitag, 09. Juli 2027 | Erzählabend", "2027-07-09", "2027-07-09"),
    ("Exposition | 21.11.2026 | - | 10.01.2027 | Noël", "2026-11-21", "2027-01-10"),
    ("12.-14.06.2027", "2027-06-12", "2027-06-14"),
    ("12.06.2027 - 14.06.2027", "2027-06-12", "2027-06-14"),
    ("14 août 2027 > 15 août 2027", "2027-08-14", "2027-08-15"),
    ("Sam. 28 Nov. 2026, | Dim. 29 Nov. 2026, | Sam. 5 Déc. 2026, | Dim. 13 Déc. 2026",
     "2026-11-28", "2026-12-13"),
    ("du 28 au 3 janvier 2027", "2026-12-28", "2027-01-03"),
    ("28 déc. 2026 - 3 janv.", "2026-12-28", "2027-01-03"),
    ("June 12-14, 2027", "2027-06-12", "2027-06-14"),
    ("dal 3 al 5 luglio 2027", "2027-07-03", "2027-07-05"),
    ("vom 3. bis 5. Juli 2027", "2027-07-03", "2027-07-05"),
    ("15. November 2026 | 13:00 – 17:00 Uhr", "2026-11-15", "2026-11-15"),
    ("2027-05-15", "2027-05-15", "2027-05-15"),
    # Sans année, déjà passé cette année → l'an prochain
    ("3 octobre > 4 octobre", "2027-10-03", "2027-10-04"),
    ("11. September - 13. September", "2027-09-11", "2027-09-13"),
    # Bruit : heures, prix, années historiques
    ("Ouvert de 10h00 - 17h00, CHF 19.- / 12-16 ans", None, None),
    ("La bataille de Morat, 22 juin 1476", None, None),
]


def test_cases():
    failures = []
    for text, start, end in CASES:
        got = parse_date_range(text, today=TODAY)
        if got != (start, end):
            failures.append(f"{text!r}: attendu {(start, end)}, obtenu {got}")
    assert not failures, "\n".join(failures)


def test_separate_clusters():
    text = "Prochaine date | Samedi 28 Novembre 2026 | Toutes les dates | Sam. 28 Nov. 2026, Dim. 13 Déc. 2026"
    clusters = find_date_clusters(text, today=TODAY)
    assert len(clusters) == 2
    assert clusters[1].end == date(2026, 12, 13)


def test_year_guessed_flag():
    assert find_date_clusters("12 juin", today=TODAY)[0].year_guessed
    assert not find_date_clusters("12 juin 2027", today=TODAY)[0].year_guessed


def test_parse_iso():
    assert parse_iso("2027-06-12") == "2027-06-12"
    assert parse_iso("2027-06-12T10:00:00+02:00") == "2027-06-12"
    # 23h30 UTC le 12 juin = 13 juin 01h30 à Zurich
    assert parse_iso("2027-06-12T23:30:00Z") in ("2027-06-13", "2027-06-12")
    assert parse_iso("") is None


if __name__ == "__main__":
    test_cases()
    test_separate_clusters()
    test_year_guessed_flag()
    test_parse_iso()
    print("OK")
