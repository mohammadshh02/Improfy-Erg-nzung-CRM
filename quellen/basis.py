# -*- coding: utf-8 -*-
"""Gemeinsame Schnittstelle aller Quell-Adapter."""
import datetime
import os

import datenbank


class Quelle:
    """Basis fuer jeden Adapter.

    Ein Adapter meldet, ob er einsatzbereit ist (`bereit`), und liefert bei
    `einlesen()` die Anzahl uebernommener Datensaetze. Ist er nicht bereit,
    sagt `grund` in einem Satz warum - das zeigt das Dashboard direkt an,
    damit man nicht raten muss, warum eine Kachel leer bleibt.
    """

    name = "unbenannt"
    braucht = ""          # was fehlt, damit die API-Betriebsart laeuft

    def __init__(self, standort=datenbank.STANDORT_STANDARD):
        self.standort = standort

    @property
    def betriebsart(self):
        return "api" if self.api_bereit() else "datei"

    def api_bereit(self):
        return False

    def bereit(self):
        return self.api_bereit() or self.datei_bereit()

    def datei_bereit(self):
        return False

    @property
    def grund(self):
        if self.bereit():
            return ""
        return f"{self.braucht} fehlt – bis dahin bleibt {self.name} leer."

    def einlesen(self):
        raise NotImplementedError

    # -- Hilfen fuer die Adapter -------------------------------------------

    def protokolliere(self, anzahl, status="ok", meldung=""):
        with datenbank.offen() as con:
            con.execute(
                "INSERT INTO import_lauf (quelle, zeitpunkt, anzahl, status, meldung)"
                " VALUES (?,?,?,?,?)",
                (self.name, datetime.datetime.now().isoformat(timespec="seconds"),
                 anzahl, status, meldung),
            )

    def lauf(self):
        """Einlesen mit Protokoll - so ruft `importieren.py` jede Quelle auf."""
        if not self.bereit():
            self.protokolliere(0, "uebersprungen", self.grund)
            return 0, self.grund
        try:
            anzahl = self.einlesen()
        except Exception as fehler:               # noqa: BLE001 - Import darf nie alles reissen
            self.protokolliere(0, "fehler", str(fehler))
            return 0, str(fehler)
        self.protokolliere(anzahl)
        return anzahl, ""


def export_ordner():
    """Wo die von Hand gezogenen Exporte liegen."""
    hier = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pfad = os.path.join(hier, "exporte")
    os.makedirs(pfad, exist_ok=True)
    return pfad


def neueste_datei(*muster):
    """Juengste Datei im Export-Ordner, die auf eines der Muster passt.

    So muss niemand Dateinamen pflegen: man legt den frischen HubSpot-Export
    einfach dazu, das OS nimmt beim naechsten Import automatisch den neuen.
    """
    import glob
    treffer = []
    for m in muster:
        treffer += glob.glob(os.path.join(export_ordner(), m))
    if not treffer:
        return None
    return max(treffer, key=os.path.getmtime)
