# -*- coding: utf-8 -*-
"""Gesamttest der CRM-Ergänzung – jede Seite einmal aufrufen.

    python -X utf8 os_test.py

Die anderen Selbsttests prüfen je eine Abteilung gründlich (`taskforce_test.py`,
`lebenslauf_test.py`, `api_test.py`). Dieser hier geht in die Breite: er holt sich alle
Routen, die Flask kennt, ruft jede einmal auf und meldet jede, die nicht sauber
antwortet. So fällt auf, wenn eine Änderung an einer Stelle eine ganz andere Seite
zerschießt – der häufigste Weg, sich etwas kaputt zu machen.

Läuft gegen eine Kopie der Datenbank, schreibt also nichts in den Echtbestand, und
braucht kein Internet. Seiten, die von außen lesen (Taskforce-Lauf, Probe einer
Schnittstelle), werden bewusst nicht ausgelöst.
"""
import os
import re
import atexit
import sqlite3
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
# Prozessnummer im Namen: zwei Laeufe duerfen sich nie dieselbe Datei teilen.
kopie = os.path.join(tempfile.gettempdir(), "improfy_os_gesamt_test_%d.db" % os.getpid())
atexit.register(lambda: os.path.exists(kopie) and os.remove(kopie))

# Kopie über SQLite statt über das Dateisystem: eine Datei, die gerade geschrieben wird
# (Entwicklungsserver nebenher), kopiert sich sonst in einem Zwischenzustand, und der Test
# schlägt bei jedem Lauf woanders fehl. Siehe dieselbe Stelle in taskforce_test.py.
if os.path.exists(kopie):
    os.remove(kopie)
_quelle = sqlite3.connect(os.path.join(HIER, "improfy_os.db"))
_ziel = sqlite3.connect(kopie)
with _ziel:
    _quelle.backup(_ziel)
_ziel.close()
_quelle.close()
os.environ["IMPROFY_OS_DB"] = kopie

import app as A                     # noqa: E402
import datenbank as db              # noqa: E402

ergebnis = []

# Routen, die nach außen greifen oder etwas verändern – hier nicht aufrufen.
AUSGELASSEN = {"static", "abmelden", "anmelden"}
NACH_AUSSEN = re.compile(r"lauf|probe|pruef|import|export|abruf|holen|senden|mail", re.I)


def pruefe(name, bedingung, detail=""):
    ergebnis.append((name, bool(bedingung)))
    print(f"  {'OK  ' if bedingung else 'FEHL'} {name}{(' – ' + str(detail)) if detail else ''}")


def _beispiel(regel):
    """Für jede Platzhalter-Art einen Wert aus dem Echtbestand einsetzen."""
    werte = {}
    for argument in regel.arguments:
        if argument == "kid":
            werte[argument] = db.wert("SELECT id FROM kunde ORDER BY id LIMIT 1")
        elif argument in ("mid", "coach_id"):
            werte[argument] = db.wert("SELECT id FROM mitarbeiter ORDER BY id LIMIT 1")
        elif argument == "pid":
            werte[argument] = db.wert("SELECT id FROM tf_profil ORDER BY id LIMIT 1")
        elif argument == "aid":
            werte[argument] = db.wert("SELECT id FROM tf_angebot ORDER BY id LIMIT 1")
        elif argument == "lid":
            werte[argument] = db.wert("SELECT id FROM lebenslauf ORDER BY id LIMIT 1")
        elif argument in ("dateiname", "bild", "schluessel", "name"):
            return None                      # brauchen eine echte Datei, eigene Prüfung
        else:
            werte[argument] = 1
        if werte.get(argument) is None:
            return None
    return werte


def main():
    c = A.app.test_client()
    print("Gesamttest Improfy-OS\n")

    print("1. Jede Seite antwortet")
    geprueft = uebersprungen = 0
    for regel in sorted(A.app.url_map.iter_rules(), key=lambda r: str(r)):
        if regel.endpoint in AUSGELASSEN or "GET" not in (regel.methods or set()):
            continue
        if NACH_AUSSEN.search(regel.endpoint):
            uebersprungen += 1
            continue
        werte = _beispiel(regel)
        if werte is None:
            uebersprungen += 1
            continue
        pfad = regel.build(werte)[1] if werte else str(regel)
        antwort = c.get(pfad)
        geprueft += 1
        # 302 ist in Ordnung: /login leitet auf die Startseite, /taskforce/kunde auf die Liste.
        if antwort.status_code not in (200, 302):
            pruefe(f"{pfad}", False, f"Status {antwort.status_code}")
    pruefe(f"{geprueft} Seiten antworten sauber", True,
           f"{uebersprungen} bewusst ausgelassen (Außenzugriff oder eigene Datei)")

    print("\n2. Gestaltung liegt vollständig vor")
    css = c.get("/static/stil.css")
    text = css.get_data(as_text=True)
    pruefe("Stilvorlage wird ausgeliefert", css.status_code == 200 and len(text) > 8000,
           f"{len(text)} Zeichen")
    for marke, was in (("--pine:#145243", "Pine-Grün des CRM"),
                       ("--accent:#43e06a", "Akzentgrün des CRM"),
                       ("General Sans", "Schrift General Sans"),
                       ("IBM Plex Mono", "Schrift IBM Plex Mono"),
                       (".ablage{", "Ablagefläche für Unterlagen")):
        pruefe(f"CSS enthält {was}", marke in text)
    start = c.get("/").get_data(as_text=True)
    pruefe("Jede Seite lädt die Schriften des CRM",
           "fontshare" in start and "fonts.googleapis" in start)

    print("\n3. Navigation zeigt alle Abteilungen")
    # Geprueft wird das Ziel, nicht die Beschriftung: wie ein Reiter heisst, darf
    # sich aendern ("Trichter" heisst jetzt "Vertrieb"); erreichbar sein muss er immer.
    for seite, pfad in (("Kunden", "/kunden"), ("Taskforce", "/taskforce"),
                        ("Lebenslauf", "/lebenslauf"), ("Vertrieb", "/trichter"),
                        ("Aufgaben", "/aufgaben"), ("Coaches", "/coaches")):
        pruefe(f"Navigation fuehrt zu {seite}", f'href="{pfad}"' in start, pfad)

    # Die Leiste wurde von 14 auf 7 Reiter gekuerzt. Was aus ihr herausfliegt, muss
    # von einer der Arbeitsseiten aus verlinkt bleiben - sonst ist es kein Aufraeumen,
    # sondern Verstecken, und die Seite ist praktisch geloescht.
    _wege = set()
    for _p in ("/", "/kunden", "/coaches", "/lebenslauf", "/taskforce", "/trichter",
               "/aufgaben"):
        _wege |= set(re.findall('href="(/[^"?#]*)', c.get(_p).get_data(as_text=True)))
    # /taskforce/tafel hat keinen Reiter: /taskforce zeigt jetzt den Einstieg. Die volle
    # Tafel muss von dort aus verlinkt bleiben, sonst ist sie praktisch geloescht.
    for _ziel in ("/nachrichten", "/aktivitaet", "/protokoll", "/konten", "/betrieb",
                  "/anbindung", "/aussen", "/lebenslauf/liste", "/taskforce/tafel"):
        pruefe(f"{_ziel} ist ohne eigenen Reiter erreichbar", _ziel in _wege)

    print("\n4. Kein Platzhalter blieb stehen")
    proben = ["/", "/kunden", "/taskforce", "/taskforce/tafel", "/lebenslauf", "/trichter",
              "/aufgaben", "/aktivitaet", "/betrieb"]
    for pfad in proben:
        t = c.get(pfad).get_data(as_text=True)
        pruefe(f"{pfad} ohne offene Jinja-Stelle",
               "{{" not in t and "{%" not in t and "Undefined" not in t)

    print("\n5. Unbekanntes wird sauber abgewiesen")
    pruefe("Unbekannte Seite gibt 404", c.get("/gibtesnicht").status_code == 404)
    pruefe("Unbekannter Kunde gibt 404", c.get("/kunde/999999").status_code == 404)
    pruefe("Unbekannte Lebenslauf-Datei gibt 404",
           c.get("/lebenslauf/datei/gibtesnicht.xlsx").status_code == 404)

    print("\n6. Konten, Rollen und Protokoll")
    import konten
    konten.init()
    pruefe("Ohne Konto laeuft der Uebergangsbetrieb weiter", not konten.persoenlicher_betrieb())
    r = c.post("/konten/anlegen", data={"anmeldename": "chef", "name": "Chefin",
                                        "passwort": "geheim12345", "rolle": "leitung"})
    pruefe("Erstes Konto laesst sich anlegen", r.status_code == 302 and konten.anzahl() == 1)
    pruefe("Ab dem ersten Konto zaehlt nur noch die persoenliche Anmeldung",
           konten.persoenlicher_betrieb() and c.get("/kunden").status_code == 302)
    pruefe("Falsches Passwort kommt nicht rein",
           "stimmt nicht" in c.post("/login", data={"anmeldename": "chef",
                                                    "passwort": "falsch"}).get_data(as_text=True))
    r = c.post("/login", data={"anmeldename": "chef", "passwort": "geheim12345"})
    pruefe("Richtige Anmeldung kommt rein",
           r.status_code == 302 and c.get("/kunden").status_code == 200)
    pruefe("Zu kurzes Passwort wird abgelehnt",
           "fehler" in c.post("/konten/anlegen",
                              data={"anmeldename": "x", "name": "X", "passwort": "kurz",
                                    "rolle": "lesen"}).headers.get("Location", ""))
    c.post("/konten/anlegen", data={"anmeldename": "pruefer", "name": "Pruefer",
                                    "passwort": "geheim12345", "rolle": "lesen"})
    c.get("/logout")
    c.post("/login", data={"anmeldename": "pruefer", "passwort": "geheim12345"})
    pruefe("Nur-Lesen darf lesen", c.get("/kunden").status_code == 200)
    pruefe("Nur-Lesen darf nichts aendern",
           c.post("/taskforce/angebot/1/status", data={"status": "gesehen"}).status_code == 403)
    pruefe("Nur-Lesen darf keine Konten verwalten", c.get("/konten").status_code == 403)
    c.get("/logout")
    c.post("/login", data={"anmeldename": "chef", "passwort": "geheim12345"})
    c.post("/taskforce/angebot/1/status", data={"status": "gesehen"})
    zeilen = konten.protokoll(20)
    pruefe("Jede Aenderung steht mit Person im Protokoll",
           any(z["aktion"].startswith("Status") and z["benutzer"] == "Chefin" for z in zeilen),
           [(z["benutzer"], z["aktion"]) for z in zeilen[:3]])
    pruefe("Anmeldungen werden protokolliert",
           any(z["aktion"] == "angemeldet" for z in zeilen))
    pruefe("Protokollseite zeigt die Eintraege",
           "Änderungsprotokoll" in c.get("/protokoll").get_data(as_text=True))

    print("\n7. Betrieb: Sicherung und Zeitsteuerung")
    import betrieb
    betrieb.init()
    pruefe("Sicherung von Hand legt einen Stand an",
           c.post("/betrieb/sichern").status_code == 302 and len(betrieb.staende()) >= 1,
           [s["name"] for s in betrieb.staende()][:2])
    pruefe("Betriebsseite zeigt Uhrzeiten und Staende",
           all(x in c.get("/betrieb").get_data(as_text=True)
               for x in ("Sicherungsstände", "Letzte Sicherung", betrieb.UHRZEIT_LAUF)))
    pruefe("Was heute lief, laeuft nicht zweimal",
           betrieb.schon_gelaufen("sicherung") and not betrieb._faellig("00:00", "sicherung"))
    pruefe("Abgeschaltete Zeitsteuerung loest nichts aus",
           not betrieb._faellig("aus", "agenten") and not betrieb._faellig("", "agenten"))

    print("\n8. Suchprofile in einem Schritt")
    import sammelanlage
    pruefe("Berufsvorschlag erkennt einen Beruf im Kurzprofil",
           sammelanlage.beruf_vorschlag("Lagerist bei Netto seit 2024") == "Lagerist"
           and sammelanlage.beruf_vorschlag(
               "Abgeschlossene Ausbildung Kaufmann für Büromanagement") == "Kaufmann für Büromanagement")
    pruefe("Kein Beruf wird lieber leer gelassen als geraten",
           sammelanlage.beruf_vorschlag("B.A. Öffentliche Verwaltung und Politik") == ""
           and sammelanlage.beruf_vorschlag("") == "")
    vor = sammelanlage.vorschlaege()
    pruefe("Vorschlagsliste nennt laufende Kunden ohne Profil", isinstance(vor, list),
           f"{len(vor)} Kunden")
    r = c.get("/taskforce/profile-anlegen")
    pruefe("Seite zeigt die Vorschlaege", r.status_code == 200
           and 'name="kunde"' in r.get_data(as_text=True))
    if vor:
        kid = vor[0]["id"]
        r = c.post("/taskforce/profile-anlegen",
                   data={"art": "job", "umkreis": "25", "kunde": str(kid),
                         f"begriff_{kid}": "Lagerhelfer", f"ort_{kid}": "Köln"},
                   follow_redirects=True)
        pruefe("Angehaktes Profil wird angelegt",
               r.status_code == 200 and db.wert(
                   "SELECT COUNT(*) FROM tf_profil WHERE kunde_id=? AND aktiv=1", (kid,)) == 1)
        r = c.post("/taskforce/profile-anlegen", data={"art": "job"}, follow_redirects=True)
        pruefe("Ohne Auswahl passiert nichts, mit Meldung",
               "nichts angelegt" in r.get_data(as_text=True))

    print("\n9. Kundennachrichten ueber WhatsApp")
    import nachrichten
    nachrichten.init()
    pruefe("Deutsche Nummern werden korrekt umgeformt",
           nachrichten.nummer("+49 177 2306596") == "491772306596"
           and nachrichten.nummer("0176 81257422") == "4917681257422"
           and nachrichten.nummer("017681257422") == "4917681257422")
    pruefe("Auslandsnummern behalten ihre Vorwahl",
           nachrichten.nummer("+43 660 1234567") == "436601234567"
           and nachrichten.nummer("0043 660 1234567") == "436601234567")
    pruefe("Unbrauchbares gibt keine Nummer zurueck",
           nachrichten.nummer("kaputt") == "" and nachrichten.nummer("") == ""
           and nachrichten.nummer("123") == "")
    text = nachrichten.text_bauen(
        {"name": "Irfanullah Hayat"},
        [{"art": "job", "titel": "Lagerhelfer", "anbieter": "Amazon", "ort": "Köln"},
         {"art": "wohnung", "titel": "2 Zimmer", "anbieter": "Privat", "ort": "Kalk"}])
    pruefe("Nachricht nennt Stellen und Wohnungen getrennt",
           "1 Stelle" in text and "1 Wohnung" in text and "Irfanullah" in text
           and "Improfy-Team" in text, text.split(chr(10))[0])
    link = nachrichten.wa_link("+49 176 41609534", text)
    pruefe("WhatsApp-Link traegt Nummer und Text",
           link.startswith("https://wa.me/4917641609534?text=") and len(link) > 100)
    pruefe("Ohne Nummer kein Link", nachrichten.wa_link("", text) == "")
    r = c.get("/nachrichten")
    pruefe("Seite antwortet und nennt den Weg", r.status_code == 200
           and "WhatsApp-Link" in r.get_data(as_text=True))
    ang = db.hole("SELECT id FROM tf_angebot LIMIT 2")
    for a in ang:
        c.post(f"/taskforce/angebot/{a['id']}/status",
               data={"status": "angeschrieben", "bearbeiter": "Test"})
    vor = nachrichten.vorschlaege()
    pruefe("Beworbene Angebote erscheinen als Vorschlag", bool(vor),
           f"{len(vor)} Kunden")
    if vor:
        e = vor[0]
        c.post("/nachrichten/vermerken",
               data={"kunde": e["kunde_id"], "text": e["text"],
                     "angebote": ",".join(str(x["id"]) for x in e["neu"])})
        pruefe("Vermerktes wird nicht zweimal vorgeschlagen",
               not any(v["kunde_id"] == e["kunde_id"] for v in nachrichten.vorschlaege()))
        pruefe("Der Verlauf haelt fest, was rausging",
               any(x["kunde_id"] == e["kunde_id"] for x in nachrichten.verlauf()))
    pruefe("Ohne Text wird nichts vermerkt",
           "fehler" in c.post("/nachrichten/vermerken",
                              data={"kunde": "1", "text": ""}).headers.get("Location", ""))

    fehl = [n for n, ok in ergebnis if not ok]
    print(f"\n{len(ergebnis) - len(fehl)} von {len(ergebnis)} Prüfungen bestanden.")
    if fehl:
        print("Fehlgeschlagen:", *fehl, sep="\n  - ")
    return 0 if not fehl else 1


if __name__ == "__main__":
    sys.exit(main())
