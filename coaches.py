# -*- coding: utf-8 -*-
"""Der Überblick über jeden Coach – Betreuung und Arbeit im System.

**Warum diese Seite überhaupt.** Die Standortleitung muss zwei verschiedene Fragen
beantworten können, und beide waren vorher nur mühsam zu beantworten:

  Wen betreut wer und wie steht es dort?   Das stand verstreut in der Kundenliste;
      man musste 120 Zeilen durchsehen und selbst zählen.
  Wer arbeitet womit im System?            Das stand in der Mitarbeiter-Spur, aber
      ohne Bezug zur Betreuung – Zahlen ohne Bedeutung.

Hier stehen beide nebeneinander, je Coach eine Zeile.

**Ehrlich zur Aussagekraft der zweiten Hälfte.** Die Tätigkeitszahlen beschreiben, was
jemand *in diesem System* getan hat. Wer im CRM arbeitet und hier nichts anfasst, steht
mit Nullen da – das heißt nicht, dass er nichts tut. Solange die Coaches noch keine
eigenen Zugänge haben, ist diese Spalte leer und die Seite sagt das auch.
Was wirklich gemessen wird, ist die Betreuung links.

**Was hier nicht hingehört.** Kein Ranking, keine Bewertung, keine Notenskala. Die
Zahlen zeigen Last und Lücken, nicht Leistung: 53 Kunden gegen 9 sagt etwas über die
Verteilung, nicht über den Menschen.
"""
import datenbank as db

LAUFEND = ("H", "I")            # Statuscodes: Maßnahme läuft
OHNE_COACH = "ohne Coach"
HAENGT_TAGE = 21                # so lange darf eine Bewerbung ohne Antwort liegen


def _zahl(sql, werte=()):
    z = db.eine(sql, werte)
    return (z["n"] if z else 0) or 0


def _je_coach(sql, werte=()):
    """Ein Zählergebnis je Coach-Name als Wörterbuch – fehlt einer, ist er schlicht 0."""
    return {(z["coach"] or OHNE_COACH): z["n"] for z in db.hole(sql, werte)}


def uebersicht():
    """Je Coach eine Zeile: wen er betreut, wo es hakt, was er im System getan hat."""
    st = (db.STANDORT_STANDARD,)

    kunden = db.hole(
        "SELECT m.id AS coach_id, m.name AS coach, m.rolle, m.kuerzel, m.aktiv,"
        "       COUNT(k.id) AS kunden,"
        "       SUM(CASE WHEN k.status_code IN ('H','I') THEN 1 ELSE 0 END) AS laufend"
        "  FROM mitarbeiter m LEFT JOIN kunde k ON k.coach_id = m.id AND k.standort = ?"
        " GROUP BY m.id ORDER BY kunden DESC", st)

    # Kunden ohne zugeordneten Coach sind niemandes Aufgabe – und fallen deshalb durch.
    # Sie bekommen eine eigene Zeile, sonst tauchen sie nirgends auf.
    herrenlos = db.eine(
        "SELECT COUNT(*) AS kunden,"
        "       SUM(CASE WHEN status_code IN ('H','I') THEN 1 ELSE 0 END) AS laufend"
        "  FROM kunde WHERE coach_id IS NULL AND standort = ?", st)

    ohne_cv = _je_coach(
        "SELECT m.name AS coach, COUNT(*) AS n FROM kunde k"
        "  LEFT JOIN mitarbeiter m ON m.id = k.coach_id"
        "  LEFT JOIN lebenslauf l ON l.kunde_id = k.id"
        "  LEFT JOIN kunde_profil p ON p.kunde_id = k.id"
        " WHERE k.standort = ? AND k.status_code IN ('H','I') AND l.id IS NULL"
        "   AND (p.cv_text IS NULL OR p.cv_text = '')"
        " GROUP BY m.name", st)

    # Ohne JEDES Profil, auch ohne pausiertes – die eine Zählweise des Hauses (siehe
    # `sammelanlage.vorschlaege`). Die Spalte heisst „ohne Profil"; wer eins hat und es
    # abgeschaltet hat, gehört nicht hierher, sondern auf den Arbeitsplatz seiner Art.
    ohne_profil = _je_coach(
        "SELECT m.name AS coach, COUNT(*) AS n FROM kunde k"
        "  LEFT JOIN mitarbeiter m ON m.id = k.coach_id"
        "  LEFT JOIN tf_profil t ON t.kunde_id = k.id"
        " WHERE k.standort = ? AND k.status_code IN ('H','I') AND t.id IS NULL"
        " GROUP BY m.name", st)

    beworben = _je_coach(
        "SELECT m.name AS coach, COUNT(*) AS n FROM tf_angebot a"
        "  JOIN tf_profil t ON t.id = a.profil_id"
        "  JOIN kunde k ON k.id = t.kunde_id"
        "  LEFT JOIN mitarbeiter m ON m.id = k.coach_id"
        " WHERE k.standort = ? AND a.status = 'beworben' GROUP BY m.name", st)

    # Eine Bewerbung, die drei Wochen ohne Antwort liegt, ist entweder abgelehnt oder
    # vergessen. Beides muss jemand anfassen.
    haengt = _je_coach(
        "SELECT m.name AS coach, COUNT(*) AS n FROM tf_angebot a"
        "  JOIN tf_profil t ON t.id = a.profil_id"
        "  JOIN kunde k ON k.id = t.kunde_id"
        "  LEFT JOIN mitarbeiter m ON m.id = k.coach_id"
        " WHERE k.standort = ? AND a.status = 'beworben'"
        "   AND a.status_am IS NOT NULL"
        "   AND julianday('now') - julianday(a.status_am) > ?"
        " GROUP BY m.name", (db.STANDORT_STANDARD, HAENGT_TAGE))

    nachrichten = _je_coach(
        "SELECT m.name AS coach, COUNT(*) AS n FROM kundennachricht n"
        "  JOIN kunde k ON k.id = n.kunde_id"
        "  LEFT JOIN mitarbeiter m ON m.id = k.coach_id"
        " WHERE k.standort = ? GROUP BY m.name", st)

    # Tätigkeit im System: der Name im Protokoll ist der Anmeldename, nicht die
    # Personalnummer. Passt er auf keinen Coach, bleibt die Spalte leer – lieber nichts
    # als eine falsche Zuordnung.
    taetig = {z["benutzer"]: z for z in db.hole(
        "SELECT benutzer, COUNT(*) AS aktionen,"
        "       COUNT(DISTINCT SUBSTR(zeitpunkt,1,10)) AS tage,"
        "       MAX(zeitpunkt) AS zuletzt,"
        "       SUM(CASE WHEN aktion = 'angemeldet' THEN 1 ELSE 0 END) AS anmeldungen"
        "  FROM protokoll WHERE benutzer IS NOT NULL GROUP BY benutzer")}

    import aufgaben
    offen = {a["coach"]: a for a in aufgaben.je_coach()}

    zeilen = []
    for k in kunden:
        name = k["coach"]
        a = offen.get(name) or {}
        t = taetig.get(name) or {}
        zeilen.append({
            "coach_id": k["coach_id"], "coach": name, "rolle": k["rolle"] or "",
            "kuerzel": k["kuerzel"] or "", "aktiv": k["aktiv"],
            "kunden": k["kunden"] or 0, "laufend": k["laufend"] or 0,
            "ohne_cv": ohne_cv.get(name, 0), "ohne_profil": ohne_profil.get(name, 0),
            "beworben": beworben.get(name, 0), "haengt": haengt.get(name, 0),
            "nachrichten": nachrichten.get(name, 0),
            "rot": a.get("rot", 0), "gelb": a.get("gelb", 0),
            "aufgaben": a.get("gesamt", 0), "naechste_frist": a.get("naechste_frist"),
            "aktionen": t.get("aktionen", 0), "tage_aktiv": t.get("tage", 0),
            "zuletzt": t.get("zuletzt"), "anmeldungen": t.get("anmeldungen", 0),
        })

    if herrenlos and (herrenlos["kunden"] or 0):
        a = offen.get(OHNE_COACH) or {}
        zeilen.append({
            "coach_id": None, "coach": OHNE_COACH, "rolle": "", "kuerzel": "", "aktiv": 1,
            "kunden": herrenlos["kunden"], "laufend": herrenlos["laufend"] or 0,
            "ohne_cv": ohne_cv.get(OHNE_COACH, 0),
            "ohne_profil": ohne_profil.get(OHNE_COACH, 0),
            "beworben": beworben.get(OHNE_COACH, 0), "haengt": haengt.get(OHNE_COACH, 0),
            "nachrichten": nachrichten.get(OHNE_COACH, 0),
            "rot": a.get("rot", 0), "gelb": a.get("gelb", 0),
            "aufgaben": a.get("gesamt", 0), "naechste_frist": a.get("naechste_frist"),
            "aktionen": 0, "tage_aktiv": 0, "zuletzt": None, "anmeldungen": 0,
        })
    return sorted(zeilen, key=lambda z: (-z["laufend"], -z["kunden"]))


def kunden(coach_id):
    """Die Kunden eines Coaches mit dem, was an ihnen offen ist."""
    wo = "k.coach_id = ?" if coach_id else "k.coach_id IS NULL"
    werte = (db.STANDORT_STANDARD, coach_id) if coach_id else (db.STANDORT_STANDARD,)
    return db.hole(
        "SELECT k.id, k.name, k.status_code, k.stadt, k.massnahme, k.naechster_schritt,"
        "       (SELECT COUNT(*) FROM lebenslauf l WHERE l.kunde_id = k.id) AS cvs,"
        # Jedes Profil, auch ein pausiertes – dieselbe Zählart wie `ohne_profil()`
        # darüber. Hier stand `aktiv=1`, und damit fiel ein Mensch mit genau einem
        # pausierten Profil zwischen beide Stühle: die Coachseite zeigte „keins",
        # während ihn die Kundenliste und die Sammelanlage nicht mehr als „ohne
        # Profil" führten. Ein Klick auf „pausieren" genügte dafür.
        "       (SELECT COUNT(*) FROM tf_profil t"
        "         WHERE t.kunde_id = k.id) AS profile,"
        "       (SELECT COUNT(*) FROM tf_angebot a JOIN tf_profil t ON t.id = a.profil_id"
        "         WHERE t.kunde_id = k.id AND a.status = 'beworben') AS beworben,"
        "       (SELECT p.cv_text FROM kunde_profil p WHERE p.kunde_id = k.id) AS cv_text"
        "  FROM kunde k WHERE k.standort = ? AND " + wo +
        " ORDER BY CASE WHEN k.status_code IN ('H','I') THEN 0 ELSE 1 END, k.name", werte)


def einer(coach_id):
    for z in uebersicht():
        if z["coach_id"] == coach_id:
            return z
    return None


def zaehler():
    """Die Kopfzahlen der Seite – und ob die Tätigkeitsspalte überhaupt etwas hergibt."""
    z = uebersicht()
    betreuende = [x for x in z if x["coach_id"] and x["kunden"]]
    return {
        "coaches": len(betreuende),
        "kunden": sum(x["kunden"] for x in z),
        "laufend": sum(x["laufend"] for x in z),
        "ohne_cv": sum(x["ohne_cv"] for x in z),
        "ohne_profil": sum(x["ohne_profil"] for x in z),
        "haengt": sum(x["haengt"] for x in z),
        "hoechste_last": max((x["laufend"] for x in betreuende), default=0),
        "niedrigste_last": min((x["laufend"] for x in betreuende), default=0),
        # Solange niemand mit eigenem Zugang arbeitet, ist die rechte Hälfte leer.
        # Das muss die Seite sagen, statt Nullen als Befund auszugeben.
        "spur_hat_daten": any(x["aktionen"] for x in z),
    }
