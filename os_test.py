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
import shutil
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

# Auch der Sicherungsordner gehoert in den Papierkorb. `betrieb.py` leitet ihn sonst aus
# seinem eigenen Verzeichnis ab: ein Testlauf, der sichert, legt einen Schnappschuss der
# TESTdatenbank ins echte `sicherungen/` und wirft bei sieben Staenden je eine echte
# Nachtsicherung heraus. Gelesen wird die Variable beim Import von `betrieb`.
sicherungen = os.path.join(tempfile.gettempdir(),
                           "improfy_os_gesamt_test_sicherungen_%d" % os.getpid())
os.environ["OS_SICHERUNG_ORDNER"] = sicherungen

# Dasselbe fuer die gebauten Unterlagen. Ohne diese Variable legt jeder Lauf zwei
# echte Dateien in `ausgabe/lebenslaeufe/` des Live-Repos – belegt am 21.09.2026:
# geloescht, Test erneut gelaufen, beide wieder da. Der Dateiname traegt Kundennummer
# und Datum, ein Testlauf ueberschreibt also ein am selben Tag echt gebautes Dokument
# desselben Menschen. Gelesen wird die Variable beim Import von `lebenslauf_bauen`
# und `cv_pdf`, sie muss darum vorher stehen.
ausgabe_ordner = os.path.join(tempfile.gettempdir(),
                              "improfy_os_test_ausgabe_%d" % os.getpid())
os.environ["OS_AUSGABE_ORDNER"] = ausgabe_ordner
atexit.register(lambda: shutil.rmtree(ausgabe_ordner, ignore_errors=True))
atexit.register(lambda: shutil.rmtree(sicherungen, ignore_errors=True))

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
    # Dasselbe gilt seit dem 21.09.2026 fuer die beiden Arbeitsplaetze /taskforce/arbeit
    # und /taskforce/wohnung: sie bekommen keinen achten Reiter, also muss der
    # Schnellzugriff des Einstiegs sie tragen.
    for _ziel in ("/nachrichten", "/aktivitaet", "/protokoll", "/konten", "/betrieb",
                  "/anbindung", "/aussen", "/lebenslauf/liste", "/taskforce/tafel",
                  "/taskforce/arbeit", "/taskforce/wohnung"):
        pruefe(f"{_ziel} ist ohne eigenen Reiter erreichbar", _ziel in _wege)

    print("\n4. Kein Platzhalter blieb stehen")
    proben = ["/", "/kunden", "/taskforce", "/taskforce/tafel", "/taskforce/arbeit",
              "/lebenslauf", "/trichter", "/aufgaben", "/aktivitaet", "/betrieb"]
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
    # Vor der ersten Sicherung festhalten, was im echten Ordner liegt. Ein Testlauf darf
    # dort weder etwas hinlegen noch etwas herausdraengen: `betrieb.aufraeumen()` behaelt
    # sieben Staende, jeder Test-Schnappschuss kostet also eine echte Nachtsicherung. Wer
    # aus so einem Stand zurueckholt, hat die Testkonten im Echtbestand - und schon das
    # erste Konto schaltet die persoenliche Anmeldung scharf.
    _echter_ordner = os.path.join(HIER, "sicherungen")
    _vorher = sorted(os.listdir(_echter_ordner)) if os.path.isdir(_echter_ordner) else []
    pruefe("Sicherung von Hand legt einen Stand an",
           c.post("/betrieb/sichern").status_code == 302 and len(betrieb.staende()) >= 1,
           [s["name"] for s in betrieb.staende()][:2])
    _nachher = sorted(os.listdir(_echter_ordner)) if os.path.isdir(_echter_ordner) else []
    pruefe("Der Testlauf sichert in den Papierkorb, nicht in den echten Ordner",
           os.path.abspath(betrieb.SICHERUNGEN) != os.path.abspath(_echter_ordner)
           and _vorher == _nachher and len(betrieb.staende()) >= 1,
           f"{betrieb.SICHERUNGEN} · echter Ordner unveraendert: {_vorher == _nachher}")
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

    print("\n10. Einen Menschen erfassen, ohne das CRM nachzubauen")
    # Angelegt wird ein Kunde eigentlich im CRM. Solange `CRM_BASIS` nicht in der .env
    # steht, kommt von dort aber nichts zurueck – und fuer jemanden, den das OS nicht
    # kennt, sucht die Taskforce nicht. Also geht es auch hier.
    #
    # Geprueft wird vor allem, was NICHT passieren darf: stilles Verdoppeln, stilles
    # Zusammenfuehren, eine zweite Wahrheit unter derselben Kundennummer. Und die Grenze:
    # erfasst wird, WER jemand ist – kein UE-Feld, kein Gutschein, kein Termin.
    #
    # Die Namen sind mit Absicht keine, die es geben kann. Ein echter Name im Testcode
    # kollidiert eines Tages mit einem echten Kunden und steht dann unbemerkt zweimal da.
    NAME = "Quintus Testbergmann"
    NAME_B = "Radulf Prüfstein"
    NAME_C = "Wendelin Ochsenfurt"
    NUMMER = "IMP-TEST-0001"
    testnamen = (NAME, NAME_B, NAME_C)

    def anzahl():
        return db.wert("SELECT COUNT(*) FROM kunde WHERE standort=?",
                       (db.STANDORT_STANDARD,))

    try:
        vorher = anzahl()
        r = c.post("/kunden/anlegen", data={"name": "   "})
        pruefe("Ohne Namen wird nichts angelegt",
               r.status_code == 200 and anzahl() == vorher
               and "Ohne Namen wird nichts angelegt" in r.get_data(as_text=True),
               f"{vorher} Kunden vorher, {anzahl()} nachher")

        r = c.post("/kunden/anlegen",
                   data={"name": NAME, "telefon": "0221 000000", "stadt": "Köln"})
        kid = db.wert("SELECT id FROM kunde WHERE name=?", (NAME,))
        pruefe("Ein unbekannter Name wird ohne Rückfrage angelegt",
               r.status_code == 302 and bool(kid) and anzahl() == vorher + 1
               and r.headers.get("Location", "").endswith(f"/kunde/{kid}"),
               r.headers.get("Location", ""))

        # Herkunft und Status: „K – Lead ohne Antrag" ist der einzige Code, der heisst
        # „ist da, noch nichts passiert". Leere Felder bleiben leer – nichts erfunden.
        satz = db.eine("SELECT * FROM kunde WHERE id=?", (kid,)) or {}
        pruefe("Der angelegte Kunde trägt seine Herkunft und den Lead-Status",
               satz.get("quelle_stand") == A.ANLAGE_QUELLE
               and satz.get("status_code") == "K" and satz.get("stadt") == "Köln"
               and satz.get("sprache") is None and satz.get("kundennummer") is None,
               f"{satz.get('quelle_stand')} / {satz.get('status_code')}")
        pruefe("Die Plakette „vorläufig“ steht in Liste und Akte",
               "vorläufig" in c.get("/kunden?q=Testbergmann").get_data(as_text=True)
               and "vorläufig" in c.get(f"/kunde/{kid}").get_data(as_text=True))
        pruefe("Das Anlegeformular steht offen, wenn der Schnellzugriff danach fragt",
               '<details id="neu"' in c.get("/kunden?neu=1").get_data(as_text=True))

        # Die Personensuche ist der Weg, auf dem die Taskforce ihn wiederfindet. Wer
        # angelegt ist und nicht gefunden wird, ist so gut wie nicht angelegt.
        gefunden = c.get("/api/kunden-suche?q=testbergmann").get_json() or {}
        pruefe("Der angelegte Kunde ist über die Personensuche findbar",
               any(k["id"] == kid for k in gefunden.get("kunden", [])),
               [k["name"] for k in gefunden.get("kunden", [])][:3])

        # Rueckfragen, nicht sperren: derselbe Name fuehrt zur Rueckfrage, nicht zum
        # zweiten Datensatz – und „trotzdem anlegen" bleibt erreichbar. Verglichen wird
        # unscharf (`tf.kunden_suchen`), weil ein exakter Vergleich zwei Namensteile
        # nicht wiederfindet, wenn im Bestand drei stehen oder die zweite Schreibweise
        # in Klammern dahinter.
        zwischen = anzahl()
        r = c.post("/kunden/anlegen", data={"name": NAME.lower()})
        text = r.get_data(as_text=True)
        pruefe("Ein ähnlicher Name führt zur Rückfrage statt zum Datensatz",
               r.status_code == 200 and anzahl() == zwischen
               and "ähnliche Namen stehen schon im Bestand" in text
               and "trotzdem anlegen" in text and NAME in text,
               f"{zwischen} Kunden, unverändert: {anzahl() == zwischen}")
        r = c.post("/kunden/anlegen", data={"name": NAME.lower(), "bestaetigt": "1"})
        pruefe("„trotzdem anlegen“ legt den zweiten Datensatz wirklich an",
               r.status_code == 302 and anzahl() == zwischen + 1,
               f"{zwischen} → {anzahl()}")

        # Eine Kundennummer gibt es einmal – dieselbe Regel wie in `api.py`. Hier gilt
        # kein „trotzdem": zwei Datensaetze unter einer Nummer waeren im QM ein Befund.
        c.post("/kunden/anlegen", data={"name": NAME_B, "kundennummer": NUMMER})
        bid = db.wert("SELECT id FROM kunde WHERE kundennummer=?", (NUMMER,))
        vor_dublette = anzahl()
        r = c.post("/kunden/anlegen", data={"name": NAME_C, "kundennummer": NUMMER})
        text = r.get_data(as_text=True)
        pruefe("Eine schon vergebene Kundennummer wird abgewiesen, mit Weg zum Vorhandenen",
               r.status_code == 200 and anzahl() == vor_dublette
               and NUMMER in text and f'/kunde/{bid}' in text
               and not db.wert("SELECT COUNT(*) FROM kunde WHERE name=?", (NAME_C,)),
               f"{vor_dublette} Kunden, unverändert: {anzahl() == vor_dublette}")

        # Die Grenze aus docs/wissen/crm-abgleich-was-gehoert-wohin.md: das Formular
        # erfasst, WER jemand ist. Alles, was das CRM verbindlich fuehrt, bleibt draussen –
        # sonst gibt es zwei Wahrheiten und jemand muss spaeter entscheiden, welche gilt.
        # Gemessen an den Feldern, die das Formular abschickt – nicht am Fliesstext.
        # Am Wortlaut gemessen schlaegt jede Statusbezeichnung an, in der „Gutschein"
        # vorkommt (G, H, I), und die Pruefung meldet Rot fuer eine Regel, die niemand
        # gebrochen hat. Ein Test, der aus der eigenen Aufschrift einen Befund macht,
        # wird abgeschaltet.
        formular = c.get("/kunden?neu=1").get_data(as_text=True)
        block = formular.split('<details id="neu"')[1].split("</details>")[0]
        felder = set(re.findall(r'name="([a-z_]+)"', block))
        pruefe("Das Formular erfasst nur, WER jemand ist – kein Stück der Akte aus dem CRM",
               felder <= {"name", "telefon", "stadt", "sprache", "kundennummer",
                          "status_code", "bestaetigt"},
               sorted(felder))

        # Die Feldnamen allein sind die halbe Regel. `status_code` steht erlaubt in der
        # Liste – was er tragen DARF, entscheidet `ANLAGE_STATUS`, und genau das war
        # bisher ungeprueft. Zwoelf Codes zur Auswahl waeren wieder die Akte: wer hier
        # „H – Gutschein da, Massnahme laeuft" waehlen koennte, zaehlte ab dem naechsten
        # Seitenaufruf als laufende Massnahme – ohne Gutschein, ohne Akte, ohne dass das
        # CRM davon weiss.
        angeboten = set(re.findall(r'<option value="([A-L])"', block))
        pruefe("Zur Wahl stehen nur Status, die ohne Akte wahr sein können",
               angeboten == set(A.ANLAGE_STATUS),
               f"angeboten {sorted(angeboten)}, erlaubt {sorted(A.ANLAGE_STATUS)}")

        # Und die Auswahlliste einzuengen reicht nicht: ein untergeschobenes Feld geht
        # an ihr vorbei. Geprueft wird deshalb der Weg, den ein Angreifer nimmt – POST
        # mit einem Code, den das Formular nie anbietet.
        c.post("/kunden/anlegen", data={"name": NAME_C, "status_code": "H",
                                        "bestaetigt": "1"})
        geschmuggelt = db.eine("SELECT status_code FROM kunde WHERE name=?", (NAME_C,))
        pruefe("Ein untergeschobener Status fällt auf den Lead-Status zurück",
               bool(geschmuggelt) and geschmuggelt["status_code"] == "K",
               (geschmuggelt or {}).get("status_code"))

        # Der UNIQUE-Fall. `kunde` traegt UNIQUE(name, standort); bei exakt gleicher
        # Schreibweise hilft „trotzdem anlegen" nicht, die Datenbank laesst den zweiten
        # Satz nicht zu. Ohne Abfangen endete genau das auf einer 500er-Seite: kein
        # Hinweis, kein Protokolleintrag, alles Eingetippte weg.
        #
        # Bisher lief der Test daran vorbei: die Rueckfrage-Pruefung oben nimmt
        # `NAME.lower()`, und das ist fuer SQLite ein anderer Name. Der Griff, der
        # wirklich kracht, ist die BUCHSTABENGLEICHE Wiederholung.
        vor_unique = anzahl()
        r = c.post("/kunden/anlegen", data={"name": NAME_B, "bestaetigt": "1"})
        text = r.get_data(as_text=True)
        pruefe("Derselbe Name buchstabengleich: abgewiesen mit Weg zum Vorhandenen,"
               " nicht mit 500",
               r.status_code == 200 and anzahl() == vor_unique
               and "nicht möglich" in text and f'/kunde/{bid}' in text
               and NAME_B in text,
               f"{r.status_code}, {vor_unique} Kunden, unverändert:"
               f" {anzahl() == vor_unique}")
    finally:
        # Der Bestand darf durch einen Testlauf nicht wachsen – auch nicht in der Kopie.
        with db.offen() as con:
            for n in testnamen:
                con.execute("DELETE FROM kunde WHERE name=? COLLATE NOCASE", (n,))

    fehl = [n for n, ok in ergebnis if not ok]
    print(f"\n{len(ergebnis) - len(fehl)} von {len(ergebnis)} Prüfungen bestanden.")
    if fehl:
        print("Fehlgeschlagen:", *fehl, sep="\n  - ")
    return 0 if not fehl else 1


if __name__ == "__main__":
    sys.exit(main())
