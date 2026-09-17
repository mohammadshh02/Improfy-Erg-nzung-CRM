# -*- coding: utf-8 -*-
"""Mitarbeiter-Verfolgung: wer war wann angemeldet und was hat er getan.

**Warum das hier steht und nicht im CRM.** Das CRM führt Nutzer und zeigt je Person die
Zahl ihrer Kunden und die Auslastung im Kalender. Was jemand **tatsächlich getan** hat –
wann angemeldet, von welchem Gerät, welche Datensätze angefasst, wie lange am Stück
gearbeitet – führt es nicht. Genau das ist aber die Frage einer Standortleitung: nicht
„wie viele Kunden hat Sheida", sondern „was ist gestern passiert".

**Woher die Daten kommen.** Nichts wird zusätzlich erhoben. Jede Änderung schreibt ohnehin
eine Zeile ins Protokoll (`konten.notieren`); dieses Modul liest sie nur anders herum –
nach Person und Tag statt nach Datensatz. Dazu kommen die Anmeldungen mit Gerät und
Adresse, die beim Anmelden ohnehin anfallen.

**Was bewusst nicht erfasst wird.** Keine Tastenanschläge, keine Mauswege, keine
Bildschirmfotos, kein Zählen von Leerlauf. Erfasst wird, was fachlich passiert ist – das
ist für die Steuerung dasselbe und für die Mitarbeitenden zumutbar. Eine Software, die
Menschen überwacht statt Arbeit sichtbar macht, wird umgangen statt genutzt.

**Tagesspur** heißt: erste Anmeldung, letzte Aktion, Anzahl der Änderungen, welche Bereiche,
wie viele verschiedene Kunden. Die Spanne zwischen erster und letzter Aktion ist **keine
Arbeitszeit** – sie sagt nur, über welchen Zeitraum hinweg gearbeitet wurde. Als
Zeiterfassung taugt sie nicht und ist auch nicht dafür gedacht.
"""
import datetime

import datenbank as db

NACHRUESTEN = [("protokoll", "ip", "TEXT"),
               ("protokoll", "geraet", "TEXT")]


def init():
    """Die zwei Zusatzspalten am Protokoll – laufen ohne Datenverlust nach."""
    with db.offen() as con:
        for tabelle, spalte, typ in NACHRUESTEN:
            try:
                vorhanden = {r[1] for r in con.execute(f"PRAGMA table_info({tabelle})")}
            except Exception:
                continue
            if vorhanden and spalte not in vorhanden:
                con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")


def heute():
    return datetime.date.today()


def _grenze(tage):
    return (datetime.datetime.now() - datetime.timedelta(days=tage)).isoformat()


def geraet_kurz(kennung):
    """Aus der langen Browserkennung ein lesbares Wort machen."""
    t = (kennung or "").lower()
    if not t:
        return ""
    system = ("Windows" if "windows" in t else "Mac" if "mac os" in t else
              "Android" if "android" in t else "iPhone" if "iphone" in t else
              "iPad" if "ipad" in t else "Linux" if "linux" in t else "")
    browser = ("Edge" if "edg/" in t else "Chrome" if "chrome" in t else
               "Firefox" if "firefox" in t else "Safari" if "safari" in t else "")
    return " · ".join(x for x in (system, browser) if x) or "unbekannt"


# ------------------------------------------------------------------ Überblick
def leute(tage=30):
    """Alle, die im Zeitraum etwas getan haben – mit den Kennzahlen der Steuerung."""
    zeilen = db.hole(
        "SELECT p.benutzer, p.rolle, COUNT(*) AS aktionen,"
        "       COUNT(DISTINCT SUBSTR(p.zeitpunkt,1,10)) AS tage,"
        "       MIN(p.zeitpunkt) AS zuerst, MAX(p.zeitpunkt) AS zuletzt,"
        "       COUNT(DISTINCT CASE WHEN p.bereich='taskforce' THEN p.objekt_id END) AS taskforce,"
        "       SUM(CASE WHEN p.aktion='angemeldet' THEN 1 ELSE 0 END) AS anmeldungen"
        "  FROM protokoll p WHERE p.zeitpunkt >= ? AND p.benutzer IS NOT NULL"
        " GROUP BY p.benutzer ORDER BY aktionen DESC", (_grenze(tage),))
    for z in zeilen:
        z["je_tag"] = round(z["aktionen"] / z["tage"], 1) if z["tage"] else 0
    return zeilen


def tagesspur(benutzer, tage=14):
    """Je Tag: erste Anmeldung, letzte Aktion, was gemacht wurde."""
    zeilen = db.hole(
        "SELECT SUBSTR(zeitpunkt,1,10) AS tag, COUNT(*) AS aktionen,"
        "       MIN(zeitpunkt) AS von, MAX(zeitpunkt) AS bis,"
        "       COUNT(DISTINCT bereich) AS bereiche,"
        "       COUNT(DISTINCT objekt_id) AS datensaetze,"
        "       SUM(CASE WHEN aktion='angemeldet' THEN 1 ELSE 0 END) AS anmeldungen"
        "  FROM protokoll WHERE benutzer=? AND zeitpunkt >= ?"
        " GROUP BY tag ORDER BY tag DESC", (benutzer, _grenze(tage)))
    for z in zeilen:
        z["spanne"] = _spanne(z["von"], z["bis"])
        z["womit"] = ", ".join(
            f"{r['bereich'] or 'sonstiges'} ({r['n']})" for r in db.hole(
                "SELECT bereich, COUNT(*) AS n FROM protokoll"
                " WHERE benutzer=? AND SUBSTR(zeitpunkt,1,10)=?"
                " GROUP BY bereich ORDER BY n DESC LIMIT 5", (benutzer, z["tag"])))
    return zeilen


def _spanne(von, bis):
    """Stunden zwischen erster und letzter Aktion – ausdrücklich keine Arbeitszeit."""
    try:
        a = datetime.datetime.fromisoformat(von)
        b = datetime.datetime.fromisoformat(bis)
    except (TypeError, ValueError):
        return None
    stunden = (b - a).total_seconds() / 3600
    return round(stunden, 1) if stunden > 0 else 0


def anmeldungen(benutzer=None, tage=30, limit=100):
    """Wann und von wo sich jemand angemeldet hat."""
    sql = ("SELECT zeitpunkt, benutzer, rolle, ip, geraet FROM protokoll"
           " WHERE aktion='angemeldet' AND zeitpunkt >= ?")
    args = [_grenze(tage)]
    if benutzer:
        sql += " AND benutzer=?"
        args.append(benutzer)
    sql += " ORDER BY zeitpunkt DESC LIMIT ?"
    args.append(limit)
    zeilen = db.hole(sql, args)
    for z in zeilen:
        z["geraet_kurz"] = geraet_kurz(z["geraet"])
    return zeilen


def verlauf(benutzer, tage=30, limit=300, bereich=None):
    """Die einzelnen Schritte einer Person – das, was im Zweifel zählt."""
    sql = "SELECT * FROM protokoll WHERE benutzer=? AND zeitpunkt >= ?"
    args = [benutzer, _grenze(tage)]
    if bereich:
        sql += " AND bereich=?"
        args.append(bereich)
    sql += " ORDER BY zeitpunkt DESC LIMIT ?"
    args.append(limit)
    return db.hole(sql, args)


def bereiche(benutzer=None, tage=30):
    sql = ("SELECT COALESCE(bereich,'sonstiges') AS bereich, COUNT(*) AS n FROM protokoll"
           " WHERE zeitpunkt >= ?")
    args = [_grenze(tage)]
    if benutzer:
        sql += " AND benutzer=?"
        args.append(benutzer)
    return db.hole(sql + " GROUP BY bereich ORDER BY n DESC", args)


def kunden_beruehrt(benutzer, tage=30):
    """Welche Kunden jemand angefasst hat – über die Taskforce-Angebote aufgelöst."""
    return db.hole(
        "SELECT k.id, k.name, COUNT(*) AS aktionen, MAX(p.zeitpunkt) AS zuletzt"
        "  FROM protokoll p"
        "  JOIN tf_angebot a ON CAST(a.id AS TEXT)=p.objekt_id AND p.bereich='taskforce'"
        "  JOIN tf_profil pr ON pr.id=a.profil_id JOIN kunde k ON k.id=pr.kunde_id"
        " WHERE p.benutzer=? AND p.zeitpunkt >= ?"
        " GROUP BY k.id ORDER BY aktionen DESC LIMIT 20", (benutzer, _grenze(tage)))


def zaehler(tage=30):
    return {"personen": db.wert(
        "SELECT COUNT(DISTINCT benutzer) FROM protokoll WHERE zeitpunkt >= ?", (_grenze(tage),)),
        "aktionen": db.wert("SELECT COUNT(*) FROM protokoll WHERE zeitpunkt >= ?", (_grenze(tage),)),
        "anmeldungen": db.wert(
            "SELECT COUNT(*) FROM protokoll WHERE aktion='angemeldet' AND zeitpunkt >= ?",
            (_grenze(tage),)),
        "heute": db.wert("SELECT COUNT(*) FROM protokoll WHERE SUBSTR(zeitpunkt,1,10)=?",
                         (heute().isoformat(),))}
