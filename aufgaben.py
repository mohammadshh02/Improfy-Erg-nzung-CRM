# -*- coding: utf-8 -*-
"""Aufgaben und Fristen – was heute jemand tun muss, mit Namen dran.

**Warum das gefehlt hat.** Das OS konnte bisher sehr gut *berichten*: 9 Maßnahmen ohne
Termine, 94 Kunden ohne ID-K, 482 Angebote. Nur war das alles Beobachtung. Ein Träger
lebt aber von „wer macht was bis wann". Ein Bericht, den niemand in eine Handlung
übersetzt, ändert nichts – und genau das war zu sehen: 482 gefundene Angebote, null
angeschrieben.

Dieses Modul dreht die Richtung um. Aus denselben Daten entstehen **Aufgaben**: je eine
Zeile mit Dringlichkeit, Frist, zuständiger Person und einem Link, der genau dorthin führt,
wo man sie erledigt. Die Regeln sind hier zentral aufgeschrieben, damit man sie an einer
Stelle diskutieren und ändern kann.

**Dringlichkeit** in drei Stufen, und die Zuordnung ist kaufmännisch gedacht:
  rot   – es geht um Geld oder eine Frist läuft ab
  gelb  – die Arbeit ist blockiert, jemand wartet
  grau  – sollte gemacht werden, eilt aber nicht

**Zuständig** ist immer der Coach des Kunden. Wo kein Coach hinterlegt ist, landet die
Aufgabe bei der Standortleitung – herrenlose Aufgaben gibt es nicht.
"""
import datetime

import datenbank as db

try:                                       # Flask nur, wenn es da ist: dieses Modul läuft
    from flask import g, has_app_context   # auch aus `taskforce.py --lauf`, ohne Anfrage
except ImportError:                        # und ohne Webserver.
    g = None

    def has_app_context():
        return False

FRIST_WARNUNG = 14          # Tage vor Maßnahmeende, ab denen es eilt
WIEDERVORLAGE = 7           # Tage ohne Antwort auf eine Bewerbung
LAUFEND = ("H", "I")        # Statuscodes: Maßnahme läuft
LEITUNG = "Standortleitung"


def heute():
    return datetime.date.today()


def _tage_bis(iso):
    try:
        return (datetime.date.fromisoformat(iso) - heute()).days
    except (TypeError, ValueError):
        return None


def _aufgabe(stufe, art, titel, kunde=None, coach=None, frist=None, link=None, warum=None):
    return {"stufe": stufe, "art": art, "titel": titel,
            "kunde": (kunde or {}).get("name") if isinstance(kunde, dict) else kunde,
            "kunde_id": (kunde or {}).get("id") if isinstance(kunde, dict) else None,
            "coach": coach or LEITUNG, "frist": frist, "link": link, "warum": warum}


# --------------------------------------------------------------------- Regeln
def _merker():
    """Der Zwischenspeicher dieser einen Anfrage – oder None, wenn gerade keine läuft.

    Hängt an `flask.g` und stirbt mit der Anfrage. Ein Merker im Modul selbst würde in der
    Kommandozeile und im nächtlichen Agentenlauf stundenlang alt werden und irgendwann
    Aufgaben zu Menschen zeigen, die längst nicht mehr in Maßnahme sind."""
    if g is None or not has_app_context():
        return None
    if not hasattr(g, "aufgaben_merker"):
        g.aufgaben_merker = {}
    return g.aufgaben_merker


def laufende_massnahmen():
    """Wer gerade in einer Maßnahme steht, mit allem, was an ihm hängt.

    Öffentlich, weil der Einstieg dieselbe Liste braucht: eine zweite Abfrage dort würde
    irgendwann anders zählen als die Regeln hier.

    **Einmal je Anfrage.** Neun Regeln fragen dieselbe Liste; ein Aufruf von /taskforce
    rechnete sie dadurch neunmal – gemessen 23 von 54 Millisekunden Seitenzeit. Am
    Ergebnis ändert der Merker nichts: dieselbe Anfrage, dieselbe Sekunde, dieselbe
    Antwort. Außerhalb einer Anfrage wird wie bisher jedes Mal frisch gerechnet."""
    merker = _merker()
    if merker is not None and "laufende" in merker:
        return merker["laufende"]
    zeilen = db.hole(
        "SELECT k.id, k.name, k.status_code, k.massnahme, m.name AS coach,"
        "       (SELECT MAX(g.bis) FROM gutschein_zeile g WHERE g.kunde_id=k.id) AS endet,"
        "       (SELECT COUNT(*) FROM termin t WHERE t.kunde_id=k.id) AS termine,"
        "       (SELECT COUNT(*) FROM lebenslauf l WHERE l.kunde_id=k.id) AS lebenslaeufe,"
        # **Gezählt wird JEDES Profil, auch ein pausiertes** – dieselbe Bedingung wie in
        # `sammelanlage.vorschlaege()`, `sammelanlage.anlegen()`, der Kundenliste und
        # dem Coaches-Überblick (`app.py`, `coaches.py`), dem Hinweis auf der Tafel und
        # `taskforce.kunden_bilanz`. Das ist die eine Zählweise des Hauses – sie stand
        # bis zum 22.09.2026 an fünf dieser Stellen NICHT, während dieser Kommentar das
        # Gegenteil behauptete. Sie richtet sich nach dem, was der Knopf tun kann:
        # `anlegen()` legt für jemanden mit pausiertem Profil kein zweites an. Stand hier
        # `aktiv=1`, zeigte der Einstieg „19 ohne Jobprofil", und hinter dem Knopf standen
        # 18 Zeilen – gemessen am 21.09.2026, ein Profil auf `aktiv=0` gesetzt.
        #
        # Pausiert heisst nicht „nichts da", sondern „steht still". Wer still steht,
        # steht auf dem Arbeitsplatz seiner Art in der unteren Liste, mit der Plakette
        # „pausiert – sucht nicht". Die Antwort darauf ist einschalten, nicht ein
        # zweites Profil.
        "       (SELECT COUNT(*) FROM tf_profil p WHERE p.kunde_id=k.id) AS profile,"
        # Die Jobprofile zusätzlich einzeln: Arbeitssuche und Wohnungssuche sind zwei
        # Arbeiten. Wer ein Wohnprofil hat, hat ein Profil – für seine Arbeitssuche sucht
        # der Agent trotzdem nichts.
        "       (SELECT COUNT(*) FROM tf_profil p WHERE p.kunde_id=k.id"
        "          AND p.art='job') AS jobprofile,"
        "       (SELECT COUNT(*) FROM kunde_profil p WHERE p.kunde_id=k.id"
        "          AND p.kurzprofil IS NOT NULL AND p.kurzprofil<>'') AS kurzprofil,"
        "       k.stadt"
        "  FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE k.standort=? AND k.status_code IN ('H','I') ORDER BY k.name",
        (db.STANDORT_STANDARD,))
    if merker is not None:
        merker["laufende"] = zeilen
    return zeilen


def massnahme_ohne_termine():
    """Der Gutschein läuft, aber es steht kein Termin im OS. Ohne Nachweis keine Abrechnung –
    das ist die teuerste Lücke, die ein Träger haben kann."""
    return [_aufgabe("rot", "Nachweis", "Termine dokumentieren – der Gutschein läuft ohne Nachweis",
                     k, k["coach"], k["endet"], f"/kunde/{k['id']}",
                     "Ohne dokumentierte Termine lässt sich die Maßnahme nicht abrechnen.")
            for k in laufende_massnahmen() if not k["termine"]]


def frist_laeuft_ab():
    """Maßnahme endet in den nächsten zwei Wochen. Danach ist nichts mehr nachzuholen."""
    aufgaben = []
    for k in laufende_massnahmen():
        tage = _tage_bis(k["endet"])
        if tage is None or tage > FRIST_WARNUNG:
            continue
        fehlt = []
        if not k["termine"]:
            fehlt.append("Termine")
        if not k["lebenslaeufe"]:
            fehlt.append("Lebenslauf")
        wort = "endet in" if tage >= 0 else "ist beendet seit"
        aufgaben.append(_aufgabe(
            "rot", "Frist",
            f"Maßnahme {wort} {abs(tage)} Tagen" + (f" – es fehlt: {', '.join(fehlt)}" if fehlt else
                                                    " – Abschluss vorbereiten"),
            k, k["coach"], k["endet"], f"/kunde/{k['id']}",
            "Nach dem Ende lässt sich die Dokumentation nicht mehr nachholen."))
    return aufgaben


def offene_rechnungen():
    """Maßnahme gelaufen, Unterrichtseinheiten gerechnet, aber keine Rechnung. Offenes Geld."""
    zeilen = db.hole(
        "SELECT g.id, g.improfy_id, g.vorname, g.nachname, g.bis, g.ue_gerechnet, g.betrag,"
        "       g.kunde_id, m.name AS coach"
        "  FROM gutschein_zeile g LEFT JOIN kunde k ON k.id=g.kunde_id"
        "  LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE (g.re_nummer IS NULL OR g.re_nummer='') AND g.bis<>'' AND g.bis < ?"
        "   AND g.ue_gerechnet IS NOT NULL AND g.ue_gerechnet > 0"
        " ORDER BY g.bis", (heute().isoformat(),))
    return [_aufgabe("rot", "Abrechnung",
                     f"Rechnung fehlt – {z['ue_gerechnet']} UE gerechnet"
                     + (f", {z['betrag']} €" if z["betrag"] else ""),
                     {"id": z["kunde_id"], "name": f"{z['vorname'] or ''} {z['nachname'] or ''}".strip()
                      or z["improfy_id"]},
                     z["coach"], z["bis"],
                     f"/kunde/{z['kunde_id']}" if z["kunde_id"] else "/kunden",
                     "Geleistete Stunden ohne Rechnung sind Geld, das liegen bleibt.")
            for z in zeilen]


def ohne_lebenslauf():
    """Ohne Lebenslauf kann die Taskforce nicht bewerben – die Maßnahme läuft leer."""
    return [_aufgabe("gelb", "Unterlagen", "Lebenslauf fehlt – bewerben ist nicht möglich",
                     k, k["coach"], k["endet"], f"/kunde/{k['id']}/lebenslauf",
                     "Solange kein Lebenslauf da ist, kann niemand für diese Person bewerben.")
            for k in laufende_massnahmen() if not k["lebenslaeufe"]]


def ohne_taskforce():
    """Laufende Maßnahme, aber kein Such-Profil. Der Agent kann nichts finden."""
    return [_aufgabe("gelb", "Taskforce", "Kein Such-Profil – der Agent sucht für diese Person nichts",
                     k, k["coach"], k["endet"], f"/taskforce/kunde/{k['id']}",
                     "Vermittlung ist das Ziel der Maßnahme; ohne Profil passiert dabei nichts.")
            for k in laufende_massnahmen() if not k["profile"]]


def ohne_kurzprofil():
    """Ohne Kurzprofil kann der Agent Angebote nicht gegen die Person abgleichen."""
    return [_aufgabe("grau", "Taskforce", "Kurzprofil fehlt – Angebote lassen sich nicht abgleichen",
                     k, k["coach"], k["endet"], f"/taskforce/kunde/{k['id']}",
                     "Der Abgleich passt/fehlt braucht Stichworte zur Person.")
            for k in laufende_massnahmen() if k["profile"] and not k["kurzprofil"]]


def ohne_wohnort():
    """Ohne Wohnort sucht der Agent am Standort statt dort, wo die Person wohnt."""
    return [_aufgabe("grau", "Stammdaten", "Wohnort fehlt – gesucht wird ersatzweise in Köln",
                     k, k["coach"], None, f"/kunde/{k['id']}",
                     "Job- und Wohnungssuche brauchen den Ort der Person.")
            for k in laufende_massnahmen() if not k["stadt"]]


def ohne_coach():
    return [_aufgabe("gelb", "Zuordnung", "Kein Coach zugeordnet",
                     k, None, None, f"/kunde/{k['id']}",
                     "Eine Akte ohne Coach bearbeitet niemand.")
            for k in db.hole(
                "SELECT id, name FROM kunde WHERE standort=? AND coach_id IS NULL"
                "  AND status_code IN ('H','I')", (db.STANDORT_STANDARD,))]


def wiedervorlage():
    """Bewerbung raus, seit über einer Woche keine Antwort. Die meisten Zusagen kommen
    erst nach dem zweiten Kontakt."""
    grenze = (datetime.datetime.now() - datetime.timedelta(days=WIEDERVORLAGE)).isoformat()
    zeilen = db.hole(
        "SELECT a.id, a.titel, a.anbieter, a.status_am, a.bearbeiter, p.kunde_id,"
        "       k.name AS kunde, m.name AS coach"
        "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
        "  JOIN kunde k ON k.id=p.kunde_id LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE a.status='angeschrieben' AND a.status_am < ? AND p.standort=?"
        " ORDER BY a.status_am", (grenze, db.STANDORT_STANDARD))
    return [_aufgabe("gelb", "Nachfassen",
                     f"Seit {_tage_bis(z['status_am'][:10]) and abs(_tage_bis(z['status_am'][:10])) or '?'} "
                     f"Tagen keine Antwort: {(z['anbieter'] or z['titel'] or '')[:60]}",
                     {"id": z["kunde_id"], "name": z["kunde"]}, z["bearbeiter"] or z["coach"],
                     None, f"/taskforce/kunde/{z['kunde_id']}",
                     "Die meisten Zusagen kommen erst nach dem zweiten Kontakt.")
            for z in zeilen]


def gute_angebote_liegen():
    """Hoch bewertete Treffer, die niemand angefasst hat. Genau dafür läuft der Agent."""
    zeilen = db.hole(
        "SELECT k.id AS kunde_id, k.name AS kunde, m.name AS coach, COUNT(*) AS n,"
        "       MAX(a.score) AS bester"
        "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
        "  JOIN kunde k ON k.id=p.kunde_id LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE a.status='neu' AND COALESCE(a.score,0) >= 6 AND p.aktiv=1 AND p.standort=?"
        " GROUP BY k.id ORDER BY n DESC", (db.STANDORT_STANDARD,))
    return [_aufgabe("gelb", "Taskforce",
                     f"{z['n']} gut passende Angebote warten (bestes {int(z['bester'] or 0)} von 10)",
                     {"id": z["kunde_id"], "name": z["kunde"]}, z["coach"], None,
                     # Ausdrücklich die Tafel: /taskforce zeigt ohne Regler den Einstieg,
                     # und „alle Filter zurücksetzen" führte von dort wieder dorthin.
                     f"/taskforce/tafel?kunde={z['kunde_id']}&score=6",
                     "Gefundene Angebote nützen erst etwas, wenn jemand sie anschreibt.")
            for z in zeilen]


REGELN = [massnahme_ohne_termine, frist_laeuft_ab, offene_rechnungen, ohne_coach,
          ohne_lebenslauf, ohne_taskforce, wiedervorlage, gute_angebote_liegen,
          ohne_kurzprofil, ohne_wohnort]

STUFEN = {"rot": 0, "gelb": 1, "grau": 2}


def alle(coach=None, stufe=None, art=None):
    """Alle Aufgaben, dringendste zuerst."""
    liste = []
    for regel in REGELN:
        try:
            liste += regel()
        except Exception as e:                    # eine kaputte Regel darf nicht alles kippen
            liste.append(_aufgabe("grau", "Fehler", f"Regel {regel.__name__} läuft nicht: {e}"))
    if coach:
        liste = [a for a in liste if (a["coach"] or LEITUNG) == coach]
    if stufe:
        liste = [a for a in liste if a["stufe"] == stufe]
    if art:
        liste = [a for a in liste if a["art"] == art]
    liste.sort(key=lambda a: (STUFEN.get(a["stufe"], 9), a["frist"] or "9999",
                              a["coach"] or "", a["kunde"] or ""))
    return liste


def je_coach():
    """Die Aufgabenliste je Mitarbeiter – die Zahl, nach der man morgens steuert."""
    zusammen = {}
    for a in alle():
        eintrag = zusammen.setdefault(a["coach"] or LEITUNG,
                                      {"coach": a["coach"] or LEITUNG, "rot": 0, "gelb": 0,
                                       "grau": 0, "gesamt": 0, "naechste_frist": None})
        eintrag[a["stufe"]] = eintrag.get(a["stufe"], 0) + 1
        eintrag["gesamt"] += 1
        if a["frist"] and (not eintrag["naechste_frist"] or a["frist"] < eintrag["naechste_frist"]):
            eintrag["naechste_frist"] = a["frist"]
    return sorted(zusammen.values(), key=lambda e: (-e["rot"], -e["gesamt"]))


def zaehler():
    liste = alle()
    return {"gesamt": len(liste),
            "rot": sum(1 for a in liste if a["stufe"] == "rot"),
            "gelb": sum(1 for a in liste if a["stufe"] == "gelb"),
            "grau": sum(1 for a in liste if a["stufe"] == "grau"),
            "arten": sorted({a["art"] for a in liste})}
