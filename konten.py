# -*- coding: utf-8 -*-
"""Benutzerkonten und Änderungsprotokoll – wer hat wann was getan.

**Warum das der wichtigste Baustein ist.** Vorher gab es ein gemeinsames Passwort. Wer
ein Angebot auf „angeschrieben" setzte, wählte seinen Namen aus einer Liste – man konnte
jeden Namen wählen und auch gar keinen. Damit war jede Mitarbeiterzahl Selbstauskunft
statt Nachweis, und bei einer Prüfung wertlos. Jetzt meldet sich jede Person selbst an,
und der Name kommt aus der Anmeldung, nicht aus einem Auswahlfeld.

**Das Protokoll** schreibt jede Änderung mit: Zeitpunkt, Person, was, an welchem Datensatz,
vorher und nachher. Es wird nie gelöscht und nie bearbeitet. Das ist die Grundlage, um bei
einer Prüfung oder einem Streit sagen zu können, was tatsächlich passiert ist.

**Rollen.** Drei Stufen, absichtlich grob – feiner wird es nur unübersichtlich:
  leitung      darf alles, auch Konten anlegen und löschen
  mitarbeiter  darf alles Fachliche, aber keine Konten verwalten
  lesen        darf nur schauen (z. B. Zentrale, Praktikant, Prüfer)

**Übergang ohne Bruch.** Solange kein einziges Konto angelegt ist, läuft das OS wie bisher
mit dem gemeinsamen Passwort weiter. Wer dann das erste Konto anlegt, schaltet den
persönlichen Betrieb ein. So kann niemand sich selbst aussperren.
"""
import datetime
import hashlib
import hmac
import json
import os
import secrets

import datenbank as db

SCHEMA = """
CREATE TABLE IF NOT EXISTS benutzer (
    id            INTEGER PRIMARY KEY,
    anmeldename   TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    rolle         TEXT NOT NULL DEFAULT 'mitarbeiter',
    salz          TEXT NOT NULL,
    hash          TEXT NOT NULL,
    mitarbeiter_id INTEGER,
    aktiv         INTEGER NOT NULL DEFAULT 1,
    angelegt      TEXT,
    letzte_anmeldung TEXT,
    standort      TEXT
);
CREATE TABLE IF NOT EXISTS protokoll (
    id         INTEGER PRIMARY KEY,
    zeitpunkt  TEXT NOT NULL,
    benutzer   TEXT,
    rolle      TEXT,
    aktion     TEXT NOT NULL,
    bereich    TEXT,
    objekt_id  TEXT,
    beschreibung TEXT,
    vorher     TEXT,
    nachher    TEXT
);
CREATE INDEX IF NOT EXISTS idx_protokoll_zeit ON protokoll(zeitpunkt DESC);
CREATE INDEX IF NOT EXISTS idx_protokoll_benutzer ON protokoll(benutzer);
"""

ROLLEN = [("leitung", "Leitung – darf alles, auch Konten verwalten"),
          ("mitarbeiter", "Mitarbeiter – darf alles Fachliche"),
          ("lesen", "Nur lesen – darf nichts ändern")]
ROLLEN_TEXT = dict(ROLLEN)
RUNDEN = 120_000            # PBKDF2-Runden; bremst das Durchprobieren von Passwörtern


def init():
    with db.offen() as con:
        con.executescript(SCHEMA)


def _hash(passwort, salz):
    return hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"),
                               salz.encode("ascii"), RUNDEN).hex()


def jetzt():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ Konten
def anzahl():
    return db.wert("SELECT COUNT(*) FROM benutzer WHERE aktiv=1")


def persoenlicher_betrieb():
    """Erst wenn ein Konto existiert, wird persönlich angemeldet."""
    return anzahl() > 0


def liste(nur_aktive=False):
    sql = ("SELECT b.*, m.name AS mitarbeiter FROM benutzer b"
           " LEFT JOIN mitarbeiter m ON m.id=b.mitarbeiter_id")
    if nur_aktive:
        sql += " WHERE b.aktiv=1"
    return db.hole(sql + " ORDER BY b.aktiv DESC, b.name")


def finden(anmeldename):
    return db.eine("SELECT * FROM benutzer WHERE LOWER(anmeldename)=LOWER(?)",
                   ((anmeldename or "").strip(),))


def anlegen(anmeldename, name, passwort, rolle="mitarbeiter", mitarbeiter_id=None):
    anmeldename = (anmeldename or "").strip().lower()
    name = (name or "").strip()
    if not anmeldename or not name:
        raise ValueError("Anmeldename und Name sind Pflicht.")
    if len(passwort or "") < 8:
        raise ValueError("Das Passwort braucht mindestens 8 Zeichen.")
    if rolle not in ROLLEN_TEXT:
        raise ValueError("Unbekannte Rolle.")
    if finden(anmeldename):
        raise ValueError(f"Der Anmeldename {anmeldename} ist schon vergeben.")
    salz = secrets.token_hex(16)
    with db.offen() as con:
        con.execute(
            "INSERT INTO benutzer (anmeldename, name, rolle, salz, hash, mitarbeiter_id,"
            " aktiv, angelegt, standort) VALUES (?,?,?,?,?,?,1,?,?)",
            (anmeldename, name, rolle, salz, _hash(passwort, salz),
             mitarbeiter_id or None, jetzt(), db.STANDORT_STANDARD))
    return anmeldename


def passwort_setzen(benutzer_id, passwort):
    if len(passwort or "") < 8:
        raise ValueError("Das Passwort braucht mindestens 8 Zeichen.")
    salz = secrets.token_hex(16)
    with db.offen() as con:
        con.execute("UPDATE benutzer SET salz=?, hash=? WHERE id=?",
                    (salz, _hash(passwort, salz), benutzer_id))


def aktiv_setzen(benutzer_id, aktiv):
    """Konten werden gesperrt, nicht gelöscht – sonst verlieren alte Protokollzeilen
    ihren Bezug, und genau die braucht man bei einer Prüfung."""
    with db.offen() as con:
        con.execute("UPDATE benutzer SET aktiv=? WHERE id=?", (1 if aktiv else 0, benutzer_id))


def rolle_setzen(benutzer_id, rolle):
    if rolle not in ROLLEN_TEXT:
        raise ValueError("Unbekannte Rolle.")
    with db.offen() as con:
        con.execute("UPDATE benutzer SET rolle=? WHERE id=?", (rolle, benutzer_id))


def pruefen(anmeldename, passwort):
    """Gibt den Benutzersatz zurück oder None. Vergleich in fester Zeit."""
    b = finden(anmeldename)
    if not b or not b["aktiv"]:
        # Trotzdem rechnen, damit ein unbekannter Name nicht schneller antwortet
        _hash(passwort or "", "00")
        return None
    if hmac.compare_digest(_hash(passwort or "", b["salz"]), b["hash"]):
        with db.offen() as con:
            con.execute("UPDATE benutzer SET letzte_anmeldung=? WHERE id=?", (jetzt(), b["id"]))
        return b
    return None


def darf_aendern(rolle):
    return rolle in ("leitung", "mitarbeiter")


def darf_verwalten(rolle):
    return rolle == "leitung"


# ------------------------------------------------------------- Protokoll
def notieren(benutzer, rolle, aktion, bereich=None, objekt_id=None,
             beschreibung=None, vorher=None, nachher=None):
    """Eine Änderung festhalten. Wird nie wieder verändert.

    `vorher`/`nachher` nehmen beliebige Werte und werden als JSON abgelegt – so lässt
    sich später auch nachvollziehen, was genau anders war."""
    def text(x):
        if x is None or isinstance(x, str):
            return x
        try:
            return json.dumps(x, ensure_ascii=False, default=str)[:4000]
        except Exception:
            return str(x)[:4000]

    with db.offen() as con:
        con.execute(
            "INSERT INTO protokoll (zeitpunkt, benutzer, rolle, aktion, bereich, objekt_id,"
            " beschreibung, vorher, nachher) VALUES (?,?,?,?,?,?,?,?,?)",
            (jetzt(), benutzer, rolle, aktion, bereich,
             str(objekt_id) if objekt_id is not None else None,
             beschreibung, text(vorher), text(nachher)))


def protokoll(limit=200, benutzer=None, bereich=None, seit=None, suche=None):
    sql = "SELECT * FROM protokoll WHERE 1=1"
    args = []
    if benutzer:
        sql += " AND benutzer=?"; args.append(benutzer)
    if bereich:
        sql += " AND bereich=?"; args.append(bereich)
    if seit:
        sql += " AND zeitpunkt >= ?"; args.append(seit)
    if suche:
        sql += " AND (beschreibung LIKE ? OR aktion LIKE ? OR objekt_id LIKE ?)"
        args += [f"%{suche}%"] * 3
    sql += " ORDER BY zeitpunkt DESC, id DESC LIMIT ?"
    args.append(limit)
    return db.hole(sql, args)


def bereiche():
    return [z["bereich"] for z in db.hole(
        "SELECT DISTINCT bereich FROM protokoll WHERE bereich IS NOT NULL ORDER BY bereich")]


def wer_war_aktiv(tage=30):
    """Für die Leitung: wer arbeitet tatsächlich im OS – gezählt, nicht behauptet."""
    grenze = (datetime.datetime.now() - datetime.timedelta(days=tage)).isoformat()
    return db.hole(
        "SELECT benutzer, COUNT(*) AS aenderungen, MAX(zeitpunkt) AS zuletzt"
        "  FROM protokoll WHERE zeitpunkt >= ? AND benutzer IS NOT NULL"
        " GROUP BY benutzer ORDER BY aenderungen DESC", (grenze,))


def erstes_konto_noetig():
    """Hinweis fürs Dashboard, solange noch mit gemeinsamem Passwort gearbeitet wird."""
    return not persoenlicher_betrieb() and bool(os.environ.get("IMPROFY_OS_PASSWORT"))
