# -*- coding: utf-8 -*-
"""Kundennachrichten über WhatsApp: was wir beworben haben, geht an den Kunden.

**Der Gedanke.** Die Taskforce bewirbt sich für den Kunden – der Kunde erfährt es heute
per Telefon, wenn überhaupt. Aus fünf Treffern werden fünf Anrufe. Besser: eine Nachricht
mit allem, was diese Woche für ihn rausging, über die zentrale Improfy-Nummer.

**Der Coach gibt frei, nicht die Maschine.** Das System stellt die Nachricht zusammen und
zeigt sie; abgeschickt wird sie erst mit einem bewussten Klick. Das ist keine
Ängstlichkeit: Eine Maschine, die ungefragt hunderte Nachrichten an echte Menschen
schickt, produziert Beschwerden, und eine von Meta gesperrte Nummer kostet den ganzen
Kanal – samt der Anfragen, die darüber hereinkommen. Dagegen sind drei Sekunden für einen
Klick nichts.

**Zwei Wege, einer davon ohne jede Einrichtung:**

1. **WhatsApp-Link** (funktioniert sofort, nichts einzurichten): Der Coach klickt, WhatsApp
   öffnet sich mit fertigem Text an genau diese Nummer, er drückt auf Senden. Kein Token,
   keine Vorlage, keine Genehmigung – und der Versand läuft über den Account, in dem der
   Coach ohnehin arbeitet.
2. **Über Kommo** (wenn `KOMMO_TOKEN` und ein Kanal eingerichtet sind): Die Nachricht geht
   über die zentrale Nummer und landet im selben Verlauf wie die eingehenden Anfragen.
   Achtung, Regel von Meta: Außerhalb von 24 Stunden nach der letzten Kundennachricht sind
   **nur freigegebene Vorlagen** erlaubt, Freitext geht nur innerhalb des Fensters.

**Was verschickt wurde, wird festgehalten** – damit dieselbe Stelle nicht zweimal
angekündigt wird und damit im Zweifel nachvollziehbar ist, was ein Kunde wann erfahren hat.
"""
import datetime
import os
import re
import urllib.parse

import datenbank as db

SCHEMA = """
CREATE TABLE IF NOT EXISTS kundennachricht (
    id         INTEGER PRIMARY KEY,
    kunde_id   INTEGER NOT NULL,
    zeitpunkt  TEXT NOT NULL,
    weg        TEXT,
    nummer     TEXT,
    text       TEXT,
    angebote   TEXT,
    bearbeiter TEXT
);
CREATE INDEX IF NOT EXISTS idx_kn_kunde ON kundennachricht(kunde_id, zeitpunkt DESC);
"""
# Die zentrale Nummer, unter der Improfy schreibt. Steht auf improfy.de/kontakt –
# dort sind drei WhatsApp-Nummern verlinkt, deshalb ist sie einstellbar statt geraten:
#   +49 178 3079654    (zweimal verlinkt, vermutlich die Hauptnummer)
#   +49 1521 5677975
#   +49 163 6926669
# Sie zählt nur für den Weg über Kommo. Beim WhatsApp-Link sendet der Coach aus seinem
# eigenen Konto – dort ist der Absender das Gerät, an dem er sitzt.
ZENTRALE_VORSCHLAEGE = ["+49 178 3079654", "+49 1521 5677975", "+49 163 6926669"]


def zentrale():
    return (os.environ.get("WHATSAPP_ZENTRALE") or "").strip()


# Nur diese Stände lohnen eine Nachricht: beworben oder Rückmeldung da.
GEMELDET = ("angeschrieben", "antwort", "erfolg")
LAEUFT = ("H", "I")


def init():
    with db.offen() as con:
        con.executescript(SCHEMA)


def jetzt():
    return datetime.datetime.now().isoformat(timespec="seconds")


def nummer(telefon):
    """Deutsche Schreibweisen in die Form bringen, die WhatsApp erwartet: 491760000000.

    Die Beispielnummer ist erfunden (Endung lauter Nullen). Hier stand die Handynummer
    einer echten Kundin – in einer versionierten Datei hat so etwas nichts zu suchen.

    Eine Nummer mit + oder 00 trägt ihre Landesvorwahl schon – die bleibt stehen. Sonst
    bekäme eine österreichische Nummer eine deutsche Vorwahl vorgeklebt und ginge an
    irgendwen."""
    roh = (telefon or "").strip()
    international = roh.startswith("+") or re.sub(r"\D", "", roh).startswith("00")
    ziffern = re.sub(r"\D", "", roh)
    if not ziffern:
        return ""
    if ziffern.startswith("00"):
        ziffern = ziffern[2:]
    elif ziffern.startswith("0"):
        ziffern = "49" + ziffern[1:]
    elif not international and not ziffern.startswith("49"):
        ziffern = "49" + ziffern
    return ziffern if 10 <= len(ziffern) <= 15 else ""


def _kurz(a):
    """Eine Zeile je Angebot – knapp, weil WhatsApp mitliest und niemand Romane liest."""
    teile = [a.get("titel") or "Angebot"]
    if a.get("anbieter"):
        teile.append(a["anbieter"])
    if a.get("ort"):
        teile.append(a["ort"])
    return "• " + " · ".join(str(t)[:60] for t in teile)


def text_bauen(kunde, angebote, art=None):
    """Die Nachricht. Bewusst nüchtern, ohne Werbesprache und ohne Versprechen."""
    vorname = (kunde.get("name") or "").split()[0] if kunde.get("name") else ""
    jobs = [a for a in angebote if a.get("art") == "job"]
    wohnungen = [a for a in angebote if a.get("art") == "wohnung"]
    zeilen = [f"Guten Tag {vorname},".strip().rstrip(",") + ",", ""]
    if jobs:
        zeilen.append(f"wir haben uns für Sie auf {len(jobs)} Stelle"
                      f"{'n' if len(jobs) != 1 else ''} beworben:")
        zeilen += [_kurz(a) for a in jobs[:8]]
        if len(jobs) > 8:
            zeilen.append(f"… und {len(jobs) - 8} weitere")
        zeilen.append("")
    if wohnungen:
        zeilen.append(f"außerdem haben wir {len(wohnungen)} Wohnung"
                      f"{'en' if len(wohnungen) != 1 else ''} für Sie angefragt:")
        zeilen += [_kurz(a) for a in wohnungen[:8]]
        if len(wohnungen) > 8:
            zeilen.append(f"… und {len(wohnungen) - 8} weitere")
        zeilen.append("")
    zeilen.append("Sobald es eine Rückmeldung gibt, melden wir uns. "
                  "Bei Fragen antworten Sie einfach auf diese Nachricht.")
    zeilen.append("")
    zeilen.append("Ihr Improfy-Team Köln")
    return "\n".join(zeilen).strip()


def wa_link(telefon, text):
    """Der Link, der WhatsApp mit fertigem Text öffnet. Funktioniert ohne jede Einrichtung."""
    n = nummer(telefon)
    if not n:
        return ""
    return f"https://wa.me/{n}?text={urllib.parse.quote(text)}"


def schon_gemeldet(kunde_id):
    """Angebots-IDs, die dieser Kunde schon genannt bekommen hat."""
    gemeldet = set()
    for z in db.hole("SELECT angebote FROM kundennachricht WHERE kunde_id=?", (kunde_id,)):
        for teil in (z["angebote"] or "").split(","):
            if teil.strip():
                gemeldet.add(teil.strip())
    return gemeldet


def vorschlaege(standort=db.STANDORT_STANDARD, nur_neue=True):
    """Je Kunde: was beworben wurde und noch nicht mitgeteilt ist."""
    zeilen = db.hole(
        "SELECT a.id, a.titel, a.anbieter, a.ort, a.status, a.status_am, p.art,"
        "       k.id AS kunde_id, k.name AS kunde, k.telefon, m.name AS coach"
        "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
        "  JOIN kunde k ON k.id=p.kunde_id LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        # Maßgeblich ist das aktive Suchprofil, nicht der Status des Kunden: Wer ein
        # Profil laufen lässt, will seinen Kunden auch informieren – auch nach dem
        # offiziellen Ende der Maßnahme, wenn die Vermittlung noch läuft.
        " WHERE a.status IN ('angeschrieben','antwort','erfolg') AND p.standort=?"
        "   AND p.aktiv=1"
        " ORDER BY k.name, a.status_am DESC", (standort,))
    je_kunde = {}
    for z in zeilen:
        eintrag = je_kunde.setdefault(z["kunde_id"], {
            "kunde_id": z["kunde_id"], "name": z["kunde"], "telefon": z["telefon"],
            "coach": z["coach"], "angebote": []})
        eintrag["angebote"].append(z)
    liste = []
    for eintrag in je_kunde.values():
        gemeldet = schon_gemeldet(eintrag["kunde_id"]) if nur_neue else set()
        offen = [a for a in eintrag["angebote"] if str(a["id"]) not in gemeldet]
        if nur_neue and not offen:
            continue
        eintrag["neu"] = offen
        eintrag["text"] = text_bauen(eintrag, offen or eintrag["angebote"])
        eintrag["nummer"] = nummer(eintrag["telefon"])
        eintrag["link"] = wa_link(eintrag["telefon"], eintrag["text"])
        eintrag["letzte"] = db.eine(
            "SELECT zeitpunkt, weg FROM kundennachricht WHERE kunde_id=?"
            " ORDER BY zeitpunkt DESC LIMIT 1", (eintrag["kunde_id"],))
        liste.append(eintrag)
    return sorted(liste, key=lambda e: (-len(e["neu"]), e["name"]))


def vermerken(kunde_id, text, angebote_ids, weg="wa-link", bearbeiter=None, telefon=None):
    """Festhalten, was ein Kunde wann erfahren hat."""
    with db.offen() as con:
        con.execute(
            "INSERT INTO kundennachricht (kunde_id, zeitpunkt, weg, nummer, text, angebote,"
            " bearbeiter) VALUES (?,?,?,?,?,?,?)",
            (kunde_id, jetzt(), weg, nummer(telefon or ""), text,
             ",".join(str(i) for i in angebote_ids), bearbeiter))


def verlauf(kunde_id=None, limit=100):
    sql = "SELECT n.*, k.name AS kunde FROM kundennachricht n JOIN kunde k ON k.id=n.kunde_id"
    args = []
    if kunde_id:
        sql += " WHERE n.kunde_id=?"
        args.append(kunde_id)
    return db.hole(sql + " ORDER BY n.zeitpunkt DESC LIMIT ?", args + [limit])


def zaehler(standort=db.STANDORT_STANDARD):
    offen = vorschlaege(standort)
    return {"kunden_offen": len(offen),
            "angebote_offen": sum(len(e["neu"]) for e in offen),
            "gesendet": db.wert("SELECT COUNT(*) FROM kundennachricht"),
            "ohne_nummer": sum(1 for e in offen if not e["nummer"])}


# --------------------------------------------------------------- Weg über Kommo
def kommo_bereit():
    """Der Versand über die zentrale Nummer braucht Token **und** einen eingerichteten Kanal."""
    return bool(os.environ.get("KOMMO_TOKEN") and os.environ.get("KOMMO_KANAL"))


def kommo_hinweis():
    """Was noch fehlt – in Worten, die man in Kommo wiederfindet."""
    if kommo_bereit():
        return ""
    fehlt = []
    if not os.environ.get("KOMMO_TOKEN"):
        fehlt.append("KOMMO_TOKEN (Kommo → Einstellungen → Integration marketplace → "
                     "Improfy-OS → Keys and scopes)")
    if not os.environ.get("KOMMO_KANAL"):
        fehlt.append("KOMMO_KANAL (die Kennung des WhatsApp-Kanals in Kommo)")
    return "Für den Versand über die zentrale Nummer fehlt: " + " und ".join(fehlt) + "."
