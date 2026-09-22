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
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import pruefkopie                # noqa: E402
# Warum die Arbeitskopie über `sqlite3.backup` läuft und nicht über `shutil.copy`,
# steht im Kopf von `pruefkopie.py`. Am Echtbestand ändert der Lauf nichts.
kopie = pruefkopie.anlegen("improfy_os_gesamt_test.db")
os.environ["IMPROFY_OS_DB"] = kopie
# **Der Sicherungsordner gehört ebenfalls dem Lauf.** Abschnitt 7 drückt auf
# `/betrieb/sichern`; `betrieb.SICHERUNGEN` zeigte dabei auf den echten Ordner des
# Repos, und `betrieb.aufraeumen` warf dort den ältesten Stand weg. Die Prüfkette hat
# sich so ihre eigene Historie überschrieben – am 22.09.2026 lagen sieben Stände aus
# 86 Minuten im Ordner, alle aus Testläufen. Der Wert muss **vor** `import app` stehen,
# `betrieb` bindet ihn beim Import.
# Gesetzt wird **unbedingt**, nicht mit `setdefault`: Ein geerbter Wert aus der Umgebung
# (eine `.env`, ein Startskript, ein Elternprozess) zeigt im Zweifel genau auf den
# Ordner, den dieser Lauf nicht anfassen darf. Wer den Ordner steuern will, ruft den
# Test aus einem eigenen Arbeitsbaum auf.
os.environ["OS_SICHERUNG_ORDNER"] = os.path.join(os.path.dirname(kopie), "sicherungen")

import app as A                     # noqa: E402
import datenbank as db              # noqa: E402

ergebnis = []

# Was hier nicht aufgerufen wird, steht namentlich in dieser Menge.
# Vorher entschied zusätzlich ein Wortmuster über die Endpunkt-Namen
# ("lauf|probe|export|…"). Das Wort „lauf" traf jede `lebenslauf_*`-Route – der
# Lebenslauf-Bauer, die meistgenutzte Seite des Hauses, lief im Gesamttest nie mit;
# neun Routen blieben stumm, von denen keine einzige nach außen greift. Was wirklich
# nach außen greift (Taskforce-Lauf, Probe einer Schnittstelle), sind POST-Routen und
# kommt in dieser Schleife ohnehin nicht vor.
# Die Bild-Routen brauchen die Nummer eines wirklich vorhandenen Bildes. Im Bestand
# liegt heute keines, und eine erfundene Nummer muss 404 geben - das ist richtig so und
# kein Fehler der Seite.
# Die Anmeldung heißt `login`/`logout` – „anmelden"/„abmelden" stand hier, traf also
# keinen Endpunkt, und `logout` lief im Rundlauf **mit**. Solange kein Konto angelegt
# ist, fällt das nicht auf; ab dem ersten Konto meldet sich der Rundlauf mitten drin
# selbst ab, und alles, was pfad-alphabetisch nach /logout kommt, landet auf der
# Anmeldeseite – 34 Seiten, die dann nur noch scheinbar geprüft wären.
# `export` braucht den Generator `exportieren`, den es nur im OS-Repo gibt. Dass die
# Route hier sauber 404 gibt statt abzustürzen, steht als eigene Prüfung unten.
AUSGELASSEN = {"static", "login", "logout", "kunde_foto_bild",
               "lebenslauf_eingang_bild", "lebenslauf_foto_zeigen", "export"}


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
    abgestuerzt = []
    for regel in sorted(A.app.url_map.iter_rules(), key=lambda r: str(r)):
        if regel.endpoint in AUSGELASSEN or "GET" not in (regel.methods or set()):
            continue
        werte = _beispiel(regel)
        if werte is None:
            uebersprungen += 1
            continue
        pfad = regel.build(werte)[1] if werte else str(regel)
        # Einer Umleitung wird gefolgt, statt sie blind hinzunehmen. Vorher galt 302 als
        # in Ordnung, und wohin es ging, sah niemand nach: `/taskforce/kunde` leitete
        # ungeprüft auf `/taskforce/kunde/0` und damit in die rohe 404-Seite.
        antwort = c.get(pfad, follow_redirects=True)
        geprueft += 1
        if antwort.status_code == 500:
            abgestuerzt.append(pfad)
        if antwort.status_code != 200:
            pruefe(f"{pfad}", False, f"Status {antwort.status_code}")
    # **Die Untergrenze macht die Prüfung leerlauffest.** Vorher stand hier eine
    # Konstante: `pruefe(..., True, ...)` war auch dann grün, wenn `_beispiel` für jede
    # Route None lieferte und die Schleife keine einzige Seite geholt hatte – bei
    # `geprueft=0` blieben 34 Seiten ungeprüft, und der Test meldete „0 Seiten
    # antworten sauber: OK". Die Zahl ist bewusst deutlich unter dem Istwand gesetzt:
    # Sie soll den Leerlauf fangen, nicht bei jeder neuen Route nachgezogen werden.
    pruefe(f"{geprueft} Seiten antworten sauber", geprueft >= 25,
           f"{uebersprungen} bewusst ausgelassen (Außenzugriff oder eigene Datei)")

    print("\n2. Gestaltung liegt vollständig vor")
    css = c.get("/static/stil.css")
    text = css.get_data(as_text=True)
    pruefe("Stilvorlage wird ausgeliefert", css.status_code == 200 and len(text) > 8000,
           f"{len(text)} Zeichen")
    # `hidden` wirkt nur ueber die Grundregel des Browsers, und die Knopfregel dieser
    # Datei schlaegt sie: Ein `<button hidden>` stand als leere gruene Pille sichtbar im
    # Blatt. Die Regel darf deshalb nicht wieder verschwinden.
    pruefe("CSS versteckt, was `hidden` trägt",
           re.search(r"\[hidden\][^{]*\{[^}]*display\s*:\s*none\s*!important", text)
           is not None)
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
    for _ziel in ("/nachrichten", "/aktivitaet", "/protokoll", "/konten", "/betrieb",
                  "/anbindung", "/aussen", "/lebenslauf/liste"):
        pruefe(f"{_ziel} ist ohne eigenen Reiter erreichbar", _ziel in _wege)

    print("\n4. Kein Platzhalter blieb stehen")
    proben = ["/", "/kunden", "/taskforce", "/lebenslauf", "/trichter", "/aufgaben",
              "/aktivitaet", "/betrieb"]
    for pfad in proben:
        t = c.get(pfad).get_data(as_text=True)
        pruefe(f"{pfad} ohne offene Jinja-Stelle",
               "{{" not in t and "{%" not in t and "Undefined" not in t)

    print("\n5. Unbekanntes wird sauber abgewiesen")
    # Ein 500er ist nie eine Auskunft, sondern immer ein Fehler im Haus. Welche Seite
    # es war, muss dastehen - sonst sucht der Nächste die Nadel im Heuhaufen.
    # `geprueft and` davor, weil `not abgestuerzt` sonst auch für eine Schleife gilt,
    # die gar nichts geholt hat: Eine Behauptung über null Seiten ist keine.
    pruefe("Keine Route endet in 500", geprueft and not abgestuerzt, abgestuerzt)
    # `/taskforce/kunde` ist der Sprung aus der Kundenauswahl. Ohne Nummer landete er
    # auf `/taskforce/kunde/0` und damit in der rohen 404-Seite des Servers, ein
    # getipptes Wort (`?kid=abc`) sogar in einem Absturz. Beides sagt dem Coach nicht,
    # was fehlt.
    # Geprüft wird der Satz selbst, nicht „Status 200 und irgendwo steht Taskforce" –
    # das Wort steht ohnehin im Seitentitel, und ohne Satz wäre die Seite wieder stumm.
    r = c.get("/taskforce/kunde", follow_redirects=True)
    pruefe("/taskforce/kunde ohne Kundennummer sagt, was fehlt",
           r.status_code == 200
           and "Bitte erst einen Kunden auswählen." in r.get_data(as_text=True),
           r.status_code)
    # Eine Zahl mit zwanzig Stellen passt nicht in 64 Bit; SQLite bricht damit ab. Die
    # Umleitung reichte sie ungeprüft weiter und endete im 500er – genau in der Antwort,
    # die hier abgeschafft werden sollte. 404 ist richtig, 200 mit Hinweis auch; ein
    # Absturz nie.
    # Und „²" gehört dazu: `"²".isdigit()` ist wahr, `int("²")` wirft. Deshalb steht in
    # `lebenslauf_uebersicht` und `taskforce_kunde_wahl` `isdecimal`. Diese Prüfung ist
    # der Grund, dass das so bleibt: Ohne sie ließ sich `isdecimal` gegen `isdigit`
    # tauschen, alle Prüfungen blieben grün, und `/lebenslauf?kunde=²` stürzte wieder ab.
    for _adresse in ("/taskforce/kunde?kid=abc",
                     "/taskforce/kunde?kid=99999999999999999999",
                     "/taskforce/kunde?kid=²",
                     "/taskforce/kunde/99999999999999999999",
                     "/kunde/99999999999999999999",
                     "/lebenslauf?kunde=99999999999999999999",
                     "/lebenslauf?kunde=²"):
        _r = c.get(_adresse, follow_redirects=True)
        pruefe(f"{_adresse} endet nicht im Absturz", _r.status_code in (200, 404),
               _r.status_code)
    # Die Schranke steht seit dieser Runde als `before_request` an der Tuer und nicht in
    # einzelnen Sichten - zehn Routen hatten sie vorher nicht, darunter der Lebenslauf
    # eines Kunden, die drei Bildrouten und fuenf POST-Wege. Geprueft wird sie deshalb
    # an allen Tueren: Jede Route, die nur Nummern im Pfad hat, bekommt eine
    # 20-stellige. Neue Routen sind damit von selbst mitgeprueft.
    #
    # **Der Pfad ist nur die halbe Tuer.** Die Schranke liest `request.view_args`, also
    # die Platzhalter im Pfad. Eine Nummer kann aber auch als Abfrageparameter kommen,
    # und `request.args.get(..., type=int)` reichte sie ungeprueft bis in die SQLite-
    # Abfrage durch: `/taskforce/export.csv?kunde=99999999999999999999` endete im 500er.
    # Der Titel unten behauptet „keine Route" - also wird auch das gemessen, an jeder
    # GET-Route und mit den Namen, unter denen Nummern hier wirklich ankommen.
    # Gegen zu grosse Zahlen steht in `app.zahl_arg` die gleiche 64-Bit-Grenze; sie
    # weist nicht ab, sondern laesst den Filter offen - eine kaputte Nummer sagt ueber
    # keinen Datensatz etwas aus.
    # **Zwei kaputte Werte, nicht einer.** Neben der 20-stelligen Nummer steht die
    # Hochzahl „²": `"²".isdigit()` ist wahr, `int("²")` wirft - deshalb prüft
    # `app.zahl_arg` mit `isdecimal`. Dieser Fix hatte keinen Wächter: Die vier
    # ²-Adressen der Selbsttests treffen das `isdecimal` in `lebenslauf_uebersicht` und
    # in `taskforce_kunde_wahl`, nicht das in `zahl_arg`. Gegengeprobt: `isdecimal` dort
    # auf `isdigit` zurückgedreht, und /taskforce?kunde=², /taskforce/export.csv?kunde=²,
    # /taskforce/api/angebote?kunde=² sowie /aktivitaet?tage=² gaben 500 - während alle
    # vier Reihen grün blieben.
    _riesig = "9" * 20
    _kaputt = (_riesig, "²")
    _zahlparameter = ("kunde", "kid", "coach", "tage", "seite", "eingang_id", "kunde_id")
    _zuviel = []
    _getuert = 0
    for regel in sorted(A.app.url_map.iter_rules(), key=lambda r: str(r)):
        # `taskforce_profil_lauf` greift nach aussen und wird wie in Abschnitt 1 nicht
        # ausgeloest - haelt die Schranke nicht, faengt er sonst an zu suchen.
        if regel.endpoint in ("static", "login", "logout", "taskforce_profil_lauf"):
            continue
        # Die Abfrageparameter werden dort geprueft, wo ein Beispielwert zu bekommen
        # ist - also auf denselben Adressen wie in Abschnitt 1.
        if "GET" in (regel.methods or set()):
            _werte = _beispiel(regel)
            if _werte is not None:
                _adr = regel.build(_werte)[1] if _werte else str(regel)
                for _name in _zahlparameter:
                    for _wert in _kaputt:
                        _getuert += 1
                        if c.get("%s?%s=%s" % (_adr, _name, _wert),
                                 follow_redirects=True).status_code == 500:
                            _zuviel.append("GET %s?%s=%s" % (_adr, _name, _wert[:4]))
        _pfad = str(regel)
        if "<int:" not in _pfad or "<" in re.sub(r"<int:[a-z_]+>", "", _pfad):
            continue                      # andere Platzhalter brauchen echte Werte
        _pfad = re.sub(r"<int:[a-z_]+>", _riesig, _pfad)
        for _methode in ("GET", "POST"):
            if _methode in (regel.methods or set()):
                if c.open(_pfad, method=_methode).status_code == 500:
                    _zuviel.append(f"{_methode} {_pfad}")
    # `_getuert and` aus demselben Grund wie oben: Eine Schleife, die nichts geprueft
    # hat, darf nicht als Beleg durchgehen.
    pruefe("Keine Route stürzt an einer unbrauchbaren Nummer ab – im Pfad wie in der "
           "Abfrage, 20-stellig wie hochgestellt", bool(_getuert) and not _zuviel,
           _zuviel or f"{_getuert} Abfragen")
    # Der Generator `exportieren` liegt nur im OS-Repo. Fehlt er, ist „gibt es nicht"
    # die Antwort – nicht ein ImportError mitten im Aufruf.
    pruefe("/export ohne Generator gibt 404 statt Absturz",
           c.get("/export/kunden").status_code == 404)
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
    # **Die Prüfkette sichert in ihren eigenen Ordner.** Ohne diese Reihe liess sich die
    # Zuweisung von `OS_SICHERUNG_ORDNER` ganz oben in dieser Datei loeschen, ohne dass
    # eine Pruefung rot wurde - und die Kette frass wieder die echten Sicherungsstaende
    # des Repos, einen bei jedem Lauf.
    pruefe("Der Selbsttest sichert nicht in den Ordner des Repos",
           os.path.abspath(betrieb.SICHERUNGEN)
           != os.path.abspath(os.path.join(HIER, "sicherungen")), betrieb.SICHERUNGEN)
    _seite_betrieb = c.get("/betrieb").get_data(as_text=True)
    pruefe("Betriebsseite zeigt Uhrzeiten und Staende",
           all(x in _seite_betrieb
               for x in ("Sicherungsstände", "Letzte Sicherung", betrieb.UHRZEIT_LAUF)))
    # **Was die Seite verspricht, muss das Modul auch tun.** Die Vorlage sagte, es
    # blieben „die N jüngsten Stände" liegen – gezählt werden seit dieser Runde
    # Kalendertage **und** Dateien. Auf den Text der Vorlage sah bis dahin keine
    # einzige Pruefung.
    _seite_betrieb_text = " ".join(_seite_betrieb.split())
    pruefe("Betriebsseite beschreibt die Aufbewahrung nach Tagen und Dateien",
           "je Kalendertag" in _seite_betrieb_text
           and "jüngsten Dateien" in _seite_betrieb_text,
           "%d Stände/Tage" % betrieb.STAENDE)
    pruefe("Was heute lief, laeuft nicht zweimal",
           betrieb.schon_gelaufen("sicherung") and not betrieb._faellig("00:00", "sicherung"))
    pruefe("Abgeschaltete Zeitsteuerung loest nichts aus",
           not betrieb._faellig("aus", "agenten") and not betrieb._faellig("", "agenten"))
    import datetime as _dt

    def _tag(n):
        """Der Kalendertag vor n Tagen – Probedateien mit festem Datum wären ab dem
        achten Tag nach dem Schreiben des Tests etwas anderes als gemeint."""
        return (betrieb.jetzt() - _dt.timedelta(days=n)).strftime("%Y-%m-%d")

    # **Der jüngste Stand ist der mit dem jüngsten Namen, nicht der zuletzt
    # angefasste.** Der Fall: Ein Stand wird zurückkopiert oder von einem Abgleich
    # angefasst – alter Name, frisches Änderungsdatum. `juengster_stand` nahm
    # `max(..., key=os.path.getmtime)` und hielt ihn für den jüngsten; gemessen
    # meldete `alter_stunden` 0,0 h, während der wirklich jüngste Stand 30 h alt war.
    # Zwei Stände im Ordner reichten, um diese Prüfung umzudrehen – deshalb steht sie
    # hier mit zweien.
    _ordner_echt = betrieb.SICHERUNGEN
    _probeordner = os.path.join(os.path.dirname(kopie), "sicherungen_probe")
    os.makedirs(_probeordner, exist_ok=True)
    try:
        betrieb.SICHERUNGEN = _probeordner
        _jung = os.path.join(_probeordner, "improfy_os_%s_0300.db" % _tag(1))
        _alt_name = os.path.join(_probeordner, "improfy_os_%s_0300.db" % _tag(6))
        for _p in (_jung, _alt_name):
            with open(_p, "wb") as _f:
                _f.write(b"Probe")
        _nun = betrieb.jetzt().timestamp()
        os.utime(_jung, (_nun - 30 * 3600, _nun - 30 * 3600))   # echter jüngster Stand
        os.utime(_alt_name, (_nun, _nun))                       # frisch zurückkopiert
        pruefe("Der jüngste Stand ist der mit dem jüngsten Namen, nicht der zuletzt "
               "angefasste",
               os.path.basename(betrieb.juengster_stand()) == os.path.basename(_jung)
               and (betrieb.alter_stunden() or 0) >= 24,
               "%s · %.1f h" % (os.path.basename(betrieb.juengster_stand() or ""),
                                betrieb.alter_stunden() or -1))
        for _p in (_jung, _alt_name):
            os.remove(_p)
        # **Und das Alter kommt aus dem Namen, auch wenn die Datei frisch ist.** Die
        # Reihe darüber misst diesen Zweig nicht: Dort trägt der jüngste Stand ein
        # altes Änderungsdatum, das Alter käme also schon aus `getmtime` allein. Dreht
        # man `alter_stunden` auf reines `getmtime` zurück, wird dort nichts rot –
        # gedreht wird es erst hier. Aufgebaut mit einem einzigen Stand: alter Name,
        # Änderungsdatum von eben. Der Fall aus dem Alltag: Jemand spielt einen Stand
        # vom 20.09. aus einem Zweitordner zurück; ohne den Namenszweig (`max` über
        # beide Angaben) meldet `alter_stunden` 0,0 h und zwei Tage Arbeit gelten als
        # gesichert. Mitgeprüft wird, dass das Änderungsdatum wirklich frisch ist –
        # sonst könnte die Reihe auch ohne den Namenszweig grün sein.
        _zurueck = os.path.join(_probeordner, "improfy_os_%s_0300.db" % _tag(2))
        with open(_zurueck, "wb") as _f:
            _f.write(b"Probe")
        _nun = betrieb.jetzt().timestamp()
        os.utime(_zurueck, (_nun, _nun))
        _nach_datei = (_nun - os.path.getmtime(_zurueck)) / 3600.0
        pruefe("Ein zurückgespielter Stand ist so alt wie sein Name, nicht wie seine "
               "Datei",
               os.path.basename(betrieb.juengster_stand() or "")
               == os.path.basename(_zurueck)
               and (betrieb.alter_stunden() or 0) >= 24 and _nach_datei < 1,
               "aus dem Namen %.1f h · aus dem Änderungsdatum %.1f h"
               % (betrieb.alter_stunden() or -1, _nach_datei))
        os.remove(_zurueck)
        # **Sieben Stände eines einzigen vergangenen Tages bleiben alle liegen.**
        # Genau die Lage vom 22.09.2026: sieben Stände aus 86 Minuten. Die Regel
        # „je Kalendertag einer" hätte am Folgetag sechs davon weggeworfen – unter
        # der alten Regel „die sieben jüngsten Dateien" hätten sie eine Woche
        # überlebt. Beides gilt jetzt zusammen.
        _sieben = ["improfy_os_%s_%02d00.db" % (_tag(1), _s) for _s in range(7, 14)]
        for _n in _sieben:
            with open(os.path.join(_probeordner, _n), "wb") as _f:
                _f.write(b"Probe")
        _uebrig7 = {os.path.basename(p) for p in betrieb.aufraeumen()}
        pruefe("Sieben Stände eines vergangenen Tages bleiben alle liegen",
               _uebrig7 == set(_sieben), sorted(_uebrig7))
    finally:
        betrieb.SICHERUNGEN = _ordner_echt
        for _n in os.listdir(_probeordner):
            os.remove(os.path.join(_probeordner, _n))
        os.rmdir(_probeordner)
    # Und der Knopf sichert bei jedem Druck - ein frischer Stand haelt ihn nicht auf.
    _zahl = len(betrieb.staende())
    c.post("/betrieb/sichern")
    pruefe("Der Knopf /betrieb/sichern sichert auch bei frischem Stand",
           len(betrieb.staende()) >= _zahl, f"{_zahl} → {len(betrieb.staende())}")
    # **Der Name muss Sekunden tragen.** `sichern` schreibt den Stand auf einen Namen
    # aus Datum und Uhrzeit; der kannte nur Minuten. Dreimal „Jetzt sichern" in
    # derselben Minute ergab damit **eine** Datei, gemeldet wurde dreimal „Gesichert:
    # …" - der zweite und dritte Stand ueberschrieben den ersten still. Genau die
    # Lage, in der jemand vor einem riskanten Schritt zweimal hintereinander sichert.
    #
    # Gemessen wird mit gestellter Uhr, nicht mit Wartezeit: Zwei Knopfdruecke des
    # Selbsttests liegen in **derselben Sekunde** (beide Staende hiessen am 22.09.2026
    # improfy_os_2026-09-22_193159.db) - darueber laesst sich die Regel nicht pruefen.
    # Eine Wartesekunde wiederum koennte die Minute wechseln, und dann waere die Reihe
    # auch mit dem alten Namen gruen. Drei Sekunden derselben Minute sind eindeutig:
    # Ohne Sekunden im Namen gaebe es diese drei Dateien nicht.
    _jetzt_echt = betrieb.jetzt
    _minute = betrieb.jetzt().replace(second=0, microsecond=0)
    try:
        for _s in (5, 6, 7):
            betrieb.jetzt = (lambda _z=_minute.replace(second=_s): _z)
            betrieb.sichern()
    finally:
        betrieb.jetzt = _jetzt_echt
    _erwartet = {_minute.replace(second=_s).strftime("improfy_os_%Y-%m-%d_%H%M%S.db")
                 for _s in (5, 6, 7)}
    pruefe("Dreimal sichern in derselben Minute ergibt drei Stände",
           _erwartet <= {s["name"] for s in betrieb.staende()}, sorted(_erwartet))
    # **Aufgeräumt wird nach Kalendertagen – und nie unter die N jüngsten Dateien.**
    # Sonst frisst ein Vormittag mit sieben Sicherungen die sieben Tage Historie auf
    # (alte Regel), oder der Folgetag wirft sechs Stände desselben Vormittags weg (die
    # Tagesregel allein). Nachgestellt mit angelegten Dateinamen über neun Tage: Von
    # jedem der letzten Tage bleibt einer, von heute alles, was älter ist als die
    # Tagesspanne fällt weg – und die N jüngsten Dateien bleiben in jedem Fall liegen.
    import glob as _glob
    _proben = ["improfy_os_%s_%02d00.db" % (_tag(t), stunde)
               for t in range(1, 10) for stunde in (8, 20)]
    for _n in _proben:
        with open(os.path.join(betrieb.SICHERUNGEN, _n), "wb") as _f:
            _f.write(b"Probe")
    _vor_raeumen = betrieb._dateien()       # jüngster zuerst
    _behalten = betrieb.aufraeumen()
    _uebrig = {os.path.basename(p) for p in _behalten}
    _heute = betrieb.jetzt().strftime("%Y-%m-%d")
    _von_heute = [p for p in _glob.glob(os.path.join(betrieb.SICHERUNGEN, "*.db"))
                  if _heute in os.path.basename(p)]
    pruefe("Je Kalendertag bleibt ein Stand – und von heute alle",
           "improfy_os_%s_2000.db" % _tag(6) in _uebrig
           and "improfy_os_%s_0800.db" % _tag(6) not in _uebrig
           and "improfy_os_%s_2000.db" % _tag(5) in _uebrig
           and len(_von_heute) >= _zahl,
           sorted(_uebrig)[:4])
    pruefe("Was älter ist als die Tagesspanne, fällt weg",
           not [n for n in _uebrig if n.startswith("improfy_os_%s" % _tag(betrieb.STAENDE))
                or n.startswith("improfy_os_%s" % _tag(betrieb.STAENDE + 2))],
           "%s und %s" % (_tag(betrieb.STAENDE), _tag(betrieb.STAENDE + 2)))
    pruefe("Die %d jüngsten Dateien bleiben in jedem Fall liegen" % betrieb.STAENDE,
           all(p in _behalten for p in _vor_raeumen[:betrieb.STAENDE]),
           [os.path.basename(p) for p in _vor_raeumen[:betrieb.STAENDE]
            if p not in _behalten] or "alle da")
    for _n in _proben:                     # Probedateien wieder wegräumen
        _p = os.path.join(betrieb.SICHERUNGEN, _n)
        if os.path.exists(_p):
            os.remove(_p)

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
    # Alle Nummern und Namen in diesem Abschnitt sind erfunden (Endung lauter Nullen,
    # Mustername). Hier standen die Handynummern zweier Kundinnen und der volle Name
    # eines Kunden samt seiner Nummer - in einer versionierten Datei hat das nichts zu
    # suchen. Geprueft wird unveraendert dasselbe: die drei Schreibweisen einer
    # deutschen Nummer, der Text mit Vornamen, der Link mit Nummer.
    pruefe("Deutsche Nummern werden korrekt umgeformt",
           nachrichten.nummer("+49 177 0000000") == "491770000000"
           and nachrichten.nummer("0176 0000000") == "491760000000"
           and nachrichten.nummer("01760000000") == "491760000000")
    pruefe("Auslandsnummern behalten ihre Vorwahl",
           nachrichten.nummer("+43 660 1234567") == "436601234567"
           and nachrichten.nummer("0043 660 1234567") == "436601234567")
    pruefe("Unbrauchbares gibt keine Nummer zurueck",
           nachrichten.nummer("kaputt") == "" and nachrichten.nummer("") == ""
           and nachrichten.nummer("123") == "")
    text = nachrichten.text_bauen(
        {"name": "Nabil Musterbewerber"},
        [{"art": "job", "titel": "Lagerhelfer", "anbieter": "Amazon", "ort": "Köln"},
         {"art": "wohnung", "titel": "2 Zimmer", "anbieter": "Privat", "ort": "Kalk"}])
    pruefe("Nachricht nennt Stellen und Wohnungen getrennt",
           "1 Stelle" in text and "1 Wohnung" in text and "Nabil" in text
           and "Improfy-Team" in text, text.split(chr(10))[0])
    link = nachrichten.wa_link("+49 152 0000000", text)
    pruefe("WhatsApp-Link traegt Nummer und Text",
           link.startswith("https://wa.me/491520000000?text=") and len(link) > 100)
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

    print("\n10. Die Arbeitskopie lässt den Echtbestand in Ruhe")
    import hashlib
    import sqlite3
    import time

    def _abdruck(pfad):
        h = hashlib.sha1()
        with open(pfad, "rb") as f:
            for stueck in iter(lambda: f.read(1 << 20), b""):
                h.update(stueck)
        return os.path.getsize(pfad), h.hexdigest()

    # Der Selbsttest lebt davon, dass `improfy_os.db` unberuehrt bleibt - geprueft hat
    # das bisher nichts: `mode=ro` durch `mode=rw` ersetzt, oder `pruefkopie` durch
    # `shutil.copy`, und keine Reihe waere rot geworden.
    # Gemessen wird **eng**, unmittelbar vor und nach dem Kopieren. Ein Abdruck ueber
    # den ganzen Lauf waere wertlos: Auf dem Echtbestand arbeitet im Alltag ein
    # Vorschau-Server mit Zeitsteuerung, der ohne Zutun der Tests schreibt. Genau
    # deshalb sind drei Anlaeufe erlaubt - eine Aenderung von aussen trifft nicht jeden,
    # eine Aenderung durch das Kopieren jeden.
    _quelle = pruefkopie.QUELLE
    _ruhig = False
    for _versuch in range(3):
        _vor = _abdruck(_quelle)
        _probe = pruefkopie.anlegen("os_test_pruefkopie_probe.db")
        _nach = _abdruck(_quelle)
        if _vor == _nach:
            _ruhig = True
            break
        time.sleep(0.5)
    pruefe("Das Anlegen der Arbeitskopie ändert den Echtbestand nicht", _ruhig,
           f"{_vor[0]} Bytes, {_vor[1][:12]} → {_nach[1][:12]}")
    # **Beide Verbindungen werden geschlossen.** Hier stand zweimal ein blankes
    # `sqlite3.connect(...)`; die offene Datei liess danach `os.remove` weiter unten und
    # den `atexit`-rmtree in `pruefkopie` stumm scheitern (beide mit `ignore_errors`).
    # Gemessen: Nach einem Lauf bei leerem Temp-Verzeichnis blieb
    # `os_test_pruefkopie_probe.db` mit 2 400 256 Bytes liegen - der vollstaendige
    # personenbezogene Bestand, 120 Kunden.
    _in_kopie = sqlite3.connect(_probe)
    _in_quelle = sqlite3.connect(pruefkopie.leseadresse(), uri=True)
    try:
        pruefe("Die Kopie trägt denselben Bestand wie die Quelle",
               _in_kopie.execute("SELECT count(*) FROM kunde").fetchone()[0]
               == _in_quelle.execute("SELECT count(*) FROM kunde").fetchone()[0])
    finally:
        _in_kopie.close()
        _in_quelle.close()
    # Die Quelle wird nur lesend geoeffnet. Ein Schreibversuch muss abprallen - das ist
    # die Aussage, auf die sich jeder Testlauf verlaesst.
    _ro = sqlite3.connect(pruefkopie.leseadresse(), uri=True)
    try:
        _ro.execute("CREATE TABLE _probe_schreibschutz (x)")
        _abgewiesen = ""
    except sqlite3.OperationalError as _e:
        _abgewiesen = str(_e)
    finally:
        _ro.close()
    pruefe("Ein Schreibversuch auf der geöffneten Quelle wird abgewiesen",
           "readonly" in _abgewiesen, _abgewiesen or "durchgelassen")
    # Zwei Laeufe aus verschiedenen Arbeitsbaeumen trafen dieselbe Zieldatei; der zweite
    # blieb im `backup()` haengen. Der Pfad traegt deshalb Arbeitsbaum und Prozessnummer.
    pruefe("Jeder Lauf hat seine eigene Zieldatei",
           str(os.getpid()) in _probe
           and os.path.dirname(_probe) != os.path.dirname(os.path.dirname(_probe)),
           _probe)
    # Und es wird nicht mehr endlos gewartet: 45 Sekunden ohne eine Zeile Ausgabe waren
    # keine Auskunft. Mit einer Frist von null greift das Zeitlimit sofort - geprueft
    # wird, dass es ueberhaupt greift und einen Namen hat.
    _t0 = time.monotonic()
    try:
        pruefkopie.anlegen("os_test_pruefkopie_frist.db", zeitgrenze=0)
        _frist = ""
    except pruefkopie.Zeitueberschreitung as _e:
        _frist = str(_e)
    pruefe("Das Kopieren läuft in eine Frist statt ins Endlose",
           bool(_frist) and "nicht durch" in _frist and time.monotonic() - _t0 < 5,
           (_frist.splitlines() or ["ohne Zeitlimit durchgelaufen"])[0][:90])
    _reste = []
    for _weg in (_probe, os.path.join(pruefkopie.ordner(), "os_test_pruefkopie_frist.db")):
        try:
            os.remove(_weg)
        except OSError:
            pass
        if os.path.exists(_weg):
            _reste.append(os.path.basename(_weg))
    # Der Waechter fuer die offenen Verbindungen oben: Bleibt eine stehen, laesst sich
    # die Probe unter Windows nicht loeschen und liegt mit dem ganzen Kundenbestand im
    # Temp-Verzeichnis. Vorher wurde das von nichts bemerkt - `os.remove` steht in einem
    # `try: ... except OSError: pass`.
    pruefe("Die Proben dieses Abschnitts bleiben nicht liegen", not _reste, _reste)
    # **Und der Ordner eines beendeten Laufs auch nicht.** Geprueft an einem echten
    # zweiten Prozess: Er legt eine Arbeitskopie an und endet; danach darf sein Ordner
    # nicht mehr dasein. Ohne den `atexit`-Haken in `pruefkopie` bliebe er stehen, und
    # keine Reihe wuerde rot.
    import subprocess
    _kind = subprocess.run(
        [sys.executable, "-X", "utf8", "-c",
         "import sys; sys.path.insert(0, r'%s'); import pruefkopie;"
         " print(pruefkopie.anlegen('os_test_kindkopie.db'))" % HIER],
        capture_output=True, text=True, timeout=180)
    _kindpfad = ((_kind.stdout or "").strip().splitlines() or [""])[-1]
    pruefe("Ein beendeter Lauf lässt seine Arbeitskopie nicht liegen",
           bool(_kindpfad) and os.path.isabs(_kindpfad)
           and not os.path.exists(os.path.dirname(_kindpfad)),
           _kindpfad or (_kind.stderr or "")[-120:])
    # **Was ein abgestuerzter Lauf liegen laesst, holt der naechste.** Nachgestellt mit
    # dem Ordner des eben beendeten Kindes: Seine Prozessnummer lebt nicht mehr, also
    # muss `aufraeumen` ihn wegraeumen - ohne diesen Weg blieben die Arbeitskopien
    # abgebrochener Laeufe mit je 2,4 MB Kundendaten bis zum naechsten Neustart liegen.
    _totenordner = os.path.dirname(_kindpfad) if _kindpfad else ""
    if _totenordner:
        os.makedirs(_totenordner, exist_ok=True)
        with open(os.path.join(_totenordner, "rest.db"), "wb") as _f:
            _f.write(b"Rest")
    pruefe("Der Ordner eines beendeten Laufs wird weggeräumt",
           bool(_totenordner) and _totenordner in pruefkopie.aufraeumen()
           and not os.path.exists(_totenordner), _totenordner or "kein Kindordner")
    # **Der Raeumer greift nur nach dem, was er selbst anlegt.** Das Muster hiess einmal
    # `-(\d+)$` und traf jeden Ordner, der auf Bindestrich und Ziffern endet: Ein
    # danebenliegender Ordner `messung-2026` mit Messwerten wurde weggeraeumt.
    _fremd = ["messung-2026", "notizen", "vergleich_17"]
    for _n in _fremd:
        os.makedirs(os.path.join(pruefkopie.SAMMELORDNER, _n), exist_ok=True)
    _eigener = pruefkopie.ordner()          # der Ordner dieses (lebenden) Laufs
    _geraeumt = [os.path.basename(p) for p in pruefkopie.aufraeumen()]
    pruefe("Der Räumer lässt fremde Ordner und den Ordner des laufenden Laufs stehen",
           not [n for n in _fremd if n in _geraeumt] and os.path.isdir(_eigener)
           and all(os.path.isdir(os.path.join(pruefkopie.SAMMELORDNER, n))
                   for n in _fremd),
           _geraeumt or "nichts geräumt")
    for _n in _fremd:                       # eigene Probeordner wieder wegräumen
        _p = os.path.join(pruefkopie.SAMMELORDNER, _n)
        if os.path.isdir(_p):
            os.rmdir(_p)

    fehl = [n for n, ok in ergebnis if not ok]
    print(f"\n{len(ergebnis) - len(fehl)} von {len(ergebnis)} Prüfungen bestanden.")
    if fehl:
        print("Fehlgeschlagen:", *fehl, sep="\n  - ")
    return 0 if not fehl else 1


if __name__ == "__main__":
    sys.exit(main())
