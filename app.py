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
import os
import re
import secrets
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
    suche = (request.args.get("q") or "").strip() or None
    coach = request.args.get("coach", type=int)
    nur = request.args.get("nur") or None
    sql = ("SELECT k.id, k.name, k.status_code, k.telefon, k.email, k.stadt, k.massnahme,"
           "       k.kundennummer, m.name AS coach,"
           "       (SELECT COUNT(*) FROM lebenslauf l WHERE l.kunde_id=k.id) AS lebenslaeufe,"
           "       (SELECT COUNT(*) FROM tf_profil p WHERE p.kunde_id=k.id AND p.aktiv=1) AS profile"
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
        sql += " AND NOT EXISTS (SELECT 1 FROM tf_profil p WHERE p.kunde_id=k.id AND p.aktiv=1)"
    zeilen = db.hole(sql + " ORDER BY k.status_code, k.name", tuple(args))
    for z in zeilen:
        z["status_text"] = db.STATUS.get(z["status_code"], "—")
        z["ampel"] = db.STATUS_AMPEL.get(z["status_code"], "grau")
    return render_template(
        "kunden.html", zeilen=zeilen, suche=suche, coach=coach, nur=nur,
        coaches=db.hole("SELECT id, name FROM mitarbeiter WHERE standort=? ORDER BY name",
                        (db.STANDORT_STANDARD,)))


# Flask lässt für <int:kid> beliebig große Zahlen durch; SQLite nimmt nur 64 Bit.
# Eine getippte Riesenzahl in der Adresse endete deshalb in einem Absturz statt in 404.
SQLITE_MAX = 2 ** 63 - 1
CRM_URL = os.environ.get("CRM_URL", "https://crm.improfy.de")


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
        gutscheine=db.hole(
            "SELECT improfy_id, massnahme, von, bis, ue_bewilligt, ue_gerechnet, status"
            "  FROM gutschein_zeile WHERE kunde_id=? ORDER BY von DESC", (kid,)),
        tf_profile=tf.profile_von(kid), lebenslaeufe=L.von_kunde(kid),
        profil=db.eine("SELECT kurzprofil, cv_text FROM kunde_profil WHERE kunde_id=?",
                       (kid,)) or {})


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

def _tf_seite(meldungen=None):
    if not meldungen and request.args.get("meldung"):
        meldungen = [request.args["meldung"]]
    """Die Tafel. Jeder Filter steht in der Adresse, damit man eine Ansicht teilen kann."""
    def zahl(name, typ=int):
        return request.args.get(name, type=typ)

    f = {"kunde_id": zahl("kunde"), "art": request.args.get("art") or None,
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
    return render_template(
        "taskforce.html", profile=tf.uebersicht(), neu=tf.anzahl_neu(), filter=f,
        neue=tf.neue_angebote(limit=100, **f), zaehler=tf.angebote_zaehlen(),
        arbeitszeiten=tf.ARBEITSZEITEN, status_liste=tf.STATUS,
        coaches=db.hole("SELECT DISTINCT m.id, m.name FROM mitarbeiter m JOIN kunde k"
                        " ON k.coach_id=m.id WHERE m.standort=? ORDER BY m.name",
                        (db.STANDORT_STANDARD,)),
        laeufe=tf.laeufe(limit=15), imap=tf.imap_konfiguriert(), alarm=tf.alarm_konfiguriert(),
        meldungen=meldungen, quellen=tf.quellen_stand(), kpi=tf.kpi_mitarbeiter(),
        lauf=betrieb.lauf_zustand(),
        ohne_profil=db.wert(
            "SELECT COUNT(*) FROM kunde k WHERE k.standort=? AND k.status_code IN ('H','I')"
            "  AND NOT EXISTS (SELECT 1 FROM tf_profil t WHERE t.kunde_id=k.id AND t.aktiv=1)",
            (db.STANDORT_STANDARD,)),
        wiedervorlage=tf.wiedervorlage(), wv_tage=tf.WIEDERVORLAGE_TAGE,
        leute=_tf_leute(), ohne_cv=tf.ohne_lebenslauf(),
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


@app.route("/taskforce")
def taskforce_seite():
    return _tf_seite()


@app.route("/taskforce/lauf", methods=["POST"])
def taskforce_lauf():
    """Stößt den Lauf an und kommt sofort zurück – die Portale brauchen bis zu zwei Minuten."""
    if betrieb.lauf_starten():
        notieren("Agentenlauf gestartet", "taskforce")
        meldung = "Der Lauf ist gestartet und arbeitet im Hintergrund. Diese Seite zeigt oben, wann er fertig ist."
    else:
        meldung = "Es läuft bereits ein Durchgang – der zweite würde dieselben Portale doppelt fragen."
    return redirect(url_for("taskforce_seite", meldung=meldung))


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


def _mit_meldung(zurueck, meldung):
    """Zurück zur Seite, mit einer sichtbaren Rückmeldung.

    Vorher lud die Seite nach jedem Klick neu, und nichts sagte, ob etwas passiert ist.
    Die Meldung steht in der Adresse – so übersteht sie das Neuladen und lässt sich teilen."""
    ziel = zurueck if (zurueck or "").startswith("/") else url_for("taskforce_seite")
    trenner = "&" if "?" in ziel else "?"
    return redirect(f"{ziel}{trenner}meldung={urllib.parse.quote(meldung)}")


def _zurueck(standard):
    z = request.form.get("zurueck") or ""
    return z if z.startswith("/") else standard


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
    return redirect(_zurueck(url_for("taskforce_seite")))


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
        return redirect(_zurueck(url_for("taskforce_seite")) + ("&" if "?" in _zurueck("") else "?") + "fehler=" + str(e)[:80])
    return redirect(_zurueck(url_for("taskforce_seite")))


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
    return redirect(_zurueck(url_for("taskforce_seite")))


@app.route("/taskforce/angebote/status", methods=["POST"])
def taskforce_sammel_status():
    ids = request.form.getlist("ids")
    status = request.form.get("status")
    person = _bearbeiter(request.form.get("bearbeiter"))
    n = tf.angebote_status(ids, status, person)
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
# Der Faden für Sicherung und nächtlichen Lauf. Startet nur im echten Betrieb, nicht in
# den Selbsttests – die sollen nichts im Hintergrund anstoßen.
if os.environ.get("IMPROFY_OS_DB") is None:
    betrieb.starten()



if __name__ == "__main__":
    db.init()
    konten.init()
    tf.init()
    port = int(os.environ.get("PORT", "8101"))   # 8100 gehoert dem alten OS
    print(f"\n  Improfy-OS läuft  →  http://localhost:{port}\n")
    # Mit Passwort im Netz erreichbar, ohne Passwort nur auf diesem Rechner.
    app.run(host="0.0.0.0" if PASSWORT else "127.0.0.1", port=port, debug=False)
