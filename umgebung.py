# -*- coding: utf-8 -*-
"""Laedt die .env - fuer alle Einstiegspunkte, nicht nur fuer das Dashboard.

Vorher lud nur `app.py` die .env. Der Import ueber die Kommandozeile sah
deshalb keinen einzigen Token und meldete "KOMMO_TOKEN fehlt", obwohl er
danebenstand. Genau die Art Fehler, die man stundenlang an der falschen
Stelle sucht.

Bestehende Umgebungsvariablen gewinnen: wer beim Aufruf `KOMMO_TOKEN=... `
davorschreibt, will das auch.
"""
import os

HIER = os.path.dirname(os.path.abspath(__file__))
ENV_DATEI = os.path.join(HIER, ".env")


def laden(pfad=None):
    """Liest KEY=WERT-Zeilen und legt sie in os.environ ab."""
    pfad = pfad or ENV_DATEI
    if not os.path.exists(pfad):
        return 0
    gesetzt = 0
    for zeile in open(pfad, encoding="utf-8"):
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, wert = zeile.split("=", 1)
        wert = wert.strip().strip('"').strip("'")
        if wert and os.environ.setdefault(schluessel.strip(), wert) == wert:
            gesetzt += 1
    return gesetzt
