# -*- coding: utf-8 -*-
"""Taskforce – Job- und Wohnungs-Agenten je Kunde.

Idee: Für jeden Kunden legt das Team ein Suchprofil an (Jobprofil und/oder
Wohnprofil). Die Agenten laufen wiederkehrend (Knopf im OS oder Aufgabenplanung
`python taskforce.py --lauf`), fragen ALLE angebundenen Quellen ab und legen
nur das ab, was noch nicht da war ("neu"). Die Taskforce sieht unter /taskforce,
was neu ist, und schreibt Arbeitgeber bzw. Vermieter an. Jeder Treffer trägt
einen Status; was einmal gesehen, angeschrieben oder verworfen wurde, kommt
nicht wieder hoch – das ist das Gedächtnis je Kunde.

Quellen (Adapter), Stand 15.09.2026:

  Jobs
  - jobs.ba            Bundesagentur für Arbeit, Jobsuche-API (öffentlich, größte
                       deutsche Stellensammlung; API-Key ist der öffentliche der BA-App)
  - jobs.stepstone     StepStone-Ergebnisliste (eingebettetes JSON der Seite)
  - jobs.kleinanzeigen Kleinanzeigen, Rubrik Jobs (HTML-Liste, Anzeigen mit data-adid)
  - jobs.arbeitnow     Arbeitnow-API (kostenlos, vor allem Büro/IT, lokal gefiltert)
  - jobs.adzuna        Adzuna-API, bündelt viele Portale – braucht ADZUNA_APP_ID/KEY
  - jobs.jooble        Jooble-API, Meta-Suche – braucht JOOBLE_KEY
  - jobs.indeed        Indeed-Ergebnisliste (JSON "results" in der Seite; braucht Browser-Kopfzeilen)
  - jobs.meinestadt    jobs.meinestadt.de (schema.org OfferCatalog im Seitenkopf)
  Kimeta rendert nur per JS und bleibt draußen.

  Wohnungen
  - wohnung.mail          ImmoScout24-Suchaufträge des Premium-Kontos: die E-Mail-Alarme
                          werden per IMAP gelesen. Zuordnung über den Suchauftragsnamen.
                          Direktes Scraping von ImmoScout24 blockt die Seite (Cloudflare)
                          und untersagt es in den Nutzungsbedingungen – der Suchauftrag
                          liefert dieselben Treffer offiziell per Mail.
  - wohnung.kleinanzeigen Kleinanzeigen, Rubrik Mietwohnungen (Angebote, keine Gesuche)
  - wohnung.link          Von Hand eingetragene Exposé-Links (Fallback, Kontrolle)
"""
import base64
import datetime
import email
import email.header
import gzip
import html
import http.cookiejar
import imaplib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import datenbank as db

SCHEMA = """
CREATE TABLE IF NOT EXISTS tf_profil (
    id            INTEGER PRIMARY KEY,
    kunde_id      INTEGER REFERENCES kunde(id),
    art           TEXT NOT NULL,            -- 'job' | 'wohnung'
    titel         TEXT NOT NULL,
    suchbegriffe  TEXT,                     -- Jobs: Berufe/Stichworte, kommagetrennt
    ort           TEXT,
    umkreis_km    INTEGER DEFAULT 25,
    arbeitszeit   TEXT,                     -- vz | tz | mj | '' = egal
    zeitarbeit    INTEGER DEFAULT 1,        -- 1 = Zeitarbeit zulassen
    max_miete     INTEGER,                  -- Wohnung: Kaltmiete in Euro
    min_zimmer    REAL,
    min_flaeche   INTEGER,
    suchauftrag   TEXT,                     -- Name des ImmoScout-Suchauftrags
    notiz         TEXT,
    aktiv         INTEGER DEFAULT 1,
    erstellt      TEXT,
    letzter_lauf  TEXT,
    standort      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tf_angebot (
    id            INTEGER PRIMARY KEY,
    profil_id     INTEGER REFERENCES tf_profil(id),
    quelle        TEXT NOT NULL,
    extern_id     TEXT NOT NULL,
    titel         TEXT,
    anbieter      TEXT,                     -- Arbeitgeber bzw. Vermieter/Anbieter
    ort           TEXT,
    entfernung_km INTEGER,
    url           TEXT,
    zusatz        TEXT,                     -- JSON: Arbeitszeit, Preis, Zimmer, ...
    veroeffentlicht TEXT,
    gefunden_am   TEXT,
    status        TEXT DEFAULT 'neu',       -- neu | gesehen | angeschrieben | verworfen
    bearbeiter    TEXT,
    notiz         TEXT,
    status_am     TEXT,
    UNIQUE(profil_id, quelle, extern_id)
);
CREATE TABLE IF NOT EXISTS tf_lauf (
    id         INTEGER PRIMARY KEY,
    profil_id  INTEGER,
    zeitpunkt  TEXT,
    quelle     TEXT,
    gefunden   INTEGER,
    neu        INTEGER,
    meldung    TEXT
);
CREATE TABLE IF NOT EXISTS tf_ereignis (
    id         INTEGER PRIMARY KEY,
    angebot_id INTEGER REFERENCES tf_angebot(id),
    status     TEXT NOT NULL,
    bearbeiter TEXT,
    zeitpunkt  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tf_angebot_status ON tf_angebot(profil_id, status);
CREATE INDEX IF NOT EXISTS idx_tf_ereignis ON tf_ereignis(bearbeiter, zeitpunkt);
"""
NACHRUESTEN = [("tf_profil", "quellen", "TEXT"),          # kommagetrennt, leer = alle
               ("tf_angebot", "score", "REAL"),          # Relevanz 0-10, sortiert die Tafel
               ("tf_angebot", "doppelt_von", "INTEGER"),  # dieselbe Stelle aus anderer Quelle
               ("tf_angebot", "beschreibung", "TEXT"),    # Kurztext, wenn die Quelle einen hat
               ("tf_profil", "kriterien", "TEXT"),        # JSON: Wohnkriterien (WBS, Balkon, Etage, …), Extra-Notizen
               ("tf_angebot", "beschreibung_lang", "TEXT"),  # volle Stellen-/Exposébeschreibung, nachgeladen
               ("tf_angebot", "abgleich", "TEXT"),        # JSON: passt / fehlt / unklar / plus
               ("tf_angebot", "match", "REAL"),           # Anteil erfüllter Anforderungen in Prozent
               ("tf_angebot", "abgleich_am", "TEXT")]

STATUS = ("neu", "gesehen", "angeschrieben", "antwort", "erfolg", "verworfen", "doppelt")
STATUS_TEXT = {"neu": "neu", "gesehen": "gesehen", "angeschrieben": "angeschrieben",
               "antwort": "Antwort erhalten", "erfolg": "Erfolg (Gespräch/Besichtigung/Zusage)",
               "verworfen": "verworfen", "doppelt": "Dublette (andere Quelle)"}
ARBEITSZEIT = {"": "egal", "vz": "Vollzeit", "tz": "Teilzeit", "mj": "Minijob",
               "ho": "Homeoffice", "snw": "Schicht/Nacht/Wochenende"}
# Vollständige Browser-Kopfzeilen: Indeed und jobs.meinestadt.de antworten einem nackten
# Python-Client mit 403, einem Browser mit 200. Cookies werden je Lauf gehalten.
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "de-DE,de;q=0.9,en;q=0.8", "Accept-Encoding": "gzip",
      "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document",
      "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
      "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
      "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"'}
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
PAUSE = 1.2   # Sekunden zwischen zwei Seitenabrufen derselben Quelle – höflich bleiben


def jetzt():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def init():
    with db.offen() as con:
        con.executescript(SCHEMA)
        for tabelle, spalte, typ in NACHRUESTEN:
            vorhanden = {r[1] for r in con.execute(f"PRAGMA table_info({tabelle})")}
            if spalte not in vorhanden:
                con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")


def _get(url, hdr=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(hdr or {})})
    with _OPENER.open(req, timeout=timeout) as r:
        roh = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            roh = gzip.decompress(roh)
        return r.geturl(), roh.decode("utf-8", "replace")


def _post_json(url, daten, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(daten).encode("utf-8"), headers={
        **UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ------------------------------------------------------------------ Profile

FELDER = ("kunde_id", "art", "titel", "suchbegriffe", "ort", "umkreis_km", "arbeitszeit",
          "zeitarbeit", "max_miete", "min_zimmer", "min_flaeche", "suchauftrag", "notiz",
          "aktiv", "quellen")


def _zahl(v, typ=int):
    try:
        return typ(str(v).replace(",", ".")) if str(v).strip() else None
    except ValueError:
        return None


def profil_speichern(daten, pid=None):
    """Profil anlegen oder ändern.

    Zwei Dinge, die hier früher schiefgingen und beide teuer waren:

    **Abgewählte Haken.** Ein nicht angekreuztes Kästchen schickt der Browser gar nicht mit.
    Wer „aktiv" fehlend als „an" liest, kann ein Profil nie pausieren und Zeitarbeit nie
    ausschließen – beide Schalter waren tot. Das Formular schickt deshalb `formular=1` mit:
    dann heißt fehlend wirklich aus.

    **Halbe Formulare.** Kam ein Speichern ohne Felder an (abgebrochenes Laden, doppelter
    Klick), wurden Titel, Suchbegriffe und Ort gelöscht und ein Jobprofil wurde stillschweigend
    zum Wohnprofil. Beim Ändern gilt deshalb: Was nicht im Formular steht, bleibt wie es war."""
    vorhanden = profil(pid) if pid else {}
    vom_formular = str(daten.get("formular") or "") == "1"

    def gesetzt(feld):
        return feld in daten if hasattr(daten, "__contains__") else False

    d = {f: (daten.get(f) if gesetzt(f) else vorhanden.get(f)) for f in FELDER}

    def haken(feld, standard=1):
        if gesetzt(feld):
            return 1 if str(daten.get(feld)).lower() in ("1", "on", "ja", "true") else 0
        if vom_formular:
            return 0                      # Kästchen war abgewählt
        return int(vorhanden.get(feld, standard) or 0) if vorhanden else standard

    d["umkreis_km"] = _zahl(d.get("umkreis_km")) or 25
    d["max_miete"] = _zahl(d.get("max_miete"))
    d["min_zimmer"] = _zahl(d.get("min_zimmer"), float)
    d["min_flaeche"] = _zahl(d.get("min_flaeche"))
    d["zeitarbeit"] = haken("zeitarbeit")
    d["aktiv"] = haken("aktiv")
    if not d.get("art"):
        d["art"] = vorhanden.get("art") or "job"
    d["titel"] = (d.get("titel") or "").strip() or ("Jobsuche" if d["art"] == "job" else "Wohnungssuche")
    q = _mehrfach(daten, "quellen") or (d.get("quellen") if not vom_formular else None)
    if isinstance(q, (list, tuple)):
        q = ",".join(x for x in q if x)
    d["quellen"] = (q or "").strip() or None
    for f in ("suchbegriffe", "ort", "arbeitszeit", "suchauftrag", "notiz"):
        d[f] = (d.get(f) or "").strip() or None
    # Kriterien nur anfassen, wenn welche mitkommen – sonst bliebe beim Ändern eines
    # anderen Feldes die ganze Reglerstellung auf der Strecke.
    if not pid or vom_formular or any(str(f).startswith("k_") for f in daten):
        krit = kriterien_aus_formular(daten, d["art"])
        d["kriterien"] = json.dumps(krit, ensure_ascii=False) if krit else None
    else:
        d["kriterien"] = vorhanden.get("kriterien")
    with db.offen() as con:
        if pid:
            con.execute(
                "UPDATE tf_profil SET titel=?, suchbegriffe=?, ort=?, umkreis_km=?, arbeitszeit=?,"
                " zeitarbeit=?, max_miete=?, min_zimmer=?, min_flaeche=?, suchauftrag=?, notiz=?,"
                " aktiv=?, quellen=?, kriterien=? WHERE id=?",
                (d["titel"], d["suchbegriffe"], d["ort"], d["umkreis_km"], d["arbeitszeit"],
                 d["zeitarbeit"], d["max_miete"], d["min_zimmer"], d["min_flaeche"],
                 d["suchauftrag"], d["notiz"], d["aktiv"], d["quellen"], d["kriterien"], pid))
            return pid
        cur = con.execute(
            "INSERT INTO tf_profil (kunde_id, art, titel, suchbegriffe, ort, umkreis_km,"
            " arbeitszeit, zeitarbeit, max_miete, min_zimmer, min_flaeche, suchauftrag, notiz,"
            " aktiv, quellen, kriterien, erstellt, standort) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["kunde_id"], d["art"], d["titel"], d["suchbegriffe"], d["ort"], d["umkreis_km"],
             d["arbeitszeit"], d["zeitarbeit"], d["max_miete"], d["min_zimmer"], d["min_flaeche"],
             d["suchauftrag"], d["notiz"], d["aktiv"], d["quellen"], d["kriterien"], jetzt(),
             db.STANDORT_STANDARD))
        return cur.lastrowid


# ------------------------------------------------------ Wohnkriterien (IS24-Regler)
# Alles, was ImmoScout24 an Reglern hat, gibt es hier auch – gespeichert als JSON im Profil,
# als Vorbelegung für den IS24-Suchlink und als Maßstab für den Abgleich jedes Exposés.
KRITERIEN = [   # (Schlüssel, Beschriftung, Typ: ja|zahl|text|liste|lang, IS24-Ausstattungscode)
    ("wbs",          "WBS (Wohnberechtigungsschein)", "ja", None),
    ("barrierefrei", "barrierefrei / stufenlos",      "ja", "handicappedaccessible"),
    ("balkon",       "Balkon / Terrasse",             "ja", "balcony"),
    ("garten",       "Garten",                        "ja", "garden"),
    ("einbaukueche", "Einbauküche",                   "ja", "builtinkitchen"),
    ("keller",       "Keller",                        "ja", "cellar"),
    ("aufzug",       "Aufzug",                        "ja", "lift"),
    ("gaeste_wc",    "Gäste-WC",                      "ja", "guesttoilet"),
    ("stellplatz",   "Stellplatz / Garage",           "ja", "parking"),
    ("haustiere",    "Haustiere erlaubt",             "ja", None),
    ("neubau",       "Neubau",                        "ja", None),
    ("kein_tausch",  "Tauschwohnungen ausschließen",  "ja", None),
    ("max_warmmiete", "max. Warmmiete €",             "zahl", None),
    ("max_zimmer",   "max. Zimmer",                   "zahl", None),
    ("max_flaeche",  "max. Fläche m²",                "zahl", None),
    ("etage_min",    "Etage von",                     "zahl", None),
    ("etage_max",    "Etage bis",                     "zahl", None),
    ("baujahr_min",  "Baujahr ab",                    "zahl", None),
    ("personen",     "Personen im Haushalt",          "zahl", None),
    ("einzug_ab",    "Einzug ab",                     "text", None),
    ("wohnungstyp",  "Wohnungstyp",                   "liste", None),
    ("extra",        "Extra-Notizen",                 "lang", None),
]
KRITERIEN_TYP = {k: t for k, _, t, _ in KRITERIEN}
KRITERIEN_TEXT = {k: b for k, b, _, _ in KRITERIEN}

# ------------------------------------------------------- Jobkriterien (Stellenanzeigen)
# Dieselbe Idee wie bei den Wohnungen: jeder Regler, den die Stellenbörse kennt, gibt es
# hier auch. Die letzte Spalte ist der Parameter der Jobsuche-Schnittstelle der
# Bundesagentur; alles ohne Parameter prüft das OS selbst am Ergebnis – dann gilt der
# Regler für *jede* Quelle, auch für StepStone, Indeed und die anderen.
#
# Die vier Parameter sind am 16.09.2026 live gegen die Schnittstelle geprüft: mehrere
# Arbeitszeiten müssen mit Semikolon getrennt werden (`vz;tz`), mit Komma liefert die
# Schnittstelle null Treffer.
ANGEBOTSARTEN = [("1", "Arbeitsstelle"), ("4", "Ausbildung / duales Studium"),
                 ("34", "Praktikum / Trainee"), ("2", "Selbstständigkeit")]
BEFRISTUNGEN = [("", "egal"), ("2", "nur unbefristet"), ("1", "nur befristet")]
ARBEITSZEITEN = [("vz", "Vollzeit"), ("tz", "Teilzeit"), ("snw", "Schicht, Nacht, Wochenende"),
                 ("ho", "Homeoffice / Telearbeit"), ("mj", "Minijob")]

JOB_KRITERIEN = [   # (Schlüssel, Beschriftung, Typ, Parameter der BA-Jobsuche)
    ("angebotsart",      "Art der Stelle",                "wahl", "angebotsart"),
    ("befristung",       "Befristung",                    "wahl", "befristung"),
    ("arbeitszeiten",    "Arbeitszeit",                   "mehr", "arbeitszeit"),
    ("behinderung",      "für Schwerbehinderte geeignet", "ja",   "behinderung"),
    ("tage",             "veröffentlicht in den letzten … Tagen", "zahl", "veroeffentlichtseit"),
    ("max_entfernung",   "max. Entfernung km",            "zahl", None),
    ("min_gehalt",       "min. Gehalt € im Monat",        "zahl", None),
    ("nur_quereinstieg", "nur Angebote für Quereinsteiger", "ja", None),
    ("muss_woerter",     "muss im Text stehen",           "text", None),
    ("ohne_woerter",     "darf nicht im Text stehen",     "text", None),
    ("nur_arbeitgeber",  "nur diese Arbeitgeber",         "text", None),
    ("ohne_arbeitgeber", "diese Arbeitgeber ausschließen", "text", None),
    ("extra",            "Extra-Notizen",                 "lang", None),
]
JOB_WAHLEN = {"angebotsart": ANGEBOTSARTEN, "befristung": BEFRISTUNGEN}
JOB_MEHRFACH = {"arbeitszeiten": ARBEITSZEITEN}


def kriterien_liste(art):
    """Die Regler, die zu dieser Art Profil gehören."""
    return JOB_KRITERIEN if art == "job" else KRITERIEN
WOHNUNGSTYPEN = [("groundfloor", "Erdgeschoss"), ("raisedgroundfloor", "Hochparterre"),
                 ("apartment", "Etagenwohnung"), ("roofstorey", "Dachgeschoss"),
                 ("maisonette", "Maisonette"), ("penthouse", "Penthouse"),
                 ("terracedflat", "Terrassenwohnung"), ("loft", "Loft"), ("halfbasement", "Souterrain")]


def _mehrfach(daten, feld):
    """Mehrfachauswahl aus dem Formular – getlist, wenn es das gibt, sonst Komma-Text."""
    if hasattr(daten, "getlist"):
        return [x for x in daten.getlist(feld) if x]
    v = daten.get(feld)
    if isinstance(v, str):
        return [x for x in v.split(",") if x]
    return [x for x in (v or []) if x]


def kriterien_aus_formular(daten, art="wohnung"):
    """Formularfelder k_<schlüssel> → Kriterien-Dict. Nur, was gesetzt ist, wird gespeichert."""
    krit = {}
    for schl, _, typ, _ in kriterien_liste(art):
        v = daten.get("k_" + schl)
        if typ == "ja":
            if str(v or "") in ("1", "on", "ja", "true"):
                krit[schl] = True
        elif typ == "zahl":
            z = _zahl(v)
            if z is not None:
                krit[schl] = z
        elif typ == "liste":
            werte = _mehrfach(daten, "k_" + schl)
            werte = [x for x in werte if x in dict(WOHNUNGSTYPEN)]
            if werte:
                krit[schl] = werte
        elif typ == "mehr":
            erlaubt = dict(JOB_MEHRFACH.get(schl) or [])
            werte = [x for x in _mehrfach(daten, "k_" + schl) if x in erlaubt]
            if werte:
                krit[schl] = werte
        elif typ == "wahl":
            erlaubt = dict(JOB_WAHLEN.get(schl) or [])
            t = (v or "").strip() if isinstance(v, str) else ""
            if t and t in erlaubt:
                krit[schl] = t
        else:
            t = (v or "").strip() if isinstance(v, str) else ""
            if t:
                krit[schl] = t
    return krit


def kriterien(p):
    try:
        return json.loads(p.get("kriterien") or "{}") if isinstance(p, dict) else {}
    except (TypeError, ValueError):
        return {}


def kriterien_text(p):
    """Lesbare Kurzform der Kriterien für Karten und Export."""
    k = kriterien(p)
    art = (p or {}).get("art") or "wohnung"
    teile = []
    for schl, bez, typ, _ in kriterien_liste(art):
        if schl not in k or schl == "extra":
            continue
        if typ == "ja":
            teile.append(bez)
        elif typ == "liste":
            teile.append(", ".join(dict(WOHNUNGSTYPEN).get(x, x) for x in k[schl]))
        elif typ == "mehr":
            benannt = dict(JOB_MEHRFACH.get(schl) or [])
            teile.append(", ".join(benannt.get(x, x) for x in k[schl]))
        elif typ == "wahl":
            benannt = dict(JOB_WAHLEN.get(schl) or [])
            teile.append(benannt.get(k[schl], k[schl]))
        else:
            teile.append(f"{bez} {k[schl]}")
    return " · ".join(teile)


IS24_LAND = {"köln": "nordrhein-westfalen", "duisburg": "nordrhein-westfalen", "berlin": "berlin",
             "hamburg": "hamburg", "bremen": "bremen", "münchen": "bayern", "frankfurt am main": "hessen",
             "frankfurt": "hessen", "stuttgart": "baden-wuerttemberg", "hannover": "niedersachsen",
             "mainz": "rheinland-pfalz", "koblenz": "rheinland-pfalz", "saarbrücken": "saarland",
             "kassel": "hessen", "wiesbaden": "hessen", "darmstadt": "hessen"}


def _is24_slug(ort):
    o = (ort or "Köln").strip().casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        o = o.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", o).strip("-")


def is24_url(p):
    """Vorbefüllte ImmoScout24-Suche aus dem Wohnprofil. Damit legt die Taskforce im
    Premium-Konto den Suchauftrag mit zwei Klicks an (Suche öffnen → „Suchauftrag speichern",
    Name = Suchauftragsname des Profils). Direktes Abfragen von IS24 bleibt bewusst aus."""
    k = kriterien(p)
    ort = (p.get("ort") or "Köln").strip()
    land = IS24_LAND.get(ort.casefold(), "nordrhein-westfalen")
    q = {"sorting": "2", "enteredFrom": "result_list"}
    if p.get("max_miete"):
        q["price"] = f"-{int(p['max_miete'])}.0"
    elif k.get("max_warmmiete"):
        q["price"] = f"-{int(k['max_warmmiete'])}.0"
        q["pricetype"] = "calculatedtotalrent"
    lo, hi = p.get("min_zimmer"), k.get("max_zimmer")
    if lo or hi:
        q["numberofrooms"] = f"{lo or ''}-{hi or ''}".replace("None", "")
    lo, hi = p.get("min_flaeche"), k.get("max_flaeche")
    if lo or hi:
        q["livingspace"] = f"{lo or ''}-{hi or ''}"
    lo, hi = k.get("etage_min"), k.get("etage_max")
    if lo is not None or hi is not None:
        q["floor"] = f"{'' if lo is None else lo}-{'' if hi is None else hi}"
    if k.get("baujahr_min"):
        q["constructionyear"] = f"{k['baujahr_min']}-"
    ausstattung = [code for schl, _, _, code in KRITERIEN if code and k.get(schl)]
    if ausstattung:
        q["equipment"] = ",".join(ausstattung)
    if k.get("haustiere"):
        q["petsallowedtypes"] = "yes,negotiable"
    if k.get("neubau"):
        q["newbuilding"] = "true"
    if k.get("kein_tausch"):
        q["exclusioncriteria"] = "swapflat"
    if k.get("wohnungstyp"):
        q["apartmenttypes"] = ",".join(k["wohnungstyp"])
    return (f"https://www.immobilienscout24.de/Suche/de/{land}/{_is24_slug(ort)}/wohnung-mieten?"
            + urllib.parse.urlencode(q, safe=",-."))


def profil(pid):
    return db.eine(
        "SELECT p.*, k.name AS kunde, k.sprache, k.telefon, k.ort_jc, m.name AS coach"
        "  FROM tf_profil p JOIN kunde k ON k.id=p.kunde_id"
        "  LEFT JOIN mitarbeiter m ON m.id=k.coach_id WHERE p.id=?", (pid,))


def profile_von(kunde_id):
    return db.hole(
        "SELECT p.*,"
        "  (SELECT COUNT(*) FROM tf_angebot a WHERE a.profil_id=p.id AND a.status='neu') AS neu,"
        "  (SELECT COUNT(*) FROM tf_angebot a WHERE a.profil_id=p.id) AS gesamt"
        "  FROM tf_profil p WHERE p.kunde_id=? ORDER BY p.art, p.id", (kunde_id,))


def profil_loeschen(pid):
    with db.offen() as con:
        con.execute("DELETE FROM tf_angebot WHERE profil_id=?", (pid,))
        con.execute("DELETE FROM tf_lauf WHERE profil_id=?", (pid,))
        con.execute("DELETE FROM tf_profil WHERE id=?", (pid,))


def uebersicht(standort=db.STANDORT_STANDARD):
    """Alle Profile mit Zählern – die Tafel der Taskforce."""
    return db.hole(
        "SELECT p.*, k.name AS kunde, k.sprache, m.name AS coach,"
        "  (SELECT COUNT(*) FROM tf_angebot a WHERE a.profil_id=p.id AND a.status='neu') AS neu,"
        "  (SELECT COUNT(*) FROM tf_angebot a WHERE a.profil_id=p.id"
        "     AND a.status IN ('angeschrieben','antwort','erfolg')) AS angeschrieben,"
        "  (SELECT COUNT(*) FROM tf_angebot a WHERE a.profil_id=p.id) AS gesamt"
        "  FROM tf_profil p JOIN kunde k ON k.id=p.kunde_id"
        "  LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
        " WHERE p.standort=? ORDER BY neu DESC, k.name, p.art", (standort,))


def anzahl_neu(standort=db.STANDORT_STANDARD):
    return db.wert(
        "SELECT COUNT(*) FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
        " WHERE a.status='neu' AND p.standort=? AND p.aktiv=1", (standort,)) or 0


def neue_angebote(limit=80, standort=db.STANDORT_STANDARD, kunde_id=None, art=None,
                  quelle=None, suche=None, status="neu", coach_id=None, min_score=None,
                  min_match=None, max_km=None, seit_tage=None, arbeitszeit=None,
                  quereinstieg=None, min_gehalt=None, max_miete=None, min_zimmer=None,
                  min_flaeche=None, sortierung=None):
    """Die Tafel: nach Relevanz sortiert, fein filterbar. Dubletten bleiben draußen.

    Was in Spalten steht, filtert die Datenbank. Was im Zusatz-JSON jeder Quelle steckt
    (Arbeitszeit, Gehalt, Miete, Zimmer), prüft Python danach – die Portale schreiben
    diese Angaben zu unterschiedlich, als dass SQL sie sauber vergleichen könnte."""
    sql = ("SELECT a.*, p.art, p.titel AS profil_titel, k.name AS kunde, k.id AS kunde_id,"
           " m.name AS coach"
           "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
           "  LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
           " WHERE p.standort=? AND p.aktiv=1")
    args = [standort]
    if status:
        sql += " AND a.status=?"; args.append(status)
    if kunde_id:
        sql += " AND k.id=?"; args.append(kunde_id)
    if coach_id:
        sql += " AND k.coach_id=?"; args.append(coach_id)
    if art:
        sql += " AND p.art=?"; args.append(art)
    if quelle:
        sql += " AND a.quelle=?"; args.append(quelle)
    if suche:
        sql += " AND (a.titel LIKE ? OR a.anbieter LIKE ? OR a.ort LIKE ?)"
        args += [f"%{suche}%"] * 3
    if min_score is not None:
        sql += " AND COALESCE(a.score,0) >= ?"; args.append(min_score)
    if min_match is not None:
        sql += " AND COALESCE(a.match,0) >= ?"; args.append(min_match)
    if max_km is not None:
        sql += " AND (a.entfernung_km IS NULL OR a.entfernung_km <= ?)"; args.append(max_km)
    if seit_tage:
        grenze = (datetime.datetime.now() - datetime.timedelta(days=int(seit_tage))).isoformat()
        sql += " AND a.gefunden_am >= ?"; args.append(grenze)
    if sortierung == "neu":
        sql += " ORDER BY a.gefunden_am DESC, a.id DESC"
    elif sortierung == "abgleich":
        sql += " ORDER BY COALESCE(a.match,-1) DESC, COALESCE(a.score,0) DESC"
    elif sortierung == "naehe":
        sql += " ORDER BY CASE WHEN a.entfernung_km IS NULL THEN 1 ELSE 0 END, a.entfernung_km"
    else:
        sql += " ORDER BY COALESCE(a.score,0) DESC, a.gefunden_am DESC, a.id DESC"
    sql += " LIMIT ?"
    args.append(limit * 4 if (arbeitszeit or quereinstieg or min_gehalt or max_miete
                              or min_zimmer or min_flaeche) else limit)
    zeilen = db.hole(sql, args)

    def wert(a, feld):
        return (zusatz(a) or {}).get(feld)

    gefiltert = []
    for a in zeilen:
        z = zusatz(a) or {}
        if arbeitszeit:
            codes = z.get("arbeitszeit_codes") or []
            text = (z.get("arbeitszeit") or "").casefold()
            benannt = dict(ARBEITSZEITEN).get(arbeitszeit, "").casefold()
            if codes or text:
                if arbeitszeit not in codes and benannt.split(",")[0] not in text:
                    continue
        if quereinstieg and not z.get("quereinstieg"):
            continue
        if min_gehalt is not None:
            g = _geld(z.get("gehalt"))
            if g is not None and g < min_gehalt:
                continue
        if max_miete is not None:
            miete = _geld(z.get("warmmiete")) or _geld(z.get("preis"))
            if miete is not None and miete > max_miete:
                continue
        if min_zimmer is not None:
            zi = _zahl_aus(z.get("zimmer"))
            if zi is not None and zi < min_zimmer:
                continue
        if min_flaeche is not None:
            fl = _zahl_aus(z.get("flaeche"))
            if fl is not None and fl < min_flaeche:
                continue
        gefiltert.append(a)
        if len(gefiltert) >= limit:
            break
    return gefiltert


def angebote_zaehlen(standort=db.STANDORT_STANDARD):
    """Zähler je Status über alle aktiven Profile."""
    zeilen = db.hole(
        "SELECT a.status, COUNT(*) AS n FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
        " WHERE p.standort=? AND p.aktiv=1 GROUP BY a.status", (standort,))
    return {z["status"]: z["n"] for z in zeilen}


def angebote(pid, status=None):
    sql = "SELECT * FROM tf_angebot WHERE profil_id=?"
    args = [pid]
    if status:
        sql += " AND status=?"
        args.append(status)
    return db.hole(sql + " ORDER BY CASE status WHEN 'neu' THEN 0 WHEN 'gesehen' THEN 1"
                   " WHEN 'angeschrieben' THEN 2 WHEN 'antwort' THEN 3 WHEN 'erfolg' THEN 4"
                   " WHEN 'verworfen' THEN 5 ELSE 6 END, COALESCE(score,0) DESC,"
                   " gefunden_am DESC, id DESC", args)


def angebot_status(aid, status, bearbeiter=None, notiz=None):
    if status not in STATUS:
        return
    with db.offen() as con:
        alt = con.execute("SELECT bearbeiter FROM tf_angebot WHERE id=?", (aid,)).fetchone()
        wer = (bearbeiter or "").strip() or (alt[0] if alt else None)
        con.execute(
            "UPDATE tf_angebot SET status=?, status_am=?, bearbeiter=?,"
            " notiz=COALESCE(?, notiz) WHERE id=?",
            (status, jetzt(), wer, notiz if notiz is not None else None, aid))
        # Jede Statusänderung ist ein Ereignis – daraus entstehen die KPIs je Mitarbeiter.
        con.execute("INSERT INTO tf_ereignis (angebot_id, status, bearbeiter, zeitpunkt)"
                    " VALUES (?,?,?,?)", (aid, status, wer, jetzt()))


def angebote_status(ids, status, bearbeiter=None):
    """Sammelaktion der Tafel: viele Angebote auf einmal umstellen."""
    n = 0
    for aid in ids:
        try:
            angebot_status(int(aid), status, bearbeiter)
            n += 1
        except (TypeError, ValueError):
            continue
    return n


WIEDERVORLAGE_TAGE = 7          # so lange geben wir einem Arbeitgeber oder Vermieter Zeit


def wiedervorlage(tage=WIEDERVORLAGE_TAGE, standort=db.STANDORT_STANDARD, limit=60):
    """Angeschrieben, aber seit Tagen nichts gehört - das ist die eigentliche Arbeit.

    Ein Anschreiben ohne Nachfassen ist verschenkte Arbeit: die meisten Zusagen kommen
    erst nach dem zweiten Kontakt. Die Tafel zeigt sonst nur Neues und verliert genau
    die Faelle aus dem Blick, in die schon Arbeit geflossen ist."""
    grenze = (datetime.datetime.now() - datetime.timedelta(days=tage)).isoformat()
    return db.hole(
        "SELECT a.*, p.art, p.titel AS profil_titel, k.name AS kunde, k.id AS kunde_id,"
        "  CAST(julianday('now') - julianday(a.status_am) AS INT) AS tage_offen"
        "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
        " WHERE a.status='angeschrieben' AND p.standort=? AND p.aktiv=1 AND a.status_am <= ?"
        " ORDER BY a.status_am LIMIT ?", (standort, grenze, limit))


def anzahl_wiedervorlage(tage=WIEDERVORLAGE_TAGE, standort=db.STANDORT_STANDARD):
    grenze = (datetime.datetime.now() - datetime.timedelta(days=tage)).isoformat()
    return db.wert(
        "SELECT COUNT(*) FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
        " WHERE a.status='angeschrieben' AND p.standort=? AND p.aktiv=1 AND a.status_am <= ?",
        (standort, grenze)) or 0


def nachgefasst(aid, bearbeiter=None):
    """Zweiter Kontakt: Status bleibt, die Uhr der Wiedervorlage beginnt von vorn."""
    with db.offen() as con:
        alt = con.execute("SELECT bearbeiter, notiz FROM tf_angebot WHERE id=?", (aid,)).fetchone()
        wer = (bearbeiter or "").strip() or (alt[0] if alt else None)
        notiz = (alt[1] if alt else None) or ""
        marke = f"nachgefasst {datetime.date.today():%d.%m.}"
        con.execute("UPDATE tf_angebot SET status_am=?, bearbeiter=?, notiz=? WHERE id=?",
                    (jetzt(), wer, (notiz + " · " + marke).strip(" ·")[:400], aid))
        con.execute("INSERT INTO tf_ereignis (angebot_id, status, bearbeiter, zeitpunkt)"
                    " VALUES (?,?,?,?)", (aid, "nachgefasst", wer, jetzt()))


def kpi_mitarbeiter(standort=db.STANDORT_STANDARD):
    """KPIs je Bearbeiter: was hat wer heute, diese Woche, diesen Monat bewegt.

    Zählt Ereignisse, nicht Endzustände - wer ein Angebot anschreibt und später auf
    "Antwort" setzt, behält das Anschreiben in der Zählung."""
    heute = datetime.date.today()
    woche = heute - datetime.timedelta(days=heute.weekday())
    monat = heute.replace(day=1)
    zeilen = db.hole(
        "SELECT COALESCE(NULLIF(bearbeiter,''), '(ohne Namen)') AS wer, status,"
        "  SUBSTR(zeitpunkt,1,10) AS tag, COUNT(*) AS n"
        "  FROM tf_ereignis GROUP BY wer, status, tag")
    kpi = {}
    for z in zeilen:
        k = kpi.setdefault(z["wer"], {f"{s}_{r}": 0 for s in ("angeschrieben", "antwort", "erfolg",
                                                            "gesehen", "verworfen", "nachgefasst")
                                     for r in ("heute", "woche", "monat", "gesamt")})
        if z["status"] not in ("angeschrieben", "antwort", "erfolg", "gesehen", "verworfen",
                               "nachgefasst"):
            continue
        tag = datetime.date.fromisoformat(z["tag"])
        k[f"{z['status']}_gesamt"] += z["n"]
        if tag >= monat:
            k[f"{z['status']}_monat"] += z["n"]
        if tag >= woche:
            k[f"{z['status']}_woche"] += z["n"]
        if tag == heute:
            k[f"{z['status']}_heute"] += z["n"]
    liste = []
    for wer, k in kpi.items():
        a = k["angeschrieben_gesamt"]
        k["wer"] = wer
        k["antwortquote"] = round(100 * k["antwort_gesamt"] / a) if a else None
        k["erfolgsquote"] = round(100 * k["erfolg_gesamt"] / a) if a else None
        k["offen"] = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE bearbeiter=? AND status='angeschrieben'",
                             (wer,)) or 0
        liste.append(k)
    return sorted(liste, key=lambda k: (-k["angeschrieben_woche"], -k["angeschrieben_gesamt"], k["wer"]))


def export_angebote(kunde_id=None, status=None, seit=None, standort=db.STANDORT_STANDARD):
    """Flache Liste für CRM/Export: ein Satz je Angebot mit Kunde, Profil, Link und Stand."""
    sql = ("SELECT a.id, k.id AS kunde_id, k.name AS kunde, k.kundennummer, p.id AS profil_id,"
           " p.art, p.titel AS profil, a.quelle, a.extern_id, a.titel, a.anbieter, a.ort,"
           " a.entfernung_km, a.url, a.zusatz, a.veroeffentlicht, a.gefunden_am, a.status,"
           " a.bearbeiter, a.notiz, a.status_am, a.score, a.match, a.abgleich"
           "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
           " WHERE p.standort=?")
    args = [standort]
    if kunde_id:
        sql += " AND k.id=?"; args.append(kunde_id)
    if status:
        sql += " AND a.status=?"; args.append(status)
    if seit:
        sql += " AND (a.gefunden_am>=? OR a.status_am>=?)"; args += [seit, seit]
    zeilen = db.hole(sql + " ORDER BY k.name, p.art, a.status, a.gefunden_am DESC", args)
    for z in zeilen:
        z["zusatz"] = zusatz(z)
        z["abgleich"] = abgleich_von(z)
    return zeilen


def laeufe(pid=None, limit=20):
    if pid:
        return db.hole("SELECT * FROM tf_lauf WHERE profil_id=? ORDER BY id DESC LIMIT ?",
                       (pid, limit))
    return db.hole("SELECT l.*, p.titel, k.name AS kunde FROM tf_lauf l"
                   " LEFT JOIN tf_profil p ON p.id=l.profil_id"
                   " LEFT JOIN kunde k ON k.id=p.kunde_id ORDER BY l.id DESC LIMIT ?", (limit,))


def zusatz(a):
    try:
        return json.loads(a.get("zusatz") or "{}")
    except (TypeError, ValueError):
        return {}


def _begriffe(p):
    return [b.strip() for b in (p.get("suchbegriffe") or "").split(",") if b.strip()]


def _passt(text, begriff):
    """Grobe Stichwortprüfung für Quellen, deren Suche unscharf ist."""
    woerter = [w for w in re.split(r"[\s/,-]+", begriff.casefold()) if len(w) > 3]
    t = (text or "").casefold()
    return any(w[:6] in t for w in woerter)


# ------------------------------------------------------------ Jobs: BA

BA_BASIS = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
BA_KEY = "jobboerse-jobsuche"      # öffentlicher Schlüssel der BA-App, kein Geheimnis
BA_STELLE = "https://www.arbeitsagentur.de/jobsuche/jobdetail/"


def ba_parameter(p, tage=14):
    """Die Regler des Profils als Parameter der BA-Jobsuche.

    Was die Schnittstelle selbst filtern kann, soll sie filtern – das spart Abrufe und
    liefert mehr Passendes je Seite. Alles Weitere prüft `job_filter` hinterher, und zwar
    für jede Quelle gleich."""
    k = kriterien(p)
    params = {"wo": p.get("ort") or "Köln", "umkreis": p.get("umkreis_km") or 25,
              "size": 50, "veroeffentlichtseit": _zahl(k.get("tage")) or tage,
              "angebotsart": k.get("angebotsart") or 1}
    if k.get("befristung"):
        params["befristung"] = k["befristung"]
    if k.get("behinderung"):
        params["behinderung"] = "true"
    zeiten = k.get("arbeitszeiten") or ([p["arbeitszeit"]] if p.get("arbeitszeit") else [])
    if zeiten:
        params["arbeitszeit"] = ";".join(zeiten)      # Komma liefert null Treffer
    if not p.get("zeitarbeit"):
        params["zeitarbeit"] = "false"
    return params


def jobs_ba(p, tage=14, max_seiten=3):
    gesehen, treffer = set(), []
    for begriff in _begriffe(p):
        for seite in range(1, max_seiten + 1):
            params = dict(ba_parameter(p, tage), was=begriff, page=seite)
            _, body = _get(BA_BASIS + "?" + urllib.parse.urlencode(params),
                           {"X-API-Key": BA_KEY, "Accept": "application/json"})
            liste = json.loads(body).get("ergebnisliste") or []
            for s in liste:
                ref = s.get("referenznummer") or s.get("hashId")
                if not ref or ref in gesehen:
                    continue
                gesehen.add(ref)
                lok = (s.get("stellenlokationen") or [{}])[0].get("adresse") or {}
                zeiten = [n for f, n in (("arbeitszeitVollzeit", "Vollzeit"),
                                         ("arbeitszeitTeilzeit", "Teilzeit"),
                                         ("arbeitszeitHomeoffice", "Homeoffice"),
                                         ("arbeitszeitSchichtNachtWochenende", "Schicht"))
                          if s.get(f)]
                if s.get("istGeringfuegigeBeschaeftigung"):
                    zeiten.append("Minijob")
                treffer.append({
                    "quelle": "jobs.ba", "extern_id": ref,
                    "titel": s.get("stellenangebotsTitel") or s.get("hauptberuf"),
                    "anbieter": s.get("firma"),
                    "ort": " ".join(x for x in (lok.get("plz"), lok.get("ort")) if x) or None,
                    "entfernung_km": s.get("entfernung"),
                    "url": BA_STELLE + urllib.parse.quote(ref, safe=""),
                    "veroeffentlicht": s.get("datumErsteVeroeffentlichung"),
                    "zusatz": {"beruf": s.get("hauptberuf"), "arbeitszeit": ", ".join(zeiten),
                               "vertrag": (s.get("vertragsdauer") or "").lower() or None,
                               "quereinstieg": bool(s.get("quereinstiegGeeignet")),
                               "arbeitszeit_codes": [c for c, f in
                                                     (("vz", "arbeitszeitVollzeit"),
                                                      ("tz", "arbeitszeitTeilzeit"),
                                                      ("ho", "arbeitszeitHomeoffice"),
                                                      ("snw", "arbeitszeitSchichtNachtWochenende"))
                                                     if s.get(f)]
                               + (["mj"] if s.get("istGeringfuegigeBeschaeftigung") else []),
                               "suchbegriff": begriff}})
            if len(liste) < 50:
                break
            time.sleep(PAUSE)
    return treffer


# ------------------------------------------------------- Jobs: StepStone

def _slug(s):
    return re.sub(r"[^a-z0-9äöüß]+", "-", s.casefold()).strip("-")


def jobs_stepstone(p, max_seiten=2):
    gesehen, treffer = set(), []
    dec = json.JSONDecoder()
    for begriff in _begriffe(p):
        for seite in range(1, max_seiten + 1):
            url = (f"https://www.stepstone.de/jobs/{urllib.parse.quote(_slug(begriff))}"
                   f"/in-{urllib.parse.quote(_slug(p.get('ort') or 'Köln'))}"
                   f"?radius={p.get('umkreis_km') or 25}&page={seite}")
            _, body = _get(url)
            i = body.find('"items":[')
            if i < 0:
                break
            try:
                items, _ = dec.raw_decode(body[i + 8:])
            except ValueError:
                break
            for s in items:
                sid = str(s.get("id") or "")
                if not sid or sid in gesehen or not s.get("title"):
                    continue
                gesehen.add(sid)
                link = s.get("url") or ""
                treffer.append({
                    "quelle": "jobs.stepstone", "extern_id": sid,
                    "titel": s.get("title"), "anbieter": s.get("companyName"),
                    "ort": s.get("location"), "entfernung_km": None,
                    "url": link if link.startswith("http") else "https://www.stepstone.de" + link,
                    "veroeffentlicht": (s.get("datePosted") or "")[:10] or None,
                    "zusatz": {"suchbegriff": begriff,
                               "gehalt": (s.get("salary") or {}).get("displayValue")
                               if isinstance(s.get("salary"), dict) else None}})
            if len(items) < 20:
                break
            time.sleep(PAUSE)
    return treffer


# --------------------------------------------------------- Jobs: Indeed

def jobs_indeed(p, max_seiten=2):
    """Indeed-Ergebnisliste: die Seite trägt ihre Treffer als JSON ("results":[...])."""
    gesehen, treffer = set(), []
    dec = json.JSONDecoder()
    for begriff in _begriffe(p):
        for seite in range(max_seiten):
            url = "https://de.indeed.com/jobs?" + urllib.parse.urlencode(
                {"q": begriff, "l": p.get("ort") or "Köln", "radius": p.get("umkreis_km") or 25,
                 "fromage": 14, "start": seite * 10})
            body = None
            for warte in (0, 5, 12):          # Indeeds Bot-Schutz ist launisch: bis zu dreimal
                try:
                    if warte:
                        time.sleep(warte)
                        _OPENER.handlers[0].cookiejar.clear() if hasattr(_OPENER.handlers[0], "cookiejar") else None
                    _, body = _get(url)
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 403:
                        raise
                    letzter = e
            if body is None:
                raise RuntimeError("Indeed blockt gerade (403) – beim nächsten Lauf wieder versuchen")
            i = body.find('"results":[')
            if i < 0:
                break
            try:
                items, _ = dec.raw_decode(body[i + 10:])
            except ValueError:
                break
            for s in items:
                jk = s.get("jobkey")
                if not jk or jk in gesehen or not s.get("title"):
                    continue
                gesehen.add(jk)
                gehalt = (s.get("salarySnippet") or {}).get("text") if isinstance(s.get("salarySnippet"), dict) else None
                pub = s.get("pubDate")
                snippet = s.get("snippet") if isinstance(s.get("snippet"), str) else ""
                treffer.append({
                    "quelle": "jobs.indeed", "extern_id": jk,
                    "titel": s.get("title"), "anbieter": s.get("company"),
                    "ort": s.get("formattedLocation") or s.get("jobLocationCity"),
                    "entfernung_km": None,
                    "beschreibung": html.unescape(re.sub(r"<[^>]+>", " ", snippet)).strip() or None,
                    "url": "https://de.indeed.com/viewjob?jk=" + jk,
                    "veroeffentlicht": datetime.date.fromtimestamp(pub / 1000).isoformat()
                    if isinstance(pub, (int, float)) and pub > 0 else None,
                    "zusatz": {"suchbegriff": begriff, "gehalt": gehalt,
                               "arbeitszeit": ", ".join(s.get("jobTypes") or [])}})
            if len(items) < 10:
                break
            time.sleep(PAUSE)
    return treffer


# --------------------------------------------------- Jobs: meinestadt.de

MS_BEI = re.compile(r"^Job als .*? bei (.+?) in (.+)$")


def jobs_meinestadt(p, max_seiten=2):
    """jobs.meinestadt.de liefert die Treffer als schema.org-OfferCatalog im Seitenkopf."""
    gesehen, treffer = set(), []
    stadt = _slug(p.get("ort") or "Köln").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    for begriff in _begriffe(p):
        for seite in range(1, max_seiten + 1):
            url = (f"https://jobs.meinestadt.de/{stadt}/suche?"
                   + urllib.parse.urlencode({"words": begriff, "radius": p.get("umkreis_km") or 25, "page": seite}))
            try:
                _, body = _get(url)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise RuntimeError(f"meinestadt kennt den Ort '{p.get('ort')}' nicht (404)")
                raise
            items = []
            for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', body, re.S):
                try:
                    d = json.loads(m.group(1))
                except ValueError:
                    continue
                if d.get("@type") == "OfferCatalog":
                    items = d.get("itemListElement") or []
                    break
            for s in items:
                url_s = s.get("url") or ""
                sid = re.search(r"id=(\d+)", url_s)
                sid = sid.group(1) if sid else url_s
                if not sid or sid in gesehen:
                    continue
                gesehen.add(sid)
                # meinestadt kodiert doppelt (&amp;uuml;) - zweimal entschärfen
                besch = html.unescape(html.unescape(s.get("description") or ""))
                firma, ort = (None, None)
                m2 = MS_BEI.match(besch)
                if m2:
                    firma, ort = m2.group(1), m2.group(2)
                treffer.append({"quelle": "jobs.meinestadt", "extern_id": sid,
                                "titel": html.unescape(html.unescape(s.get("name") or "")), "anbieter": firma,
                                "ort": ort, "entfernung_km": None, "url": url_s,
                                "veroeffentlicht": None, "zusatz": {"suchbegriff": begriff}})
            if len(items) < 20:
                break
            time.sleep(PAUSE)
    return treffer


# ---------------------------------------------------- Kleinanzeigen (beide)

KA_ARTIKEL = re.compile(r'<article[^>]*data-adid="(\d+)"[^>]*data-href="([^"]+)"[^>]*>(.*?)</article>', re.S)
KA_PLZ = re.compile(r"^\d{5}\s+\S")


def _ka_liste(params, max_seiten=2):
    """Kleinanzeigen-Suche über das Suchformular; Kleinanzeigen leitet auf die Rubrik-URL um."""
    ergebnisse, url = [], "https://www.kleinanzeigen.de/s-suchanfrage.html?" + urllib.parse.urlencode(params)
    for seite in range(1, max_seiten + 1):
        final, body = _get(url)
        for adid, href, inner in KA_ARTIKEL.findall(body):
            ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', inner, re.S)
            titel = None
            if ld:
                try:
                    titel = json.loads(ld.group(1)).get("title")
                except ValueError:
                    pass
            text = html.unescape(re.sub(r"\s+", " ", re.sub(
                r"<[^>]+>", " | ", re.sub(r"<script.*?</script>", "", inner, flags=re.S))))
            teile = [t.strip() for t in text.split("|") if t.strip()]
            ort = next((t for t in teile if KA_PLZ.match(t)), None)
            preis = next((t for t in teile if "€" in t and len(t) < 20), None)
            masse = next((t for t in teile if "m²" in t or "Zi." in t), None)
            anbieter = teile[-1] if teile and len(teile[-1]) < 60 and "€" not in teile[-1] else None
            titel = titel or next((t for t in teile if 15 <= len(t) <= 120 and not KA_PLZ.match(t)), f"Anzeige {adid}")
            ergebnisse.append({"adid": adid, "url": "https://www.kleinanzeigen.de" + href,
                               "titel": titel, "ort": ort, "preis": preis, "masse": masse,
                               "anbieter": anbieter, "beschreibung": " ".join(teile)[:300]})
        # nächste Seite: /s-rubrik/ort/seite:2/...
        if "/seite:" in final or len(KA_ARTIKEL.findall(body)) < 20:
            break
        url = re.sub(r"(kleinanzeigen\.de/s-[^/]+/)", r"\1seite:%d/" % (seite + 1), final, count=1)
        time.sleep(PAUSE)
    return ergebnisse


def jobs_kleinanzeigen(p):
    gesehen, treffer = set(), []
    for begriff in _begriffe(p):
        for a in _ka_liste({"keywords": begriff, "locationStr": p.get("ort") or "Köln",
                            "radius": p.get("umkreis_km") or 25, "categoryId": 102}):
            if a["adid"] in gesehen:
                continue
            gesehen.add(a["adid"])
            treffer.append({"quelle": "jobs.kleinanzeigen", "extern_id": a["adid"],
                            "titel": a["titel"], "anbieter": a["anbieter"], "ort": a["ort"],
                            "entfernung_km": None, "url": a["url"], "veroeffentlicht": None,
                            "beschreibung": a["beschreibung"],
                            "zusatz": {"suchbegriff": begriff, "gehalt": a["preis"]}})
    return treffer


GESUCH = re.compile(r"\b(such(e|t|en)|gesucht|wir suchen|ich suche)\b", re.I)


def wohnung_kleinanzeigen(p):
    k = kriterien(p)
    # WBS-Wohnungen nennen das Wort fast immer in der Anzeige – als Suchwort trifft es genau die.
    params = {"keywords": "WBS" if k.get("wbs") else "", "locationStr": p.get("ort") or "Köln",
              "radius": p.get("umkreis_km") or 10, "categoryId": 203}
    if p.get("max_miete"):
        params["maxPrice"] = int(p["max_miete"])
    elif k.get("max_warmmiete"):
        params["maxPrice"] = int(k["max_warmmiete"])
    treffer = []
    for a in _ka_liste(params, max_seiten=3):
        if GESUCH.search(a["titel"] or ""):
            continue                       # Mieter, die selbst suchen – nicht anschreiben
        zimmer = re.search(r"(\d+(?:[.,]\d)?)\s*Zi", a["masse"] or "")
        flaeche = re.search(r"(\d+)\s*m²", a["masse"] or "")
        zi = float(zimmer.group(1).replace(",", ".")) if zimmer else None
        qm = int(flaeche.group(1)) if flaeche else None
        if p.get("min_zimmer") and zi is not None and zi < p["min_zimmer"]:
            continue
        if k.get("max_zimmer") and zi is not None and zi > k["max_zimmer"]:
            continue
        if p.get("min_flaeche") and qm is not None and qm < p["min_flaeche"]:
            continue
        if k.get("max_flaeche") and qm is not None and qm > k["max_flaeche"]:
            continue
        treffer.append({"quelle": "wohnung.kleinanzeigen", "extern_id": a["adid"],
                        "titel": a["titel"], "anbieter": a["anbieter"] or "Kleinanzeigen",
                        "ort": a["ort"], "entfernung_km": None, "url": a["url"],
                        "veroeffentlicht": None, "beschreibung": a["beschreibung"],
                        "zusatz": {"preis": a["preis"], "zimmer": zimmer.group(1) if zimmer else None,
                                   "flaeche": flaeche.group(1) + " m²" if flaeche else None}})
    return treffer


# ------------------------------------------------------ Jobs: Arbeitnow

def jobs_arbeitnow(p):
    """Kostenlose API, Suche ist unscharf – deshalb lokal nach Begriff und Ort filtern."""
    ort = (p.get("ort") or "Köln").casefold()
    gesehen, treffer = set(), []
    for begriff in _begriffe(p):
        url = "https://www.arbeitnow.com/api/job-board-api?" + urllib.parse.urlencode(
            {"search": begriff, "location": p.get("ort") or "Köln"})
        _, body = _get(url)
        for s in json.loads(body).get("data") or []:
            slug = s.get("slug") or s.get("url")
            if not slug or slug in gesehen:
                continue
            if not _passt(s.get("title"), begriff) or ort[:5] not in (s.get("location") or "").casefold():
                continue
            gesehen.add(slug)
            treffer.append({"quelle": "jobs.arbeitnow", "extern_id": slug,
                            "titel": s.get("title"), "anbieter": s.get("company_name"),
                            "ort": s.get("location"), "entfernung_km": None, "url": s.get("url"),
                            "veroeffentlicht": datetime.date.fromtimestamp(s["created_at"]).isoformat()
                            if s.get("created_at") else None,
                            "zusatz": {"suchbegriff": begriff,
                                       "arbeitszeit": ", ".join(s.get("job_types") or [])}})
    return treffer


# --------------------------------------------- Jobs: Adzuna / Jooble (Key)

def jobs_adzuna(p):
    app_id, key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and key):
        raise RuntimeError("Adzuna: ADZUNA_APP_ID und ADZUNA_APP_KEY fehlen in der .env")
    gesehen, treffer = set(), []
    for begriff in _begriffe(p):
        url = "https://api.adzuna.com/v1/api/jobs/de/search/1?" + urllib.parse.urlencode(
            {"app_id": app_id, "app_key": key, "what": begriff, "where": p.get("ort") or "Köln",
             "distance": p.get("umkreis_km") or 25, "results_per_page": 50, "max_days_old": 14})
        _, body = _get(url, {"Accept": "application/json"})
        for s in json.loads(body).get("results") or []:
            sid = str(s.get("id") or "")
            if not sid or sid in gesehen:
                continue
            gesehen.add(sid)
            treffer.append({"quelle": "jobs.adzuna", "extern_id": sid, "titel": s.get("title"),
                            "anbieter": (s.get("company") or {}).get("display_name"),
                            "ort": (s.get("location") or {}).get("display_name"),
                            "entfernung_km": None, "url": s.get("redirect_url"),
                            "veroeffentlicht": (s.get("created") or "")[:10] or None,
                            "zusatz": {"suchbegriff": begriff, "vertrag": s.get("contract_time")}})
    return treffer


def jobs_jooble(p):
    key = os.environ.get("JOOBLE_KEY")
    if not key:
        raise RuntimeError("Jooble: JOOBLE_KEY fehlt in der .env")
    gesehen, treffer = set(), []
    for begriff in _begriffe(p):
        d = _post_json("https://de.jooble.org/api/" + key,
                       {"keywords": begriff, "location": p.get("ort") or "Köln",
                        "radius": p.get("umkreis_km") or 25, "page": 1})
        for s in d.get("jobs") or []:
            sid = str(s.get("id") or s.get("link") or "")
            if not sid or sid in gesehen:
                continue
            gesehen.add(sid)
            treffer.append({"quelle": "jobs.jooble", "extern_id": sid, "titel": s.get("title"),
                            "anbieter": s.get("company"), "ort": s.get("location"),
                            "entfernung_km": None, "url": s.get("link"),
                            "veroeffentlicht": (s.get("updated") or "")[:10] or None,
                            "zusatz": {"suchbegriff": begriff, "gehalt": s.get("salary") or None,
                                       "portal": s.get("source")}})
    return treffer


# -------------------------------------------------------- Wohnung: IS24-Mail

# ImmoScout24 verschickt seine Treffer nicht mit der Adresse der Webseite, sondern
# ueber eine Weiterleitung:  push.search.is24.de/email/expose/<nr>?...&savedSearchId=<nr>
# Beide Formen werden erkannt. Die savedSearchId ist der verlaessliche Schluessel:
# im Betreff steht kein Name des Suchauftrags, sondern nur dessen Kriterien.
EXPOSE = re.compile(r"https?://(?:www\.)?immobilienscout24\.de/expose/(\d+)[^\s\"'<>]*")
EXPOSE_PUSH = re.compile(r"https?://push\.search\.is24\.de/email/expose/(\d+)([^\s\"'<>]*)")
SUCHAUFTRAG_NR = re.compile(r"savedSearchId=(\d+)")
ANGEBOTSMAIL = re.compile(r"^\s*\d+\s+Angebote?\b", re.I)
PREIS = re.compile(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*€")
ZIMMER = re.compile(r"(\d+(?:[.,]\d)?)\s*Zi")
FLAECHE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²")


def imap_konfiguriert():
    return all(os.environ.get(k) for k in ("TF_IMAP_HOST", "TF_IMAP_USER", "TF_IMAP_PASSWORT"))


def _mail_teil(msg, art="text/plain"):
    """Einen Teil der Mail als Text. Die Treffermails von ImmoScout24 haben eine
    sauber aufgebaute Nur-Text-Fassung – die ist tausendmal verlässlicher als das HTML."""
    for part in msg.walk():
        if part.get_content_type() == art:
            try:
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                continue
    return ""


def _mail_text(msg):
    teile = []
    for part in msg.walk():
        if part.get_content_type() in ("text/html", "text/plain"):
            try:
                teile.append(part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"))
            except Exception:
                continue
    return "\n".join(teile)


def _betreff(msg):
    roh = msg.get("Subject") or ""
    return "".join(t.decode(enc or "utf-8", errors="replace") if isinstance(t, bytes) else t
                   for t, enc in email.header.decode_header(roh))


# So sieht ein Treffer in der Textfassung aus:
#
#   Titel: Sanierte 1 Zimmer Wohnung
#
#   Link: https://push.search.is24.de/email/expose/170803054?...&savedSearchId=140961757&...
#   Adresse: Laudahnstr. 2, Lindenthal (Ortsteil), Köln
#   Kaltmiete: 465 €
#   Wohnfläche: 31 m²
#   Zimmer: 1
#   Balkon/Terrasse, Einbauküche, Keller
#
# Die letzte Zeile sind die Ausstattungsmerkmale – genau das, was der Abgleich
# gegen WBS, Balkon, Einbauküche und Keller braucht.
BLOCK = re.compile(
    r"Titel:\s*(?P<titel>[^\r\n]+)\s*\r?\n\s*\r?\n"
    r"Link:\s*(?P<url>https?://[^\s]+expose/(?P<id>\d+)[^\s]*)"
    r"(?P<rest>(?:\r?\n(?!\s*Titel:)[^\r\n]*)*)", re.I)
FELD = {"adresse": re.compile(r"^Adresse:\s*(.+)$", re.I | re.M),
        "preis": re.compile(r"^Kaltmiete:\s*(.+)$", re.I | re.M),
        "warmmiete": re.compile(r"^Warmmiete:\s*(.+)$", re.I | re.M),
        "flaeche": re.compile(r"^Wohnfläche:\s*(.+)$", re.I | re.M),
        "zimmer": re.compile(r"^Zimmer:\s*(.+)$", re.I | re.M)}
BEKANNTE_FELDER = re.compile(r"^(Adresse|Kaltmiete|Warmmiete|Wohnfläche|Zimmer|Link):", re.I)


def _exposes_aus_text(text):
    """Treffer aus der Nur-Text-Fassung einer Suchauftragsmail."""
    gefunden = []
    for m in BLOCK.finditer(text):
        rest = m.group("rest") or ""
        zusatz = {}
        for name, muster in FELD.items():
            treffer = muster.search(rest)
            if treffer:
                zusatz[name] = treffer.group(1).strip()
        # Was übrig bleibt und kein bekanntes Feld ist, sind die Ausstattungsmerkmale.
        # Beim letzten Treffer folgt darauf der Mailfuß – am Trennstrich wird Schluss gemacht,
        # sonst stehen "Alle Angebote ansehen" und eine URL in der Ausstattung.
        merkmale = []
        for zeile in rest.splitlines():
            zeile = zeile.strip()
            if not zeile:
                continue
            if BEKANNTE_FELDER.match(zeile):
                continue
            if zeile.startswith("http") or set(zeile) <= set("-–—_=") or len(zeile) > 90:
                break
            merkmale.append(zeile)
        if merkmale:
            zusatz["merkmale"] = ", ".join(merkmale)[:200]
        nr = SUCHAUFTRAG_NR.search(m.group("url"))
        if nr:
            zusatz["suchauftrag_nr"] = nr.group(1)
        ort = zusatz.pop("adresse", None)
        gefunden.append({
            "quelle": "wohnung.mail", "extern_id": m.group("id"),
            "url": f"https://www.immobilienscout24.de/expose/{m.group('id')}",
            "titel": m.group("titel").strip(), "anbieter": "ImmoScout24",
            "ort": ort, "entfernung_km": None, "veroeffentlicht": None,
            "beschreibung": " · ".join(x for x in (ort, zusatz.get("merkmale")) if x) or None,
            "zusatz": zusatz})
    return gefunden


def _exposes_aus_html(text):
    """Alle Exposés einer Mail, mit Titel, Preis, Zimmern, Fläche und Suchauftrags-Nummer.
    Rückfallweg, wenn die Mail keine brauchbare Textfassung hat."""
    gefunden = {}
    treffer = [(m.group(1), m.start(), m.end(), SUCHAUFTRAG_NR.search(m.group(2) or ""))
               for m in EXPOSE_PUSH.finditer(text)]
    treffer += [(m.group(1), m.start(), m.end(), None) for m in EXPOSE.finditer(text)]
    for eid, start, ende, nr in treffer:
        umfeld = html.unescape(re.sub(r"<[^>]+>", " ", text[max(0, start - 1500):ende + 1500]))
        umfeld = re.sub(r"\s+", " ", umfeld)
        preis, zimmer, flaeche = PREIS.search(umfeld), ZIMMER.search(umfeld), FLAECHE.search(umfeld)
        # Der Titel steht in der Textfassung direkt vor dem Wort "Link:".
        vorlauf = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text[max(0, start - 400):start])))
        titel = ""
        m = re.search(r"([^|•·]{12,90}?)\s*Link:\s*$", vorlauf)
        if m:
            titel = m.group(1).strip()
        if not titel:
            kandidaten = [t.strip() for t in re.split(r"[|•·]|  +", umfeld)
                          if 12 <= len(t.strip()) <= 90 and not re.search(r"\d{3,}", t)]
            titel = max(kandidaten, key=len) if kandidaten else f"ImmoScout24 Exposé {eid}"
        satz = gefunden.setdefault(eid, {
            "quelle": "wohnung.mail", "extern_id": eid,
            "url": f"https://www.immobilienscout24.de/expose/{eid}",
            "titel": titel, "anbieter": "ImmoScout24", "ort": None, "entfernung_km": None,
            "veroeffentlicht": None,
            "zusatz": {"preis": preis.group(1) + " €" if preis else None,
                       "zimmer": zimmer.group(1) if zimmer else None,
                       "flaeche": flaeche.group(1) + " m²" if flaeche else None}})
        if nr:
            satz["zusatz"]["suchauftrag_nr"] = nr.group(1)
    return list(gefunden.values())


def _mail_passt(p, betreff, exposes):
    """Gehört diese Mail zu diesem Wohnprofil?

    ImmoScout24 schreibt keinen Namen des Suchauftrags in den Betreff, sondern dessen
    Kriterien ("1 Angebot: Mietwohnung, in Köln, 2 - 3 Zimmer"). Deshalb drei Wege,
    vom sichersten zum weichesten:

      1. Im Profilfeld „Suchauftrag" steht die Nummer des Suchauftrags (savedSearchId).
         Die steht in jedem Exposé-Link und ist eindeutig.
      2. Im Profilfeld steht ein Text, der im Betreff vorkommt.
      3. Das Feld ist leer: dann zählt der Ort des Profils im Betreff.
    """
    wunsch = (p.get("suchauftrag") or "").strip()
    if wunsch.isdigit():
        return any((e.get("zusatz") or {}).get("suchauftrag_nr") == wunsch for e in exposes)
    if wunsch:
        return wunsch.casefold() in betreff.casefold()
    ort = (p.get("ort") or "").strip()
    return bool(ort) and ort.casefold()[:5] in betreff.casefold()


def wohnung_mail(p, tage=14):
    if not imap_konfiguriert():
        raise RuntimeError("ImmoScout24-Postfach nicht verbunden: TF_IMAP_HOST, TF_IMAP_USER,"
                           " TF_IMAP_PASSWORT in der .env fehlen.")
    name = (p.get("suchauftrag") or "").strip()
    if not name and not (p.get("ort") or "").strip():
        raise ValueError("Weder Suchauftrag noch Ort im Profil – ohne das eine oder das andere"
                         " lassen sich die Mails keinem Kunden zuordnen.")
    seit = (datetime.date.today() - datetime.timedelta(days=tage)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL(os.environ["TF_IMAP_HOST"])
    try:
        box.login(os.environ["TF_IMAP_USER"], os.environ["TF_IMAP_PASSWORT"])
        box.select(os.environ.get("TF_IMAP_ORDNER") or "INBOX", readonly=True)
        typ, ids = box.search(None, f'(SINCE {seit} FROM "immobilienscout24")')
        treffer = {}
        for mid in (ids[0].split() if typ == "OK" else []):
            typ, kopf = box.fetch(mid, "(RFC822.HEADER)")
            if typ != "OK":
                continue
            betreff = _betreff(email.message_from_bytes(kopf[0][1]))
            # Nur die Treffermails des Suchauftrags. Der Rest des Postfachs sind
            # Anfragebestätigungen und Antworten von Anbietern – die gehören nicht
            # in die Liste neuer Wohnungen.
            if not ANGEBOTSMAIL.match(betreff):
                continue
            typ, daten = box.fetch(mid, "(RFC822)")
            if typ != "OK":
                continue
            msg = email.message_from_bytes(daten[0][1])
            exposes = _exposes_aus_text(_mail_teil(msg)) or _exposes_aus_html(_mail_text(msg))
            if not _mail_passt(p, betreff, exposes):
                continue
            for e in exposes:
                e["veroeffentlicht"] = (msg.get("Date") or "")[:31]
                e["zusatz"]["suchauftrag"] = betreff[:120]
                treffer.setdefault(e["extern_id"], e)
        return list(treffer.values())
    finally:
        try:
            box.logout()
        except Exception:
            pass


def wohnung_link_eintragen(pid, url, titel=None, preis=None, notiz=None):
    m = EXPOSE.search(url or "")
    eid = m.group(1) if m else (url or "").strip()
    if not eid:
        raise ValueError("Kein Link.")
    return _ablegen(pid, [{
        "quelle": "wohnung.link", "extern_id": eid, "url": url.strip(),
        "titel": (titel or "").strip() or f"Exposé {eid}", "anbieter": "ImmoScout24",
        "ort": None, "entfernung_km": None, "veroeffentlicht": None,
        "zusatz": {"preis": preis or None, "notiz": notiz or None}}])


# ---------------------------------------------------------- Quellenregister

QUELLEN = {
    "jobs.ba":              ("Bundesagentur Jobsuche", "job", jobs_ba, lambda: True),
    "jobs.indeed":          ("Indeed", "job", jobs_indeed, lambda: True),
    "jobs.stepstone":       ("StepStone", "job", jobs_stepstone, lambda: True),
    "jobs.meinestadt":      ("jobs.meinestadt.de", "job", jobs_meinestadt, lambda: True),
    "jobs.kleinanzeigen":   ("Kleinanzeigen Jobs", "job", jobs_kleinanzeigen, lambda: True),
    "jobs.arbeitnow":       ("Arbeitnow", "job", jobs_arbeitnow, lambda: True),
    "jobs.adzuna":          ("Adzuna (viele Portale)", "job", jobs_adzuna,
                             lambda: bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))),
    "jobs.jooble":          ("Jooble (Meta-Suche)", "job", jobs_jooble,
                             lambda: bool(os.environ.get("JOOBLE_KEY"))),
    "wohnung.mail":         ("ImmoScout24 Suchaufträge (Mail)", "wohnung", wohnung_mail, imap_konfiguriert),
    "wohnung.kleinanzeigen": ("Kleinanzeigen Mietwohnungen", "wohnung", wohnung_kleinanzeigen, lambda: True),
}


def quellen_stand():
    return [{"schluessel": s, "name": n, "art": art, "bereit": bereit()}
            for s, (n, art, _, bereit) in QUELLEN.items()]


def quellen_fuer(p):
    gewaehlt = [q.strip() for q in (p.get("quellen") or "").split(",") if q.strip()]
    return [s for s, (_, art, _, _) in QUELLEN.items()
            if art == p["art"] and (not gewaehlt or s in gewaehlt)]


# ---------------------------------------------------------------- Läufe

RAUSCHEN = re.compile(r"\((m/w/d|w/m/d|m/w/x|d/m/w|gn\*?|all genders)\)|m/w/d|w/m/d|\bin\b|"
                      r"\b(gesucht|ab sofort|vollzeit|teilzeit|minijob|dringend)\b|[^\wäöüß ]", re.I)


def _schluessel(t):
    """Grober Vergleichsschlüssel: Titel ohne Rauschen + erstes Wort des Anbieters.
    Dieselbe Stelle heißt bei StepStone, Indeed und der BA fast nie exakt gleich –
    aber so gleich, dass die Taskforce sie nicht dreimal anschreiben soll."""
    titel = re.sub(r"\s+", " ", RAUSCHEN.sub(" ", (t.get("titel") or "").casefold())).strip()
    anbieter = re.sub(r"[^\wäöüß]", "", (t.get("anbieter") or "").casefold().split(" ")[0])
    return (titel[:40], anbieter[:12])


def _tage_alt(iso):
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(str(iso)[:10])).days
    except (TypeError, ValueError):
        return None


def _score(p, t):
    """Relevanz 0-10: Begriff im Titel, Nähe, Frische, Rahmen des Profils."""
    s = 0.0
    z = t.get("zusatz") or {}
    titel = (t.get("titel") or "").casefold()
    if p["art"] == "job":
        begriff = (z.get("suchbegriff") or "").casefold()
        woerter = [w for w in re.split(r"[\s/,-]+", begriff) if len(w) > 3]
        if woerter and any(w[:6] in titel for w in woerter):
            s += 4                                   # Beruf steht im Titel, nicht nur im Text
        if (p.get("arbeitszeit") or "") and ARBEITSZEIT.get(p["arbeitszeit"], "").casefold() in (z.get("arbeitszeit") or "").casefold():
            s += 1
        if z.get("quereinstieg"):
            s += 1                                   # unsere Kunden steigen meist quer ein
        if z.get("vertrag") == "unbefristet":
            s += 0.5
    else:
        preis = re.sub(r"[^\d]", "", (z.get("preis") or "").split(",")[0])
        if p.get("max_miete") and preis:
            s += 4 if int(preis) <= p["max_miete"] else -3
        elif preis:
            s += 1
        if z.get("zimmer") and p.get("min_zimmer"):
            try:
                s += 1 if float(str(z["zimmer"]).replace(",", ".")) >= p["min_zimmer"] else -2
            except ValueError:
                pass
        if z.get("flaeche"):
            s += 0.5
        k = kriterien(p)
        if k.get("wbs") and re.search(r"\bwbs\b|wohnberechtigung", (t.get("titel") or "") + " " + (t.get("beschreibung") or ""), re.I):
            s += 2
    km = t.get("entfernung_km")
    if isinstance(km, (int, float)):
        s += 2 if km <= 10 else (1 if km <= 25 else 0)
    else:
        ort = (p.get("ort") or "").casefold()[:5]
        if ort and ort in (t.get("ort") or "").casefold():
            s += 1.5
    alt = _tage_alt(t.get("veroeffentlicht"))
    if alt is not None:
        s += 2 if alt <= 3 else (1 if alt <= 10 else 0)
    else:
        s += 0.5
    return max(0.0, min(10.0, round(s, 1)))


def _ablegen(pid, treffer):
    """Nur ablegen, was neu ist – UNIQUE(profil, quelle, extern_id) ist das Gedächtnis.
    Was inhaltlich schon aus einer anderen Quelle da ist, wird als Dublette markiert."""
    p = profil(pid)
    neu = 0
    with db.offen() as con:
        bekannt = {}
        for r in con.execute("SELECT id, titel, anbieter FROM tf_angebot WHERE profil_id=?"
                             " AND status!='doppelt'", (pid,)):
            bekannt.setdefault(_schluessel({"titel": r[1], "anbieter": r[2]}), r[0])
        for t in treffer:
            schl = _schluessel(t)
            original = bekannt.get(schl)
            cur = con.execute(
                "INSERT OR IGNORE INTO tf_angebot (profil_id, quelle, extern_id, titel, anbieter,"
                " ort, entfernung_km, url, zusatz, veroeffentlicht, gefunden_am, status, score,"
                " doppelt_von, beschreibung) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, t["quelle"], t["extern_id"], t.get("titel"), t.get("anbieter"),
                 t.get("ort"), t.get("entfernung_km"), t.get("url"),
                 json.dumps(t.get("zusatz") or {}, ensure_ascii=False),
                 t.get("veroeffentlicht"), jetzt(),
                 "doppelt" if original else "neu", _score(p, t) if p else None,
                 original, (t.get("beschreibung") or "")[:400] or None))
            if cur.rowcount and not original:
                neu += 1
                bekannt[schl] = cur.lastrowid
    return neu


def dubletten_nachziehen(pid):
    """Bestand einmalig bereinigen: was schon ohne Dublettenprüfung drin war, nachträglich markieren."""
    p = profil(pid)
    gesehen, markiert = {}, 0
    with db.offen() as con:
        for r in con.execute("SELECT id, titel, anbieter, zusatz, ort, entfernung_km, veroeffentlicht,"
                             " status FROM tf_angebot WHERE profil_id=? ORDER BY id", (pid,)):
            t = {"titel": r[1], "anbieter": r[2], "zusatz": json.loads(r[3] or "{}"), "ort": r[4],
                 "entfernung_km": r[5], "veroeffentlicht": r[6]}
            con.execute("UPDATE tf_angebot SET score=? WHERE id=?", (_score(p, t), r[0]))
            schl = _schluessel(t)
            if schl in gesehen and r[7] == "neu":
                con.execute("UPDATE tf_angebot SET status='doppelt', doppelt_von=? WHERE id=?",
                            (gesehen[schl], r[0]))
                markiert += 1
            elif r[7] != "doppelt":
                gesehen.setdefault(schl, r[0])
    return markiert


def _woerter(text):
    return [w.strip().casefold() for w in re.split(r"[,;\n]", text or "") if w.strip()]


STUNDEN_IM_MONAT = 173          # 40 Stunden/Woche, der übliche Umrechnungswert


def _zahl_aus(wert, mindest=0.0):
    """Die erste brauchbare Zahl aus „2.400 € - 2.800 €", „48 m²", „2 Zi.".

    Die Portale schreiben ihre Angaben sehr unterschiedlich; gesucht wird der kleinste
    Wert, damit eine Spanne nicht zu günstig gerechnet wird."""
    if wert is None:
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    werte = []
    for z in re.findall(r"\d[\d.]*(?:,\d+)?", str(wert)):
        try:
            werte.append(float(z.replace(".", "").replace(",", ".")))
        except ValueError:
            pass
    werte = [w for w in werte if w >= mindest]
    return min(werte) if werte else None


def _geld(wert):
    """Ein Betrag als Monatswert. Stundenlöhne werden hochgerechnet, sonst nicht vergleichbar.

    „Ab 15,11 € pro Stunde" ist bei den Stellenbörsen genauso üblich wie „2.400 € - 2.800 €";
    ungerechnet fiele jeder Stundenlohn durch jeden Mindestgehalt-Filter."""
    if wert is None:
        return None
    text = str(wert)
    if re.search(r"(pro|je|/)\s*(std|stunde)", text, re.I):
        stunde = _zahl_aus(text, 1)
        return round(stunde * STUNDEN_IM_MONAT) if stunde else None
    if re.search(r"(pro|im|/)\s*(jahr|j\.)|p\.a\.|jährlich", text, re.I):
        jahr = _zahl_aus(text, 1000)
        return round(jahr / 12) if jahr else None
    betrag = _zahl_aus(text, 100)                 # Kleinstwerte draußen lassen
    if betrag and betrag > 200000:                # offensichtlich kein Monatsgehalt
        return None
    return betrag


def job_filter(p, treffer):
    """Die Regler anwenden, die keine Quelle selbst kann. Gibt (bleibt, aussortiert) zurück.

    Bewusst hier und nicht nur in der Abfrage: so gilt jeder Regler für jede Quelle gleich –
    StepStone kennt kein „min. Gehalt", die Bundesagentur kein „Arbeitgeber ausschließen".
    Aussortiert wird nur, was sicher nicht passt; fehlt die Angabe, bleibt das Angebot drin.
    Ein zu Unrecht ausgeblendetes Angebot sieht niemand mehr, ein zu viel gezeigtes kostet
    einen Blick."""
    k = kriterien(p)
    if p.get("art") != "job" or not k:
        return list(treffer), 0
    max_km = _zahl(k.get("max_entfernung"))
    min_gehalt = _zahl(k.get("min_gehalt"))
    muss = _woerter(k.get("muss_woerter"))
    ohne = _woerter(k.get("ohne_woerter"))
    nur_ag = _woerter(k.get("nur_arbeitgeber"))
    ohne_ag = _woerter(k.get("ohne_arbeitgeber"))
    zeiten = set(k.get("arbeitszeiten") or [])
    bleibt = []
    for t in treffer:
        z = t.get("zusatz") or {}
        text = " ".join(str(x) for x in (t.get("titel"), t.get("beschreibung"),
                                         z.get("beruf"), z.get("arbeitszeit")) if x).casefold()
        anbieter = (t.get("anbieter") or "").casefold()
        km = t.get("entfernung_km")
        if max_km is not None and km is not None and km > max_km:
            continue
        if min_gehalt is not None:
            g = _geld(z.get("gehalt"))
            if g is not None and g < min_gehalt:
                continue
        if k.get("nur_quereinstieg") and not z.get("quereinstieg"):
            continue
        if muss and not any(w in text for w in muss):
            continue
        if ohne and any(w in text or w in anbieter for w in ohne):
            continue
        if nur_ag and not any(w in anbieter for w in nur_ag):
            continue
        if ohne_ag and any(w in anbieter for w in ohne_ag):
            continue
        # Arbeitszeit: nur aussortieren, wenn die Quelle sie kennt und keine passt.
        codes = z.get("arbeitszeit_codes")
        if zeiten and codes and not (zeiten & set(codes)):
            continue
        bleibt.append(t)
    return bleibt, len(treffer) - len(bleibt)


def lauf(pid):
    """Alle Quellen des Profils abfragen. Gibt (gefunden, neu, meldungen) zurück."""
    p = profil(pid)
    if not p:
        return 0, 0, ["Profil nicht gefunden."]
    if p["art"] == "job" and not _begriffe(p):
        return 0, 0, ["Jobprofil ohne Suchbegriffe – bitte Berufe oder Stichworte eintragen."]
    gesamt, neu_gesamt, meldungen = 0, 0, []
    for schluessel in quellen_fuer(p):
        name, _, funktion, bereit = QUELLEN[schluessel]
        if not bereit():
            continue                         # nicht konfiguriert – still überspringen
        try:
            treffer = funktion(p)
            treffer, weg = job_filter(p, treffer)
            neu = _ablegen(pid, treffer)
            meldung = f"{weg} durch die Filter aussortiert" if weg else ""
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            treffer, neu, meldung = [], 0, f"{name}: nicht erreichbar ({e})"
        except Exception as e:
            treffer, neu, meldung = [], 0, f"{name}: {e}"
        if meldung:
            meldungen.append(meldung)
        gesamt += len(treffer)
        neu_gesamt += neu
        with db.offen() as con:
            con.execute("INSERT INTO tf_lauf (profil_id, zeitpunkt, quelle, gefunden, neu, meldung)"
                        " VALUES (?,?,?,?,?,?)", (pid, jetzt(), schluessel, len(treffer), neu, meldung))
    with db.offen() as con:
        con.execute("UPDATE tf_profil SET letzter_lauf=? WHERE id=?", (jetzt(), pid))
    # Die besten neuen Angebote gleich mit der vollen Beschreibung abgleichen – so steht auf der
    # Tafel nicht nur ein Titel, sondern „passt: Führerschein, Schicht · fehlt: B2".
    try:
        abgleich_profil(pid, max_n=12)
    except Exception as e:                       # der Abgleich darf den Lauf nie kippen
        meldungen.append(f"Abgleich: {e}")
    return gesamt, neu_gesamt, meldungen


def alle_laufen(standort=db.STANDORT_STANDARD, alarm=True):
    ergebnisse = []
    for p in db.hole("SELECT id, titel FROM tf_profil WHERE aktiv=1 AND standort=? ORDER BY id",
                     (standort,)):
        gefunden, neu, meldungen = lauf(p["id"])
        ergebnisse.append((p["id"], p["titel"], gefunden, neu, "; ".join(meldungen)))
    if alarm:
        alarm_senden(ergebnisse)
    return ergebnisse


def alarm_konfiguriert():
    return bool(os.environ.get("TF_CHAT_WEBHOOK"))


def alarm_senden(ergebnisse):
    """Meldet neue Angebote in den Google-Chat-Raum der Taskforce (Webhook in TF_CHAT_WEBHOOK).
    Ohne Webhook passiert nichts – die Tafel im OS zeigt es ohnehin."""
    hook = os.environ.get("TF_CHAT_WEBHOOK")
    neue = [(t, n) for _, t, _, n, _ in ergebnisse if n]
    if not hook or not neue:
        return False
    basis = os.environ.get("TF_OS_URL") or "http://localhost:8100"
    zeilen = [f"*Taskforce: {sum(n for _, n in neue)} neue Angebote*"]
    zeilen += [f"• {t}: {n} neu" for t, n in neue[:15]]
    zeilen.append(f"Zur Tafel: {basis}/taskforce")
    try:
        _post_json(hook, {"text": "\n".join(zeilen)}, timeout=15)
        return True
    except Exception:
        return False



# ------------------------------------------- Stellen-/Exposébeschreibung nachladen

def _text(html_teil):
    t = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", html_teil or "", flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t\u00a0]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def _ld_json(body):
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', body, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for e in (d if isinstance(d, list) else [d]):
            if isinstance(e, dict):
                yield e


def _ka_details(body):
    """Kleinanzeigen-Anzeige: Beschreibung, Detailliste (Schlüssel → Wert) und Merkmal-Häkchen."""
    besch = ""
    m = re.search(r'id="viewad-description-text"[^>]*>(.*?)</p>', body, re.S)
    if m:
        besch = _text(m.group(1))
    details, merkmale = {}, []
    m = re.search(r'id="viewad-details"(.*?)</section>', body, re.S)
    if m:
        for li in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S):
            teile = [x.strip() for x in html.unescape(re.sub(r"<[^>]+>", "|", li)).split("|") if x.strip()]
            if len(teile) >= 2:
                details[teile[0]] = teile[1]
            elif len(teile) == 1:
                merkmale.append(teile[0])
    return besch, details, merkmale


def beschreibung_laden(a):
    """Holt die volle Beschreibung eines Angebots von der Quelle. Gibt (text, zusatz_neu) zurück.
    Quellen, die Detailseiten blocken (Indeed, IS24), liefern den gespeicherten Kurztext."""
    q, url = a["quelle"], a.get("url") or ""
    z = zusatz(a)
    if q == "jobs.ba":
        h = base64.b64encode(a["extern_id"].encode()).decode()
        _, body = _get(f"https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{h}",
                       {"X-API-Key": BA_KEY, "Accept": "application/json"})
        d = json.loads(body)
        if d.get("eintrittszeitraum"):
            z["eintritt"] = str(d["eintrittszeitraum"])[:10]
        return d.get("stellenangebotsBeschreibung") or "", z
    if q in ("jobs.kleinanzeigen", "wohnung.kleinanzeigen"):
        _, body = _get(url)
        besch, details, merkmale = _ka_details(body)
        for schl, feld in (("Warmmiete", "warmmiete"), ("Nebenkosten", "nebenkosten"), ("Etage", "etage"),
                           ("Wohnungstyp", "wohnungstyp"), ("Verfügbar ab", "verfuegbar"),
                           ("Arbeitszeit", "arbeitszeit"), ("Art", "art"), ("Zimmer", "zimmer"),
                           ("Wohnfläche", "flaeche")):
            if details.get(schl) and not z.get(feld):
                z[feld] = details[schl]
        if merkmale:
            z["merkmale"] = ", ".join(merkmale)
        return besch, z
    if q == "jobs.stepstone":
        _, body = _get(url)
        for e in _ld_json(body):
            if e.get("@type") == "JobPosting" and e.get("description"):
                return _text(e["description"]), z
        return a.get("beschreibung") or "", z
    if q == "jobs.meinestadt":
        _, body = _get(url)
        teile = []
        for mod, inner in re.findall(r'<section[^>]*class="ms-jobDetailSection[^"]*"[^>]*data-mod="(\w+)"[^>]*>(.*?)</section>',
                                     body, re.S):
            if mod != "application":
                teile.append(_text(inner))
        return "\n".join(t for t in teile if t) or (a.get("beschreibung") or ""), z
    if q == "jobs.indeed":
        try:
            _, body = _get(url)
            m = re.search(r'"sanitizedJobDescription":"((?:[^"\\]|\\.)*)"', body)
            if m:
                return _text(json.loads('"' + m.group(1) + '"')), z
        except urllib.error.HTTPError:
            pass                                       # Indeed blockt Detailseiten – Kurztext reicht
        return a.get("beschreibung") or "", z
    if q in ("wohnung.mail", "wohnung.link"):
        return a.get("beschreibung") or "", z          # IS24-Exposés: kein Abruf (Cloudflare/AGB)
    try:                                               # Arbeitnow, Adzuna, Jooble & Co.
        _, body = _get(url)
    except Exception:
        return a.get("beschreibung") or "", z
    for e in _ld_json(body):
        if e.get("description") and e.get("@type") in ("JobPosting", "Product", "Offer", "RealEstateListing"):
            return _text(e["description"]), z
    m = re.search(r'<meta name="description" content="([^"]*)"', body)
    return (html.unescape(m.group(1)) if m else (a.get("beschreibung") or "")), z


# ------------------------------------------------- Abgleich Kunde ↔ Anforderungen

# Anforderungen, wie sie in Stellenbeschreibungen stehen, und woran man im Kundenprofil
# (Kurzprofil, Lebenslauftext, Profil-Notiz) erkennt, dass der Kunde sie erfüllt.
MERKMALE = [   # (Beschriftung, Muster in der Stelle, Muster beim Kunden)
    ("Führerschein", r"führerschein|fahrerlaubnis|\bklasse b\b", r"führerschein|fahrerlaubnis|\bklasse b\b"),
    ("LKW-Führerschein", r"\bklasse (c|ce|c1|c1e)\b|lkw-führerschein|lkw-fahrer", r"\bklasse (c|ce|c1|c1e)\b|lkw"),
    ("Staplerschein", r"stapler|flurförder", r"stapler|flurförder"),
    # Beim Kunden zählt C1/C2 nur in der Nähe von „Deutsch" – „Englisch C1" ist kein Deutsch-Nachweis.
    ("Deutsch B2 oder besser", r"deutsch\w*[^.\n]{0,40}\b(b2|c1|c2|verhandlungssicher|fließend|sehr gut)",
     r"\bb2\b|deutsch\w*(?:(?!englisch|arabisch|russisch|ukrainisch|türkisch|persisch|farsi|dari)[^.\n,;]){0,25}"
     r"\b(c1|c2|fließend|sehr gut|muttersprach)|\b(c1|c2)\b[^.\n,;]{0,12}deutsch"),
    ("Deutsch (Grundkenntnisse)", r"deutsch\w*[^.\n]{0,40}\b(a2|b1|grundkenntnisse|gute|gut)\b",
     r"\b(a2|b1|b2|c1|c2)\b|deutsch"),
    ("Englisch", r"englisch", r"englisch|english"),
    ("Schichtbereitschaft", r"schicht", r"schicht"),
    ("Nachtarbeit", r"nachtschicht|nachtarbeit|nachtdienst", r"nacht"),
    ("Wochenendarbeit", r"wochenend", r"wochenend"),
    ("Sachkunde §34a", r"34\s?a\b|sachkunde", r"34\s?a\b|sachkunde"),
    ("abgeschlossene Ausbildung", r"abgeschlossene\w* (berufs)?ausbildung|ausbildung (als|zum|zur)|ausgebildete",
     r"ausbildung|abschluss|geselle|facharbeiter|diplom|bachelor|master|studium"),
    ("Berufserfahrung", r"berufserfahrung|erfahrung (im|in|als|mit)|erfahrene",
     r"erfahrung|jahre|tätig|gearbeitet|beschäftigt|praktikum|angestellt|mitarbeiter|\b(19|20)\d\d\s*[-–]\s*(19|20)\d\d"),
    ("PC / MS Office", r"ms[- ]office|pc-kenntnisse|edv|computer|excel", r"office|edv|computer|\bpc\b|excel|word"),
    ("körperliche Belastbarkeit", r"körperlich\w* belastbar|belastbarkeit|schwer\w* heben", r"belastbar|kräftig|\bfit\b"),
    ("Pflege-Qualifikation", r"pflegebasiskurs|pflegehelfer|pflegefachkraft|examinier|pflegeausbildung", r"pflege"),
    ("Führungszeugnis", r"führungszeugnis", r"führungszeugnis"),
    ("Gesundheitszeugnis / Hygiene", r"gesundheitszeugnis|hygieneschulung|belehrung nach", r"gesundheitszeugnis|hygiene"),
    ("Kundenkontakt", r"kundenorientier|freundlich\w* auftreten|kundenkontakt|serviceorientier",
     r"kunden|service|verkauf|gastronomie|kellner|küche|beratung|patienten|empfang|kasse|"
     r"bürger|dolmetsch|betreuung"),
    ("eigener PKW", r"eigene\w* (pkw|auto|fahrzeug)|mit eigenem (pkw|auto)", r"eigene\w* (pkw|auto|fahrzeug)|\bauto\b"),
]
PLUS = [   # was für unsere Kunden spricht
    ("Quereinstieg möglich", r"quereinsteiger|keine vorkenntnisse|ohne vorkenntnisse|ohne erfahrung|ungelernt|anlernen|lernen dich an|keine ausbildung (nötig|erforderlich|notwendig)"),
    ("unbefristet", r"unbefristet"),
    ("Sprachkurs / Sprachunterstützung", r"sprachkurs|deutschkurs|mehrsprachig"),
    ("Vollzeit", r"vollzeit"), ("Teilzeit", r"teilzeit"), ("Minijob", r"minijob|geringfügig"),
]
WOHN_MERKMALE = {   # Kriterium → (positiv in Exposé, negativ in Exposé)
    "wbs": (r"\bwbs\b|wohnberechtigung", r"kein\w* wbs|ohne wbs"),
    "barrierefrei": (r"barrierefrei|stufenlos|rollstuhl|behindertengerecht|schwellenlos|seniorengerecht", None),
    "balkon": (r"balkon|terrasse|loggia", r"kein\w* balkon|ohne balkon"),
    "garten": (r"garten", r"kein\w* garten"),
    "einbaukueche": (r"einbauküche|\bebk\b|küche vorhanden", r"keine einbauküche|ohne (einbau)?küche"),
    "keller": (r"keller", None),
    "aufzug": (r"aufzug|fahrstuhl|\blift\b", r"kein\w* aufzug|ohne aufzug"),
    "gaeste_wc": (r"gäste-?wc|gästetoilette", None),
    "stellplatz": (r"stellplatz|garage|tiefgarage|parkplatz", None),
    "haustiere": (r"haustiere? (erlaubt|willkommen|nach vereinbarung|möglich)|tiere erlaubt", r"keine haustiere|haustiere nicht"),
    "neubau": (r"neubau|erstbezug", None),
}


def _kunden_text(kunde_id, p=None):
    """Alles, was das OS über den Kunden weiß, als ein Text für den Abgleich."""
    k = db.eine("SELECT sprache, hinweis FROM kunde WHERE id=?", (kunde_id,)) or {}
    kp = db.eine("SELECT kurzprofil, cv_text FROM kunde_profil WHERE kunde_id=?", (kunde_id,)) or {}
    teile = [kp.get("kurzprofil"), kp.get("cv_text"), (p or {}).get("notiz"), k.get("sprache"), k.get("hinweis")]
    return "\n".join(t for t in teile if t)


VERNEINT = re.compile(r"\b(kein\w*|ohne|nicht|noch kein\w*)\b[^.\n,;]{0,25}$")


def _erfuellt(kunde_text, muster):
    """Trifft das Muster beim Kunden – und steht davor kein „kein/ohne/nicht"?
    „kein Führerschein" im Kurzprofil darf die Anforderung Führerschein nicht erfüllen."""
    for m in re.finditer(muster, kunde_text):
        if not VERNEINT.search(kunde_text[max(0, m.start() - 30):m.start()]):
            return True
    return False


def _abgleich_job(text, kunde_text):
    passt, fehlt, unklar, plus = [], [], [], []
    st, kt = text.casefold(), kunde_text.casefold()
    bekannt = len(kt) >= 40                     # ohne Kurzprofil/Lebenslauf: nur „unklar", nie „fehlt"
    for bez, in_stelle, beim_kunden in MERKMALE:
        if not re.search(in_stelle, st):
            continue
        if _erfuellt(kt, beim_kunden):
            passt.append(bez)
        elif bekannt:
            fehlt.append(bez)
        else:
            unklar.append(bez)
    for bez, muster in PLUS:
        if re.search(muster, st):
            plus.append(bez)
    return passt, fehlt, unklar, plus


def _abgleich_wohnung(text, z, p):
    k = kriterien(p)
    passt, fehlt, unklar, plus = [], [], [], []
    t = text.casefold() + " " + " ".join(str(v) for v in z.values() if v).casefold()
    for schl, (ja, nein) in WOHN_MERKMALE.items():
        if not k.get(schl):
            continue
        bez = KRITERIEN_TEXT[schl]
        if nein and re.search(nein, t):
            fehlt.append(bez)
        elif re.search(ja, t):
            passt.append(bez)
        else:
            unklar.append(bez)
    warm = re.sub(r"[^\d]", "", str(z.get("warmmiete") or "").split(",")[0])
    if k.get("max_warmmiete") and warm:
        (passt if int(warm) <= k["max_warmmiete"] else fehlt).append(f"Warmmiete {warm} €")
    et = re.search(r"-?\d+", str(z.get("etage") or ""))
    if et and (k.get("etage_max") is not None or k.get("etage_min") is not None):
        e = int(et.group(0))
        ok = (k.get("etage_min") is None or e >= k["etage_min"]) and (k.get("etage_max") is None or e <= k["etage_max"])
        (passt if ok else fehlt).append(f"Etage {e}")
    if k.get("wohnungstyp") and z.get("wohnungstyp"):
        namen = [dict(WOHNUNGSTYPEN)[x].casefold() for x in k["wohnungstyp"]]
        (passt if any(n in str(z["wohnungstyp"]).casefold() for n in namen) else fehlt).append(str(z["wohnungstyp"]))
    if z.get("verfuegbar"):
        plus.append(f"frei ab {z['verfuegbar']}")
    if k.get("kein_tausch") and re.search(r"tausch", t):
        fehlt.append("Tauschwohnung")
    return passt, fehlt, unklar, plus


def abgleich(aid, laden=True):
    """Beschreibung nachladen und gegen Kunde (Job) bzw. Kriterien (Wohnung) abgleichen."""
    a = db.eine("SELECT a.*, p.art, p.kunde_id, p.notiz AS profil_notiz, p.kriterien"
                "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id WHERE a.id=?", (aid,))
    if not a:
        return None
    p = {"art": a["art"], "notiz": a["profil_notiz"], "kriterien": a["kriterien"]}
    text, z = a.get("beschreibung_lang") or "", zusatz(a)
    if laden and not text:
        text, z = beschreibung_laden(a)
    text = (text or "").strip()
    grundlage = " ".join(x for x in (a.get("titel"), text or a.get("beschreibung")) if x)
    if a["art"] == "job":
        passt, fehlt, unklar, plus = _abgleich_job(grundlage, _kunden_text(a["kunde_id"], p))
    else:
        passt, fehlt, unklar, plus = _abgleich_wohnung(grundlage, z, p)
    n = len(passt) + len(fehlt)
    match = round(100 * len(passt) / n) if n else None
    ergebnis = {"passt": passt, "fehlt": fehlt, "unklar": unklar, "plus": plus}
    with db.offen() as con:
        con.execute("UPDATE tf_angebot SET beschreibung_lang=?, abgleich=?, match=?, abgleich_am=?, zusatz=?,"
                    " beschreibung=COALESCE(beschreibung, ?) WHERE id=?",
                    (text[:6000] or None, json.dumps(ergebnis, ensure_ascii=False), match, jetzt(),
                     json.dumps(z, ensure_ascii=False), text[:400] or None, aid))
    return ergebnis, match


def abgleich_profil(pid, max_n=20, status="neu"):
    """Die relevantesten noch nicht abgeglichenen Angebote eines Profils nachladen und abgleichen."""
    ids = [r["id"] for r in db.hole(
        "SELECT id FROM tf_angebot WHERE profil_id=? AND status=? AND abgleich IS NULL"
        " ORDER BY COALESCE(score,0) DESC, id DESC LIMIT ?", (pid, status, max_n))]
    n = 0
    for i, aid in enumerate(ids):
        try:
            abgleich(aid)
            n += 1
        except Exception as e:
            with db.offen() as con:      # nicht endlos erneut versuchen – Fehler festhalten
                con.execute("UPDATE tf_angebot SET abgleich=?, abgleich_am=? WHERE id=?",
                            (json.dumps({"fehler": str(e)[:160]}, ensure_ascii=False), jetzt(), aid))
        if i < len(ids) - 1:
            time.sleep(0.8)
    return n


def abgleich_von(a):
    try:
        return json.loads(a.get("abgleich") or "{}")
    except (TypeError, ValueError):
        return {}


def kunden_info(kunde_id):
    """Alles über den Kunden auf einen Blick – für Profilseite und Tafel."""
    k = db.eine("SELECT k.*, m.name AS coach FROM kunde k LEFT JOIN mitarbeiter m ON m.id=k.coach_id"
                " WHERE k.id=?", (kunde_id,))
    if not k:
        return None
    k["status_text"] = db.STATUS.get(k.get("status_code"), "—")
    k["lebenslaeufe"] = db.hole("SELECT * FROM lebenslauf WHERE kunde_id=? ORDER BY geaendert DESC, id DESC",
                                (kunde_id,))
    k["profil"] = db.eine("SELECT * FROM kunde_profil WHERE kunde_id=?", (kunde_id,)) or {}
    k["gutscheine"] = db.hole("SELECT * FROM gutschein WHERE kunde_id=?", (kunde_id,))
    return k


def ohne_lebenslauf(standort=db.STANDORT_STANDARD):
    """Aktive Jobprofile, deren Kunde keinen Lebenslauf im OS hat."""
    return db.hole(
        "SELECT p.id, p.titel, k.id AS kunde_id, k.name AS kunde FROM tf_profil p JOIN kunde k ON k.id=p.kunde_id"
        " WHERE p.art='job' AND p.aktiv=1 AND p.standort=?"
        "   AND NOT EXISTS (SELECT 1 FROM lebenslauf l WHERE l.kunde_id=k.id) ORDER BY k.name", (standort,))


if __name__ == "__main__":
    import umgebung
    umgebung.laden()
    db.init()
    init()
    if "--lauf" in sys.argv:
        for pid, titel, gefunden, neu, meldung in alle_laufen():
            print(f"  {titel:40} gefunden {gefunden:3}  neu {neu:3}  {meldung}")
        print(f"  offen für die Taskforce: {anzahl_neu()} neue Angebote")
    else:
        print("Aufruf: python taskforce.py --lauf   (alle aktiven Profile, alle Quellen)")
