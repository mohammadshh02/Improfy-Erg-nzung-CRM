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
    # Auch die Kurzform zaehlt: viele schreiben schlicht "EDV:" oder "Staerken:".
    ("edv", r"EDV[\s-]*KENNTNISS\w*|IT[\s-]*KENNTNISS\w*|COMPUTERKENNTNISS\w*|PC[\s-]*KENNTNISS\w*|EDV\b|IT[\s-]*SKILLS"),
    ("skills", r"SOFT\s*S?\s*KILLS|SOFT\s*SKILLS|KOMPETENZEN|PERSÖNLICHE\s*QUALIFIKATION|STÄRKEN\b|STAERKEN\b|PERSÖNLICHE\s*EIGENSCHAFTEN"),
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
# Der Aufzaehlungsstrich davor ist ueblich, sobald jemand den Werdegang als Liste
# schreibt ("- 07.2023-10.2023 Lagerhelfer, Amazon"). Ohne ihn zu ueberspringen, faellt
# eine solche Liste komplett durch - am 18.09.2026 an einem echten Kundentext gesehen,
# acht Stationen, keine einzige erkannt.
STATION = re.compile(
    r"^[\s\-–—•*·]*\(?\s*((?:seit\s+)?\d{1,2}\.\d{1,2}\.\d{4}|(?:seit\s+)?\d{1,2}[./]\d{4}|(?:seit\s+)?\d{4})"
    r"\s*(?:[-–—]\s*(\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}[./]\d{4}|\d{4}|heute|jetzt))?\s*\)?\s*(.*)$", re.I)
FIRMENWORT = re.compile(r"\b(GmbH|AG|KG|UG|e\.?V\.?|GbR|mbH|Ltd|Inc|Universität|Hochschule|Schule|"
                        r"Institut|Akademie|Berufskolleg|Berufsbildungswerk|Klinik|Apotheke|Praxis|"
                        r"Fabrik|Markt|Zentrum|Amt|Behörde|Parlament|Kreuz|Improfy)\b", re.I)
NIVEAU = re.compile(r"\b(muttersprach\w*|fließend|flie[sß]end|verhandlungssicher|gute\s*kenntnisse|"
                    r"grundkenntnisse|sehr\s*gut|[ABC][12])\b", re.I)
SPRACHWORT = re.compile(r"^[A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]{2,}$")
# Wörter, die direkt vor einem Niveau stehen können, ohne eine Sprache zu sein.
# „Persisch und Türkisch jeweils fließend" machte sonst aus „jeweils" eine Sprache –
# am 17.09.2026 an einem echten Kundentext aufgefallen.
# Füllwörter: Das Niveau dahinter gilt für die Sprachen **davor**
# („Persisch und Türkisch jeweils fließend").
FUELLWORT = {"JEWEILS", "BEIDE", "ALLE", "DAVON"}
# Überschriften und Floskeln, die nie eine Sprache sind.
KEINE_SPRACHE = {
    "SPRACHKENNTNISSE", "SPRACHEN", "SPRACHE", "NIVEAU", "NIVEAUS", "STUFE",
    "SOWIE", "SOWOHL", "AUCH", "UND", "ODER", "KENNTNISSE", "GRUNDKENNTNISSE",
    "MUTTERSPRACHE", "DEUTSCHKENNTNISSE", "ENGLISCHKENNTNISSE",
} | FUELLWORT
# Programme stehen im selben Satz wie ein Niveau („MS Office gute Kenntnisse") und
# rutschten so in die Sprachen. Am 17.09.2026 an einem echten Kundentext aufgefallen:
# dort standen PowerPoint und Outlook als Sprache im Lebenslauf.
KEIN_SPRACHWORT = {
    "MS", "OFFICE", "WORD", "EXCEL", "POWERPOINT", "OUTLOOK", "WINDOWS", "INTERNET",
    "EDV", "SAP", "DATEV", "HANDSCANNER", "TEAMS", "ZOOM", "PC", "COMPUTER", "SOFTWARE",
}
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


def _ist_ueberschrift(text, m):
    """Steht dieser Treffer an einer Stelle, an der eine Ueberschrift stehen kann?

    Zwei Bedingungen, beide notwendig:

    **Zeilenanfang.** Davor darf nur Leerraum oder ein Aufzaehlungszeichen stehen. Ohne
    das zaehlt jedes Vorkommen im Fliesstext - "berufliche *Weiterbildung*, Vertiefung
    der Deutschkenntnisse" hat so den Werdegang entzweigeschnitten und sechs Stationen
    verschluckt (18.09.2026 an einem echten Kundentext gesehen).

    **Abschluss.** Dahinter kommt das Zeilenende, ein Doppelpunkt oder ein Gedankenstrich.
    Eine Zeile, die mit "Ausbildung zum Kaufmann bei Firma X" beginnt, ist eine Station,
    keine Ueberschrift."""
    davor = text.rfind("\n", 0, m.start()) + 1
    if text[davor:m.start()].strip(" \t-–—•*·"):
        return False
    rest = text[m.end():]
    zeilenrest = rest.split("\n", 1)[0]
    return not zeilenrest.strip() or zeilenrest.lstrip()[:1] in (":", "-", "–", "—")


def _ohne_doppelpunkt(text):
    """Der Doppelpunkt hinter der Ueberschrift gehoert nicht zum Inhalt.

    Ohne das stand im Formular ": MS Word" statt "MS Word"."""
    text = (text or "").lstrip()
    return text[1:].lstrip() if text[:1] == ":" else text


def _zerlege(text):
    """Text in die Abschnitte der Vorlage zerlegen. Alles vor dem ersten Titel ist der Kopf."""
    treffer = [m for m in UEBERSCHRIFT.finditer(text) if _ist_ueberschrift(text, m)]
    abschnitte = {"kopf": text[:treffer[0].start()] if treffer else text}
    for i, m in enumerate(treffer):
        name = m.lastgroup
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
        inhalt = _ohne_doppelpunkt(text[m.end():ende].strip())
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


def _komma_ausserhalb_klammern(text):
    """Die Stelle des ersten Kommas, das nicht in einer Klammer steht - sonst None.

    "M.A. Europaeische Studien (Erasmus, Europa-Universitaet Flensburg)" hat sein erstes
    Komma **in** der Klammer. Dort getrennt, blieb der Abschluss als "M.A. Europaeische
    Studien (Erasmus" stehen - eine offene Klammer mitten im Lebenslauf."""
    tiefe = 0
    for i, z in enumerate(text or ""):
        if z in "([{":
            tiefe += 1
        elif z in ")]}":
            tiefe = max(0, tiefe - 1)
        elif z == "," and tiefe == 0:
            return i
    return None


def _klammertiefe(text, stelle):
    """Wie tief in Klammern steht diese Stelle? 0 heißt: frei im Satz."""
    tiefe = 0
    for z in text[:stelle]:
        if z in "([{":
            tiefe += 1
        elif z in ")]}":
            tiefe = max(0, tiefe - 1)
    return tiefe


def _sauber(text):
    """Satzzeichen am Rand abschneiden. Eine Firma heisst nicht "Universitaet Nangarhar."."""
    return (text or "").strip().strip(" ,;.-–—")


def _frei_geschrieben(rest):
    """"Titel, Firma: Taetigkeiten" in seine drei Teile zerlegen.

    Greift nur, wenn kein senkrechter Strich dasteht - sonst hat die Vorlage schon
    getrennt und jede Rateregel waere ein Rueckschritt.

    Die Reihenfolge ist absichtlich so:
      1. Der erste Doppelpunkt trennt die Taetigkeiten ab. Er steht in dieser
         Schreibweise praktisch immer dort und nirgends sonst.
      2. Danach das erste Komma: links der Titel, rechts die Firma. Weitere Kommas
         bleiben bei der Firma ("Amazon Deutschland GmbH, Duisburg").
      3. Steht kein Komma da, hilft das Firmenwort weiter ("Teilnehmer Improfy GmbH
         Koeln" -> "Teilnehmer" und "Improfy GmbH Koeln").
    Passt nichts davon, bleibt es beim ganzen Text als Firma - lieber ungetrennt als
    an der falschen Stelle geschnitten."""
    taetigkeit = ""
    if ":" in rest:
        kopf, schwanz = rest.split(":", 1)
        # Nur trennen, wenn links wirklich eine Ueberschrift steht und nicht eine
        # Uhrzeit oder ein Verhaeltnis ("Teilzeit 20:30").
        if kopf.strip() and not kopf.strip()[-1].isdigit():
            rest, taetigkeit = kopf.strip(), schwanz.strip()
    komma = _komma_ausserhalb_klammern(rest)
    if komma is not None:
        titel = _sauber(rest[:komma])
        firma = _sauber(rest[komma + 1:])
        if titel and firma:
            return titel, firma, taetigkeit
    # Auch hier gilt der Klammerschutz: In "M.A. Europäische Studien (Erasmus,
    # Europa-Universität Flensburg)" steht das Wort „Universität" **in** der Klammer.
    # Dort getrennt, blieb eine offene Klammer im Lebenslauf stehen.
    m = next((x for x in FIRMENWORT.finditer(rest)
              if _klammertiefe(rest, x.start()) == 0), None)
    if m and m.start() > 2:
        titel, firma = _sauber(rest[:m.start()]), _sauber(rest[m.start():])
        if titel and firma:
            return titel, firma, taetigkeit
    return "", _sauber(rest), taetigkeit


def _fortsetzungen_anfuegen(text):
    """Zeilen, die erkennbar die vorige fortsetzen, wieder an sie anhaengen.

    Zwei Zeichen gelten als Fortsetzung, beide fuer sich schon eindeutig genug:

      eingerueckt      Wer eine Liste schreibt, rueckt die Folgezeile ein.
      klein begonnen   Ein Satz, der mit einem Kleinbuchstaben anfaengt, hat vorher
                       angefangen.

    Eine Zeile, die selbst eine Station ist, wird nie angehaengt - sonst verschwinden
    Stationen im Text der vorigen."""
    ergebnis = []
    for roh in (text or "").splitlines():
        zeile = roh.strip()
        if not zeile:
            continue
        eingerueckt = roh[:1] in (" ", "\t") and roh.lstrip() != roh
        setzt_fort = ergebnis and not STATION.match(zeile) and (
            eingerueckt or zeile[:1].islower())
        if setzt_fort:
            ergebnis[-1] = ergebnis[-1].rstrip() + " " + zeile
        else:
            ergebnis.append(zeile)
    return "\n".join(ergebnis)


def _stationen(text, art):
    """Berufserfahrung oder Bildung: je Eintrag Zeitraum, Firma/Institution, Titel, Stichpunkte."""
    zeilen = _zeilen(_fortsetzungen_anfuegen(text))
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
            erste_taetigkeit = ""
            if not rechts:
                titel, firma, erste_taetigkeit = _frei_geschrieben(links)
            aktuell = {"zeitraum": zeitraum, "firma": firma, "jobtitel": titel,
                       "taetigkeiten": [erste_taetigkeit] if erste_taetigkeit else []}
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


def _niveau_wort(roh):
    """Aus „B2", „fließend", „Muttersprache" die Schreibweise der Vorlage machen."""
    roh = (roh or "").lower()
    if "mutter" in roh:
        return "Muttersprache"
    if "flie" in roh or "verhandlungssicher" in roh or roh.startswith("c"):
        return "fließend"
    if "gute" in roh or "sehr gut" in roh or roh.startswith("b"):
        return "gute Kenntnisse"
    return "Grundkenntnisse"


def _sprachen(text):
    """'DEUTSCH - B1  ARABISCH - Muttersprache' oder untereinander."""
    gefunden, gesehen = [], set()
    flach = re.sub(r"\s*[-–—]\s*", " - ", " ".join(_zeilen(text)))
    for m in re.finditer(r"\b([A-ZÄÖÜ][A-ZÄÖÜa-zäöüß]{2,})\b[\s:-]*"
                         r"(muttersprach\w*|fließend|flie[sß]end|verhandlungssicher|"
                         r"gute\s*kenntnisse|grundkenntnisse|sehr\s*gut|[ABC][12])", flach, re.I):
        sprache = m.group(1).upper()
        roh = m.group(2).lower()
        # „Persisch und Türkisch jeweils fließend": Vor dem Niveau steht ein Füllwort,
        # das Niveau gilt aber für die Sprachen davor. Dann rückwärts einsammeln.
        if sprache in KEIN_SPRACHWORT:
            continue                     # ein Programm, keine Sprache
        vorlauf = []
        if sprache in FUELLWORT:
            davor = flach[:m.start(1)]
            for wort in reversed(re.findall(r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]{2,}|,|und", davor)[-5:]):
                if wort in (",", "und"):
                    continue
                gross = wort.upper()
                if gross in KEINE_SPRACHE or gross in KEIN_SPRACHWORT or gross in gesehen:
                    break
                vorlauf.insert(0, gross)
                if len(vorlauf) >= 3:
                    break
            if not vorlauf:
                continue
        if sprache in KEINE_SPRACHE and not vorlauf:
            continue
        for name in (vorlauf or [sprache]):
            if name in gesehen or name in KEIN_SPRACHWORT:
                continue
            gesehen.add(name)
            gefunden.append({"sprache": name, "niveau": _niveau_wort(roh),
                             "roh": m.group(2).strip()})
        continue
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
            teil = teil.strip(" .;")
            if 2 < len(teil) <= 40 and not teil.isupper() or (teil.isupper() and len(teil) <= 20):
                if teil and teil.lower() not in [e["name"].lower() for e in eintraege]:
                    eintraege.append({"name": teil, "sterne": standard})
    return _ohne_nachsatz(eintraege)[:8]


# "MS Word, Excel, PowerPoint, Outlook, jeweils gute Kenntnisse" - das Letzte ist keine
# Faehigkeit, sondern die Bewertung der vorigen. Ungefiltert stand "jeweils gute
# Kenntnisse" als eigenes Programm im Lebenslauf.
NACHSATZ = re.compile(r"^(jeweils\s+|alle\s+|durchweg\s+|je\s+)?"
                      r"(sehr\s+gute?|gute?|grund)\s*kenntnisse$", re.I)


def _ohne_nachsatz(eintraege):
    """Ein abschliessendes Niveau-Wort entfernen und auf die Eintraege davor anwenden."""
    if not eintraege or not NACHSATZ.match(eintraege[-1]["name"].strip()):
        return eintraege
    niveau = eintraege[-1]["name"].lower()
    sterne = 5 if "sehr" in niveau else 3 if "grund" in niveau else 4
    for e in eintraege[:-1]:
        e["sterne"] = sterne
    return eintraege[:-1]


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
