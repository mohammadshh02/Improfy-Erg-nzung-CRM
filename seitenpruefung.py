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
  Bilder     Genau ein Foto je Seite – höchstens eines, und wo im Dokument Fotos sind,
             auch mindestens eines je Seite. Ganz auf dem Blatt, auf jeder Seite an
             derselben Stelle, und auf keinem Textblock.

Was hier **nicht** geprüft wird: ob der Inhalt auf jeder Seite an derselben Kante
beginnt. Ich habe es versucht – über die Überschriften und über die häufigste Kante –
und beide Male Fehlalarme bekommen: Seite 1 hat mit der Kontaktspalte eine zweite,
gewollte Kante. Eine Prüfung, die grundlos rot wird, erzieht dazu, Rot zu ignorieren.
Die Kante ist stattdessen im Aufbau festgeschrieben (`table-layout: fixed` mit fester
Spaltenbreite) und wird dort geprüft, wo sie entsteht: in `lebenslauf_test.py`.

Die dritte Regel braucht eine Unterscheidung, sonst schlägt sie bei jedem farbigen
Kopfband an: Ein Hintergrund umschließt seinen Text **ganz** (weiße Schrift auf grünem
Band). Eine Form, die nur eine Ecke eines Wortes berührt, ist dagegen immer ein Unfall –
genau so lag die Zipfelspitze auf dem „S" von „SCHULBILDUNG".

**Warum es die vierte Regel gibt (21.09.2026).** Jede Seite trägt jetzt ein Bewerbungs-
foto, und zwar auf jeder Seite an derselben Stelle. Das ist eine Zusage, die man auf dem
Bildschirm kaum nachmisst: Ein Foto, das von Seite zu Seite um zwei Millimeter wandert,
sieht man erst, wenn man die Blätter übereinanderlegt. Dazu kam ein Fehler, der ein
halbes Jahr unbemerkt durchlief – ein Zierkreis der Kopfform `ecke` ragte über die
Blattkante, und Chrome druckte daraufhin **das ganze Dokument** auf 92 % verkleinert.
Keine Prüfung hat das gesehen, weil alle nur nachsahen, ob Text da ist, nicht wie groß.

Benutzung:

    import seitenpruefung
    maengel = seitenpruefung.pruefe(pdf_rohdaten)
"""

RAND_MM = 8.0          # so nah darf Text an die Blattkante; darunter wird es riskant
DECKUNG_MIN = 0.04     # weniger Überlappung als das ist Rundung, kein Fehler
DECKUNG_HINTERGRUND = 0.85   # ab hier gilt eine Form als Hintergrund, nicht als Kollision
TEXT_UEBERLAPP = 0.25  # so viel Text-auf-Text ist sicher keine Absicht
# So weit darf ein Foto von Seite zu Seite wandern – zwei Anteile, und die Unterscheidung
# ist der ganze Punkt:
#
#   FOTO_ORT_MM    der feste Anteil: Rundung des gedruckten Kastens, Messrauschen.
#   FOTO_DRIFT_MM  der wachsende Anteil: Der Seitenvorschub wird in Millimetern gerechnet
#                  und in Punkt gedruckt, daraus bleibt eine Restdrift von 0,26 mm je
#                  Seite. Die ist kein Fehler, sie summiert sich nur.
#
# Eine feste Schranke von 3 mm hatte deshalb ein Ablaufdatum: Ab Seite 13 wäre jeder
# lange Lebenslauf rot geworden, ohne dass sich etwas verschoben hätte – und eine
# Prüfung, die grundlos rot wird, erzieht dazu, Rot zu ignorieren. Mit dem wachsenden
# Anteil liegt die Schranke selbst auf Seite 50 erst bei 18 mm und damit weit unter dem
# echten Versatz, der bei einer halben Spaltenbreite (rund 30 mm) beginnt.
FOTO_ORT_MM = 3.0
FOTO_DRIFT_MM = 0.3
# Kleiner als das ist kein Bewerbungsfoto, sondern eine abgeschnittene Zierform (siehe
# `_fotos`). Die schmalste gedruckte Fotospalte misst 39 mm – bis dahin ist Luft.
MINDESTMASS_MM = 10.0

MM = 72.0 / 25.4


def ortstoleranz(seitenabstand):
    """Wie weit das Foto auf Seite n von dem der ersten Fotoseite abweichen darf (mm).

    `seitenabstand` ist die Zahl der Blätter dazwischen: Die Drift summiert sich je
    Seite, also wächst die Toleranz mit."""
    return FOTO_ORT_MM + FOTO_DRIFT_MM * max(0, seitenabstand)


def _flaeche(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _schnitt(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _deckung(text_rect, form_rect):
    """Welcher Anteil des Textblocks liegt unter der Form?"""
    f = _flaeche(text_rect)
    return _flaeche(_schnitt(text_rect, form_rect)) / f if f else 0.0


def _textbloecke(seite, fusszeile_ab):
    """Textblöcke mit Inhalt oberhalb von `fusszeile_ab`."""
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


def _fotos(seite):
    """Die Bewerbungsfotos einer Seite - so groß, wie sie gedruckt herauskommen.

    Zwei Entscheidungen stecken darin:

    **Aus der Textstruktur, nicht aus `get_image_info`.** Die Fotos sind mit
    `object-fit: cover` beschnitten: Ein Bild im Seitenverhältnis 9:16 wird auf
    Spaltenbreite gezogen und unten abgeschnitten. `get_image_info` meldet die
    ungeschnittene Lage - bei einem hochkanten Handyfoto 88 mm hoch statt der
    gedruckten 67 mm. Damit würde jede Prüfung Überlappungen melden, die niemand
    sieht. Die Bildblöcke aus `get_text("dict")` tragen den geschnittenen Kasten.

    **Nur Hochformat zählt als Foto.** Ein Bewerbungsfoto steht im Verhältnis 3:4
    hochkant. Breit und flach sind die farbigen Flächen der Vorlagen - die beiden
    Zierkreise der Kopfform `ecke` etwa druckt Chrome als ein Bild über die ganze
    Blattbreite. Die gehören nicht in diese Regel, sie sind Hintergrund.

    **Und nichts Haarfeines.** Eine Zierform, die an der Blattkante abgeschnitten wird,
    kommt als Bildblock von 0,0 mm Breite und 40 mm Höhe heraus - hochkant nach der
    Rechnung oben, und damit zählte sie als Foto. Gemessen an einem nachgestellten
    Überstand: Der Strich stand vor dem echten Foto in der Liste, und wer die Fotobreite
    misst, bekam -0,01 mm statt 47,61 mm. Ein Bewerbungsfoto ist nie schmaler als
    MINDESTMASS_MM."""
    fotos = []
    for block in seite.get_text("dict")["blocks"]:
        if block.get("type") != 1:
            continue
        r = tuple(block["bbox"])
        breite, hoehe = r[2] - r[0], r[3] - r[1]
        if hoehe > breite and min(breite, hoehe) >= MINDESTMASS_MM * MM:
            fotos.append(r)
    return fotos


def pruefe(rohdaten, rand_mm=RAND_MM):
    """Alle Mängel eines PDF als Liste von Klartextzeilen. Leere Liste heißt: sauber."""
    import pymupdf

    maengel = []
    orte = []          # wo das Foto auf jeder Seite steht – für den Vergleich danach
    seitenzahl = 0
    with pymupdf.open(stream=rohdaten, filetype="pdf") as doc:
        seitenzahl = len(doc)
        for nr, seite in enumerate(doc, 1):
            breite, hoehe = seite.rect.width, seite.rect.height
            # Früher stand hier eine Seitenzahl als Druckerfußzeile, und die unteren 10 mm
            # blieben deshalb ungeprüft. Die Seitenzahl ist raus (21.09.2026), also wird
            # das ganze Blatt geprüft: Text, der unten herausläuft, fällt jetzt auf.
            fusszeile_ab = hoehe
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

            # --- Bilder -------------------------------------------------------------
            fotos = _fotos(seite)
            spannen = _spannen(seite, fusszeile_ab) if fotos else []
            if len(fotos) > 1:
                maengel.append(
                    f"Seite {nr}: {len(fotos)} Fotos auf einer Seite – gewollt ist eines")
            for foto in fotos:
                if (foto[0] < 0 or foto[1] < 0
                        or foto[2] > breite or foto[3] > hoehe):
                    maengel.append(f"Seite {nr}: Foto ragt über die Blattkante hinaus")
                # Ein Foto auf dem Text ist immer ein Unfall – anders als eine farbige
                # Fläche trägt es nie Schrift. Deshalb nicht der Flächenanteil wie bei
                # den Formen: Ein Foto, das einem langen Absatz einen Millimeter in die
                # Zeilenanfänge schneidet, deckt vom Absatz kaum ein Prozent ab und
                # käme damit durch – zu lesen ist die Zeile trotzdem nicht mehr.
                # Gemessen wird deshalb die Überschneidung selbst: mehr als ein halber
                # Millimeter in beide Richtungen ist keine Rundung mehr. Gegen die
                # Buchstabenkästen, nicht gegen die Absätze – aus demselben Grund wie
                # eine Regel weiter oben.
                for rect, text in spannen:
                    s = _schnitt(rect, foto)
                    if (s[2] - s[0]) > .5 * MM and (s[3] - s[1]) > .5 * MM:
                        maengel.append(
                            f"Seite {nr}: Foto liegt auf Text – {text[:34]!r}")
                        break
            if fotos:
                orte.append((nr, fotos[0]))

    # --- Keine Seite ohne Foto --------------------------------------------------------
    # Die Gegenrichtung zur Regel darunter, und ohne sie hat die ganze Zusage ein Loch:
    # Verglichen wurde bisher nur unter den Seiten, die ein Foto tragen. Eine Seite ganz
    # ohne konnte gar nicht auffallen – genau der Fehler aus der ersten Runde, wo ab
    # Seite 2 links eine leere 61-mm-Spalte stand, weil der Einzug gesetzt war und das
    # Foto fehlte. Deshalb: Sind überhaupt Fotos im Dokument, gehört auf jede Seite eines.
    # Ein Dokument ganz ohne Fotos bleibt erlaubt – nicht jeder Lebenslauf hat ein Bild.
    if orte and len(orte) < seitenzahl:
        mit_foto = {nr for nr, _ in orte}
        ohne = [n for n in range(1, seitenzahl + 1) if n not in mit_foto]
        maengel.append(
            f"Seiten ohne Foto: {', '.join(str(n) for n in ohne[:8])}"
            f"{' …' if len(ohne) > 8 else ''} – sind Fotos im Dokument, "
            f"trägt jede Seite eines")

    # --- Dieselbe Stelle auf jeder Seite ----------------------------------------------
    # Der Sinn der ganzen Anordnung: Legt man die Blätter übereinander, deckt sich das
    # Foto. Ein Versatz von wenigen Millimetern fällt am Bildschirm nicht auf, auf dem
    # Papierstapel sofort.
    if len(orte) > 1:
        erste_nr, erste = orte[0]
        for nr, foto in orte[1:]:
            weit = max(abs(foto[0] - erste[0]), abs(foto[1] - erste[1])) / MM
            erlaubt = ortstoleranz(nr - erste_nr)
            if weit > erlaubt:
                maengel.append(
                    f"Seite {nr}: Foto steht {weit:.1f} mm woanders als auf "
                    f"Seite {erste_nr} (erlaubt {erlaubt:.1f} mm)")
    return maengel
