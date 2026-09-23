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
import math
import os
import socket
import sqlite3
import time
import urllib.error

from flask import Blueprint, abort, jsonify, request

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
    """Eine Nummer aus der Abfrage – zu große Zahlen gelten als nicht angegeben.

    **Ein Fund der Zusammenführung.** Hier stand `request.args.get(name, type=int)`,
    und das reicht die Zahl ungeprüft bis in die SQLite-Abfrage durch. SQLite bindet
    nur 64 Bit: `/api/kunden?coach=99999999999999999999` endete deshalb in
    `OverflowError` und einem 500er statt in einer leeren Liste – dasselbe für
    `/api/taskforce/angebote?kunde=…` und `/api/taskforce/wiedervorlage?tage=…`.
    Kein Zweig konnte das allein sehen: Die Schranke `app.zahl_arg` ist in `main`
    entstanden, `api.py` in `taskforce`. Erst beide zusammen ergeben die Lücke, und
    erst die Reihe „Keine Route stürzt an einer unbrauchbaren Nummer ab" aus `main`
    zeigt sie – sie war nach dem Zusammenführen rot.

    Abgewiesen wird nichts: Eine unbrauchbare Angabe zählt als „nicht angegeben",
    der Filter bleibt offen. Das ist die Hausregel beim Filtern und steht ausführlich
    bei `app.zahl_arg` – die Regel wird von dort geholt, damit es sie nur einmal gibt.

    Der Import steht **in** der Funktion: `app` bindet diese Datei am Ende seines
    eigenen Imports ein, andersherum gäbe es einen Ringschluss."""
    import app                                  # erst zur Laufzeit, siehe oben
    return app.zahl_arg(name)


def _text(name):
    return (request.args.get(name) or "").strip() or None


# ----------------------------------------------------- Felder aus dem Körper
# `_int` und `_text` holen Werte aus der ABFRAGE (`?kunde=7`); dort kann nur Text
# ankommen, und eine unbrauchbare Angabe gilt als nicht angegeben. Im KÖRPER steht
# JSON, und dort kann in jedem Feld alles stehen: eine Liste, ein Objekt, eine Zahl
# mit dreissig Stellen. `eingang()` sorgt seit dem 23.09.2026 dafür, dass der Körper
# überhaupt eine Abbildung ist – ein Feld tiefer war es bis heute offen. Zehn
# gemessene 500er, alle mit gültigem JSON-Objekt, also an `eingang()` vorbei:
#
#   /taskforce/angebote/status     ids ["abc"] · "12,abc" · 5 · [{"a":1}] · [10^20]
#   /taskforce/angebot/<id>/status bearbeiter {"x":1} · notiz {"x":1}
#   /taskforce/profil              profil "abc" · profil [1] · kunde {"x":1}
#
# Die 64-Bit-Grenze ist dieselbe, die `_int` für Abfrageparameter längst zieht: SQLite
# bindet nicht mehr, und der `OverflowError` fällt mitten in die Transaktion. Beide
# Regeln stehen deshalb **hier** und nicht je Route – die nächste Schreibroute erbt
# sie, ohne dass jemand daran denken muss.

def feld_zahl(wert):
    """Aus einem Körperfeld eine Nummer, die SQLite binden kann – sonst `None`.

    `None` heisst „unbrauchbar", nicht „nicht angegeben": Die Routen zählen das mit und
    sagen dem Aufrufer, wie viele Angaben sie nicht lesen konnten. An einer
    Schreibschnittstelle darf nichts stillschweigend verschwinden.

    `bool` fällt heraus, obwohl Python ihn als `int` führt: `true` ist keine Nummer
    eines Angebots, und `1` wäre eine erfundene Antwort auf eine unsinnige Frage.

    **Umgewandelt wird im Versuch, nicht nach Vortest.** Hier stand
    `wert.strip().lstrip("-").isdecimal()` als Wächter vor `int(wert)` – und dieser
    Wächter hat selbst die 500er erzeugt, gegen die er gebaut war: `lstrip("-")` nimmt
    **alle** führenden Minuszeichen weg, `int()` bekommt sie danach wieder alle
    („--5" → `ValueError`), und ab 4.300 Ziffern weist `int(str)` in Python 3.12
    ohnehin ab, ganz gleich was ein Vortest sagt. Gemessen am 23.09.2026: `["--5"]`,
    `"--5"` als Textaufzählung und eine Zahl mit 4.301 Ziffern fällten zwei der drei
    Schreibrouten. Ein Vortest muss jeden Sonderfall einzeln kennen; ein Versuch mit
    Fang muss das nicht."""
    if wert is None or isinstance(wert, bool):
        return None
    if isinstance(wert, float):
        # `inf` und `NaN` sind gültiges JSON für Pythons Leser und keine Zahlen, mit
        # denen sich rechnen liesse. `int(float('inf'))` wirft `OverflowError`.
        if not math.isfinite(wert) or not wert.is_integer():
            return None
        wert = int(wert)
    if isinstance(wert, str):
        try:
            wert = int(wert.strip())
        except (ValueError, TypeError):
            return None
    if not isinstance(wert, int):
        return None
    return wert if abs(wert) <= db.SQLITE_MAX else None


def feld_text(daten, name, standard=None):
    """Ein Textfeld aus dem Körper – Text oder Zahl, niemals Liste oder Objekt.

    Eine Zahl wird zu Text: Ein CRM, das `phone: 2211234` schickt, meint eine
    Rufnummer. Eine Liste oder ein Objekt wird dagegen **abgewiesen** statt umgewandelt.
    `str({"mobil": "0170"})` ergäbe `{'mobil': '0170'}` – eine Python-Zeile, die als
    Rufnummer in den Stammdaten steht und die echte überschreibt. Gemessen am
    23.09.2026 auf `/api/kunde`: `name = "['Vorname', 'Nachname']"`, HTTP 200.

    **`true`/`false` sind kein Text**, obwohl `str(False)` einen ergäbe: „False" ist
    wahr im Sinne von `if` und landete als Rufnummer in der Akte. Gemessen am
    23.09.2026: `{"phone": false}` → HTTP 200, `telefon = "False"` – die echte
    Rufnummer eines Menschen überschrieben durch ein Wort. Ein CRM, das „keine
    Rufnummer hinterlegt" als `false` schickt, hätte damit genau das getan, was dieses
    Haus nirgends tut: einen Kontakt erfinden. `feld_zahl` wirft `bool` aus demselben
    Grund heraus.

    400 statt stillem Übergehen, weil es um Stammdaten geht: Der Aufrufer muss
    erfahren, dass sein Wert nicht angekommen ist. Ein Feld zu LEEREN geht über diese
    Schnittstelle bewusst nicht – „überschrieben wird nur, was mitgeschickt wird"."""
    if name not in daten or daten[name] is None:
        return standard
    wert = daten[name]
    if isinstance(wert, (list, tuple, dict, bool)):
        abort(400, "Feld '%s' muss Text sein, kein %s – ein verschachtelter Wert wird"
                   " nicht umgewandelt, und true/false ist kein Text."
                   % (name, type(wert).__name__))
    return str(wert).strip() or standard


def _bindbar(wert):
    """Kann SQLite diesen Wert binden – oder fällt er mitten in der Transaktion um?

    `int` über 64 Bit wirft `OverflowError`, `inf`/`NaN` sind keine Messwerte. Beides
    fällt in `with db.offen()` und reisst den ganzen Stapel mit (siehe den Kopf von
    `ZUSATZ_TEXT_MAX` in `taskforce.py`)."""
    if wert is None or isinstance(wert, (str, bool)):
        return True
    if isinstance(wert, int):
        return abs(wert) <= db.SQLITE_MAX
    if isinstance(wert, float):
        return math.isfinite(wert) and abs(wert) <= db.SQLITE_MAX
    return False


def koerper_felder(daten, objekte=()):
    """Den **ganzen** Körper auf Werte bringen, die die Module verarbeiten können.

    Für Routen, die den Körper als Ganzes weiterreichen, statt einzelne Felder zu
    lesen – `tf.profil_speichern` etwa liest je nach Art über zwanzig Schlüssel, und
    eine Liste von Feldnamen in der Route wäre am Tag ihrer Entstehung unvollständig.
    Genau das ist passiert: Acht Textfelder standen namentlich geschützt da, und
    `umkreis_km`, `max_miete`, `min_flaeche`, `quellen` und `k_wohnungstyp` fielen
    weiter in einen 500er. Hier wird deshalb **jedes** Feld geprüft, auch das, das es
    morgen gibt:

      * ein Objekt ist nur erlaubt, wo die Route es ausdrücklich erwartet (`objekte`)
      * eine Liste darf nur einfache Werte enthalten, keine Listen und keine Objekte
      * jede Zahl muss in 64 Bit passen und endlich sein

    Was durchkommt, kann noch das falsche Feld treffen – dafür prüfen die Module
    weiter selbst. Was hier abgewiesen wird, hätte keine Chance mehr, sauber zu
    scheitern: Es fiele mitten in die Transaktion."""
    sauber = {}
    for name, wert in daten.items():
        if isinstance(wert, dict):
            if name in objekte:
                sauber[name] = wert
                continue
            abort(400, "Feld '%s' darf kein Objekt sein." % name)
        if isinstance(wert, (list, tuple)):
            if any(isinstance(x, (list, tuple, dict)) or not _bindbar(x) for x in wert):
                abort(400, "Feld '%s' darf nur eine Liste einfacher Werte sein." % name)
            sauber[name] = list(wert)
            continue
        if not _bindbar(wert):
            abort(400, "Feld '%s' trägt einen Wert, den die Datenbank nicht aufnehmen"
                       " kann (Zahlen bis 64 Bit, keine unendlichen)." % name)
        sauber[name] = wert
    return sauber


@api.errorhandler(400)
def _schlechte_anfrage(fehler):
    """Auch die Absage ist JSON. Wer eine Schnittstelle bedient, liest keine HTML-Seite.

    Greift für jedes `abort(400, …)` aus diesem Blueprint – also auch für alles, was
    künftig dazukommt, ohne dass die einzelne Route etwas davon wissen muss."""
    return jsonify({"fehler": getattr(fehler, "description", None)
                    or "Die Anfrage ist nicht lesbar"}), 400


def eingang():
    """Was hereinkommt – als JSON-Körper oder als Formular, beides gilt.

    Das CRM schickt JSON. Ein Mensch, der die Schnittstelle mit curl ausprobiert, tippt
    eher `-d feld=wert`. Beides anzunehmen kostet nichts und erspart Rückfragen.

    **Heraus kommt immer eine Abbildung.** JSON kennt auf oberster Ebene auch Listen,
    Text, Zahlen und Wahrheitswerte, und `get_json` gibt genau das zurück – jedes
    `daten.get(…)` danach ist dann ein `AttributeError` und damit ein 500er. Gemessen
    am 23.09.2026 auf `/api/taskforce/profil/1/uebernehmen`, alles mit
    `Content-Type: application/json`:

        [{"quelle": "jobs.ba", "extern_id": "N9"}]   → 500  'list' hat kein .get
        "hallo"                                      → 500
        7                                            → 500
        true                                         → 500

    Die erste Form ist die, die ein CRM-Entwickler **zuerst** schickt:
    `/api/taskforce/suche` antwortet mit `{"stand":…, "anzahl":…, "daten":[…]}`, und wer
    `daten` nimmt und roh weiterreicht, hat genau diese Liste im Körper. Statt einer
    nackten Absturzseite bekommt er jetzt 400 und den Satz, was erwartet wird.

    Der Fang steht **hier** und nicht in einer Route: `_fehlt()` liest dieselbe
    Funktion, und jede künftige Route erbte den Fehler sonst mit."""
    if request.is_json:
        try:
            daten = request.get_json(silent=True)
        # Tief verschachteltes JSON bricht im C-Scanner mit `RecursionError` ab, und das
        # ist **kein** `ValueError`: `silent=True` fängt es nicht.
        except (ValueError, RecursionError):
            abort(400, "Der Körper der Anfrage ist kein lesbares JSON.")
        if daten is None:                      # unlesbar oder leer – wie ein leeres Formular
            return {}
        if not isinstance(daten, dict):
            abort(400, "Der Körper muss ein JSON-Objekt sein (geschweifte Klammern),"
                       " kein %s. Beispiel: {\"treffer\": [ … ]}" % type(daten).__name__)
        return daten
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
    # Jedes Stammdatenfeld geht durch `feld_text`: Eine Liste oder ein Objekt wird
    # abgewiesen und nicht in eine Python-Zeile umgewandelt (siehe dort).
    felder = {}
    for crm_feld, unser in CRM_AUF_UNS.items():
        for quelle in (crm_feld, unser):
            wert = feld_text(d, quelle)
            if wert:
                felder[unser] = wert
                break
    if not felder.get("name") and not felder.get("kundennummer"):
        return jsonify({"fehler": "name oder customer_number wird gebraucht"}), 400

    # **Ein unbekanntes Statuswort überschreibt nichts.** Hier stand
    # `crm_karte.STATUS.get(status, …)` mit `None` als Rückfall: Jedes Wort, das nicht
    # in den acht bekannten steht, schrieb `status_code = NULL` in den VORHANDENEN
    # Satz. Gemessen am 23.09.2026 an Kunde 7: `{"status": "in Massnahme"}` → HTTP 200,
    # `geaendert: ["kundennummer", "status_code"]`, Statuscode von 'G' auf leer.
    # Heute haben 120 von 120 Kunden einen Statuscode; wer seinen verliert, fällt aus
    # jeder Auswertung, die danach filtert – ohne Fehler, ohne Meldung, ohne Spur.
    #
    # Abgewiesen wird mit 400 und nicht still übergangen, obwohl das den ganzen Aufruf
    # kostet: Der Status entscheidet, ob ein Mensch als „Maßnahme läuft" zählt. Ein
    # Abgleich, der Namen und Telefon übernimmt und den Status heimlich liegen lässt,
    # hinterlässt zwei Systeme, die sich über denselben Kunden uneinig sind – und
    # niemand liest ein `hinweis`-Feld in einer 200er-Antwort. Die Antwort nennt die
    # acht Wörter, damit die Gegenseite in fünf Minuten weiss, was erwartet wird.
    status = (feld_text(d, "status") or "").lower()
    if status:
        if status not in crm_karte.STATUS:
            return jsonify({"fehler": "unbekannter Status",
                            "bekommen": status,
                            "erlaubt": sorted(crm_karte.STATUS),
                            "hinweis": "der vorhandene Statuscode wurde NICHT"
                                       " überschrieben; es wurde nichts gespeichert"}), 400
        felder["status_code"] = crm_karte.STATUS[status]
    # Unser eigener Buchstabe darf auch direkt kommen – aber nur einer, den es gibt.
    # Ein erfundener Buchstabe richtet denselben Schaden an wie ein leerer.
    eigener = feld_text(d, "status_code")
    if eigener:
        if eigener.upper() not in db.STATUS:
            return jsonify({"fehler": "unbekannter status_code",
                            "bekommen": eigener, "erlaubt": sorted(db.STATUS),
                            "hinweis": "es wurde nichts gespeichert"}), 400
        felder["status_code"] = eigener.upper()

    # **Eine mehrdeutige Kundennummer wird nicht geraten.** Hier stand `db.eine(…)`:
    # Liegt dieselbe Nummer zweimal im Bestand, nahm die Route den ERSTEN Satz und
    # schrieb den mitgeschickten Namen hinein. Kollidierte der Name, gab es einen
    # `IntegrityError` und eine 500er-Seite; kollidierte er nicht, gab es HTTP 200 und
    # einen überschriebenen Menschen – Datenverlust mit Erfolgsmeldung, nicht einmal
    # im Protokoll als Fehler sichtbar. Gemessen am 23.09.2026: Satz 22 hiess danach
    # so, wie der Aufruf es sagte, und die Kundenzahl blieb bei 120.
    #
    # **Warum die Nummer überhaupt zweimal dasteht.** Im Bestand betrifft es genau
    # einen Fall, und es ist eine DUBLETTE DESSELBEN MENSCHEN: zwei Sätze, gleicher
    # Vorname, der Nachname um zwei Buchstaben verschieden (Umschrift aus einer anderen
    # Schrift), gleicher Status. Es ist ausdrücklich **kein** Haushalt: Dass sich
    # Familienmitglieder eine BG-Nummer des Jobcenters teilen, klingt plausibel, war
    # hier aber falsch geraten – wer darauf eine Regel für Mehrfachnummern baut, baut
    # sie auf einen Tippfehler. Die richtige Antwort ist deshalb keine Sonderregel,
    # sondern eine Rückfrage: Das CRM bekommt beide Sätze genannt und entscheidet.
    nummer = felder.get("kundennummer")
    treffer = []
    if nummer:
        treffer = db.hole("SELECT * FROM kunde WHERE kundennummer=? AND standort=?"
                          " ORDER BY id", (nummer, db.STANDORT_STANDARD))
    if len(treffer) > 1:
        return jsonify({
            "fehler": "Kundennummer ist nicht eindeutig",
            "kundennummer": nummer,
            "gefunden": [{"kunde": t["id"], "name": t["name"],
                          "status_code": t["status_code"]} for t in treffer],
            "hinweis": "es wurde nichts geändert – den Satz über seine Nummer im OS"
                       " ansprechen (POST /api/kunde mit eindeutiger Nummer) oder die"
                       " Dublette im Bestand auflösen"}), 409
    vorhanden = treffer[0] if treffer else None
    if not vorhanden and felder.get("name"):
        vorhanden = db.eine("SELECT * FROM kunde WHERE name=? AND standort=?",
                            (felder["name"], db.STANDORT_STANDARD))

    # Der Name trägt `UNIQUE(name, standort)`. Wer einen vorhandenen Satz auf den Namen
    # eines anderen umbenennt, läuft in einen `IntegrityError` – ungefangen war das eine
    # 500er-Seite ohne Hinweis. Der Formularweg (`app.kunde_anlegen`) fängt denselben
    # Fall seit jeher ab und nennt den Satz, der im Weg steht; hier fehlte er.
    try:
        with db.offen() as con:
            if vorhanden:
                gesetzt = ", ".join(f"{f}=?" for f in felder)
                con.execute(f"UPDATE kunde SET {gesetzt}, quelle_stand='CRM', stand_am=?"
                            f" WHERE id=?",
                            list(felder.values()) + [jetzt(), vorhanden["id"]])
                kid, neu = vorhanden["id"], False
            else:
                felder.setdefault("name", nummer)
                spalten = list(felder) + ["standort", "quelle_stand", "stand_am"]
                werte = list(felder.values()) + [db.STANDORT_STANDARD, "CRM", jetzt()]
                cur = con.execute(f"INSERT INTO kunde ({', '.join(spalten)})"
                                  f" VALUES ({', '.join('?' * len(spalten))})", werte)
                kid, neu = cur.lastrowid, True
    except sqlite3.IntegrityError as e:
        schon = db.eine("SELECT id, name, kundennummer FROM kunde WHERE name=? AND standort=?",
                        (felder.get("name"), db.STANDORT_STANDARD))
        return jsonify({
            "fehler": "Name ist im Bestand schon vergeben",
            "grund": str(e)[:120],
            "gefunden": ({"kunde": schon["id"], "name": schon["name"],
                          "kundennummer": schon["kundennummer"]} if schon else None),
            "hinweis": "es wurde nichts geändert – zwei Menschen dürfen an einem"
                       " Standort nicht buchstabengleich heissen"}), 409
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
    # Erst die Typen, dann die Fachlogik – und zwar für JEDES Feld, nicht für eine
    # Liste von Namen: Diese Route reicht den ganzen Körper an `tf.profil_speichern`
    # weiter, und das liest je nach Art über zwanzig Schlüssel. `kriterien` darf als
    # Objekt kommen, alles andere nicht (siehe `koerper_felder`).
    d = koerper_felder(eingang(), objekte=("kriterien",))
    # Und die Handvoll Felder, auf denen `profil_speichern` `.strip()` ruft, muss Text
    # sein. Das ist keine zweite Regel, sondern dieselbe eine Ebene tiefer: Was dort
    # als Zahl ankäme, fiele mit `AttributeError` um.
    for _feld in ("titel", "art", "suchbegriffe", "ort", "arbeitszeit", "suchauftrag",
                  "notiz", "standort"):
        if _feld in d:
            d[_feld] = feld_text(d, _feld)
    # **Eine Quelle, die es nicht gibt, wird nicht stillschweigend gespeichert.** Das
    # ist keine Typfrage mehr, sondern eine Wertfrage, und die gehört zum Feld: Ein
    # Profil mit `quellen='5'` fragt kein Portal und sucht damit nichts – und niemand
    # sähe, warum. Über die Oberfläche kann das nicht passieren (dort stehen Haken),
    # über die Schnittstelle schon: `{"quellen": {"a": 1}}` legte die Quelle „a" an.
    # Leer bleibt leer und heisst weiter „alle Portale".
    if d.get("quellen") not in (None, "", [], ()):
        _gewaehlt = tf._mehrfach(d, "quellen")
        _bekannt = [q for q in _gewaehlt if q in tf.QUELLEN]
        if not _bekannt:
            return jsonify({"fehler": "keine dieser Quellen ist angebunden",
                            "bekommen": _gewaehlt or repr(d["quellen"])[:80],
                            "erlaubt": sorted(tf.QUELLEN),
                            "hinweis": "Feld weglassen heisst „alle Portale\""}), 400
        d["quellen"] = _bekannt
    roh_pid = d.get("profil") or d.get("id")
    pid = feld_zahl(roh_pid)
    if roh_pid is not None and pid is None:
        return jsonify({"fehler": "profil muss die Nummer eines Suchprofils sein",
                        "bekommen": repr(roh_pid)[:80]}), 400
    if not pid:
        fehlt = _fehlt("kunde", "art")
        if fehlt:
            return jsonify({"fehler": "Pflichtfelder fehlen", "felder": fehlt}), 400
        if str(d.get("art")) not in ("job", "wohnung"):
            return jsonify({"fehler": "art muss 'job' oder 'wohnung' sein"}), 400
        kid = feld_zahl(d.get("kunde_id") or d.get("kunde"))
        if kid is None:
            return jsonify({"fehler": "kunde muss die Nummer eines Kunden sein",
                            "bekommen": repr(d.get("kunde_id") or d.get("kunde"))[:80],
                            "hinweis": "die Nummer aus /api/kunden oder aus der Antwort"
                                       " von POST /api/kunde"}), 400
        d.setdefault("titel", "Arbeitssuche" if d["art"] == "job" else "Wohnungssuche")
        d.setdefault("ort", "Köln")
        d.setdefault("umkreis_km", 25)
        d.setdefault("standort", db.STANDORT_STANDARD)
        d["kunde_id"] = kid
    # Kriterien dürfen als verschachteltes Objekt kommen – das Formular kennt nur Text.
    if isinstance(d.get("kriterien"), dict):
        d["kriterien"] = json.dumps(d["kriterien"], ensure_ascii=False)
    neu = tf.profil_speichern(d, pid=pid)
    return jsonify({"stand": jetzt(), "profil": neu or pid, "angelegt": not pid})


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
    „jemand hat angeschrieben" hilft niemandem beim Nachfassen.

    `bearbeiter` und `notiz` gehen durch `feld_text`: `tf.angebot_status` ruft darauf
    `.strip()`, und ein Objekt (ein CRM, das den Bearbeiter als `{"name":…,"id":…}`
    führt) fiel dort ungefangen um."""
    d = eingang()
    status = feld_text(d, "status", "")
    if status not in tf.STATUS:
        return jsonify({"fehler": "unbekannter Status", "erlaubt": list(tf.STATUS)}), 400
    # Vor dem Schreiben ausgelesen, nicht im Aufruf: Ein unbrauchbarer Wert soll die
    # 400 werfen, bevor irgendetwas angefasst wird, und das soll man hier sehen.
    bearbeiter = feld_text(d, "bearbeiter")
    notiz = feld_text(d, "notiz")
    if not db.eine("SELECT id FROM tf_angebot WHERE id=?", (aid,)):
        return jsonify({"fehler": "Angebot nicht gefunden"}), 404
    tf.angebot_status(aid, status, bearbeiter=bearbeiter, notiz=notiz)
    return jsonify({"stand": jetzt(), "angebot": aid, "status": status})


@api.route("/taskforce/angebote/status", methods=["POST"])
@schreibend("Angebotsstatus für mehrere gesetzt")
def tf_angebote_status():
    """Mehrere Angebote auf einmal auf denselben Stand setzen.

    `ids` darf eine Liste von Nummern sein oder eine Aufzählung als Text („12,13").
    Was keine Nummer ist, wird gezählt und in `abgewiesen` genannt – dieselbe Zusage
    wie beim Übernehmen: die brauchbaren Sätze werden ausgeführt, und der Aufrufer
    erfährt, wie viele nicht lesbar waren. `[int(i) for i in ids]` stand hier ohne
    Fang, und fünf Formen fielen in einen 500er (siehe `feld_zahl`)."""
    d = eingang()
    status = feld_text(d, "status", "")
    roh = d.get("ids") or d.get("angebote") or []
    if isinstance(roh, str):
        roh = [t for t in roh.replace(";", ",").split(",") if t.strip()]
    elif not isinstance(roh, (list, tuple)):
        roh = [roh]                    # eine einzelne Nummer ist auch eine Angabe
    ids = [z for z in (feld_zahl(x) for x in roh) if z is not None]
    abgewiesen = len(roh) - len(ids)
    if status not in tf.STATUS:
        return jsonify({"fehler": "unbekannter Status", "erlaubt": list(tf.STATUS)}), 400
    if not ids:
        return jsonify({"fehler": "ids fehlen", "abgewiesen": abgewiesen,
                        "hinweis": "ids sind Nummern von Angeboten – als Liste"
                                   " [12, 13] oder als Text \"12,13\""}), 400
    bearbeiter = feld_text(d, "bearbeiter")     # vor dem Schreiben, siehe Schwesterroute
    tf.angebote_status(ids, status, bearbeiter=bearbeiter)
    return jsonify({"stand": jetzt(), "anzahl": len(ids), "status": status,
                    "abgewiesen": abgewiesen})


@api.route("/taskforce/angebot/<int:aid>/abgleich", methods=["POST"])
@schreibend("Abgleich nachgeladen")
def tf_angebot_abgleich(aid):
    """Beschreibung beim Portal nachladen und gegen den Kunden abgleichen.
    Dabei fallen auch die Kontaktdaten an – Mail, Telefon, Ansprechpartner.

    **Hier wird nach draußen gegriffen, und draußen geht vieles schief.** Die Adresse
    des Angebots kommt vom Aufrufer (über `…/uebernehmen`) und ist zu Recht nur auf
    „Text" geprüft – eine abgelaufene Anzeige, ein Portal in Wartung, ein Tippfehler:
    `tf.abgleich` wirft dann. Ungefangen war das ein 500er, während derselbe Griff am
    Bildschirm (`app.taskforce_angebot_abgleich`) längst eine Fehlermeldung zeigt.
    Gemessen am 23.09.2026 mit einer StepStone-Adresse, die es nicht gibt: Bildschirm
    302 mit Meldung, Schnittstelle 500.

    Gleiche Behandlung, nur maschinenlesbar – aber **nicht alles ist ein 400**. Für
    einen Maschinenaufrufer heisst 400 „wiederhol es nicht, so wie du fragst, geht es
    nie". Das stimmt für eine tote Adresse, und nur dafür:

        Netz (`URLError`, `HTTPError`, `TimeoutError`)   400 – die Adresse trägt der
                                                         Aufrufer, sie führt ins Leere
        Datenbank belegt (`OperationalError`)            503 – trifft sich der Nachtlauf
                                                         mit einem CRM-Aufruf, ist es
                                                         in Sekunden vorbei; wer hier
                                                         400 bekäme, verwürfe den
                                                         Auftrag für immer
        alles übrige                                     502 – das Portal hat etwas
                                                         geliefert, das wir nicht lesen
                                                         konnten. Unsere Seite, nicht
                                                         seine Frage."""
    if not db.eine("SELECT id FROM tf_angebot WHERE id=?", (aid,)):
        return jsonify({"fehler": "Angebot nicht gefunden"}), 404
    try:
        ergebnis = tf.abgleich(aid)
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        return jsonify({"fehler": "Abgleich nicht möglich",
                        "grund": f"{type(e).__name__}: {e}"[:200]}), 400
    except sqlite3.OperationalError as e:
        return jsonify({"fehler": "gerade nicht möglich",
                        "grund": f"{type(e).__name__}: {e}"[:200],
                        "hinweis": "die Datenbank ist belegt – in einer Minute erneut"
                                   " versuchen"}), 503
    except Exception as e:
        return jsonify({"fehler": "Abgleich fehlgeschlagen",
                        "grund": f"{type(e).__name__}: {e}"[:200],
                        "hinweis": "die Antwort des Portals war nicht lesbar"}), 502
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


# Wie viele Treffer ein Aufruf mitbringen darf. Der Bildschirmweg hat seine Grenze
# geschenkt bekommen: Die Treffer reisen dort im Formular mit, und Werkzeug weist ab
# 500.000 Bytes (`max_form_memory_size`) mit **413** ab, ohne dass eine Zeile entsteht.
# Die Schnittstelle hatte gar keine: `MAX_CONTENT_LENGTH` ist `None`, und 100.000 Sätze
# in einem 36-MB-Körper liefen am 23.09.2026 mit HTTP 200 in 4,2 Sekunden durch –
# 100.000 Zeilen auf der Tafel eines Kunden, ohne Fehler, ohne Meldung. Ein verirrter
# CRM-Aufruf schüttet so eine Tafel zu, und gesehen hätte es erst, wer sie öffnet.
#
# **Die beiden Grenzen können sich nicht treffen, und das ist in Ordnung.** Der
# Formularweg zählt BYTES: wie viele Treffer hineinpassen, hängt davon ab, wie groß sie
# sind – am 23.09.2026 zweimal gemessen, mit großen Sätzen (Ø 681 B) gingen 731 durch
# und 732 nicht, mit kleineren 914 und 915. Hier wird STÜCK gezählt, weil hier die
# Stückzahl der Schaden ist: Es geht
# um die Zahl der Zeilen, die auf einer Tafel landen, nicht um die Länge des Körpers.
# Eine Zahl, die beides gleichzeitig trifft, gibt es nicht; wer sie behauptet, hat eine
# der beiden Messungen nicht gemacht. Beide Wege sagen dasselbe ZU – „zu viel auf
# einmal wird abgewiesen, und es wird nichts geschrieben" –, nur eben an verschiedenen
# Maßen.
#
# 1.000 ist die Zahl für dieses Maß: `tf.direktsuche` schneidet bei 200 Treffern ab
# (bei „beides" 100 je Art), der Normalfall bringt also höchstens 200 mit. Die größte
# gewachsene Tafel im Echtbestand hat 1.424 Zeilen – über viele Läufe entstanden, nie
# über einen Aufruf. 1.000 liegt über allem, was aus einer Suche kommen kann, und weit
# unter dem, was eine Tafel unbrauchbar macht. Wer wirklich mehr hat, schickt zwei
# Aufrufe; die Antwort nennt die Grenze und die bekommene Zahl.
TREFFER_JE_AUFRUF = 1000


@api.route("/taskforce/profil/<int:pid>/uebernehmen", methods=["POST"])
@schreibend("Treffer übernommen")
def tf_uebernehmen(pid):
    """Treffer einer Suche auf die Tafel eines Profils legen.

    Erwartet `treffer`: eine Liste, wie sie aus `/api/taskforce/suche` kommt. Was dort
    schon liegt, kommt nicht doppelt – das Gedächtnis je Profil gilt auch hier.

    **Derselbe Riegel wie am Bildschirm, und zwar derselbe.** Geprüft wird jeder Satz
    mit `tf.treffer_sauber` – der Funktion, die auch der Formularweg benutzt. Hier stand
    stattdessen `t.get("quelle") and t.get("extern_id")`, und damit war diese Route über
    jeden Weg zu fällen, den der Formularweg längst abwehrt. Drei davon gemessen:

        {"treffer": [{"quelle": "wohnung.kleinanzeigen", "extern_id": "y",
                      "zusatz": {"preis": {"x": 1}}}]}
              `_score` ruft `.split` auf ein dict → `AttributeError` INNERHALB von
              `with db.offen()`: 500, und der ganze Stapel fällt zurück
        {"treffer": "{kaputtes JSON"}      `json.loads` → `ValueError` → 500
        {"treffer": [null]}                `.get` auf `None` → 500

    Dazu Feldschmuggel (`profil_id`, `status`, `score`, `doppelt_von` schreibt der
    Server, nicht der Aufrufer – `_ablegen` liest nur die Felder aus `TREFFER_TEXT`
    und `TREFFER_ZAHL`), Zahlen jenseits von 64 Bit, negative Werte, überlange
    `zusatz`-Texte und `quelle` ausserhalb von `tf.QUELLEN`.

    **Wo diese Route sich vom Bildschirmweg unterscheidet – bewusst:** Ein Mensch am
    Bildschirm bekommt eine Meldung auf der Seite, an der er steht, und die verworfenen
    Treffer fallen still weg. Das CRM bekommt stattdessen eine Zahl: `abgewiesen` sagt,
    wie viele Sätze der Riegel aussortiert hat, und fällt gar nichts durch, kommt ein
    400 mit Begründung statt einer stillen Weiterleitung. Ein Programm kann eine
    Meldung auf einer Seite nicht lesen – eine Zahl im Körper schon.

    **Und es geht nur eine Handvoll auf einmal** (`TREFFER_JE_AUFRUF`, siehe dort):
    darüber 413 mit der Grenze und der bekommenen Zahl, und es wird nichts abgelegt.
    Der Formularweg antwortet an seiner Grenze ebenso mit 413 – er misst aber die
    Größe des Rumpfes, dieser Weg die Stückzahl. Zwei Maße, eine Zusage: zu viel auf
    einmal wird laut abgewiesen, und es wird nichts geschrieben."""
    if not tf.profil(pid):
        return jsonify({"fehler": "Profil nicht gefunden"}), 404
    # Ein Körper, der gar keine Abbildung ist, endet in `eingang()` mit 400 – dort und
    # nicht hier, damit jede Route dasselbe tut (siehe `eingang`).
    daten = eingang()
    treffer = daten.get("treffer") or []
    if isinstance(treffer, str):
        # Als Formularfeld (`-d treffer=[…]`) kommt die Liste als Text an.
        try:
            treffer = json.loads(treffer)
        except (ValueError, RecursionError):
            return jsonify({"fehler": "'treffer' ist kein lesbares JSON",
                            "hinweis": "eine Liste von Treffern, wie sie"
                                       " /api/taskforce/suche liefert"}), 400
    if not isinstance(treffer, list):
        return jsonify({"fehler": "'treffer' muss eine Liste sein",
                        "bekommen": type(treffer).__name__}), 400
    # **Die Menge wird gezählt, bevor irgendetwas geprüft oder abgelegt wird.** Sonst
    # geht ein Aufruf mit 100.000 Sätzen still durch (gemessen: 36 MB Körper, HTTP 200
    # nach 4,2 s, 100.000 Zeilen auf der Tafel eines Kunden – kein Fehler, keine
    # Meldung, gesehen hätte es erst, wer die Tafel öffnet).
    if len(treffer) > TREFFER_JE_AUFRUF:
        return jsonify({"fehler": "zu viele Treffer auf einmal",
                        "grenze": TREFFER_JE_AUFRUF, "bekommen": len(treffer),
                        "abgelegt": 0,
                        "hinweis": "in mehreren Aufrufen schicken; es wurde nichts"
                                   " abgelegt"}), 413
    brauchbar = [t for t in treffer if tf.treffer_sauber(t)]
    abgewiesen = len(treffer) - len(brauchbar)
    if not brauchbar:
        return jsonify({"fehler": "keine brauchbaren Treffer",
                        "abgewiesen": abgewiesen,
                        "hinweis": "jeder Treffer braucht 'quelle' (ein Schlüssel aus"
                                   " /api/quellen) und 'extern_id' als Text; Textfelder"
                                   " bleiben Text oder null, Zahlen bleiben Zahlen,"
                                   " 'zusatz' bleibt ein Objekt"}), 400
    neu = tf._ablegen(pid, brauchbar)
    return jsonify({"stand": jetzt(), "profil": pid, "uebergeben": len(brauchbar), "neu": neu,
                    "schon_da": len(brauchbar) - neu, "abgewiesen": abgewiesen})
