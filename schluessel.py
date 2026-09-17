#!/usr/bin/env python3
"""
Schlüssel in der .env setzen
============================
Ersetzt genau eine Zeile in `improfy_os/.env`, ohne dass jemand die Datei von
Hand bearbeiten muss — im Editor ist schnell ein Leerzeichen oder Umbruch drin,
und dann findet die App den Schlüssel nicht mehr.

    python3 schluessel.py                  # zeigen, was gesetzt ist
    python3 schluessel.py HUBSPOT_TOKEN    # Wert neu setzen (Eingabe unsichtbar)

Die Eingabe wird nicht angezeigt und landet nicht in der Shell-History.
Danach den Import laufen lassen:  python3 importieren.py hubspot
"""
import getpass
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HIER, ".env")

BEKANNT = {
    "HUBSPOT_TOKEN": ("HubSpot Private App, Scope crm.objects.contacts.read", "pat-"),
    "KOMMO_TOKEN": ("Kommo Long-lived Token", ""),
    "KOMMO_BASIS": ("Kommo-Adresse, z. B. improfyhamburg.kommo.com", ""),
    "IMPROFY_OS_PASSWORT": ("Passwort fürs Dashboard", ""),
    "IMPROFY_OS_SECRET": ("Signierschlüssel für Sitzungen", ""),
    "IMPROFY_DRIVE": ("Pfad zum lokalen Drive-Ordner", ""),
}


def lies():
    """.env als Liste von Zeilen — Kommentare und Reihenfolge bleiben erhalten."""
    try:
        with open(ENV, encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return []


def schreib(zeilen):
    """Atomar und mit engen Rechten, damit nur die App den Schlüssel lesen kann."""
    tmp = ENV + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n")
    os.replace(tmp, ENV)


def zeigen():
    zeilen = lies()
    if not zeilen:
        print(f"Keine .env gefunden unter {ENV}")
        return
    print(f"{ENV}\n")
    gesetzt = set()
    for zeile in zeilen:
        if zeile.strip().startswith("#") or "=" not in zeile:
            continue
        name, wert = zeile.split("=", 1)
        name, wert = name.strip(), wert.strip()
        gesetzt.add(name)
        beschreibung = BEKANNT.get(name, ("", ""))[0]
        # Nie den ganzen Wert zeigen - nur Anfang und Ende zur Wiedererkennung.
        kurz = f"{wert[:8]}…{wert[-4:]}" if len(wert) > 16 else "(kurz)"
        print(f"  {name:<20} {kurz:<18} {len(wert):>4} Zeichen   {beschreibung}")
    for name, (beschreibung, _) in BEKANNT.items():
        if name not in gesetzt:
            print(f"  {name:<20} {'— nicht gesetzt':<18} {'':>4}            {beschreibung}")
    print("\nWert ändern:  python3 schluessel.py HUBSPOT_TOKEN")


def setzen(name):
    beschreibung, praefix = BEKANNT.get(name, ("", ""))
    print(f"{name}" + (f" — {beschreibung}" if beschreibung else ""))
    wert = getpass.getpass("Wert einfügen (bleibt unsichtbar): ").strip()

    if not wert:
        print("Abgebrochen, nichts geändert.")
        return 1
    if praefix and not wert.startswith(praefix):
        print(f"Das sieht nicht aus wie ein {name} — erwartet wird ein Anfang mit "
              f"'{praefix}'. Nichts geändert.")
        return 1
    if any(z in wert for z in " \t\n\""):
        print("Der Wert enthält Leerzeichen oder Anführungszeichen. Vermutlich ist beim "
              "Kopieren etwas mitgerutscht — nichts geändert.")
        return 1

    zeilen, ersetzt = lies(), False
    for i, zeile in enumerate(zeilen):
        if zeile.split("=", 1)[0].strip() == name and not zeile.strip().startswith("#"):
            zeilen[i] = f"{name}={wert}"
            ersetzt = True
            break
    if not ersetzt:
        zeilen.append(f"{name}={wert}")

    schreib(zeilen)
    print(f"✓ {name} gespeichert ({len(wert)} Zeichen)"
          + ("" if ersetzt else " — war vorher nicht gesetzt"))
    print("\nJetzt den Import laufen lassen:")
    print("  python3 importieren.py hubspot")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        zeigen()
        sys.exit(0)
    if sys.argv[1] in ("-h", "--help", "hilfe"):
        print(__doc__)
        sys.exit(0)
    sys.exit(setzen(sys.argv[1].strip().upper()))
