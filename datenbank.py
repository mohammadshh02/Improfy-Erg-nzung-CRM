# -*- coding: utf-8 -*-
"""Datenhaltung des Improfy-OS.

Eine einzige SQLite-Datei haelt den gesamten Standort-Zustand. Warum SQLite und
kein Server: die Daten passen in wenige Megabyte, es gibt genau einen Schreiber
(den Import), und eine Datei laesst sich sichern, indem man sie kopiert.

Jede Tabelle traegt ein Feld `standort`. Heute steht dort ueberall "Koeln".
Sobald das OS auf weitere Standorte ausgerollt wird, filtert die Oberflaeche
darueber - es muss nichts umgebaut werden.
"""
import os
import sqlite3
from contextlib import contextmanager

HIER = os.path.dirname(os.path.abspath(__file__))
DB_DATEI = os.environ.get("IMPROFY_OS_DB", os.path.join(HIER, "improfy_os.db"))

STANDORT_STANDARD = "Köln"

# Die Statuscodes stammen aus der bestehenden Gesamtuebersicht. Sie sind bewusst
# unveraendert uebernommen, damit alte und neue Auswertungen vergleichbar bleiben.
STATUS = {
    "A": "Antrag nie rausgegangen",
    "B": "Antrag zurückgewiesen (👎) / Neufassung nötig",
    "C": "Anschreiben fertig – Versand prüfen",
    "D": "Antrag-PDF liegt vor – Anschreiben fehlt",
    "E": "Antrag raus – wartet auf Jobcenter",
    "F": "Antrag hängt beim Kunden (nicht abgegeben)",
    "G": "Gutschein abgelehnt",
    "H": "Gutschein da – Maßnahme läuft / startet",
    "I": "Gutschein in Klärung",
    "J": "Maßnahme abgeschlossen – Rechnung offen",
    "K": "Lead ohne Antrag",
    "L": "Kunde pausiert / abgesprungen",
}

# Welche Status zaehlen als "noch in Bearbeitung"? Braucht die Kapazitaetsrechnung.
STATUS_OFFEN = set("ABCDEFIK")
STATUS_LAEUFT = {"H"}          # bindet Coach-Zeit
STATUS_ERLEDIGT = {"G", "J", "L"}

# Ampel fuer die Kundenuebersicht: rot = haengt, gelb = wartet, gruen = laeuft.
STATUS_AMPEL = {
    "A": "rot", "B": "rot", "D": "rot", "F": "rot",
    "C": "gelb", "E": "gelb", "I": "gelb", "K": "gelb",
    "H": "gruen", "J": "gruen",
    "G": "grau", "L": "grau",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS mitarbeiter (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    kuerzel     TEXT,                      -- so, wie der Coach in den Listen auftaucht
    email       TEXT,
    rolle       TEXT DEFAULT 'Coach',
    standort    TEXT NOT NULL,
    aktiv       INTEGER DEFAULT 1,
    wochenstunden REAL DEFAULT 40,
    UNIQUE(name, standort)
);

CREATE TABLE IF NOT EXISTS kunde (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    coach_id        INTEGER REFERENCES mitarbeiter(id),
    sprache         TEXT,
    telefon         TEXT,
    email           TEXT,
    ort_jc          TEXT,
    kundennummer    TEXT,
    antrag_datum    TEXT,
    massnahme       TEXT,
    chat_reaktion   TEXT,
    anschreiben     TEXT,
    gutschein_status TEXT,
    gutschein_nr    TEXT,
    rueckmeldung    TEXT,
    status_code     TEXT,
    naechster_schritt TEXT,
    hinweis         TEXT,
    standort        TEXT NOT NULL,
    -- Aus welcher Quelle stammt der letzte Stand dieses Satzes? Der
    -- Bestands-Snapshot darf nur ueberschreiben, was er selbst geschrieben hat.
    quelle_stand    TEXT DEFAULT 'Bestand',
    stand_am        TEXT,
    UNIQUE(name, standort)
);

CREATE TABLE IF NOT EXISTS lead (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    kunde_id     INTEGER REFERENCES kunde(id),
    coach_id     INTEGER REFERENCES mitarbeiter(id),
    quelle       TEXT,                     -- Online, Empfehlung, Jobcenter, ...
    eingang      TEXT,                     -- ISO-Datum
    termin       TEXT,                     -- ISO-Datum, leer = nie terminiert
    status       TEXT,
    standort     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS termin (
    id         INTEGER PRIMARY KEY,
    kunde_id   INTEGER REFERENCES kunde(id),
    coach_id   INTEGER REFERENCES mitarbeiter(id),
    beginn     TEXT NOT NULL,              -- ISO-Zeitstempel
    ende       TEXT,
    titel      TEXT,
    art        TEXT,                       -- Erstgespraech, Coaching, AV-Telefonat, ...
    quelle     TEXT,                       -- Kalender, Chat, manuell
    standort   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gutschein (
    id          INTEGER PRIMARY KEY,
    kunde_id    INTEGER REFERENCES kunde(id),
    nummer      TEXT,
    von         TEXT,
    bis         TEXT,
    ue          INTEGER,                   -- Unterrichtseinheiten
    massnahme   TEXT,
    status      TEXT,
    standort    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qm_katalog (
    schluessel  TEXT PRIMARY KEY,
    bezeichnung TEXT NOT NULL,
    pflicht     INTEGER DEFAULT 1,
    gilt_fuer   TEXT DEFAULT 'alle',       -- 'alle' oder ein Massnahme-Name
    reihenfolge INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dokument (
    id          INTEGER PRIMARY KEY,
    kunde_id    INTEGER REFERENCES kunde(id),
    schluessel  TEXT REFERENCES qm_katalog(schluessel),
    vorhanden   INTEGER DEFAULT 0,
    dateiname   TEXT,
    geaendert   TEXT,
    geprueft    TEXT,
    UNIQUE(kunde_id, schluessel)
);

CREATE TABLE IF NOT EXISTS aktivitaet (
    id           INTEGER PRIMARY KEY,
    mitarbeiter_id INTEGER REFERENCES mitarbeiter(id),
    kunde_id     INTEGER REFERENCES kunde(id),
    zeitpunkt    TEXT NOT NULL,
    quelle       TEXT,                     -- Drive, Chat, Kalender, HubSpot
    art          TEXT,                     -- hochgeladen, geaendert, Termin, Nachricht
    beschreibung TEXT,
    standort     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_lauf (
    id        INTEGER PRIMARY KEY,
    quelle    TEXT NOT NULL,
    zeitpunkt TEXT NOT NULL,
    anzahl    INTEGER,
    status    TEXT,
    meldung   TEXT
);

CREATE INDEX IF NOT EXISTS idx_kunde_coach  ON kunde(coach_id);
CREATE INDEX IF NOT EXISTS idx_kunde_status ON kunde(status_code);
CREATE INDEX IF NOT EXISTS idx_termin_coach ON termin(coach_id, beginn);
CREATE INDEX IF NOT EXISTS idx_lead_eingang ON lead(eingang);
CREATE INDEX IF NOT EXISTS idx_akt_ma       ON aktivitaet(mitarbeiter_id, zeitpunkt);
"""


def verbindung():
    con = sqlite3.connect(DB_DATEI)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def offen():
    con = verbindung()
    try:
        yield con
        con.commit()
    finally:
        con.close()


# Spalten, die spaeter dazugekommen sind. SQLite kennt kein
# "ADD COLUMN IF NOT EXISTS", deshalb wird vorher nachgesehen.
NACHRUESTEN = [
    ("kunde", "quelle_stand", "TEXT DEFAULT 'Bestand'"),
    ("kunde", "stand_am", "TEXT"),
    ("kunde", "absage_grund", "TEXT"),
    # Von HubSpot, Vertrieb und Nachfassen vorausgesetzt, aber in keinem Schema
    # angelegt - auf einer frischen Datenbank fielen /vertrieb, /nachfassen und
    # die Mitarbeiter-Akte mit "no such column: stadt" um.
    ("kunde", "stadt", "TEXT"),
    ("kunde", "plz", "TEXT"),
    ("kunde", "jobcenter", "TEXT"),
    ("kunde", "drive_id", "TEXT"),
]


QUELLEN_MIT_SCHEMA = ("av_liste", "coach_rueckmeldung", "crm", "gutscheinliste", "hubspot_deals",
                      "kundenmappe", "lead_status", "lebenslauf", "messe", "telefonbuch")


def _quellen_schemata():
    """Tabellen der Datenquellen anlegen, auch wenn die Quelle nie gelaufen ist.

    Vorher legte jede Quelle ihre Tabelle erst beim eigenen Import an. Wurde sie
    uebersprungen (fehlender Export), fiel das Dashboard beim ersten Zugriff mit
    'no such table: rueckmeldung' um. Die Kennzahlen duerfen leere Tabellen
    voraussetzen, keine fehlenden."""
    import importlib
    for name in QUELLEN_MIT_SCHEMA:
        try:
            yield importlib.import_module(f"quellen.{name}").SCHEMA
        except Exception:
            continue


def init():
    with offen() as con:
        con.executescript(SCHEMA)
        for schema in _quellen_schemata():
            con.executescript(schema)
        for tabelle, spalte, typ in NACHRUESTEN:
            vorhanden = {r[1] for r in con.execute(f"PRAGMA table_info({tabelle})")}
            if spalte not in vorhanden:
                con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")


def hole(sql, args=()):
    with offen() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def eine(sql, args=()):
    zeilen = hole(sql, args)
    return zeilen[0] if zeilen else None


def wert(sql, args=(), standard=0):
    with offen() as con:
        r = con.execute(sql, args).fetchone()
    if not r or r[0] is None:
        return standard
    return r[0]
