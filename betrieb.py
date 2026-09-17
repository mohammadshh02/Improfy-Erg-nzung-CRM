# -*- coding: utf-8 -*-
"""Betrieb: nächtliche Sicherung der Datenbank und selbsttätiger Lauf der Agenten.

Zwei Dinge, die jedes ernsthafte System hat und die hier fehlten:

**Sicherung.** Die ganze Arbeit von 120 Akten steckt in einer einzigen Datei. Eine kaputte
Datei, ein versehentliches Löschen, ein Festplattenfehler – und alles ist weg. Gesichert
wird mit `sqlite3.Connection.backup()`, nicht mit Kopieren: Das ist auch dann sicher, wenn
gerade jemand im OS arbeitet. Sieben Stände bleiben liegen, der älteste fällt raus.

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
import sqlite3
import threading
import time

import datenbank as db

HIER = os.path.dirname(os.path.abspath(__file__))
SICHERUNGEN = os.environ.get("OS_SICHERUNG_ORDNER") or os.path.join(HIER, "sicherungen")
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
    name = f"improfy_os_{jetzt():%Y-%m-%d_%H%M}.db"
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


def aufraeumen():
    """Nur die jüngsten Stände behalten – sonst läuft irgendwann die Platte voll."""
    vorhanden = sorted(glob.glob(os.path.join(SICHERUNGEN, "improfy_os_*.db")), reverse=True)
    for alt in vorhanden[STAENDE:]:
        try:
            os.remove(alt)
        except OSError:
            pass
    return vorhanden[:STAENDE]


def staende():
    return [{"name": os.path.basename(p), "bytes": os.path.getsize(p),
             "zeit": datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")}
            for p in sorted(glob.glob(os.path.join(SICHERUNGEN, "improfy_os_*.db")), reverse=True)]


# ------------------------------------------------------- Nächtlicher Agentenlauf
def agenten_lauf():
    """Alle aktiven Taskforce-Profile durchlaufen. Gibt eine lesbare Bilanz zurück."""
    import taskforce as tf
    ergebnisse = tf.alle_laufen()
    neu = sum(n for _, _, _, n, _ in ergebnisse)
    gefunden = sum(g for _, _, g, _, _ in ergebnisse)
    return f"{len(ergebnisse)} Profile · {gefunden} gefunden · {neu} neu"


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
_lauf = {"laeuft": False, "seit": None, "ergebnis": None, "fehler": None}


def lauf_starten():
    """Gibt True zurück, wenn der Lauf angestoßen wurde, False wenn schon einer läuft."""
    if _lauf["laeuft"]:
        return False
    _lauf.update(laeuft=True, seit=jetzt().isoformat(timespec="seconds"),
                 ergebnis=None, fehler=None)

    def arbeiten():
        try:
            _lauf["ergebnis"] = agenten_lauf()
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
