# -*- coding: utf-8 -*-
"""Vorlagen unter Last prüfen – bricht etwas ab, reißt etwas, bleibt etwas weiß?

    python -X utf8 cv_probe.py            alle Vorlagen, alle Fälle
    python -X utf8 cv_probe.py atanas     nur eine Vorlage

**Warum das nötig ist.** Eine Lebenslauf-Vorlage sieht mit dem einen Beispiel gut aus,
mit dem nächsten fällt sie auseinander. Vier Fälle entscheiden:

  **viel**   – acht Stationen, lange Tätigkeiten, zehn Fähigkeiten. Hier zeigt sich, ob
               Inhalt abgeschnitten wird und ob Einträge mitten durchreißen.
  **wenig**  – ein Job, keine Bildung, zwei Sprachen. Hier zeigt sich, ob die Seite
               halb leer bleibt oder ob es trotzdem gesetzt aussieht.
  **lang**   – sehr lange Firmennamen, E-Mail-Adressen und Berufsbezeichnungen. Hier
               zeigt sich, ob etwas über den Rand läuft.
  **voll**   – der Qualifikationsblock ausgereizt: 12 Soft Skills, 6 Programme, 5
               Sprachen, 4 Zusatzqualifikationen und Hobbys. Hier zeigt sich, was das
               Raster am Blattende tut, wenn es der höchste Block des Lebenslaufs ist.

Geprüft wird gegen das erzeugte PDF, nicht gegen den Bildschirm: Seitenzahl, Dateigröße
und – das Wichtigste – ob jeder Eintrag, den wir hineingegeben haben, im PDF-Text auch
wieder auftaucht. Was nicht wieder herauskommt, ist unterwegs verloren gegangen.
"""
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import pruefkopie                     # noqa: E402
# Dieselben zwei Zeilen wie in den drei Selbsttests: `app` bindet seinen Datenbankpfad
# beim Import. Ohne sie liefe die Vorlagenprobe auf `improfy_os.db` - heute schreibt sie
# nichts, aber die erste Schreibzeile traefe den Echtbestand.
os.environ["IMPROFY_OS_DB"] = pruefkopie.anlegen("improfy_cv_probe.db")

# Diese Datei LAS `OS_AUSGABE_ORDNER`, setzte es aber nie – als eigenständiges
# Werkzeug schrieb sie damit weiter ins Live-Repo, während der Kommentar unten das
# Gegenteil behauptete. Gelesen wird die Variable beim Import von `cv_pdf`, sie muss
# darum vorher stehen.
# Gesetzt wird **unbedingt**, nicht mit `setdefault`: Ein geerbter Wert aus der Umgebung
# (eine `.env`, ein Startskript, ein Elternprozess) zeigt im Zweifel genau auf den
# Ordner, den dieser Lauf nicht anfassen darf. Dieselbe Hausregel wie in den drei
# Selbsttests; hier stand sie bis zur Zusammenführung anders herum.
# Der Papierkorb kommt aus `pruefkopie` – derselbe Ordner wie die Arbeitskopie, dieselbe
# Prozessnummer, dasselbe Aufräumen am Programmende.
os.environ["OS_AUSGABE_ORDNER"] = pruefkopie.papierkorb("ausgabe")

import app as A                       # noqa: E402
import cv_pdf                         # noqa: E402
from flask import render_template     # noqa: E402

# Wohin gebaute Unterlagen gehen. Ableitbar aus dem eigenen Verzeichnis – aber dann
# schreibt jeder Testlauf ins Live-Repo. Der Dateiname trägt Kundennummer und Datum,
# also überschreibt ein Test ein am selben Tag echt gebautes Dokument desselben
# Menschen. Dieselbe Fehlerklasse wie bei `sicherungen/`, darum dieselbe Lösung:
# `OS_AUSGABE_ORDNER` setzen die Selbsttests auf einen Papierkorb.
AUSGABE = os.path.join(os.environ.get("OS_AUSGABE_ORDNER")
                       or os.path.join(HIER, "ausgabe"), "vorlagenpruefung")


def _beruf(n):
    return [{"zeitraum": f"0{(i % 9) + 1}.201{i % 10} - 12.202{i % 5}",
             "jobtitel": ["Lagerhelfer", "Kommissionierer", "Produktionshelfer",
                          "Sicherheitsmitarbeiter", "Reinigungskraft", "Verkäufer",
                          "Fahrer", "Küchenhilfe"][i % 8],
             "firma": ["Amazon Logistik Werne GmbH", "REWE Markt GmbH Zweigniederlassung West",
                       "Deutsche Post DHL Group", "Securitas Sicherheitsdienste",
                       "Piepenbrock Dienstleistungen", "Netto Marken-Discount Stiftung & Co. KG",
                       "Kaufland Dienstleistung GmbH & Co. KG", "Sodexo Services GmbH"][i % 8],
             "taetigkeiten": ["Warenannahme und Kontrolle der Lieferscheine",
                              "Kommissionierung mit dem Handscanner",
                              "Einhaltung der Vorgaben zu Arbeitssicherheit und Hygiene",
                              "Einweisung neuer Kolleginnen und Kollegen"][: (i % 4) + 1]}
            for i in range(n)]


# **Alle drei Personen sind frei erfunden** – Name, Geburtsdatum, Anschrift und Nummer
# gibt es so nicht. Die Datei ist versioniert; ein echter Kundenname oder eine echte
# Handynummer hätte hier nichts verloren, auch nicht als Probedatensatz. Die Nummern
# können gar nicht vergeben sein (+49 000 …), die Anschriften sind Musteranschriften.
FAELLE = {
    "viel": {
        "vorname": "Nabil", "nachname": "Musterbewerber",
        "angestrebter_job": "Fachkraft für Lagerlogistik", "geschlecht": "m",
        "geburtsdatum": "01.01.1990", "mobil": "+49 000 0000000",
        "email": "nabil.musterbewerber@beispiel.de",
        "adresse": "Musterweg 1, 00000 Musterstadt",
        "massnahme_zeitraum": "17.08.2026 - 11.10.2026",
        "fuehrerschein": {"vorhanden": True, "klasse": "B", "eu": True},
        "ueber_mich": ("Guten Tag,\n\nmein Name ist Nabil Musterbewerber. Nach meiner "
                       "Ausbildung war ich über zwölf Jahre in der Lagerlogistik tätig, zuletzt "
                       "als Schichtverantwortlicher für ein Team von acht Personen.\n\n"
                       "Ich arbeite sorgfältig, bin körperlich belastbar und gewohnt, im "
                       "Schichtbetrieb zu arbeiten. Der Umgang mit Handscanner und "
                       "Lagerverwaltungssoftware ist mir vertraut.\n\n"
                       "Seit 2023 lebe ich in Deutschland und verfüge über Deutschkenntnisse "
                       "auf dem Niveau B1, an denen ich weiter arbeite."),
        "berufserfahrung": _beruf(8),
        "bildung": [{"zeitraum": f"20{10 + i} - 20{12 + i}",
                     "abschluss": ["Fachkraft für Lagerlogistik", "Mittlere Reife",
                                   "Weiterbildung Arbeitssicherheit"][i % 3],
                     "institution": "Berufskolleg Ehrenfeld", "note": "2,3"} for i in range(4)],
        "zusatzqualifikationen": ["Staplerschein", "Sachkunde §34a", "Erste Hilfe"],
        "sprachen": [{"sprache": "DEUTSCH", "niveau": "B1"},
                     {"sprache": "DARI", "niveau": "Muttersprache"},
                     {"sprache": "PASCHTU", "niveau": "Muttersprache"},
                     {"sprache": "ENGLISCH", "niveau": "Grundkenntnisse"},
                     {"sprache": "URDU", "niveau": "Fließend"}],
        "edv_kenntnisse": [{"programm": p, "sterne": 3 + (i % 3)} for i, p in enumerate(
            ["MS Word", "MS Excel", "Handscanner", "SAP EWM", "Outlook"])],
        "soft_skills": [{"eigenschaft": e, "sterne": 4 + (i % 2)} for i, e in enumerate(
            ["Zuverlässigkeit", "Belastbarkeit", "Teamfähigkeit", "Sorgfalt",
             "Lernbereitschaft", "Pünktlichkeit", "Kommunikationsfähigkeit",
             "Verantwortungsbewusstsein", "Flexibilität", "Auffassungsgabe"])],
        "hobbys": "Fußball, Kochen, Fahrradfahren",
    },
    "wenig": {
        "vorname": "Ana", "nachname": "Popa", "angestrebter_job": "Reinigungskraft",
        "geschlecht": "w", "geburtsdatum": "02.11.1995", "mobil": "+49 176 1234567",
        "email": "a.popa@example.de", "adresse": "Kalker Hauptstraße 1, 51103 Köln",
        "massnahme_zeitraum": "01.09.2026 - 30.11.2026",
        "fuehrerschein": {"vorhanden": False, "klasse": "", "eu": False},
        "ueber_mich": "Ich suche eine Stelle als Reinigungskraft in Köln.",
        "berufserfahrung": _beruf(1),
        "bildung": [], "zusatzqualifikationen": [],
        "sprachen": [{"sprache": "DEUTSCH", "niveau": "A2"},
                     {"sprache": "RUMÄNISCH", "niveau": "Muttersprache"}],
        "edv_kenntnisse": [], "soft_skills": [{"eigenschaft": "Zuverlässigkeit", "sterne": 5}],
        "hobbys": "",
    },
    "lang": {
        "vorname": "Maximiliane-Charlotte", "nachname": "Von Hohenzollern-Sigmaringen",
        "angestrebter_job": "Sachbearbeiterin für Personalverwaltung und Lohnbuchhaltung",
        "geschlecht": "w", "geburtsdatum": "29.02.1988",
        "mobil": "+49 (0) 221 / 170 027 61 - 4711",
        "email": "maximiliane.charlotte.von.hohenzollern@sehr-lange-firmendomain-beispiel.de",
        "adresse": "Bundesallee an der Gedächtniskirche 188a, 10717 Berlin-Wilmersdorf",
        "massnahme_zeitraum": "01.01.2026 - 31.12.2026",
        "fuehrerschein": {"vorhanden": True, "klasse": "BE", "eu": True},
        "ueber_mich": "Kurzer Text, aber sehr lange Angaben ringsherum.",
        "berufserfahrung": [{
            "zeitraum": "01.2015 - 12.2024",
            "jobtitel": "Sachbearbeiterin Personalverwaltung und Entgeltabrechnung",
            "firma": "Rheinisch-Westfälische Dienstleistungsgesellschaft für Personalwesen mbH & Co. KG",
            "taetigkeiten": ["Vorbereitende Lohnbuchhaltung einschließlich der Meldungen an "
                             "Sozialversicherungsträger und Finanzamt"]}],
        "bildung": [{"zeitraum": "2008 - 2012",
                     "abschluss": "Bachelor of Arts Betriebswirtschaftslehre mit Schwerpunkt "
                                  "Personalmanagement",
                     "institution": "Fachhochschule für Ökonomie und Management", "note": "1,8"}],
        "zusatzqualifikationen": ["Geprüfte Personalfachkauffrau (IHK)"],
        "sprachen": [{"sprache": "DEUTSCH", "niveau": "Muttersprache"},
                     {"sprache": "ENGLISCH", "niveau": "Verhandlungssicher"}],
        "edv_kenntnisse": [{"programm": "DATEV Lohn und Gehalt comfort", "sterne": 5}],
        "soft_skills": [{"eigenschaft": "Verantwortungsbewusstsein", "sterne": 5}],
        "hobbys": "Chorgesang",
    },
    # **Der vierte Fall, und warum es ihn braucht (21.09.2026).** Die drei oberen reizen
    # die Berufserfahrung aus, den Qualifikationsblock nicht: `viel` hat zehn Faehigkeiten
    # und fuenf Programme und liegt damit bei vier Seiten genau auf der Kante - zwei
    # Faehigkeiten mehr, und es werden fuenf. Beim Umbau auf „ein Foto je Seite" ist genau
    # dort etwas passiert, das keiner der drei Faelle gesehen hat: Der Textkoerper rueckt
    # seither um die Fotospalte ein, das Qualifikationsraster hat nur noch 117 mm statt
    # 178 mm und faellt von zwei Spalten auf eine (siehe den Kommentar am `.quali-raster`
    # in cv_skin.html).
    #
    # Dieser Fall stellt den Block deshalb an die erste Stelle: 12 Soft Skills, 6
    # Programme, 5 Sprachen, 4 Zusatzqualifikationen, Hobbys - der hoechste Block des
    # Lebenslaufs, und er steht am Ende, wo der Umbruch wehtut. Die Berufserfahrung ist
    # bewusst knapp (drei Stationen): Was hier reisst, soll aus den Qualifikationen
    # kommen und nicht aus acht Stationen davor.
    #
    # Die Person ist wie die drei anderen **frei erfunden** - Name, Geburtsdatum,
    # Anschrift, Nummer (+49 000 …) gibt es so nicht.
    "voll": {
        "vorname": "Amira", "nachname": "Musterfrau",
        "angestrebter_job": "Kauffrau für Büromanagement", "geschlecht": "w",
        "geburtsdatum": "14.06.1992", "mobil": "+49 000 0000000",
        "email": "amira.musterfrau@beispiel.de",
        "adresse": "Musterallee 12, 00000 Musterstadt",
        "massnahme_zeitraum": "02.03.2026 - 29.05.2026",
        "fuehrerschein": {"vorhanden": True, "klasse": "B", "eu": True},
        "ueber_mich": ("Guten Tag,\n\nmein Name ist Amira Musterfrau. Ich habe acht Jahre "
                       "im Büro einer Spedition gearbeitet, zuletzt in der "
                       "Auftragsabwicklung.\n\n"
                       "Ich arbeite strukturiert, bin im Umgang mit Kundinnen und Kunden "
                       "geübt und übernehme gern Verantwortung für einen festen "
                       "Aufgabenbereich."),
        "berufserfahrung": [
            {"zeitraum": "03.2019 - 08.2025",
             "jobtitel": "Sachbearbeiterin Auftragsabwicklung",
             "firma": "Rheinische Speditionsgesellschaft mbH",
             "taetigkeiten": ["Auftragserfassung und Terminüberwachung",
                              "Schriftverkehr mit Kunden und Fahrern",
                              "Vorbereitung der monatlichen Abrechnung"]},
            {"zeitraum": "09.2015 - 02.2019", "jobtitel": "Bürokraft",
             "firma": "Steuerkanzlei Musterberg und Partner",
             "taetigkeiten": ["Postbearbeitung und Aktenpflege",
                              "Vorbereitende Buchhaltung"]},
            {"zeitraum": "08.2012 - 08.2015", "jobtitel": "Verkäuferin",
             "firma": "Modehaus Musterstadt GmbH",
             "taetigkeiten": ["Beratung und Kassentätigkeit"]}],
        "bildung": [{"zeitraum": "2009 - 2012", "abschluss": "Kauffrau für Büromanagement",
                     "institution": "Berufskolleg Musterstadt", "note": "2,1"},
                    {"zeitraum": "2003 - 2009", "abschluss": "Mittlere Reife",
                     "institution": "Realschule Musterstadt", "note": "2,4"}],
        "zusatzqualifikationen": ["Zertifikat Büromanagement (IHK)",
                                  "Buchführung Grundkurs", "Zehn-Finger-Schreiben",
                                  "Erste Hilfe"],
        "sprachen": [{"sprache": "DEUTSCH", "niveau": "C1"},
                     {"sprache": "BOSNISCH", "niveau": "Muttersprache"},
                     {"sprache": "ENGLISCH", "niveau": "Fließend"},
                     {"sprache": "FRANZÖSISCH", "niveau": "Grundkenntnisse"},
                     {"sprache": "ITALIENISCH", "niveau": "Grundkenntnisse"}],
        "edv_kenntnisse": [{"programm": p, "sterne": 3 + (i % 3)} for i, p in enumerate(
            ["MS Word", "MS Excel", "MS PowerPoint", "MS Outlook",
             "DATEV Mittelstand", "SAP ERP"])],
        # Die langen Wörter stehen hier mit Absicht: „Verantwortungsbewusstsein" ist bei
        # 10 pt 43,5 mm breit und damit das Maß, an dem sich entscheidet, ob eine
        # Rasterspalte noch eine Zeile je Fähigkeit trägt.
        "soft_skills": [{"eigenschaft": e, "sterne": 4 + (i % 2)} for i, e in enumerate(
            ["Zuverlässigkeit", "Belastbarkeit", "Teamfähigkeit", "Sorgfalt",
             "Lernbereitschaft", "Pünktlichkeit", "Kommunikationsfähigkeit",
             "Verantwortungsbewusstsein", "Flexibilität", "Auffassungsgabe",
             "Organisationstalent", "Durchsetzungsvermögen"])],
        "hobbys": "Lesen, Schwimmen, Gartenarbeit",
    },
}


def _pdf_text(rohdaten):
    try:
        import pymupdf
        with pymupdf.open(stream=rohdaten, filetype="pdf") as doc:
            return len(doc), "\n".join(s.get_text("text") for s in doc)
    except Exception:
        return None, ""


# Ab wann eine Schlussseite als "leer" gilt.
#
# 300 war zu streng. Eine Seite mit einer Kapitelueberschrift und vier echten Zeilen -
# Sprachen und Faehigkeiten, rund 190 Zeichen - ist duenn, aber sie traegt etwas. Der
# Fehler, der nie passieren darf, sieht anders aus: 22 bis 80 Zeichen, also nur der
# wiederholte Kopf und die Seitenzahl. Genau der wird bei 150 noch gefangen.
#
# Die Grenze ist bewusst hier dokumentiert und nicht stillschweigend gesenkt worden:
# Sie wurde gesenkt, weil der strengere Wert bei einem Lebenslauf mit einem Job und
# drei Fotos anschlug - einem echten, wenn auch seltenen Fall.
MIN_LETZTE_SEITE = 150


def _musterfoto(kleidung=(44, 60, 82), grund=(233, 235, 237)):
    """Eine gezeichnete Silhouette - nur fuer die Probe, sie wird nirgends gespeichert.

    Ohne Foto faellt der Umbruch anders als im Echtbetrieb: die Fotospalte ist das
    hoechste Einzelstueck auf Seite 1. Wer ohne sie prueft, prueft den falschen Fall.

    Die drei Bilder sind **verschieden eingefaerbt**, und das ist kein Schmuck: Jede
    Seite traegt eines, und sind es weniger Fotos als Seiten, fangen sie von vorn an.
    Mit dreimal demselben Bild waere im PDF nicht zu erkennen, ob die Reihenfolge
    stimmt oder ob auf jeder Seite zufaellig dasselbe steht."""
    import io
    from PIL import Image, ImageDraw
    b = Image.new("RGB", (900, 1150), grund)
    z = ImageDraw.Draw(b)
    z.ellipse((310, 200, 590, 480), fill=(203, 176, 148))
    z.rounded_rectangle((240, 495, 660, 1150), 46, fill=kleidung)
    puffer = io.BytesIO()
    b.save(puffer, "JPEG", quality=88)
    return cv_pdf.foto_uri(puffer.getvalue(), "image/jpeg")


FOTOS = None


def pruefe(schluessel, fall_name, daten, speichern=True):
    """Eine Vorlage mit einem Fall bauen und nachsehen, was herauskam."""
    global FOTOS
    if FOTOS is None:
        FOTOS = [_musterfoto(), _musterfoto((92, 46, 46), (240, 236, 228)),
                 _musterfoto((38, 84, 70), (226, 238, 234))]
    # Mit allen drei Fotos, nicht nur mit einem: Geprueft wird der volle Fall - drei
    # Bilder, die sich ueber die Seiten verteilen und sich auf einem laengeren
    # Lebenslauf wiederholen.
    #
    # Zwei Durchgaenge wie im Echtbetrieb: erst die Seiten zaehlen, dann je Seite ein
    # Foto setzen. `cv_pdf.bauen` macht genau das, legt das Ergebnis aber in
    # ausgabe/lebenslaeufe/ ab - dort haben 69 Probelaeufe nichts zu suchen, da liegen
    # die Lebenslaeufe echter Kunden. Die Probe schreibt in ihren eigenen Ordner.
    #
    # `_seiten_zaehlen` gibt `None` zurueck, wenn die Seitenzahl nicht zu ermitteln ist
    # (kein pymupdf). Dann wird wie im Echtbetrieb ohne Einzug gedruckt - ein Foto auf
    # Seite 1 statt eines Einzugs ohne Fotos dahinter.
    with A.app.test_request_context():
        pdf = cv_pdf.pdf_aus_html(
            cv_pdf.html_bauen(render_template, daten, schluessel, bilder=FOTOS))
        blaetter = cv_pdf._seiten_zaehlen(pdf)
        if blaetter is None:
            pdf = cv_pdf.pdf_aus_html(
                cv_pdf.html_bauen(render_template, daten, schluessel, bilder=FOTOS,
                                  seiten=1, einzug=False))
        elif blaetter > 1:
            pdf = cv_pdf.pdf_aus_html(
                cv_pdf.html_bauen(render_template, daten, schluessel, bilder=FOTOS,
                                  seiten=blaetter))
    seiten, text = _pdf_text(pdf)
    # Zwei Dinge sind gewollt und dürfen nicht als Verlust gelten:
    #   Versalien – die Vorlagen setzen Überschriften groß (ZUVERLÄSSIGKEIT)
    #   Trennung  - ein langer Firmenname bricht mit Bindestrich um
    flach = " ".join(text.replace("-\n", "-").split()).casefold()
    # Bei Silbentrennung steht im PDF „Kommunikations-\nfähigkeit". Wird nur der
    # Zeilenumbruch entfernt, bleibt der Bindestrich stehen und das Wort gilt als
    # verloren, obwohl es dasteht. Deshalb beide Lesarten prüfen.
    ohne_trennung = " ".join(text.replace("-\n", "").split()).casefold()

    # Kommt alles wieder heraus, was hineinging?
    erwartet = []
    for b in daten.get("berufserfahrung") or []:
        erwartet.append(("Station", b["firma"].split()[0]))
        for t in b.get("taetigkeiten") or []:
            erwartet.append(("Tätigkeit", t.split()[0]))
    for b in daten.get("bildung") or []:
        erwartet.append(("Bildung", b["abschluss"].split()[0]))
    for s in daten.get("sprachen") or []:
        erwartet.append(("Sprache", s["sprache"]))
    for s in daten.get("soft_skills") or []:
        erwartet.append(("Skill", s["eigenschaft"]))
    for e in daten.get("edv_kenntnisse") or []:
        erwartet.append(("EDV", e["programm"].split()[0]))
    fehlt = [f"{art} {wort}" for art, wort in erwartet
             if wort and wort.casefold() not in flach
             and wort.casefold() not in ohne_trennung]

    if speichern:
        os.makedirs(AUSGABE, exist_ok=True)
        with open(os.path.join(AUSGABE, f"{schluessel}_{fall_name}.pdf"), "wb") as f:
            f.write(pdf)
    # Liegt etwas uebereinander oder ragt in den Rand? Das faellt sonst erst am
    # Bildschirm auf - dreimal hintereinander genau so passiert (18.09.2026).
    import seitenpruefung
    fehlt += seitenpruefung.pruefe(pdf)

    if seiten > 1:
        import pymupdf
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            letzte = len(" ".join(doc[-1].get_text("text").split()))
        if letzte < MIN_LETZTE_SEITE:
            fehlt.append(f"Schlussseite fast leer ({letzte} Zeichen)")
    return {"vorlage": schluessel, "fall": fall_name, "seiten": seiten,
            "kb": len(pdf) // 1024, "zeichen": len(flach), "fehlt": fehlt}


def main(nur=None):
    if not cv_pdf.bereit():
        print("Kein Chrome gefunden – ohne Browser kein PDF-Druck.")
        return 1
    schluessel = [s for s, _, _ in cv_pdf.DESIGNS if not nur or s == nur]
    print(f"Vorlagenprüfung · {len(schluessel)} Vorlagen × {len(FAELLE)} Fälle\n")
    print(f"  {'Vorlage':12} {'Fall':8} {'Seiten':>7} {'kB':>5} {'Zeichen':>8}  Verlust")
    schlecht = 0
    for s in schluessel:
        for fall, daten in FAELLE.items():
            try:
                e = pruefe(s, fall, dict(daten))
            except Exception as fehler:
                print(f"  {s:12} {fall:8} {'FEHLER':>7}  {fehler}")
                schlecht += 1
                continue
            verlust = ", ".join(e["fehlt"][:3]) + ("…" if len(e["fehlt"]) > 3 else "")
            if e["fehlt"]:
                schlecht += 1
            print(f"  {e['vorlage']:12} {e['fall']:8} {str(e['seiten']):>7} {e['kb']:>5}"
                  f" {e['zeichen']:>8}  {verlust or '—'}")
    print(f"\nPDFs liegen in {AUSGABE}")
    if schlecht:
        print(f"{schlecht} Durchgänge mit Verlust oder Fehler – das muss auf null.")
    else:
        print("Kein Inhalt verloren, nichts uebereinander, keine leere Schlussseite.")
    return 0 if not schlecht else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
