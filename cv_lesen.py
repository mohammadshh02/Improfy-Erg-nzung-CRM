# -*- coding: utf-8 -*-
"""Einen vorhandenen Lebenslauf als Text einlesen und in die Felder der Vorlage zerlegen.

Gedacht für den Alltag: Der alte Lebenslauf liegt als PDF im Kundenordner oder in der
Chat-Gruppe „CVs Köln". Man öffnet ihn, markiert alles, kopiert und fügt den Text hier
ein. Das Formular ist danach ausgefüllt und muss nur noch geprüft werden.

**Warum ein eigener Leser und keine KI:** Die Improfy-Lebensläufe folgen alle derselben
Vorlage, und die ist maschinell gut zu lesen. Ein Regelwerk dafür ist verlässlich,
kostenlos, braucht keinen Schlüssel und liefert immer dasselbe Ergebnis. Bei fremd
aufgebauten Lebensläufen erkennt es weniger – dann bleibt die Handeingabe. Die KI-Auslese
aus PDF oder Foto steckt in der eigenständigen CV-App und braucht einen Anthropic-Schlüssel.

Erkannt wird der Aufbau der Improfy-Vorlage:

    <Name>
    <Geburtsdatum>  <Adresse>  <Telefon>  <E-Mail>
    ÜBER MICH            <Fließtext>
    BERUFSERFAHRUNG      (MM.YYYY - MM.YYYY) Firma | Jobtitel
                         <Tätigkeiten>
    SCHULBILDUNG         (YYYY - YYYY) Abschluss | Institution
    SPRACHKENNTNISSE     DEUTSCH - B1   ARABISCH - Muttersprache
    EDV KENNTNISSE       MS Word - Gute Kenntnisse
    SOFT SKILLS          Zuverlässigkeit, Belastbarkeit
    ZUSATZQUALIFIKATIONEN  Führerschein Klasse B

Was nicht sicher erkannt wird, bleibt leer. Lieber ein leeres Feld als ein falsches:
ein erfundener Zeitraum im Lebenslauf fällt beim Arbeitgeber auf, eine Lücke nicht.
"""
import re

# Überschriften der Vorlage. Der Schlüssel ist der Abschnitt, den wir daraus machen.
ABSCHNITTE = [
    ("ueber_mich", r"ÜBER\s*MICH|UEBER\s*MICH|ÜBER\s*MIC\b|PROFIL\b"),
    ("beruf", r"BERUFSERFAHRUNG|BERUFLICHE\s*LAUFBAH\w*|BERUFLICHER\s*WERDEGANG|BERUFSPRAXIS"),
    ("bildung", r"SCHULBILDUNG|SCHULAUSBILDUNG|AUSBILDUNG\b|BILDUNG\b|STUDIUM\b"),
    ("sprachen", r"SPRACHKENNTNISS\w*|SPRACHEN\b"),
    ("edv", r"EDV[\s-]*KENNTNISS\w*|IT[\s-]*KENNTNISS\w*|COMPUTERKENNTNISS\w*|PC[\s-]*KENNTNISS\w*"),
    ("skills", r"SOFT\s*S?\s*KILLS|SOFT\s*SKILLS|KOMPETENZEN|PERSÖNLICHE\s*QUALIFIKATION"),
    ("zusatz", r"ZUSATZQUALIFIKATION\w*|WEITERBILDUNG\w*|ZERTIFIKATE|QUALIFIKATIONEN\b"),
    ("hobbys", r"HOBBY\w*|INTERESSEN|FREIZEIT"),
]
UEBERSCHRIFT = re.compile("|".join(f"(?P<{name}>{muster})" for name, muster in ABSCHNITTE), re.I)

DATUM = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b")
TELEFON = re.compile(r"(?:\+49|0)[\d\s/()-]{8,20}\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
PLZ_ORT = re.compile(r"([A-Za-zÄÖÜäöüß.\-]+(?:str\.?|straße|weg|allee|platz|gasse|ring)\s*\d*[a-z]?)"
                     r"\s*,?\s*(\d{5})\s+([A-ZÄÖÜ][\wäöüß.\- ]+)", re.I)
# "(07.2025 - 08.2025) Firma | Jobtitel"  oder  "(seit 04.2024) Jobtitel | Firma"
STATION = re.compile(
    r"^\(?\s*((?:seit\s+)?\d{1,2}\.\d{1,2}\.\d{4}|(?:seit\s+)?\d{1,2}[./]\d{4}|(?:seit\s+)?\d{4})"
    r"\s*(?:[-–—]\s*(\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}[./]\d{4}|\d{4}|heute|jetzt))?\s*\)?\s*(.*)$", re.I)
FIRMENWORT = re.compile(r"\b(GmbH|AG|KG|UG|e\.?V\.?|GbR|mbH|Ltd|Inc|Universität|Hochschule|Schule|"
                        r"Institut|Akademie|Berufskolleg|Berufsbildungswerk|Klinik|Apotheke|Praxis|"
                        r"Fabrik|Markt|Zentrum|Amt|Behörde|Parlament|Kreuz|Improfy)\b", re.I)
NIVEAU = re.compile(r"\b(muttersprach\w*|fließend|flie[sß]end|verhandlungssicher|gute\s*kenntnisse|"
                    r"grundkenntnisse|sehr\s*gut|[ABC][12])\b", re.I)
SPRACHWORT = re.compile(r"^[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]{2,}$")
# Bewertungen stehen im Design als Sternereihe in einer eigenen Zeile.
STERN = re.compile(r"[★☆✩✭✮⭐]")
NUR_STERNE = re.compile(r"^[\s★☆✩✭✮⭐*·•]+$")


def _kopfzeilen_entfernen(text):
    """Die Vorlage wiederholt Name, Geburtsdatum, Anschrift, Telefon und Mail auf jeder Seite.
    Beim Kopieren landen diese Zeilen mitten in den Abschnitten und verfälschen sie – im
    Feld „EDV-Kenntnisse" stand sonst die Telefonnummer. Ab dem zweiten Vorkommen fliegen
    sie raus, das erste bleibt als Kopf stehen."""
    zeilen = text.split("\n")
    merkmal = []
    for muster in (TELEFON, EMAIL, DATUM):
        m = muster.search(text)
        if m:
            merkmal.append(m.group(0).strip())
    adr = PLZ_ORT.search(text)
    if adr:
        merkmal.append(adr.group(0).strip())
    gesehen, sauber = set(), []
    for zeile in zeilen:
        kern = zeile.strip()
        treffer = next((w for w in merkmal if kern and w in kern and len(kern) <= len(w) + 25), None)
        if treffer:
            if treffer in gesehen:
                continue            # Wiederholung – weglassen
            gesehen.add(treffer)
        sauber.append(zeile)
    return "\n".join(sauber)


def _zerlege(text):
    """Text in die Abschnitte der Vorlage zerlegen. Alles vor dem ersten Titel ist der Kopf."""
    treffer = list(UEBERSCHRIFT.finditer(text))
    abschnitte = {"kopf": text[:treffer[0].start()] if treffer else text}
    for i, m in enumerate(treffer):
        name = m.lastgroup
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
        inhalt = text[m.end():ende].strip()
        # Kommt eine Überschrift mehrfach vor (die Vorlage wiederholt sie je Seite),
        # gewinnt der längere Block - der kurze ist meist nur eine Wiederholung der Kopfzeile.
        if len(inhalt) > len(abschnitte.get(name, "")):
            abschnitte[name] = inhalt
    return abschnitte


def _zeilen(text):
    return [z.strip() for z in (text or "").splitlines() if z.strip()]


def _kopf(text, ganzer_text):
    """Name, Geburtsdatum, Adresse, Telefon, E-Mail aus dem Kopf der Vorlage."""
    daten = {}
    tel = TELEFON.search(ganzer_text)
    if tel:
        daten["mobil"] = re.sub(r"\s{2,}", " ", tel.group(0)).strip()
    mail = EMAIL.search(ganzer_text)
    if mail:
        daten["email"] = mail.group(0)
    geb = DATUM.search(ganzer_text)
    if geb:
        daten["geburtsdatum"] = geb.group(1)
    adr = PLZ_ORT.search(ganzer_text)
    if adr:
        daten["adresse"] = f"{adr.group(1).strip()}, {adr.group(2)} {adr.group(3).strip()}"
    # Der Name steht ganz oben und enthält keine Ziffern.
    for zeile in _zeilen(text)[:6]:
        if any(c.isdigit() for c in zeile) or "@" in zeile:
            continue
        teile = [t for t in zeile.replace("  ", " ").split() if t]
        if 2 <= len(teile) <= 5 and all(t[0].isupper() for t in teile if t[:1].isalpha()):
            daten["vorname"] = teile[0]
            daten["nachname"] = " ".join(teile[1:])
            break
    return daten


def _seite_mit_firma(paare):
    """Steht die Firma links oder rechts vom Strich? Der Abschnitt entscheidet als Ganzes.

    „Lagerhelfer | Amazon" ist für sich nicht zu entscheiden – in „Teilnehmer | Improfy
    GmbH" derselben Liste steht die Firma aber eindeutig rechts. Lebensläufe halten diese
    Reihenfolge durch, also übernimmt sie der ganze Abschnitt. Gibt "rechts", "links"
    oder None, wenn nichts eindeutig ist."""
    rechts = links = 0
    for l, r in paare:
        if not r:
            continue
        l_firma, r_firma = bool(FIRMENWORT.search(l)), bool(FIRMENWORT.search(r))
        if r_firma and not l_firma:
            rechts += 1
        elif l_firma and not r_firma:
            links += 1
    if rechts > links:
        return "rechts"
    if links > rechts:
        return "links"
    return None


def _stationen(text, art):
    """Berufserfahrung oder Bildung: je Eintrag Zeitraum, Firma/Institution, Titel, Stichpunkte."""
    zeilen = _zeilen(text)
    # Die Firmenseite einmal für den ganzen Abschnitt bestimmen (siehe _seite_mit_firma).
    paare = []
    for z in zeilen:
        m = STATION.match(z)
        if m and "|" in (m.group(3) or ""):
            l, r = (m.group(3).split("|", 1) + [""])[:2]
            paare.append((l.strip(" •-"), r.strip(" •-")))
    firmenseite = _seite_mit_firma(paare)
    stationen, aktuell = [], None
    for zeile in zeilen:
        m = STATION.match(zeile)
        if m and (m.group(2) or "seit" in (m.group(1) or "").lower() or re.match(r"^\d{4}$", m.group(1))
                  or "|" in (m.group(3) or "")):
            if aktuell:
                stationen.append(aktuell)
            von, bis, rest = m.group(1), m.group(2), (m.group(3) or "").strip(" |-–—")
            zeitraum = von if not bis else f"{von} - {bis}"
            links, rechts = (rest.split("|", 1) + [""])[:2] if "|" in rest else (rest, "")
            links, rechts = links.strip(" •-"), rechts.strip(" •-")
            # Welche Seite ist die Firma? Erst der ganze Abschnitt, dann die einzelne Zeile.
            if rechts and firmenseite == "rechts":
                firma, titel = rechts, links
            elif rechts and firmenseite == "links":
                firma, titel = links, rechts
            elif rechts and FIRMENWORT.search(rechts) and not FIRMENWORT.search(links):
                firma, titel = rechts, links
            elif rechts and ("," in rechts) and ("," not in links):
                firma, titel = rechts, links
            else:
                firma, titel = links, rechts
            aktuell = {"zeitraum": zeitraum, "firma": firma, "jobtitel": titel, "taetigkeiten": []}
            if art == "bildung":
                aktuell = {"zeitraum": zeitraum, "abschluss": titel or firma,
                           "institution": firma if titel else "", "note": ""}
        elif aktuell is not None:
            punkt = zeile.strip(" •·-")
            # „Improfy GmbH," und in der nächsten Zeile „Köln" – der Zeilenumbruch des PDF.
            feld = "institution" if art == "bildung" else "firma"
            if (aktuell.get(feld) or "").endswith(",") and len(punkt.split()) <= 3 \
                    and not re.search(r"\d", punkt) and punkt[:1].isupper():
                aktuell[feld] = aktuell[feld] + " " + punkt
                continue
            if not punkt or len(punkt) > 220:
                continue
            if len(punkt.split()) <= 3 and not re.search(r"\d", punkt) and punkt.istitle():
                continue          # abgerissener Name oder Seitenfuß, keine Tätigkeit
            if art == "bildung":
                if not aktuell["institution"]:
                    aktuell["institution"] = punkt
            elif len(aktuell["taetigkeiten"]) < 4:
                # Die Vorlage schreibt mehrere Stichpunkte gern in eine Zeile.
                for t in re.split(r"(?<=[a-zäöüß)])\s{2,}(?=[A-ZÄÖÜ])|;\s*", punkt):
                    t = t.strip(" •·-")
                    if len(t) > 3 and len(aktuell["taetigkeiten"]) < 4:
                        aktuell["taetigkeiten"].append(t)
    if aktuell:
        stationen.append(aktuell)
    return stationen[:7 if art != "bildung" else 4]


def _sprachen(text):
    """'DEUTSCH - B1  ARABISCH - Muttersprache' oder untereinander."""
    gefunden, gesehen = [], set()
    flach = re.sub(r"\s*[-–—]\s*", " - ", " ".join(_zeilen(text)))
    for m in re.finditer(r"\b([A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]{2,})\b[\s:-]*"
                         r"(muttersprach\w*|fließend|flie[sß]end|verhandlungssicher|"
                         r"gute\s*kenntnisse|grundkenntnisse|sehr\s*gut|[ABC][12])", flach, re.I):
        sprache = m.group(1).upper()
        if sprache in gesehen or sprache in ("SPRACHKENNTNISSE", "SPRACHEN", "NIVEAU"):
            continue
        gesehen.add(sprache)
        roh = m.group(2).lower()
        if "mutter" in roh:
            niveau = "Muttersprache"
        elif "flie" in roh or "verhandlungssicher" in roh or roh.startswith("c"):
            niveau = "fließend"
        elif "gute" in roh or "sehr gut" in roh or roh.startswith("b"):
            niveau = "gute Kenntnisse"
        else:
            niveau = "Grundkenntnisse"
        gefunden.append({"sprache": sprache, "niveau": niveau, "roh": m.group(2).strip()})
    if not gefunden:
        # Die Vorlage setzt die Sprachen gern nebeneinander und die Niveaus in die Zeile
        # darunter: "DEUTSCH FARSI" / "- A2 - Muttersprache". Dann paarweise zuordnen.
        namen, niveaus = [], []
        for zeile in _zeilen(text):
            treffer = NIVEAU.findall(zeile)
            if treffer:
                niveaus += [t if isinstance(t, str) else t[0] for t in treffer]
            else:
                namen += [w for w in re.split(r"[\s,/]+", zeile) if SPRACHWORT.match(w)]
        for i, name in enumerate(namen):
            roh = (niveaus[i] if i < len(niveaus) else "").lower()
            if "mutter" in roh:
                niveau = "Muttersprache"
            elif "flie" in roh or "verhandlungssicher" in roh or roh.startswith("c"):
                niveau = "fließend"
            elif "gute" in roh or "sehr gut" in roh or roh.startswith("b"):
                niveau = "gute Kenntnisse"
            elif roh:
                niveau = "Grundkenntnisse"
            else:
                niveau = ""
            gefunden.append({"sprache": name.upper(), "niveau": niveau, "roh": roh})
    # Deutsch steht laut Vorlage immer zuerst.
    gefunden.sort(key=lambda s: 0 if s["sprache"].startswith("DEUTSCH") else 1)
    return gefunden


def _liste_mit_sternen(text, standard=4):
    """Fähigkeiten mit ihrer Bewertung.

    Im Design des Designers steht die Bewertung als Sternereihe in der Zeile unter der
    Fähigkeit. Aus einem PDF kommt das als eigene Zeile „★★★★☆" zurück; ungefiltert
    stand diese Zeile als eigene Fähigkeit im Formular. Jetzt zählt sie als Bewertung
    der zuletzt gelesenen Fähigkeit."""
    eintraege = []
    for zeile in _zeilen(text):
        nur_sterne = NUR_STERNE.match(zeile)
        if nur_sterne:
            voll = zeile.count("★") or zeile.count("*")
            if eintraege and voll:
                # Mehrere Reihen nebeneinander gehören zu den zuletzt gelesenen Einträgen.
                reihen = [r for r in re.split(r"\s{2,}", zeile.strip()) if r.strip()]
                for eintrag, reihe in zip(eintraege[-len(reihen):], reihen):
                    treffer = reihe.count("★") or reihe.count("*")
                    if treffer:
                        eintrag["sterne"] = max(1, min(5, treffer))
            continue
        for teil in re.split(r"\s{2,}|,|;|•", zeile):
            teil = teil.strip(" -–—•·")
            teil = re.sub(r"\s*-\s*(gute\s*kenntnisse|grundkenntnisse|sehr\s*gut)\s*$", "", teil, flags=re.I)
            teil = STERN.sub("", teil).strip()
            if 2 < len(teil) <= 40 and not teil.isupper() or (teil.isupper() and len(teil) <= 20):
                if teil and teil.lower() not in [e["name"].lower() for e in eintraege]:
                    eintraege.append({"name": teil, "sterne": standard})
    return eintraege[:8]


def lese_lebenslauf(text):
    """Text eines Lebenslaufs → Felder der Improfy-Vorlage. Fehlendes bleibt leer."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return None, []
    a = _zerlege(_kopfzeilen_entfernen(text))
    daten = {
        "vorname": "", "nachname": "", "geschlecht": "", "angestrebter_job": "",
        "geburtsdatum": "", "mobil": "", "email": "", "adresse": "",
        "fuehrerschein": {"vorhanden": False, "klasse": "", "eu": False},
        "berufserfahrung": [], "bildung": [], "zusatzqualifikationen": [],
        "sprachen": [], "edv_kenntnisse": [], "soft_skills": [],
        "ueber_mich": "", "hobbys": "", "kunde_von": "",
    }
    daten.update(_kopf(a.get("kopf", ""), text))
    gefunden = []
    if a.get("ueber_mich"):
        daten["ueber_mich"] = re.sub(r"\n{3,}", "\n\n", a["ueber_mich"]).strip()
        gefunden.append("Über mich")
    if a.get("beruf"):
        daten["berufserfahrung"] = _stationen(a["beruf"], "beruf")
        if daten["berufserfahrung"]:
            gefunden.append(f"{len(daten['berufserfahrung'])} Stationen Berufserfahrung")
    if a.get("bildung"):
        daten["bildung"] = _stationen(a["bildung"], "bildung")
        if daten["bildung"]:
            gefunden.append(f"{len(daten['bildung'])} Einträge Bildung")
    if a.get("sprachen"):
        daten["sprachen"] = [{"sprache": s["sprache"], "niveau": s["niveau"]} for s in _sprachen(a["sprachen"])]
        if daten["sprachen"]:
            gefunden.append(f"{len(daten['sprachen'])} Sprachen")
    if a.get("edv"):
        daten["edv_kenntnisse"] = [{"programm": e["name"], "sterne": e["sterne"]}
                                   for e in _liste_mit_sternen(a["edv"])][:5]
        if daten["edv_kenntnisse"]:
            gefunden.append(f"{len(daten['edv_kenntnisse'])} EDV-Kenntnisse")
    if a.get("skills"):
        daten["soft_skills"] = [{"eigenschaft": e["name"], "sterne": 5}
                                for e in _liste_mit_sternen(a["skills"], 5)][:8]
        if daten["soft_skills"]:
            gefunden.append(f"{len(daten['soft_skills'])} Soft Skills")
    if a.get("zusatz"):
        daten["zusatzqualifikationen"] = [z for z in _zeilen(a["zusatz"]) if 3 < len(z) <= 80][:3]
        if daten["zusatzqualifikationen"]:
            gefunden.append("Zusatzqualifikationen")
    if a.get("hobbys"):
        daten["hobbys"] = ", ".join(_zeilen(a["hobbys"]))[:120]
    # Führerschein steht mal bei den Zusatzqualifikationen, mal irgendwo im Text.
    fs = re.search(r"Führerschein[^.\n]{0,40}?Klasse\s*([A-Z]{1,2}\d?)", text, re.I)
    if fs:
        daten["fuehrerschein"] = {"vorhanden": True, "klasse": fs.group(1).upper(), "eu": False}
        gefunden.append(f"Führerschein Klasse {fs.group(1).upper()}")
    elif re.search(r"\bFührerschein\b", text, re.I) and not re.search(r"kein\w*\s+Führerschein", text, re.I):
        daten["fuehrerschein"] = {"vorhanden": True, "klasse": "", "eu": False}
        gefunden.append("Führerschein")
    return daten, gefunden
