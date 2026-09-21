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
# („Für Frau Popa CV Nummer 6 bitte" – Name erfunden, hier stand eine echte Kundin).
# Beschrieben sind sie in `cv_sammlung.py`,
# gesetzt werden alle vom selben Gerüst `cv_designs/cv_skin.html`.
import cv_sammlung

DESIGNS = cv_sammlung.designs()
# Alle Nummern laufen über dasselbe Gerüst; die Unterschiede stehen im Skin.
DESIGN_DATEI = {schluessel: "cv_designs/cv_skin.html" for schluessel, _, _ in DESIGNS}

SPRACH_STUFEN = {5: "Muttersprache", 4: "Fließend", 3: "Gute Kenntnisse",
                 2: "Grundkenntnisse", 1: "Grundkenntnisse"}
# Einen grauen Platzhalter fuer ein fehlendes Foto gibt es bewusst nicht mehr: Ist kein
# Bild da, bleibt die Stelle leer - das sieht besser aus als ein leerer Kasten.


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


# Ein Traegerlogo bettet hier nichts mehr ein, und das bleibt so: Das Papier gehoert dem
# Bewerber. Ein Logo oben rechts sagt dem Recruiter „Maßnahme" statt „Kandidat" - genau
# die Schublade, in die niemand will. (Ansage Masoud, 17.09.2026; steht als Pruefung im
# Testsatz.)


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


def _ist_improfy(station):
    """Beschreibt diese Station die Teilnahme bei Improfy?

    Gesucht wird im Firmenfeld **und** im Jobtitel: mal steht „Teilnehmer | Improfy
    GmbH, Köln", mal „Teilnehmer Improfy GmbH Köln" in einem Feld, je nachdem, wie der
    Text ausgelesen wurde."""
    zusammen = " ".join(str(station.get(f) or "") for f in ("firma", "jobtitel"))
    return "improfy" in zusammen.lower()


def _ohne_doppelte_massnahme(beruf):
    """Die Improfy-Station genau einmal - die automatische gewinnt.

    Sie trägt den echten Zeitraum aus der Gutscheinliste und die drei Stichpunkte der
    Vorlage; die ausgelesene ist meist eine Zeile ohne Inhalt. Stünden beide da, läse
    der Arbeitgeber dieselbe Maßnahme zweimal untereinander."""
    ergebnis, gesehen = [], False
    for station in beruf:
        if _ist_improfy(station):
            if gesehen:
                continue
            gesehen = True
        ergebnis.append(station)
    return ergebnis


# Ein Foto je Druckseite: Die Bilder stehen nicht mehr gestapelt im Kopf, sondern eines
# auf jeder Seite, immer an derselben Stelle. Die Vorlage braucht dafuer zwei Angaben,
# die sie sich nicht selbst ausrechnen kann - wie viele Seiten es werden (`seiten`) und
# wie weit ein Blatt vorschiebt (`vorschub`).

# Der Seitenvorschub bei `@page { size: A4; margin: 0 0 12mm }`: 297 mm Blatt minus
# 12 mm Fussrand. Ein absolut gesetztes Element mit `top: calc(k * 285mm)` landet damit
# auf Seite k+1 an derselben Hoehe (nachgemessen ueber fuenf Seiten, Restdrift 0,26 mm
# je Seite). Wer den Seitenrand im Rahmen aendert, muss diese Zahl mitaendern.
VORSCHUB = "285mm"


def _bilderliste(bilder=None, foto=None, weitere=None):
    """Die Fotos in Platzreihenfolge, als eine Liste.

    Zwei Schreibweisen fuehren hierher: die neue (`bilder` als Liste) und die alte
    (`foto` plus `weitere` als {platz: datenuri}), die `vorschau_bauen.py` und der
    Selbsttest noch benutzen. Die alten Plaetze heissen `kopf`, `neben1`, `neben2` -
    alphabetisch sortiert ergibt das genau ihre Reihenfolge im Aufbau."""
    if bilder is not None:
        return [b for b in bilder if b]
    liste = [foto] if foto else []
    liste += [wert for _platz, wert in sorted((weitere or {}).items()) if wert]
    return liste


def html_bauen(render, daten, design="nr25", foto=None, weitere=None,
               bilder=None, seiten=1, vorschub=VORSCHUB, einzug=True):
    """Design-Vorlage mit den Daten füllen. `render` ist Flasks render_template.

    `bilder` sind die Bewerbungsfotos als Daten-URIs in Platzreihenfolge, `seiten` die
    Zahl der Druckseiten aus dem ersten Durchgang. Es werden nie mehr Fotos gesetzt als
    Seiten da sind - jedes zusaetzliche verlaengert das Dokument selbst.

    `einzug` haelt den Textkoerper neben der Fotospalte. Er gehoert zu `seiten`: Nur wo
    wirklich auf jeder Seite ein Foto steht, darf auch auf jeder Seite eingerueckt
    werden. Wer die Seitenzahl nicht kennt, setzt ihn ab - sonst bliebe ab Seite 2 eine
    leere 61-mm-Spalte neben dem Text (siehe `_seiten_zaehlen` und `bauen`)."""
    daten = _sprachen_lesbar(dict(daten))
    beruf = _ohne_doppelte_massnahme(
        [improfy_block(daten)] + list(daten.get("berufserfahrung") or []))
    # Ist kein Foto da, bekommt die Vorlage gar keines: ein grauer Platzhalter sieht
    # schlechter aus als gar kein Foto. Ein Trägerlogo geht aus demselben Grund nicht
    # mit (siehe oben) - die Vorlage kennt dafür auch keine Stelle mehr.
    return render(DESIGN_DATEI.get(design, "cv_designs/cv_skin.html"),
                  d=daten, beruf=beruf, niveau=_niveau_txt,
                  bilder=_bilderliste(bilder, foto, weitere),
                  seiten=max(1, int(seiten or 1)), vorschub=vorschub,
                  einzug=bool(einzug), skin=cv_sammlung.skin(design))


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


def _seiten_zaehlen(pdf):
    """Wie viele Seiten hat dieses PDF? `None`, wenn es sich nicht feststellen laesst.

    **Warum nicht einfach 1.** Eine 1 waere eine Antwort, die nach Wissen aussieht: Der
    Aufbau richtet zwei Dinge danach aus - wie viele Fotos gesetzt werden **und** ob der
    Textkoerper einrueckt. Mit einer geratenen 1 bekaeme ein vierseitiger Lebenslauf ein
    Foto auf Seite 1 und trotzdem auf allen vier Seiten den Einzug: Seite 2 bis 4 haetten
    dann eine 61 mm breite leere Spalte neben dem Text. `None` sagt ehrlich „unbekannt",
    und `bauen` zieht daraus den Schluss, der nichts kaputt macht."""
    try:
        import pymupdf
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            return max(1, len(doc))
    except Exception:
        return None


def bauen(render, daten, design="nr25", foto=None, dateiname=None, weitere=None,
          bilder=None):
    """Designtes PDF erzeugen und in ausgabe/lebenslaeufe/ ablegen.

    **Zwei Durchgaenge.** Wie viele Seiten ein Lebenslauf bekommt, weiss erst der Druck.
    Der erste Durchgang setzt deshalb ein Foto und zaehlt die Seiten, der zweite verteilt
    dann eines auf jede Seite.

    Warum die Zahl aus Durchgang 1 stehen bleibt: Die Fotos haengen absolut im Anker und
    nehmen im Satz keinen Platz ein - am Umbruch aendert sich zwischen den Durchgaengen
    nichts. Nachgemessen ueber alle 23 Vorlagen mal drei Faelle (21.09.2026): 69 von 69
    Laeufen gleich lang.

    Und warum nie mehr Fotos als Seiten gesetzt werden duerfen: Ein Foto unterhalb des
    letzten Seitenendes verlaengert das Dokument um genau die Seite, auf der es steht -
    danach fehlt wieder eines, und das Blatt waechst bei jedem Durchgang weiter.

    **Ohne pymupdf**, also ohne Seitenzahl, wird ein zweites Mal gedruckt: ein Foto auf
    Seite 1 und **kein** Einzug. Das ist nicht der Stand vor dem Umbau - vorher standen
    alle hinterlegten Fotos gestapelt im Kopf von Seite 1, jetzt wird das erste gedruckt
    und die uebrigen fallen weg. Es ist der beste Rueckfall, den es ohne Seitenzahl gibt:
    ein Blatt, auf dem nichts leer steht. Die beiden Alternativen waren schlechter:
    Mit Einzug, aber ohne Fotos ab Seite 2 stuende dort eine 61 mm breite leere Spalte;
    ganz ohne Fotos zu drucken nimmt dem Bewerber sein Bild - das Erste, was ein
    Arbeitgeber ansieht -, und niemand wuerde merken, warum.

    Wer das aendern will, braucht pymupdf - ohne Seitenzahl gibt es keinen Weg, mehr als
    ein Foto zu setzen, ohne das Dokument bei jedem Durchgang zu verlaengern."""
    fotos_da = bool(_bilderliste(bilder, foto, weitere))
    pdf = pdf_aus_html(html_bauen(render, daten, design, foto, weitere, bilder, seiten=1))
    if fotos_da:
        seiten = _seiten_zaehlen(pdf)
        if seiten is None:
            pdf = pdf_aus_html(html_bauen(render, daten, design, foto, weitere, bilder,
                                          seiten=1, einzug=False))
        elif seiten > 1:
            pdf = pdf_aus_html(html_bauen(render, daten, design, foto, weitere, bilder,
                                          seiten=seiten))
    os.makedirs(AUSGABE, exist_ok=True)
    name = dateiname or "Lebenslauf.pdf"
    pfad = os.path.join(AUSGABE, name)
    with open(pfad, "wb") as f:
        f.write(pdf)
    return pdf, name, pfad
