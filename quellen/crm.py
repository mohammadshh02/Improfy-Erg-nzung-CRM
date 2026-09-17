# -*- coding: utf-8 -*-
"""Anbindung an das produktive CRM (crm.improfy.de) – lesend.

Das CRM ist die verbindliche Akte: Kunden, Coaches, Termine, UE, Gutscheine,
Abrechnung. Das OS ergaenzt darum herum (Taskforce, Monatsblatt, Vertrieb) und
soll seine Stammdaten mittelfristig nicht mehr aus Exporten ziehen, sondern von
dort lesen.

**Richtung:** nur lesen. Dieser Adapter schreibt nie ins CRM. Eine lesende
Anbindung kann das CRM nicht beschaedigen; das ist die ganze Sicherheitszusage.

**Vorrang der Quellen** (siehe auch docs/wissen/crm-abgleich-was-gehoert-wohin.md):

    CRM  >  HubSpot  >  Sheets/Drive-Exporte

Sobald `CRM_BASIS` und `CRM_TOKEN` in der `.env` stehen, fuehrt das CRM die
Stammdaten und ueberschreibt, was die aelteren Quellen geliefert haben. Fehlt
der Zugang, aendert sich nichts: die bisherigen Quellen laufen weiter. HubSpot
wird abgeschaltet – bis dahin bleibt es die zweite Quelle, danach faellt es
ersatzlos weg, ohne dass hier etwas umgebaut werden muss.

Erwartet wird eine Schnittstelle, die JSON liefert. Welchen genauen Pfad das CRM
anbietet, steht noch nicht fest; deshalb sind die Pfade ueber die `.env`
einstellbar und die Feldnamen werden tolerant gelesen.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import datenbank
from quellen.basis import Quelle

SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_kunde (
    crm_id        TEXT PRIMARY KEY,      -- Id des Kunden im CRM
    kunde_id      INTEGER REFERENCES kunde(id),
    kundennummer  TEXT,                  -- Kd-Nr. bzw. BG-Nummer, der Schlüssel zwischen beiden Systemen
    name          TEXT,
    stadt         TEXT,
    coach         TEXT,
    massnahme     TEXT,
    status        TEXT,
    ue_gebucht    REAL,
    ue_kontingent REAL,
    stand         TEXT
);
"""

ZEIT = 20


def basis():
    return (os.environ.get("CRM_BASIS") or "").rstrip("/")


def bereit():
    return bool(basis() and os.environ.get("CRM_TOKEN"))


def _hole(pfad, params=None):
    url = basis() + pfad + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + os.environ["CRM_TOKEN"],
        "Accept": "application/json",
        "User-Agent": "Improfy-OS (lesend)"})
    with urllib.request.urlopen(req, timeout=ZEIT) as r:
        return json.loads(r.read().decode("utf-8"))


def _liste(antwort):
    """Das CRM kann eine Liste liefern oder ein Objekt mit 'data'/'items'."""
    if isinstance(antwort, list):
        return antwort
    for feld in ("data", "items", "kunden", "results", "rows"):
        if isinstance(antwort, dict) and isinstance(antwort.get(feld), list):
            return antwort[feld]
    return []


def _feld(satz, *namen):
    for n in namen:
        if isinstance(satz, dict) and satz.get(n) not in (None, ""):
            return satz[n]
    return None


def kunden():
    """Kunden des Standorts aus dem CRM, auf die Felder des OS gebracht."""
    pfad = os.environ.get("CRM_PFAD_KUNDEN") or "/api/kunden"
    satzliste = _liste(_hole(pfad, {"standort": os.environ.get("CRM_STANDORT") or "Köln"}))
    kunden = []
    for s in satzliste:
        kunden.append({
            "crm_id": str(_feld(s, "id", "customer_id", "kunde_id") or ""),
            "kundennummer": _feld(s, "kundennummer", "kd_nr", "customer_number", "bg_nummer"),
            "name": _feld(s, "name", "full_name") or " ".join(
                x for x in (_feld(s, "vorname", "first_name"), _feld(s, "nachname", "last_name")) if x),
            "telefon": _feld(s, "telefon", "phone"),
            "email": _feld(s, "email", "e_mail"),
            "sprache": _feld(s, "sprache", "language"),
            "stadt": _feld(s, "ort", "stadt", "city"),
            "plz": _feld(s, "plz", "zip"),
            "coach": _feld(s, "coach", "zustaendig", "owner", "betreuer"),
            "massnahme": _feld(s, "massnahme", "measure"),
            "status": _feld(s, "status", "stage"),
            "ue_gebucht": _feld(s, "ue_gebucht", "ue_booked", "ue"),
            "ue_kontingent": _feld(s, "ue_kontingent", "ue_total", "kontingent"),
        })
    return kunden


def mitarbeiter():
    pfad = os.environ.get("CRM_PFAD_NUTZER") or "/api/nutzer"
    return [{"name": _feld(s, "name", "full_name"),
             "rolle": _feld(s, "rolle", "role"),
             "standort": _feld(s, "standort", "location"),
             "crm_id": str(_feld(s, "id") or "")}
            for s in _liste(_hole(pfad))]


def probe():
    """Verbindungstest fuer die Quellen-Seite: antwortet das CRM und wie viele Kunden kommen an?"""
    if not bereit():
        return {"ok": False, "meldung": "CRM_BASIS und CRM_TOKEN fehlen in der .env"}
    try:
        k = kunden()
        return {"ok": True, "meldung": f"{len(k)} Kunden gelesen", "anzahl": len(k)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "meldung": f"CRM antwortet mit {e.code} ({e.reason})"}
    except Exception as e:
        return {"ok": False, "meldung": f"{type(e).__name__}: {e}"}


def _passender_kunde(satz, bekannte):
    """Zuordnung ueber die Kundennummer, sonst ueber den Namen – nur wenn eindeutig."""
    nummer = (satz.get("kundennummer") or "").strip()
    if nummer:
        treffer = [k["id"] for k in bekannte if (k["kundennummer"] or "").strip() == nummer]
        if len(treffer) == 1:
            return treffer[0]
    name = (satz.get("name") or "").casefold().strip()
    if name:
        treffer = [k["id"] for k in bekannte if (k["name"] or "").casefold().strip() == name]
        if len(treffer) == 1:
            return treffer[0]
    return None


class CRM(Quelle):
    name = "CRM crm.improfy.de (lesend)"
    braucht = "CRM_BASIS und CRM_TOKEN in der .env"

    def api_bereit(self):
        return bereit()

    def einlesen(self):
        """Stammdaten aus dem CRM uebernehmen. Das CRM hat Vorrang vor HubSpot und Exporten.

        Neue Kunden werden angelegt, bekannte aktualisiert. Felder, die das CRM
        nicht fuehrt (Taskforce, QM-Stand des OS), bleiben unangetastet."""
        satzliste = kunden()
        jetzt = datenbank.jetzt() if hasattr(datenbank, "jetzt") else None
        with datenbank.offen() as con:
            con.executescript(SCHEMA)
            bekannte = [dict(r) for r in con.execute(
                "SELECT id, name, kundennummer FROM kunde WHERE standort=?", (self.standort,))]
            anzahl = 0
            for s in satzliste:
                kid = _passender_kunde(s, bekannte)
                if kid is None:
                    cur = con.execute(
                        "INSERT INTO kunde (name, telefon, email, sprache, stadt, plz, kundennummer,"
                        " massnahme, standort, quelle_stand) VALUES (?,?,?,?,?,?,?,?,?,'CRM')",
                        (s["name"], s["telefon"], s["email"], s["sprache"], s["stadt"], s["plz"],
                         s["kundennummer"], s["massnahme"], self.standort))
                    kid = cur.lastrowid
                    bekannte.append({"id": kid, "name": s["name"], "kundennummer": s["kundennummer"]})
                else:
                    con.execute(
                        "UPDATE kunde SET telefon=COALESCE(?,telefon), email=COALESCE(?,email),"
                        " sprache=COALESCE(?,sprache), stadt=COALESCE(?,stadt), plz=COALESCE(?,plz),"
                        " kundennummer=COALESCE(?,kundennummer), massnahme=COALESCE(?,massnahme),"
                        " quelle_stand='CRM' WHERE id=?",
                        (s["telefon"], s["email"], s["sprache"], s["stadt"], s["plz"],
                         s["kundennummer"], s["massnahme"], kid))
                con.execute(
                    "INSERT INTO crm_kunde (crm_id, kunde_id, kundennummer, name, stadt, coach,"
                    " massnahme, status, ue_gebucht, ue_kontingent, stand)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(crm_id) DO UPDATE SET kunde_id=excluded.kunde_id,"
                    " kundennummer=excluded.kundennummer, name=excluded.name, stadt=excluded.stadt,"
                    " coach=excluded.coach, massnahme=excluded.massnahme, status=excluded.status,"
                    " ue_gebucht=excluded.ue_gebucht, ue_kontingent=excluded.ue_kontingent,"
                    " stand=excluded.stand",
                    (s["crm_id"], kid, s["kundennummer"], s["name"], s["stadt"], s["coach"],
                     s["massnahme"], s["status"], s["ue_gebucht"], s["ue_kontingent"], jetzt))
                anzahl += 1
        return anzahl
