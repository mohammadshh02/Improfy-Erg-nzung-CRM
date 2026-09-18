# -*- coding: utf-8 -*-
"""Nachsehen, ob auf einem fertigen PDF etwas übereinanderliegt oder abgeschnitten ist.

**Warum es das gibt.** Drei Fehler hintereinander sind mir durchgegangen und erst dem
Nutzer am Bildschirm aufgefallen: eine Marke lag auf der Telefonnummer, eine Linie strich
das Geburtsdatum durch, eine getönte Fläche lag unter der Anschrift. Alle drei hatten
dieselbe Ursache – und keiner davon wurde von einer Prüfung bemerkt, weil die Prüfungen
nur zählten, *ob* Text im PDF steht, nicht *wo*.

Was hier geprüft wird, ist die Richtlinie, an der sich jede Vorlage messen lassen muss:

  Rand       Kein Text ragt in den Seitenrand. Was dort steht, schneidet der Drucker ab.
  Text       Keine zwei Textblöcke liegen übereinander.
  Formen     Keine farbige Fläche ragt **teilweise** in einen Textblock.

Die dritte Regel braucht eine Unterscheidung, sonst schlägt sie bei jedem farbigen
Kopfband an: Ein Hintergrund umschließt seinen Text **ganz** (weiße Schrift auf grünem
Band). Eine Form, die nur eine Ecke eines Wortes berührt, ist dagegen immer ein Unfall –
genau so lag die Zipfelspitze auf dem „S" von „SCHULBILDUNG".

Benutzung:

    import seitenpruefung
    maengel = seitenpruefung.pruefe(pdf_rohdaten)
"""

RAND_MM = 8.0          # so nah darf Text an die Blattkante; darunter wird es riskant
DECKUNG_MIN = 0.04     # weniger Überlappung als das ist Rundung, kein Fehler
DECKUNG_HINTERGRUND = 0.85   # ab hier gilt eine Form als Hintergrund, nicht als Kollision
TEXT_UEBERLAPP = 0.25  # so viel Text-auf-Text ist sicher keine Absicht

MM = 72.0 / 25.4


def _flaeche(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _schnitt(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _deckung(text_rect, form_rect):
    """Welcher Anteil des Textblocks liegt unter der Form?"""
    f = _flaeche(text_rect)
    return _flaeche(_schnitt(text_rect, form_rect)) / f if f else 0.0


def _textbloecke(seite, fusszeile_ab):
    """Textblöcke mit Inhalt. Die Fußzeile bleibt draußen – sie steht bewusst im Rand."""
    return [(b[:4], " ".join(b[4].split())) for b in seite.get_text("blocks")
            if b[4].strip() and b[1] < fusszeile_ab]


def _spannen(seite, fusszeile_ab):
    """Die Kästen der tatsächlichen Buchstaben, nicht der Absätze.

    Gegen den Absatzkasten zu prüfen erzeugt Fehlalarme: Die feine Führungslinie
    zwischen „PERSISCH" und „fließend" liegt **in** dessen Kasten, aber sauber in der
    Lücke zwischen den Wörtern. Gegen die Buchstabenkästen geprüft, berührt sie nichts.
    Umgekehrt trifft eine Linie, die durch ein Wort läuft, dessen Kasten sofort."""
    ergebnis = []
    for block in seite.get_text("dict")["blocks"]:
        for zeile in block.get("lines", []):
            for spanne in zeile.get("spans", []):
                if spanne["text"].strip() and spanne["bbox"][1] < fusszeile_ab:
                    ergebnis.append((spanne["bbox"], " ".join(spanne["text"].split())))
    return ergebnis


def pruefe(rohdaten, rand_mm=RAND_MM):
    """Alle Mängel eines PDF als Liste von Klartextzeilen. Leere Liste heißt: sauber."""
    import pymupdf

    maengel = []
    with pymupdf.open(stream=rohdaten, filetype="pdf") as doc:
        for nr, seite in enumerate(doc, 1):
            breite, hoehe = seite.rect.width, seite.rect.height
            # Die Seitenzahl steht als Druckerfußzeile im unteren Rand – das ist gewollt.
            fusszeile_ab = hoehe - 10 * MM
            bloecke = _textbloecke(seite, fusszeile_ab)

            # --- Rand ---------------------------------------------------------------
            for rect, text in bloecke:
                if (rect[0] < rand_mm * MM or rect[2] > breite - rand_mm * MM
                        or rect[1] < rand_mm * MM):
                    maengel.append(
                        f"Seite {nr}: Text im Seitenrand – {text[:38]!r}")

            # --- Text auf Text ------------------------------------------------------
            for i, (ra, ta) in enumerate(bloecke):
                for rb, tb in bloecke[i + 1:]:
                    kleiner = min(_flaeche(ra), _flaeche(rb))
                    if not kleiner:
                        continue
                    if _flaeche(_schnitt(ra, rb)) / kleiner > TEXT_UEBERLAPP:
                        maengel.append(
                            f"Seite {nr}: Text liegt auf Text – "
                            f"{ta[:24]!r} und {tb[:24]!r}")

            # --- Formen, die in den Text ragen --------------------------------------
            formen = [f["rect"] for f in seite.get_drawings() if f.get("fill")]
            for rect, text in _spannen(seite, fusszeile_ab):
                for form in formen:
                    d = _deckung(rect, (form.x0, form.y0, form.x1, form.y1))
                    if DECKUNG_MIN < d < DECKUNG_HINTERGRUND:
                        maengel.append(
                            f"Seite {nr}: Fläche ragt in den Text "
                            f"({d:.0%}) – {text[:34]!r}")
                        break
    return maengel
