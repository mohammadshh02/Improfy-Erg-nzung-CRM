# -*- coding: utf-8 -*-
"""Bedienprobe – jeden Knopf, jedes Formular und jeden Regler einmal anfassen.

    python -X utf8 bedienprobe.py

**Was die anderen Tests nicht prüfen.** `os_test.py` ruft jede Seite auf: das sagt, ob sie
sich öffnet, nicht ob die Knöpfe darauf etwas tun. `taskforce_test.py` prüft die Kette
gründlich, aber an den Stellen, die jemand beim Schreiben im Kopf hatte. Die Fehler, die im
Betrieb weh tun, sitzen dazwischen: ein Knopf, dessen Ziel es nicht mehr gibt, weil eine
Route umbenannt wurde. Ein Formular, das POST schickt an eine Route, die nur GET kann. Ein
Filter, der zwar eine Seite liefert, aber nicht filtert.

Acht Durchgänge:

 1. **Verweise** – jedes `url_for('…')` in jeder Vorlage zeigt auf eine Route, die es gibt.
 2. **Formulare** – jedes `<form>` zeigt auf eine Route, die seine Methode beherrscht.
 3. **POST-Routen** – jede antwortet, statt mit 500 umzufallen. Portalabfragen, Löschen und
    Abmelden werden ausgelassen; die gehören in den Taskforce-Test, wo sie kontrolliert
    ablaufen.
 4. **Regler** – jede zurückgegebene Zeile erfüllt die Bedingung, die der Regler behauptet.
    Ein Regler, der alles durchlässt, ist nicht automatisch kaputt: „ohne Angabe bleibt
    stehen" ist gewollt. Darum wird geprüft, ob die Zeilen die Bedingung *verletzen* –
    nicht, ob die Zahl kleiner wurde.
 5. **Ansicht mal Regler** – jede Kombination liefert eine Seite oder den Weg zur Tafel.
 6. **Pfadleiste** – keine Taskforce-Seite ist eine Sackgasse.
 7. **Knopfprobe** – jeder `submit` wird wie im Browser abgeschickt: Formular bestimmen,
    Absender anwenden, Felder sammeln, Aufruf absetzen. Dazu drei gezielte Prüfungen
    (Umschalter, doppelte Ziele, Größenordnung der Überschriften) und eine Regel über
    `stil.css`: zu jedem klebenden Element gehört ein Gegenmaß für Sprungziele.
 8. **Übernehmen** – der eine Weg, der wirklich Angebote ablegt, gemessen an den Zeilen
    in `tf_angebot` statt an der Antwort der Route.

Läuft gegen eine Kopie der Datenbank und braucht kein Internet.
"""
import atexit
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse

import lxml.html
from werkzeug.datastructures import MultiDict

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

# Diese Probe drueckt jeden Knopf – auch „jetzt sichern". Ohne eigenen Sicherungsordner
# landet der Schnappschuss der TESTdatenbank im echten `sicherungen/` und verdraengt dort
# bei sieben Staenden eine echte Nachtsicherung. Gelesen wird die Variable beim Import
# von `betrieb`, also muss sie vorher stehen.
sicherungen = os.path.join(tempfile.gettempdir(),
                           "improfy_bedienprobe_sicherungen_%d" % os.getpid())
os.environ["OS_SICHERUNG_ORDNER"] = sicherungen

# Dasselbe fuer die gebauten Unterlagen. Ohne diese Variable legt jeder Lauf zwei
# echte Dateien in `ausgabe/lebenslaeufe/` des Live-Repos – belegt am 21.09.2026:
# geloescht, Test erneut gelaufen, beide wieder da. Der Dateiname traegt Kundennummer
# und Datum, ein Testlauf ueberschreibt also ein am selben Tag echt gebautes Dokument
# desselben Menschen. Gelesen wird die Variable beim Import von `lebenslauf_bauen`
# und `cv_pdf`, sie muss darum vorher stehen.
ausgabe_ordner = os.path.join(tempfile.gettempdir(),
                              "improfy_bedienprobe_ausgabe_%d" % os.getpid())
os.environ["OS_AUSGABE_ORDNER"] = ausgabe_ordner
atexit.register(lambda: shutil.rmtree(ausgabe_ordner, ignore_errors=True))
atexit.register(lambda: shutil.rmtree(sicherungen, ignore_errors=True))

import app as A                    # noqa: E402
import datenbank as db             # noqa: E402
import taskforce as tf             # noqa: E402

ergebnis = []


def pruefe(name, bedingung, detail=""):
    ergebnis.append((name, bool(bedingung)))
    print(f"  {'OK  ' if bedingung else 'FEHL'} {name}{(' – ' + str(detail)) if detail else ''}")


ZIEL = re.compile(r"url_for\(\s*'([a-zA-Z_0-9.]+)'")
FORMULAR = re.compile(r"<form([^>]*)>", re.I)
# Ein Knopf darf sein Formular umleiten. `formaction` und `formmethod` gelten dann vor
# `action` und `method` – und ein fest verdrahtetes `formaction="/gibtsnicht"` kam an
# Durchgang 2 vorbei, weil der nur das `<form>`-Tag las. Heute benutzen alle drei
# `formaction` im Bestand `url_for`; die Regel muss aber auch dann halten, wenn das
# nächste Mal jemand eine Adresse hintippt.
ABSENDER = re.compile(r"<(?:button|input)([^>]*\bformaction=[^>]*)>", re.I)

# Loesen einen echten Abruf aus oder veraendern zu viel, um sie blind aufzurufen.
NICHT_AUSLOESEN = re.compile(r"lauf|pruef|loesch|import|abmeld|sicher", re.I)

# Was ein Aufruf mitbringen muss, damit er etwas Sinnvolles tut statt nur abzuweisen.
DATEN_JE_ROUTE = {
    "taskforce_status": {"status": "gesehen", "bearbeiter": "Probe", "zurueck": "/taskforce"},
    "taskforce_nachgefasst": {"bearbeiter": "Probe", "zurueck": "/taskforce"},
    # `aktiv` und `zeitarbeit` gehören mitgeschickt, auch wenn sie hier nichts prüfen sollen.
    #
    # Ohne `aktiv` las `profil_speichern` das fehlende Kästchen als „aus" und pausierte das
    # Profil — mitten in dieser Probe. Danach fielen 665 Stellenangebote hinter `p.aktiv=1`
    # aus jeder Abfrage, und **fünf der acht Reglerprüfungen liefen gegen null Zeilen**: sie
    # konnten keinen kaputten Filter mehr bemerken, meldeten aber grün. Ein Test, der sich
    # selbst die Daten wegnimmt, ist schlimmer als keiner — er beruhigt.
    "taskforce_profil_speichern": {"formular": "1", "titel": "Probe", "art": "job",
                                   "suchbegriffe": "Lagerhelfer", "ort": "Köln",
                                   "umkreis_km": "25", "aktiv": "1", "zeitarbeit": "1"},
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
        for roh in ABSENDER.findall(text):
            # Ein Absender mit eigenem Ziel ist ein eigenes Formular. Ohne `formmethod`
            # gilt POST: `formaction` steht im Bestand nur an Knöpfen, die schreiben.
            methode = (re.search(r'formmethod="(\w+)"', roh, re.I)
                       or [None, "post"])[1].upper()
            ziel = ZIEL.search(roh)
            if ziel:
                formulare.add((datei + " (formaction)", methode, ziel.group(1)))
            elif "formaction=" in roh.lower():
                tot.append(f"{datei}: formaction ohne url_for – {roh.strip()[:60]}")
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
    # Der Kunde fuer die Proben: einer, an dem wirklich Angebote haengen. Sonst laufen die
    # Reglerprobe und die Seitenproben gegen einen Menschen ohne Daten und koennen nichts
    # bemerken. Erst wenn es gar keinen gibt, wird der erstbeste genommen.
    kid = db.wert("SELECT p.kunde_id FROM tf_profil p JOIN tf_angebot a ON a.profil_id=p.id"
                  " WHERE p.standort=? GROUP BY p.kunde_id ORDER BY COUNT(*) DESC LIMIT 1",
                  (db.STANDORT_STANDARD,), None) or db.wert(
                  "SELECT id FROM kunde WHERE standort=? ORDER BY id LIMIT 1",
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
    # Erst nachsehen, ob überhaupt etwas zu prüfen da ist. Eine Prüfung gegen null Zeilen
    # kann nichts verletzen und meldet deshalb grün, ohne etwas geprüft zu haben – das ist
    # kein Ergebnis, sondern eine Beruhigung. Hier ist das schon einmal passiert: ein POST
    # weiter oben pausierte das Testprofil, und danach liefen fünf der acht Prüfungen leer.
    grundmenge = tf.neue_angebote(limit=2000, status="neu")
    pruefe("Die Reglerprobe hat überhaupt Daten", len(grundmenge) > 0,
           f"{len(grundmenge)} neue Angebote")
    for name, argumente, bedingung in REGLER:
        zeilen = tf.neue_angebote(limit=2000, status="neu", **argumente)
        schlecht = [a for a in zeilen if not bedingung(a)]
        # „0 Zeilen" wird genannt, nicht verschwiegen: es kann stimmen (kein Angebot dieser
        # Quelle im Bestand) oder heißen, dass der Regler gar nicht zum Zug kam.
        pruefe(f"Regler {name}", not schlecht,
               f"{len(zeilen)} Zeilen" + (" – nichts zu prüfen" if not zeilen else "")
               + (f", {len(schlecht)} verletzen ihn" if schlecht else ""))

    grund = [a["id"] for a in tf.neue_angebote(limit=40, status="neu")]
    for sortierung in ("abgleich", "neu", "naehe"):
        andere = [a["id"] for a in tf.neue_angebote(limit=40, status="neu", sortierung=sortierung)]
        pruefe(f"Sortierung {sortierung} ordnet um", andere != grund)

    print("\n5. Jede Seite der Tafel verträgt jeden Regler")
    fehler = []
    # /taskforce ist der Einstieg, sobald kein Regler dasteht – mit Regler die Tafel.
    # /taskforce/tafel ist die Tafel immer, mit `art` verengt auf eine Suche.
    # /taskforce/arbeit und /taskforce/wohnung sind seit dem 21.09.2026 die Dashboards –
    # sie tragen die Regler zwar nicht mehr, duerfen an ihnen aber auch nicht zerbrechen:
    # sie stehen in Lesezeichen und in Links, die schon verschickt sind.
    basen = ("/taskforce", "/taskforce/tafel", "/taskforce/tafel?art=job",
             "/taskforce/tafel?art=wohnung", "/taskforce/arbeit", "/taskforce/wohnung")
    regler_liste = ("kunde=%d" % kid, "coach=1", "status=gesehen", "quelle=jobs.ba",
                    "q=lager", "score=5", "match=34", "km=25", "tage=7", "sort=naehe",
                    "arbeitszeit=vz", "gehalt=2200", "quereinstieg=1", "miete=900",
                    "zimmer=2", "flaeche=55", "score=abc")
    for basis in basen:
        for regler in regler_liste:
            adresse = basis + ("&" if "?" in basis else "?") + regler
            # 302 zählt als bestanden, und zwar als die RICHTIGE Antwort: ein
            # Tafelregler an /taskforce/arbeit meint die Tafel, nicht das Dashboard
            # (`NUR_TAFEL_REGLER` in app.py). Ihn hier zu halten hieße, den Filter
            # lautlos wegzuwerfen. Die Probe stand auf 200 und war damit stehen
            # geblieben – sie meldete Rot für genau das Verhalten, das gebaut wurde.
            if c.get(adresse).status_code not in (200, 302):
                fehler.append(adresse)
    pruefe(f"{len(basen) * len(regler_liste)} Kombinationen aus Ansicht und Regler"
           " liefern eine gültige Seite oder den Weg zur Tafel",
           not fehler, "; ".join(fehler[:3]))

    print("\n6. Jede Seite sagt, wo man ist")
    # Wer über die Personensuche oder aus dem CRM mitten hineinspringt, braucht den Weg
    # zurück. Eine Seite ohne Pfadleiste ist eine Sackgasse.
    ohne = []
    for pfad_ in ("/taskforce", "/taskforce/tafel", "/taskforce/tafel?art=job",
                  "/taskforce/tafel?art=wohnung", "/taskforce/arbeit", "/taskforce/wohnung",
                  "/taskforce/suchen",
                  f"/taskforce/kunde/{kid}/stand", f"/taskforce/kunde/{kid}",
                  f"/taskforce/profil/{pid}"):
        if 'class="pfad"' not in c.get(pfad_).get_data(as_text=True):
            ohne.append(pfad_)
    pruefe("Jede Seite der Taskforce trägt eine Pfadleiste", not ohne, "; ".join(ohne))

    print("\n7. Knopfprobe – jeder Knopf löst wirklich etwas aus")
    # Die Durchgänge 1 und 2 lesen die Vorlagen: zeigt der Knopf auf eine Route, kann sie
    # seine Methode? Das ist die halbe Frage. Die andere Hälfte beantwortet erst der echte
    # Aufruf – ein Formular kann tadellos verdrahtet sein und trotzdem nichts bewirken,
    # weil die Nutzdaten woanders lagen als der Knopf sie sucht. Genau so war
    # „übernehmen" am 21.09.2026 kaputt: Route da, Methode richtig, 302 zurück, und in
    # `tf_angebot` kam nie eine Zeile an.
    #
    # Darum wird hier wie ein Browser gesammelt: das Formular des Knopfes bestimmen, den
    # Absender anwenden (`formaction` vor `action`, `formmethod` vor `method`, `name` und
    # `value` des Knopfes mitschicken), jedes Feld mit `name` mitnehmen, das nicht
    # `disabled` ist, Häkchen nur wenn `checked`, Radios nur das gewählte, beim `select`
    # das `selected` – und ohne `selected` das erste, weil der Browser das auch tut.
    def _formular_von(knopf):
        eltern = knopf.getparent()
        while eltern is not None and eltern.tag != "form":
            eltern = eltern.getparent()
        return eltern

    def _felder(formular, knopf):
        daten = []
        for feld in formular.iter("input", "select", "textarea"):
            name = feld.get("name")
            if not name or feld.get("disabled") is not None:
                continue
            art = (feld.get("type") or "text").lower()
            if feld.tag == "input":
                if art in ("submit", "button", "image", "reset", "file"):
                    continue
                if art in ("checkbox", "radio") and feld.get("checked") is None:
                    continue
                # `on` ist die Vorgabe eines Haekchens ohne `value`. Ein leeres
                # Textfeld schickt der Browser leer – wer hier `on` einsetzt,
                # prueft etwas, das nie jemand abschickt.
                vorgabe = "on" if art in ("checkbox", "radio") else ""
                wert = feld.get("value")
                daten.append((name, vorgabe if wert is None else wert))
            elif feld.tag == "textarea":
                daten.append((name, feld.text or ""))
            else:
                moeglich = [o for o in feld.iter("option")]
                gewaehlt = [o for o in moeglich if o.get("selected") is not None]
                if not gewaehlt and moeglich and feld.get("multiple") is None:
                    gewaehlt = moeglich[:1]
                for o in gewaehlt:
                    daten.append((name, o.get("value") if o.get("value") is not None
                                  else (o.text or "").strip()))
        if knopf.get("name"):
            daten.append((knopf.get("name"), knopf.get("value") or ""))
        return daten

    karte = A.app.url_map.bind("localhost")

    def _endpunkt(pfad, methode):
        try:
            return karte.match(urllib.parse.urlsplit(pfad).path, method=methode)[0]
        except Exception:
            return ""

    # Jede Seite, die sich ohne Zutun aufrufen lässt – dieselbe Menge wie im Gesamttest.
    seiten = []
    for regel in sorted(A.app.url_map.iter_rules(), key=lambda r: str(r)):
        if "GET" not in (regel.methods or set()) or str(regel).startswith("/api"):
            continue
        if NICHT_AUSLOESEN.search(regel.endpoint) or regel.endpoint == "static":
            continue
        werte = {a: werte_je_name.get(a) for a in regel.arguments}
        if any(v is None for v in werte.values()):
            continue
        seiten.append(regel.build(werte)[1])
    seiten.append("/kunden?neu=1")

    stumm, gedrueckt, ausgelassen = [], 0, 0
    for seite in seiten:
        vorher = c.get(seite)
        if vorher.status_code != 200:
            continue
        quelltext = vorher.get_data(as_text=True)
        baum = lxml.html.fromstring(quelltext)
        for knopf in baum.xpath("//button | //input[@type='submit']"):
            if knopf.tag == "button" and (knopf.get("type") or "submit").lower() != "submit":
                continue
            formular = _formular_von(knopf)
            if formular is None:
                continue
            ziel = knopf.get("formaction") or formular.get("action") or seite
            methode = (knopf.get("formmethod") or formular.get("method") or "get").upper()
            endpunkt = _endpunkt(ziel, methode)
            if not endpunkt:
                # Frueher ein stilles `continue`. Genau daran rutschte ein
                # `formaction` auf eine Adresse durch, die es gar nicht gibt: der
                # Knopf wurde nie gedrueckt, und die Probe meldete trotzdem gruen.
                # Was sich nicht pruefen laesst, ist ein Befund, keine Ausnahme.
                stumm.append(f"{seite} › {aufschrift} → {methode} {ziel}"
                             " trifft keine Route")
                continue
            if NICHT_AUSLOESEN.search(endpunkt):
                # Diese hier werden mit Absicht ausgelassen (Portalabfrage, Loeschen,
                # Abmelden, Sichern) – gezaehlt wird es trotzdem, damit die Zahl unter
                # der Pruefung nicht heimlich schrumpft.
                ausgelassen += 1
                continue
            daten = MultiDict(_felder(formular, knopf))
            aufschrift = " ".join((knopf.text_content() or "").split())[:40]
            if methode == "POST":
                antwort = c.post(ziel, data=daten)
            else:
                antwort = c.get(ziel + ("&" if "?" in ziel else "?")
                                + urllib.parse.urlencode(list(daten.items(multi=True))))
            gedrueckt += 1
            if antwort.status_code not in (200, 302):
                stumm.append(f"{seite} › {aufschrift} → {antwort.status_code}")
            elif (methode == "POST" and antwort.status_code == 200
                  and antwort.get_data(as_text=True) == quelltext):
                # Nur POST. Ein Filter, der mit seinen eigenen Vorgaben abgeschickt
                # wird, MUSS dieselbe Seite liefern – daran ist nichts kaputt.
                stumm.append(f"{seite} › {aufschrift} → dieselbe Seite, Zeichen für Zeichen")
    pruefe(f"{gedrueckt} Knöpfe lösen wirklich etwas aus", not stumm,
           "; ".join(stumm[:3]) or f"{ausgelassen} mit Absicht ausgelassen")

    # Der Umschalter des Schnellzugriffs. Er ist kein Link, sondern zwei Radios im selben
    # Formular – nur so trägt er die schon getippten Eingaben mit. Bricht das auseinander
    # (zweites Formular, kein `checked`, verschiedene Namen), schickt „jetzt suchen"
    # stumm die falsche Art ab, und niemand sieht es der Seite an.
    baum = lxml.html.fromstring(c.get("/taskforce").get_data(as_text=True))
    radios = baum.xpath("//input[@type='radio'][@name='art']")
    formulare = {_formular_von(r) for r in radios}
    gewaehlt = [r for r in radios if r.get("checked") is not None]
    wohnung = c.get("/taskforce/suchen?art=wohnung&wo=K%C3%B6ln").get_data(as_text=True)
    pruefe("Der Umschalter trägt beide Arten, in einem Formular, mit genau einer Vorwahl",
           len(radios) == 2 and len(formulare) == 1 and len(gewaehlt) == 1
           and sorted(r.get("value") for r in radios) == ["job", "wohnung"]
           and "Wohnungssuche" in wohnung,
           f"{len(radios)} Radios in {len(formulare)} Formular(en), "
           f"{len(gewaehlt)} vorgewählt")

    # Derselbe Knopf, dasselbe Ziel, x-mal untereinander: das ist kein zweiter Weg, das
    # ist Rauschen vor dem einen Weg. Gemessen wird das Paar aus Aufschrift und `href` –
    # zweimal ist in Ordnung (Kopf und Fuß einer Liste), dreimal ist das Listenmuster.
    #
    # **Eine bekannte Fundstelle steht hier namentlich, statt die Schwelle zu lockern.**
    # `/aufgaben` zeigt je offener Aufgabe eine Zeile; drei davon hängen am selben
    # Menschen, und alle drei werden auf seiner Akte erledigt. Der Knopf heißt deshalb
    # dreimal `erledigen` und zeigt dreimal auf `/kunde/<id>` – die Regel greift, und
    # sie greift zu Recht: welche der drei Aufgaben der Klick schließt, sagt der Knopf
    # nicht. Das zu ändern ist eine Entscheidung über die Aufgabenliste und gehörte
    # nicht zum Umbau vom 21.09.2026 (Taskforce). Bis dahin steht der Befund hier
    # ausgeschrieben statt als gelockerte Schwelle – eine Regel, die man mit einer
    # größeren Zahl beschänftigt, misst danach gar nichts mehr.
    # Mit Ziel, nicht nur mit Aufschrift: sonst verschluckt die Ausnahme künftig auch
    # eine Wiederholung, die auf ein FALSCHES Ziel zeigt – und die Regel zählt Paare
    # aus Aufschrift und `href`. Das Ziel ist die Akte des Menschen, an dem die
    # Aufgaben hängen; welche Nummer das ist, sagt der Bestand, darum das Muster.
    BEKANNT = re.compile(r"^/kunde/\d+$")
    doppelt = []
    for seite in seiten:
        antwort = c.get(seite)
        if antwort.status_code != 200:
            continue
        baum = lxml.html.fromstring(antwort.get_data(as_text=True))
        paare = {}
        for verweis in baum.xpath("//a[@href][contains(@class,'btn') or contains(@class,'knopf')]"):
            schrift = " ".join((verweis.text_content() or "").split())
            if not schrift:
                continue
            paare[(schrift, verweis.get("href"))] = paare.get(
                (schrift, verweis.get("href")), 0) + 1
        for (schrift, ziel), wie_oft in paare.items():
            if wie_oft > 2 and not (seite == "/aufgaben" and schrift == "erledigen"
                                    and BEKANNT.match(ziel)):
                doppelt.append(f'{seite}: {wie_oft}× „{schrift}" → {ziel}')
    pruefe("Kein Knopf wiederholt sich mit demselben Ziel", not doppelt,
           "; ".join(doppelt[:3]))

    # Das Maßsystem der Stilvorlage, von oben gelesen: eine Überschrift darf nie kleiner
    # sein als der Text, den sie anführt. `.content h2` stand bis zum 21.09.2026 auf
    # 10,5 px gegen 14,5 px Fließtext – die Seite zerfiel dadurch in gleich laute Blöcke.
    stil = open(os.path.join(HIER, "static", "stil.css"), encoding="utf-8").read()
    ohne_kommentar = re.sub(r"/\*.*?\*/", "", stil, flags=re.S)

    def _groesse(wahl):
        # Nicht die erste Regel mit diesem Namen, sondern die erste, die wirklich
        # eine Schriftgroesse setzt: `body` steht auch in `html,body{margin:0…}`.
        for block in re.finditer(re.escape(wahl) + r"\s*\{([^}]*)\}", ohne_kommentar):
            treffer = re.search(r"font-size:\s*([\d.]+)px", block.group(1))
            if treffer:
                return float(treffer.group(1))
        return None

    fliess, eins, zwei = _groesse("body"), _groesse(".content h1"), _groesse(".content h2")
    pruefe("Keine Überschrift ist kleiner als der Fließtext",
           None not in (fliess, eins, zwei) and eins > zwei > fliess,
           f"Fließtext {fliess} px · h2 {zwei} px · h1 {eins} px")

    # Die Klasse Fehler, die ein Testclient sonst nie findet: etwas Klebendes über dem
    # Inhalt, und kein Gegenmaß für Sprungziele. Wer `#neu` anspringt, landet dann unter
    # der Leiste. Zu jedem `position:sticky`/`fixed` mit `z-index` gehört darum ein
    # `scroll-margin-top` bzw. ein `scroll-padding-top` an der Wurzel.
    klebrig = [b for b in re.findall(r"\{[^}]*\}", ohne_kommentar)
               if re.search(r"position:\s*(sticky|fixed)", b) and "z-index" in b]
    gegenmass = ohne_kommentar.count("scroll-margin-top")
    wurzel = ohne_kommentar.count("scroll-padding-top")
    pruefe("Zu jedem klebenden Element gehört ein Gegenmaß für Sprungziele",
           not klebrig or (gegenmass >= 1 and wurzel >= 1),
           f"{len(klebrig)} klebend, {gegenmass}× scroll-margin-top, "
           f"{wurzel}× scroll-padding-top")

    print("\n8. Übernehmen legt wirklich ab")
    # Der Befund vom 21.09.2026, gemessen im Browser: suchen, zwei Treffer anhaken,
    # Profil wählen, „übernehmen" – Rücksprung auf dieselbe Seite, keine Meldung, und in
    # `tf_angebot` keine einzige neue Zeile. Ursache war die Sitzung: die Trefferliste lag
    # dort, Flask legt die Sitzung ins Cookie, und ein Cookie fasst 4.096 Bytes. Gemessen
    # wurden 23.094. Der Browser warf es stumm weg.
    #
    # Geprüft wird deshalb nicht „die Route antwortet 302" – das tat sie die ganze Zeit –,
    # sondern ob Zeilen dazugekommen sind. Die Portale werden dafür nicht gefragt: die
    # Treffer kommen aus dieser Liste hier, mit genau den Zeichen, an denen ein
    # Attributwert zerbricht.
    BOES = ('Nachtschicht "mit Zulage" & mehr\nZweite Zeile – O\'Brien <b>fett</b> € 2.400')
    # Eine Quelle, die `tf.QUELLEN` kennt: seit dem 22.09.2026 weist der Wächter einen
    # frei erfundenen Schlüssel ab – auf der Tafel stünde sonst eine Plakette, hinter
    # der kein Portal steckt, und kein Quellenfilter fände die Zeile je wieder.
    erfunden = [{"quelle": "jobs.kleinanzeigen", "extern_id": "BP-%03d" % i,
                 # Unterscheidbar so, wie `tf._schluessel` unterscheidet: an
                 # `titel[:40]` und am ERSTEN WORT des Anbieters. Hier stand die
                 # laufende Nummer hinten am Titel und hinter einem Leerzeichen im
                 # Anbieter – damit trugen alle 120 denselben Vergleichsschluessel,
                 # 119 landeten als `status='doppelt'`, und `_ablegen` meldete 1
                 # statt 120. Die Pruefung war auf einem Datensatz gruen, den sie
                 # gar nicht pruefen wollte.
                 "titel": "Probe %03d %s" % (i, BOES),
                 "anbieter": "Prüffirma%03d & Söhne" % i,
                 "ort": "Köln", "entfernung_km": i % 30,
                 "url": "https://example.org/bedienprobe/%d" % i,
                 # `None`, nicht ein Datum: so geben es `jobs_kleinanzeigen`,
                 # `wohnung_kleinanzeigen` und meinestadt wirklich ab. Hier stand ein
                 # gefülltes Feld – und deshalb blieb die Probe gruen, waehrend 146 von
                 # 151 echten Treffern am Waechter haengen blieben.
                 "veroeffentlicht": None, "beschreibung": BOES * 3,
                 "zusatz": {"arbeitszeit": "Vollzeit", "gehalt": "2.400 € – 2.800 €"},
                 "score": 5} for i in range(120)]
    echte_suche = tf.direktsuche
    tf.direktsuche = lambda p, quellen=None, grenze=200: (erfunden, [])
    try:
        antwort = c.get("/taskforce/suchen?art=job&was=Lagerhelfer&wo=K%C3%B6ln&km=25")
        seite = antwort.get_data(as_text=True)
        keks = antwort.headers.get("Set-Cookie") or ""
        baum = lxml.html.fromstring(seite)
        formular = baum.get_element_by_id("uebernahme")
        versteckt = formular.xpath(".//input[@type='hidden'][starts-with(@name,'t')]")
        pruefe("Jeder Treffer reist im Formular mit, nicht im Cookie",
               len(versteckt) == len(erfunden) and len(keks) < 4096,
               f"{len(versteckt)} von {len(erfunden)} Feldern, Cookie {len(keks)} Bytes")

        daten = [("profil", str(pid)), ("zurueck", "/taskforce/suchen")]
        for i, feld in enumerate(versteckt):
            daten.append(("treffer", str(i)))
            daten.append((feld.get("name"), feld.get("value")))
        # Gezaehlt wird `status='neu'`, nicht `COUNT(*)`. Eine Zeile mit
        # `status='doppelt'` liegt zwar in der Tabelle, der Mensch davor liest
        # aber „0 von 120 uebernommen" – und genau das sah die alte Pruefung
        # nicht. Sie blieb gruen, als `_ablegen` jeden Treffer als Dublette ablegte.
        def _neue():
            return db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?"
                           " AND status='neu'", (pid,))

        vorher, alle_vorher = _neue(), db.wert(
            "SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?", (pid,))
        antwort = c.post("/taskforce/suchen/uebernehmen", data=MultiDict(daten))
        alle_nachher = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?",
                               (pid,))
        pruefe("Angehakte Treffer kommen als NEU an, nicht als Dublette",
               antwort.status_code == 302 and _neue() - vorher == len(erfunden),
               f"{_neue() - vorher} von {len(erfunden)} als neu, "
               f"{alle_nachher - alle_vorher} Zeilen insgesamt, "
               f"{sum(len(k) + len(v) for k, v in daten)} Bytes im Rumpf")

        satz = db.eine("SELECT titel FROM tf_angebot WHERE extern_id='BP-000'"
                       " AND profil_id=? ORDER BY id LIMIT 1", (pid,))
        pruefe("Anführungszeichen und Zeilenumbrüche überstehen den Weg unversehrt",
               bool(satz) and satz["titel"] == erfunden[0]["titel"],
               repr((satz or {}).get("titel"))[:80])

        # Und der Gegenfall: geht es schief, muss es dastehen. Stilles Nichts war der
        # eigentliche Skandal – die Route leitete mit `?meldung=…` zurück, die Suchseite
        # las den Namen nie aus der Adresse.
        leer = c.post("/taskforce/suchen/uebernehmen",
                      data={"zurueck": "/taskforce/suchen?art=job", "profil": str(pid)},
                      follow_redirects=True)
        pruefe("Ein Fehlschlag wird benannt, statt still zu enden",
               "Nichts angehakt" in leer.get_data(as_text=True),
               leer.status_code)
    finally:
        tf.direktsuche = echte_suche

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
