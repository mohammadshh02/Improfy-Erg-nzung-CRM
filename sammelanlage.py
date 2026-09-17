# -*- coding: utf-8 -*-
"""Suchprofile für alle laufenden Kunden in einem Schritt anlegen.

**Das Problem, das dieses Modul löst.** Die Taskforce ist gebaut, findet Angebote und
gleicht sie ab – aber sie tut das nur für Kunden, die ein Suchprofil haben. Beim ersten
Blick in den Echtbestand hatten **19 von 19 laufenden Maßnahmen kein Profil**. Eine
Maschine, die niemand einschaltet, ist wertlos; und ein Profil von Hand anzulegen kostet
je Kunde ein paar Minuten, die niemand hat.

Hier entsteht deshalb eine Vorschlagsliste: je Kunde ein vorgeschlagener Suchbegriff und
ein Ort, beides aus dem, was das System ohnehin weiß. Wer damit einverstanden ist, hakt
an und legt alle auf einmal an. Wer nicht, ändert die Zeile vorher.

**Warum ein Vorschlag und kein Automatismus.** Der Suchbegriff entscheidet, was der Agent
wochenlang sucht. Ein falsch geratener Beruf produziert hunderte unpassende Treffer, und
die Coaches gewöhnen sich an, die Liste zu ignorieren – der teuerste Fehler, den man hier
machen kann. Deshalb: vorschlagen, zeigen, bestätigen lassen.
"""
import re

import datenbank as db
import taskforce as tf

# Berufsbezeichnungen enden im Deutschen ziemlich zuverlässig auf diese Silben.
BERUFSENDUNG = re.compile(
    r"\b([A-ZÄÖÜ][a-zäöüß]+(?:er|erin|ist|istin|kraft|helfer|helferin|techniker|technikerin|"
    r"mann|frau|assistent|assistentin|pfleger|pflegerin|fahrer|fahrerin|köchin|koch|"
    r"leiter|leiterin|berater|beraterin|arbeiter|arbeiterin|monteur|mechaniker))\b")
# „Ausbildung als Kaufmann für Büromanagement", „tätig als Lagerist"
NACH_ALS = re.compile(r"(?:als|Ausbildung|Beruf|Tätigkeit als)\s+([A-ZÄÖÜ][\wäöüß]+"
                      r"(?:\s+(?:für|im|in|der|des)\s+[A-ZÄÖÜ][\wäöüß]+){0,2})")
# Wörter, die wie ein Beruf aussehen, aber keiner sind
KEIN_BERUF = {"arbeitgeber", "arbeitserlaubnis", "deutscher", "bachelor", "master",
              "weiter", "jobcenter", "teilnehmer", "teilnehmerin", "bewerber", "kunde",
              "mitarbeiter", "januar", "februar", "oktober", "november", "dezember"}


def beruf_vorschlag(text):
    """Aus dem Kurzprofil einen Suchbegriff raten. Gibt "" zurück, wenn nichts sicher ist."""
    if not text:
        return ""
    erste = re.split(r"[.;\n]", text.strip())[0][:200]
    for muster in (NACH_ALS, BERUFSENDUNG):
        for treffer in muster.finditer(erste) or []:
            wort = treffer.group(1).strip()
            if wort.split()[0].lower() in KEIN_BERUF or len(wort) < 5:
                continue
            return wort
    return ""


def vorschlaege(standort=db.STANDORT_STANDARD, art="job"):
    """Alle laufenden Kunden ohne Profil dieser Art, mit Vorschlag für Begriff und Ort."""
    zeilen = db.hole(
        "SELECT k.id, k.name, k.stadt, k.plz, k.massnahme, m.name AS coach,"
        "       p.kurzprofil, p.cv_text,"
        "       (SELECT COUNT(*) FROM lebenslauf l WHERE l.kunde_id=k.id) AS lebenslaeufe"
        "  FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        "  LEFT JOIN kunde_profil p ON p.kunde_id=k.id"
        " WHERE k.standort=? AND k.status_code IN ('H','I')"
        "   AND NOT EXISTS (SELECT 1 FROM tf_profil t WHERE t.kunde_id=k.id"
        "                     AND t.art=? AND t.aktiv=1)"
        " ORDER BY k.name", (standort, art))
    for z in zeilen:
        z["begriff"] = beruf_vorschlag(z["kurzprofil"]) or beruf_vorschlag(z["cv_text"])
        z["ort"] = (z["stadt"] or "").strip() or "Köln"
        z["ort_geraten"] = not (z["stadt"] or "").strip()
        z["bereit"] = bool(z["begriff"]) if art == "job" else True
    return zeilen


def anlegen(auswahl, art="job", umkreis=25, standort=db.STANDORT_STANDARD):
    """Profile anlegen. `auswahl` ist eine Liste von (kunde_id, begriff, ort).

    Gibt (angelegt, uebersprungen) zurück. Ein Lauf wird bewusst **nicht** ausgelöst –
    zwanzig Profile gleichzeitig über zehn Portale zu jagen, dauert eine Viertelstunde
    und gehört in den nächtlichen Lauf, nicht in einen Klick."""
    angelegt, uebersprungen = [], []
    for kunde_id, begriff, ort in auswahl:
        begriff = (begriff or "").strip()
        ort = (ort or "").strip() or "Köln"
        if art == "job" and not begriff:
            uebersprungen.append((kunde_id, "kein Suchbegriff"))
            continue
        vorhanden = db.wert(
            "SELECT COUNT(*) FROM tf_profil WHERE kunde_id=? AND art=? AND aktiv=1",
            (kunde_id, art))
        if vorhanden:
            uebersprungen.append((kunde_id, "hat schon ein Profil"))
            continue
        name = db.wert("SELECT name FROM kunde WHERE id=?", (kunde_id,), "") or ""
        daten = {"kunde_id": kunde_id, "art": art, "formular": "1", "aktiv": "1",
                 "ort": ort, "umkreis_km": str(umkreis), "zeitarbeit": "1",
                 "titel": begriff or f"Wohnungssuche {name.split()[-1] if name else ''}".strip()}
        if art == "job":
            daten["suchbegriffe"] = begriff
        else:
            daten["suchauftrag"] = f"TF-{name.split()[-1]}" if name else ""
        try:
            pid = tf.profil_speichern(daten)
            angelegt.append((kunde_id, pid, begriff or daten["titel"]))
        except Exception as e:
            uebersprungen.append((kunde_id, str(e)[:80]))
    return angelegt, uebersprungen


def aus_formular(form, art="job"):
    """Die angehakten Zeilen des Formulars in die Form bringen, die `anlegen` erwartet."""
    auswahl = []
    for kid in form.getlist("kunde"):
        try:
            kid_zahl = int(kid)
        except (TypeError, ValueError):
            continue
        auswahl.append((kid_zahl,
                        (form.get(f"begriff_{kid}") or "").strip(),
                        (form.get(f"ort_{kid}") or "").strip()))
    return auswahl
