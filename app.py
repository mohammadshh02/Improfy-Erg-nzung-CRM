# -*- coding: utf-8 -*-
"""Improfy-OS – Dashboard für den Standort Köln.

Start:  python -X utf8 app.py     →  http://localhost:8101

Anmeldung: ein Passwort aus IMPROFY_OS_PASSWORT (oder .env). Das OS zeigt
personenbezogene Kundendaten – es läuft nie ohne Anmeldung.
"""
import datetime
import functools
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.parse

from flask import (Flask, abort, redirect, render_template, request, send_file,
                   session, url_for)

import datenbank as db
import aussenanbindung as AA
import cv_lesen
import dokument_lesen
import fotos
import nachrichten
import crm_karte
import cv_pdf
import cv_sammlung
import coaches
import lebenslauf_bauen as LB
import aktivitaet
import aufgaben
import betrieb
import einstieg
import konten
import sammelanlage
import taskforce as tf
import trichter
from quellen import lebenslauf as L

HIER = os.path.dirname(os.path.abspath(__file__))

# .env laden, damit Passwort und Tokens nicht im Code stehen.
import umgebung  # noqa: E402
umgebung.laden()

app = Flask(__name__)
app.secret_key = os.environ.get("IMPROFY_OS_SECRET") or secrets.token_hex(32)
app.permanent_session_lifetime = datetime.timedelta(hours=12)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "") == "1",
)

PASSWORT = os.environ.get("IMPROFY_OS_PASSWORT", "")


def angemeldet():
    """Angemeldet ist, wer ein persönliches Konto hat – oder, solange es noch keine
    Konten gibt, wer das gemeinsame Passwort kennt. Ohne beides ist das OS offen und
    lauscht dann nur auf 127.0.0.1, damit niemand im Netz an die Kundendaten kommt."""
    if session.get("benutzer_id"):
        return True
    if konten.persoenlicher_betrieb():
        return False                      # ab dem ersten Konto zählt nur noch persönlich
    return not PASSWORT or session.get("os_angemeldet") is True


def wer():
    """Die angemeldete Person. Im Übergangsbetrieb ohne Konten steht hier der Standort."""
    return {"id": session.get("benutzer_id"),
            "name": session.get("benutzer_name") or "gemeinsamer Zugang",
            "rolle": session.get("benutzer_rolle") or "mitarbeiter",
            "persoenlich": bool(session.get("benutzer_id"))}


def notieren(aktion, bereich=None, objekt_id=None, beschreibung=None,
             vorher=None, nachher=None):
    """Jede Änderung ins Protokoll. Schlägt das fehl, darf es die Aktion nicht kippen."""
    p = wer()
    try:
        konten.notieren(p["name"], p["rolle"], aktion, bereich, objekt_id,
                        beschreibung, vorher, nachher)
    except Exception:
        pass


def nur_lesen():
    return wer()["rolle"] == "lesen"


@app.before_request
def _schutz():
    if request.endpoint in ("login", "static") or angemeldet():
        return None
    # Schnittstellen dürfen sich statt mit der Sitzung mit einem Schlüssel ausweisen –
    # ein anderes System hat keine Anmeldemaske. Gilt nur lesend (siehe api.py).
    if request.path.startswith("/api/") or request.path == "/api":
        import api as _api
        if _api.token_gueltig():
            return None
        from flask import jsonify
        return jsonify({"fehler": "Zugang verweigert",
                        "hinweis": "OS_API_TOKEN als 'Authorization: Bearer <token>' mitgeben."}), 401
    return redirect(url_for("login", weiter=request.path))


@app.before_request
def _schreibschutz():
    """Wer nur lesen darf, darf auch nur lesen – unabhängig davon, welchen Knopf die
    Seite anzeigt. Die Prüfung gehört an die Tür, nicht ins Formular."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.endpoint in ("login", "logout") or not session.get("benutzer_id"):
        return None
    if nur_lesen():
        return render_template("fehler.html", titel="Nur Leserecht",
                               text="Dieses Konto darf nichts ändern. Die Leitung kann "
                                    "die Rolle unter „Konten\u201c anpassen."), 403
    return None


@app.after_request
def _kein_cache(antwort):
    antwort.headers["Cache-Control"] = "no-store"
    return antwort


@app.route("/login", methods=["GET", "POST"])
def login():
    fehler = ""
    persoenlich = konten.persoenlicher_betrieb()
    if not persoenlich and not PASSWORT:
        return redirect(url_for("uebersicht"))
    if request.method == "POST":
        if persoenlich:
            b = konten.pruefen(request.form.get("anmeldename", ""),
                               request.form.get("passwort", ""))
            if b:
                session.permanent = True
                session.update(benutzer_id=b["id"], benutzer_name=b["name"],
                               benutzer_rolle=b["rolle"])
                konten.notieren(b["name"], b["rolle"], "angemeldet", "konten", b["id"],
                                beschreibung=aktivitaet.geraet_kurz(request.user_agent.string),
                                nachher={"ip": request.remote_addr,
                                         "geraet": request.user_agent.string[:200]})
                _spur_ergaenzen(request.remote_addr, request.user_agent.string[:200])
                return redirect(request.args.get("weiter") or url_for("uebersicht"))
            fehler = "Anmeldename oder Passwort stimmt nicht."
        else:
            eingabe = request.form.get("passwort", "")
            if hmac.compare_digest(
                    hashlib.sha256(eingabe.encode()).hexdigest(),
                    hashlib.sha256(PASSWORT.encode()).hexdigest()):
                session.permanent = True
                session["os_angemeldet"] = True
                return redirect(request.args.get("weiter") or url_for("uebersicht"))
            fehler = "Passwort stimmt nicht."
    return render_template("login.html", fehler=fehler, persoenlich=persoenlich)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------- Konten und Protokoll

@app.route("/konten")
def konten_seite():
    p = wer()
    if konten.persoenlicher_betrieb() and not konten.darf_verwalten(p["rolle"]):
        return render_template("fehler.html", titel="Kein Zugriff",
                               text="Konten verwaltet die Leitung."), 403
    return render_template("konten.html", benutzer=konten.liste(), rollen=konten.ROLLEN,
                           ich=p, leute=db.hole(
                               "SELECT id, name FROM mitarbeiter WHERE standort=? ORDER BY name",
                               (db.STANDORT_STANDARD,)),
                           aktive=konten.wer_war_aktiv(),
                           fehler=request.args.get("fehler"),
                           meldung=request.args.get("meldung"))


@app.route("/konten/anlegen", methods=["POST"])
def konten_anlegen():
    p = wer()
    if konten.persoenlicher_betrieb() and not konten.darf_verwalten(p["rolle"]):
        abort(403)
    try:
        name = konten.anlegen(request.form.get("anmeldename"), request.form.get("name"),
                              request.form.get("passwort"), request.form.get("rolle"),
                              request.form.get("mitarbeiter_id", type=int))
    except ValueError as e:
        return redirect(url_for("konten_seite", fehler=str(e)))
    notieren("Konto angelegt", "konten", name, f"Rolle {request.form.get('rolle')}")
    return redirect(url_for("konten_seite", meldung=f"Konto {name} angelegt."))


@app.route("/konten/<int:bid>/aendern", methods=["POST"])
def konten_aendern(bid):
    p = wer()
    if konten.persoenlicher_betrieb() and not konten.darf_verwalten(p["rolle"]):
        abort(403)
    was = request.form.get("was")
    try:
        if was == "passwort":
            konten.passwort_setzen(bid, request.form.get("passwort"))
            notieren("Passwort gesetzt", "konten", bid)
        elif was == "rolle":
            konten.rolle_setzen(bid, request.form.get("rolle"))
            notieren("Rolle geändert", "konten", bid, request.form.get("rolle"))
        elif was in ("sperren", "freigeben"):
            if was == "sperren" and bid == p["id"]:
                return redirect(url_for("konten_seite",
                                        fehler="Das eigene Konto lässt sich nicht sperren."))
            konten.aktiv_setzen(bid, was == "freigeben")
            notieren("Konto " + was, "konten", bid)
    except ValueError as e:
        return redirect(url_for("konten_seite", fehler=str(e)))
    return redirect(url_for("konten_seite", meldung="Gespeichert."))


@app.route("/api/kunden-suche")
def api_kunden_suche():
    """Die Vorschlaege, waehrend getippt wird – fuer die Kopfsuche und die Regler."""
    from flask import jsonify
    return jsonify(kunden=tf.kunden_suchen(request.args.get("q") or ""))


@app.route("/suche")
def suche_seite():
    """Ein Feld für alles.

    Ein Coach am Telefon will einen Namen tippen und die Akte haben – egal, auf welcher
    Seite er gerade steht. Vorher filterte jede Liste für sich, und man musste erst
    wissen, wo man suchen muss."""
    q = (request.args.get("q") or "").strip()
    gruppen = []
    if len(q) >= 2:
        muster = f"%{q}%"

        def gruppe(titel, sql, args, bauen, grenze=12):
            zeilen = db.hole(sql + f" LIMIT {grenze + 1}", args)
            mehr = len(zeilen) > grenze
            return {"titel": titel, "treffer": [bauen(z) for z in zeilen[:grenze]],
                    "mehr": (grenze + 1) if mehr else None}

        gruppen.append(gruppe(
            "Kunden",
            "SELECT k.id, k.name, k.status_code, k.telefon, k.email, k.kundennummer,"
            "       k.stadt, m.name AS coach FROM kunde k"
            "  LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
            " WHERE k.name LIKE ? OR k.telefon LIKE ? OR k.email LIKE ?"
            "    OR k.kundennummer LIKE ? OR k.ort_jc LIKE ? OR k.stadt LIKE ?"
            " ORDER BY k.name", [muster] * 6,
            lambda z: {"titel": z["name"], "link": url_for("kunde_detail", kid=z["id"]),
                       "unter": " · ".join(x for x in (z["telefon"], z["email"],
                                                       z["kundennummer"]) if x),
                       "rechts": " · ".join(x for x in (z["coach"], z["stadt"],
                                                        z["status_code"]) if x)}))
        gruppen.append(gruppe(
            "Mitarbeiter",
            "SELECT id, name, rolle, email FROM mitarbeiter WHERE name LIKE ? OR email LIKE ?"
            " ORDER BY name", [muster] * 2,
            lambda z: {"titel": z["name"], "link": url_for("mitarbeiter_detail", mid=z["id"]),
                       "unter": z["email"] or "", "rechts": z["rolle"] or ""}, 6))
        gruppen.append(gruppe(
            "Angebote der Taskforce",
            "SELECT a.id, a.titel, a.anbieter, a.ort, a.status, a.quelle, k.id AS kunde_id,"
            "       k.name AS kunde FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
            "  JOIN kunde k ON k.id=p.kunde_id"
            " WHERE a.titel LIKE ? OR a.anbieter LIKE ? OR a.ort LIKE ?"
            " ORDER BY COALESCE(a.score,0) DESC", [muster] * 3,
            lambda z: {"titel": z["titel"] or "(ohne Titel)",
                       "link": url_for("taskforce_kunde", kid=z["kunde_id"]),
                       "unter": " · ".join(x for x in (z["anbieter"], z["ort"], z["kunde"]) if x),
                       "rechts": z["status"]}))
        gruppen.append(gruppe(
            "Lebensläufe",
            "SELECT l.id, l.name, l.url, l.geaendert, k.id AS kunde_id, k.name AS kunde"
            "  FROM lebenslauf l LEFT JOIN kunde k ON k.id=l.kunde_id"
            " WHERE l.name LIKE ? ORDER BY l.geaendert DESC", [muster],
            lambda z: {"titel": z["name"],
                       "link": url_for("kunde_detail", kid=z["kunde_id"]) if z["kunde_id"]
                       else url_for("lebenslauf_uebersicht"),
                       "unter": z["kunde"] or "keinem Kunden zugeordnet",
                       "rechts": (z["geaendert"] or "")[:10]}, 8))
        gruppen.append(gruppe(
            "Leads",
            "SELECT id, name, quelle, status, eingang FROM lead WHERE name LIKE ?"
            " ORDER BY eingang DESC", [muster],
            lambda z: {"titel": z["name"] or "(ohne Namen)", "link": url_for("leads"),
                       "unter": " · ".join(x for x in (z["quelle"], z["status"]) if x),
                       "rechts": (z["eingang"] or "")[:10]}, 8))
    return render_template("suche.html", q=q, gruppen=gruppen,
                           gesamt=sum(len(g["treffer"]) for g in gruppen))


@app.route("/nachrichten")
def nachrichten_seite():
    """Was für einen Kunden beworben wurde, geht ihm per WhatsApp zu – nach Freigabe."""
    return render_template(
        "nachrichten.html", liste=nachrichten.vorschlaege(), z=nachrichten.zaehler(),
        verlauf=nachrichten.verlauf(limit=30), kommo_bereit=nachrichten.kommo_bereit(),
        zentrale=nachrichten.zentrale(),
        zentrale_vorschlaege=nachrichten.ZENTRALE_VORSCHLAEGE,
        kommo_hinweis=nachrichten.kommo_hinweis(),
        meldung=request.args.get("meldung"), fehler=request.args.get("fehler"))


@app.route("/nachrichten/vermerken", methods=["POST"])
def nachrichten_vermerken():
    """Festhalten, dass eine Nachricht rausging. Verschickt wird sie in WhatsApp selbst."""
    kid = request.form.get("kunde", type=int)
    text = request.form.get("text") or ""
    ids = [i for i in (request.form.get("angebote") or "").split(",") if i.strip()]
    if not kid or not text.strip():
        return redirect(url_for("nachrichten_seite", fehler="Kein Kunde oder kein Text."))
    telefon = db.wert("SELECT telefon FROM kunde WHERE id=?", (kid,), "")
    nachrichten.vermerken(kid, text, ids, "wa-link", _bearbeiter(), telefon)
    notieren("Kundennachricht verschickt", "nachrichten", kid, f"{len(ids)} Angebote")
    return redirect(url_for("nachrichten_seite",
                            meldung=f"Vermerkt: {len(ids)} Angebote mitgeteilt."))


@app.route("/trichter")
def trichter_seite():
    """Vertriebstrichter für die Standortleitung – vom WhatsApp-Lead bis zum Gutschein."""
    from quellen import kommo
    return render_template(
        "trichter.html", z=trichter.zaehler(), stufen=trichter.stufen(),
        haengen=trichter.antraege(offen_ab_tagen=21)[:40], leiter=trichter.je_leiter(),
        monate=trichter.je_monat(), laufzeit=trichter.durchlaufzeit(),
        quellen=trichter.leadquellen(), leadstufen=trichter.leadstufen(),
        kommo_bereit=kommo.Kommo().api_bereit(),
        meldung=request.args.get("meldung"), fehler=request.args.get("fehler"))


@app.route("/trichter/kommo", methods=["POST"])
def trichter_kommo():
    """Anfragen aus Kommo holen. Liest nur – nach Kommo wird nie geschrieben."""
    from quellen import kommo
    try:
        ergebnis = kommo.Kommo().einlesen()
        notieren("Kommo eingelesen", "vertrieb", None, str(ergebnis)[:160])
        return redirect(url_for("trichter_seite", meldung=f"Aus Kommo geholt: {ergebnis}"))
    except Exception as e:
        return redirect(url_for("trichter_seite", fehler=f"Kommo antwortet nicht: {e}"))


@app.route("/aufgaben")
def aufgaben_seite():
    """Aus Beobachtungen werden Aufträge – mit Namen, Frist und Link zum Erledigen."""
    f = {"coach": request.args.get("coach") or None,
         "stufe": request.args.get("stufe") or None,
         "art": request.args.get("art") or None}
    return render_template("aufgaben.html", liste=aufgaben.alle(**f), z=aufgaben.zaehler(),
                           je_coach=aufgaben.je_coach(), filter=f)


@app.route("/betrieb")
def betrieb_seite():
    """Sicherung und nächtlicher Lauf – was von selbst passiert, muss nachprüfbar sein."""
    return render_template("betrieb.html", z=betrieb.zustand(), laeufe=betrieb.laeufe(20),
                           staende_max=betrieb.STAENDE,
                           meldung=request.args.get("meldung"),
                           fehler=request.args.get("fehler"))


@app.route("/betrieb/sichern", methods=["POST"])
def betrieb_sichern():
    try:
        pfad, groesse = betrieb.sichern()
        betrieb._notieren("sicherung", f"{os.path.basename(pfad)} · {groesse // 1024} kB (von Hand)")
        notieren("Sicherung erstellt", "betrieb", os.path.basename(pfad))
        return redirect(url_for("betrieb_seite",
                                meldung=f"Gesichert: {os.path.basename(pfad)}, {groesse // 1024} kB."))
    except Exception as e:
        return redirect(url_for("betrieb_seite", fehler=f"Sicherung fehlgeschlagen: {e}"))


def _spur_ergaenzen(ip, geraet):
    """Adresse und Gerät an die zuletzt geschriebene Protokollzeile hängen."""
    try:
        with db.offen() as con:
            con.execute("UPDATE protokoll SET ip=?, geraet=? WHERE id=(SELECT MAX(id) FROM protokoll)",
                        (ip, geraet))
    except Exception:
        pass


@app.route("/aktivitaet")
def aktivitaet_seite():
    """Wer war wann angemeldet und was hat er getan – je Person auswählbar."""
    wer = request.args.get("wer") or None
    tage = request.args.get("tage", 30, type=int)
    return render_template(
        "aktivitaet.html", gewaehlt=wer, tage=tage, z=aktivitaet.zaehler(tage),
        alle_leute=aktivitaet.leute(tage), bereiche=aktivitaet.bereiche(tage=tage),
        spur=aktivitaet.tagesspur(wer, tage) if wer else [],
        logins=aktivitaet.anmeldungen(wer, tage) if wer else [],
        schritte=aktivitaet.verlauf(wer, tage) if wer else [],
        kunden=aktivitaet.kunden_beruehrt(wer, tage) if wer else [])


@app.route("/coaches")
def coaches_seite():
    """Der Ueberblick ueber jeden Coach - Betreuung und Taetigkeit nebeneinander.

    Loest die alte Seite "Mitarbeiter-Spur" ab. Die Spur allein war eine Zahlenreihe
    ohne Bezug: 40 Aktionen sagen nichts, wenn man nicht weiss, wie viele Menschen
    jemand betreut. Beides zusammen ist die Frage, die die Standortleitung stellt."""
    coach = request.args.get("coach", type=int)
    tage = request.args.get("tage", 30, type=int)
    if tage not in (7, 14, 30, 90):
        tage = 30
    gewaehlt = coaches.einer(coach) if coach else None
    name = gewaehlt["coach"] if gewaehlt else None
    return render_template(
        "coaches.html", z=coaches.zaehler(), zeilen=coaches.uebersicht(),
        einer=gewaehlt, tage=tage,
        kunden=coaches.kunden(coach) if gewaehlt else [],
        spur=aktivitaet.tagesspur(name, tage) if name else [],
        logins=aktivitaet.anmeldungen(name, tage) if name else [])


@app.route("/protokoll")
def protokoll_seite():
    """Was wann von wem geändert wurde. Wird nie bearbeitet und nie gelöscht."""
    f = {"benutzer": request.args.get("benutzer") or None,
         "bereich": request.args.get("bereich") or None,
         "suche": (request.args.get("q") or "").strip() or None}
    tage = request.args.get("tage", type=int)
    seit = None
    if tage:
        seit = (datetime.datetime.now() - datetime.timedelta(days=tage)).isoformat()
    return render_template("protokoll.html",
                           zeilen=konten.protokoll(limit=400, seit=seit, **f),
                           filter=dict(f, tage=tage), bereiche=konten.bereiche(),
                           leute=[z["benutzer"] for z in konten.wer_war_aktiv(3650)])


# ---------------------------------------------------------------- Übersicht

@app.route("/")
def uebersicht():
    """Die Startseite des Pakets: Aufgaben und der Stand der vier Abteilungen."""
    from quellen import kommo
    return render_template(
        "uebersicht.html",
        aufgaben=aufgaben.zaehler(), aufgaben_coach=aufgaben.je_coach(),
        tf_neu=tf.anzahl_neu(),
        # `aktiv=1` bleibt: die Kachel schreibt darunter wörtlich „aktive Profile"
        # (`uebersicht.html`). Wo eine Oberfläche nur „Profil" sagt, wird jedes
        # gezählt – siehe `sammelanlage.vorschlaege`.
        tf_profile=db.wert("SELECT COUNT(*) FROM tf_profil WHERE aktiv=1"),
        cv_kunden=db.wert("SELECT COUNT(*) FROM kunde WHERE standort=?",
                          (db.STANDORT_STANDARD,)),
        cv_vorhanden=db.wert(
            "SELECT COUNT(DISTINCT kunde_id) FROM lebenslauf WHERE kunde_id IS NOT NULL"),
        vertrieb=trichter.zaehler(), spur=aktivitaet.zaehler(),
        quellen_bereit=sum(1 for q in tf.quellen_stand() if q["bereit"]),
        quellen_gesamt=len(tf.quellen_stand()),
        kommo=kommo.Kommo().api_bereit(), imap=tf.imap_konfiguriert(),
        betrieb_laeuft=betrieb.laeuft(), lauf_uhrzeit=betrieb.UHRZEIT_LAUF,
        sicherung_uhrzeit=betrieb.UHRZEIT_SICHERUNG)


@app.route("/kunden")
def kunden():
    """Die Kundenliste des Pakets – ein Spiegel des CRM, nicht seine Ablösung.

    Gezeigt wird nur, was Taskforce und Lebenslauf brauchen: wer ist wer, wer betreut,
    liegt ein Lebenslauf vor, läuft ein Suchprofil. Termine, Unterrichtseinheiten,
    Dokumente und Abrechnung stehen im CRM und werden hier nicht doppelt geführt."""
    return _kundenliste()


def _kundenliste(fehler=None, aehnlich=None, verweis=None, eingabe=None):
    """Die Liste samt Anlegeformular darüber.

    Eigene Funktion, weil `kunde_anlegen` bei einer Rückfrage dieselbe Seite noch einmal
    zeigen muss – mit den eingetippten Werten im Formular. Eine Weiterleitung würde sie
    wegwerfen, und wer seinen Namen zweimal tippt, tippt ihn beim zweiten Mal anders."""
    suche = (request.args.get("q") or "").strip() or None
    coach = request.args.get("coach", type=int)
    nur = request.args.get("nur") or None
    sql = ("SELECT k.id, k.name, k.status_code, k.telefon, k.email, k.stadt, k.massnahme,"
           "       k.kundennummer, k.quelle_stand, m.name AS coach,"
           "       (SELECT COUNT(*) FROM lebenslauf l WHERE l.kunde_id=k.id) AS lebenslaeufe,"
           # Jedes Profil, auch ein pausiertes – die Spalte heisst „Profile", nicht
           # „laufende Profile". Die eine Zählweise des Hauses, siehe `sammelanlage`.
           "       (SELECT COUNT(*) FROM tf_profil p WHERE p.kunde_id=k.id) AS profile"
           "  FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id WHERE k.standort=?")
    args = [db.STANDORT_STANDARD]
    if coach:
        sql += " AND k.coach_id=?"
        args.append(coach)
    if suche:
        sql += " AND (k.name LIKE ? OR k.stadt LIKE ? OR k.kundennummer LIKE ?)"
        args += [f"%{suche}%"] * 3
    if nur == "laufend":
        sql += " AND k.status_code IN ('H','I')"
    elif nur == "ohne_cv":
        sql += " AND NOT EXISTS (SELECT 1 FROM lebenslauf l WHERE l.kunde_id=k.id)"
    elif nur == "ohne_profil":
        # Ohne JEDES Profil. Wer eins hat und es pausiert hat, steht nicht „ohne
        # Suchprofil" da – er steht still, und das ist eine andere Frage.
        sql += " AND NOT EXISTS (SELECT 1 FROM tf_profil p WHERE p.kunde_id=k.id)"
    elif nur == "vorlaeufig":
        # Die Arbeitsliste fürs Andocken: alle hier erfassten Menschen, die das CRM noch
        # nicht bestätigt hat. Ohne diesen Filter findet man sie nur, indem man 120 Zeilen
        # nach gelben Plaketten absucht.
        sql += " AND k.quelle_stand=?"
        args.append(ANLAGE_QUELLE)
    zeilen = db.hole(sql + " ORDER BY k.status_code, k.name", tuple(args))
    for z in zeilen:
        z["status_text"] = db.STATUS.get(z["status_code"], "—")
        z["ampel"] = db.STATUS_AMPEL.get(z["status_code"], "grau")
    return render_template(
        "kunden.html", zeilen=zeilen, suche=suche, coach=coach, nur=nur,
        coaches=db.hole("SELECT id, name FROM mitarbeiter WHERE standort=? ORDER BY name",
                        (db.STANDORT_STANDARD,)),
        # Nur die Status, die eine Erfassung ohne Akte ehrlich abbilden kann – siehe
        # `ANLAGE_STATUS`. Die Liste der Kunden zeigt weiterhin jeden Code, der im
        # Bestand steht; angelegt werden können hier nur diese.
        status_liste=[(c, db.STATUS[c]) for c in ANLAGE_STATUS],
        fehler=fehler, aehnlich=aehnlich or [], verweis=verweis, eingabe=eingabe or {})


@app.route("/kunden/anlegen", methods=["POST"])
def kunde_anlegen():
    """Einen Menschen erfassen – Name, Telefon, Ort, Status. Mehr nicht.

    Angelegt wird ein Kunde eigentlich im CRM. Solange `CRM_BASIS` nicht in der .env
    steht, kommt von dort aber nichts zurück, und für jemanden, den das OS nicht kennt,
    kann die Taskforce nicht suchen. Also geht es auch hier – der Knopf in die Topbar
    bleibt daneben bestehen, keiner der beiden Wege versperrt den anderen.

    Die Grenze aus `docs/wissen/crm-abgleich-was-gehoert-wohin.md` bleibt, wo sie ist:
    kein UE-Feld, kein Gutschein, kein Termin, kein Maßnahmenprotokoll. Erfasst wird,
    WER jemand ist, nicht seine Akte."""
    eingabe = {f: (request.form.get(f) or "").strip()
               for f in ("name", "telefon", "stadt", "sprache", "kundennummer",
                         "status_code")}
    if not eingabe["name"]:
        return _kundenliste(fehler="Ohne Namen wird nichts angelegt.", eingabe=eingabe)
    # Geprüft gegen `ANLAGE_STATUS`, nicht gegen `db.STATUS`: die Auswahlliste einzuengen
    # reicht nicht, ein untergeschobenes Formularfeld käme sonst weiter durch. „K – Lead
    # ohne Antrag" ist der Rückfall – ist da, noch nichts passiert.
    if eingabe["status_code"] not in ANLAGE_STATUS:
        eingabe["status_code"] = "K"

    # Dieselbe Regel wie in der Schnittstelle (`api.py`): eine Kundennummer gibt es
    # einmal. Abgewiesen wird mit dem Weg zum vorhandenen Datensatz – „gibt es schon",
    # ohne zu sagen wo, ist eine Sackgasse. Hier gilt kein „trotzdem": zwei Datensätze
    # unter einer Nummer wären im QM ein Befund, keine Entscheidung.
    if eingabe["kundennummer"]:
        schon = db.eine("SELECT id, name FROM kunde WHERE kundennummer=? AND standort=?",
                        (eingabe["kundennummer"], db.STANDORT_STANDARD))
        if schon:
            return _kundenliste(
                fehler=f"Die Kundennummer {eingabe['kundennummer']} ist schon vergeben.",
                verweis=schon, eingabe=eingabe)

    # Unscharf gegen Dubletten. Nur ein Teil der Kunden hat überhaupt eine Nummer, und
    # ein exakter Namensvergleich findet zwei Namensteile nicht wieder, wenn im Bestand
    # drei stehen, ein Rufname dazwischenrutscht oder die zweite Schreibweise in Klammern
    # dahinter steht – bei Namen aus vier Quellen ist das die Regel, nicht der Sonderfall.
    # `tf.kunden_suchen` vergleicht darum über `vergleichbar()`: ohne Umlaute, Akzente und
    # Reihenfolge, dieselbe Funktion wie die Kopfsuche. Eine zweite Rechenart wären zwei
    # Wahrheiten.
    #
    # Gefragt wird zurück, nicht gesperrt: „trotzdem anlegen" bleibt immer erreichbar.
    # Eine Hürde führt nur dazu, dass jemand den Namen absichtlich falsch tippt – und
    # dann steht der Mensch doppelt da, ohne dass es je auffällt.
    if not request.form.get("bestaetigt"):
        aehnlich = tf.kunden_suchen(eingabe["name"], limit=5)
        if aehnlich:
            return _kundenliste(eingabe=eingabe, aehnlich=aehnlich)

    # Leere Felder bleiben leer. Ein erfundener Ort ist schlimmer als ein fehlender:
    # die Suche läuft dann am falschen Fleck, ohne dass jemand es merkt.
    #
    # `kunde` trägt UNIQUE(name, standort) (datenbank.py). Bei exakt gleicher Schreibweise
    # hilft „trotzdem anlegen" also nicht – die Datenbank lässt den zweiten Satz nicht zu.
    # Ohne dieses Abfangen endete genau dieser Fall auf einer 500er-Seite: kein Hinweis,
    # kein Protokolleintrag, und alles Eingetippte weg. Die Rückfrage vorher ist unscharf
    # und fängt ihn nur, solange niemand „trotzdem" drückt.
    try:
        with db.offen() as con:
            cur = con.execute(
                "INSERT INTO kunde (name, telefon, stadt, sprache, kundennummer, status_code,"
                " standort, quelle_stand, stand_am) VALUES (?,?,?,?,?,?,?,?,?)",
                (eingabe["name"], eingabe["telefon"] or None, eingabe["stadt"] or None,
                 eingabe["sprache"] or None, eingabe["kundennummer"] or None,
                 eingabe["status_code"], db.STANDORT_STANDARD, ANLAGE_QUELLE, tf.jetzt()))
            kid = cur.lastrowid
    except sqlite3.IntegrityError:
        schon = db.eine("SELECT id, name FROM kunde WHERE name=? AND standort=?",
                        (eingabe["name"], db.STANDORT_STANDARD))
        return _kundenliste(
            fehler=f"{eingabe['name']} steht mit genau dieser Schreibweise schon im"
                   " Bestand – ein zweiter Satz unter demselben Namen ist nicht möglich.",
            verweis=schon, eingabe=eingabe)
    notieren(f"Kunde angelegt: {eingabe['name']}", "kunden", kid, ANLAGE_QUELLE)
    return redirect(url_for("kunde_detail", kid=kid))


# Flask lässt für <int:kid> beliebig große Zahlen durch; SQLite nimmt nur 64 Bit.
# Eine getippte Riesenzahl in der Adresse endete deshalb in einem Absturz statt in 404.
SQLITE_MAX = 2 ** 63 - 1


@app.errorhandler(413)
def zu_viel_auf_einmal(_):
    """Der Rumpf war größer, als Werkzeug annimmt (`max_form_memory_size`, 500.000 B).

    Getroffen wird das beim Übernehmen: die Treffer reisen im Formular mit, und mit
    echten Treffergrößen liegt die Grenze bei rund 575 Stück. Heute unerreichbar, weil
    `tf.direktsuche` bei 200 abschneidet – aber genau eine Zahl entfernt, und ohne
    diesen Griff bekam man die nackte englische Werkzeug-Seite: keine Erklärung, kein
    Weg zurück, alles Angehakte weg."""
    return render_template(
        "fehler.html", titel="Zu viel auf einmal",
        text="Es wurden mehr Treffer auf einmal abgeschickt, als in einen Aufruf passen"
             " (Grenze rund 575). Es wurde nichts übernommen. Bitte die Suche enger"
             " fassen – Ort, Umkreis oder Stichworte – und die Treffer in zwei"
             " Durchgängen anhaken."), 413
CRM_URL = os.environ.get("CRM_URL", "https://crm.improfy.de")

# Woher ein Datensatz kommt, steht in `kunde.quelle_stand`. Was hier in der Oberfläche
# entsteht, trägt darum eine eigene Herkunft – und Kundenliste wie Kundenakte machen
# sie als Plakette „vorläufig" sichtbar.
#
# **Der Marker räumt sich NICHT von selbst weg – nicht verlässlich.** Hier stand das
# Gegenteil; es hielt nicht. `quellen/crm.py._passender_kunde` ordnet über die exakt
# gleiche Kundennummer zu, sonst über den exakt casefold-gleichen Namen. Genau das ist
# der Vergleich, von dem zwei Bildschirme weiter oben steht, dass er diesen Bestand nicht
# wiederfindet: „Erika Müstermann" hier erfasst, „Erika Maria Müstermann (Muesterman)" im CRM –
# kein Treffer, zweiter Datensatz, und der erste behält Suchprofil, Angebote und Verlauf.
#
# **Warum hier trotzdem nicht unscharf verglichen wird.** `tf.vergleichbar` ist mit Absicht
# weich (ohne Umlaute, ohne Reihenfolge) und darum richtig für einen Vorschlag an einen
# Menschen: ein Fehltreffer kostet einen Blick. `_passender_kunde` dagegen SCHREIBT – es
# überschreibt Stammdaten und hängt eine CRM-ID an einen Satz. Ein Fehltreffer führt dort
# zwei Menschen zusammen, ohne dass es jemand sieht. Lieber eine Lücke als ein falscher
# Wert; die Zuordnung bleibt deshalb eine Entscheidung, die ein Mensch trifft.
#
# **Der Weg dafür:** Die Kundenliste hat den Filter „nur vorläufig erfasste" – das ist die
# Arbeitsliste fürs Andocken. Auf der Akte trägt man die Kundennummer aus dem CRM nach
# (`kunde_nummer_nachtragen`); ab da greift der exakte Weg, und die Herkunft springt beim
# nächsten Einlesen von selbst auf „CRM". Kein zweiter Namensvergleich, keine zweite
# Wahrheit.
ANLAGE_QUELLE = "Oberfläche (vorläufig)"

# Welche Status hier überhaupt zur Wahl stehen.
#
# Das Formular erfasst, WER jemand ist – nicht seine Akte. Zwölf Codes zur Auswahl zu
# stellen widerspricht dem: A–F behaupten einen Antrag, G–I eine Gutschein-Entscheidung,
# J eine abgeschlossene Maßnahme. Wer hier „H – Gutschein da, Maßnahme läuft" wählt, zählt
# ab dem nächsten Seitenaufruf im Leerlaufband als laufende Maßnahme, steht in der
# Sammelanlage und in der Kapazitätsrechnung der Coaches – ohne Gutschein, ohne Akte, ohne
# dass das CRM davon weiß. Und er bleibt so stehen: `quellen/crm.py` fasst `status_code`
# beim Einlesen nicht an.
#
# Übrig bleiben die beiden Codes, die nachweislich nichts über eine Akte behaupten:
# K „Lead ohne Antrag" (ist da, noch nichts passiert) und L „Kunde pausiert / abgesprungen"
# (wird nicht verfolgt). Beide zählen in keiner Auswertung als laufende Maßnahme – geprüft
# an jeder Stelle, die `status_code IN ('H','I')` fragt. Alles Weitere entsteht im CRM und
# kommt von dort zurück.
ANLAGE_STATUS = ("K", "L")


@app.context_processor
def crm_verweis():
    """`crm_url` steht jeder Vorlage zur Verfügung, nicht nur der Kundenakte.

    Der Weg ins CRM hängt seit dem 21.09.2026 in der Topbar und damit auf jeder Seite –
    einen Kunden mit Akte, Terminen und UE erfasst man nur drüben. Ihn an jede Route
    einzeln zu hängen wäre dieselbe Zeile achtmal. `kunde_detail` gibt ihn weiter mit:
    ein ausdrücklich übergebener Wert überschreibt den Kontext, der alte Weg gilt also
    unverändert weiter."""
    return {"crm_url": CRM_URL, "anlage_quelle": ANLAGE_QUELLE}


@app.route("/kunde/<int:kid>")
def kunde_detail(kid):
    """Der Kunde, wie das Paket ihn braucht – mit Weg ins CRM für alles Übrige."""
    if kid > SQLITE_MAX:
        abort(404)
    kunde = db.eine(
        "SELECT k.*, m.name AS coach FROM kunde k"
        " LEFT JOIN mitarbeiter m ON m.id=k.coach_id WHERE k.id=?", (kid,))
    if not kunde:
        abort(404)
    kunde["status_text"] = db.STATUS.get(kunde["status_code"], "—")
    kunde["ampel"] = db.STATUS_AMPEL.get(kunde["status_code"], "grau")
    return render_template(
        "kunde_detail.html", kunde=kunde, crm_url=CRM_URL,
        meldung=request.args.get("meldung"), fehler=request.args.get("fehler"),
        gutscheine=db.hole(
            "SELECT improfy_id, massnahme, von, bis, ue_bewilligt, ue_gerechnet, status"
            "  FROM gutschein_zeile WHERE kunde_id=? ORDER BY von DESC", (kid,)),
        tf_profile=tf.profile_von(kid), lebenslaeufe=L.von_kunde(kid),
        profil=db.eine("SELECT kurzprofil, cv_text FROM kunde_profil WHERE kunde_id=?",
                       (kid,)) or {})


@app.route("/kunde/<int:kid>/kundennummer", methods=["POST"])
def kunde_nummer_nachtragen(kid):
    """Die Kundennummer aus dem CRM nachtragen – der Weg, einen vorläufigen Satz zuzuordnen.

    Ein hier erfasster Mensch hat keine Kundennummer; das CRM findet ihn beim Einlesen
    darum nur über den exakt gleich geschriebenen Namen, und den hat er selten (siehe
    `ANLAGE_QUELLE`). Steht die Nummer erst drin, greift der sichere Weg: `quellen/crm.py`
    ordnet über sie zu und setzt die Herkunft auf „CRM". Die Zuordnung trifft damit ein
    Mensch, der beide Sätze vor sich hat – nicht ein unscharfer Vergleich im Hintergrund.

    Zwei Regeln, beide aus dem QM: eine vorhandene Nummer wird nie überschrieben (das wäre
    eine zweite Wahrheit über denselben Satz), und eine schon vergebene Nummer wird
    abgewiesen – mit dem Weg zum Datensatz, der sie hat."""
    kunde = db.eine("SELECT id, name, kundennummer FROM kunde WHERE id=?", (kid,))
    if not kunde:
        abort(404)
    nummer = (request.form.get("kundennummer") or "").strip()
    if not nummer:
        return redirect(url_for("kunde_detail", kid=kid, fehler="Keine Nummer eingetragen."))
    if (kunde["kundennummer"] or "").strip():
        return redirect(url_for(
            "kunde_detail", kid=kid,
            fehler=f"Hier steht schon die Nummer {kunde['kundennummer']}."
                   " Eine vorhandene Kundennummer wird nicht überschrieben."))
    schon = db.eine("SELECT id, name FROM kunde WHERE kundennummer=? AND standort=?",
                    (nummer, db.STANDORT_STANDARD))
    if schon:
        return redirect(url_for(
            "kunde_detail", kid=kid,
            fehler=f"Die Kundennummer {nummer} gehört schon zu {schon['name']}"
                   f" (Datensatz {schon['id']})."))
    with db.offen() as con:
        con.execute("UPDATE kunde SET kundennummer=? WHERE id=?", (nummer, kid))
    notieren(f"Kundennummer nachgetragen: {nummer}", "kunden", kid, kunde["name"])
    return redirect(url_for(
        "kunde_detail", kid=kid,
        meldung=f"Kundennummer {nummer} eingetragen. Beim nächsten Einlesen aus dem CRM"
                " findet der Adapter diesen Satz darüber wieder und setzt die Herkunft"
                " auf „CRM“."))


@app.route("/anbindung")
def anbindung_seite():
    """Die Datenkarte des CRM – Grundlage für die Naht, am laufenden System nachgesehen."""
    return render_template(
        "anbindung.html", bericht=crm_karte.bericht(), faecher=crm_karte.UNSERE_FAECHER,
        ablage={"Maßnahmenordner": crm_karte.ABLAGE["massnahme"],
                "Dokumente": crm_karte.ABLAGE["dokumente"],
                "Endergebnis": crm_karte.ABLAGE["endergebnis"]},
        pflicht_gesamt=sum(1 for g in crm_karte.ABLAGE.values() for f in g if "*" in f),
        von_hand=crm_karte.NOCH_VON_HAND, max_kb=crm_karte.MAX_KB,
        kunde_felder=crm_karte.KUNDE_FELDER, konto_felder=crm_karte.KONTO_FELDER,
        status=crm_karte.STATUS, status_text=crm_karte.STATUS_TEXT,
        standorte=crm_karte.STANDORTE)


@app.route("/aussen")
def aussen_seite(probe=None, meldung=None):
    """Außenanbindungen: welches Portal, auf welchem Weg, antwortet es gerade."""
    return render_template("aussenanbindung.html", zeilen=AA.register(),
                           laeufe=AA.letzte_laeufe(), probe=probe or {}, meldung=meldung)


@app.route("/aussen/pruefen", methods=["POST"])
def aussen_pruefen():
    quelle = request.form.get("quelle")
    if quelle:
        ergebnis = {quelle: AA.pruefe(quelle)}
        text = f"{quelle}: {ergebnis[quelle]['meldung']}"
    else:
        lauf = AA.pruefe_alle()
        ergebnis = lauf["ergebnis"]
        ok = sum(1 for e in ergebnis.values() if e["ok"])
        text = f"{ok} von {len(ergebnis)} Anbindungen haben geantwortet."
    return aussen_seite(probe=ergebnis, meldung=text)


# ------------------------------------------------------- Lebenslauf bauen
# Füllt die Improfy-Excel-Vorlage. Die Füll-Logik liegt unverändert in cv/ und
# stammt aus der CV-App (siehe cv/HERKUNFT.txt); hier nur Seite und Knöpfe.

LEER_BERUF = {"zeitraum": "", "firma": "", "jobtitel": "", "taetigkeiten": []}
LEER_BILDUNG = {"zeitraum": "", "abschluss": "", "institution": "", "note": ""}


def _cv_seite(kid, daten=None, fehler=None, gelesen=None, rohtext="", hinweise=None,
              meldung=None, gesucht=""):
    # Ohne Kunden ist das kein Fehler, sondern der erste Zustand der Seite: das Formular
    # steht da, die Vorlagen sind sichtbar, und oben wird gefragt, für wen es sein soll.
    v = LB.vorbelegung(kid) if kid else None
    if kid and not v:
        abort(404)
    d = daten or (v["daten"] if v else {})
    def auffuellen(liste, leer, anzahl):
        liste = list(liste or [])
        return liste + [dict(leer) for _ in range(max(0, anzahl - len(liste)))]
    return render_template(
        "lebenslauf_bauen.html", v=v, d=d, fehler=fehler, gelesen=gelesen, rohtext=rohtext,
        hinweise=hinweise or [], lesbar=dokument_lesen.ENDUNGEN, meldung=meldung,
        foto=fotos.foto(kid) if kid else None,
        kundenwahl=LB.uebersicht(),
        designs=cv_pdf.DESIGNS, chrome=cv_pdf.bereit(), galerie=cv_pdf.galerie(),
        vorlagen=cv_sammlung.alle(), gesucht=gesucht,
        beruf=auffuellen(d.get("berufserfahrung"), LEER_BERUF, 7),
        bildung=auffuellen(d.get("bildung"), LEER_BILDUNG, 4),
        sprachen=auffuellen(d.get("sprachen"), {"sprache": "", "niveau": ""}, 4),
        edv=auffuellen(d.get("edv_kenntnisse"), {"programm": "", "sterne": 4}, 4),
        skills=auffuellen(d.get("soft_skills"), {"eigenschaft": "", "sterne": 5}, 6),
        niveaus=LB.NIVEAUS, frueher=LB.gebaute(kid) if kid else [])


@app.route("/lebenslauf")
def lebenslauf_uebersicht():
    """Der Lebenslauf fängt hier an – nicht beim Kunden.

    Vorher musste man erst in die Kundenliste, dort die Person suchen, sie anklicken und
    von ihrer Seite aus den Lebenslauf öffnen. Vier Schritte für die eine Sache, für die
    man hergekommen ist. Jetzt öffnet dieser Reiter direkt das Bauformular; die Person
    ist die erste Frage darin, nicht die Voraussetzung dafür."""
    kid = (request.args.get("kunde") or "").strip()
    if kid.isdigit() and 0 < int(kid) <= SQLITE_MAX:
        if LB.vorbelegung(int(kid)):
            return _cv_seite(int(kid))

    # Wer den Namen tippt statt ihn aus der Liste zu klicken, bekommt keine ID mit.
    # Dann loest der Server auf. Ohne das landet der Coach wortlos wieder auf der
    # Auswahl und weiss nicht, warum.
    name = (request.args.get("_name") or "").strip()
    if name:
        treffer = [z for z in LB.uebersicht()
                   if (z["name"] or "").casefold() == name.casefold()]
        if len(treffer) == 1:
            return _cv_seite(treffer[0]["id"])
        teil = [z for z in LB.uebersicht()
                if name.casefold() in (z["name"] or "").casefold()]
        if len(teil) == 1:
            return _cv_seite(teil[0]["id"])
        if not teil:
            return _cv_seite(None, gesucht=name,
                             fehler='Kein Kunde heisst "%s".' % name)
        return _cv_seite(None, gesucht=name,
                         fehler='Mehrere Kunden passen zu "%s": %s. Bitte den ganzen '
                                'Namen aus der Liste waehlen.'
                                % (name, ", ".join(z["name"] for z in teil[:6])))
    return _cv_seite(None)


@app.route("/lebenslauf/liste")
def lebenslauf_liste():
    """Der Überblick: wer hat einen Lebenslauf, wer nicht. Zum Nachhalten, nicht zum Bauen."""
    suche = (request.args.get("q") or "").strip() or None
    stand = request.args.get("stand") or None
    zeilen = LB.uebersicht(suche=suche, stand=stand)
    return render_template(
        "lebenslauf.html", zeilen=zeilen, gebaut=LB.gebaute(), suche=suche, stand=stand,
        ohne_id=sum(1 for z in zeilen if not z["interne_id"]))


@app.route("/kunde/<int:kid>/lebenslauf")
def lebenslauf_formular(kid):
    return _cv_seite(kid)


@app.route("/kunde/<int:kid>/lebenslauf/lesen", methods=["POST"])
def lebenslauf_lesen(kid):
    """Alte Unterlagen auswerten und das Formular damit vorbelegen.

    Hochgeladene Dateien und eingefügter Text laufen durch denselben Weg. Gespeichert
    wird dabei nichts – erst der Knopf darunter erzeugt Excel oder PDF."""
    rohtext = request.form.get("rohtext") or ""
    dateien = [(d.filename, d.read()) for d in request.files.getlist("unterlagen")
               if d and d.filename]
    daten, gefunden, hinweise, gelesener_text = dokument_lesen.lese_unterlagen(dateien, rohtext)
    if gelesener_text:
        rohtext = gelesener_text
    if not daten:
        return _cv_seite(kid, fehler="Aus den Unterlagen ließ sich nichts auslesen.",
                         rohtext=rohtext,
                         hinweise=hinweise or ["Keine Datei abgelegt und kein Text eingefügt."])
    # Was das OS sicher weiß, gewinnt gegen das, was im alten Lebenslauf steht.
    v = LB.vorbelegung(kid)
    for feld in ("vorname", "nachname"):
        if v["daten"].get(feld):
            daten[feld] = v["daten"][feld]
    for feld in ("mobil", "email", "geburtsdatum"):
        if not daten.get(feld) and v["daten"].get(feld):
            daten[feld] = v["daten"][feld]
    daten["kunde_von"] = v["daten"]["kunde_von"]
    if not daten.get("sprachen"):
        daten["sprachen"] = v["daten"]["sprachen"]
    if not daten.get("massnahme_zeitraum"):
        daten["massnahme_zeitraum"] = v["daten"]["massnahme_zeitraum"]
    return _cv_seite(kid, daten, gelesen=gefunden or ["nichts Verwertbares"], rohtext=rohtext,
                     hinweise=hinweise)


@app.route("/kunde/<int:kid>/lebenslauf", methods=["POST"])
def lebenslauf_erzeugen(kid):
    daten = LB.aus_formular(request.form)
    try:
        rohdaten, fehlend, dateiname, _pfad = LB.bauen(kid, daten)
    except Exception as e:
        return _cv_seite(kid, daten, fehler=str(e))
    from flask import Response
    return Response(
        rohdaten,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{dateiname}"',
                 "X-Fehlende-Felder": str(len(fehlend))})


@app.route("/kunde/<int:kid>/lebenslauf/pdf", methods=["POST"])
def lebenslauf_pdf(kid):
    """Designter Lebenslauf als PDF – aus denselben Feldern wie die Excel."""
    from flask import render_template as _render
    daten = LB.aus_formular(request.form)
    design = request.form.get("design") or cv_pdf.DESIGNS[0][0]
    # Ein frisch hochgeladenes Foto gewinnt und wird gleich am Kunden gemerkt,
    # sonst nimmt das PDF das, was schon hinterlegt ist.
    hochgeladen = request.files.get("foto")
    if hochgeladen and hochgeladen.filename:
        try:
            fotos.speichern(kid, hochgeladen.read(), hochgeladen.filename)
            notieren("Bewerbungsfoto hinterlegt", "lebenslauf", kid, hochgeladen.filename)
        except Exception:
            pass
    roh, mime = fotos.rohdaten(kid)
    foto_uri = cv_pdf.foto_uri(roh, mime) if roh else None
    v = LB.vorbelegung(kid)
    name = LB.blattname(v["interne_id"], daten.get("vorname") or "", daten.get("nachname") or "")
    dateiname = f"{name}_{design}_{datetime.date.today():%Y-%m-%d}.pdf"
    try:
        pdf, dateiname, _pfad = cv_pdf.bauen(_render, daten, design, foto_uri, dateiname)
    except Exception as e:
        return _cv_seite(kid, daten, fehler=f"PDF nicht erzeugt: {e}")
    LB.merken(kid, dateiname)
    from flask import Response
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{dateiname}"'})


@app.route("/kunde/<int:kid>/foto", methods=["POST"])
def lebenslauf_foto(kid):
    """Bewerbungsfoto hinterlegen oder entfernen – es gehört an den Kunden, nicht an einen Klick."""
    if request.form.get("was") == "loeschen":
        fotos.loeschen(kid)
        notieren("Bewerbungsfoto entfernt", "lebenslauf", kid)
        return _cv_seite(kid, meldung="Foto entfernt.")
    datei = request.files.get("foto")
    if not datei or not datei.filename:
        return _cv_seite(kid, fehler="Keine Datei gewählt.")
    try:
        stand = fotos.speichern(kid, datei.read(), datei.filename)
    except Exception as e:
        return _cv_seite(kid, fehler=f"Das Bild ließ sich nicht lesen: {e}")
    notieren("Bewerbungsfoto hinterlegt", "lebenslauf", kid, datei.filename)
    return _cv_seite(kid, meldung=f"Foto hinterlegt, {stand['bytes'] // 1024} kB"
                                  + (f", {stand['breite']}×{stand['hoehe']} Pixel"
                                     if stand["breite"] else "") + ".")


@app.route("/kunde/<int:kid>/foto.jpg")
def lebenslauf_foto_zeigen(kid):
    roh, mime = fotos.rohdaten(kid)
    if not roh:
        abort(404)
    from flask import Response
    return Response(roh, mimetype=mime or "image/jpeg")


@app.route("/lebenslauf/fotos", methods=["GET", "POST"])
def lebenslauf_fotos():
    """Fotos aus einem Ordner zuordnen – für den Schwung aus der Chat-Gruppe."""
    ergebnis = None
    if request.method == "POST":
        pfad = (request.form.get("ordner") or "").strip()
        try:
            zugeordnet, offen = fotos.aus_ordner(pfad)
            notieren(f"{len(zugeordnet)} Fotos zugeordnet", "lebenslauf", None, pfad)
            ergebnis = {"zugeordnet": zugeordnet, "offen": offen, "ordner": pfad}
        except Exception as e:
            ergebnis = {"fehler": str(e), "ordner": pfad}
    return render_template("fotos.html", stand=fotos.stand(), ergebnis=ergebnis,
                           chat_bereit=fotos.chat_bereit(),
                           ohne=db.hole(
                               "SELECT k.id, k.name, m.name AS coach FROM kunde k"
                               "  LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
                               " WHERE k.standort=? AND k.status_code IN ('H','I')"
                               "   AND NOT EXISTS (SELECT 1 FROM kunde_foto f WHERE f.kunde_id=k.id)"
                               " ORDER BY k.name", (db.STANDORT_STANDARD,)))


@app.route("/lebenslauf/galerie/<path:bild>")
def lebenslauf_galerie(bild):
    pfad = os.path.join(HIER, "cv", "galerie", os.path.basename(bild))
    if not os.path.exists(pfad):
        abort(404)
    return send_file(pfad)


@app.route("/lebenslauf/datei/<path:dateiname>")
def lebenslauf_datei(dateiname):
    pfad = LB.datei(dateiname)
    if not pfad:
        abort(404)
    return send_file(pfad, as_attachment=True, download_name=os.path.basename(pfad))


# --------------------------------------------------------------- Taskforce
# Job- und Wohnungs-Agenten je Kunde. Alles Fachliche steht in taskforce.py;
# hier nur die Seiten und die Knöpfe.

def _tf_seite(meldungen=None, modus=None):
    """Die Tafel. Jeder Filter steht in der Adresse, damit man eine Ansicht teilen kann.

    `modus` ist die Aufspaltung: 'job' (Arbeitssuche), 'wohnung' (Wohnungssuche) oder None
    fuer das Gesamtbild. Er wirkt auf alles auf dieser Seite – Kennzahlen, Wiedervorlage,
    Quellen, Regler, Tabelle und den Agentenlauf –, damit die Zahlen einer Ansicht
    zusammenpassen und nicht die Haelfte aus der anderen Suche stammt."""
    if not meldungen and request.args.get("meldung"):
        meldungen = [request.args["meldung"]]

    def zahl(name, typ=int):
        return request.args.get(name, type=typ)

    f = {"kunde_id": zahl("kunde"), "art": modus,
         "quelle": request.args.get("quelle") or None,
         "suche": (request.args.get("q") or "").strip() or None,
         "status": request.args.get("status", "neu") or None,
         "coach_id": zahl("coach"), "min_score": zahl("score"), "min_match": zahl("match"),
         "max_km": zahl("km"), "seit_tage": zahl("tage"),
         "arbeitszeit": request.args.get("arbeitszeit") or None,
         "quereinstieg": bool(request.args.get("quereinstieg")),
         "min_gehalt": zahl("gehalt"), "max_miete": zahl("miete"),
         "min_zimmer": zahl("zimmer", float), "min_flaeche": zahl("flaeche"),
         "sortierung": request.args.get("sort") or None}
    # Wie viele der eingeklappten Feinregler stehen? Ein gesetzter Regler, den man nicht
    # sieht, erklaert eine leere Tabelle nicht – die Zahl steht darum am Klappknopf.
    fein = ("coach_id", "quelle", "min_score", "min_match", "max_km", "seit_tage",
            "arbeitszeit", "quereinstieg", "min_gehalt", "max_miete", "min_zimmer", "min_flaeche")
    feine_regler = len([s for s in fein if f.get(s)])
    kunde_name = db.wert("SELECT name FROM kunde WHERE id=?", (f["kunde_id"],)) if f["kunde_id"] else None

    # Die Zahlen an den beiden Knoepfen: man sieht die andere Haelfte, ohne hinzuwechseln.
    #
    # **Jedes Profil, auch ein pausiertes** – die eine Zählweise des Hauses. Hier stand
    # `if p["aktiv"]`, und weil in Python gefiltert wurde statt in SQL, ist die Stelle
    # bei der Erhebung der Zählstellen durchgerutscht. Der Knopf schreibt wörtlich
    # „N Profile" – nicht „aktive" –, und einen Klick weiter listet der Arbeitsplatz
    # eine Zeile mehr. Wo eine Oberfläche „aktive Profile" schreibt, bleibt `aktiv=1`:
    # die Kachel in `taskforce.html`, `uebersicht.html` und `/api/gesundheit`.
    stand = {art: {"profile": len(tf.uebersicht(art=art)),
                   "neu": tf.anzahl_neu(art=art)} for art in ("job", "wohnung")}
    # „Alle Filter zuruecksetzen" fuehrt auf die eigene Route, nicht auf request.path.
    # Wer ueber /taskforce?kunde=..&score=6 hierherkommt – so verlinkt es die Aufgabenliste –,
    # landete mit request.path auf /taskforce ohne Regler, und das ist seit dem Umbau der
    # Einstieg. Ein Ruecksetzknopf, der die Tafel verlaesst, setzt nicht zurueck.
    #
    # Seit /taskforce/arbeit das Dashboard ist, fuehrt das Zuruecksetzen auf die Tafel
    # derselben Art: `taskforce_tafel` mit `art`. Sonst sprang der Knopf von der Tafel
    # ins Dashboard – zurueckgesetzt waeren die Regler dann zwar, aber man stuende vor
    # einer anderen Seite als der, auf der man gefiltert hat.
    basis_url = url_for("taskforce_tafel", art=modus)
    return render_template(
        "taskforce.html", modus=modus, stand=stand, basis_url=basis_url,
        feine_regler=feine_regler, filter_kunde_name=kunde_name,
        profile=tf.uebersicht(art=modus), neu=tf.anzahl_neu(art=modus), filter=f,
        neue=tf.neue_angebote(limit=100, **f), zaehler=tf.angebote_zaehlen(art=modus),
        arbeitszeiten=tf.ARBEITSZEITEN, status_liste=tf.STATUS,
        coaches=db.hole("SELECT DISTINCT m.id, m.name FROM mitarbeiter m JOIN kunde k"
                        " ON k.coach_id=m.id WHERE m.standort=? ORDER BY m.name",
                        (db.STANDORT_STANDARD,)),
        laeufe=tf.laeufe(limit=15), imap=tf.imap_konfiguriert(), alarm=tf.alarm_konfiguriert(),
        meldungen=meldungen, quellen=tf.quellen_stand(modus), kpi=tf.kpi_mitarbeiter(),
        lauf=betrieb.lauf_zustand(),
        # „Kein Suchprofil" heisst im Modus: keins *dieser Art*. Wer ein Jobprofil hat, aber
        # dringend eine Wohnung sucht, fiel in der alten Zaehlung durch.
        #
        # **Ohne `aktiv=1`** – die eine Zählweise des Hauses. Diese Zahl steht direkt vor
        # dem Knopf „Profile in einem Schritt anlegen", und dahinter liegt
        # `sammelanlage.vorschlaege()`. Zählte sie aktive Profile, versprach die Zahl
        # Zeilen, die die Sammelanlage gar nicht zeigt: wer pausiert hat, bekommt dort
        # kein zweites Profil. Dass sein Profil stillsteht, sagt die Plakette auf dem
        # Arbeitsplatz – die Antwort darauf ist einschalten, nicht anlegen.
        ohne_profil=db.wert(
            "SELECT COUNT(*) FROM kunde k WHERE k.standort=? AND k.status_code IN ('H','I')"
            "  AND NOT EXISTS (SELECT 1 FROM tf_profil t WHERE t.kunde_id=k.id"
            + (" AND t.art=?" if modus else "") + ")",
            (db.STANDORT_STANDARD,) + ((modus,) if modus else ())),
        wiedervorlage=tf.wiedervorlage(art=modus), wv_tage=tf.WIEDERVORLAGE_TAGE,
        leute=_tf_leute(), ohne_cv=(tf.ohne_lebenslauf() if modus != "wohnung" else []),
        kunden=db.hole("SELECT id, name FROM kunde WHERE standort=? ORDER BY name",
                       (db.STANDORT_STANDARD,)))


def _tf_leute():
    """Namen für das Feld „wer". Die angemeldete Person steht vorn."""
    namen = [m["name"] for m in db.hole(
        "SELECT name FROM mitarbeiter WHERE standort=? ORDER BY name", (db.STANDORT_STANDARD,))]
    ich = wer()
    if ich["persoenlich"] and ich["name"] in namen:
        namen.remove(ich["name"])
        namen.insert(0, ich["name"])
    return namen


def _bearbeiter(aus_formular=None):
    """Wer hat gehandelt?

    Bei persönlicher Anmeldung zählt die Anmeldung, nicht das Auswahlfeld – sonst bleibt
    jede Mitarbeiterzahl Selbstauskunft. Im Übergangsbetrieb ohne Konten gilt weiterhin,
    was im Formular steht."""
    ich = wer()
    if ich["persoenlich"]:
        return ich["name"]
    return (aus_formular or "").strip() or None


def _tf_einstieg():
    """Der Einstieg: was jetzt zu tun ist, für wen, und wo die Arbeit stehenbleibt.

    Die Tafel zeigt, was die Agenten gefunden haben. Sie zeigt nicht, dass davon nichts
    ankommt. Alles Gerechnete steht in `einstieg.py`, damit der Selbsttest es ohne
    Seitenaufruf anfassen kann – hier steht nur die Seite."""
    # Dieselben zwei Knoepfe wie auf der Tafel, dieselbe Zählweise – jedes Profil,
    # auch ein pausiertes. Die Begründung steht bei `_tf_seite`.
    stand = {art: {"profile": len(tf.uebersicht(art=art)),
                   "neu": tf.anzahl_neu(art=art)} for art in ("job", "wohnung")}
    # Einmal holen, dann teilen: die Zahl unter der Liste muss genau die Menge zaehlen,
    # aus der die Liste ihre Zeilen nimmt. Zwei getrennte Abfragen waren zwei Mengen –
    # acht Zeilen mit „0 offene Handgriffe" darunter ist schlimmer als keine Zahl.
    alle = einstieg.arbeitsliste(limit=None)
    ketten = einstieg.kette()
    # Die Kopfzeile zaehlt genau die Menge, die hinter ihrem Knopf steht: laufende
    # Massnahmen ohne aktives JOBprofil - dieselbe Bedingung, die `sammelanlage.
    # vorschlaege(art='job')` als Zeilen zeigt, gerechnet aus derselben Liste, die
    # darunter die Zeilen fuellt. Die Plakette in der Zeile liest dasselbe Feld.
    #
    # Vorher stand hier Stufe 1 minus Stufe 2 des Bandes. Das Band zaehlt artlos: wer ein
    # Wohnprofil hat, gilt dort als versorgt. Ein Kunde mit Wohnprofil und ohne Jobprofil
    # senkte damit die Kopfzeile um eins, waehrend die Seite hinter dem Knopf ihn weiter
    # auffuehrte - zwei verschiedene Mengen, beschriftet als eine.
    ohne_profil = len([z for z in alle if z["laufend"] and not z["jobprofile"]])
    return render_template(
        "taskforce_einstieg.html", stand=stand, neu=tf.anzahl_neu(),
        kette=ketten, liste=alle[:einstieg.LISTE], liste_gesamt=len(alle),
        ohne_profil=ohne_profil, laufend_gesamt=ketten[0]["zahl"],
        aussen=einstieg.aussenstehend(), beste=einstieg.beste_treffer(),
        profile=tf.uebersicht(), lauf=betrieb.lauf_zustand())


# Regler, die es nur auf der Tafel gibt. Die übrigen Namen der Adresse (`was`, `wo`, `km`,
# `gehalt`, `miete` …) füllen auf dem Arbeitsplatz die Suchmaske und gelten dort sichtbar;
# diese hier hätte er nirgends hingetan. Steht einer davon in der Adresse, war die Tafel
# gemeint – dieselbe Regel wie unter /taskforce, nur andersherum.
NUR_TAFEL_REGLER = ("status", "q", "coach", "score", "match", "tage", "sort", "kunde")

# Wie viele Menschen ohne Suchprofil der Arbeitsplatz auf einmal zeigt. Eine
# Bildschirmgrenze, keine Bestandszahl – mehr steht hinter „alle anzeigen".
DASHBOARD_OHNE_PROFIL = 8


def _tf_dashboard(art):
    """Der Arbeitsplatz einer Art: was gesucht wird, für wen, und was dabei herauskam.

    /taskforce/arbeit und /taskforce/wohnung waren bisher die Tafel mit gesetztem Filter –
    dieselbe Wand aus Treffern, nur halbiert. Die Tafel dieser Art gibt es weiterhin, sie
    steht jetzt unter /taskforce/tafel?art=… und ist von hier aus verlinkt.

    Gerechnet wird hier nichts Neues. Jede Zahl kommt aus derselben Funktion, aus der sie
    auch die Tafel und der Einstieg holen, nur mit `art` davor – zwei Wege, dieselbe Zahl
    zu rechnen, gehen früher oder später auseinander, und dann steht auf zwei Seiten
    Verschiedenes über dieselbe Sache."""
    # Wer mit einem Tafelregler hereinkommt – ein Lesezeichen, ein verschickter Link,
    # ein Sprung aus der Aufgabenliste –, meint die Tafel dieser Art. Ihn hier zu halten
    # hiesse, seinen Filter lautlos wegzuwerfen; die Seite hat keine Tabelle, an der man
    # merken würde, dass er fehlt. Weitergereicht wird die ganze Adresse, nur `art` gilt
    # die der Route.
    if any(name in request.args for name in NUR_TAFEL_REGLER):
        werte = request.args.to_dict(flat=False)
        werte["art"] = [art]
        return redirect(url_for("taskforce_tafel") + "?"
                        + urllib.parse.urlencode(werte, doseq=True))
    zaehler = tf.angebote_zaehlen(art=art)
    # Eine Menge, eine Zählart – und zwar die von `sammelanlage.vorschlaege`, derselben
    # Funktion, die auch die Seite hinter dem Knopf füllt. Hier stand ein zweiter Filter
    # (`if z["id"] not in mit_profil`), der nachbesserte, was `vorschlaege` mit `aktiv=1`
    # zu viel lieferte. Zwei Nachbesserungen an einer Zahl sind zwei Zählweisen: das
    # Dashboard sagte „18 ohne", die Sammelanlage „19 ohne". Repariert ist es jetzt an
    # der Quelle. Gezählt werden durchgehend MENSCHEN, und wer unten mit pausiertem
    # Profil steht, steht nicht mehr oben als „kein Profil".
    profile = tf.uebersicht(art=art)
    mit_profil = {p["kunde_id"] for p in profile}
    alle_ohne = sammelanlage.vorschlaege(art=art)
    # Nicht alle auf einmal – dieselbe Grenze und derselbe Weg zum Rest wie auf dem
    # Stand eines Kunden (`STAND_OFFEN`). 19 Zeilen „kein Profil" standen vor
    # allem, was auf dieser Seite schon eingestellt ist.
    zeige_alle = bool(request.args.get("alle"))
    ohne_profil = alle_ohne if zeige_alle else alle_ohne[:DASHBOARD_OHNE_PROFIL]
    return render_template(
        "taskforce_dashboard.html", art=art,
        profile=profile, leute_mit=len(mit_profil),
        pausiert=len({p["kunde_id"] for p in profile if not p["aktiv"]}),
        # Dieselbe Funktion wie im Leerlaufband des Einstiegs: ohne laufende Maßnahme ist
        # „jede hat ein Profil" eine Aussage über niemanden.
        laufend_gesamt=len(aufgaben.laufende_massnahmen()),
        neu=tf.anzahl_neu(art=art), zaehler=zaehler,
        # Dieselbe Lesart wie in `tf.kunden_bilanz`: „angeschrieben" heisst mindestens
        # angeschrieben, „Antwort" schliesst das Ergebnis ein. Wer geantwortet hat, wurde
        # vorher angeschrieben – zaehlte man hier enger, saehe dieselbe Sache auf zwei
        # Seiten verschieden aus.
        angeschrieben=sum(zaehler.get(s, 0) for s in ("angeschrieben", "antwort", "erfolg")),
        mit_antwort=sum(zaehler.get(s, 0) for s in ("antwort", "erfolg")),
        wiedervorlage=tf.anzahl_wiedervorlage(art=art), wv_tage=tf.WIEDERVORLAGE_TAGE,
        neue=tf.neue_angebote(limit=8, art=art),
        # Laufende Maßnahmen, für die es in dieser Art gar kein Profil gibt – auch kein
        # pausiertes. Die Sammelanlage hinter dem Knopf zählt seit dem 21.09.2026
        # dieselbe Menge; wer pausiert hat, steht unten in der Liste dieser Seite mit
        # der Plakette „pausiert – sucht nicht" und nicht mehr hier oben.
        ohne_profil=ohne_profil, ohne_profil_gesamt=len(alle_ohne),
        zeige_alle=zeige_alle,
        quellen=tf.quellen_stand(art), arbeitszeiten=tf.ARBEITSZEITEN,
        f=_suchform(), lauf=betrieb.lauf_zustand(),
        # Die Rueckmeldung aus /taskforce/suchen/als-profil, wenn kein Mensch gewaehlt war:
        # sie kommt als `meldung` in der Adresse zurueck und muss hier sichtbar werden.
        meldung=request.args.get("meldung"))


# Die Regler der Tafel. Steht einer davon in der Adresse, ist die Tafel gemeint – ein
# Lesezeichen, ein Link aus dem CRM, ein Sprung aus der Aufgabenliste. Geprüft wird, ob
# der Schlüssel dasteht, nicht was er enthält: `?score=abc` kommt aus einem alten
# Lesezeichen und meint die Tafel, auch wenn die Zahl Unsinn ist.
TAFEL_REGLER = ("art", "kunde", "status", "q", "coach", "score", "match", "km", "tage",
                "sort", "quelle", "arbeitszeit", "gehalt", "quereinstieg", "miete",
                "zimmer", "flaeche", "meldung")


@app.route("/taskforce")
def taskforce_seite():
    """Der Einstieg – oder die Tafel, sobald ein Regler in der Adresse steht.

    `?art=job` und `?art=wohnung` gelten weiter: das CRM, Lesezeichen und geteilte Ansichten
    verlinken so. Sie sind dasselbe wie /taskforce/arbeit bzw. /taskforce/wohnung. Die volle
    Tafel ohne Regler steht unter /taskforce/tafel. Entfernt ist nichts – wer hierher kommt,
    sieht nur zuerst, was zu tun ist, statt zuerst, was gefunden wurde."""
    if not any(name in request.args for name in TAFEL_REGLER):
        return _tf_einstieg()
    art = request.args.get("art")
    return _tf_seite(modus=art if art in ("job", "wohnung") else None)


@app.route("/taskforce/tafel")
def taskforce_tafel():
    """Die volle Tafel – ohne dass man erst einen Regler setzen muss.

    Ohne `art` das Gesamtbild wie bisher. Mit `?art=job` oder `?art=wohnung` dieselbe
    Tafel, verengt auf eine Suche: der Platz, an dem die Treffer einer Art abgearbeitet
    werden. Bis zum 21.09.2026 warf diese Route `art` weg und zeigte immer alles – wer
    von /taskforce/arbeit aus auf die Tafel wechselte, bekam die Hälfte dazu, die er
    gerade nicht wollte."""
    art = request.args.get("art")
    return _tf_seite(modus=art if art in ("job", "wohnung") else None)


@app.route("/taskforce/arbeit")
def taskforce_arbeit():
    """Arbeitssuche: wonach gesucht wird, für wen, und was die Agenten gebracht haben.

    Der Arbeitsplatz der Arbeitssuche, nicht mehr die gefilterte Tafel. Die steht einen
    Klick weiter unter /taskforce/tafel?art=job und ist von hier aus verlinkt."""
    return _tf_dashboard("job")


@app.route("/taskforce/wohnung")
def taskforce_wohnung():
    """Wohnungssuche: Miete, Zimmer, Fläche – für wen gesucht wird und was liegt.

    Gegenstück zur Arbeitssuche. Die Tafel dieser Art steht unter
    /taskforce/tafel?art=wohnung."""
    return _tf_dashboard("wohnung")


TF_SEITE = {"job": "taskforce_arbeit", "wohnung": "taskforce_wohnung"}

# Wie viele offene Angebote die Kundenseite auf einmal zeigt. Eine Bildschirmgrenze,
# keine Bestandszahl - mehr steht hinter „alle anzeigen".
STAND_OFFEN = 25


def _suchform():
    """Die Eingaben der Direktsuche aus der Adresse lesen – und zurueck in die Adresse.

    Jede Suche steht damit vollstaendig im Link: sie laesst sich weiterschicken, als
    Lesezeichen ablegen und vom CRM genauso aufrufen."""
    z = lambda n, typ=int: request.args.get(n, type=typ)
    f = {"was": (request.args.get("was") or "").strip(),
         "wo": (request.args.get("wo") or "").strip(),
         "km": z("km") or 25,
         "arbeitszeit": request.args.get("arbeitszeit") or None,
         "gehalt": z("gehalt"), "miete": z("miete"),
         "zimmer": z("zimmer", float), "flaeche": z("flaeche"),
         "quereinstieg": bool(request.args.get("quereinstieg")),
         "wbs": bool(request.args.get("wbs")),
         "quellen": request.args.getlist("quelle") or None}
    f["fein"] = len([s for s in ("arbeitszeit", "gehalt", "miete", "zimmer", "flaeche",
                                 "quereinstieg", "wbs") if f.get(s)])
    return f


def _suchprofil(art, f):
    """Aus den Eingaben ein Profil bauen, wie es die Adapter erwarten."""
    # Die Schluessel heissen genau so, wie `taskforce.job_filter` und
    # `taskforce.JOB_KRITERIEN` sie lesen – nicht so, wie das Formularfeld heisst.
    # `nur_quereinstieg` stand hier als `quereinstieg`, und damit filterte der
    # Bildschirm nicht: gemessen an zwei Treffern kamen mit `quereinstieg` beide
    # durch (0 aussortiert), mit `nur_quereinstieg` einer (1 aussortiert). Das
    # gespeicherte Profil filterte richtig, weil `_such_regler` den richtigen
    # Schluessel schreibt – die Direktsuche zeigte also mehr an, als sie zusagte.
    kriterien = {}
    if f.get("gehalt"):
        kriterien["min_gehalt"] = f["gehalt"]
    if f.get("quereinstieg"):
        kriterien["nur_quereinstieg"] = True
    if f.get("wbs"):
        kriterien["wbs"] = True
    if f.get("arbeitszeit"):
        kriterien["arbeitszeiten"] = [f["arbeitszeit"]]
    return tf.suchspalte(art=art, begriffe=f["was"], ort=f["wo"], umkreis_km=f["km"],
                         arbeitszeit=f.get("arbeitszeit"), max_miete=f.get("miete"),
                         min_zimmer=f.get("zimmer"), min_flaeche=f.get("flaeche"),
                         kriterien=kriterien)


def _suchen(art, f):
    """Eine Suche ausfuehren – oder zwei, wenn beides gefragt ist.

    „Beides" ist kein dritter Suchlauf, sondern die zwei vorhandenen nacheinander.
    Sortiert wird am Ende ueber beide Haelften – wer beides sucht, will die beste
    Wohnung neben der besten Stelle sehen, nicht erst 200 Stellen.

    **Nacheinander, nicht gleichzeitig – hier stand das Gegenteil.** Innerhalb einer
    Art laufen die Portale parallel (`tf.direktsuche`), die beiden Arten aber nicht:
    diese Schleife wartet die erste ab, bevor sie die zweite startet. Gemessen am
    22.09.2026: 6,7 s = 4,2 s Arbeit + 2,5 s Wohnung. Das zu aendern ist eine eigene
    Runde; bis dahin steht hier, was wirklich passiert."""
    if art != "beides":
        return tf.direktsuche(_suchprofil(art, f), quellen=f["quellen"])
    treffer, meldungen = [], []
    for eine in ("job", "wohnung"):
        t, m = tf.direktsuche(_suchprofil(eine, f), quellen=f["quellen"], grenze=100)
        treffer += t
        meldungen += m
    treffer.sort(key=lambda x: (-(x.get("score") or 0), x.get("titel") or ""))
    return treffer, meldungen


@app.route("/taskforce/suchen")
def taskforce_suchen():
    """Beruf, Ort, Umkreis – Treffer. Ohne Kunde, ohne angelegtes Profil.

    Der Weg ueber Kunde → Suchprofil → Agentenlauf bleibt: er ist der taegliche Betrieb.
    Diese Seite ist das Gegenstueck dafuer, dass jemand einfach nachsehen will."""
    art = request.args.get("art")
    art = art if art in ("job", "wohnung", "beides") else "job"
    f = _suchform()
    gesucht = bool(request.args.get("was") or request.args.get("wo"))
    treffer, meldungen, dauer = [], [], 0.0
    if gesucht:
        begonnen = time.time()
        treffer, meldungen = _suchen(art, f)
        dauer = time.time() - begonnen
        # Hier lag die Trefferliste bis zum 21.09.2026 in der Sitzung. Flask legt die
        # Sitzung in ein Cookie, und ein Cookie fasst 4.096 Bytes; gemessen wurden
        # 23.094 Bytes fuer eine gewoehnliche Suche. Der Browser wirft ein zu grosses
        # Cookie stumm weg – „uebernehmen" fand danach NIE etwas wieder, nicht erst
        # bei vielen Treffern. Die Treffer reisen jetzt im Formular mit (siehe
        # `taskforce_suchen.html`); ein POST-Rumpf kennt die 4-KB-Grenze nicht.
        # Die Meldungen gehören ins Protokoll, nicht nur auf den Bildschirm: darin
        # steht, welche Wartestufe bei Indeed getragen hat und wie viele Treffer die
        # Grenze abgeschnitten hat. Beides beantwortet später eine Frage, die man dem
        # Bildschirm von gestern nicht mehr stellen kann.
        notieren(f"Direktsuche: {f['was'] or '—'} in {f['wo'] or 'Köln'}", "taskforce", None,
                 f"{len(treffer)} Treffer in {dauer:.1f} s"
                 + ("; " + " · ".join(meldungen) if meldungen else ""))
    return render_template("taskforce_suchen.html", art=art, f=f, gesucht=gesucht,
                           treffer=treffer, meldungen=meldungen, dauer=dauer,
                           arbeitszeiten=tf.ARBEITSZEITEN,
                           quellen=tf.quellen_stand(None if art == "beides" else art),
                           profile=tf.uebersicht(art=None if art == "beides" else art),
                           # Die Rückmeldung aus „übernehmen", wenn nichts abgelegt
                           # wurde. Sie kam schon immer als `meldung` in der Adresse
                           # zurück – gelesen hat sie hier bis zum 21.09.2026 niemand.
                           meldung=request.args.get("meldung"))


# Die Felder, die `tf._ablegen` und `tf._score` aus einem Treffer lesen, mit dem Typ,
# den sie vertragen. Was nicht hier steht, wandert gar nicht erst in die Datenbank.
TREFFER_TEXT = ("quelle", "extern_id", "titel", "anbieter", "ort", "url",
                "veroeffentlicht", "beschreibung")
TREFFER_ZAHL = ("entfernung_km", "score")

# Die Schlüssel in `zusatz`, die weiter unten als TEXT gelesen werden – mit der Stelle,
# die es tut. Sie vertragen nur Text oder nichts; eine Zahl, ein Wahrheitswert oder
# eine Liste fällt dort mit `AttributeError` um, mitten in `_ablegen` und damit mitten
# in der Transaktion – es geht dann ein ganzer Stapel verloren, nicht nur der
# vergiftete Satz.
#   suchbegriff   `tf._score`      .casefold()
#   arbeitszeit   `tf._score`      .casefold()
#   preis         `tf._score`      .split(",")
# Alles übrige in `zusatz` wird über `str(…)`, `_zahl_aus` oder blosse Wahrheit
# gelesen und darf deshalb auch `bool` sein – `quereinstieg` IST einer.
ZUSATZ_TEXT = ("suchbegriff", "arbeitszeit", "preis")

# Wie lang so ein Wert werden darf. `tf._score` macht aus `preis` mit `int(…)` eine
# Zahl, und **Python 3.12 wirft bei mehr als 4.300 Ziffern** – ungefangen, mitten in
# `_ablegen` und damit mitten in der Transaktion: `db.offen()` committet nur bei
# sauberem Durchlauf, also fällt der GANZE Stapel zurück. Gemessen mit rund 5 kB
# Nutzlast, weit unter der 500-kB-Grenze des Formulars.
#
# 200 ist gemessen, nicht geraten: der längste Wert dieser drei Schlüssel im echten
# Bestand hat 17 Zeichen („Vollzeit, Schicht"), der längste Wert in `zusatz`
# überhaupt 38; der Tester hat 561 echte Treffer aus sechs Portalen gemessen, der
# längste Wert dort hatte 24 Zeichen. Die Grenze gilt **nur für diese drei**, weil
# eine zu enge Grenze genau die Sorte Verlust erzeugt, die heute schon 146 Treffer
# gekostet hat.
#
# ACHTUNG, hier stand bis zum 22.09.2026 „alles andere in `zusatz` bleibt unbegrenzt,
# weil dort kein `int(…)` wartet". Das ist FALSCH. In `_abgleich_wohnung`
# (`taskforce.py:2358` und `:2361`) warten zwei weitere: `warmmiete` und `etage`.
# Beide sind hier NICHT begrenzt. Der Schaden ist milder – `app.py` und
# `taskforce.py:2414` fangen dort je Satz, es gibt also keine 500er-Seite und keinen
# Stapelverlust – aber der Satz bekommt `abgleich={"fehler": …}`, und
# `abgleich_profil` sucht nur `WHERE abgleich IS NULL`: die Zeile wird nie wieder
# angefasst. Wer `zusatz` um einen Schlüssel erweitert, der irgendwo in eine Zahl
# umgewandelt wird, gehört in `ZUSATZ_TEXT` – nicht weil hier eine Regel steht,
# sondern weil dort ein `int(…)` wartet.
ZUSATZ_TEXT_MAX = 200


def _einfacher_wert(w):
    """Text, endliche Zahl, Wahrheitswert oder nichts – mehr verträgt keine Stelle,
    die das später anfasst.

    `Infinity` und `NaN` sind gültiges JSON für Pythons `json`-Leser. `inf` landete
    als `inf` in `entfernung_km`, und die Tafel schrieb danach „inf km"; `NaN` wurde
    still zu NULL. Beides ist kein Messwert, sondern ein Loch."""
    if w is None or isinstance(w, (str, bool)):
        return True
    return isinstance(w, (int, float)) and math.isfinite(w)


def _zahl_fuer_spalte(wert):
    """Eine Zahl, die SQLite in ihre Spalte schreiben kann – und die etwas bedeutet.

    Drei Dinge, jedes einzeln gemessen:

    **Text ist keine Zahl.** `"abc"` kam durch und landete in einer INTEGER-Spalte: die
    Tafel schrieb danach „abc km", und `entfernung_km <= ?` traf die Zeile nie wieder.

    **Grenze bei 64 Bit.** Ein Python-`int` ist unbegrenzt, `math.isfinite(10**30)` ist
    `True`, und `tf._ablegen` bindet den Wert ohne Umweg: `sqlite3` wirft dann
    `OverflowError: Python int too large to convert to SQLite INTEGER`. Das ist kein
    verlorener Satz, sondern ein verlorener STAPEL – `db.offen()` committet nur bei
    sauberem Durchlauf, also sind auch die Zeilen weg, die davor schon eingefügt waren.
    Genau der Schaden, gegen den dieser Wächter geschrieben ist. Abstürzen lässt
    `sqlite3` nur an ganzen Zahlen – `1e308` bindet es klaglos –, und genau die
    liefert `json.loads`. **Die Grenze unten gilt trotzdem für beide Typen:** auch
    `1e308` wird abgewiesen, weil eine Entfernung von 10^308 km nichts bedeutet.
    Kein Portal liefert so etwas; es kostet keinen Treffer. `SQLITE_MAX` steht seit
    jeher in dieser Datei und wird an zwei anderen Stellen schon angewandt; hier
    fehlte sie.

    **Nicht unter null.** Weder eine Entfernung noch eine Relevanz kann negativ sein.
    `{"entfernung_km": -5}` stürzt nichts ab, aber `tf._score` gibt dafür +2 („nah"),
    jeder `max_km`-Filter nimmt die Zeile mit, und die Sortierung nach Nähe stellt sie
    vor jeden echten Treffer. Ein Satz, der sich selbst nach oben nagelt."""
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return False
    if not math.isfinite(wert):
        return False
    return 0 <= wert <= SQLITE_MAX


def _treffer_sauber(satz):
    """Ist das ein Treffer, wie ihn die Suchseite gebaut hat – oder etwas anderes?

    `quelle` und `extern_id` bleiben Pflicht und müssen Text sein – `tf._ablegen` liest
    beide ohne `get`, und sie sind der Schlüssel gegen Dubletten. Alle übrigen
    Textfelder dürfen `None` sein; siehe unten.

    **Geprüft wird bis in die zweite Ebene.** Dass `zusatz` ein Wörterbuch IST, reicht
    nicht: `tf._score` liest daraus `suchbegriff` mit `.casefold()` und `preis` mit
    `.split()`. Drei gemessene 500er, alle mit gültigem JSON und alle am alten
    Wächter vorbei:
        {"zusatz": {"suchbegriff": {"x": 1}}}  → dict hat kein .casefold (job)
        {"zusatz": {"preis": {"x": 1}}}        → dict hat kein .split   (wohnung)
        {"zusatz": {"preis": [1, 2]}}          → list hat kein .split   (wohnung)
    Der erste Fall ist unbedingt: die Zeile läuft für jedes Jobprofil.

    **Eine Liste ist nur für `arbeitszeit_codes` erlaubt**, und nur mit einfachen
    Werten darin. Das ist das einzige Feld im ganzen Bestand, das eine Liste trägt
    (`jobs_ba`), und `job_filter` baut daraus ein `set` – ein Wörterbuch darin wäre
    nicht hashbar. Überall sonst wäre eine Liste ein Absturz: `{"preis": [1, 2]}`
    kam durch einen Wert-nur-Test hindurch und fiel dann in `_score` auf `.split()`."""
    if not isinstance(satz, dict):
        return False
    for pflicht in ("quelle", "extern_id"):
        if not (isinstance(satz.get(pflicht), str) and satz[pflicht].strip()):
            return False
    # Und `quelle` muss eine Quelle sein, die dieses Haus kennt. Ein frei erfundener
    # Schlüssel wurde sonst mit abgelegt, und auf der Tafel stünde danach eine
    # Plakette, hinter der kein Portal steckt.
    #
    # Das gilt für DIESEN Weg – einen Treffer, der aus einer Portalsuche stammt. Es
    # heisst nicht, dass jede Zeile in `tf_angebot` einen Schlüssel aus `tf.QUELLEN`
    # trägt: `tf.wohnung_link_eintragen` legt von Hand eingetragene Exposés unter
    # `wohnung.link` ab, und der steht dort mit Absicht nicht drin – es ist kein
    # Portal, das man abfragen kann.
    if satz["quelle"] not in tf.QUELLEN:
        return False
    # **`None` ist erlaubt, und zwar weil unsere eigenen Adapter es erzeugen.**
    # `jobs_kleinanzeigen` und `wohnung_kleinanzeigen` setzen `veroeffentlicht`
    # ausdruecklich auf `None` – bei jedem Treffer; meinestadt ebenso; `anbieter`
    # fehlt bei anonymen Anzeigen. Die Suchseite gibt den Treffer vollstaendig weiter
    # (`{{ x|tojson }}`), also steht `"veroeffentlicht": null` im Formular.
    #
    # Hier stand `isinstance(satz[f], str)` ohne den `None`-Fall. Gemessen an echten
    # Treffern einer Koelner Suche am 22.09.2026, wie viele durch diesen Waechter
    # kamen: BA 5/5, StepStone 50/50, **meinestadt 0/40, Kleinanzeigen 0/54,
    # Wohnungen 0/52** – 146 von 201 Treffern waren unuebernehmbar, und die Meldung
    # sagte dem Nutzer, er solle die Suche noch einmal starten. Eine Schleife, die
    # nichts aendern konnte.
    #
    # Abgewehrt wird weiterhin alles, was KEIN Text und nicht `None` ist – Listen,
    # Woerterbuecher, Zahlen an Textfeldern.
    if any(f in satz and satz[f] is not None and not isinstance(satz[f], str)
           for f in TREFFER_TEXT):
        return False
    if any(f in satz and satz[f] is not None and not _zahl_fuer_spalte(satz[f])
           for f in TREFFER_ZAHL):
        return False
    zusatz = satz.get("zusatz")
    if "zusatz" in satz:
        if not isinstance(zusatz, dict):
            return False
        for name, wert in zusatz.items():
            if isinstance(wert, list):
                if name != "arbeitszeit_codes":
                    return False
                if not all(isinstance(x, str) for x in wert):
                    return False
            elif name in ZUSATZ_TEXT:
                if wert is not None and not isinstance(wert, str):
                    return False
                if wert is not None and len(wert) > ZUSATZ_TEXT_MAX:
                    return False
            elif not _einfacher_wert(wert):
                return False
    return True


@app.route("/taskforce/suchen/uebernehmen", methods=["POST"])
def taskforce_suche_uebernehmen():
    """Ausgewaehlte Treffer auf die Tafel eines Suchprofils legen.

    Die Naht zum CRM: heute waehlt ein Mensch das Profil, spaeter liefert das CRM den
    Kunden. Was uebergeben wird, ist in beiden Faellen dieselbe Liste."""
    pid = request.form.get("profil", type=int)
    # Dieselbe Regel wie in `_zurueck` und `_mit_meldung`: eine Rücksprungadresse, die
    # nicht mit `/` beginnt, ist keine Adresse dieses Hauses. Ohne die Prüfung war das
    # ein offener Redirect – ein Formularfeld reicht, um jemanden nach dem Klick auf
    # „übernehmen" irgendwohin zu schicken.
    zurueck = _zurueck(url_for("taskforce_suchen"))
    # Der gewaehlte Treffer kommt aus dem Formular, nicht aus der Sitzung: neben jedem
    # Haekchen steht sein Datensatz als `t<Nummer>`. Damit ist der Weg zustandslos –
    # zwei Menschen an einem gemeinsamen Zugang ueberschreiben einander nicht mehr die
    # Suche, und ein Neuladen nach zwanzig Minuten traegt immer noch dieselbe Liste.
    #
    # Was hier hereinkommt, hat der Browser geschickt und ist darum nichts, worauf man
    # blind zugreift. Geprüft wird der TYP, nicht nur der Wahrheitswert – vier
    # gemessene Wege in eine 500er-Seite, alle mit gueltigem JSON:
    #   {"quelle": {"x": 1}}        ein dict ist wahr und kam am alten Guard vorbei,
    #                               `sqlite3` kann es nicht binden
    #   {"titel": ["a", "b"]}       dito
    #   {"entfernung_km": {"a": 1}} dito
    #   {"zusatz": ["a"]}           `_score` ruft darauf `.get` auf → AttributeError
    # Also: Text bleibt Text, Zahl bleibt Zahl, `zusatz` bleibt ein Woerterbuch – und
    # die drei Schluessel darin, die als Text gelesen werden, bleiben Text (ZUSATZ_TEXT).
    # Alles andere faellt still weg – lieber ein Treffer weniger als eine 500er-Seite
    # mitten im Ablegen.
    gewaehlt = []
    angehakt = request.form.getlist("treffer", type=int)
    for i in angehakt:
        roh = request.form.get("t%d" % i)
        if not roh:
            continue
        try:
            satz = json.loads(roh)
        # `RecursionError` ist kein `ValueError`: tief verschachteltes JSON bricht im
        # C-Scanner ab, nicht mit einem Formatfehler. Gemessen mit CPython 3.12.10:
        # Tiefe 2.000 geht durch, Tiefe 5.000 wirft – bei 10 kB Nutzlast. Hier geht
        # nichts verloren, der Abbruch liegt vor `_ablegen`; aber die Zusage „lieber
        # ein Treffer weniger als eine 500er-Seite" gilt auch für diesen Weg.
        except (ValueError, RecursionError):
            continue
        if _treffer_sauber(satz):
            gewaehlt.append(satz)
    # Was schiefging, muss benannt werden – und zwar unterschieden. „Nichts angehakt
    # oder kein Profil gewählt" stand hier für drei verschiedene Lagen, und die
    # dritte (die Treffer kamen nicht mit) sah aus wie ein Bedienfehler. Bis zum
    # 21.09.2026 war sie ausserdem gar nicht zu sehen: die Suchseite las `meldung`
    # nicht aus der Adresse. Der Klick auf „übernehmen" endete auf derselben Seite,
    # ohne Meldung, ohne Fehler, ohne Zeile in der Datenbank.
    if not pid or not gewaehlt:
        if not pid:
            grund = "Kein Suchprofil gewählt – es wurde nichts übernommen."
        elif not angehakt:
            grund = "Nichts angehakt – es wurde nichts übernommen."
        else:
            grund = (f"Die {len(angehakt)} angehakten Treffer kamen nicht mit."
                     " Bitte die Suche noch einmal starten und dann übernehmen.")
        return redirect(zurueck + ("&" if "?" in zurueck else "?")
                        + "meldung=" + urllib.parse.quote(grund))
    neu = tf._ablegen(pid, gewaehlt)
    notieren(f"{neu} Treffer aus der Direktsuche übernommen", "taskforce", None, str(pid))
    return redirect(url_for("taskforce_profil", pid=pid,
                            meldung=f"{neu} von {len(gewaehlt)} Treffern übernommen."
                                    f" Der Rest lag schon auf der Tafel."))


# Die Felder der Suchmaske, die im Profil nicht als Spalte liegen. Beruf, Ort, Umkreis,
# Arbeitszeit, Miete, Zimmer und Fläche haben eigene Spalten; Gehalt, Quereinstieg und WBS
# stehen als JSON in `tf_profil.kriterien`, die Quellenwahl in `tf_profil.quellen`.
SUCHE_ALS_KRITERIUM = {"gehalt": "k_min_gehalt", "quereinstieg": "k_nur_quereinstieg",
                       "wbs": "k_wbs"}


def _such_regler(formular, art):
    """Die übrigen Regler der Suchmaske in die Form bringen, die `profil_speichern` liest.

    Ohne das stand neben dem Knopf „mit genau diesen Reglern", und das Profil hatte dann
    kein Mindestgehalt, keinen Quereinstieg, kein WBS und alle acht Portale – der Agent
    suchte also etwas anderes als der Mensch, der die Suche gut fand.

    Die Quellenwahl wird nur festgeschrieben, wenn sie wirklich einengt. Sind alle Portale
    angehakt – der Normalfall, weil sie alle vorausgewählt sind –, bleibt das Feld leer und
    heisst weiter „alle". Eine ausgeschriebene Liste würde das Profil sonst auf die heute
    bekannten Quellen festnageln; ein Portal, das nächsten Monat dazukommt, bliebe für
    jedes vorher angelegte Profil stumm.

    **Verglichen wird gegen die VERBUNDENEN Quellen, nicht gegen alle.** Genau das
    war der Fehler: eine nicht verbundene Quelle trägt im Formular `disabled`, der
    Browser schickt sie nicht mit – `gewaehlt` kannte sie also nie, `moeglich` zählte
    sie aber. Damit war `passend < moeglich` IMMER wahr, und jedes hier entstandene
    Profil wurde auf die heute verbundenen Portale festgenagelt, ohne dass jemand
    einen Haken angefasst hatte. Gemessen am 21.09.2026 (drei Quellen nicht
    verbunden): `quellen='wohnung.kleinanzeigen'` für jedes Wohnprofil. Sobald der
    ImmoScout-Zugang steht, bliebe jedes dieser Profile dafür dauerhaft stumm – und
    niemand sähe, warum."""
    felder = {}
    for aus_suche, ins_profil in SUCHE_ALS_KRITERIUM.items():
        wert = (formular.get(aus_suche) or "").strip()
        if wert:
            felder[ins_profil] = wert
    gewaehlt = {q for q in tf._mehrfach(formular, "quelle") if q}
    moeglich = {q["schluessel"] for q in tf.quellen_stand(art) if q["bereit"]}
    passend = gewaehlt & moeglich
    if passend and passend < moeglich:
        felder["quellen"] = sorted(passend)
    return felder


def _profil_aus_suche(kid, art, formular, doppelt=False):
    """Aus den Reglern einer Suche ein Suchprofil bauen. Eine Stelle, damit die
    Einzel- und die Doppelanlage nie auseinanderlaufen.

    `doppelt` heisst: aus einer Suche entstehen zwei Profile. Dann duerfen die Suchbegriffe
    nicht in die Wohnungshaelfte wandern – „Lagerhelfer, Produktionshelfer" als Name eines
    Wohnprofils ist nicht nur haesslich, es fuehrt in drei Wochen jemanden in die Irre, der
    wissen will, wonach dort eigentlich gesucht wird."""
    wohnsuche = art == "wohnung" and doppelt
    return tf.profil_speichern({
        "kunde_id": kid, "art": art, "standort": db.STANDORT_STANDARD,
        "titel": ("Wohnungssuche" if wohnsuche else
                  (formular.get("titel") or "").strip()
                  or (formular.get("was") or "").strip()
                  or ("Arbeitssuche" if art == "job" else "Wohnungssuche")),
        "suchbegriffe": "" if wohnsuche else (formular.get("was") or ""),
        "ort": formular.get("wo") or "Köln",
        "umkreis_km": formular.get("km") or 25,
        "max_miete": formular.get("miete") or None,
        "min_zimmer": formular.get("zimmer") or None,
        "min_flaeche": formular.get("flaeche") or None,
        "arbeitszeit": formular.get("arbeitszeit") or None,
        # Was keine eigene Spalte hat: Gehalt, Quereinstieg, WBS, Quellenwahl. Ein
        # Kriterium, das zur anderen Art gehört, lässt `kriterien_aus_formular` liegen –
        # es liest nur die Liste der jeweiligen Art.
        **_such_regler(formular, art),
    })


def _zurueck_mit_suche(zurueck, formular):
    """Die Rücksprungadresse mit den eingetippten Werten, damit eine Rückmeldung sie
    nicht wegwirft.

    Das Dashboard baut seine Maske aus `request.args`. Kommt die Meldung „Kein Kunde
    gewählt" an eine Adresse ohne diese Werte zurück, steht die Maske wieder leer da und
    alles muss neu getippt werden – auf `/taskforce/suchen` passiert das nicht, weil dort
    die Suche ohnehin in der Adresse steht. Bereits vorhandene Regler werden nicht
    überschrieben: die Adresse gilt vor dem Formular."""
    teile = urllib.parse.urlsplit(zurueck)
    werte = urllib.parse.parse_qs(teile.query, keep_blank_values=True)
    for feld in ("was", "wo", "km", "arbeitszeit", "gehalt", "miete", "zimmer", "flaeche",
                 "quereinstieg", "wbs", "titel"):
        wert = (formular.get(feld) or "").strip()
        if wert and feld not in werte:
            werte[feld] = [wert]
    quellen = [q for q in tf._mehrfach(formular, "quelle") if q]
    if quellen and "quelle" not in werte:
        werte["quelle"] = quellen
    return urllib.parse.urlunsplit((
        teile.scheme, teile.netloc, teile.path,
        urllib.parse.urlencode(werte, doseq=True), teile.fragment))


@app.route("/taskforce/suchen/als-profil", methods=["POST"])
def taskforce_suche_als_profil():
    """Aus einer Suche, die etwas taugt, ein stehendes Suchprofil machen.

    Der fehlende Schritt zwischen „ich sehe nach" und „der Agent sucht das taeglich".
    Ohne ihn musste man die Begriffe ein zweites Mal eintippen – auf einer anderen Seite,
    in einem anderen Formular, und wehe man tippte anders."""
    kid = request.form.get("kunde", type=int)
    art = request.form.get("art")
    art = art if art in ("job", "wohnung", "beides") else "job"
    zurueck = _zurueck(url_for("taskforce_suchen"))
    if not kid or not tf.kunden_info(kid):
        return _mit_meldung(_zurueck_mit_suche(zurueck, request.form),
                            "Kein Kunde gewählt – es wurde nichts angelegt.")
    # „Beides" ist keine Art, sondern zwei Auftraege. Also werden zwei Profile daraus –
    # sonst muesste der Mensch dieselbe Suche zweimal machen, um beides festzuhalten.
    if art == "beides":
        angelegt = [_profil_aus_suche(kid, eine, request.form, doppelt=True)
                    for eine in ("job", "wohnung")]
        notieren("Zwei Suchprofile aus einer Suche angelegt", "taskforce", str(angelegt[0]))
        return redirect(url_for("taskforce_stand", kid=kid,
                                meldung="Zwei Suchprofile angelegt, für Arbeit und für Wohnung."
                                        " Die Agenten suchen ab jetzt beides von selbst."))
    # Dieselbe Stelle wie bei der Doppelanlage. Hier stand bis zum 21.09.2026 eine zweite,
    # wortgleiche Fassung desselben Wörterbuchs – zwei Stellen, die dasselbe bauen, laufen
    # beim nächsten Regler auseinander, und genau das war passiert: die Doppelanlage kannte
    # Felder, die die Einzelanlage nicht mitnahm.
    pid = _profil_aus_suche(kid, art, request.form)
    notieren("Suchprofil aus einer Suche angelegt", "taskforce", str(pid),
             tf.profil(pid).get("titel"))
    return redirect(url_for("taskforce_profil", pid=pid,
                            meldung="Profil angelegt. Der Agent sucht das ab jetzt von selbst – "
                                    "unten laufen lassen für den ersten Durchgang."))


@app.route("/taskforce/api/suche")
def taskforce_api_suche():
    """Die Direktsuche als Schnittstelle – die Naht, an der das CRM andockt.

    Kein Kunde, keine Ablage: Frage rein, Treffer raus. Das CRM schickt Beruf, Ort und
    Umkreis und bekommt Quelle, Fremd-ID, Titel, Anbieter, Ort und Relevanz zurueck."""
    from flask import jsonify
    art = request.args.get("art")
    art = art if art in ("job", "wohnung", "beides") else "job"
    f = _suchform()
    treffer, meldungen = _suchen(art, f)
    return jsonify(art=art, suche={k: f[k] for k in ("was", "wo", "km")},
                   anzahl=len(treffer), meldungen=meldungen, treffer=treffer)



@app.route("/taskforce/lauf", methods=["POST"])
def taskforce_lauf():
    """Stößt den Lauf an und kommt sofort zurück – die Portale brauchen bis zu zwei Minuten.

    Aus der Arbeitssuche laufen nur die Jobportale, aus der Wohnungssuche nur die
    Wohnungsquellen. Das halbiert die Wartezeit und fragt keine Portale ohne Anlass."""
    art = request.form.get("art") or None
    if art not in ("job", "wohnung"):
        art = None
    # Ohne Rücksprungadresse zurück auf die Seite, die zu dieser Art gehört: mit `art` auf
    # deren Arbeitsplatz (seit dem 21.09.2026 steht dort die Kostprobe der neuen Treffer
    # und die Laufanzeige), ohne `art` auf die Tafel. Nicht auf den Einstieg – die Meldung
    # gehört dorthin, wo man gleich nachsieht, was der Lauf gebracht hat. Wer von der Tafel
    # aus startet, schickt seine Adresse als `zurueck` mit und bleibt, wo er war.
    ziel = TF_SEITE.get(art, "taskforce_tafel")
    woran = {"job": "Arbeits-Agenten", "wohnung": "Wohnungs-Agenten"}.get(art, "Agentenlauf")
    if betrieb.lauf_starten(art):
        notieren(f"{woran} gestartet", "taskforce")
        meldung = f"{woran}: der Lauf ist gestartet und arbeitet im Hintergrund. Diese Seite zeigt oben, wann er fertig ist."
    else:
        meldung = "Es läuft bereits ein Durchgang – der zweite würde dieselben Portale doppelt fragen."
    # Wer vom Einstieg aus startet, kommt auf den Einstieg zurück: dort steht das
    # Leerlaufband, das der Lauf verändern soll, und dessen Laufanzeige lädt sich alle
    # 15 Sekunden selbst nach. Ohne meldung= in der Adresse – der Anzeigebalken dort sagt
    # dasselbe, und `meldung` ist ein Reglername der Tafel, führte also wieder zur Tafel.
    zurueck = request.form.get("zurueck") or ""
    if zurueck.startswith("/"):
        return redirect(zurueck)
    return redirect(url_for(ziel, meldung=meldung))


@app.route("/taskforce/profile-anlegen")
def sammelanlage_seite():
    """Suchprofile für alle laufenden Kunden auf einmal – der Schritt, der bisher fehlte."""
    art = request.args.get("art") or "job"
    return render_template("sammelanlage.html", art=art,
                           zeilen=sammelanlage.vorschlaege(art=art),
                           meldung=request.args.get("meldung"),
                           fehler=request.args.get("fehler"))


@app.route("/taskforce/profile-anlegen", methods=["POST"])
def sammelanlage_anlegen():
    art = request.form.get("art") or "job"
    umkreis = request.form.get("umkreis", 25, type=int) or 25
    auswahl = sammelanlage.aus_formular(request.form, art)
    if not auswahl:
        return redirect(url_for("sammelanlage_seite", art=art,
                                fehler="Nichts angehakt – es wurde nichts angelegt."))
    angelegt, weg = sammelanlage.anlegen(auswahl, art, umkreis)
    notieren(f"{len(angelegt)} Suchprofile angelegt", "taskforce", None,
             ", ".join(b for _, _, b in angelegt)[:200])
    text = f"{len(angelegt)} Profile angelegt."
    if weg:
        text += f" {len(weg)} übersprungen: " + "; ".join(f"{g}" for _, g in weg[:3])
    return redirect(url_for("sammelanlage_seite", art=art, meldung=text))


@app.route("/taskforce/kunde/<int:kid>/stand")
def taskforce_stand(kid):
    """Die Seite, die ein Coach fuer einen Menschen aufschlaegt.

    Drei Fragen in dieser Reihenfolge: Wie steht es, was liegt jetzt an, was lief bisher.
    Die Tafel zeigt alle Kunden nebeneinander – gut, um zu sehen, wo etwas liegt, aber zum
    Arbeiten an einer Person taugt sie nicht: filtern, scrollen, Faden verlieren. Hier steht
    alles zu einem Menschen beieinander, mit den Knoepfen daneben.

    `?art=job` oder `?art=wohnung` verengt die Seite auf eine Suche – so kommt man aus dem
    Dashboard einer Art hierher und sieht genau das, woran man gerade arbeitet. Ohne `art`
    bleibt alles wie bisher: beide Haelften. Verengt steht ein sichtbarer Link zur vollen
    Fassung daneben, damit die andere Haelfte nicht lautlos fehlt."""
    kunde = tf.kunden_info(kid)
    if not kunde:
        abort(404)
    art = request.args.get("art")
    art = art if art in ("job", "wohnung") else None
    # Die besten zuerst, und nicht alle auf einmal.
    #
    # Ohne Grenze war diese Seite fuer den einen Menschen mit echten Daten 123.750 Zeichen
    # lang - dieselbe Wand, die der Einstieg gerade von der Tafel genommen hat, nur eine
    # Seite weiter. Und sie trifft ausgerechnet das Ziel, auf das der Einstieg am haeufigsten
    # verweist. Wer 618 Stellen am Stueck sieht, schreibt keine an; wer 25 sortierte sieht,
    # arbeitet sie durch. Der Rest ist einen Klick entfernt, nicht verschwunden.
    alle_offen = [a for a in tf.neue_angebote(limit=400, kunde_id=kid, status=None, art=art)
                  if a["status"] in ("neu", "gesehen")]
    zeige_alle = bool(request.args.get("alle"))
    offen = alle_offen if zeige_alle else alle_offen[:STAND_OFFEN]
    # Selbst suchen, ohne die Begriffe neu zu tippen: das Profil dieses Menschen fuellt
    # die Suchmaske vor. Wer am Telefon schnell nachsehen will, ist dann einen Klick weit weg.
    # Die gewaehlte Art bestimmt, welches Profil die Suchmaske fuellt. Ohne sie bleibt es
    # beim Jobprofil wie bisher – auf `stand?art=wohnung` oeffnete der Knopf sonst eine
    # Arbeitssuche, auf einer Seite, die ausdruecklich nur die Wohnungssuche zeigt.
    profile = tf.profile_von(kid)
    erstes = (next((p for p in profile if p["art"] == (art or "job")), None)
              or next((p for p in profile if p["art"] == "job"), None)
              or (profile[0] if profile else None))
    suche = {"art": (erstes or {}).get("art") or "job",
             "was": (erstes or {}).get("suchbegriffe") or "",
             "wo": (erstes or {}).get("ort") or kunde.get("stadt") or "Köln",
             "km": (erstes or {}).get("umkreis_km") or 25}
    return render_template(
        "taskforce_stand.html", kunde=kunde, suche=suche, nur_art=art,
        bilanz=tf.kunden_bilanz(kid), offen=offen,
        offen_gesamt=len(alle_offen), zeige_alle=zeige_alle,
        verlauf=tf.kunden_nachweis(kid, art=art), status_text=tf.STATUS_TEXT,
        leute=_tf_leute(), meldung=request.args.get("meldung"))


@app.route("/taskforce/kunde/<int:kid>/stand.csv")
def taskforce_stand_csv(kid):
    """Derselbe Stand als CSV – fuer die Akte und fuer alle, die lieber rechnen.

    „Derselbe" heisst auch: dieselbe Verengung. `?art=` wirkt hier wie auf dem Bildschirm –
    sonst sagt die Seite „nur die Arbeitssuche" und die Datei daneben enthaelt beides. Im
    QM entscheidet die Datei, nicht der Bildschirm, von dem sie geholt wurde."""
    kunde = tf.kunden_info(kid)
    if not kunde:
        abort(404)
    seit, bis = request.args.get("seit") or None, request.args.get("bis") or None
    art = request.args.get("art")
    art = art if art in ("job", "wohnung") else None
    import csv
    import io as _io
    puffer = _io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";")
    schreiber.writerow(["Datum", "Art", "Angebot", "Anbieter", "Ort", "Quelle", "Stand",
                        "Bearbeiter", "Notiz", "Kontakt", "Link"])
    for z in tf.kunden_nachweis(kid, seit=seit, bis=bis, art=art):
        schreiber.writerow([
            (z["status_am"] or z["gefunden_am"] or "")[:16].replace("T", " "),
            "Stelle" if z["art"] == "job" else "Wohnung", z["titel"], z["anbieter"], z["ort"],
            z["quelle"], tf.STATUS_TEXT.get(z["status"], z["status"]), z["bearbeiter"],
            z["notiz"], z["kontakt_mail"] or z["kontakt_tel"] or "", z["url"]])
    name = re.sub(r"[^A-Za-z0-9]+", "_", kunde["name"]).strip("_")
    antwort = app.response_class(puffer.getvalue().encode("utf-8-sig"), mimetype="text/csv")
    antwort.headers["Content-Disposition"] = f'attachment; filename="Taskforce_{name}.csv"'
    return antwort


@app.route("/taskforce/kunde")
def taskforce_kunde_wahl():
    return redirect(url_for("taskforce_kunde", kid=int(request.args.get("kid") or 0)))


@app.route("/taskforce/kunde/<int:kid>")
def taskforce_kunde(kid, fehler=None, meldung=None):
    kunde = tf.kunden_info(kid)
    if not kunde:
        abort(404)
    ort = (kunde.get("stadt") or "").strip() or "Köln"
    profile = tf.profile_von(kid)
    for p in profile:
        p["kriterien_text"] = tf.kriterien_text(p)
    return render_template("taskforce_kunde.html", kunde=kunde, profile=profile,
                           arbeitszeit=tf.ARBEITSZEIT, ort_vorschlag=ort, fehler=fehler, meldung=meldung,
                           quellen=tf.quellen_stand(), kriterien=tf.KRITERIEN,
                           wohnungstypen=tf.WOHNUNGSTYPEN, jobkriterien=tf.JOB_KRITERIEN,
                           jobwahlen=tf.JOB_WAHLEN, jobmehrfach=tf.JOB_MEHRFACH)


# Eine Rücksprungadresse gehört diesem Haus nur, wenn sie mit **genau einem**
# Schrägstrich beginnt.
#
# `z.startswith("/")` reichte nicht: `//example.org` beginnt mit `/` und ist trotzdem
# eine fremde Adresse – ein Browser löst ein schema-relatives `Location` gegen das
# Schema der aktuellen Seite auf und landet auf `https://example.org`. Gemessen am
# 21.09.2026 kamen `//example.org`, `////example.org` und `//example.org/x` durch.
# Es gibt keinen CSRF-Token; ein fremdes Formular genügt, um jemanden nach dem Klick
# auf „übernehmen" wegzuschicken.
#
# Abgewehrt wird deshalb alles, was nicht `/` + ein Zeichen ist, das kein zweiter
# Schrägstrich und kein Backslash ist – Browser lesen `/\\example.org` wie
# `//example.org`. Dazu fällt weg, was `\s` trifft, und alles unter U+0020: `\t`,
# `\n` und `\r` wirft der Browser aus einer Adresse heraus, `/\t/example.org` wäre
# danach wieder `//example.org`. Ein `/` allein bleibt erlaubt.
#
# **Nicht abgewehrt** – und das steht hier, damit es niemand für eine Lücke hält:
# U+007F, U+0080–U+009F (ausser U+0085) und U+FEFF kommen durch. Jedes Zeichen von
# U+0000 bis U+00FF ist am 22.09.2026 an drei Stellen durchprobiert worden; die Folge
# dieser Reste ist null: Werkzeug weist nur CR und LF ab, es gibt keinen
# Header-Split, keinen 500er und keinen offenen Redirect. Sie stehen nicht im
# Zeichenvorrat, aus dem ein Browser eine fremde Adresse baut.
#
# **`\\Z`, nicht `$`.** `$` matcht in Python auch VOR einem abschliessenden
# Zeilenumbruch: `"/x\\n"` kam damit durch. Kein offener Redirect – Werkzeug weist
# einen Kopfzeilenwert mit Umbruch ab –, aber ein 500er NACH dem Schreiben. Bei
# `POST /taskforce/angebot/<id>/status` waeren `angebot_status` und `notieren` schon
# gelaufen, und der Nutzer saehe eine Absturzseite, ohne zu wissen, ob etwas passiert
# ist. `\\Z` heisst: Ende ist Ende.
INTERNER_WEG = re.compile(r"^/(?![/\\])[^\s\x00-\x1f\\]*\Z")


def _eigener_weg(ziel, standard):
    """Die Adresse, wenn sie unsere ist – sonst der Standard."""
    z = ziel or ""
    return z if INTERNER_WEG.match(z) else standard


def _mit_meldung(zurueck, meldung):
    """Zurück zur Seite, mit einer sichtbaren Rückmeldung.

    Vorher lud die Seite nach jedem Klick neu, und nichts sagte, ob etwas passiert ist.
    Die Meldung steht in der Adresse – so übersteht sie das Neuladen und lässt sich teilen."""
    ziel = _eigener_weg(zurueck, url_for("taskforce_tafel"))
    trenner = "&" if "?" in ziel else "?"
    return redirect(f"{ziel}{trenner}meldung={urllib.parse.quote(meldung)}")


def _zurueck(standard):
    """Die Rücksprungadresse aus dem Formular – siehe `_eigener_weg`."""
    return _eigener_weg(request.form.get("zurueck"), standard)


@app.route("/taskforce/kunde/<int:kid>/lebenslauf", methods=["POST"])
def taskforce_lebenslauf_neu(kid):
    try:
        L.hand_eintragen(kid, request.form.get("url"), request.form.get("name"))
    except ValueError as e:
        return taskforce_kunde(kid, fehler=str(e))
    return redirect(_zurueck(url_for("taskforce_kunde", kid=kid)))


@app.route("/taskforce/lebenslauf/<int:lid>/loesen", methods=["POST"])
def taskforce_lebenslauf_loesen(lid):
    L.loesen(lid)
    return redirect(_zurueck(url_for("taskforce_tafel")))


@app.route("/taskforce/kunde/<int:kid>/kurzprofil", methods=["POST"])
def taskforce_kurzprofil(kid):
    L.profil_speichern(kid, request.form.get("kurzprofil"), request.form.get("cv_text"))
    notieren("Kurzprofil gespeichert", "kunde", kid,
             (request.form.get("kurzprofil") or "")[:120])
    return redirect(_zurueck(url_for("taskforce_kunde", kid=kid)))


@app.route("/taskforce/kunde/<int:kid>/profil", methods=["POST"])
def taskforce_profil_neu(kid):
    # Das Formular hat mehrere Mehrfachfelder (Quellen, Wohnungstypen, Arbeitszeiten).
    # Deshalb geht das MultiDict unverändert weiter – dict() würde alles bis auf den
    # letzten Wert verschlucken.
    daten = request.form.copy()
    daten["kunde_id"] = kid
    try:
        pid = tf.profil_speichern(daten)
    except Exception as e:
        return taskforce_kunde(kid, fehler=str(e))
    tf.lauf(pid)
    return redirect(url_for("taskforce_profil", pid=pid))


@app.route("/taskforce/profil/<int:pid>")
def taskforce_profil(pid, fehler=None, meldung=None):
    p = tf.profil(pid)
    if not p:
        abort(404)
    status = request.args.get("status") or None
    seite = max(1, request.args.get("seite", 1, type=int))
    alle = tf.angebote(pid)
    gefiltert = [a for a in alle if (a["status"] == status if status else a["status"] != "doppelt")]
    je_seite = 60
    liste = gefiltert[(seite - 1) * je_seite: seite * je_seite]
    seiten = max(1, -(-len(gefiltert) // je_seite))
    zaehler = {s: sum(1 for a in alle if a["status"] == s) for s in tf.STATUS}
    return render_template("taskforce_profil.html", p=p, liste=liste, status=status,
                           seite=seite, seiten=seiten, gesamt=len(gefiltert),
                           status_liste=tf.STATUS, zaehler=zaehler, laeufe=tf.laeufe(pid),
                           arbeitszeit=tf.ARBEITSZEIT, imap=tf.imap_konfiguriert(),
                           fehler=fehler, meldung=meldung, quellen=tf.quellen_stand(),
                           gewaehlt=[q for q in (p.get("quellen") or "").split(",") if q],
                           leute=_tf_leute(), status_text=tf.STATUS_TEXT,
                           kunde=tf.kunden_info(p["kunde_id"]), k=tf.kriterien(p),
                           kriterien=tf.KRITERIEN, wohnungstypen=tf.WOHNUNGSTYPEN,
                           jobkriterien=tf.JOB_KRITERIEN, jobwahlen=tf.JOB_WAHLEN,
                           jobmehrfach=tf.JOB_MEHRFACH,
                           kriterien_text=tf.kriterien_text(p),
                           is24=tf.is24_url(p) if p["art"] == "wohnung" else None,
                           ohne_abgleich=sum(1 for a in alle if a["status"] == "neu" and not a.get("abgleich")))


@app.route("/taskforce/profil/<int:pid>/abgleich", methods=["POST"])
def taskforce_profil_abgleich(pid):
    n = tf.abgleich_profil(pid, max_n=30)
    return taskforce_profil(pid, meldung=f"{n} Angebote nachgeladen und abgeglichen.")


@app.route("/taskforce/angebot/<int:aid>/abgleich", methods=["POST"])
def taskforce_angebot_abgleich(aid):
    try:
        tf.abgleich(aid)
    except Exception as e:
        return redirect(_zurueck(url_for("taskforce_tafel")) + ("&" if "?" in _zurueck("") else "?") + "fehler=" + str(e)[:80])
    return redirect(_zurueck(url_for("taskforce_tafel")))


@app.route("/taskforce/profil/<int:pid>/lauf", methods=["POST"])
def taskforce_profil_lauf(pid):
    gefunden, neu, meldungen = tf.lauf(pid)
    text = f"Lauf fertig: {gefunden} Treffer, davon {neu} neu."
    if meldungen:
        return taskforce_profil(pid, meldung=text, fehler=" · ".join(meldungen))
    return taskforce_profil(pid, meldung=text)


@app.route("/taskforce/profil/<int:pid>/speichern", methods=["POST"])
def taskforce_profil_speichern(pid):
    vorher = tf.profil(pid) or {}
    tf.profil_speichern(request.form, pid)
    nachher = tf.profil(pid) or {}
    geaendert = {f: (vorher.get(f), nachher.get(f)) for f in
                 ("titel", "suchbegriffe", "ort", "umkreis_km", "aktiv", "kriterien")
                 if vorher.get(f) != nachher.get(f)}
    if geaendert:
        notieren("Profil geändert", "taskforce", pid, nachher.get("titel"),
                 vorher={f: v[0] for f, v in geaendert.items()},
                 nachher={f: v[1] for f, v in geaendert.items()})
    return redirect(url_for("taskforce_profil", pid=pid))


@app.route("/taskforce/profil/<int:pid>/loeschen", methods=["POST"])
def taskforce_profil_loeschen(pid):
    p = tf.profil(pid)
    kid = p["kunde_id"] if p else 0
    notieren("Profil gelöscht", "taskforce", pid, (p or {}).get("titel"), vorher=p)
    tf.profil_loeschen(pid)
    return redirect(url_for("taskforce_kunde", kid=kid))


@app.route("/taskforce/profil/<int:pid>/link", methods=["POST"])
def taskforce_wohnung_link(pid):
    try:
        neu = tf.wohnung_link_eintragen(pid, request.form.get("url", ""),
                                        request.form.get("titel"), request.form.get("preis"))
    except Exception as e:
        return taskforce_profil(pid, fehler=str(e))
    return taskforce_profil(pid, meldung="Exposé eingetragen." if neu else "War schon da.")


@app.route("/taskforce/angebot/<int:aid>/nachgefasst", methods=["POST"])
def taskforce_nachgefasst(aid):
    person = _bearbeiter(request.form.get("bearbeiter"))
    tf.nachgefasst(aid, person)
    notieren("nachgefasst", "taskforce", aid, f"Bearbeiter {person or '—'}")
    return redirect(_zurueck(url_for("taskforce_tafel")))


@app.route("/taskforce/angebote/status", methods=["POST"])
def taskforce_sammel_status():
    ids = request.form.getlist("ids")
    status = request.form.get("status")
    person = _bearbeiter(request.form.get("bearbeiter"))
    notiz = (request.form.get("notiz") or "").strip() or None
    n = tf.angebote_status(ids, status, person, notiz)
    notieren(f"Status {status} (Sammelaktion)", "taskforce", ",".join(ids[:20]),
             f"{n} Angebote · Bearbeiter {person or '—'}", nachher={"status": status})
    return _mit_meldung(request.form.get("zurueck"),
                        f"{n} Angebote auf {status} gesetzt"
                        + (f" – {person}" if person else ""))


@app.route("/taskforce/angebot/<int:aid>/status", methods=["POST"])
def taskforce_status(aid):
    status = request.form.get("status")
    person = _bearbeiter(request.form.get("bearbeiter"))
    vorher = db.eine("SELECT status, bearbeiter, titel FROM tf_angebot WHERE id=?", (aid,)) or {}
    tf.angebot_status(aid, status, person, request.form.get("notiz"))
    notieren(f"Status {status}", "taskforce", aid, (vorher.get("titel") or "")[:120],
             vorher={"status": vorher.get("status"), "bearbeiter": vorher.get("bearbeiter")},
             nachher={"status": status, "bearbeiter": person})
    return _mit_meldung(request.form.get("zurueck"),
                        f"{(vorher.get('titel') or 'Angebot')[:40]} auf {status} gesetzt"
                        + (f" – {person}" if person else ""))


@app.route("/taskforce/api/angebote")
def taskforce_api():
    """Schnittstelle fürs CRM: alle Angebote mit Kunde, Link und Stand als JSON.
    Filter: ?kunde=<id> &status=<neu|gesehen|angeschrieben|antwort|erfolg|verworfen> &seit=YYYY-MM-DD"""
    from flask import jsonify
    zeilen = tf.export_angebote(kunde_id=request.args.get("kunde", type=int),
                                status=request.args.get("status") or None,
                                seit=request.args.get("seit") or None)
    return jsonify({"stand": datetime.datetime.now().isoformat(timespec="seconds"),
                    "anzahl": len(zeilen), "angebote": zeilen})


@app.route("/taskforce/api/kpi")
def taskforce_api_kpi():
    from flask import jsonify
    return jsonify({"stand": datetime.datetime.now().isoformat(timespec="seconds"),
                    "mitarbeiter": tf.kpi_mitarbeiter()})


@app.route("/taskforce/export.csv")
def taskforce_export_csv():
    import csv
    import io as _io
    from flask import Response
    zeilen = tf.export_angebote(kunde_id=request.args.get("kunde", type=int),
                                status=request.args.get("status") or None)
    felder = ["kunde", "kundennummer", "art", "profil", "quelle", "titel", "anbieter", "ort",
              "url", "status", "bearbeiter", "notiz", "status_am", "gefunden_am", "veroeffentlicht",
              "score", "match", "passt", "fehlt"]
    puffer = _io.StringIO()
    w = csv.DictWriter(puffer, fieldnames=felder, delimiter=";", extrasaction="ignore")
    w.writeheader()
    for z in zeilen:
        ab = z.get("abgleich") or {}
        z["passt"] = ", ".join(ab.get("passt") or [])
        z["fehlt"] = ", ".join(ab.get("fehlt") or [])
        w.writerow(z)
    return Response("\ufeff" + puffer.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=taskforce_angebote.csv"})


@app.template_filter("abgleich")
def _f_abgleich(a):
    return tf.abgleich_von(a)


@app.template_filter("zusatz")
def _zusatz(a):
    return tf.zusatz(a)


# ---------------------------------------------------------------- Exporte

@app.route("/export/<art>")
def export(art):
    import exportieren
    pfad = exportieren.baue(art)
    if not pfad:
        abort(404)
    return send_file(pfad, as_attachment=True,
                     download_name=os.path.basename(pfad))


@app.template_filter("datum")
def _datum(iso):
    if not iso:
        return "—"
    try:
        return datetime.date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return iso


@app.template_filter("tage_her")
def _tage_her(iso):
    """Wie lange liegt das zurück? Macht aus einem Datum einen Vorwurf."""
    tage = k.alter_in_tagen(str(iso)[:10])
    if tage is None:
        return ""
    if tage == 0:
        return "heute"
    return f"seit {tage} Tagen"


@app.template_filter("zeit")
def _zeit(iso):
    if not iso:
        return "—"
    try:
        return datetime.datetime.fromisoformat(str(iso)).strftime("%d.%m. %H:%M")
    except ValueError:
        return str(iso)


# Konten- und Protokolltabelle immer bereitstellen, auch wenn das OS als Modul
# eingebunden wird (Tests, Schnittstelle) – CREATE TABLE IF NOT EXISTS kostet nichts.
konten.init()
aktivitaet.init()
fotos.init()
nachrichten.init()
betrieb.init()
# Die Schnittstellen fuer das CRM. Eigene Datei, eigener Praefix /api - damit sie
# vollstaendig bereitstehen, bevor angedockt wird, und niemand am CRM etwas nachbauen muss.
import api as api_modul  # noqa: E402

app.register_blueprint(api_modul.api)


# Der Faden für Sicherung und nächtlichen Lauf. Startet nur im echten Betrieb, nicht in
# den Selbsttests – die sollen nichts im Hintergrund anstoßen.
if os.environ.get("IMPROFY_OS_DB") is None:
    betrieb.starten()



if __name__ == "__main__":
    db.init()
    konten.init()
    tf.init()
    # Port: Umgebungsvariable PORT oder --port. Zweiteres, damit mehrere Arbeitsstaende
    # desselben Repos (git worktree) gleichzeitig laufen koennen, ohne sich den Port zu nehmen.
    port = int(os.environ.get("PORT", "8101"))   # 8100 gehoert dem alten OS
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"\n  Improfy-OS läuft  →  http://localhost:{port}\n")
    # Mit Passwort im Netz erreichbar, ohne Passwort nur auf diesem Rechner.
    app.run(host="0.0.0.0" if PASSWORT else "127.0.0.1", port=port, debug=False)
