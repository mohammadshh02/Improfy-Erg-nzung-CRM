# -*- coding: utf-8 -*-
"""Betrieb: nächtliche Sicherung der Datenbank und selbsttätiger Lauf der Agenten.

Zwei Dinge, die jedes ernsthafte System hat und die hier fehlten:

**Sicherung.** Die ganze Arbeit von 120 Akten steckt in einer einzigen Datei. Eine kaputte
Datei, ein versehentliches Löschen, ein Festplattenfehler – und alles ist weg. Gesichert
wird mit `sqlite3.Connection.backup()`, nicht mit Kopieren: Das ist auch dann sicher, wenn
gerade jemand im OS arbeitet. Gehalten wird je Kalendertag ein Stand, sieben Tage weit
zurück – **und** darüber hinaus immer die sieben jüngsten Dateien: Eine Prüfkette, die
an einem Vormittag mehrmals sichert, hatte mit der alten Regel die ganze Historie
überschrieben, die neue allein hätte am Folgetag sechs Stände desselben Vormittags
weggeworfen.

**Selbsttätiger Lauf.** Die Agenten liefen nur, wenn jemand auf den Knopf drückte – und
niemand drückte. Jetzt laufen sie nachts von selbst, und morgens liegt die Liste da. Die
Uhrzeit steht in `TF_LAUF_UHRZEIT` (Vorgabe 05:30), abschalten mit `TF_LAUF_UHRZEIT=aus`.

**Warum ein Faden im Prozess und kein Dienst im Betriebssystem.** Das OS läuft als ein
Programm auf einem Rechner. Ein Faden, der schläft und alle paar Minuten schaut, ob er dran
ist, braucht keine Rechte, keine Einrichtung und keinen Dienst, der beim nächsten Windows-
Update vergessen wird. Er hält den Zustand in der Datenbank, übersteht also einen Neustart
und läuft auch dann genau einmal am Tag, wenn der Rechner zwischendurch aus war.
"""
import datetime
import glob
import os
import re
import sqlite3
import threading
import time

import datenbank as db

HIER = os.path.dirname(os.path.abspath(__file__))
SICHERUNGEN = os.environ.get("OS_SICHERUNG_ORDNER") or os.path.join(HIER, "sicherungen")
# **`STAENDE` zählt seit dem 22.09.2026 Kalendertage – und weiterhin Dateien.** Vorher
# blieben nur die sieben jüngsten Dateien liegen, egal wann sie entstanden. Eine
# Prüfkette, die an einem Vormittag siebenmal sichert, schob damit die gesamte Historie
# aus dem Ordner: gemessen lagen am 22.09. sieben Stände aus 86 Minuten, der älteste war
# keine anderthalb Stunden alt. Genau aus diesen Ständen müsste ein zerstörtes Kopffoto
# zurückgeholt werden. Jetzt bleibt je Kalendertag der jüngste Stand stehen (vom
# heutigen Tag alle) – sieben Tage weit zurück –, und zusätzlich immer die sieben
# jüngsten Dateien, damit die neue Regel an keinem Tag weniger aufbewahrt als die alte.
STAENDE = int(os.environ.get("OS_SICHERUNG_STAENDE", "7"))
UHRZEIT_SICHERUNG = os.environ.get("OS_SICHERUNG_UHRZEIT", "03:00")
UHRZEIT_LAUF = os.environ.get("TF_LAUF_UHRZEIT", "05:30")
TAKT = 300          # alle fünf Minuten nachsehen, ob etwas ansteht

SCHEMA = """
CREATE TABLE IF NOT EXISTS betrieb_lauf (
    id        INTEGER PRIMARY KEY,
    art       TEXT NOT NULL,
    zeitpunkt TEXT NOT NULL,
    tag       TEXT NOT NULL,
    ergebnis  TEXT,
    fehler    TEXT
);
CREATE INDEX IF NOT EXISTS idx_betrieb_art ON betrieb_lauf(art, tag);
"""


def init():
    with db.offen() as con:
        con.executescript(SCHEMA)


def jetzt():
    return datetime.datetime.now()


def _notieren(art, ergebnis=None, fehler=None):
    with db.offen() as con:
        con.execute("INSERT INTO betrieb_lauf (art, zeitpunkt, tag, ergebnis, fehler)"
                    " VALUES (?,?,?,?,?)",
                    (art, jetzt().isoformat(timespec="seconds"), jetzt().strftime("%Y-%m-%d"),
                     ergebnis, fehler))


def schon_gelaufen(art, tag=None):
    tag = tag or jetzt().strftime("%Y-%m-%d")
    return bool(db.wert("SELECT COUNT(*) FROM betrieb_lauf WHERE art=? AND tag=? AND fehler IS NULL",
                        (art, tag)))


def letzter(art):
    return db.eine("SELECT * FROM betrieb_lauf WHERE art=? ORDER BY zeitpunkt DESC LIMIT 1", (art,))


def laeufe(limit=20):
    return db.hole("SELECT * FROM betrieb_lauf ORDER BY zeitpunkt DESC LIMIT ?", (limit,))


# ------------------------------------------------------------------- Sicherung
def sichern():
    """Einen Stand wegschreiben und alte Stände aufräumen. Gibt (pfad, bytes) zurück."""
    os.makedirs(SICHERUNGEN, exist_ok=True)
    # **Der Name traegt Sekunden.** Er kannte nur Minuten, und `sichern` schreibt immer
    # auf denselben Namen: Dreimal „Jetzt sichern" in derselben Minute ergab **eine**
    # Datei, gemeldet wurde dreimal „Gesichert: …". Der zweite und dritte Stand
    # ueberschrieben den ersten still - genau die Lage, in der jemand vor einem
    # riskanten Schritt zweimal hintereinander sichert.
    #
    # Die Sortierung in `_dateien` bleibt richtig, auch neben aelteren Namen ohne
    # Sekunden: Der Punkt vor „db" liegt in der Zeichenordnung vor jeder Ziffer, also
    # steht der kurze Name derselben Minute vor dem langen - und der lange ist der
    # juengere. `_zeit_von` liest weiterhin nur Stunde und Minute; die Sekunden fehlen
    # ihm zur sicheren Seite hin (ein Stand gilt hoechstens knapp eine Minute zu alt).
    name = f"improfy_os_{jetzt():%Y-%m-%d_%H%M%S}.db"
    ziel = os.path.join(SICHERUNGEN, name)
    quelle = sqlite3.connect(db.DB_DATEI)
    try:
        sicherung = sqlite3.connect(ziel)
        try:
            quelle.backup(sicherung)       # sicher auch bei laufendem Betrieb
        finally:
            sicherung.close()
    finally:
        quelle.close()
    aufraeumen()
    return ziel, os.path.getsize(ziel)


def _dateien():
    """Alle Sicherungsstände, jüngster zuerst.

    Sortiert wird über den Namen, und der trägt Datum und Uhrzeit – das ist dieselbe
    Reihenfolge wie nach Alter, aber unabhängig davon, was ein Kopiervorgang mit dem
    Änderungsdatum gemacht hat."""
    return sorted(glob.glob(os.path.join(SICHERUNGEN, "improfy_os_*.db")), reverse=True)


def _tag_von(pfad):
    """Der Kalendertag eines Standes – aus dem Dateinamen, sonst aus dem Datum der Datei."""
    treffer = re.search(r"improfy_os_(\d{4}-\d{2}-\d{2})_", os.path.basename(pfad))
    if treffer:
        return treffer.group(1)
    return datetime.date.fromtimestamp(os.path.getmtime(pfad)).isoformat()


def aufraeumen():
    """Je Kalendertag einen Stand behalten – und nie weniger als die `STAENDE` jüngsten.

    Vom heutigen Tag bleibt **jeder** Stand liegen: Wer vor einem riskanten Schritt von
    Hand sichert und danach noch einmal, will beide haben. Von den Tagen davor bleibt
    der jüngste – das reicht, um einen Fehler zu finden, der erst Tage später auffällt,
    und genau das konnte die alte Regel „die sieben jüngsten Dateien" nicht mehr.

    **Beide Regeln zusammen, nicht die eine statt der anderen.** Die Tagesregel allein
    war am Tag nach ihrer Einführung schlechter als die alte: Im Ordner lagen sieben
    Stände vom 22.09. (11:34 bis 13:00); mit dem ersten Lauf am 23.09. wäre der 22.09.
    ein vergangener Tag gewesen und sechs davon wären weggefallen – unter der alten
    Regel hätten sie eine Woche überlebt. Wird heute um 12:00 ein Kopffoto zerstört und
    fällt das übermorgen auf, ist der Stand von 11:34 dann weg. Deshalb gilt zusätzlich
    der alte Boden: Die `STAENDE` jüngsten Dateien bleiben in jedem Fall liegen. So ist
    die Regel in keinem Fall schlechter als vorher, reicht aber weiter zurück."""
    heute = jetzt().strftime("%Y-%m-%d")
    alle = _dateien()                             # jüngster zuerst
    behalten, tage = [], []
    for pfad in alle:
        tag = _tag_von(pfad)
        if tag not in tage:
            if len(tage) >= STAENDE:
                break                             # älter als die letzten Tage: weg
            tage.append(tag)
            behalten.append(pfad)
        elif tag == heute:
            behalten.append(pfad)
    # Der Boden aus der alten Regel. `behalten` bleibt dabei „jüngster zuerst" sortiert,
    # weil die Liste am Ende aus `alle` neu zusammengesetzt wird.
    boden = set(alle[:STAENDE])
    behalten = [p for p in alle if p in boden or p in behalten]
    for alt in [p for p in alle if p not in behalten]:
        try:
            os.remove(alt)
        except OSError:
            pass
    return behalten


def _zeit_von(pfad):
    """Der Zeitpunkt aus dem Dateinamen als Zeitstempel – None, wenn er nicht dasteht."""
    treffer = re.search(r"improfy_os_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})",
                        os.path.basename(pfad))
    if not treffer:
        return None
    try:
        return datetime.datetime.strptime("%s %s:%s" % treffer.groups(),
                                          "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return None


def juengster_stand():
    """Der jüngste Sicherungsstand oder None – dieselbe Reihenfolge wie in `_dateien`.

    **Der Name entscheidet, nicht das Änderungsdatum.** Hier stand vorher
    `max(vorhanden, key=os.path.getmtime)` – also genau das, wovor der Kommentar an
    `_dateien` weiter oben ausdrücklich warnt. Ein zurückkopierter oder
    synchronisierter Stand trägt einen alten Namen und ein frisches Änderungsdatum;
    gemessen wurde damit ein Stand vom 16.09. als der jüngste, obwohl einer vom 21.09.
    danebenlag, und `alter_stunden` meldete daraufhin 0,0 h statt 30 h."""
    vorhanden = _dateien()
    return vorhanden[0] if vorhanden else None


def alter_stunden():
    """Wie alt der jüngste Stand ist. None, wenn es noch keinen gibt.

    Gezählt wird nach der **ungünstigeren** der beiden Angaben, Name und
    Änderungsdatum: Der Name ist die verlässlichere Quelle (ein Kopiervorgang setzt
    das Änderungsdatum neu), das Änderungsdatum die genauere (der Name kennt nur
    Minuten). Wo beide auseinanderfallen, gilt der ältere Wert – wer nach dem Alter
    des jüngsten Standes fragt, soll lieber einmal zu früh an eine Sicherung denken als
    einen Tag zu spät.

    Der Fall, für den der Namenszweig da ist: Jemand spielt einen Stand vom 20.09. aus
    einem Zweitordner zurück. Sein Änderungsdatum ist dann von eben, sein Name nicht –
    ohne den Namen käme hier 0,0 h heraus, und zwei Tage Arbeit gälten als gesichert."""
    pfad = juengster_stand()
    if not pfad:
        return None
    nun = time.time()
    alter = (nun - os.path.getmtime(pfad)) / 3600.0
    aus_namen = _zeit_von(pfad)
    if aus_namen is not None:
        alter = max(alter, (nun - aus_namen) / 3600.0)
    return max(0.0, alter)


def staende():
    return [{"name": os.path.basename(p), "bytes": os.path.getsize(p),
             "zeit": datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")}
            for p in _dateien()]


# ------------------------------------------------------- Nächtlicher Agentenlauf
def agenten_lauf(art=None):
    """Alle aktiven Taskforce-Profile durchlaufen. Gibt eine lesbare Bilanz zurück.

    art grenzt auf eine Haelfte ein ('job' oder 'wohnung'); der naechtliche Lauf laesst es
    weg und nimmt weiter alles."""
    import taskforce as tf
    ergebnisse = tf.alle_laufen(art=art)
    neu = sum(n for _, _, _, n, _ in ergebnisse)
    gefunden = sum(g for _, _, g, _, _ in ergebnisse)
    woran = {"job": "Arbeitssuche", "wohnung": "Wohnungssuche"}.get(art)
    vorn = f"{woran}: " if woran else ""
    return f"{vorn}{len(ergebnisse)} Profile · {gefunden} gefunden · {neu} neu"


# --------------------------------------------------------------------- Zeitplan
def _faellig(uhrzeit, art):
    """Ist die Uhrzeit vorbei und heute noch nichts gelaufen?"""
    if not uhrzeit or uhrzeit.strip().lower() in ("aus", "off", "nein", ""):
        return False
    try:
        stunde, minute = (int(x) for x in uhrzeit.split(":"))
    except (ValueError, TypeError):
        return False
    jetzt_ = jetzt()
    ziel = jetzt_.replace(hour=stunde, minute=minute, second=0, microsecond=0)
    return jetzt_ >= ziel and not schon_gelaufen(art)


def einmal_pruefen():
    """Ein Durchgang: fällige Aufgaben erledigen. Gibt die erledigten Arten zurück."""
    erledigt = []
    if _faellig(UHRZEIT_SICHERUNG, "sicherung"):
        try:
            pfad, groesse = sichern()
            _notieren("sicherung", f"{os.path.basename(pfad)} · {groesse // 1024} kB")
            erledigt.append("sicherung")
        except Exception as e:
            _notieren("sicherung", fehler=str(e)[:300])
    if _faellig(UHRZEIT_LAUF, "agenten"):
        try:
            _notieren("agenten", agenten_lauf())
            erledigt.append("agenten")
        except Exception as e:
            _notieren("agenten", fehler=str(e)[:300])
    return erledigt


# ------------------------------------------------- Lauf auf Knopfdruck, im Hintergrund
# Der Knopf „alle jetzt laufen lassen" hing die Seite, bis zehn Portale geantwortet hatten –
# bis zu zwei Minuten weißer Bildschirm. Jetzt startet der Lauf einen Faden und die Seite
# kommt sofort zurück; der Zustand steht hier und wird oben auf der Tafel angezeigt.
_lauf = {"laeuft": False, "seit": None, "ergebnis": None, "fehler": None, "art": None}


def lauf_starten(art=None):
    """Gibt True zurück, wenn der Lauf angestoßen wurde, False wenn schon einer läuft.

    Es bleibt bei *einem* Lauf gleichzeitig, auch bei getrennten Haelften: zwei parallele
    Laeufe wuerden dieselben Portale doppelt fragen und sich Sperren einhandeln. Die Seite
    schreibt darum mit, welche Haelfte gerade dran ist."""
    if _lauf["laeuft"]:
        return False
    _lauf.update(laeuft=True, seit=jetzt().isoformat(timespec="seconds"),
                 ergebnis=None, fehler=None, art=art)

    def arbeiten():
        try:
            _lauf["ergebnis"] = agenten_lauf(art)
            _notieren("agenten", _lauf["ergebnis"] + " (von Hand)")
        except Exception as e:
            _lauf["fehler"] = str(e)[:300]
            _notieren("agenten", fehler=_lauf["fehler"])
        finally:
            _lauf["laeuft"] = False

    threading.Thread(target=arbeiten, name="improfy-lauf", daemon=True).start()
    return True


def lauf_zustand():
    return dict(_lauf)


_faden = None


def starten():
    """Den Hintergrundfaden starten. Mehrfaches Aufrufen schadet nicht."""
    global _faden
    if _faden and _faden.is_alive():
        return _faden
    init()

    def schleife():
        while True:
            try:
                einmal_pruefen()
            except Exception:
                pass                      # der Faden darf nie sterben
            time.sleep(TAKT)

    _faden = threading.Thread(target=schleife, name="improfy-betrieb", daemon=True)
    _faden.start()
    return _faden


def laeuft():
    return bool(_faden and _faden.is_alive())


def zustand():
    """Was die Betriebsseite anzeigt."""
    return {
        "faden": laeuft(),
        "sicherung_uhrzeit": UHRZEIT_SICHERUNG,
        "lauf_uhrzeit": UHRZEIT_LAUF,
        "staende": staende(),
        "ordner": SICHERUNGEN,
        "letzte_sicherung": letzter("sicherung"),
        "letzter_lauf": letzter("agenten"),
        "heute_gesichert": schon_gelaufen("sicherung"),
        "heute_gelaufen": schon_gelaufen("agenten"),
    }
