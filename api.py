# -*- coding: utf-8 -*-
"""Die Schnittstellen der Taskforce – alles, was crm.improfy.de braucht, um anzudocken.

**Warum diese Datei so vollständig ist.** Am CRM nachträglich etwas zu ergänzen ist teuer:
fremdes Repository, fremder Entwickler, fremder Freigabeweg. Auf unserer Seite kostet eine
Schnittstelle eine Stunde. Also steht hier **alles bereit, bevor angedockt wird** – lesend
und schreibend, damit das CRM die Taskforce vollständig bedienen kann, ohne dass dort auch
nur eine Zeile für uns geschrieben werden muss.

    GET /api          Verzeichnis aller Schnittstellen (dort steht, was es gibt)

**Lesend und schreibend.** Die Schnittstelle des alten OS war bewusst nur lesend; wer
ändern wollte, nahm die Oberfläche. Hier geht das nicht: Das CRM führt die Kunden und soll
Suchprofile anlegen, Läufe anstoßen und Rückmeldungen zurückschreiben können, ohne dass
jemand ein zweites Fenster öffnet. Schreibende Aufrufe sind darum vorhanden, tragen alle
den Schlüssel und landen **jede einzeln im Protokoll** – wer über die Schnittstelle etwas
ändert, ist nachher genauso nachvollziehbar wie jemand, der klickt.

**Zugang.** Dieselbe Regel wie im alten OS:

  * ohne `IMPROFY_OS_PASSWORT` lauscht die Anwendung nur auf 127.0.0.1 – dann ist die
    Schnittstelle nur auf diesem Rechner erreichbar und braucht keinen Schlüssel;
  * mit Passwort verlangt sie einen Schlüssel: `OS_API_TOKEN` in der `.env`, im Aufruf als
    `Authorization: Bearer <token>`, `X-API-Token` oder `?token=`.

Für schreibende Aufrufe gilt zusätzlich: Sobald die Anwendung im Netz steht, **muss** ein
Schlüssel gesetzt sein. Eine offene Schreibschnittstelle im Netz gibt es nicht.

Ist `OS_API_HERKUNFT` gesetzt (z. B. `https://crm.improfy.de`), darf diese Herkunft die
Schnittstelle auch aus dem Browser heraus aufrufen.

Jede Antwort trägt `stand` (Zeitpunkt) und, wo es eine Liste ist, `anzahl`.

**Feldnamen.** Wo das CRM eigene Namen führt (`customer_number`, `status`, `branch` …),
nimmt diese Schnittstelle sie entgegen und übersetzt selbst – die Zuordnung steht in
`crm_karte.py`, am laufenden CRM nachgesehen. Das CRM schickt seine Kunden also so, wie es
sie ohnehin hat.
"""
import datetime
import functools
import hmac
import json
import os
import time

from flask import Blueprint, jsonify, request

import crm_karte
import datenbank as db
import taskforce as tf

api = Blueprint("api", __name__, url_prefix="/api")


def jetzt():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------- Zugang

def token_noetig():
    """Nur wenn die Anwendung im Netz lauscht (Passwort gesetzt), ist ein Schlüssel Pflicht."""
    return bool(os.environ.get("IMPROFY_OS_PASSWORT"))


def token_gueltig():
    erwartet = os.environ.get("OS_API_TOKEN") or ""
    if not token_noetig():
        return True
    if not erwartet:
        return False
    kopf = request.headers.get("Authorization") or ""
    mitgegeben = kopf[7:].strip() if kopf.lower().startswith("bearer ") else (
        request.args.get("token") or request.headers.get("X-API-Token") or "")
    return bool(mitgegeben) and hmac.compare_digest(mitgegeben, erwartet)


def _abgewiesen():
    return jsonify({
        "fehler": "Zugang verweigert",
        "hinweis": "Schlüssel fehlt oder stimmt nicht. OS_API_TOKEN in der .env setzen und als"
                   " 'Authorization: Bearer <token>' oder '?token=' mitgeben."
    }), 401


def geschuetzt(f):
    """Lesender Aufruf: Schlüssel, sobald die Anwendung im Netz steht."""
    @functools.wraps(f)
    def innen(*a, **kw):
        if not token_gueltig():
            return _abgewiesen()
        return f(*a, **kw)
    return innen


def schreibend(aktion):
    """Schreibender Aufruf: Schlüssel, Protokoll, und im Netz nie ohne Schlüssel.

    Das Protokoll ist kein Beiwerk. Sobald zwei Systeme dieselben Daten ändern, ist die
    erste Frage bei jeder Unstimmigkeit „wer war das" – und die zweite „wann". Beides muss
    beantwortbar sein, sonst traut am Ende niemand mehr einem der beiden Systeme."""
    def aussen(f):
        @functools.wraps(f)
        def innen(*a, **kw):
            if token_noetig() and not (os.environ.get("OS_API_TOKEN") or ""):
                return jsonify({
                    "fehler": "Schreiben ist gesperrt",
                    "hinweis": "Die Anwendung ist im Netz erreichbar, aber es ist kein"
                               " OS_API_TOKEN gesetzt. Ohne Schlüssel kein Schreibzugriff."
                }), 403
            if not token_gueltig():
                return _abgewiesen()
            antwort = f(*a, **kw)
            try:
                from app import notieren
                koerper = antwort[0] if isinstance(antwort, tuple) else antwort
                daten = koerper.get_json(silent=True) or {}
                notieren(f"Schnittstelle: {aktion}", "api", None,
                         json.dumps({k: v for k, v in daten.items()
                                     if k in ("id", "profil", "angebot", "kunde", "anzahl",
                                              "status", "geaendert")}, ensure_ascii=False)[:300])
            except Exception:
                pass                       # Protokoll darf den Aufruf nie kippen
            return antwort
        return innen
    return aussen


@api.after_request
def _kopfzeilen(antwort):
    herkunft = os.environ.get("OS_API_HERKUNFT")
    if herkunft:
        antwort.headers["Access-Control-Allow-Origin"] = herkunft
        antwort.headers["Access-Control-Allow-Headers"] = "Authorization, X-API-Token, Content-Type"
        antwort.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        antwort.headers["Vary"] = "Origin"
    return antwort


# ------------------------------------------------------------------ Werkzeug

def liste(daten, **extra):
    return jsonify({"stand": jetzt(), "anzahl": len(daten), "daten": daten, **extra})


def _int(name):
    return request.args.get(name, type=int)


def _text(name):
    return (request.args.get(name) or "").strip() or None


def eingang():
    """Was hereinkommt – als JSON-Körper oder als Formular, beides gilt.

    Das CRM schickt JSON. Ein Mensch, der die Schnittstelle mit curl ausprobiert, tippt
    eher `-d feld=wert`. Beides anzunehmen kostet nichts und erspart Rückfragen."""
    if request.is_json:
        return request.get_json(silent=True) or {}
    return {k: v for k, v in request.form.items()}


def _fehlt(*felder):
    d = eingang()
    return [f for f in felder if not str(d.get(f) or "").strip()]


# --------------------------------------------------------------- Verzeichnis

VERZEICHNIS = [
    ("GET /api", "dieses Verzeichnis"),
    ("GET /api/gesundheit", "lebt die Taskforce, wie viel liegt neu, wann lief sie zuletzt"),
    ("GET /api/quellen", "welche Portale angebunden sind und welche Zugangsdaten fehlen"),
    ("GET /api/karte", "Datenkarte des CRM: Feldnamen, Statuswerte, Ablagefächer"),

    ("GET /api/kunden?q=&status=&coach=&limit=", "Kunden der Taskforce mit Profilen und Zählern"),
    ("GET /api/kunden/suche?q=", "Personensuche zum Tippen – ohne Umlaute, Reihenfolge egal"),
    ("GET /api/kunde/<id>", "ein Kunde mit Profilen, Lebensläufen und offenen Angeboten"),
    ("POST /api/kunde", "Kunde aus dem CRM anlegen oder aktualisieren (CRM-Feldnamen erlaubt)"),

    ("GET /api/taskforce/profile?kunde=&art=", "Job- und Wohnprofile mit Zählern"),
    ("GET /api/taskforce/profil/<id>", "ein Profil mit Kriterien, Quellen und Läufen"),
    ("POST /api/taskforce/profil", "Suchprofil anlegen oder ändern"),
    ("DELETE /api/taskforce/profil/<id>", "Suchprofil löschen (Angebote bleiben)"),
    ("POST /api/taskforce/profil/<id>/lauf", "die Agenten dieses Profils sofort laufen lassen"),
    ("POST /api/taskforce/lauf?art=", "alle Profile laufen lassen, im Hintergrund"),

    ("GET /api/taskforce/angebote?kunde=&profil=&art=&status=&seit=&limit=",
     "gefundene Stellen und Wohnungen mit Abgleich, Relevanz und Kontaktdaten"),
    ("GET /api/taskforce/angebot/<id>", "ein Angebot vollständig: Beschreibung, Abgleich, Empfänger"),
    ("POST /api/taskforce/angebot/<id>/status", "Status setzen: gesehen, angeschrieben, antwort, erfolg, verworfen"),
    ("POST /api/taskforce/angebote/status", "Status für mehrere Angebote auf einmal"),
    ("POST /api/taskforce/angebot/<id>/abgleich", "Beschreibung nachladen und gegen den Kunden abgleichen"),
    ("GET /api/taskforce/wiedervorlage?tage=&art=", "angeschrieben, aber ohne Antwort – die eigentliche Arbeit"),
    ("GET /api/taskforce/kunde/<id>/bilanz?seit=&bis=",
     "Nachweis je Kunde fuers Jobcenter: gesucht, angeschrieben, Rueckmeldung, Ergebnis"),
    ("GET /api/taskforce/kpi", "Zahlen je Mitarbeiter: angeschrieben, Antworten, Quoten"),
    ("GET /api/taskforce/laeufe?profil=&limit=", "was die Agenten zuletzt gefragt haben"),

    ("GET /api/taskforce/suche?art=job|wohnung|beides&was=&wo=&km=&…",
     "live bei allen Portalen suchen – ohne Kunde, ohne Ablage"),
    ("POST /api/taskforce/profil/<id>/uebernehmen", "Treffer einer Suche auf die Tafel eines Profils legen"),

    ("GET /taskforce/export.csv?kunde=&status=", "Taskforce-Angebote als CSV"),
]


@api.route("")
@api.route("/")
@geschuetzt
def verzeichnis():
    return jsonify({
        "name": "Improfy-Taskforce", "standort": db.STANDORT_STANDARD, "stand": jetzt(),
        "schreibend": True,
        "zugang": ("Schlüssel nötig (OS_API_TOKEN)" if token_noetig()
                   else "offen, weil die Anwendung nur auf 127.0.0.1 lauscht"),
        "statuswerte": {"angebot": list(tf.STATUS), "kunde_crm": crm_karte.STATUS},
        "arten": {"job": "Arbeitssuche", "wohnung": "Wohnungssuche"},
        "schnittstellen": [{"aufruf": a, "inhalt": i} for a, i in VERZEICHNIS],
    })


@api.route("/gesundheit")
@geschuetzt
def gesundheit():
    quellen = tf.quellen_stand()
    letzter = db.eine("SELECT zeitpunkt, quelle, gefunden, neu, meldung FROM tf_lauf"
                      " ORDER BY id DESC LIMIT 1")
    return jsonify({
        "stand": jetzt(), "ok": True,
        "kunden": db.wert("SELECT COUNT(*) FROM kunde WHERE standort=?", (db.STANDORT_STANDARD,)),
        # **Hier bleibt `aktiv=1`, und zwar mit Absicht.** `/gesundheit` beantwortet
        # „was läuft gerade" – daneben stehen neue Treffer, Wiedervorlage und wie
        # viele Quellen verbunden sind. Ein pausiertes Profil läuft nicht. Die
        # Zählweise „jedes Profil" gilt dort, wo eine Oberfläche „Profil" oder „kein
        # Profil" schreibt (Kundenliste, Coaches, Sammelanlage, Kopfsuche); das ist
        # eine andere Frage und darf hier nicht dieselbe Zahl liefern.
        "profile": {art: db.wert("SELECT COUNT(*) FROM tf_profil WHERE aktiv=1 AND art=?"
                                 " AND standort=?", (art, db.STANDORT_STANDARD))
                    for art in ("job", "wohnung")},
        "neu": {art: tf.anzahl_neu(art=art) for art in ("job", "wohnung")},
        "wiedervorlage": tf.anzahl_wiedervorlage(),
        "quellen": {"gesamt": len(quellen), "bereit": len([q for q in quellen if q["bereit"]]),
                    "fehlend": [q["schluessel"] for q in quellen if not q["bereit"]]},
        "letzter_lauf": letzter,
    })


@api.route("/quellen")
@geschuetzt
def quellen():
    return liste(tf.quellen_stand(), imap=tf.imap_konfiguriert(), alarm=tf.alarm_konfiguriert())


@api.route("/karte")
@geschuetzt
def karte():
    """Die Datenkarte des CRM, wie wir sie kennen – damit beide Seiten dieselbe Zuordnung lesen."""
    return jsonify({"stand": jetzt(), "aufbau": crm_karte.AUFBAU,
                    "kunde_felder": crm_karte.KUNDE_FELDER,
                    "liste_felder": crm_karte.LISTE_FELDER,
                    "konto_felder": crm_karte.KONTO_FELDER,
                    "status": crm_karte.STATUS, "status_text": crm_karte.STATUS_TEXT,
                    "laeuft": list(crm_karte.LAEUFT), "standorte": crm_karte.STANDORTE,
                    "ablage": crm_karte.ABLAGE, "unsere_faecher": crm_karte.UNSERE_FAECHER})


# -------------------------------------------------------------------- Kunden

def _kunde_zeile(k):
    k["status_text"] = db.STATUS.get(k.get("status_code"), None)
    k["profile"] = tf.profile_von(k["id"])
    k["neu"] = db.wert("SELECT COUNT(*) FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
                       " WHERE p.kunde_id=? AND a.status='neu'", (k["id"],))
    return k


@api.route("/kunden")
@geschuetzt
def kunden():
    sql = ("SELECT k.*, m.name AS coach FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
           " WHERE k.standort=?")
    args = [db.STANDORT_STANDARD]
    if _text("status"):
        sql += " AND k.status_code=?"
        args.append(_text("status"))
    if _int("coach"):
        sql += " AND k.coach_id=?"
        args.append(_int("coach"))
    if _text("q"):
        sql += " AND (k.name LIKE ? OR k.kundennummer LIKE ?)"
        args += [f"%{_text('q')}%"] * 2
    sql += " ORDER BY k.name LIMIT ?"
    args.append(_int("limit") or 500)
    return liste([_kunde_zeile(z) for z in db.hole(sql, args)])


@api.route("/kunden/suche")
@geschuetzt
def kunden_suche():
    """Personensuche zum Tippen: ohne Umlaute, ohne Akzente, Reihenfolge egal."""
    return liste(tf.kunden_suchen(_text("q") or "", limit=_int("limit") or 12))


@api.route("/kunde/<int:kid>")
@geschuetzt
def kunde(kid):
    satz = tf.kunden_info(kid)
    if not satz:
        return jsonify({"fehler": "Kunde nicht gefunden"}), 404
    profile = tf.profile_von(kid)
    for p in profile:
        p["kriterien_gelesen"] = tf.kriterien(p)
    return jsonify({
        "stand": jetzt(), "kunde": satz, "profile": profile,
        "angebote_neu": tf.neue_angebote(limit=50, kunde_id=kid, status="neu"),
    })


# Welche CRM-Felder auf welche Spalte bei uns gehen. Steht hier und nicht verstreut im
# Code, damit man beim naechsten CRM-Feld genau eine Zeile ergaenzt.
CRM_AUF_UNS = {
    "name": "name", "customer_number": "kundennummer", "phone": "telefon",
    "email": "email", "language": "sprache", "measure": "massnahme",
    "case_worker": "ort_jc", "address": "stadt",
}


@api.route("/kunde", methods=["POST"])
@schreibend("Kunde angelegt oder aktualisiert")
def kunde_schreiben():
    """Einen Kunden aus dem CRM übernehmen – anlegen, wenn neu, sonst aktualisieren.

    **Das CRM führt die Kunden, nicht wir.** Erkannt wird über die Kundennummer; erst wenn
    die fehlt, über den Namen. Überschrieben wird nur, was mitgeschickt wird – ein Feld,
    das das CRM nicht kennt, darf nicht stillschweigend geleert werden.

    Feldnamen des CRM (`customer_number`, `phone`, `status` …) sind erlaubt und werden
    übersetzt; unsere eigenen ebenso. Der Status kommt als CRM-Wort („aktiv") und wird auf
    unseren Buchstaben abgebildet."""
    d = eingang()
    if not str(d.get("name") or "").strip() and not str(d.get("customer_number") or "").strip():
        return jsonify({"fehler": "name oder customer_number wird gebraucht"}), 400

    felder = {}
    for crm_feld, unser in CRM_AUF_UNS.items():
        for quelle in (crm_feld, unser):
            if quelle in d and str(d.get(quelle) or "").strip():
                felder[unser] = str(d[quelle]).strip()
                break
    status = str(d.get("status") or "").strip().lower()
    if status:
        felder["status_code"] = crm_karte.STATUS.get(status, d.get("status_code") or None)
    if d.get("status_code"):
        felder["status_code"] = d["status_code"]

    nummer = felder.get("kundennummer")
    vorhanden = None
    if nummer:
        vorhanden = db.eine("SELECT * FROM kunde WHERE kundennummer=? AND standort=?",
                            (nummer, db.STANDORT_STANDARD))
    if not vorhanden and felder.get("name"):
        vorhanden = db.eine("SELECT * FROM kunde WHERE name=? AND standort=?",
                            (felder["name"], db.STANDORT_STANDARD))

    with db.offen() as con:
        if vorhanden:
            gesetzt = ", ".join(f"{f}=?" for f in felder)
            con.execute(f"UPDATE kunde SET {gesetzt}, quelle_stand='CRM', stand_am=? WHERE id=?",
                        list(felder.values()) + [jetzt(), vorhanden["id"]])
            kid, neu = vorhanden["id"], False
        else:
            felder.setdefault("name", nummer)
            spalten = list(felder) + ["standort", "quelle_stand", "stand_am"]
            werte = list(felder.values()) + [db.STANDORT_STANDARD, "CRM", jetzt()]
            cur = con.execute(f"INSERT INTO kunde ({', '.join(spalten)})"
                              f" VALUES ({', '.join('?' * len(spalten))})", werte)
            kid, neu = cur.lastrowid, True
    return jsonify({"stand": jetzt(), "kunde": kid, "angelegt": neu,
                    "geaendert": sorted(felder)})


@api.route("/taskforce/kunde/<int:kid>/bilanz")
@geschuetzt
def tf_kunde_bilanz(kid):
    """Der Nachweis je Kunde: was gesucht, was angeschrieben, was zurückkam.

    Das ist die Antwort auf die Frage des Jobcenters – „was haben Sie für diesen Menschen
    unternommen". `seit=` und `bis=` grenzen auf einen Zeitraum ein, etwa den der Maßnahme."""
    if not db.eine("SELECT id FROM kunde WHERE id=?", (kid,)):
        return jsonify({"fehler": "Kunde nicht gefunden"}), 404
    seit, bis = _text("seit"), _text("bis")
    return jsonify({"stand": jetzt(), "kunde": kid,
                    "zeitraum": {"seit": seit, "bis": bis},
                    "bilanz": tf.kunden_bilanz(kid, seit=seit, bis=bis),
                    "nachweis": tf.kunden_nachweis(kid, seit=seit, bis=bis)})


# ----------------------------------------------------------------- Profile

@api.route("/taskforce/profile")
@geschuetzt
def tf_profile():
    zeilen = tf.uebersicht(art=_text("art"))
    if _int("kunde"):
        zeilen = [z for z in zeilen if z["kunde_id"] == _int("kunde")]
    return liste(zeilen)


@api.route("/taskforce/profil/<int:pid>")
@geschuetzt
def tf_profil(pid):
    p = tf.profil(pid)
    if not p:
        return jsonify({"fehler": "Profil nicht gefunden"}), 404
    p["kriterien_gelesen"] = tf.kriterien(p)
    p["kriterien_text"] = tf.kriterien_text(p)
    p["quellen_aktiv"] = tf.quellen_fuer(p)
    if p["art"] == "wohnung":
        p["is24_url"] = tf.is24_url(p)
    return jsonify({"stand": jetzt(), "profil": p,
                    "angebote": tf.angebote(pid, status=_text("status")),
                    "laeufe": tf.laeufe(pid, limit=20)})


@api.route("/taskforce/profil", methods=["POST"])
@schreibend("Suchprofil angelegt oder geändert")
def tf_profil_schreiben():
    """Suchprofil anlegen oder ändern.

    Pflicht ist wenig: `kunde` und `art`. Alles andere hat brauchbare Vorgaben – ein
    Jobprofil ohne Suchbegriffe findet nichts, sagt das aber beim Lauf, statt hier den
    Aufruf abzuweisen. Wer ein bestehendes Profil ändert, schickt `profil` mit; dann
    bleibt stehen, was nicht mitgeschickt wird."""
    d = dict(eingang())
    pid = d.get("profil") or d.get("id")
    if not pid:
        fehlt = _fehlt("kunde", "art")
        if fehlt:
            return jsonify({"fehler": "Pflichtfelder fehlen", "felder": fehlt}), 400
        if str(d.get("art")) not in ("job", "wohnung"):
            return jsonify({"fehler": "art muss 'job' oder 'wohnung' sein"}), 400
        d.setdefault("titel", "Arbeitssuche" if d["art"] == "job" else "Wohnungssuche")
        d.setdefault("ort", "Köln")
        d.setdefault("umkreis_km", 25)
        d.setdefault("standort", db.STANDORT_STANDARD)
        d["kunde_id"] = d.get("kunde_id") or d.get("kunde")
    # Kriterien dürfen als verschachteltes Objekt kommen – das Formular kennt nur Text.
    if isinstance(d.get("kriterien"), dict):
        d["kriterien"] = json.dumps(d["kriterien"], ensure_ascii=False)
    neu = tf.profil_speichern(d, pid=int(pid) if pid else None)
    return jsonify({"stand": jetzt(), "profil": neu or int(pid), "angelegt": not pid})


@api.route("/taskforce/profil/<int:pid>", methods=["DELETE"])
@schreibend("Suchprofil gelöscht")
def tf_profil_loeschen(pid):
    if not tf.profil(pid):
        return jsonify({"fehler": "Profil nicht gefunden"}), 404
    tf.profil_loeschen(pid)
    return jsonify({"stand": jetzt(), "profil": pid, "geloescht": True})


@api.route("/taskforce/profil/<int:pid>/lauf", methods=["POST"])
@schreibend("Agentenlauf für ein Profil")
def tf_profil_lauf(pid):
    """Die Agenten dieses Profils sofort laufen lassen. Antwortet erst, wenn sie fertig sind –
    das dauert je nach Portal bis zu einer Minute."""
    if not tf.profil(pid):
        return jsonify({"fehler": "Profil nicht gefunden"}), 404
    begonnen = time.time()
    gefunden, neu, meldungen = tf.lauf(pid)
    return jsonify({"stand": jetzt(), "profil": pid, "gefunden": gefunden, "neu": neu,
                    "meldungen": meldungen, "sekunden": round(time.time() - begonnen, 1)})


@api.route("/taskforce/lauf", methods=["POST"])
@schreibend("Agentenlauf für alle Profile")
def tf_lauf_alle():
    """Alle aktiven Profile laufen lassen – im Hintergrund, weil es Minuten dauert.
    `art=job` oder `art=wohnung` grenzt auf eine Hälfte ein."""
    import betrieb
    art = _text("art") or (eingang().get("art") or None)
    art = art if art in ("job", "wohnung") else None
    gestartet = betrieb.lauf_starten(art)
    return jsonify({"stand": jetzt(), "gestartet": gestartet, "art": art,
                    "hinweis": ("läuft im Hintergrund, Stand über GET /api/gesundheit"
                                if gestartet else "es läuft bereits ein Durchgang")})


# ---------------------------------------------------------------- Angebote

@api.route("/taskforce/angebote")
@geschuetzt
def tf_angebote():
    """Die gefundenen Stellen und Wohnungen. Ohne Filter kommt alles, was neu ist.

    `seit=YYYY-MM-DD` liefert nur, was seitdem dazukam – so holt sich das CRM regelmäßig
    den Zuwachs, ohne jedes Mal alles zu übertragen."""
    zeilen = tf.export_angebote(kunde_id=_int("kunde"), status=_text("status"),
                                seit=_text("seit"))
    if _text("art"):
        zeilen = [z for z in zeilen if z.get("art") == _text("art")]
    if _int("profil"):
        zeilen = [z for z in zeilen if z.get("profil_id") == _int("profil")]
    grenze = _int("limit") or 500
    return liste(zeilen[:grenze], gesamt=len(zeilen))


@api.route("/taskforce/angebot/<int:aid>")
@geschuetzt
def tf_angebot(aid):
    a = db.eine("SELECT a.*, p.art, p.kunde_id, p.titel AS profil_titel, k.name AS kunde"
                "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
                "  JOIN kunde k ON k.id=p.kunde_id WHERE a.id=?", (aid,))
    if not a:
        return jsonify({"fehler": "Angebot nicht gefunden"}), 404
    a["zusatz_gelesen"] = tf.zusatz(a)
    a["abgleich_gelesen"] = tf.abgleich_von(a)
    a["kontakt"] = {"mail": a.get("kontakt_mail"), "telefon": a.get("kontakt_tel"),
                    "name": a.get("kontakt_name"),
                    "weg": "mail" if a.get("kontakt_mail") else "portal"}
    a["verlauf"] = db.hole("SELECT status, bearbeiter, zeitpunkt FROM tf_ereignis"
                           " WHERE angebot_id=? ORDER BY id", (aid,))
    return jsonify({"stand": jetzt(), "angebot": a})


@api.route("/taskforce/angebot/<int:aid>/status", methods=["POST"])
@schreibend("Angebotsstatus gesetzt")
def tf_angebot_status(aid):
    """Status eines Angebots setzen – das ist die Rückmeldung, auf die es ankommt.

    `bearbeiter` gehört dazu: Ohne Namen zählen die Zahlen je Mitarbeiter nicht, und
    „jemand hat angeschrieben" hilft niemandem beim Nachfassen."""
    d = eingang()
    status = str(d.get("status") or "").strip()
    if status not in tf.STATUS:
        return jsonify({"fehler": "unbekannter Status", "erlaubt": list(tf.STATUS)}), 400
    if not db.eine("SELECT id FROM tf_angebot WHERE id=?", (aid,)):
        return jsonify({"fehler": "Angebot nicht gefunden"}), 404
    tf.angebot_status(aid, status, bearbeiter=d.get("bearbeiter"), notiz=d.get("notiz"))
    return jsonify({"stand": jetzt(), "angebot": aid, "status": status})


@api.route("/taskforce/angebote/status", methods=["POST"])
@schreibend("Angebotsstatus für mehrere gesetzt")
def tf_angebote_status():
    d = eingang()
    status = str(d.get("status") or "").strip()
    ids = d.get("ids") or d.get("angebote") or []
    if isinstance(ids, str):
        ids = [t for t in ids.replace(";", ",").split(",") if t.strip()]
    ids = [int(i) for i in ids]
    if status not in tf.STATUS:
        return jsonify({"fehler": "unbekannter Status", "erlaubt": list(tf.STATUS)}), 400
    if not ids:
        return jsonify({"fehler": "ids fehlen"}), 400
    tf.angebote_status(ids, status, bearbeiter=d.get("bearbeiter"))
    return jsonify({"stand": jetzt(), "anzahl": len(ids), "status": status})


@api.route("/taskforce/angebot/<int:aid>/abgleich", methods=["POST"])
@schreibend("Abgleich nachgeladen")
def tf_angebot_abgleich(aid):
    """Beschreibung beim Portal nachladen und gegen den Kunden abgleichen.
    Dabei fallen auch die Kontaktdaten an – Mail, Telefon, Ansprechpartner."""
    if not db.eine("SELECT id FROM tf_angebot WHERE id=?", (aid,)):
        return jsonify({"fehler": "Angebot nicht gefunden"}), 404
    ergebnis = tf.abgleich(aid)
    if not ergebnis:
        return jsonify({"fehler": "Abgleich nicht möglich"}), 400
    abgleich, match = ergebnis
    a = db.eine("SELECT kontakt_mail, kontakt_tel, kontakt_name FROM tf_angebot WHERE id=?", (aid,))
    return jsonify({"stand": jetzt(), "angebot": aid, "abgleich": abgleich, "match": match,
                    "kontakt": a})


@api.route("/taskforce/wiedervorlage")
@geschuetzt
def tf_wiedervorlage():
    return liste(tf.wiedervorlage(tage=_int("tage") or tf.WIEDERVORLAGE_TAGE,
                                  art=_text("art"), limit=_int("limit") or 60),
                 tage=_int("tage") or tf.WIEDERVORLAGE_TAGE)


@api.route("/taskforce/kpi")
@geschuetzt
def tf_kpi():
    return liste(tf.kpi_mitarbeiter(), wiedervorlage=tf.anzahl_wiedervorlage())


@api.route("/taskforce/laeufe")
@geschuetzt
def tf_laeufe():
    return liste(tf.laeufe(pid=_int("profil"), limit=_int("limit") or 30))


# ------------------------------------------------------------- Direktsuche

@api.route("/taskforce/suche")
@geschuetzt
def tf_suche():
    """Live bei allen Portalen suchen – ohne Kunde, ohne Profil, ohne Ablage.

    Die Naht, an der das CRM am wenigsten von uns wissen muss: Frage rein, Treffer raus.
    Alle Portale werden gleichzeitig gefragt, das dauert wenige Sekunden."""
    art = _text("art") if _text("art") in ("job", "wohnung", "beides") else "job"
    # Ohne Frage keine Abfrage. Ein Aufruf ohne `was` und ohne `wo` waere sonst eine
    # Rundfrage an zehn Portale fuer nichts - und der Gesamttest, der jede Seite einmal
    # aufruft, loeste bei jedem Lauf eine echte Suche aus.
    if not (_text("was") or _text("wo")):
        return liste([], art=art, hinweis="was= (Beruf oder Stichwort) oder wo= (Ort) angeben",
                     beispiel="/api/taskforce/suche?art=job&was=Lagerhelfer&wo=K%C3%B6ln&km=25")
    kriterien = {}
    if _int("gehalt"):
        kriterien["min_gehalt"] = _int("gehalt")
    if request.args.get("quereinstieg"):
        # `nur_quereinstieg` – so heisst der Schlüssel in `tf.JOB_KRITERIEN`, und nur
        # so liest ihn `tf.job_filter`. Hier stand `quereinstieg`; die Naht sagte dem
        # CRM damit „nur Quereinstieg" zu und lieferte alles. Gemessen an zwei
        # Treffern: alter Schlüssel 2 durch / 0 aussortiert, richtiger 1 / 1.
        kriterien["nur_quereinstieg"] = True
    if request.args.get("wbs"):
        kriterien["wbs"] = True
    if _text("arbeitszeit"):
        kriterien["arbeitszeiten"] = [_text("arbeitszeit")]
    p = tf.suchspalte(art=art, begriffe=_text("was") or "", ort=_text("wo") or "Köln",
                      umkreis_km=_int("km") or 25, arbeitszeit=_text("arbeitszeit"),
                      max_miete=_int("miete"), min_zimmer=request.args.get("zimmer", type=float),
                      min_flaeche=_int("flaeche"), kriterien=kriterien)
    begonnen = time.time()
    if art == "beides":
        treffer, meldungen = [], []
        for eine in ("job", "wohnung"):
            t, m = tf.direktsuche(tf.suchspalte(
                art=eine, begriffe=_text("was") or "", ort=_text("wo") or "Köln",
                umkreis_km=_int("km") or 25, arbeitszeit=_text("arbeitszeit"),
                max_miete=_int("miete"), min_zimmer=request.args.get("zimmer", type=float),
                min_flaeche=_int("flaeche"), kriterien=kriterien),
                quellen=request.args.getlist("quelle") or None, grenze=100)
            treffer += t
            meldungen += m
        treffer.sort(key=lambda x: (-(x.get("score") or 0), x.get("titel") or ""))
    else:
        treffer, meldungen = tf.direktsuche(p, quellen=request.args.getlist("quelle") or None,
                                            grenze=_int("limit") or 200)
    return liste(treffer, art=art, meldungen=meldungen,
                 suche={"was": _text("was"), "wo": _text("wo") or "Köln", "km": _int("km") or 25},
                 sekunden=round(time.time() - begonnen, 1))


@api.route("/taskforce/profil/<int:pid>/uebernehmen", methods=["POST"])
@schreibend("Treffer übernommen")
def tf_uebernehmen(pid):
    """Treffer einer Suche auf die Tafel eines Profils legen.

    Erwartet `treffer`: eine Liste, wie sie aus `/api/taskforce/suche` kommt. Was dort
    schon liegt, kommt nicht doppelt – das Gedächtnis je Profil gilt auch hier."""
    if not tf.profil(pid):
        return jsonify({"fehler": "Profil nicht gefunden"}), 404
    treffer = eingang().get("treffer") or []
    if isinstance(treffer, str):
        treffer = json.loads(treffer)
    brauchbar = [t for t in treffer if t.get("quelle") and t.get("extern_id")]
    if not brauchbar:
        return jsonify({"fehler": "keine brauchbaren Treffer",
                        "hinweis": "jeder Treffer braucht mindestens 'quelle' und 'extern_id'"}), 400
    neu = tf._ablegen(pid, brauchbar)
    return jsonify({"stand": jetzt(), "profil": pid, "uebergeben": len(brauchbar), "neu": neu,
                    "schon_da": len(brauchbar) - neu})
