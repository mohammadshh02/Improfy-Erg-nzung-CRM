# -*- coding: utf-8 -*-
"""Selbsttest des Lebenslauf-Bauers – läuft gegen eine Kopie der Datenbank.

    python -X utf8 lebenslauf_test.py

Geprüft wird der Weg, den die Kollegin im Alltag geht: Kunde in der Akte öffnen,
„Lebenslauf bauen", Formular ausfüllen, Excel herunterladen. Braucht kein Internet.
"""
import os
import re
import shutil
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
kopie = os.path.join(tempfile.gettempdir(), "improfy_os_cv_test.db")
shutil.copy(os.path.join(HIER, "improfy_os.db"), kopie)
os.environ["IMPROFY_OS_DB"] = kopie

import openpyxl                     # noqa: E402
from flask import render_template as flask_render   # noqa: E402
import app as A                     # noqa: E402
import datenbank as db              # noqa: E402
import lebenslauf_bauen as LB       # noqa: E402

ergebnis = []


def vorlagen_dateien():
    ordner = os.path.join(HIER, "templates", "cv_designs")
    return [os.path.join(ordner, f) for f in os.listdir(ordner)
            if f.startswith("cv_") and f.endswith(".html")]


def offen(pfad):
    with open(pfad, encoding="utf-8") as f:
        return f.read()


def ohne_kommentare(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def pruefe(name, bedingung, detail=""):
    ergebnis.append((name, bool(bedingung)))
    print(f"  {'OK  ' if bedingung else 'FEHL'} {name}{(' – ' + str(detail)) if detail else ''}")


def main():
    db.init()
    c = A.app.test_client()
    kid = db.wert("SELECT kunde_id FROM gutschein_zeile WHERE kunde_id IS NOT NULL"
                  "   AND improfy_id IS NOT NULL AND improfy_id<>'' LIMIT 1")
    kunde = db.eine("SELECT * FROM kunde WHERE id=?", (kid,))
    print(f"\nTestkunde: {kunde['name']} (id {kid})")

    print("\n1. Vorbelegung aus dem OS")
    v = LB.vorbelegung(kid)
    pruefe("Kunde gefunden, Name geteilt", v and v["daten"]["vorname"] and v["daten"]["nachname"],
           f"{v['daten']['vorname']} | {v['daten']['nachname']}")
    pruefe("Interne Kundennummer kommt aus dem Register, nicht aus der Tastatur",
           re.match(r"^ID-K\d+$", v["interne_id"] or ""), v["interne_id"])
    pruefe("Blattname ist ID + Name", v["blattname"].startswith(v["interne_id"]), v["blattname"])
    pruefe("Telefon aus der Kundenakte übernommen",
           (v["daten"]["mobil"] or "") == (kunde["telefon"] or ""), v["daten"]["mobil"])
    pruefe("Deutsch steht bei den Sprachen zuerst",
           v["daten"]["sprachen"] and v["daten"]["sprachen"][0]["sprache"] == "DEUTSCH")
    pruefe("Coach steht im Feld „Kunde von\"", bool(v["daten"]["kunde_von"]), v["daten"]["kunde_von"])

    print("\n2. Formularseite")
    r = c.get(f"/kunde/{kid}/lebenslauf")
    pruefe("Seite antwortet und zeigt den Blattnamen", r.status_code == 200 and v["blattname"] in r.text)
    pruefe("Vorbelegte Werte stehen im Formular", v["daten"]["nachname"] in r.text)

    print("\n3. Excel erzeugen")
    daten = {
        "vorname": v["daten"]["vorname"], "nachname": v["daten"]["nachname"],
        "angestrebter_job": "Lagerhelfer", "geschlecht": "m", "geburtsdatum": "01.01.1990",
        "mobil": "+49 170 0000000", "email": "test@example.de", "adresse": "Teststr. 1, 50667 Köln",
        "kunde_von": "Selbsttest", "fs_vorhanden": "on", "fs_klasse": "B", "fs_eu": "on",
        "beruf_zeitraum": ["01.2020 - 12.2023"], "beruf_firma": ["Testfirma GmbH, Köln"],
        "beruf_jobtitel": ["Lagerhelfer"], "beruf_taet": ["Kommissionieren\nWareneingang"],
        "bild_zeitraum": ["2015 - 2018"], "bild_abschluss": ["Berufsausbildung"],
        "bild_institution": ["Berufsschule"], "bild_note": [""],
        "spr_sprache": ["DEUTSCH", "ARABISCH"], "spr_niveau": ["gute Kenntnisse", "Muttersprache"],
        "edv_programm": ["MS Word"], "edv_sterne": ["4"],
        "ss_eigenschaft": ["Zuverlässigkeit"], "ss_sterne": ["5"],
        "zusatzqual": "Sachkunde §34a", "hobbys": "Fußball", "ueber_mich": "Guten Tag, ...",
    }
    r = c.post(f"/kunde/{kid}/lebenslauf", data=daten)
    pruefe("Excel kommt als Download zurück", r.status_code == 200 and len(r.data) > 20000,
           f"{len(r.data)} Bytes")
    dateiname = re.search(r'filename="([^"]+)"', r.headers.get("Content-Disposition", ""))
    dateiname = dateiname.group(1) if dateiname else ""
    pruefe("Dateiname trägt die Kunden-ID", dateiname.startswith(v["interne_id"]), dateiname)

    print("\n4. Inhalt der Datei")
    pfad = LB.datei(dateiname)
    ws = openpyxl.load_workbook(pfad).active
    pruefe("Blatt heißt wie der Kunde", ws.title.startswith(v["interne_id"]), ws.title)
    pruefe("Kopfdaten stehen in der Vorlage",
           ws["B10"].value == daten["vorname"] and ws["B12"].value == "Lagerhelfer"
           and ws["B14"].value == daten["mobil"], [ws[z].value for z in ("B10", "B12", "B14")])
    pruefe("Improfy-Block geschlechtsrichtig", ws["D25"].value == "Teilnehmer", ws["D25"].value)
    pruefe("Kunde von gesetzt", ws["B3"].value == "Selbsttest", ws["B3"].value)
    pruefe("Berufserfahrung eingetragen",
           ws["B30"].value == "01.2020 - 12.2023" and ws["D31"].value == "Lagerhelfer")
    pruefe("Bildung eingetragen", ws["D98"].value == "Berufsausbildung")

    print("\n5. Ergebnis hängt am Kunden")
    gebaut = LB.gebaute(kid)
    pruefe("Lebenslauf ist am Kunden vermerkt", any(g["name"] == dateiname for g in gebaut))
    dok = db.eine("SELECT vorhanden, dateiname FROM dokument WHERE kunde_id=? AND schluessel='lebenslauf'",
                  (kid,))
    pruefe("Qualitätsmanagement zählt den Lebenslauf als vorhanden",
           dok and dok["vorhanden"] == 1, dict(dok) if dok else None)
    pruefe("Datei lässt sich erneut herunterladen",
           c.get("/lebenslauf/datei/" + dateiname).status_code == 200)
    pruefe("Kundenakte verweist auf den Bauer",
           "lebenslauf" in c.get(f"/kunde/{kid}").text.casefold())

    print("\n6. Alte Unterlagen ablegen")
    import io
    import dokument_lesen as DL

    def feld(text, name):
        return re.findall('name="%s" value="([^"]*)"' % name, text)

    r = c.post(f"/kunde/{kid}/lebenslauf/lesen",
               data={"unterlagen": (io.BytesIO(open(pfad, "rb").read()), "alter_bogen.xlsx")},
               content_type="multipart/form-data")
    t = r.get_data(as_text=True)
    pruefe("Hochgeladene Improfy-Excel wird zurückgelesen",
           r.status_code == 200 and "Improfy-Vorlage gelesen" in t)
    pruefe("Alle Felder kommen aus der Excel zurück",
           feld(t, "angestrebter_job")[:1] == ["Lagerhelfer"]
           and feld(t, "beruf_firma")[:1] == ["Testfirma GmbH, Köln"]
           and feld(t, "bild_abschluss")[:1] == ["Berufsausbildung"]
           and feld(t, "fs_klasse")[:1] == ["B"],
           [feld(t, n)[:1] for n in ("angestrebter_job", "beruf_firma", "bild_abschluss", "fs_klasse")])
    pruefe("Tätigkeiten stehen wieder im Textfeld",
           "Kommissionieren" in t)

    text = ("Max Mustermann\n01.01.1990\nBERUFSERFAHRUNG\n"
            "(2020 - 2023) Lagerhelfer | Amazon GmbH,\nKöln\nWarenannahme\n"
            "SPRACHKENNTNISSE\nDEUTSCH - B1\n")
    daten2, gefunden2, hinweise2, roh2 = DL.lese_unterlagen(
        [("lebenslauf.txt", text.encode("utf-8"))])
    pruefe("Textdatei wird gelesen", bool(gefunden2), gefunden2)
    pruefe("Firma und Titel stehen richtig herum",
           daten2["berufserfahrung"][0]["firma"].startswith("Amazon")
           and daten2["berufserfahrung"][0]["jobtitel"] == "Lagerhelfer",
           daten2["berufserfahrung"][:1])
    pruefe("Umgebrochener Firmenname wird zusammengesetzt",
           daten2["berufserfahrung"][0]["firma"] == "Amazon GmbH, Köln",
           daten2["berufserfahrung"][0]["firma"])
    pruefe("Jedes Formularfeld ist im Ergebnis vorhanden",
           set(DL.leere_daten()) <= set(daten2))

    _, _, hinweise3, _ = DL.lese_unterlagen([("foto.jpg", b"\xff\xd8\xff")])
    pruefe("Bilder werden klar abgelehnt",
           any("Bilder" in h for h in hinweise3), hinweise3)
    _, _, hinweise4, _ = DL.lese_unterlagen([("scan.pdf", b"%PDF-1.4 kaputt")])
    pruefe("Unlesbares PDF wird gemeldet", bool(hinweise4), hinweise4)
    r = c.post(f"/kunde/{kid}/lebenslauf/lesen", data={"rohtext": ""})
    pruefe("Ohne Datei und ohne Text kommt eine Meldung, kein Absturz",
           r.status_code == 200 and "ließ sich nichts auslesen" in r.get_data(as_text=True))

    print("\n7. Designtes PDF")
    import cv_pdf
    block = cv_pdf.improfy_block({"geschlecht": "w", "massnahme_zeitraum": "18.05.2026 - 23.08.2026"})
    pruefe("Improfy-Block trägt den Maßnahme-Zeitraum",
           block["zeitraum"] == "18.05.2026 - 23.08.2026" and block["jobtitel"] == "Teilnehmerin", block)
    pruefe("Mehrere Sprachen in einer Zelle werden getrennt",
           [e["sprache"] for e in LB._sprachen({"sprache": "Deutsch/Paschtu"})] == ["DEUTSCH", "PASCHTU"],
           LB._sprachen({"sprache": "Deutsch/Paschtu"}))
    pruefe("Ohne Zeitraum bleibt es bei aktuell",
           cv_pdf.improfy_block({})["zeitraum"] == "aktuell")
    # Seit dem Umbau auf die Sammlung teilen sich alle Nummern ein Gerüst. Geprüft wird
    # deshalb nicht mehr „jede Vorlage hat ihre eigene Datei", sondern: jede angebotene
    # Nummer führt auf eine vorhandene Datei **und** hat eine eigene Beschreibung.
    # Ohne das Zweite stünden im Auswahlfeld 23 Einträge, die alle gleich aussähen.
    import cv_sammlung
    pruefe("Jede angebotene Vorlage führt auf eine vorhandene Datei",
           all(os.path.exists(os.path.join("templates", cv_pdf.DESIGN_DATEI[sch]))
               for sch, _, _ in cv_pdf.DESIGNS),
           [sch for sch, _, _ in cv_pdf.DESIGNS
            if not os.path.exists(os.path.join("templates", cv_pdf.DESIGN_DATEI.get(sch, "")))])
    _skins = [cv_sammlung.skin(sch) for sch, _, _ in cv_pdf.DESIGNS]
    pruefe("Keine zwei Vorlagen sehen gleich aus",
           len({(k["kopfform"], k["farbe"], k["farbe2"], k["bewertung"]) for k in _skins})
           == len(_skins),
           f"{len(_skins)} Vorlagen")
    pruefe("Jede Vorlage nennt eine Kopfform, die das Gerüst kennt",
           all(k["kopfform"] in ("band", "links", "ecke", "flaeche", "rahmen", "schlicht")
               and k["bewertung"] in ("sterne", "punkte", "balken", "text") for k in _skins),
           [k["kennung"] for k in _skins
            if k["kopfform"] not in ("band", "links", "ecke", "flaeche", "rahmen", "schlicht")])
    with A.app.test_request_context():
        html = cv_pdf.html_bauen(flask_render,
                                 dict(daten, massnahme_zeitraum="01.01.2026 - 01.03.2026"),
                                 cv_pdf.DESIGNS[0][0])
    # Seit dem Umbau auf den gemeinsamen Rahmen gibt es keine festen Seiten mehr –
    # der Inhalt fließt über so viele, wie er braucht. Geprüft wird deshalb, dass die
    # Bausteine da sind, nicht wie viele Seiten herauskommen.
    pruefe("Die erste Vorlage rendert Name, Zeitraum und die Kapitel",
           daten["nachname"] in html and "01.01.2026 - 01.03.2026" in html
           and html.count('class="kapitel"') >= 2,
           f"{html.count('class=\"kapitel\"')} Kapitel")
    # Der eigentliche Fehler war eine **feste Seitenhöhe**: Was darüber hinausging, war weg.
    # `overflow: hidden` an Zierelementen (Bildkopf, Linie neben der Überschrift) ist in
    # Ordnung – geprüft wird deshalb nur die feste Höhe, und Kommentare zählen nicht mit.
    schlecht = [os.path.basename(f) for f in vorlagen_dateien()
                if re.search(r"height:\s*29[0-9]mm", ohne_kommentare(offen(f)))]
    pruefe("Keine Vorlage presst den Inhalt in eine feste Seitenhöhe",
           not schlecht, schlecht)

    print("\n8. Unvollständige Eingabe bricht nicht ab")
    r = c.post(f"/kunde/{kid}/lebenslauf", data={"vorname": "Nur", "nachname": "Name"})
    pruefe("Excel entsteht auch ohne Pflichtfelder, Lücken werden gemeldet",
           r.status_code == 200 and int(r.headers.get("X-Fehlende-Felder", 0)) > 0,
           r.headers.get("X-Fehlende-Felder") + " fehlende Felder")

    print("\n9. Bewerbungsfoto")
    import io as _io
    import fotos
    fotos.init()
    try:
        from PIL import Image
        bild = Image.new("RGB", (2000, 2500), (150, 170, 120))
        puffer = _io.BytesIO(); bild.save(puffer, "JPEG", quality=95)
        gross = puffer.getvalue()
        r = c.post(f"/kunde/{kid}/foto", data={"foto": (_io.BytesIO(gross), "Probe.jpg")},
                   content_type="multipart/form-data")
        f = fotos.foto(kid)
        pruefe("Foto wird gespeichert und auf Druckgröße verkleinert",
               r.status_code == 200 and f and f["bytes"] < len(gross) and f["breite"] <= 1200,
               f"{len(gross)//1024} kB → {f['bytes']//1024} kB, {f['breite']}×{f['hoehe']}")
        pruefe("Foto lässt sich wieder ausliefern",
               c.get(f"/kunde/{kid}/foto.jpg").status_code == 200)
        pruefe("Formular zeigt das hinterlegte Foto",
               "Foto hinterlegt" in c.get(f"/kunde/{kid}/lebenslauf").get_data(as_text=True))
    except ImportError:
        pruefe("Pillow fehlt – Fototest übersprungen", True)
    kunden = [{"id": 1, "name": "Farnam Foroutan"}, {"id": 2, "name": "Hakan Tan"}]
    pruefe("Foto wird über den Dateinamen zugeordnet",
           fotos.zuordnen("Farnam_Foroutan.jpg", kunden) == 1
           and fotos.zuordnen("Bild von Hakan Tan.png", kunden) == 2)
    pruefe("Unklare Dateinamen werden nicht zugeordnet",
           fotos.zuordnen("IMG_2931.jpg", kunden) is None
           and fotos.zuordnen("unbekannt.png", kunden) is None)
    pruefe("Fotoseite antwortet", c.get("/lebenslauf/fotos").status_code == 200)
    pruefe("Unbekannter Ordner wird gemeldet, nicht geschluckt",
           "gibt es nicht" in c.post("/lebenslauf/fotos",
                                     data={"ordner": "Z:/gibtesnicht"}).get_data(as_text=True))
    c.post(f"/kunde/{kid}/foto", data={"was": "loeschen"})
    pruefe("Foto lässt sich entfernen", fotos.foto(kid) is None)
    pruefe("Kein Trägerlogo auf dem Lebenslauf – das Papier gehört dem Bewerber",
           not any("logo" in ohne_kommentare(offen(f)).lower() for f in vorlagen_dateien()),
           [os.path.basename(f) for f in vorlagen_dateien()
            if "logo" in ohne_kommentare(offen(f)).lower()])
    # Der Umbruchschutz steht in einem der beiden gemeinsamen Rahmen; eine Vorlage,
    # die keinen einbindet, müsste ihn selbst mitbringen.
    pruefe("Jede Vorlage schützt Einträge vor dem Umbruch",
           all("break-inside: avoid" in offen(f) or "_fein.html" in offen(f)
               or "_rahmen.html" in offen(f) for f in vorlagen_dateien()),
           [os.path.basename(f) for f in vorlagen_dateien()
            if "break-inside: avoid" not in offen(f) and "_fein.html" not in offen(f)
            and "_rahmen.html" not in offen(f)])

    fehl = [n for n, ok in ergebnis if not ok]
    print(f"\n{len(ergebnis) - len(fehl)} von {len(ergebnis)} Prüfungen bestanden.")
    if fehl:
        print("Fehlgeschlagen:", *fehl, sep="\n  - ")
    return 0 if not fehl else 1


if __name__ == "__main__":
    sys.exit(main())
