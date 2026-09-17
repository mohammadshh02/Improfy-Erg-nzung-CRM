# -*- coding: utf-8 -*-
"""Quell-Adapter des Improfy-OS.

Jede Quelle (HubSpot, Google Drive, Kalender, Chat, Kommo) hat hier genau ein
Modul mit derselben Schnittstelle - siehe `basis.py`. Jedes Modul kann in zwei
Betriebsarten laufen:

  "datei" - liest einen Export (CSV/XLSX), den jemand von Hand gezogen hat
  "api"   - holt die Daten direkt ueber die Schnittstelle

Das OS startet in der Betriebsart "datei", weil dafuer keine Zugangsdaten noetig
sind. Sobald ein Token hinterlegt ist, schaltet dieselbe Klasse auf "api" um -
alles dahinter bleibt gleich.
"""
