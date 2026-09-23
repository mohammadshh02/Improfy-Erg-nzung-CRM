# -*- coding: utf-8 -*-
"""Lebenslauf bauen – die Improfy-Excel-Vorlage aus den Daten des OS befüllen.

Die Füll-Logik stammt aus der eigenständigen CV-App (github.com/mohammadshh02/Improfy-CV)
und liegt unverändert in `cv/`. Dieses Modul ist die Brücke: es holt, was das OS über
den Kunden weiß, und übergibt es an `fill_cv.fuelle_blatt`.

**Warum das im OS besser aufgehoben ist als in der CV-App allein:**

Die CV-App lässt die Kunden-ID von Hand eintippen. Drei der fünf bisher erzeugten
Lebensläufe tragen deshalb eine ID, die im Drive-Register einem *anderen* Menschen
gehört (siehe `quellen/drive_ordner.py`). Im OS wird der Kunde aus dem Register
gewählt, die ID kommt automatisch dazu – der Fehler kann nicht mehr passieren.

Dazu kommt die Vorbelegung: Name, Telefon, E-Mail, Sprache, Geburtsdatum, der
betreuende Coach und das Kurzprofil aus der Taskforce stehen bereits im OS. Wer
einen Lebenslauf anlegt, tippt nur noch das ab, was das OS wirklich nicht hat:
Werdegang und Bildung.

Das Ergebnis wird am Kunden vermerkt (Tabelle `lebenslauf`), damit die Taskforce
es sofort sieht und das Qualitätsmanagement es als vorhanden zählt.
"""
import datetime
import io
import os
import re
import sys

import openpyxl

import datenbank as db

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "cv"))
import fill_cv                                    # noqa: E402  (liegt in cv/)

VORLAGE = os.path.join(HIER, "cv", "vorlagen", "Muster-Vorlage.xlsx")
# Wohin gebaute Unterlagen gehen. Ableitbar aus dem eigenen Verzeichnis – aber dann
# schreibt jeder Testlauf ins Live-Repo. Der Dateiname trägt Kundennummer und Datum,
# also überschreibt ein Test ein am selben Tag echt gebautes Dokument desselben
# Menschen. Dieselbe Fehlerklasse wie bei `sicherungen/`, darum dieselbe Lösung:
# `OS_AUSGABE_ORDNER` setzen die Selbsttests auf einen Papierkorb.
AUSGABE = os.path.join(os.environ.get("OS_AUSGABE_ORDNER")
                       or os.path.join(HIER, "ausgabe"), "lebenslaeufe")
DATUM = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b")

# Sprachniveaus in der Schreibweise, die die Vorlage erwartet
NIVEAUS = ("Muttersprache", "fließend", "gute Kenntnisse", "Grundkenntnisse")


def _teile_namen(voll):
    roh = re.sub(r"\(.*?\)", " ", voll or "").strip()
    teile = [t for t in roh.split() if t]
    if len(teile) < 2:
        return ("", teile[0] if teile else "")
    return (teile[0], " ".join(teile[1:]))


def _interne_id(kunde):
    """ID-K####. Erst aus der Gutscheinliste, sonst aus dem Drive-Ordnernamen."""
    wert = db.wert(
        "SELECT improfy_id FROM gutschein_zeile WHERE kunde_id=? AND improfy_id IS NOT NULL"
        "  AND improfy_id<>'' ORDER BY id LIMIT 1", (kunde["id"],))
    if wert:
        return wert.strip()
    if kunde.get("drive_id"):
        import csv
        import glob
        dateien = sorted(glob.glob(os.path.join(HIER, "exporte", "drive_ordner*.csv")), reverse=True)
        if dateien:
            with open(dateien[0], encoding="utf-8-sig", newline="") as f:
                for z in csv.DictReader(f):
                    if (z.get("drive_id") or "").strip() == kunde["drive_id"]:
                        m = re.match(r"(ID-K\d+)", (z.get("ordnername") or "").strip())
                        if m:
                            return m.group(1)
    return ""


def _geburtsdatum(kunde_id):
    """Das OS führt kein Geburtsdatum. In der Gutscheinliste sind bei einem Teil der
    Zeilen die Spalten verrutscht; ein Datum im Feld Maßnahme oder Sprache ist in
    Wahrheit das Geburtsdatum. Wird übernommen, aber als unsicher gemeldet."""
    for z in db.hole("SELECT massnahme, sprache FROM gutschein_zeile WHERE kunde_id=? ORDER BY id",
                     (kunde_id,)):
        for feld in ("massnahme", "sprache"):
            m = DATUM.fullmatch((z[feld] or "").strip())
            if m:
                return m.group(1)
    return ""


def _massnahme_zeitraum(kunde_id):
    """„18.05.2026 - 23.08.2026" – der Zeitraum der Maßnahme aus der Gutscheinliste.

    Der Designer schreibt diesen Zeitraum in den Improfy-Block; ein blankes „aktuell"
    steht in keinem seiner Lebensläufe. Genommen wird die jüngste Zeile mit beiden Daten."""
    z = db.eine("SELECT von, bis FROM gutschein_zeile WHERE kunde_id=? AND von<>'' AND bis<>''"
                " ORDER BY von DESC LIMIT 1", (kunde_id,))
    if not z:
        return ""
    def deutsch(iso):
        try:
            return datetime.date.fromisoformat(iso).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return iso
    return f"{deutsch(z['von'])} - {deutsch(z['bis'])}"


def _sprachen(kunde):
    """Muttersprache aus dem Kundensatz, Deutsch steht laut Vorlage immer zuerst.

    Im Sprachfeld der Kundenliste stehen oft mehrere Sprachen in einer Zelle
    („Deutsch/Paschtu", „Arabisch, Kurdisch"). Ungetrennt landete das als eine einzige
    Sprache „DEUTSCH/PASCHTU" im Lebenslauf. Deutsch wird dabei nicht doppelt geführt."""
    liste = [{"sprache": "DEUTSCH", "niveau": ""}]
    roh = re.split(r"[/,;+&]| und ", kunde.get("sprache") or "")
    for teil in roh:
        name = teil.strip(" .-")
        if not name or name.casefold() == "deutsch":
            continue
        if any(e["sprache"] == name.upper() for e in liste):
            continue
        liste.append({"sprache": name.upper(), "niveau": "Muttersprache"})
    return liste


def vorbelegung(kunde_id):
    """Alles, was das OS über den Kunden weiß, in der Form, die die Vorlage erwartet."""
    kunde = db.eine(
        "SELECT k.*, m.name AS coach FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE k.id=?", (kunde_id,))
    if not kunde:
        return None
    vor, nach = _teile_namen(kunde["name"])
    profil = db.eine("SELECT kurzprofil, cv_text FROM kunde_profil WHERE kunde_id=?", (kunde_id,)) or {}
    geburt = _geburtsdatum(kunde_id)
    intern = _interne_id(kunde)
    adresse = " ".join(x for x in ((kunde.get("plz") or ""), (kunde.get("stadt") or "")) if x).strip()
    return {
        "kunde_id": kunde_id,
        "kunde_name": kunde["name"],
        "interne_id": intern,
        "blattname": blattname(intern, vor, nach),
        "geburtsdatum_unsicher": bool(geburt),
        "kurzprofil": profil.get("kurzprofil") or "",
        "cv_text": profil.get("cv_text") or "",
        "daten": {
            "kunde_von": kunde.get("coach") or fill_cv.DEFAULT_KUNDE_VON,
            "vorname": vor, "nachname": nach,
            "geschlecht": "",
            "angestrebter_job": "",
            "geburtsdatum": geburt,
            "massnahme_zeitraum": _massnahme_zeitraum(kunde_id),
            "mobil": kunde.get("telefon") or "",
            "email": kunde.get("email") or "",
            "adresse": adresse,
            "fuehrerschein": {"vorhanden": False, "klasse": "", "eu": False},
            "berufserfahrung": [], "bildung": [], "zusatzqualifikationen": [],
            "sprachen": _sprachen(kunde), "edv_kenntnisse": [], "soft_skills": [],
            "ueber_mich": "", "hobbys": "",
        },
    }


def blattname(interne_id, vorname, nachname):
    """'ID-K0000_Nabil_Al_Musterbewerber' – so heißen die Blätter in der Master-Mappe.

    In der Kundenliste stehen teils Schreibvarianten im Namen ("Jan = Jan Wendisch").
    Beide Beispiele sind erfunden; hier standen ein echter Kundenname samt Kundennummer
    und ein echter Doppeleintrag, und die Datei ist versioniert.
    Alles hinter dem Gleichheitszeichen und Sonderzeichen fliegen raus, sonst tragen
    Dateiname und Blattname Zeichen, an denen Excel und Windows sich stoßen."""
    roh = f"{vorname} {nachname}".split("=")[0]
    roh = re.sub(r"[^\w\s-]", " ", roh, flags=re.UNICODE)
    name = re.sub(r"\s+", "_", roh.strip())
    return "_".join(x for x in (interne_id, name) if x) or "Kandidat"


def _blattname_kurz(name):
    return re.sub(r"[\[\]:*?/\\]", "", name or "")[:31] or "Kandidat"


def bauen(kunde_id, daten, bearbeiter=None):
    """Excel erzeugen, ablegen und am Kunden vermerken.

    Gibt (bytes, fehlende_pflichtfelder, dateiname, pfad) zurück. Was fehlt, ist in der
    Datei gelb markiert – die Vorlage bringt diese Prüfung selbst mit."""
    kunde = db.eine("SELECT * FROM kunde WHERE id=?", (kunde_id,))
    if not kunde:
        raise ValueError("Kunde nicht gefunden.")
    vor = daten.get("vorname") or ""
    nach = daten.get("nachname") or ""
    intern = _interne_id(kunde)
    voll = blattname(intern, vor, nach)

    mappe = openpyxl.load_workbook(VORLAGE)
    blatt = mappe["Muster"]
    blatt.title = _blattname_kurz(voll)
    fehlend = fill_cv.fuelle_blatt(blatt, daten)

    speicher = io.BytesIO()
    mappe.save(speicher)
    rohdaten = speicher.getvalue()

    os.makedirs(AUSGABE, exist_ok=True)
    dateiname = f"{voll}_{datetime.date.today():%Y-%m-%d}.xlsx"
    pfad = os.path.join(AUSGABE, dateiname)
    with open(pfad, "wb") as f:
        f.write(rohdaten)

    with db.offen() as con:
        con.execute(
            "INSERT INTO lebenslauf (kunde_id, datei_id, name, url, mime, geaendert, quelle,"
            " zugeordnet_ueber, eingelesen) VALUES (?,?,?,?,?,?,'os','hand',?)"
            " ON CONFLICT(datei_id) DO UPDATE SET name=excluded.name, geaendert=excluded.geaendert",
            (kunde_id, "os:" + dateiname, dateiname, "/lebenslauf/datei/" + dateiname,
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             datetime.datetime.now().isoformat(timespec="seconds"),
             datetime.datetime.now().isoformat(timespec="seconds")))
        # Für das Qualitätsmanagement zählt der Lebenslauf ab jetzt als vorhanden.
        con.execute(
            "INSERT INTO dokument (kunde_id, schluessel, vorhanden, dateiname, geaendert, geprueft)"
            " VALUES (?, 'lebenslauf', 1, ?, ?, ?)"
            " ON CONFLICT(kunde_id, schluessel) DO UPDATE SET vorhanden=1,"
            " dateiname=excluded.dateiname, geaendert=excluded.geaendert, geprueft=excluded.geprueft",
            (kunde_id, dateiname, datetime.datetime.now().isoformat(timespec="seconds"),
             datetime.datetime.now().isoformat(timespec="seconds")))
    return rohdaten, fehlend, dateiname, pfad


def aus_formular(form):
    """Formularfelder in die Struktur der Vorlage bringen.

    Gleiche Feldnamen wie in der CV-App, damit beide Wege austauschbar bleiben."""
    def w(k):
        return (form.get(k) or "").strip()

    def zeilen(text):
        return [z.strip() for z in (text or "").splitlines() if z.strip()]

    def reihen(*schluessel):
        """Parallele Formularfelder zu Zeilen zusammenlegen.

        Mit `zip` gingen alle Zeilen verloren, sobald **eine** der Listen kürzer war:
        Sprachen ohne Niveau verschwanden vollständig aus dem Lebenslauf, statt ohne
        Niveau zu erscheinen. Deshalb wird auf die längste Liste aufgefüllt."""
        listen = [form.getlist(s) for s in schluessel]
        if not listen or not any(listen):
            return []
        laenge = max(len(x) for x in listen)
        return [dict(zip(schluessel, [(x[i] if i < len(x) else "") for x in listen]))
                for i in range(laenge)]

    def sterne(v, standard=4):
        try:
            return max(1, min(5, int(v)))
        except (TypeError, ValueError):
            return standard

    daten = {
        "kunde_von": w("kunde_von") or fill_cv.DEFAULT_KUNDE_VON,
        "vorname": w("vorname"), "nachname": w("nachname"),
        "geschlecht": w("geschlecht"),
        "angestrebter_job": w("angestrebter_job"),
        "geburtsdatum": w("geburtsdatum"),
        "massnahme_zeitraum": w("massnahme_zeitraum"),
        "mobil": w("mobil"), "email": w("email"), "adresse": w("adresse"),
        "fuehrerschein": {"vorhanden": form.get("fs_vorhanden") == "on",
                          "klasse": w("fs_klasse"), "eu": form.get("fs_eu") == "on"},
        "ueber_mich": w("ueber_mich"), "hobbys": w("hobbys"),
        # Satzzeichen am Rand abschneiden: Aus einem Fließtext gelesene Stichpunkte
        # beginnen sonst mit „, Vertiefung der Deutschkenntnisse".
        "zusatzqualifikationen": [z.strip(" ,;.·-–—") for z in zeilen(w("zusatzqual"))
                                  if z.strip(" ,;.·-–—")],
        "berufserfahrung": [], "bildung": [], "sprachen": [],
        "edv_kenntnisse": [], "soft_skills": [],
    }
    for r in reihen("beruf_zeitraum", "beruf_firma", "beruf_jobtitel", "beruf_taet"):
        if any(r[k] for k in ("beruf_zeitraum", "beruf_firma", "beruf_jobtitel")):
            daten["berufserfahrung"].append({
                "zeitraum": r["beruf_zeitraum"], "firma": r["beruf_firma"],
                "jobtitel": r["beruf_jobtitel"], "taetigkeiten": zeilen(r["beruf_taet"])})
    for r in reihen("bild_zeitraum", "bild_abschluss", "bild_institution", "bild_note"):
        if any(r[k] for k in ("bild_zeitraum", "bild_abschluss", "bild_institution")):
            daten["bildung"].append({
                "zeitraum": r["bild_zeitraum"], "abschluss": r["bild_abschluss"],
                "institution": r["bild_institution"], "note": r["bild_note"]})
    for r in reihen("spr_sprache", "spr_niveau"):
        if r["spr_sprache"]:
            daten["sprachen"].append({"sprache": r["spr_sprache"].upper(), "niveau": r["spr_niveau"]})
    for r in reihen("edv_programm", "edv_sterne"):
        if r["edv_programm"]:
            daten["edv_kenntnisse"].append({"programm": r["edv_programm"], "sterne": sterne(r["edv_sterne"])})
    for r in reihen("ss_eigenschaft", "ss_sterne"):
        if r["ss_eigenschaft"]:
            daten["soft_skills"].append({"eigenschaft": r["ss_eigenschaft"], "sterne": sterne(r["ss_sterne"], 5)})
    return daten


def merken(kunde_id, dateiname, art="Lebenslauf"):
    """Eine erzeugte Datei am Kunden vermerken – Excel wie PDF, damit beides in der Akte steht."""
    jetzt = datetime.datetime.now().isoformat(timespec="seconds")
    with db.offen() as con:
        con.execute(
            "INSERT INTO lebenslauf (kunde_id, datei_id, name, url, mime, geaendert, quelle,"
            " zugeordnet_ueber, eingelesen) VALUES (?,?,?,?,?,?,'os','hand',?)"
            " ON CONFLICT(datei_id) DO UPDATE SET name=excluded.name, geaendert=excluded.geaendert",
            (kunde_id, "os:" + dateiname, dateiname, "/lebenslauf/datei/" + dateiname,
             "application/pdf" if dateiname.lower().endswith(".pdf")
             else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             jetzt, jetzt))


def gebaute(kunde_id=None):
    sql = ("SELECT l.*, k.name AS kunde FROM lebenslauf l JOIN kunde k ON k.id=l.kunde_id"
           " WHERE l.quelle='os'")
    args = ()
    if kunde_id:
        sql += " AND l.kunde_id=?"
        args = (kunde_id,)
    return db.hole(sql + " ORDER BY l.geaendert DESC", args)


def uebersicht(standort=None, suche=None, stand=None):
    """Eine Zeile je Kunde: Kundennummer, ob ein Lebenslauf da ist und ob er hier gebaut wurde."""
    standort = standort or db.STANDORT_STANDARD
    kunden = db.hole(
        "SELECT k.id, k.name, k.status_code, k.sprache, k.drive_id, m.name AS coach,"
        "  (SELECT COUNT(*) FROM lebenslauf l WHERE l.kunde_id=k.id) AS anzahl,"
        "  (SELECT COUNT(*) FROM lebenslauf l WHERE l.kunde_id=k.id AND l.quelle='os') AS eigene,"
        "  (SELECT l.name FROM lebenslauf l WHERE l.kunde_id=k.id AND l.quelle='os'"
        "     ORDER BY l.geaendert DESC LIMIT 1) AS datei"
        "  FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE k.standort=? ORDER BY k.name", (standort,))
    ids = {r["kunde_id"]: r["improfy_id"] for r in db.hole(
        "SELECT kunde_id, improfy_id FROM gutschein_zeile"
        " WHERE kunde_id IS NOT NULL AND improfy_id IS NOT NULL AND improfy_id<>''")}
    zeilen = []
    for k in kunden:
        k["interne_id"] = (ids.get(k["id"]) or "").strip()
        k["hat_cv"] = bool(k["anzahl"])
        k["gebaut"] = bool(k["eigene"])
        k["ampel"] = db.STATUS_AMPEL.get(k["status_code"], "grau")
        if suche and suche.casefold() not in (k["name"] or "").casefold():
            continue
        if stand == "ohne" and k["hat_cv"]:
            continue
        if stand == "mit" and not k["hat_cv"]:
            continue
        if stand == "gebaut" and not k["gebaut"]:
            continue
        zeilen.append(k)
    return zeilen


def datei(dateiname):
    pfad = os.path.join(AUSGABE, os.path.basename(dateiname))
    return pfad if os.path.exists(pfad) else None
