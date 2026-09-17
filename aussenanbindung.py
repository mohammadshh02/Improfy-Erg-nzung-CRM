# -*- coding: utf-8 -*-
"""Außenanbindungen – jede Schnittstelle des OS nach draußen, mit Live-Prüfung.

Das OS holt Daten von außen (Stellenportale, Wohnungsportale, Kalender, HubSpot,
CRM) und schickt Daten nach außen (Chat-Alarm). Dieses Modul ist das Register:
was gibt es, wie wird zugegriffen, was braucht es dafür, und antwortet es gerade.

Warum das eine eigene Seite verdient: Die Portale liefern nicht alle über eine
offizielle Schnittstelle. Wo es eine gibt (Bundesagentur, Adzuna, Jooble), ist der
Zugriff sauber und stabil. Wo es keine gibt, liest das OS die öffentliche
Ergebnisliste der Seite; das kann jederzeit brechen, wenn der Anbieter sein
Seitengerüst ändert, und Indeed sperrt zeitweise. Und wo der Anbieter es
ausdrücklich untersagt (ImmoScout24), wird bewusst nicht gelesen, sondern der
offizielle Weg über Suchauftrags-Mails genommen.

Die Live-Prüfung ruft jede Quelle einmal mit einem festen Probe-Profil auf und
misst, ob und wie schnell sie antwortet. Sie schreibt nichts in die Datenbank.
"""
import datetime
import os
import time

import datenbank as db
import taskforce as tf

# art:      holen | senden
# zugriff:  api (offizielle Schnittstelle) | seite (öffentliche Ergebnisliste)
#           | mail (Postfach) | datei (Export) | webhook
# vertrag:  was der Anbieter verlangt
ANBINDUNGEN = [
    {
        "schluessel": "jobs.ba", "name": "Bundesagentur für Arbeit – Jobsuche",
        "bereich": "Stellen", "art": "holen", "zugriff": "api",
        "endpunkt": "rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
        "braucht": "nichts – öffentlicher Schlüssel der BA-App",
        "vertrag": "öffentliche Schnittstelle der BA",
        "hinweis": "Größte deutsche Stellensammlung. Auch die Detailseite kommt über die "
                   "Schnittstelle (v4/jobdetails), daraus stammt der Text für den Abgleich.",
        "stabil": "hoch",
    },
    {
        "schluessel": "jobs.indeed", "name": "Indeed",
        "bereich": "Stellen", "art": "holen", "zugriff": "seite",
        "endpunkt": "de.indeed.com/jobs",
        "braucht": "nichts, aber vollständige Browser-Kopfzeilen",
        "vertrag": "keine offene Schnittstelle für uns; gelesen wird die öffentliche Trefferliste",
        "hinweis": "Bot-Schutz sperrt zeitweise (403). Der Agent wiederholt bis zu dreimal. "
                   "Detailseiten blocken meist, deshalb nur der Kurztext aus der Trefferliste.",
        "stabil": "mittel",
    },
    {
        "schluessel": "jobs.stepstone", "name": "StepStone",
        "bereich": "Stellen", "art": "holen", "zugriff": "seite",
        "endpunkt": "stepstone.de/jobs/…",
        "braucht": "nichts",
        "vertrag": "keine offene Schnittstelle für uns; gelesen wird die öffentliche Trefferliste",
        "hinweis": "Die Seite trägt ihre Treffer als JSON in sich. Die Stellenbeschreibung kommt "
                   "aus dem schema.org-Block der Detailseite.",
        "stabil": "mittel",
    },
    {
        "schluessel": "jobs.meinestadt", "name": "jobs.meinestadt.de",
        "bereich": "Stellen", "art": "holen", "zugriff": "seite",
        "endpunkt": "jobs.meinestadt.de/<stadt>/suche",
        "braucht": "nichts, aber vollständige Browser-Kopfzeilen",
        "vertrag": "keine offene Schnittstelle für uns; gelesen wird die öffentliche Trefferliste",
        "hinweis": "Treffer stehen als schema.org-Liste im Seitenkopf, doppelt HTML-kodiert.",
        "stabil": "mittel",
    },
    {
        "schluessel": "jobs.kleinanzeigen", "name": "Kleinanzeigen – Jobs",
        "bereich": "Stellen", "art": "holen", "zugriff": "seite",
        "endpunkt": "kleinanzeigen.de/s-suchanfrage.html (Rubrik 102)",
        "braucht": "nichts",
        "vertrag": "keine offene Schnittstelle für uns",
        "hinweis": "Viele Zeitarbeits- und Quereinsteigerangebote, die auf den großen Portalen fehlen.",
        "stabil": "mittel",
    },
    {
        "schluessel": "jobs.arbeitnow", "name": "Arbeitnow",
        "bereich": "Stellen", "art": "holen", "zugriff": "api",
        "endpunkt": "arbeitnow.com/api/job-board-api",
        "braucht": "nichts",
        "vertrag": "frei nutzbare Schnittstelle",
        "hinweis": "Schwerpunkt Büro und IT, für unsere Kundschaft dünn. Wird lokal nachgefiltert.",
        "stabil": "hoch",
    },
    {
        "schluessel": "jobs.adzuna", "name": "Adzuna",
        "bereich": "Stellen", "art": "holen", "zugriff": "api",
        "endpunkt": "api.adzuna.com/v1/api/jobs/de/search",
        "braucht": "ADZUNA_APP_ID und ADZUNA_APP_KEY",
        "vertrag": "kostenlose Registrierung als Entwickler",
        "hinweis": "Bündelt viele Portale. Sobald die Schlüssel in der .env stehen, läuft sie mit.",
        "stabil": "hoch",
    },
    {
        "schluessel": "jobs.jooble", "name": "Jooble",
        "bereich": "Stellen", "art": "holen", "zugriff": "api",
        "endpunkt": "de.jooble.org/api/<schlüssel>",
        "braucht": "JOOBLE_KEY",
        "vertrag": "Schlüssel auf Anfrage beim Anbieter",
        "hinweis": "Meta-Suche über viele Portale.",
        "stabil": "hoch",
    },
    {
        "schluessel": "wohnung.mail", "name": "ImmoScout24 – Suchaufträge",
        "bereich": "Wohnungen", "art": "holen", "zugriff": "mail",
        "endpunkt": "IMAP-Postfach, Absender immobilienscout24",
        "braucht": "TF_IMAP_HOST, TF_IMAP_USER, TF_IMAP_PASSWORT",
        "vertrag": "offizieller Weg: Suchauftrag im Premium-Konto, Treffer kommen per Mail",
        "hinweis": "Bewusst kein Auslesen der Seite: ImmoScout24 untersagt das in den Nutzungs"
                   "bedingungen und sperrt technisch. Das OS erzeugt stattdessen den fertigen "
                   "Suchlink mit allen Kriterien; der Suchauftrag liefert dieselben Treffer legal.",
        "stabil": "hoch",
    },
    {
        "schluessel": "wohnung.kleinanzeigen", "name": "Kleinanzeigen – Mietwohnungen",
        "bereich": "Wohnungen", "art": "holen", "zugriff": "seite",
        "endpunkt": "kleinanzeigen.de/s-suchanfrage.html (Rubrik 203)",
        "braucht": "nichts",
        "vertrag": "keine offene Schnittstelle für uns",
        "hinweis": "Gesuche werden ausgefiltert, es bleiben Angebote. WBS wird als Suchwort gesetzt.",
        "stabil": "mittel",
    },
]

# Anbindungen, die nicht zur Taskforce gehören und deshalb anders geprüft werden
WEITERE = [
    {
        "schluessel": "crm", "name": "CRM crm.improfy.de", "bereich": "Stammdaten",
        "art": "holen", "zugriff": "api", "endpunkt": "CRM_BASIS + /api/kunden",
        "braucht": "CRM_BASIS und CRM_TOKEN", "vertrag": "eigenes System",
        "hinweis": "Führt später die Akte. Nur lesend, das OS schreibt nie ins CRM.",
        "stabil": "offen",
    },
    {
        "schluessel": "hubspot", "name": "HubSpot", "bereich": "Stammdaten",
        "art": "holen", "zugriff": "api", "endpunkt": "api.hubapi.com/crm/v3/objects/contacts",
        "braucht": "HUBSPOT_TOKEN (sonst CSV-Export in exporte/)",
        "vertrag": "Private App im eigenen Portal",
        "hinweis": "Läuft aus. Bis zur Umstellung zweite Quelle hinter dem CRM.",
        "stabil": "hoch",
    },
    {
        "schluessel": "kommo", "name": "Kommo", "bereich": "Leads",
        "art": "holen", "zugriff": "api", "endpunkt": "improfyhamburg.kommo.com",
        "braucht": "KOMMO_BASIS und KOMMO_TOKEN", "vertrag": "eigenes Konto",
        "hinweis": "Leads. Kennt nur Sammelkonten je Standort, keine einzelnen Coaches.",
        "stabil": "hoch",
    },
    {
        "schluessel": "google", "name": "Google Drive, Kalender, Chat", "bereich": "Ablage",
        "art": "holen", "zugriff": "datei", "endpunkt": "Exporte in exporte/",
        "braucht": "Exportdateien; für den Dauerbetrieb eigene Google-Zugangsdaten",
        "vertrag": "Firmenkonto",
        "hinweis": "Heute über Exporte, die von Hand gezogen werden. Kundenordner, Termine, CV-Gruppe.",
        "stabil": "mittel",
    },
    {
        "schluessel": "chat_alarm", "name": "Google Chat – Alarm der Taskforce", "bereich": "Meldungen",
        "art": "senden", "zugriff": "webhook", "endpunkt": "TF_CHAT_WEBHOOK",
        "braucht": "TF_CHAT_WEBHOOK", "vertrag": "Webhook des Chat-Raums",
        "hinweis": "Einzige Schnittstelle, die nach außen schreibt: meldet neue Angebote in den Raum.",
        "stabil": "hoch",
    },
    {
        "schluessel": "figma", "name": "Figma – Design-Vorlagen", "bereich": "Lebenslauf",
        "art": "holen", "zugriff": "api", "endpunkt": "api.figma.com/v1/files/<key>",
        "braucht": "FIGMA_TOKEN", "vertrag": "persönliches Zugriffstoken im Figma-Konto",
        "hinweis": "Steckt in der eigenständigen CV-App (Rahmen und Bilder für designte PDF). "
                   "Im OS bisher nicht eingebaut – das OS füllt nur die Excel-Vorlage.",
        "stabil": "offen",
    },
]

PROBE_JOB = {"art": "job", "suchbegriffe": "Lagerhelfer", "ort": "Köln", "umkreis_km": 25,
             "arbeitszeit": "", "zeitarbeit": 1, "quellen": None, "kriterien": None}
PROBE_WOHNUNG = {"art": "wohnung", "ort": "Köln", "umkreis_km": 10, "max_miete": 800,
                 "min_zimmer": 1, "min_flaeche": None, "suchauftrag": "", "kriterien": None}


def _bereit(schluessel):
    eintrag = tf.QUELLEN.get(schluessel)
    if eintrag:
        return eintrag[3]()
    if schluessel == "crm":
        from quellen import crm
        return crm.bereit()
    if schluessel == "hubspot":
        return bool(os.environ.get("HUBSPOT_TOKEN"))
    if schluessel == "kommo":
        return bool(os.environ.get("KOMMO_TOKEN"))
    if schluessel == "chat_alarm":
        return tf.alarm_konfiguriert()
    if schluessel == "figma":
        return bool(os.environ.get("FIGMA_TOKEN"))
    if schluessel == "google":
        import glob
        return bool(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "exporte", "*")))
    return False


def register():
    """Alle Anbindungen mit Konfigurationsstand, ohne sie aufzurufen."""
    zeilen = []
    for a in ANBINDUNGEN + WEITERE:
        z = dict(a)
        z["bereit"] = _bereit(a["schluessel"])
        z["taskforce"] = a["schluessel"] in tf.QUELLEN
        zeilen.append(z)
    return zeilen


def letzte_laeufe(limit=200):
    """Was die Taskforce-Quellen zuletzt geliefert haben – je Quelle zusammengefasst."""
    zeilen = db.hole(
        "SELECT quelle,"
        "  COUNT(*) AS laeufe,"
        "  SUM(gefunden) AS gefunden,"
        "  SUM(neu) AS neu,"
        "  SUM(CASE WHEN meldung<>'' THEN 1 ELSE 0 END) AS fehler,"
        "  MAX(zeitpunkt) AS zuletzt,"
        "  MAX(CASE WHEN meldung<>'' THEN meldung END) AS letzte_meldung"
        "  FROM (SELECT * FROM tf_lauf ORDER BY id DESC LIMIT ?) GROUP BY quelle", (limit,))
    return {z["quelle"]: z for z in zeilen}


def pruefe(schluessel):
    """Eine Anbindung einmal aufrufen und messen. Schreibt nichts in die Datenbank."""
    eintrag = tf.QUELLEN.get(schluessel)
    start = time.monotonic()
    try:
        if eintrag:
            name, art, funktion, bereit = eintrag
            if not bereit():
                return {"ok": None, "meldung": "nicht verbunden", "dauer": 0, "treffer": None}
            treffer = funktion(dict(PROBE_WOHNUNG if art == "wohnung" else PROBE_JOB))
            return {"ok": True, "meldung": f"{len(treffer)} Treffer",
                    "dauer": round(time.monotonic() - start, 1), "treffer": len(treffer)}
        if schluessel == "crm":
            from quellen import crm
            p = crm.probe()
            return {"ok": p["ok"], "meldung": p["meldung"],
                    "dauer": round(time.monotonic() - start, 1), "treffer": p.get("anzahl")}
        return {"ok": None, "meldung": "keine Live-Prüfung vorgesehen", "dauer": 0, "treffer": None}
    except Exception as e:
        return {"ok": False, "meldung": f"{type(e).__name__}: {str(e)[:160]}",
                "dauer": round(time.monotonic() - start, 1), "treffer": None}


def pruefe_alle(nur_bereite=True):
    ergebnis = {}
    for a in ANBINDUNGEN:
        if nur_bereite and not _bereit(a["schluessel"]):
            ergebnis[a["schluessel"]] = {"ok": None, "meldung": "nicht verbunden", "dauer": 0,
                                         "treffer": None}
            continue
        ergebnis[a["schluessel"]] = pruefe(a["schluessel"])
    return {"stand": datetime.datetime.now().isoformat(timespec="seconds"), "ergebnis": ergebnis}
