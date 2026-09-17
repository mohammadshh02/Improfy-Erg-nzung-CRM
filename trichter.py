# -*- coding: utf-8 -*-
"""Vertriebstrichter für die Standortleitung – vom WhatsApp-Lead bis zum Gutschein.

**Wozu.** Das CRM beginnt bei „Kunde anlegen". Alles davor – die WhatsApp-Anfrage, der
Rückruf, der Antrag beim Jobcenter, das Warten auf die Bewilligung – findet dort nicht
statt. Genau diese Strecke steuert aber ein Standortleiter täglich, und genau sie ist die
Zahl, nach der man einen Standort beurteilt: Wie viele Anfragen kommen rein, wie viele
werden Kunde, und wie lange dauert das.

**Die sechs Stufen** und woher sie kommen:

    1 Anfrage      Kommo (WhatsApp) oder von Hand · Tabelle `lead`
    2 Kontakt      Lead hat eine Stufe über „neu" erreicht
    3 Antrag raus  `kunde.antrag_datum` gesetzt
    4 Reaktion     `kunde.chat_reaktion` – im Raum „Anträge AVGS" mit 👍 bestätigt
    5 Bewilligung  `kunde.gutschein_status` steht auf bewilligt
    6 Kunde        Maßnahme läuft (Status H oder I) oder ist gelaufen

Jede Stufe zählt **auch alle, die schon weiter sind** – sonst sieht ein guter Monat aus wie
ein leerer. Die Quote einer Stufe ist der Anteil, der es von der vorigen bis hierher
geschafft hat.

**Durchlaufzeit** ist die ehrlichste Kennzahl des Vertriebs: Tage vom Antrag bis zur
Bewilligung. Sie sagt mehr über ein Jobcenter aus als jede Bewilligungsquote – ein Amt, das
sechs Wochen braucht, bindet Kapazität, die woanders fehlt.
"""
import datetime

import datenbank as db

# Statuscodes: welche Buchstaben bedeuten was. Siehe docs/wissen.
LAEUFT = ("H", "I")
GELAUFEN = ("J", "K", "L")
ABGESPRUNGEN = ("E", "F", "G")


def heute():
    return datetime.date.today()


def _seit(monate):
    if not monate:
        return None
    return (heute() - datetime.timedelta(days=30 * monate)).isoformat()


def _wo(seit, standort):
    bedingung, args = " WHERE 1=1", []
    if standort:
        bedingung += " AND k.standort=?"
        args.append(standort)
    if seit:
        bedingung += " AND (k.antrag_datum >= ? OR k.antrag_datum IS NULL)"
        args.append(seit)
    return bedingung, args


# So spricht der Bestand wirklich – nachgesehen am 17.09.2026, nicht geraten:
#   chat_reaktion  "👍 raus" (56×) heißt: der Antrag ist nachweislich verschickt.
#                  "⚪ keine Reaktion – nicht raus" heißt: er ist es gerade nicht.
#   gutschein_status "Gutschein da" (11×) ist die Bewilligung, nicht das Wort "bewilligt".
#                  "abgelehnt" (20×) ist der Ausstieg.
# Das ist die Chat-Regel aus dem Raum „Anträge AVGS": nur mit 👍 gilt ein Antrag als raus.
RAUS = "👍"
BEWILLIGT_WORTE = ("gutschein da", "bewilligt", "zugesagt")
ABGELEHNT_WORTE = ("abgelehnt", "abgesagt", "👎")


def _hat(text, worte):
    t = (text or "").lower()
    return any(w in t for w in worte)


def stufe_von(k):
    """Die höchste Stufe, die dieser Mensch erreicht hat. 0 = noch nichts.

    Bewusst als höchste erreichte Stufe und nicht als einzelne Häkchen: Nur so ist der
    Trichter monoton – jede Stufe enthält alle, die schon weiter sind. Sonst kommen
    Quoten über 100 % heraus, und die glaubt zu Recht niemand."""
    if k.get("status_code") in LAEUFT + GELAUFEN:
        return 5                                  # Kunde
    if _hat(k.get("gutschein_status"), BEWILLIGT_WORTE) or (k.get("gutschein_nr") or "").strip():
        return 4                                  # Bewilligung
    if RAUS in (k.get("chat_reaktion") or ""):
        return 3                                  # Antrag nachweislich raus
    if (k.get("antrag_datum") or "").strip():
        return 2                                  # Antrag vorbereitet
    return 1                                      # erfasst


STUFEN_NAMEN = [
    (1, "Erfasst", "im Bestand angelegt, noch kein Antrag"),
    (2, "Antrag vorbereitet", "Antragsdatum gesetzt"),
    (3, "Antrag raus", "im Raum Anträge AVGS mit 👍 bestätigt"),
    (4, "Bewilligung", "Gutschein liegt vor"),
    (5, "Kunde", "Maßnahme läuft oder ist gelaufen"),
]


def stufen(standort=db.STANDORT_STANDARD, monate=None):
    """Der Trichter: je Stufe die Anzahl derer, die mindestens so weit sind."""
    seit = _seit(monate)
    args = [standort]
    wo = " WHERE k.standort=?"
    if seit:
        wo += " AND (k.antrag_datum >= ? OR k.antrag_datum IS NULL)"
        args.append(seit)
    leute = db.hole(
        "SELECT k.status_code, k.gutschein_status, k.gutschein_nr, k.chat_reaktion,"
        "       k.antrag_datum FROM kunde k" + wo, args)
    erreicht = [stufe_von(k) for k in leute]
    abgelehnt = sum(1 for k in leute if _hat(k.get("gutschein_status"), ABGELEHNT_WORTE)
                    or _hat(k.get("chat_reaktion"), ABGELEHNT_WORTE))

    lead_wo, lead_args = " WHERE standort=?", [standort]
    if seit:
        lead_wo += " AND (eingang >= ? OR eingang IS NULL)"
        lead_args.append(seit)
    anfragen = db.wert(f"SELECT COUNT(*) FROM lead {lead_wo}", lead_args)

    liste = [{"stufe": "Anfrage", "anzahl": anfragen, "quote": None,
              "erklaerung": "aus Kommo (WhatsApp) – ohne Token noch leer"}]
    vorher = anfragen or None
    for nummer, name, erklaerung in STUFEN_NAMEN:
        anzahl = sum(1 for e in erreicht if e >= nummer)
        liste.append({"stufe": name, "anzahl": anzahl, "erklaerung": erklaerung,
                      "quote": round(anzahl * 100 / vorher) if vorher else None})
        vorher = anzahl or vorher
    liste.append({"stufe": "davon abgelehnt", "anzahl": abgelehnt, "quote": None,
                  "erklaerung": "Jobcenter hat abgelehnt oder abgesagt", "ausstieg": True})
    return liste


def durchlaufzeit(standort=db.STANDORT_STANDARD):
    """Tage vom Antrag bis zur Bewilligung, je Jobcenter.

    Die Zahl, die zeigt, welches Amt Kapazität bindet. Gerechnet wird über das
    Antragsdatum und das Datum der ersten Gutscheinzeile."""
    zeilen = db.hole(
        "SELECT k.jobcenter, k.antrag_datum,"
        "       (SELECT MIN(g.von) FROM gutschein_zeile g WHERE g.kunde_id=k.id) AS start"
        "  FROM kunde k WHERE k.standort=? AND k.antrag_datum IS NOT NULL"
        "   AND k.antrag_datum <> '' AND k.jobcenter IS NOT NULL AND k.jobcenter <> ''",
        (standort,))
    je_amt = {}
    for z in zeilen:
        if not z["start"]:
            continue
        try:
            tage = (datetime.date.fromisoformat(z["start"][:10])
                    - datetime.date.fromisoformat(z["antrag_datum"][:10])).days
        except (TypeError, ValueError):
            continue
        if not 0 <= tage <= 365:
            continue                     # offensichtlich falsch erfasst
        je_amt.setdefault(z["jobcenter"], []).append(tage)
    ergebnis = [{"jobcenter": amt, "faelle": len(tage), "schnitt": round(sum(tage) / len(tage)),
                 "schnellste": min(tage), "langsamste": max(tage)}
                for amt, tage in je_amt.items() if len(tage) >= 1]
    return sorted(ergebnis, key=lambda z: (-z["faelle"], z["schnitt"]))


def antraege(standort=db.STANDORT_STANDARD, offen_ab_tagen=None):
    """Alle gestellten Anträge mit ihrem Weg: raus → Reaktion → Bewilligung → Kunde.

    Das ist die Liste, die der Standortleiter morgens durchgeht. Was lange ohne Reaktion
    liegt, steht oben."""
    zeilen = db.hole(
        "SELECT k.id, k.name, k.antrag_datum, k.chat_reaktion, k.gutschein_status,"
        "       k.gutschein_nr, k.jobcenter, k.massnahme, k.status_code, m.name AS coach,"
        "       (SELECT MIN(g.von) FROM gutschein_zeile g WHERE g.kunde_id=k.id) AS start"
        "  FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE k.standort=? AND k.antrag_datum IS NOT NULL AND k.antrag_datum <> ''"
        " ORDER BY k.antrag_datum DESC", (standort,))
    heute_ = heute()
    liste = []
    for z in zeilen:
        try:
            tage = (heute_ - datetime.date.fromisoformat(z["antrag_datum"][:10])).days
        except (TypeError, ValueError):
            tage = None
        hoch = stufe_von(z)
        abgelehnt = (_hat(z["gutschein_status"], ABGELEHNT_WORTE)
                     or _hat(z["chat_reaktion"], ABGELEHNT_WORTE))
        z = dict(z, tage_offen=tage, bewilligt=hoch >= 4, ist_kunde=hoch >= 5,
                 abgelehnt=abgelehnt)
        z["stufe"] = {n: name for n, name, _ in STUFEN_NAMEN}[hoch]
        # Hängen heißt: raus oder vorbereitet, aber seit Wochen nichts zurück.
        z["haengt"] = (hoch in (2, 3) and not abgelehnt and tage is not None
                       and tage >= (offen_ab_tagen or 21))
        liste.append(z)
    if offen_ab_tagen:
        liste = [z for z in liste if z["haengt"]]
    return sorted(liste, key=lambda z: (not z["haengt"], -(z["tage_offen"] or 0)))


def je_monat(standort=db.STANDORT_STANDARD, monate=12):
    """Anträge und Bewilligungen je Monat – zeigt, ob der Vertrieb läuft oder stockt."""
    seit = (heute() - datetime.timedelta(days=31 * monate)).isoformat()
    zeilen = db.hole(
        "SELECT SUBSTR(k.antrag_datum,1,7) AS monat, COUNT(*) AS antraege,"
        "  SUM(CASE WHEN LOWER(COALESCE(k.gutschein_status,'')) LIKE '%gutschein da%'"
        "        OR COALESCE(k.gutschein_nr,'') <> '' THEN 1 ELSE 0 END) AS bewilligt,"
        "  SUM(CASE WHEN k.status_code IN ('H','I','J','K','L') THEN 1 ELSE 0 END) AS kunden"
        "  FROM kunde k WHERE k.standort=? AND k.antrag_datum >= ?"
        " GROUP BY monat ORDER BY monat DESC", (standort, seit))
    for z in zeilen:
        z["quote"] = round(z["bewilligt"] * 100 / z["antraege"]) if z["antraege"] else None
    return zeilen


def je_leiter(standort=db.STANDORT_STANDARD):
    """Was jede Person im Vertrieb bewegt hat – Anträge, Bewilligungen, Quote."""
    zeilen = db.hole(
        "SELECT COALESCE(m.name,'ohne Zuordnung') AS wer, COUNT(*) AS antraege,"
        "  SUM(CASE WHEN LOWER(COALESCE(k.gutschein_status,'')) LIKE '%gutschein da%'"
        "        OR COALESCE(k.gutschein_nr,'') <> '' THEN 1 ELSE 0 END) AS bewilligt,"
        "  SUM(CASE WHEN INSTR(COALESCE(k.chat_reaktion,''), '" + RAUS + "') > 0 THEN 1 ELSE 0 END) AS raus,"
        "  SUM(CASE WHEN k.status_code IN ('H','I') THEN 1 ELSE 0 END) AS laufend"
        "  FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE k.standort=? AND k.antrag_datum IS NOT NULL AND k.antrag_datum <> ''"
        " GROUP BY wer ORDER BY antraege DESC", (standort,))
    for z in zeilen:
        z["quote"] = round(z["bewilligt"] * 100 / z["antraege"]) if z["antraege"] else None
    return zeilen


def leadquellen(standort=db.STANDORT_STANDARD):
    """Woher die Anfragen kommen. Ohne Kommo-Token ist das leer – dann sagt die Seite das."""
    return db.hole(
        "SELECT COALESCE(quelle,'unbekannt') AS quelle, COUNT(*) AS anzahl,"
        "       SUM(CASE WHEN kunde_id IS NOT NULL THEN 1 ELSE 0 END) AS gewandelt"
        "  FROM lead WHERE standort=? GROUP BY quelle ORDER BY anzahl DESC", (standort,))


def leadstufen(standort=db.STANDORT_STANDARD):
    """Die Stufen, wie Kommo sie führt – je Stufe die Anzahl."""
    return db.hole(
        "SELECT COALESCE(status,'unbekannt') AS stufe, COUNT(*) AS anzahl"
        "  FROM lead WHERE standort=? GROUP BY stufe ORDER BY anzahl DESC", (standort,))


def zaehler(standort=db.STANDORT_STANDARD):
    offen = antraege(standort, offen_ab_tagen=21)
    return {"leads": db.wert("SELECT COUNT(*) FROM lead WHERE standort=?", (standort,)),
            "antraege": db.wert(
                "SELECT COUNT(*) FROM kunde WHERE standort=? AND antrag_datum IS NOT NULL"
                " AND antrag_datum <> ''", (standort,)),
            "haengen": len(offen),
            "raus": db.wert(
                "SELECT COUNT(*) FROM kunde WHERE standort=? AND INSTR(COALESCE(chat_reaktion,''), ?) > 0",
                (standort, RAUS)),
            "laufend": db.wert(
                "SELECT COUNT(*) FROM kunde WHERE standort=? AND status_code IN ('H','I')",
                (standort,))}
