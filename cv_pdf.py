# -*- coding: utf-8 -*-
"""Designter Lebenslauf als PDF – aus denselben Daten wie die Excel-Vorlage.

Die fünf Design-Vorlagen stammen aus der CV-App (`templates/cv_designs/`, Herkunft in
`cv/HERKUNFT.txt`). Sie sind reines HTML mit den Improfy-Farben aus Figma; gedruckt wird
über Chrome im Hintergrund. Kein Schlüssel, keine KI, kein Internet nötig – nur ein
installierter Chrome oder Edge.

Bewusst **nicht** übernommen: die KI-Designs der CV-App (freier Stil, Nachbau einer
Galerie-Vorlage). Die brauchen einen Anthropic-Schlüssel und prüfen jedes Ergebnis noch
einmal nach; das gehört in die eigenständige App. Die Galerie-Bilder liegen als Vorschau
in `cv/galerie/`, damit man sieht, welche Vorlagen es dort gibt.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
AUSGABE = os.path.join(HIER, "ausgabe", "lebenslaeufe")

# Die festen Vorlagen. Reihenfolge ist die Reihenfolge in der Auswahl.
# Die Vorlagen der Sammlung, aus der die Coaches beim Designer nach Nummer bestellen
# („Für Frau Rezai CV Nummer 6 bitte"). Beschrieben sind sie in `cv_sammlung.py`,
# gesetzt werden alle vom selben Gerüst `cv_designs/cv_skin.html`.
import cv_sammlung

DESIGNS = cv_sammlung.designs()
# Alle Nummern laufen über dasselbe Gerüst; die Unterschiede stehen im Skin.
DESIGN_DATEI = {schluessel: "cv_designs/cv_skin.html" for schluessel, _, _ in DESIGNS}

SPRACH_STUFEN = {5: "Muttersprache", 4: "Fließend", 3: "Gute Kenntnisse",
                 2: "Grundkenntnisse", 1: "Grundkenntnisse"}
PLATZHALTER_FOTO = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' "
                    "width='150' height='184'><rect width='100%25' height='100%25' fill='%23eef1f4'/></svg>")


def finde_chrome():
    """Chrome, Chromium oder Edge – in dieser Reihenfolge, auf allen drei Systemen."""
    for name in ("chrome", "google-chrome-stable", "google-chrome", "chromium",
                 "chromium-browser", "msedge"):
        pfad = shutil.which(name)
        if pfad:
            return pfad
    kandidaten = [
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        # Linux
        "/usr/bin/google-chrome", "/usr/bin/chromium",
    ]
    for pfad in kandidaten:
        if pfad and os.path.exists(pfad):
            return pfad
    return None


CHROME = finde_chrome()


def bereit():
    return bool(CHROME)


def galerie():
    """Die Design-Galerie der CV-App als Vorschau – dort entstehen die KI-Nachbauten."""
    try:
        with open(os.path.join(HIER, "cv", "galerie", "manifest.json"), encoding="utf-8") as f:
            eintraege = json.load(f)
    except Exception:
        return []
    vorhanden = []
    for e in eintraege:
        bild = os.path.join(HIER, "cv", "galerie", e.get("thumb", ""))
        if os.path.exists(bild):
            vorhanden.append(e)
    return vorhanden


def _logo_uri():
    for name in ("improfy-logo-cv.png", "improfy-logo.png"):
        pfad = os.path.join(HIER, "static", name)
        if os.path.exists(pfad):
            with open(pfad, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    return ""


def foto_uri(rohdaten, mimetype=None):
    """Bewerbungsfoto auf Druckgröße bringen und einbetten.

    Ein Handy-Foto in voller Auflösung bläht das PDF auf mehrere Megabyte auf und lässt
    sich kaum per Mail verschicken. Gedruckt wird es etwa 45 mm breit; 1000 Pixel lange
    Kante sind dafür reichlich."""
    if not rohdaten:
        return None
    try:
        import io
        from PIL import Image, ImageOps
        bild = Image.open(io.BytesIO(rohdaten))
        bild.load()
        bild = ImageOps.exif_transpose(bild)      # Drehung aus den EXIF-Daten übernehmen
        bild.thumbnail((1000, 1000))
        if bild.mode not in ("RGB", "L"):
            bild = bild.convert("RGB")
        puffer = io.BytesIO()
        bild.save(puffer, format="JPEG", quality=86, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(puffer.getvalue()).decode("ascii")
    except Exception:
        # Ohne Pillow oder bei einem unlesbaren Bild lieber das Original einbetten
        # als den ganzen Lebenslauf scheitern lassen.
        return (f"data:{mimetype or 'image/jpeg'};base64,"
                + base64.b64encode(rohdaten).decode("ascii"))


def _niveau_txt(sterne):
    return {5: "Sehr gute Kenntnisse", 4: "Gute Kenntnisse", 3: "Grundkenntnisse",
            2: "Grundkenntnisse", 1: "Grundkenntnisse"}.get(int(sterne or 0), "Gute Kenntnisse")


def _sprachen_lesbar(daten):
    """Ein Niveau als Zahl würde im fertigen Lebenslauf als nackte 5 beim Arbeitgeber landen."""
    for eintrag in daten.get("sprachen") or []:
        wert = str(eintrag.get("niveau", "")).strip()
        if wert.isdigit():
            eintrag["niveau"] = SPRACH_STUFEN.get(int(wert), wert)
    return daten


def improfy_block(daten):
    """Die Teilnahme bei Improfy steht in jedem Lebenslauf als jüngste Station.

    Mit dem echten Zeitraum der Maßnahme, wenn er bekannt ist – der Designer schreibt
    ihn so („18.05.2026 - 23.08.2026"), nicht als „aktuell"."""
    geschlecht = (daten.get("geschlecht") or "").strip().lower()
    return {"zeitraum": (daten.get("massnahme_zeitraum") or "").strip() or "aktuell",
            "jobtitel": "Teilnehmerin" if geschlecht == "w" else "Teilnehmer",
            "firma": "Improfy GmbH, Köln",
            "taetigkeiten": ["Bewerbungsvorbereitung", "Training Vorstellungsgespräche",
                             "Aktive Kontaktaufnahme mit Unternehmen"]}


def html_bauen(render, daten, design="nr25", foto=None):
    """Design-Vorlage mit den Daten füllen. `render` ist Flasks render_template."""
    daten = _sprachen_lesbar(dict(daten))
    beruf = [improfy_block(daten)] + list(daten.get("berufserfahrung") or [])
    # Bewusst **ohne** Logo des Trägers: Das Papier gehört dem Bewerber. Ein Logo oben
    # rechts sagt dem Recruiter „Maßnahme" statt „Kandidat" – genau die Schublade, in
    # die niemand will. `zeige_foto` sagt der Vorlage, ob überhaupt eines da ist; ein
    # grauer Platzhalter sieht schlechter aus als gar kein Foto.
    return render(DESIGN_DATEI.get(design, "cv_designs/cv_skin.html"),
                  d=daten, beruf=beruf, foto=foto or PLATZHALTER_FOTO,
                  zeige_foto=bool(foto), niveau=_niveau_txt, logo="",
                  skin=cv_sammlung.skin(design))


def pdf_aus_html(html, zeit=90):
    """HTML über Chrome im Hintergrund nach PDF drucken."""
    if not CHROME:
        raise RuntimeError("Kein Chrome oder Edge gefunden – ohne Browser kein PDF-Druck.")
    ordner = tempfile.mkdtemp()
    html_pfad = os.path.join(ordner, "cv.html")
    pdf_pfad = os.path.join(ordner, "cv.pdf")
    try:
        with open(html_pfad, "w", encoding="utf-8") as f:
            f.write(html)
        lauf = subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
             "--print-to-pdf-no-header", f"--print-to-pdf={pdf_pfad}",
             "file:///" + html_pfad.replace("\\", "/")],
            capture_output=True, timeout=zeit)
        if not os.path.exists(pdf_pfad):
            meldung = (lauf.stderr or b"").decode("utf-8", "replace")[-300:]
            raise RuntimeError(f"Chrome hat kein PDF erzeugt. {meldung}")
        with open(pdf_pfad, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(ordner, ignore_errors=True)


def bauen(render, daten, design="nr25", foto=None, dateiname=None):
    """Designtes PDF erzeugen und in ausgabe/lebenslaeufe/ ablegen."""
    pdf = pdf_aus_html(html_bauen(render, daten, design, foto))
    os.makedirs(AUSGABE, exist_ok=True)
    name = dateiname or "Lebenslauf.pdf"
    pfad = os.path.join(AUSGABE, name)
    with open(pfad, "wb") as f:
        f.write(pdf)
    return pdf, name, pfad
