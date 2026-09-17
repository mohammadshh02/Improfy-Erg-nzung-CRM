# -*- coding: utf-8 -*-
"""Kommo - der Online-Lead-Trichter.

Kommo haelt die Anfragen, die ueber WhatsApp hereinkommen, bevor sie ueberhaupt
Kunden werden. Am 07.09.2026 lagen dort 796 Leads, davon 790 in den ersten zwei
Stufen - das ist die groesste einzelne Luecke im Standortbild.

Zugang: ein Long-lived Token aus der privaten Integration "Improfy-OS"
(Einstellungen -> Integration marketplace -> Improfy-OS -> Keys and scopes).
Er gehoert als KOMMO_TOKEN in die .env, nie in den Code.

Die vier Kommo-Nutzer sind Sammelkonten je Standort, keine einzelnen Coaches:
improfy.hamburg@ (Nawaf), improfy.koeln@ (Zahra), Frankfurt, Mannheim. Deshalb
ordnet dieser Adapter Leads dem **Standort** zu und nicht einer Person - eine
Coach-Zuordnung gibt es in Kommo schlicht nicht.
"""
import json
import os
import urllib.error
import urllib.request

import datenbank
from quellen.basis import Quelle

BASIS = os.environ.get("KOMMO_BASIS", "https://improfyhamburg.kommo.com")

# Kommo-Nutzer -> Improfy-Standort. Die IDs traegt der erste Lauf selbst nach.
KONTO_STANDORT = {
    "improfy.koeln@gmail.com":    "Köln",
    "improfy.hamburg@gmail.com":  "Hamburg",
    "frankfurt.improfy@gmail.com": "Frankfurt",
    "mannheim.improfy@gmail.com": "Mannheim",
}


# Nur wo hinter dem Sammelkonto nachweislich eine Person steht.
# improfy.koeln@ laeuft in Kommo unter "Zahra ( Leadcreator)".
KONTO_MITARBEITER_NAME = {
    "improfy.koeln@gmail.com": "Zahra Ahmadian",
}
KONTO_MITARBEITER = {}      # wird beim Einlesen mit IDs gefuellt


class Kommo(Quelle):
    name = "Kommo"
    braucht = "KOMMO_TOKEN in der .env"

    def api_bereit(self):
        return bool(os.environ.get("KOMMO_TOKEN"))

    def _hole(self, pfad, **parameter):
        """Eine Seite der API holen. Gibt None zurueck, wenn nichts mehr kommt.

        Kommo antwortet auf die letzte Seite mit 204 ohne Inhalt - das ist kein
        Fehler, sondern das Ende der Liste.
        """
        if parameter:
            teile = "&".join(f"{s}={w}" for s, w in parameter.items())
            pfad = f"{pfad}?{teile}"
        anfrage = urllib.request.Request(
            f"{BASIS}/api/v4/{pfad}",
            headers={"Authorization": f"Bearer {os.environ['KOMMO_TOKEN']}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(anfrage, timeout=30) as antwort:
                if antwort.status == 204:
                    return None
                return json.load(antwort)
        except urllib.error.HTTPError as fehler:
            if fehler.code == 204:
                return None
            if fehler.code == 401:
                raise RuntimeError(
                    "Kommo lehnt den Token ab (401). Ist KOMMO_TOKEN aktuell "
                    "und nicht abgelaufen?") from fehler
            raise

    def _nutzer(self):
        daten = self._hole("users", limit=250) or {}
        heraus = {}
        for n in daten.get("_embedded", {}).get("users", []):
            heraus[n["id"]] = {
                "name": n.get("name") or "",
                "email": (n.get("email") or "").lower(),
            }
        return heraus

    def _stufen(self):
        """Pipeline-Stufen: id -> (Name, Reihenfolge)."""
        daten = self._hole("leads/pipelines") or {}
        heraus = {}
        for p in daten.get("_embedded", {}).get("pipelines", []):
            for s in p.get("_embedded", {}).get("statuses", []):
                heraus[s["id"]] = s.get("name") or str(s["id"])
        return heraus

    def einlesen(self):
        with datenbank.offen() as con:
            vorhanden = {r[1] for r in con.execute("PRAGMA table_info(lead)")}
            if "bearbeiter" not in vorhanden:
                con.execute("ALTER TABLE lead ADD COLUMN bearbeiter TEXT")
            namen = {r["name"]: r["id"] for r in con.execute(
                "SELECT id, name FROM mitarbeiter")}
        KONTO_MITARBEITER.clear()
        for mail, name in KONTO_MITARBEITER_NAME.items():
            if name in namen:
                KONTO_MITARBEITER[mail] = namen[name]

        nutzer = self._nutzer()
        stufen = self._stufen()
        anzahl, seite = 0, 1
        with datenbank.offen() as con:
            con.execute("DELETE FROM lead WHERE quelle='Kommo'")
            while True:
                daten = self._hole("leads", page=seite, limit=250)
                if not daten:
                    break
                treffer = daten.get("_embedded", {}).get("leads", [])
                if not treffer:
                    break
                for lead in treffer:
                    besitzer = nutzer.get(lead.get("responsible_user_id"), {})
                    standort = KONTO_STANDORT.get(besitzer.get("email", ""), "unbekannt")
                    # Kommo kennt nur die vier Sammelkonten. Wer den Lead
                    # tatsaechlich bearbeitet, steht nirgends - deshalb wird das
                    # Konto festgehalten und NICHT als Coach ausgegeben.
                    con.execute(
                        "INSERT INTO lead (name, quelle, eingang, status, standort,"
                        " bearbeiter, coach_id)"
                        " VALUES (?, 'Kommo', ?, ?, ?, ?, ?)",
                        (lead.get("name") or f"Lead {lead['id']}",
                         _tag(lead.get("created_at")),
                         stufen.get(lead.get("status_id"), "unbekannt"),
                         standort, besitzer.get("name") or "—",
                         KONTO_MITARBEITER.get(besitzer.get("email", ""))))
                    anzahl += 1
                seite += 1
                if seite > 40:          # Notbremse: 10.000 Leads sind genug
                    break
        return anzahl


def _tag(zeitstempel):
    """Kommo liefert Unix-Sekunden. Ohne Wert bleibt das Feld leer."""
    if not zeitstempel:
        return None
    import datetime
    return datetime.date.fromtimestamp(zeitstempel).isoformat()
