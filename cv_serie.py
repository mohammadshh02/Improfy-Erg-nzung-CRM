# -*- coding: utf-8 -*-
"""Serienlauf: jeder Kunde durch jede Vorlage – und nachsehen, ob es hält.

    python -X utf8 cv_serie.py              alle Kunden mit Daten, alle Vorlagen
    python -X utf8 cv_serie.py --pdf        zusätzlich alle PDFs ablegen
    python -X utf8 cv_serie.py --grenze 5   nur die ersten fünf Kunden

**Warum das über einen einzelnen Testfall hinausgeht.** Ein Beispiel sagt nichts. Echte
Kundendaten sind schief: Namen mit Gleichheitszeichen („Jan = Jan Wendisch", erfunden), fehlende
Geburtsdaten, Kurzprofile mit drei Wörtern, Lebenslauftexte über 3.000 Zeichen, Menschen
ohne jede Berufserfahrung. Eine Vorlage, die mit einem Datensatz gut aussieht, fällt beim
nächsten auseinander – und das merkt man erst, wenn sie beim Arbeitgeber liegt.

Der Lauf geht deshalb **den echten Weg**: Vorbelegung aus dem Bestand, alten Text
auslesen, Formular füllen, PDF bauen. Genau das, was ein Coach im Browser tut.

**Geprüft wird an jedem erzeugten PDF:**

  Verlust     Kommt jeder erkannte Eintrag im PDF-Text wieder vor? Was fehlt, ist
              unterwegs abgeschnitten worden – der schlimmste Fehler.
  Seiten      Mehr als vier Seiten deutet auf einen Umbruchfehler hin, null auf einen
              Absturz.
  Kopf        Stehen Name und mindestens eine Kontaktangabe drauf? Ein Lebenslauf ohne
              Telefonnummer ist wertlos.
  Leere       Wie viel Text steht auf der letzten Seite? Unter 300 Zeichen ist die Seite
              fast leer – dann lohnt ein Blick, ob sie nötig war.

Läuft gegen eine Kopie der Datenbank. Am Echtbestand ändert sich nichts.
"""
import os
import re
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import pruefkopie                # noqa: E402
# Warum die Arbeitskopie über `sqlite3.backup` läuft und nicht über `shutil.copy`,
# steht im Kopf von `pruefkopie.py`. Am Echtbestand ändert der Lauf nichts.
KOPIE = pruefkopie.anlegen("improfy_cv_serie.db")
os.environ["IMPROFY_OS_DB"] = KOPIE

import app as A                     # noqa: E402
import cv_pdf                       # noqa: E402
import datenbank as db              # noqa: E402
import lebenslauf_bauen as LB       # noqa: E402

AUSGABE = os.path.join(HIER, "ausgabe", "serie")
MAX_SEITEN = 4
MIN_LETZTE_SEITE = 300      # Zeichen; darunter gilt eine Seite als fast leer


def kunden_mit_daten(grenze=None):
    """Alle, für die sich ein Lebenslauf überhaupt lohnt: laufende Maßnahme oder ein Text."""
    zeilen = db.hole(
        "SELECT k.id, k.name, k.status_code FROM kunde k"
        "  LEFT JOIN kunde_profil p ON p.kunde_id=k.id"
        " WHERE k.standort=? AND (k.status_code IN ('H','I')"
        "    OR (p.cv_text IS NOT NULL AND p.cv_text <> '')"
        "    OR (p.kurzprofil IS NOT NULL AND p.kurzprofil <> ''))"
        " ORDER BY k.name", (db.STANDORT_STANDARD,))
    return zeilen[:grenze] if grenze else zeilen


def formular_fuellen(c, kid, v):
    """Den Weg des Coaches gehen: alten Text auslesen, Felder übernehmen."""
    quelle = v["cv_text"] or v["kurzprofil"] or ""
    t = c.post(f"/kunde/{kid}/lebenslauf/lesen", data={"rohtext": quelle}).get_data(as_text=True)
    form = {}
    for name, wert in re.findall(r'name="([a-z_0-9]+)" value="([^"]*)"', t):
        form.setdefault(name, []).append(wert)
    for name, inhalt in re.findall(r'name="(beruf_taet|ueber_mich|zusatzqual)"[^>]*>([^<]*)', t):
        form.setdefault(name, []).append(inhalt)
    form.pop("q", None)
    # Ohne Über-mich-Text bleibt die erste Seite leer – dann nimmt der Lauf das Kurzprofil,
    # so wie es der Knopf im Formular anbietet.
    if not any((x or "").strip() for x in form.get("ueber_mich", [])) and v["kurzprofil"]:
        form["ueber_mich"] = [f"Guten Tag,\n\nmein Name ist {v['daten']['vorname']} "
                              f"{v['daten']['nachname']}. {v['kurzprofil']}"]
    return form


def _pdf_lesen(rohdaten):
    import pymupdf
    with pymupdf.open(stream=rohdaten, filetype="pdf") as doc:
        seiten = [s.get_text("text") for s in doc]
    return seiten


def pruefe_pdf(rohdaten, form, v):
    """Was ist mit dem fertigen PDF? Gibt eine Liste von Beanstandungen zurück."""
    mangel = []
    try:
        seiten = _pdf_lesen(rohdaten)
    except Exception as e:
        return [f"PDF unlesbar ({e})"], 0
    if not seiten:
        return ["PDF ohne Seiten"], 0
    ganz = " ".join(" ".join(s.replace("-\n", "-").split()) for s in seiten).casefold()

    nachname = (v["daten"]["nachname"] or "").split()[0].casefold() if v["daten"]["nachname"] else ""
    if nachname and nachname not in ganz:
        mangel.append("Name fehlt")
    kontakt = [(form.get(f) or [""])[0] for f in ("mobil", "email")]
    if not any(k and " ".join(k.split()).casefold() in ganz for k in kontakt if k):
        mangel.append("keine Kontaktangabe")

    # Jeder Eintrag, den das Formular trägt, muss im PDF wieder auftauchen
    for feld, art in (("beruf_firma", "Station"), ("bild_abschluss", "Bildung"),
                      ("spr_sprache", "Sprache"), ("ss_eigenschaft", "Stärke"),
                      ("edv_programm", "EDV")):
        for wert in form.get(feld, []):
            wort = (wert or "").split()[0] if wert else ""
            if wort and len(wort) > 2 and wort.casefold() not in ganz:
                mangel.append(f"{art} {wort} fehlt")
    if len(seiten) > MAX_SEITEN:
        mangel.append(f"{len(seiten)} Seiten")
    if len(seiten) > 1 and len(" ".join(seiten[-1].split())) < MIN_LETZTE_SEITE:
        mangel.append("letzte Seite fast leer")
    return mangel, len(seiten)


def main(grenze=None, pdfs=False):
    if not cv_pdf.bereit():
        print("Kein Chrome gefunden – ohne Browser kein PDF-Druck.")
        return 1
    c = A.app.test_client()
    leute = kunden_mit_daten(grenze)
    vorlagen = [s for s, _, _ in cv_pdf.DESIGNS]
    print(f"Serienlauf · {len(leute)} Kunden × {len(vorlagen)} Vorlagen "
          f"= {len(leute) * len(vorlagen)} Lebensläufe\n")
    if pdfs:
        os.makedirs(AUSGABE, exist_ok=True)

    schlecht, gesamt, ohne_inhalt = [], 0, []
    print(f"  {'Kunde':26} {'Vorlage':9} {'Seiten':>6} {'kB':>4}  Beanstandung")
    for k in leute:
        v = LB.vorbelegung(k["id"])
        if not v:
            continue
        form = formular_fuellen(c, k["id"], v)
        # Sprachen zaehlen nicht als Inhalt: Deutsch steht bei jedem, auch bei dem, ueber
        # den das System sonst nichts weiss. Gezaehlt wird, was einen Lebenslauf traegt.
        inhalt = sum(len([x for x in form.get(f, []) if x])
                     for f in ("beruf_firma", "bild_abschluss"))
        lang = len((form.get("ueber_mich") or [""])[0])
        if inhalt == 0 and lang < 150:
            ohne_inhalt.append(k["name"])
        for vorlage in vorlagen:
            r = c.post(f"/kunde/{k['id']}/lebenslauf/pdf", data=dict(form, design=vorlage))
            gesamt += 1
            if r.status_code != 200:
                schlecht.append((k["name"], vorlage, f"Status {r.status_code}"))
                print(f"  {k['name'][:25]:26} {vorlage:9} {'—':>6} {'—':>4}  Status {r.status_code}")
                continue
            mangel, seiten = pruefe_pdf(r.data, form, v)
            if pdfs:
                sicher = re.sub(r"[^\w-]+", "_", k["name"])[:40]
                with open(os.path.join(AUSGABE, f"{sicher}_{vorlage}.pdf"), "wb") as f:
                    f.write(r.data)
            if mangel:
                schlecht.append((k["name"], vorlage, "; ".join(mangel[:3])))
                print(f"  {k['name'][:25]:26} {vorlage:9} {seiten:>6} {len(r.data)//1024:>4}"
                      f"  {'; '.join(mangel[:3])}")

    print(f"\n{gesamt - len(schlecht)} von {gesamt} Lebensläufen ohne Beanstandung.")
    if ohne_inhalt:
        print()
        print(f"{len(ohne_inhalt)} von {len(leute)} Kunden haben nichts zu setzen -"
              f" weder eine Station noch einen Abschluss noch ein Kurzprofil:")
        print("  " + ", ".join(ohne_inhalt[:14])
              + (" ..." if len(ohne_inhalt) > 14 else ""))
        print()
        print("  Das ist kein Fehler der Vorlagen, sondern eine Luecke im Bestand.")
        print("  Der Lebenslauf besteht dann nur aus Name, Kontakt und dem Improfy-Block.")
        print("  Sobald der alte Lebenslauf dieser Menschen abgelegt ist, fuellt er sich.")
    if pdfs:
        print(f"\nPDFs: {AUSGABE}")
    return 0 if not schlecht else 1


if __name__ == "__main__":
    grenze = None
    if "--grenze" in sys.argv:
        grenze = int(sys.argv[sys.argv.index("--grenze") + 1])
    sys.exit(main(grenze, "--pdf" in sys.argv))
