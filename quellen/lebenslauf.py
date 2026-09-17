# -*- coding: utf-8 -*-
"""Lebensläufe der Kunden aus der Drive-Ablage – auf Abruf im OS.

Die Taskforce braucht beim Anschreiben eines Arbeitgebers sofort den Lebenslauf.
Der liegt im Drive-Kundenordner (meist in einem Unterordner „Bewerbung") und in
der Chat-Gruppe „CVs Köln". Die Chat-Gruppe hat keine Schnittstelle; Drive schon:
eine Suche nach „Lebenslauf/CV" über den Drive-Connector liefert Datei-ID, Name,
Elternordner und Link. Das Ergebnis liegt als `exporte/drive_lebenslaeufe_*.csv`.

Zuordnung zum Kunden, in dieser Reihenfolge, nur wenn eindeutig:
  1. Elternordner ist der Kundenordner (kunde.drive_id)
  2. Elternordner ist ein bekannter Kundenordner aus dem Register (ID-K####_Vor_Nach)
  3. Elternordner ist ein **Unterordner** eines Kundenordners (Dokumente, Endergebnis,
     Modul_2/Dokumente ...) – die Auflösung steht in `exporte/drive_unterordner_*.csv`
  4. Vor-/Nachname des Kunden steht im Dateinamen

Warum Schritt 3 noetig ist: die wenigsten Lebenslaeufe liegen direkt im Kundenordner.
Sie stecken in Unterordnern, und viele heissen schlicht "Lebenslauf.pdf" – ohne den
Ordner ist bei denen nicht zu erkennen, zu wem sie gehoeren. Die Ordnerkette laesst
sich nur ueber den Drive-Zugang aufloesen, den das OS selbst nicht hat; deshalb liegt
das Ergebnis als Datei daneben und wird hier mitgelesen.

Was sich nicht eindeutig zuordnen lässt, bleibt ohne Kunde – die Taskforce kann
jeden Lebenslauf von Hand verknüpfen (Link eintragen). Lieber eine Lücke als ein
fremder Lebenslauf in der falschen Akte.
"""
import csv
import datetime
import re

import datenbank
from quellen.basis import Quelle, neueste_datei

SCHEMA = """
CREATE TABLE IF NOT EXISTS lebenslauf (
    id               INTEGER PRIMARY KEY,
    kunde_id         INTEGER REFERENCES kunde(id),
    datei_id         TEXT UNIQUE,
    name             TEXT,
    url              TEXT,
    mime             TEXT,
    geaendert        TEXT,
    ordner_id        TEXT,
    quelle           TEXT DEFAULT 'drive',      -- drive | hand
    zugeordnet_ueber TEXT,                      -- ordner | register | name | hand
    eingelesen       TEXT
);
CREATE TABLE IF NOT EXISTS kunde_profil (
    kunde_id   INTEGER PRIMARY KEY REFERENCES kunde(id),
    kurzprofil TEXT,     -- Stichworte für den Abgleich: Qualifikationen, Führerschein, Sprachen
    cv_text    TEXT,     -- eingefügter Lebenslauftext (aus PDF/Chat kopiert)
    geaendert  TEXT
);
"""

RAUSCHEN = {"lebenslauf", "herr", "frau", "alt", "alte", "altes", "neu", "neue", "pdf", "docx",
            "cv", "the", "von", "kopie", "word", "englisch", "deutsch", "version", "falsch",
            "korrigiert", "abstimmung", "vollständig", "mit", "und", "zeugnis", "bildungshistorie"}


def _tokens(s):
    return [t.casefold() for t in re.split(r"[\s_\-(),.]+", s or "")
            if len(t) > 2 and t.casefold() not in RAUSCHEN and not t.upper().startswith("ID-")]


def _kunde_zu_name(tokens, kunden):
    """Eindeutiger Kunde, dessen Name zu den Tokens des Dateinamens passt.

    Verglichen werden ganze Wörter (sonst landet „Rahimi" bei „Ebrahimi"). Es zählt,
    wenn der Nachname des Kunden im Dateinamen steht, oder wenn alle Tokens im Namen
    vorkommen. „Abdul Nasir Dabas" trifft damit nicht „Abdul Nasir Amiri"."""
    if not tokens:
        return None
    treffer = []
    for k in kunden:
        woerter = [w for w in re.split(r"[\s(),\-]+", k["name"].casefold()) if w]
        getroffen = [t for t in tokens if t in woerter]
        if not getroffen:
            continue
        nachname = woerter[-1]
        # Der Nachname muss dabei sein, oder mindestens zwei Namensteile müssen passen.
        # Ein einzelner Mittelname reicht nicht: „Herr Hussein_Lebenslauf.pdf" gehört
        # Mohammed Hussein und landete sonst bei Fada *Hussein* Mirzai. Genau dieser
        # Fehler steckt schon in drei der fünf Lebensläufe, die die CV-App erzeugt hat.
        # Der Nachname des Kunden MUSS im Dateinamen stehen. Alles andere führt in die Irre:
        #   „Herr Hussein_Lebenslauf.pdf"        gehört Mohammed Hussein, nicht Fada *Hussein* Mirzai
        #   „Lebenslauf_Abdul Nasir Dabas.pdf"   gehört Herrn Dabas, nicht Abdul Nasir *Amiri*
        #   „Herr Amin Ali_Lebenslauf.pdf"       gehört Amin Ali, nicht Bayan Yazdeen *Ali*
        # Deshalb zusätzlich: entweder trägt der Dateiname nur diesen einen Namen,
        # oder es passen mindestens zwei Namensteile.
        if nachname in tokens and (len(getroffen) >= 2 or len(tokens) == 1):
            treffer.append(k["id"])
    return treffer[0] if len(set(treffer)) == 1 else None


class DriveLebenslaeufe(Quelle):
    name = "Drive-Lebensläufe"
    braucht = "drive_lebenslaeufe_*.csv in exporte/"

    def datei_bereit(self):
        return neueste_datei("drive_lebenslaeufe*.csv") is not None

    def einlesen(self):
        pfad = neueste_datei("drive_lebenslaeufe*.csv")
        register = {}
        reg = neueste_datei("drive_ordner*.csv")
        if reg:
            with open(reg, encoding="utf-8-sig", newline="") as f:
                for z in csv.DictReader(f):
                    if z.get("drive_id") and z.get("ordnername"):
                        register[z["drive_id"].strip()] = z["ordnername"].strip()
        # Unterordner wie "Dokumente" oder "Modul_2/Endergebnis" auf ihren Kundenordner
        # zurueckfuehren. Dieselbe Behandlung wie ein Kundenordner, nur eine Ebene tiefer.
        unter = neueste_datei("drive_unterordner*.csv")
        if unter:
            with open(unter, encoding="utf-8-sig", newline="") as f:
                for z in csv.DictReader(f):
                    if z.get("unterordner_id") and z.get("kundenordner"):
                        register.setdefault(z["unterordner_id"].strip(), z["kundenordner"].strip())
        jetzt = datetime.datetime.now().isoformat(timespec="seconds")
        with datenbank.offen() as con:
            con.executescript(SCHEMA)
            kunden = [dict(r) for r in con.execute(
                "SELECT id, name, drive_id FROM kunde WHERE standort=?", (self.standort,))]
            je_ordner = {k["drive_id"]: k["id"] for k in kunden if k["drive_id"]}
            anzahl = 0
            with open(pfad, encoding="utf-8-sig", newline="") as f:
                for z in csv.DictReader(f):
                    did = (z.get("datei_id") or "").strip()
                    if not did:
                        continue
                    ordner = (z.get("parent_id") or "").strip()
                    kid, wie = None, None
                    if ordner in je_ordner:
                        kid, wie = je_ordner[ordner], "ordner"
                    elif ordner in register:
                        kid, wie = _kunde_zu_name(_tokens(register[ordner]), kunden), "register"
                    if not kid:
                        kid, wie = _kunde_zu_name(_tokens(z.get("name")), kunden), "name"
                    if not kid:
                        wie = None
                    con.execute(
                        "INSERT INTO lebenslauf (kunde_id, datei_id, name, url, mime, geaendert,"
                        " ordner_id, quelle, zugeordnet_ueber, eingelesen) VALUES (?,?,?,?,?,?,?,?,?,?)"
                        " ON CONFLICT(datei_id) DO UPDATE SET name=excluded.name, url=excluded.url,"
                        " mime=excluded.mime, geaendert=excluded.geaendert, ordner_id=excluded.ordner_id,"
                        " eingelesen=excluded.eingelesen,"
                        " kunde_id=CASE WHEN lebenslauf.zugeordnet_ueber='hand' THEN lebenslauf.kunde_id"
                        "               ELSE excluded.kunde_id END,"
                        " zugeordnet_ueber=CASE WHEN lebenslauf.zugeordnet_ueber='hand' THEN 'hand'"
                        "               ELSE excluded.zugeordnet_ueber END",
                        (kid, did, z.get("name"), z.get("url"), z.get("mime"), z.get("geaendert"),
                         ordner or None, "drive", wie, jetzt))
                    if kid:
                        anzahl += 1
                        # QM: „Lebenslauf vorhanden" gilt damit auch ohne lokalen Drive-Ordner.
                        con.execute(
                            "INSERT INTO dokument (kunde_id, schluessel, vorhanden, dateiname,"
                            " geaendert, geprueft) VALUES (?, 'lebenslauf', 1, ?, ?, ?)"
                            " ON CONFLICT(kunde_id, schluessel) DO UPDATE SET vorhanden=1,"
                            " dateiname=excluded.dateiname, geaendert=excluded.geaendert,"
                            " geprueft=excluded.geprueft",
                            (kid, z.get("name"), z.get("geaendert"), jetzt))
        return anzahl


def von_kunde(kunde_id):
    return datenbank.hole(
        "SELECT * FROM lebenslauf WHERE kunde_id=? ORDER BY geaendert DESC, id DESC", (kunde_id,))


def hand_eintragen(kunde_id, url, name=None):
    url = (url or "").strip()
    if not url.startswith("http"):
        raise ValueError("Bitte einen vollständigen Link (https://…) eintragen.")
    m = re.search(r"/d/([\w-]{20,})", url)
    datei_id = m.group(1) if m else "hand:" + url[-60:]
    with datenbank.offen() as con:
        con.execute(
            "INSERT INTO lebenslauf (kunde_id, datei_id, name, url, quelle, zugeordnet_ueber, eingelesen)"
            " VALUES (?,?,?,?,'hand','hand',?) ON CONFLICT(datei_id) DO UPDATE SET kunde_id=excluded.kunde_id,"
            " name=COALESCE(NULLIF(excluded.name,''), lebenslauf.name), zugeordnet_ueber='hand'",
            (kunde_id, datei_id, (name or "").strip() or "Lebenslauf (Link)", url,
             datetime.datetime.now().isoformat(timespec="seconds")))


def loesen(lid):
    with datenbank.offen() as con:
        con.execute("UPDATE lebenslauf SET kunde_id=NULL, zugeordnet_ueber=NULL WHERE id=?", (lid,))


def profil_von(kunde_id):
    return datenbank.eine("SELECT * FROM kunde_profil WHERE kunde_id=?", (kunde_id,)) or {}


def profil_speichern(kunde_id, kurzprofil, cv_text):
    with datenbank.offen() as con:
        con.execute(
            "INSERT INTO kunde_profil (kunde_id, kurzprofil, cv_text, geaendert) VALUES (?,?,?,?)"
            " ON CONFLICT(kunde_id) DO UPDATE SET kurzprofil=excluded.kurzprofil,"
            " cv_text=excluded.cv_text, geaendert=excluded.geaendert",
            (kunde_id, (kurzprofil or "").strip() or None, (cv_text or "").strip() or None,
             datetime.datetime.now().isoformat(timespec="seconds")))


def ohne_lebenslauf(standort=datenbank.STANDORT_STANDARD):
    """Kunden in Maßnahme ohne hinterlegten Lebenslauf – die Taskforce kann sie nicht bewerben."""
    return datenbank.hole(
        "SELECT k.id, k.name, k.status_code FROM kunde k WHERE k.standort=? AND k.status_code IN ('H','I','J')"
        "   AND NOT EXISTS (SELECT 1 FROM lebenslauf l WHERE l.kunde_id=k.id) ORDER BY k.name", (standort,))
