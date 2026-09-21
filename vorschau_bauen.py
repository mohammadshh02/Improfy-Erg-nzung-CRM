# -*- coding: utf-8 -*-
"""Vorschaubilder der Vorlagen erzeugen.

    python -X utf8 vorschau_bauen.py

**Warum nicht die Bilder des Designers nehmen.** Die liegen in `cv/galerie/` und sehen gut
aus – aber gedruckt wird der Nachbau, nicht das Original. Wer nach dem Originalbild wählt,
bekommt etwas leicht anderes in die Hand. Die Auswahl zeigt deshalb, was wirklich
herauskommt: jede Vorlage einmal gebaut und abfotografiert.

**Die Musterdaten sind frei erfunden.** Die Bilder liegen in `static/` und gehen mit ins
Repository – dort hat kein Kundenname und keine echte Telefonnummer etwas verloren.

Nach jeder Änderung an einer Vorlage neu laufen lassen, sonst zeigt die Auswahl einen
alten Stand.
"""
import io
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
ZIEL = os.path.join(HIER, "static", "vorlagen")

# Frei erfunden: Name, Nummer und Anschrift gibt es so nicht.
#
# **Die Form muss der echten entsprechen**, nicht nur der Inhalt: Was hier steht, geht
# durch dieselbe Vorlage wie die Daten eines Kunden (`lebenslauf_bauen.aus_formular`).
# Drei Abweichungen haben die Vorschaubilder still verfälscht:
#   * `zusatzqualifikationen` war ein String - die Vorlage lief darüber Buchstabe für
#     Buchstabe und setzte „S", „t", „a", „p" als eigene Aufzählungspunkte.
#   * `fuehrerschein` war ein String - abgefragt wird `fuehrerschein.vorhanden`, und das
#     gibt es an einem String nicht: Der Führerschein fiel aus dem Bild.
#   * `bildung[].einrichtung` heißt echt `institution` - die Schulen standen ohne
#     Einrichtung da.
MUSTER = {
    "vorname": "Maria", "nachname": "Musterfrau",
    "angestrebter_job": "Fachkraft für Lagerlogistik",
    "geschlecht": "w", "geburtsdatum": "01.01.1990",
    "mobil": "+49 000 0000000", "email": "maria.musterfrau@beispiel.de",
    "adresse": "Musterweg 1, 00000 Musterstadt",
    "massnahme_zeitraum": "01.01.2026 - 31.03.2026",
    "fuehrerschein": {"vorhanden": True, "klasse": "B", "eu": True},
    "ueber_mich": ("Guten Tag,\n\nmein Name ist Maria Musterfrau. Nach meiner Ausbildung war "
                   "ich über zwölf Jahre in der Lagerlogistik tätig, zuletzt als "
                   "Schichtverantwortliche für ein Team von acht Personen.\n\n"
                   "Ich arbeite sorgfältig, bin körperlich belastbar und gewohnt, im "
                   "Schichtbetrieb zu arbeiten. Der Umgang mit Handscanner und "
                   "Lagerverwaltungssoftware ist mir vertraut."),
    "berufserfahrung": [
        {"zeitraum": "01.2010 - 12.2020", "jobtitel": "Lagerhelferin",
         "firma": "Musterlogistik GmbH",
         "taetigkeiten": ["Warenannahme und Kontrolle der Lieferscheine",
                          "Kommissionierung mit dem Handscanner"]},
        {"zeitraum": "02.2011 - 12.2021", "jobtitel": "Kommissioniererin",
         "firma": "Beispielmarkt GmbH",
         "taetigkeiten": ["Warenannahme und Kontrolle der Lieferscheine",
                          "Einweisung neuer Kolleginnen und Kollegen"]},
        {"zeitraum": "03.2012 - 12.2022", "jobtitel": "Produktionshelferin",
         "firma": "Musterwerk AG",
         "taetigkeiten": ["Einhaltung der Vorgaben zu Arbeitssicherheit und Hygiene"]},
    ],
    "bildung": [
        {"zeitraum": "2006 - 2009", "abschluss": "Ausbildung Fachkraft für Lagerlogistik",
         "institution": "Musterberufsschule", "note": ""},
        {"zeitraum": "2002 - 2006", "abschluss": "Mittlere Reife",
         "institution": "Musterschule", "note": ""},
    ],
    "zusatzqualifikationen": ["Staplerschein", "Erste-Hilfe-Kurs"],
    "sprachen": [{"sprache": "DEUTSCH", "niveau": "Muttersprache"},
                 {"sprache": "ENGLISCH", "niveau": "gute Kenntnisse"}],
    "edv_kenntnisse": [{"programm": "Lagerverwaltung", "sterne": 5},
                       {"programm": "Excel", "sterne": 4}],
    "soft_skills": [{"eigenschaft": "Zuverlässigkeit", "sterne": 5},
                    {"eigenschaft": "Teamfähigkeit", "sterne": 5},
                    {"eigenschaft": "Belastbarkeit", "sterne": 4}],
    "hobbys": "Laufen, Kochen",
}


def _platzhalterfoto():
    """Eine gezeichnete Silhouette – kein Mensch, kein Bild von der Festplatte."""
    from PIL import Image, ImageDraw
    b = Image.new("RGB", (900, 1150), (233, 235, 237))
    z = ImageDraw.Draw(b)
    z.ellipse((310, 200, 590, 480), fill=(203, 176, 148))
    z.rounded_rectangle((240, 495, 660, 1150), 46, fill=(44, 60, 82))
    puffer = io.BytesIO()
    b.save(puffer, "JPEG", quality=90)
    return puffer.getvalue()


def main(breite=440):
    import pymupdf
    from PIL import Image
    from flask import render_template
    import app as A
    import cv_pdf
    import cv_sammlung

    if not cv_pdf.bereit():
        print("Kein Chrome gefunden – ohne Browser kein PDF und damit keine Vorschau.")
        return 1
    os.makedirs(ZIEL, exist_ok=True)
    foto = cv_pdf.foto_uri(_platzhalterfoto(), "image/jpeg")

    for k in cv_sammlung.alle():
        with A.app.test_request_context():
            roh = cv_pdf.pdf_aus_html(
                cv_pdf.html_bauen(render_template, dict(MUSTER), k["kennung"], foto))
        with pymupdf.open(stream=roh, filetype="pdf") as doc:
            seite = doc[0]
            zoom = breite / seite.rect.width
            p = seite.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        bild = Image.frombytes("RGB", (p.width, p.height), p.samples)
        pfad = os.path.join(ZIEL, f"{k['kennung']}.png")
        bild.save(pfad, optimize=True)
        print(f"  Nr. {str(k['nummer']):5} {k['name']:18} {bild.width}×{bild.height} "
              f"{os.path.getsize(pfad) // 1024} kB")
    print(f"\n{len(cv_sammlung.alle())} Vorschaubilder in {ZIEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
