# -*- coding: utf-8 -*-
"""Bewerbungsfotos: einmal ablegen, immer wiederverwenden.

**Warum das ein eigenes Modul ist.** Bisher war das Foto flüchtig: Wer ein PDF baute,
lud es hoch, und beim nächsten Bauen war es wieder weg. Das Foto ist aber das Erste, was
ein Arbeitgeber ansieht, und es ändert sich nie – es gehört an den Kunden, nicht an einen
einzelnen Knopfdruck.

**Woher die Fotos kommen.** Heute schickt sie jemand in die Google-Chat-Gruppe „CVs Köln".
Drei Wege sind hier vorgesehen, in dieser Reihenfolge des Aufwands:

1. **Hochladen** im Lebenslauf-Formular – funktioniert sofort, ein Klick.
2. **Aus einem Ordner einlesen**: Wer die Bilder aus dem Chat in einen Ordner zieht,
   bekommt sie über den Dateinamen den Kunden zugeordnet. Derselbe strenge Namensabgleich
   wie bei den Lebensläufen: Nachname muss vorkommen, und entweder ist der Dateiname
   eindeutig oder zwei Namensteile passen. Lieber nicht zuordnen als falsch zuordnen –
   ein fremdes Gesicht im Lebenslauf ist der peinlichste denkbare Fehler.
3. **Direkt aus Google Chat** – braucht die Chat-Schnittstelle mit einem Dienstkonto
   (`GOOGLE_CHAT_TOKEN`). Das muss ein Google-Administrator freischalten; ohne diese
   Freigabe lässt sich ein Chatraum nicht auslesen. Der Weg ist vorbereitet, aber bis
   dahin führt Weg 1 oder 2 zum Ziel.

**Gespeichert wird verkleinert.** Ein Handyfoto hat gern 5 MB; gedruckt wird es knapp
6 cm breit. 1200 Pixel lange Kante reichen dafür satt und halten die Datenbank klein.
"""
import datetime
import io
import os
import re

import datenbank as db

SCHEMA = """
CREATE TABLE IF NOT EXISTS kunde_foto (
    kunde_id  INTEGER PRIMARY KEY,
    daten     BLOB NOT NULL,
    mime      TEXT,
    breite    INTEGER,
    hoehe     INTEGER,
    bytes     INTEGER,
    quelle    TEXT,
    dateiname TEXT,
    geaendert TEXT
);
"""

# Der Eingang: Bilder, die noch keinem Kunden gehoeren. Bewusst eine eigene Tabelle und
# keine Ablage im Dateisystem - was in der Datenbank liegt, ist von der Sicherung mit
# erfasst und verschwindet nicht beim Aufraeumen des Downloads-Ordners.
SCHEMA_EINGANG = """
CREATE TABLE IF NOT EXISTS foto_eingang (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    daten     BLOB NOT NULL,
    mime      TEXT,
    breite    INTEGER,
    hoehe     INTEGER,
    bytes     INTEGER,
    dateiname TEXT,
    quelle    TEXT,
    erstellt  TEXT
);
"""
BILDENDUNGEN = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".tif", ".tiff")
KANTE = 1200            # lange Kante nach dem Verkleinern


def init():
    with db.offen() as con:
        con.executescript(SCHEMA)
        con.executescript(SCHEMA_EINGANG)


def jetzt():
    return datetime.datetime.now().isoformat(timespec="seconds")


def verkleinern(rohdaten):
    """Auf Druckgröße bringen. Gibt (bytes, mime, breite, hoehe) zurück."""
    try:
        from PIL import Image, ImageOps
        bild = Image.open(io.BytesIO(rohdaten))
        bild.load()
        bild = ImageOps.exif_transpose(bild)      # Drehung aus den EXIF-Daten übernehmen
        bild.thumbnail((KANTE, KANTE))
        if bild.mode not in ("RGB", "L"):
            bild = bild.convert("RGB")
        puffer = io.BytesIO()
        bild.save(puffer, format="JPEG", quality=88, optimize=True)
        return puffer.getvalue(), "image/jpeg", bild.width, bild.height
    except Exception:
        # Ohne Pillow lieber das Original behalten als gar kein Foto.
        return rohdaten, "image/jpeg", None, None


def speichern(kunde_id, rohdaten, dateiname=None, quelle="hochgeladen"):
    if not rohdaten:
        raise ValueError("Keine Bilddaten.")
    daten, mime, breite, hoehe = verkleinern(rohdaten)
    with db.offen() as con:
        con.execute(
            "INSERT INTO kunde_foto (kunde_id, daten, mime, breite, hoehe, bytes, quelle,"
            " dateiname, geaendert) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(kunde_id) DO UPDATE SET daten=excluded.daten, mime=excluded.mime,"
            " breite=excluded.breite, hoehe=excluded.hoehe, bytes=excluded.bytes,"
            " quelle=excluded.quelle, dateiname=excluded.dateiname, geaendert=excluded.geaendert",
            (kunde_id, daten, mime, breite, hoehe, len(daten), quelle, dateiname, jetzt()))
    return {"bytes": len(daten), "breite": breite, "hoehe": hoehe, "mime": mime}


def foto(kunde_id):
    """Der gespeicherte Satz oder None."""
    return db.eine("SELECT * FROM kunde_foto WHERE kunde_id=?", (kunde_id,))


def rohdaten(kunde_id):
    z = db.eine("SELECT daten, mime FROM kunde_foto WHERE kunde_id=?", (kunde_id,))
    return (z["daten"], z["mime"]) if z else (None, None)


def loeschen(kunde_id):
    with db.offen() as con:
        con.execute("DELETE FROM kunde_foto WHERE kunde_id=?", (kunde_id,))


# ------------------------------------------------------------------- Der Eingang
def eingang_ablegen(rohdaten, dateiname=None, quelle="hochgeladen"):
    """Ein Bild in den Eingang legen - verkleinert, wie alles andere auch."""
    daten, mime, breite, hoehe = verkleinern(rohdaten)
    with db.offen() as con:
        con.execute(
            "INSERT INTO foto_eingang (daten, mime, breite, hoehe, bytes, dateiname,"
            " quelle, erstellt) VALUES (?,?,?,?,?,?,?,?)",
            (daten, mime, breite, hoehe, len(daten), dateiname, quelle, jetzt()))


def eingang(grenze=200):
    """Was im Eingang liegt - ohne die Bilddaten, die holt die Seite einzeln."""
    return db.hole(
        "SELECT id, mime, breite, hoehe, bytes, dateiname, quelle, erstellt"
        "  FROM foto_eingang ORDER BY id LIMIT ?", (grenze,))


def eingang_bild(eingang_id):
    z = db.eine("SELECT daten, mime FROM foto_eingang WHERE id=?", (eingang_id,))
    return (z["daten"], z["mime"]) if z else (None, None)


def eingang_zuordnen(eingang_id, kunde_id):
    """Ein Bild aus dem Eingang einem Kunden geben. Danach ist es aus dem Eingang weg."""
    z = db.eine("SELECT daten, mime, breite, hoehe, dateiname FROM foto_eingang WHERE id=?",
                (eingang_id,))
    if not z:
        raise ValueError("Dieses Bild liegt nicht mehr im Eingang.")
    with db.offen() as con:
        con.execute(
            "INSERT INTO kunde_foto (kunde_id, daten, mime, breite, hoehe, bytes, quelle,"
            " dateiname, geaendert) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(kunde_id) DO UPDATE SET daten=excluded.daten, mime=excluded.mime,"
            " breite=excluded.breite, hoehe=excluded.hoehe, bytes=excluded.bytes,"
            " quelle=excluded.quelle, dateiname=excluded.dateiname, geaendert=excluded.geaendert",
            (kunde_id, z["daten"], z["mime"], z["breite"], z["hoehe"], len(z["daten"]),
             "eingang", z["dateiname"], jetzt()))
        con.execute("DELETE FROM foto_eingang WHERE id=?", (eingang_id,))


def eingang_verwerfen(eingang_id):
    with db.offen() as con:
        con.execute("DELETE FROM foto_eingang WHERE id=?", (eingang_id,))


def aufnehmen(dateien, standort=db.STANDORT_STANDARD):
    """Hochgeladene Bilder annehmen: eindeutige sofort zuordnen, den Rest in den Eingang.

    `dateien` ist eine Folge von (dateiname, rohdaten). Gibt zurueck, was wohin ging."""
    kunden = db.hole("SELECT id, name FROM kunde WHERE standort=?", (standort,))
    zugeordnet, offen, abgelehnt = [], 0, []
    for name, roh in dateien:
        if not roh:
            continue
        if os.path.splitext(name or "")[1].lower() not in BILDENDUNGEN:
            abgelehnt.append(f"{name}: keine Bilddatei")
            continue
        try:
            kunde_id = zuordnen(name, kunden)
            if kunde_id:
                speichern(kunde_id, roh, name, quelle="hochgeladen")
                zugeordnet.append((kunde_id, name))
            else:
                eingang_ablegen(roh, name)
                offen += 1
        except Exception as e:
            abgelehnt.append(f"{name}: {e}")
    return zugeordnet, offen, abgelehnt


def stand():
    """Wie viele laufende Kunden ein Foto haben – im CRM ist es ein Pflichtpunkt."""
    return {
        "mit": db.wert("SELECT COUNT(*) FROM kunde_foto"),
        "eingang": db.wert("SELECT COUNT(*) FROM foto_eingang"),
        "ohne_laufend": db.wert(
            "SELECT COUNT(*) FROM kunde k WHERE k.standort=? AND k.status_code IN ('H','I')"
            "  AND NOT EXISTS (SELECT 1 FROM kunde_foto f WHERE f.kunde_id=k.id)",
            (db.STANDORT_STANDARD,)),
    }


# ------------------------------------------------------- Aus einem Ordner einlesen
def _teile(text):
    # Der Unterstrich zählt in \w als Wortzeichen – „Farnam_Foroutan" bliebe sonst ein
    # einziges Wort und fände keinen Namen. Ziffern fliegen mit raus (IMG_2931).
    return [t for t in re.split(r"[^a-zA-ZäöüßÄÖÜ]+", (text or "").casefold()) if len(t) > 2]


def zuordnen(dateiname, kunden):
    """Welcher Kunde steckt in diesem Dateinamen? None, wenn nicht sicher.

    Dieselbe strenge Regel wie bei den Lebensläufen: Der Nachname muss vorkommen, und
    entweder besteht der Dateiname nur aus diesem einen Namen oder es passen zwei
    Namensteile. Ein fremdes Gesicht im Lebenslauf wäre der peinlichste Fehler."""
    stamm = os.path.splitext(os.path.basename(dateiname))[0]
    tokens = set(_teile(stamm))
    if not tokens:
        return None
    treffer = []
    for k in kunden:
        teile = _teile(k["name"])
        if not teile:
            continue
        nachname = teile[-1]
        getroffen = [t for t in teile if t in tokens]
        if nachname in tokens and (len(getroffen) >= 2 or len(tokens) == 1):
            treffer.append(k["id"])
    return treffer[0] if len(treffer) == 1 else None


def aus_ordner(pfad, standort=db.STANDORT_STANDARD):
    """Alle Bilder eines Ordners den Kunden zuordnen. Gibt (zugeordnet, offen) zurück."""
    if not os.path.isdir(pfad):
        raise ValueError(f"Den Ordner {pfad} gibt es nicht.")
    kunden = db.hole("SELECT id, name FROM kunde WHERE standort=?", (standort,))
    zugeordnet, offen = [], []
    for name in sorted(os.listdir(pfad)):
        if os.path.splitext(name)[1].lower() not in BILDENDUNGEN:
            continue
        voll = os.path.join(pfad, name)
        kunde_id = zuordnen(name, kunden)
        if not kunde_id:
            offen.append(name)
            continue
        try:
            with open(voll, "rb") as f:
                speichern(kunde_id, f.read(), name, quelle="ordner")
            zugeordnet.append((kunde_id, name))
        except Exception as e:
            offen.append(f"{name} ({e})")
    return zugeordnet, offen


def chat_bereit():
    """Der Weg über die Google-Chat-Schnittstelle. Braucht die Freigabe eines Admins."""
    return bool(os.environ.get("GOOGLE_CHAT_TOKEN"))
