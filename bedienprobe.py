# -*- coding: utf-8 -*-
"""Bedienprobe – jeden Knopf, jedes Formular und jeden Regler einmal anfassen.

    python -X utf8 bedienprobe.py

**Was die anderen Tests nicht prüfen.** `os_test.py` ruft jede Seite auf: das sagt, ob sie
sich öffnet, nicht ob die Knöpfe darauf etwas tun. `taskforce_test.py` prüft die Kette
gründlich, aber an den Stellen, die jemand beim Schreiben im Kopf hatte. Die Fehler, die im
Betrieb weh tun, sitzen dazwischen: ein Knopf, dessen Ziel es nicht mehr gibt, weil eine
Route umbenannt wurde. Ein Formular, das POST schickt an eine Route, die nur GET kann. Ein
Filter, der zwar eine Seite liefert, aber nicht filtert.

Vier Durchgänge:

 1. **Verweise** – jedes `url_for('…')` in jeder Vorlage zeigt auf eine Route, die es gibt.
 2. **Formulare** – jedes `<form>` zeigt auf eine Route, die seine Methode beherrscht.
 3. **POST-Routen** – jede antwortet, statt mit 500 umzufallen. Portalabfragen, Löschen und
    Abmelden werden ausgelassen; die gehören in den Taskforce-Test, wo sie kontrolliert
    ablaufen.
 4. **Regler** – jede zurückgegebene Zeile erfüllt die Bedingung, die der Regler behauptet.
    Ein Regler, der alles durchlässt, ist nicht automatisch kaputt: „ohne Angabe bleibt
    stehen" ist gewollt. Darum wird geprüft, ob die Zeilen die Bedingung *verletzen* –
    nicht, ob die Zahl kleiner wurde.

Läuft gegen eine Kopie der Datenbank und braucht kein Internet.
"""
import atexit
import os
import re
import sqlite3
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
kopie = os.path.join(tempfile.gettempdir(), "improfy_bedienprobe_%d.db" % os.getpid())
atexit.register(lambda: os.path.exists(kopie) and os.remove(kopie))
_quelle = sqlite3.connect(os.path.join(HIER, "improfy_os.db"))
_ziel = sqlite3.connect(kopie)
with _ziel:
    _quelle.backup(_ziel)
_ziel.close()
_quelle.close()
os.environ["IMPROFY_OS_DB"] = kopie

import app as A                    # noqa: E402
import datenbank as db             # noqa: E402
import taskforce as tf             # noqa: E402

ergebnis = []


def pruefe(name, bedingung, detail=""):
    ergebnis.append((name, bool(bedingung)))
    print(f"  {'OK  ' if bedingung else 'FEHL'} {name}{(' – ' + str(detail)) if detail else ''}")


ZIEL = re.compile(r"url_for\(\s*'([a-zA-Z_0-9.]+)'")
FORMULAR = re.compile(r"<form([^>]*)>", re.I)

# Loesen einen echten Abruf aus oder veraendern zu viel, um sie blind aufzurufen.
NICHT_AUSLOESEN = re.compile(r"lauf|pruef|loesch|import|abmeld|sicher", re.I)

# Was ein Aufruf mitbringen muss, damit er etwas Sinnvolles tut statt nur abzuweisen.
DATEN_JE_ROUTE = {
    "taskforce_status": {"status": "gesehen", "bearbeiter": "Probe", "zurueck": "/taskforce"},
    "taskforce_nachgefasst": {"bearbeiter": "Probe", "zurueck": "/taskforce"},
    "taskforce_profil_speichern": {"formular": "1", "titel": "Probe", "art": "job",
                                   "suchbegriffe": "Lagerhelfer", "ort": "Köln",
                                   "umkreis_km": "25"},
    "taskforce_kunde_profil": {"art": "job", "titel": "Probe", "suchbegriffe": "Lagerhelfer",
                               "ort": "Köln", "umkreis_km": "25"},
    "taskforce_kurzprofil": {"kurzprofil": "Probe", "cv_text": ""},
    "taskforce_kunde_lebenslauf": {"name": "Probe", "url": "https://example.org/cv.pdf"},
    "taskforce_profil_link": {"url": "https://example.org/x", "titel": "Probe"},
    "sammelanlage_anlegen": {"art": "job"},
}


def main():
    c = A.app.test_client()
    bekannt = {r.endpoint: (r.methods or set()) for r in A.app.url_map.iter_rules()}

    print("Bedienprobe\n")
    print("1. Jeder Verweis in den Vorlagen zeigt auf eine Route, die es gibt")
    tot, formulare, vorlagen = [], set(), 0
    for datei in sorted(os.listdir(os.path.join(HIER, "templates"))):
        if not datei.endswith(".html"):
            continue
        vorlagen += 1
        with open(os.path.join(HIER, "templates", datei), encoding="utf-8") as f:
            text = f.read()
        for endpunkt in sorted(set(ZIEL.findall(text))):
            if endpunkt not in bekannt and not endpunkt.startswith("static"):
                tot.append(f"{datei}: url_for('{endpunkt}')")
        for roh in FORMULAR.findall(text):
            methode = (re.search(r'method="(\w+)"', roh, re.I) or [None, "get"])[1].upper()
            ziel = ZIEL.search(roh)
            if ziel:
                formulare.add((datei, methode, ziel.group(1)))
    pruefe(f"Kein toter Knopf in {vorlagen} Vorlagen", not tot, "; ".join(tot[:3]))

    print("\n2. Jedes Formular passt zu seiner Route")
    falsch = []
    for datei, methode, endpunkt in sorted(formulare):
        erlaubt = bekannt.get(endpunkt, set())
        if endpunkt not in bekannt or methode not in erlaubt:
            falsch.append(f"{datei}: {methode} → {endpunkt}")
    pruefe(f"Alle {len(formulare)} Formulare treffen eine Route, die ihre Methode kann",
           not falsch, "; ".join(falsch[:3]))

    print("\n3. Jede POST-Route antwortet, statt umzufallen")
    kid = db.wert("SELECT id FROM kunde WHERE standort=? ORDER BY id LIMIT 1",
                  (db.STANDORT_STANDARD,))
    pid = db.wert("SELECT id FROM tf_profil ORDER BY id LIMIT 1")
    aid = db.wert("SELECT id FROM tf_angebot ORDER BY id LIMIT 1")
    lid = db.wert("SELECT id FROM lebenslauf ORDER BY id LIMIT 1")
    werte_je_name = {"kid": kid, "pid": pid, "aid": aid, "lid": lid, "mid": 1, "id": 1}

    kaputt, gerufen = [], 0
    for regel in sorted(A.app.url_map.iter_rules(), key=lambda r: str(r)):
        if "POST" not in (regel.methods or set()) or str(regel).startswith("/api"):
            continue
        if NICHT_AUSLOESEN.search(regel.endpoint):
            continue
        werte = {a: werte_je_name.get(a) for a in regel.arguments}
        if any(v is None for v in werte.values()):
            continue
        pfad = regel.build(werte)[1]
        antwort = c.post(pfad, data=DATEN_JE_ROUTE.get(regel.endpoint, {}))
        gerufen += 1
        if antwort.status_code >= 500:
            kaputt.append(f"{pfad} → {antwort.status_code}")
    pruefe(f"{gerufen} POST-Routen antworten sauber", not kaputt, "; ".join(kaputt[:3]))

    print("\n4. Jeder Regler hält, was er behauptet")
    # Geprueft wird nicht „wurde die Liste kuerzer" – ein Regler ohne passende Daten
    # schneidet zu Recht nichts. Geprueft wird, ob eine zurueckgegebene Zeile die
    # Bedingung verletzt. Das faellt auch dann auf, wenn der Regler gar nicht greift.
    REGLER = [
        ("nur Stellen", {"art": "job"}, lambda a: a["art"] == "job"),
        ("nur Wohnungen", {"art": "wohnung"}, lambda a: a["art"] == "wohnung"),
        ("Relevanz ab 5", {"min_score": 5}, lambda a: (a["score"] or 0) >= 5),
        ("Abgleich ab 34 %", {"min_match": 34},
         lambda a: a["match"] is None or a["match"] >= 34),
        ("Umkreis bis 25 km", {"max_km": 25},
         lambda a: a["entfernung_km"] is None or a["entfernung_km"] <= 25),
        ("Quelle", {"quelle": "jobs.ba"}, lambda a: a["quelle"] == "jobs.ba"),
        ("Volltextsuche", {"suche": "lager"}, lambda a: "lager" in " ".join(
            str(a.get(f) or "") for f in ("titel", "anbieter", "ort", "beschreibung")).lower()),
        ("Kunde", {"kunde_id": kid}, lambda a: a["kunde_id"] == kid),
    ]
    for name, argumente, bedingung in REGLER:
        zeilen = tf.neue_angebote(limit=2000, status="neu", **argumente)
        schlecht = [a for a in zeilen if not bedingung(a)]
        pruefe(f"Regler {name}", not schlecht,
               f"{len(zeilen)} Zeilen" + (f", {len(schlecht)} verletzen ihn" if schlecht else ""))

    grund = [a["id"] for a in tf.neue_angebote(limit=40, status="neu")]
    for sortierung in ("abgleich", "neu", "naehe"):
        andere = [a["id"] for a in tf.neue_angebote(limit=40, status="neu", sortierung=sortierung)]
        pruefe(f"Sortierung {sortierung} ordnet um", andere != grund)

    print("\n5. Jede Seite der Tafel verträgt jeden Regler")
    fehler = []
    for basis in ("/taskforce", "/taskforce/arbeit", "/taskforce/wohnung"):
        for regler in ("kunde=%d" % kid, "coach=1", "status=gesehen", "quelle=jobs.ba",
                       "q=lager", "score=5", "match=34", "km=25", "tage=7", "sort=naehe",
                       "arbeitszeit=vz", "gehalt=2200", "quereinstieg=1", "miete=900",
                       "zimmer=2", "flaeche=55", "score=abc"):
            if c.get(f"{basis}?{regler}").status_code != 200:
                fehler.append(f"{basis}?{regler}")
    pruefe("51 Kombinationen aus Ansicht und Regler liefern eine gültige Seite",
           not fehler, "; ".join(fehler[:3]))

    print("\n6. Jede Seite sagt, wo man ist")
    # Wer über die Personensuche oder aus dem CRM mitten hineinspringt, braucht den Weg
    # zurück. Eine Seite ohne Pfadleiste ist eine Sackgasse.
    ohne = []
    for pfad_ in ("/taskforce", "/taskforce/arbeit", "/taskforce/wohnung", "/taskforce/suchen",
                  f"/taskforce/kunde/{kid}/stand", f"/taskforce/kunde/{kid}",
                  f"/taskforce/profil/{pid}"):
        if 'class="pfad"' not in c.get(pfad_).get_data(as_text=True):
            ohne.append(pfad_)
    pruefe("Jede Seite der Taskforce trägt eine Pfadleiste", not ohne, "; ".join(ohne))

    gut = sum(1 for _, ok in ergebnis if ok)
    print(f"\n{gut} von {len(ergebnis)} Prüfungen bestanden.")
    if gut < len(ergebnis):
        print("Fehlgeschlagen:")
        for name, ok in ergebnis:
            if not ok:
                print("  -", name)
    return 0 if gut == len(ergebnis) else 1


if __name__ == "__main__":
    sys.exit(main())
