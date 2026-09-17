# -*- coding: utf-8 -*-
"""Die Vorlagensammlung – 23 Nummern, ein Gerüst.

**Warum nicht 23 einzelne Dateien.** Die Vorlagen unterscheiden sich in Farbe, Kopfform,
Fotoplatz und der Art, wie Fähigkeiten dargestellt werden – nicht im Aufbau. Wer sie
einzeln schreibt, pflegt 23-mal dieselben Umbruchregeln und hat nach dem dritten Fehler
23 verschiedene Zustände. Hier beschreibt jede Nummer nur, **worin sie sich unterscheidet**;
Satz, Umbruch und Maß kommen aus dem gemeinsamen Gerüst (`templates/cv_designs/cv_skin.html`).

Die Farben sind aus den Vorschaubildern der Sammlung (`cv/galerie/`) ausgelesen, nicht
geraten – Pixel gezählt und die beiden kräftigsten Töne genommen.

**Ehrlich zur Genauigkeit:** Ohne die Quelldateien des Designers lässt sich eine Vorlage
nicht auf den Millimeter nachbauen. Freigestellte Kleckse, Farbverläufe und angeschnittene
Formen sind angenähert. Farbe, Aufbau, Kopfform und Charakter stimmen; die Handschrift
bleibt erkennbar.

**Kopfformen:**
  band      farbiges Band über die volle Breite, Name darin
  links     farbige Spalte am linken Rand, Inhalt rechts daneben
  ecke      farbige Form in der oberen Ecke, Name daneben
  flaeche   ganze Seite zart eingefärbt, Name auf der Fläche
  rahmen    dünner Rahmen um das Blatt, Name mittig
  schlicht  weiß, nur eine Akzentmarke am Namen

**Bewertungen:** sterne · punkte · balken · text (ein Niveau statt einer Zahl)
"""

# (Kennung, Nummer, Bezeichnung, Kopfform, Farbe, Zweitfarbe, Bewertung, Beschreibung)
SAMMLUNG = [
    ("nr25", 25, "Improfy-Standard", "band", "#8cc63f", "#a5d65e", "sterne",
     "Grüner Kopf mit Sprechblase, Foto links – die Vorlage, die Atanas zuletzt für Köln setzte"),
    ("nr2", 2, "Sonne", "ecke", "#f0a830", "#2f6f8f", "sterne",
     "Warme Form in der oberen Ecke, Name groß daneben"),
    ("nr3", 3, "Grafit", "band", "#303030", "#9a9a9a", "punkte",
     "Dunkelgraues Band, Name in Konturschrift"),
    ("nr3v2", "3.2", "Grafit Teal", "links", "#1c1c1c", "#2aa8a8", "punkte",
     "Schwarze Spalte links, türkise Akzente"),
    ("nr5", 5, "Signal", "schlicht", "#0078c0", "#2f6f8f", "punkte",
     "Weiß mit blauer Signalmarke am Namen – sehr aufgeräumt"),
    ("nr6", 6, "Rosé", "links", "#cf8f8f", "#e3c4c4", "balken",
     "Roséfarbene Spalte, zurückhaltend und freundlich"),
    ("nr7", 7, "Kontrast", "band", "#f0a800", "#181830", "punkte",
     "Gelbes Band über tiefem Nachtblau – der stärkste Kontrast der Sammlung"),
    ("nr8", 8, "Salbei", "links", "#4a6060", "#a8a878", "balken",
     "Gedeckte Spalte in Salbei und Sand, Name in Gold"),
    ("nr9", 9, "Nacht", "links", "#181818", "#00a8d8", "punkte",
     "Schwarze Spalte, cyanfarbener Block am Kopf"),
    ("nr10", 10, "Magenta", "ecke", "#d83060", "#f0a8c0", "sterne",
     "Magenta-Bogen in der Ecke, Name schlicht daneben"),
    ("nr11", 11, "Petrol", "band", "#304848", "#30a8a8", "balken",
     "Dunkles Petrolband mit türkisem Streifen"),
    ("nr12", 12, "Sand", "flaeche", "#8a7551", "#f3efe7", "balken",
     "Warmer Papierton, Name mittig, Bronzelinien – die ruhigste der Sammlung"),
    ("nr13", 13, "Bogen", "rahmen", "#7fa8a8", "#dfe8e8", "text",
     "Feiner Rahmen mit geschwungener Oberkante"),
    ("nr14", 14, "Rosenquarz", "ecke", "#e0609c", "#f7c4da", "sterne",
     "Geometrische Formen in Rosa, Name in Versalien"),
    ("nr15", 15, "Tiefsee", "links", "#003060", "#00a8d8", "balken",
     "Blaue Blockstufen in der linken Spalte"),
    ("nr16", 16, "Jade", "links", "#181818", "#48c0a8", "punkte",
     "Schwarze Spalte mit jadegrünem Kopfblock"),
    ("nr17", 17, "Apricot", "ecke", "#d8927a", "#f0cdbd", "text",
     "Weiche Apricotform, Name in derselben Farbe"),
    ("nr18", 18, "Himmel", "flaeche", "#3d6f9e", "#d7e5f3", "text",
     "Zart hellblaue Seite, Name mittig"),
    ("nr18v2", "18.2", "Himmel Tief", "links", "#003060", "#0060a8", "balken",
     "Dunkelblaue Randspalte mit abgestuften Blöcken"),
    ("nr19", 19, "Marine", "band", "#17335c", "#21446f", "punkte",
     "Marineblaues Band über die volle Breite, Foto überlappt die Kante"),
    ("nr21", 21, "Flieder", "flaeche", "#6d5b86", "#cdc0dc", "sterne",
     "Fliederfarbene Seite, Name mittig in Versalien"),
    ("nr27", "27.1", "Atlantik", "links", "#00456e", "#c3d9dd", "punkte",
     "Tiefblaue Randspalte, heller Inhalt daneben"),
    ("nr1", 1, "Blanko", "schlicht", "#4a4a4a", "#9a9a9a", "text",
     "Ganz ohne Farbe – für Arbeitgeber, die nichts als Text sehen wollen"),
]

# Das Vorschaubild des Designers zu jeder Nummer. Die Namen folgen einer Regel
# (nr7 -> cv7_thumb.png); nr27 heißt in der Sammlung „27.1" und tanzt aus der Reihe.
SONDERBILD = {"nr27": "cv27_1_thumb.png"}


def _bild(kennung):
    """Das Vorschaubild des Designers – das Original, zum Vergleich."""
    return SONDERBILD.get(kennung, "cv" + kennung[2:] + "_thumb.png")


def _eigen(kennung):
    """Die eigene Vorschau: eine echt gebaute Seite. Was hier steht, kommt auch heraus.

    Erzeugt von `vorschau_bauen.py`. Fehlt die Datei, zeigt die Auswahl ersatzweise das
    Original des Designers – lieber ein ähnliches Bild als ein leeres Feld."""
    return "vorlagen/%s.png" % kennung


NACH_KENNUNG = {k: dict(zip(
    ("kennung", "nummer", "name", "kopfform", "farbe", "farbe2", "bewertung", "text"), z),
    bild=_bild(z[0]), eigen=_eigen(z[0]))
    for z in SAMMLUNG for k in (z[0],)}


def designs():
    """Die Liste in der Form, die `cv_pdf.DESIGNS` erwartet."""
    return [(k["kennung"], f"Nr. {k['nummer']} · {k['name']}", k["text"])
            for k in NACH_KENNUNG.values()]


def alle():
    """Alle Nummern mit allem, was die Auswahl im Formular braucht – samt Vorschaubild."""
    return list(NACH_KENNUNG.values())


def skin(kennung):
    return NACH_KENNUNG.get(kennung) or NACH_KENNUNG["nr25"]
