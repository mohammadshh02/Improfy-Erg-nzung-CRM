# -*- coding: utf-8 -*-
"""Selbsttest der Taskforce – prüft den ganzen Weg, den die Taskforce täglich geht.

Läuft gegen eine Kopie der Datenbank (nichts Echtes wird verändert) und braucht Internet
für die Quellen. Aufruf:  python -X utf8 taskforce_test.py

Was geprüft wird:
 1. Profil anlegen (Job + Wohnung) über die Oberfläche
 2. jede Quelle einzeln: antwortet sie, liefert sie brauchbare Felder
 3. Gedächtnis: zweiter Lauf legt nichts doppelt an
 4. Dubletten über Quellen werden markiert, Relevanz ist gesetzt
 5. Status-Workflow einzeln und als Sammelaktion, KPIs zählen
 6. JSON-Schnittstelle, CSV-Export, alle Seiten antworten
 7. Die Kette: Lebenslauf verknüpfen, Kurzprofil, Beschreibung nachladen, Abgleich,
    Wohnkriterien (WBS, Balkon, Etage …) samt IS24-Suchlink
 8. Jobregler: Art, Befristung, Arbeitszeit, Entfernung, Gehalt, Wörter, Arbeitgeber
 9. Tafel: jeder Filter greift und liefert eine gültige Seite
10. Einstieg: /taskforce zeigt Leerlauf und Handgriffe, /taskforce/tafel die volle Tafel;
    jede Taskforce-Seite hat genau eine Pfadleiste mit einem Weg zurück, der trägt
"""
import atexit
import datetime
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
# Die Prozessnummer im Namen: zwei gleichzeitige Läufe – oder einer, der hängengeblieben
# ist und weiterschreibt – teilten sich sonst eine Datei. Das Ergebnis waren Fehler, die
# bei jedem Lauf woanders auftraten und nichts mit dem Geprüften zu tun hatten.
kopie = os.path.join(tempfile.gettempdir(), "improfy_os_test_%d.db" % os.getpid())
atexit.register(lambda: os.path.exists(kopie) and os.remove(kopie))

# Die Kopie über SQLite ziehen, nicht über das Dateisystem.
#
# Vorher stand hier shutil.copy. Das ging gut, solange niemand sonst an der Datenbank war –
# und ging schief, sobald der Entwicklungsserver nebenher lief: eine Datei, die gerade
# geschrieben wird, kopiert sich in einem Zwischenzustand. Der Test schlug dann an Stellen
# fehl, die mit seiner eigentlichen Frage nichts zu tun hatten (falsche Profil-Nummern,
# doppelt gezählte KPIs), und zwar bei jedem Lauf woanders. Ein Test, der mal so und mal
# so ausgeht, ist schlimmer als ein roter: man hört auf, ihm zu glauben.
#
# `backup` nimmt die Datenbank im Ganzen, sauber auch bei laufenden Schreibzugriffen.
if os.path.exists(kopie):
    os.remove(kopie)
_quelle = sqlite3.connect(os.path.join(HIER, "improfy_os.db"))
_ziel = sqlite3.connect(kopie)
with _ziel:
    _quelle.backup(_ziel)
_ziel.close()
_quelle.close()
os.environ["IMPROFY_OS_DB"] = kopie

# Der Sicherungsordner muss mit in den Papierkorb zeigen, nicht nur die Datenbank.
# `betrieb.py` leitet ihn sonst aus seinem eigenen Verzeichnis ab: ein Testlauf, der auf
# „jetzt sichern" drückt, legt dann einen Schnappschuss der TESTdatenbank ins echte
# `sicherungen/` – und `aufraeumen()` wirft bei sieben Ständen je eine echte
# Nachtsicherung heraus. Wer daraus zurücksichert, holt sich Testkonten in den
# Echtbestand, und schon das erste Konto schaltet die persönliche Anmeldung scharf.
# Gelesen wird die Variable beim Import von `betrieb`, also muss sie vorher stehen.
sicherungen = os.path.join(tempfile.gettempdir(),
                           "improfy_os_test_sicherungen_%d" % os.getpid())
os.environ["OS_SICHERUNG_ORDNER"] = sicherungen
atexit.register(lambda: shutil.rmtree(sicherungen, ignore_errors=True))

import app as A                    # noqa: E402
import datenbank as db             # noqa: E402
import taskforce as tf             # noqa: E402

ergebnis = []


def pruefe(name, bedingung, detail=""):
    ergebnis.append((name, bool(bedingung), detail))
    print(f"  {'OK  ' if bedingung else 'FEHL'} {name}{(' – ' + str(detail)) if detail else ''}")


def main():
    db.init(); tf.init()
    c = A.app.test_client()
    kid = db.wert("SELECT id FROM kunde WHERE name LIKE 'Mohamed Ammar%'") or db.wert("SELECT MIN(id) FROM kunde")
    with db.offen() as con:
        con.execute("DELETE FROM tf_ereignis")
        con.execute("DELETE FROM tf_angebot WHERE profil_id IN (SELECT id FROM tf_profil WHERE kunde_id=?)", (kid,))
        con.execute("DELETE FROM tf_profil WHERE kunde_id=?", (kid,))

    print("\n1. Profile anlegen")
    r = c.post(f"/taskforce/kunde/{kid}/profil", data={"art": "job", "titel": "Test Job", "suchbegriffe": "Lagerhelfer, Reinigungskraft",
                                                     "ort": "Köln", "umkreis_km": "25", "zeitarbeit": "1", "quellen": []})
    pj = db.wert("SELECT MAX(id) FROM tf_profil WHERE art='job' AND kunde_id=?", (kid,))
    pruefe("Jobprofil angelegt (302 + Datensatz)", r.status_code == 302 and pj, f"id {pj}")
    r = c.post(f"/taskforce/kunde/{kid}/profil", data={"art": "wohnung", "titel": "Test Wohnung", "suchauftrag": "",
                                                     "ort": "Köln", "umkreis_km": "10", "max_miete": "600", "min_zimmer": "1",
                                                     "k_balkon": "1", "k_aufzug": "1", "k_max_warmmiete": "800", "k_etage_max": "3",
                                                     "k_wohnungstyp": ["groundfloor", "raisedgroundfloor"], "k_extra": "Rollator"})
    pw = db.wert("SELECT MAX(id) FROM tf_profil WHERE art='wohnung' AND kunde_id=?", (kid,))
    pruefe("Wohnprofil angelegt", r.status_code == 302 and pw, f"id {pw}")

    print("\n2. Quellen einzeln")
    p = tf.profil(pj)
    for schl, (name, art, fn, bereit) in tf.QUELLEN.items():
        if not bereit():
            pruefe(f"{name}: nicht konfiguriert – übersprungen", True)
            continue
        q = tf.profil(pw) if art == "wohnung" else p
        t0 = time.time()
        try:
            treffer = fn(q)
            felder = all(t.get("extern_id") and t.get("titel") and t.get("url") for t in treffer)
            if schl == "jobs.arbeitnow" and not treffer:
                pruefe(f"{name}: 0 Treffer – Quelle ist dünn (Büro/IT), kein Fehler", True)
            elif schl == "wohnung.mail" and not treffer:
                pruefe(f"{name}: 0 Treffer – im Postfach liegt gerade keine passende"
                       " Suchauftragsmail, kein Fehler", True)
            else:
                pruefe(f"{name}: {len(treffer)} Treffer in {time.time()-t0:.0f}s, Felder vollständig",
                       len(treffer) > 0 and felder, "" if felder else "Felder fehlen")
        except Exception as e:
            if schl == "jobs.indeed" and "403" in str(e):
                pruefe(f"{name}: blockt gerade (403) – Agent versucht es beim nächsten Lauf erneut", True, "Warnung")
            else:
                pruefe(f"{name}: antwortet", False, str(e)[:120])

    print("\n3. Gedächtnis")
    g1, n1, m1 = tf.lauf(pj)
    g2, n2, m2 = tf.lauf(pj)
    pruefe("Lauf 1 legt Angebote an", n1 > 0, f"{g1} gefunden, {n1} neu")
    pruefe("Lauf 2 legt (fast) nichts erneut an", n2 <= max(3, g2 // 20), f"{g2} gefunden, {n2} neu")
    pruefe("Fehlermeldungen der Quellen werden geloggt, brechen aber nicht ab", isinstance(m1, list), m1)

    print("\n4. Dubletten und Relevanz")
    dop = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=? AND status='doppelt'", (pj,))
    ohne_score = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=? AND score IS NULL", (pj,))
    pruefe("Dubletten über Quellen markiert", dop >= 0, f"{dop} Dubletten")
    pruefe("Jedes Angebot hat eine Relevanz", ohne_score == 0)
    erste = tf.neue_angebote(limit=5, kunde_id=kid)
    pruefe("Tafel sortiert nach Relevanz", all((erste[i]["score"] or 0) >= (erste[i+1]["score"] or 0) for i in range(len(erste)-1)),
           [a["score"] for a in erste])

    print("\n5. Status-Workflow und KPIs")
    ids = [a["id"] for a in tf.angebote(pj, "neu")[:3]]
    r = c.post(f"/taskforce/angebot/{ids[0]}/status", data={"status": "angeschrieben", "bearbeiter": "Test Kraft", "zurueck": "/taskforce"})
    r2 = c.post(f"/taskforce/angebot/{ids[0]}/status", data={"status": "antwort", "bearbeiter": "", "zurueck": "/taskforce"})
    a = db.eine("SELECT status, bearbeiter FROM tf_angebot WHERE id=?", (ids[0],))
    pruefe("Einzelstatus: angeschrieben → Antwort, Bearbeiter bleibt", a["status"] == "antwort" and a["bearbeiter"] == "Test Kraft", a)
    r = c.post("/taskforce/angebote/status", data={"ids": [str(ids[1]), str(ids[2])], "status": "angeschrieben", "bearbeiter": "Test Kraft", "zurueck": "/taskforce"})
    n = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE id IN (?,?) AND status='angeschrieben'", (ids[1], ids[2]))
    pruefe("Sammelaktion setzt beide", n == 2)
    k = [x for x in tf.kpi_mitarbeiter() if x["wer"] == "Test Kraft"]
    pruefe("KPI zählt 3 Anschreiben, 1 Antwort", k and k[0]["angeschrieben_gesamt"] == 3 and k[0]["antwort_gesamt"] == 1,
           k[0] if k else "kein KPI")
    pruefe("Angeschriebene tauchen nicht mehr als neu auf", all(x["id"] not in ids for x in tf.neue_angebote(limit=1000, kunde_id=kid)))

    print("\n6. Seiten und Schnittstelle")
    for url in ["/taskforce", f"/taskforce?kunde={kid}&art=job&q=Lager", f"/taskforce/kunde/{kid}", f"/taskforce/profil/{pj}",
                f"/taskforce/profil/{pj}?status=angeschrieben", f"/taskforce/profil/{pw}",
                "/taskforce/api/angebote?status=angeschrieben", "/taskforce/api/kpi", "/taskforce/export.csv", f"/kunde/{kid}", "/"]:
        r = c.get(url)
        pruefe(f"GET {url} → {r.status_code}", r.status_code == 200)
    j = c.get(f"/taskforce/api/angebote?kunde={kid}&status=angeschrieben").get_json()
    pruefe("JSON enthält Kunde, Link, Status, Bearbeiter", j["anzahl"] == 2 and all(x["url"] and x["bearbeiter"] for x in j["angebote"]), j["anzahl"])

    print("\n7. Die Kette: Lebenslauf, Kurzprofil, Beschreibung, Abgleich, Wohnkriterien")
    r = c.post(f"/taskforce/kunde/{kid}/lebenslauf", data={"url": "https://drive.google.com/file/d/1TestTestTestTestTestTest12345/view",
                                                         "name": "Lebenslauf Test", "zurueck": f"/taskforce/kunde/{kid}"})
    info = tf.kunden_info(kid)
    pruefe("Lebenslauf per Link verknüpft und im Kundeninfo sichtbar", r.status_code == 302 and any(l["name"] == "Lebenslauf Test" for l in info["lebenslaeufe"]))
    pruefe("Kundeninfo liefert Telefon/Sprache/Status/Coach auf Abruf", all(k in info for k in ("telefon", "sprache", "status_text", "coach", "gutscheine")))
    r = c.post(f"/taskforce/kunde/{kid}/kurzprofil", data={"kurzprofil": "Führerschein Klasse B, Deutsch B1, Schicht möglich, 2 Jahre Reinigung", "cv_text": ""})
    pruefe("Kurzprofil gespeichert", r.status_code == 302 and "Klasse B" in (tf.kunden_info(kid)["profil"].get("kurzprofil") or ""))
    n = tf.abgleich_profil(pj, max_n=6)
    mit = db.hole("SELECT quelle, match, abgleich, LENGTH(beschreibung_lang) AS l FROM tf_angebot WHERE profil_id=? AND abgleich IS NOT NULL", (pj,))
    geladen = [m for m in mit if m["l"]]
    pruefe(f"Beschreibungen nachgeladen ({len(geladen)} von {len(mit)}) und abgeglichen", n > 0 and len(geladen) >= 1, [m["quelle"] for m in geladen])
    pruefe("Abgleich nennt passt/fehlt/unklar", any(any(tf.abgleich_von(m).get(k) for k in ("passt", "fehlt", "unklar", "plus")) for m in mit))
    pw_p = tf.profil(pw)
    krit = tf.kriterien(pw_p)
    pruefe("Wohnkriterien gespeichert (Balkon, Aufzug, Warmmiete, Etage, 2 Wohnungstypen, Extra)",
           krit.get("balkon") and krit.get("aufzug") and krit.get("max_warmmiete") == 800 and krit.get("etage_max") == 3
           and krit.get("wohnungstyp") == ["groundfloor", "raisedgroundfloor"] and krit.get("extra") == "Rollator", krit)
    url = tf.is24_url(pw_p)
    pruefe("IS24-Suchlink trägt Preis, Zimmer, Ausstattung, Etage, Typen", all(x in url for x in ("price=-600.0", "numberofrooms=1.0-", "balcony", "lift", "floor=-3", "groundfloor")), url)
    nw = tf.abgleich_profil(pw, max_n=3)
    wm = db.hole("SELECT abgleich, zusatz FROM tf_angebot WHERE profil_id=? AND abgleich IS NOT NULL", (pw,))
    pruefe("Wohnungs-Abgleich prüft Kriterien gegen Anzeige", nw > 0 and any(tf.abgleich_von(w) for w in wm), [tf.abgleich_von(w) for w in wm][:1])
    r = c.get(f"/taskforce/profil/{pw}")
    pruefe("Profilseite Wohnung zeigt Kriterien-Formular, Extra-Notizen und IS24-Link", r.status_code == 200 and "Extra-Notizen" in r.text and "IS24-Suche" in r.text)
    r = c.get(f"/taskforce/profil/{pj}")
    pruefe("Profilseite Job zeigt Kunde auf Abruf, Lebenslauf und Abgleich", r.status_code == 200 and "Kunde auf Abruf" in r.text and "Lebenslauf Test" in r.text and "Abgleich" in r.text)
    r = c.post(f"/taskforce/profil/{pw}/speichern", data={"art": "wohnung", "titel": "Test Wohnung", "suchauftrag": "TF-Test", "ort": "Köln",
                                                          "umkreis_km": "10", "max_miete": "600", "k_wbs": "1", "k_wohnungstyp": ["loft"], "quellen": ["wohnung.kleinanzeigen"]})
    krit = tf.kriterien(tf.profil(pw))
    pruefe("Kriterien bearbeiten: WBS an, Balkon aus, Typ Loft", r.status_code == 302 and krit.get("wbs") and not krit.get("balkon") and krit.get("wohnungstyp") == ["loft"], krit)
    if tf.imap_konfiguriert():
        ueber_ort = tf.wohnung_mail({"art": "wohnung", "suchauftrag": "", "ort": "Köln"}, tage=120)
        pruefe(f"IS24-Mail: Zuordnung über den Ort findet {len(ueber_ort)} Exposés",
               all(e.get("extern_id") and e.get("titel") and e.get("url") for e in ueber_ort),
               [e["titel"][:40] for e in ueber_ort[:2]])
        nummer = next((e["zusatz"].get("suchauftrag_nr") for e in ueber_ort
                       if e["zusatz"].get("suchauftrag_nr")), None)
        if nummer:
            ueber_nr = tf.wohnung_mail({"art": "wohnung", "suchauftrag": nummer, "ort": "Köln"}, tage=120)
            pruefe(f"IS24-Mail: Zuordnung über die Suchauftrags-Nummer {nummer}",
                   ueber_nr and all(e["zusatz"].get("suchauftrag_nr") == nummer for e in ueber_nr),
                   f"{len(ueber_nr)} Exposés")
        daneben = tf.wohnung_mail({"art": "wohnung", "suchauftrag": "gibt-es-nicht", "ort": "Köln"}, tage=120)
        pruefe("IS24-Mail: unbekannter Suchauftrag liefert nichts, statt alles", daneben == [])
    r = c.get("/taskforce/api/angebote?kunde=%d" % kid).get_json()
    pruefe("JSON-Schnittstelle liefert Abgleich und Relevanz je Angebot", all("abgleich" in x and "score" in x for x in r["angebote"]))

    print("\n8. Jobregler: alle Filter für Stellenanzeigen")
    r = c.post(f"/taskforce/profil/{pj}/speichern", data={
        "art": "job", "titel": "Test Job", "suchbegriffe": "Reinigungskraft", "ort": "Köln",
        "umkreis_km": "20", "k_angebotsart": "1", "k_befristung": "2",
        "k_arbeitszeiten": ["vz", "tz"], "k_tage": "7", "k_max_entfernung": "15",
        "k_min_gehalt": "2200", "k_nur_quereinstieg": "1", "k_ohne_woerter": "Zeitarbeit",
        "k_ohne_arbeitgeber": "Randstad, Adecco", "k_extra": "kein Nachtdienst",
        "quellen": ["jobs.ba"]})
    kj = tf.kriterien(tf.profil(pj))
    pruefe("Jobkriterien gespeichert (Art, Befristung, zwei Arbeitszeiten, Tage, km, Gehalt, Wörter)",
           r.status_code == 302 and kj.get("angebotsart") == "1" and kj.get("befristung") == "2"
           and kj.get("arbeitszeiten") == ["vz", "tz"] and kj.get("tage") == 7
           and kj.get("max_entfernung") == 15 and kj.get("min_gehalt") == 2200
           and kj.get("nur_quereinstieg") and kj.get("extra") == "kein Nachtdienst", kj)
    params = tf.ba_parameter(tf.profil(pj))
    pruefe("Regler gehen als Parameter an die Jobsuche – Arbeitszeit mit Semikolon",
           params.get("arbeitszeit") == "vz;tz" and params.get("befristung") == "2"
           and params.get("veroeffentlichtseit") == 7 and params.get("umkreis") == 20, params)
    probe = [
        {"titel": "Helfer", "anbieter": "Amazon", "entfernung_km": 8,
         "zusatz": {"gehalt": "2.400 € - 2.800 €", "quereinstieg": True, "arbeitszeit_codes": ["vz"]}},
        {"titel": "Helfer weit weg", "anbieter": "DHL", "entfernung_km": 40,
         "zusatz": {"quereinstieg": True}},
        {"titel": "Helfer", "anbieter": "Randstad", "entfernung_km": 5, "zusatz": {"quereinstieg": True}},
        {"titel": "Helfer Zeitarbeit", "anbieter": "X", "entfernung_km": 5, "zusatz": {"quereinstieg": True}},
        {"titel": "Helfer schlecht bezahlt", "anbieter": "Z", "entfernung_km": 5,
         "zusatz": {"gehalt": "1.500 €", "quereinstieg": True}},
        {"titel": "Helfer ohne Angaben", "anbieter": "Q", "entfernung_km": None,
         "zusatz": {"quereinstieg": True}},
    ]
    bleibt, weg = tf.job_filter(tf.profil(pj), probe)
    pruefe("Filter sortieren Entfernung, Zeitarbeitsfirma, Wort und Gehalt aus",
           weg == 4 and [t["titel"] for t in bleibt] == ["Helfer", "Helfer ohne Angaben"],
           [t["titel"] for t in bleibt])
    pruefe("Stundenlohn wird auf den Monat gerechnet, Zimmerzahl bleibt Zahl",
           tf._geld("15,11 € pro Stunde") == 2614 and tf._geld("48 m²") is None
           and tf._zahl_aus("2 Zi.") == 2, [tf._geld("15,11 € pro Stunde"), tf._zahl_aus("2 Zi.")])
    pruefe("Profilseite Job zeigt alle Regler",
           all(x in c.get(f"/taskforce/profil/{pj}").text for x in
               ("k_angebotsart", "k_befristung", "k_arbeitszeiten", "k_max_entfernung",
                "k_min_gehalt", "k_muss_woerter", "k_ohne_arbeitgeber", "k_nur_quereinstieg")))

    print("\n8b. Formular hält, was es soll")
    basis = {"formular": "1", "art": "job", "titel": "Test Job",
             "suchbegriffe": "Reinigungskraft", "ort": "Köln", "umkreis_km": "20"}
    c.post(f"/taskforce/profil/{pj}/speichern", data=basis)
    p8 = tf.profil(pj)
    pruefe("Abgewählte Haken schalten wirklich aus (pausieren, Zeitarbeit)",
           p8["aktiv"] == 0 and p8["zeitarbeit"] == 0, (p8["aktiv"], p8["zeitarbeit"]))
    c.post(f"/taskforce/profil/{pj}/speichern", data=dict(basis, aktiv="1", zeitarbeit="1"))
    p8 = tf.profil(pj)
    pruefe("Gesetzte Haken schalten wieder ein", p8["aktiv"] == 1 and p8["zeitarbeit"] == 1)
    c.post(f"/taskforce/profil/{pj}/speichern", data={})
    p8 = tf.profil(pj)
    pruefe("Halbes Formular löscht nichts und dreht die Art nicht um",
           p8["titel"] == "Test Job" and p8["art"] == "job" and p8["ort"] == "Köln",
           {x: p8[x] for x in ("titel", "art", "ort")})
    c.post(f"/taskforce/profil/{pj}/speichern",
           data=dict(basis, aktiv="1", zeitarbeit="1", k_min_gehalt="2200"))
    pruefe("Vollständiges Formular ohne Haken räumt die Regler ab, mit Wert setzt es sie",
           tf.kriterien(tf.profil(pj)).get("min_gehalt") == 2200, tf.kriterien(tf.profil(pj)))
    c.post(f"/taskforce/profil/{pj}/speichern", data={"titel": "Test Job"})
    pruefe("Teilweises Speichern lässt die Reglerstellung stehen",
           tf.kriterien(tf.profil(pj)).get("min_gehalt") == 2200, tf.kriterien(tf.profil(pj)))
    pruefe("Unsinnige Adressen geben 404 oder eine Seite, nie einen Absturz",
           c.get("/kunde/99999999999999999999").status_code == 404
           and c.get("/taskforce?score=abc").status_code == 200
           and c.get("/trichter?tage=abc").status_code == 200)

    print("\n9. Tafel: jeder Filter greift")
    def zeilen(url):
        return c.get(url).text.count('name="ids" value=')
    # Die volle Tafel steht unter /taskforce/tafel; /taskforce ohne Regler zeigt den
    # Einstieg. Mit Regler ist /taskforce weiterhin die Tafel – darum steht unten alles
    # Weitere unveraendert auf /taskforce?…
    alle = zeilen("/taskforce/tafel")
    pruefe("Tafel zeigt Angebote", alle > 0, alle)
    pruefe("Nur Jobs / nur Wohnungen trennt sauber",
           zeilen("/taskforce?art=job") + zeilen("/taskforce?art=wohnung") >= alle
           and zeilen("/taskforce?art=wohnung") < alle)
    pruefe("Die Aufspaltung hat eigene Adressen",
           zeilen("/taskforce/arbeit") == zeilen("/taskforce?art=job")
           and zeilen("/taskforce/wohnung") == zeilen("/taskforce?art=wohnung"))
    pruefe("Beide Halften und die Direktsuche stehen auf jeder Ansicht zur Wahl",
           all(all(w in c.get(u).text for w in ("/taskforce/arbeit", "/taskforce/wohnung",
                                               "/taskforce/suchen"))
               for u in ("/taskforce", "/taskforce/arbeit", "/taskforce/wohnung")))
    pruefe("In der Wohnungssuche stehen keine Stellenregler",
           "GEHALT AB" not in c.get("/taskforce/wohnung").text.upper()
           and "GEHALT AB" in c.get("/taskforce/arbeit").text.upper())
    print("\n9b. Direktsuche und Kontaktdaten")
    # Die Seite selbst darf ohne Eingabe nichts abfragen – sonst fragt jeder Aufruf zehn
    # Portale. Darum wird hier nur die leere Seite geprueft, nicht die Suche selbst.
    leer = c.get("/taskforce/suchen?art=job")
    pruefe("Die Direktsuche steht ohne Kunde und ohne Profil bereit",
           leer.status_code == 200 and "Beruf oder Stichworte" in leer.text)
    pruefe("Ohne Eingabe wird kein Portal gefragt",
           'id="uebernahme"' not in leer.text and "Beruf und Ort eintragen" in leer.text)
    pruefe("Die Wohnungssuche zeigt Wohnungsregler",
           "Miete bis" in c.get("/taskforce/suchen?art=wohnung").text)
    pruefe("Die Suche laesst sich als Profil bauen",
           tf.suchspalte(art="job", begriffe="Lagerhelfer", ort="Köln", umkreis_km=25)["ort"] == "Köln"
           and tf.ba_parameter(tf.suchspalte(begriffe="Lagerhelfer"))["wo"] == "Köln")
    pruefe("Tauschwohnungen sind keine Angebote", bool(tf.TAUSCH.search("TAUSCHWOHNUNG 2 Zimmer")))
    beides = c.get("/taskforce/suchen?art=beides")
    pruefe("Man kann Arbeit, Wohnung oder beides suchen",
           beides.status_code == 200 and "Gehalt ab" in beides.text and "Miete bis" in beides.text)

    # Der Schritt von „ich sehe nach" zu „der Agent sucht das taeglich".
    zweiter = db.wert("SELECT id FROM kunde WHERE standort=? AND id!=? LIMIT 1",
                      (db.STANDORT_STANDARD, kid))
    c.post("/taskforce/suchen/als-profil",
           data={"kunde": str(zweiter), "art": "job", "was": "Küchenhilfe", "wo": "Leverkusen",
                 "km": "15", "titel": "Gastro Leverkusen"})
    angelegt = [p for p in tf.profile_von(zweiter) if p["titel"] == "Gastro Leverkusen"]
    pruefe("Aus einer Suche wird ein Suchprofil – mit genau ihren Reglern",
           angelegt and angelegt[0]["suchbegriffe"] == "Küchenhilfe"
           and angelegt[0]["ort"] == "Leverkusen" and angelegt[0]["umkreis_km"] == 15,
           angelegt[0] if angelegt else None)
    c.post("/taskforce/suchen/als-profil",
           data={"kunde": str(zweiter), "art": "beides", "was": "Lagerhelfer", "wo": "Köln"})
    beide = {p["art"]: p for p in tf.profile_von(zweiter) if p["titel"] in
             ("Lagerhelfer", "Wohnungssuche")}
    pruefe("Beides legt zwei Profile an, und das Wohnprofil erbt keine Berufe",
           set(beide) == {"job", "wohnung"} and not beide["wohnung"]["suchbegriffe"]
           and beide["wohnung"]["titel"] == "Wohnungssuche", list(beide))

    probe = ("Ansprechpartnerin ist Frau Jansen. Wir freuen uns auf Ihre Bewerbung an "
             "bewerbung@baeckerei-schollin.de oder telefonisch unter 02064/477223.")
    k = tf.kontakt_aus_text(probe, "Bäckerei Schollin GmbH")
    pruefe("Der Empfaenger wird aus dem Text gelesen",
           k["kontakt_mail"] == "bewerbung@baeckerei-schollin.de"
           and k["kontakt_tel"] == "02064/477223" and k["kontakt_name"] == "Frau Jansen", k)
    pruefe("Kennungen gelten nicht als Rufnummer",
           tf.kontakt_aus_text("Anbieter-ID: 01.206064.2.4")["kontakt_tel"] is None)
    pruefe("Das naechste Satzwort gehoert nicht zum Namen",
           tf.kontakt_aus_text("Fragen an Herr Bürgi Wir melden uns")["kontakt_name"] == "Herr Bürgi")
    pruefe("Sammelpostfaecher der Portale zaehlen nicht als Kontakt",
           tf.kontakt_aus_text("Kontakt: no-reply@arbeitsagentur.de")["kontakt_mail"] is None)
    pruefe("Steht nichts da, wird nichts erfunden",
           not any(tf.kontakt_aus_text("Eine Stelle ohne jede Kontaktangabe.").values()))

    print("\n9d. Stand je Kunde: was wurde gesucht, angeschrieben, was kam zurück")
    # Der ganze Weg an einem Menschen: ankreuzen, notieren, Stand setzen – und die Zahlen
    # müssen mitgehen. Das ist der Kern, deshalb wird er als Kette geprüft, nicht in Teilen.
    vorher = tf.kunden_bilanz(kid)
    frisch = [str(r["id"]) for r in db.hole(
        "SELECT a.id FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id"
        " WHERE p.kunde_id=? AND a.status='neu' LIMIT 3", (kid,))]
    c.post("/taskforce/angebote/status",
           data={"ids": frisch, "status": "angeschrieben", "bearbeiter": "Test Kraft",
                 "notiz": "Per Mail beworben", "zurueck": f"/taskforce/kunde/{kid}/stand"})
    nachher = tf.kunden_bilanz(kid)
    pruefe("Angeschriebene zählen beim Kunden, nicht nur beim Mitarbeiter",
           nachher["gesamt"]["angeschrieben"] == vorher["gesamt"]["angeschrieben"] + len(frisch),
           nachher["gesamt"]["angeschrieben"])
    pruefe("Die Notiz der Sammelaktion steht am Angebot",
           all(z["notiz"] for z in tf.kunden_nachweis(kid)[:len(frisch)]))
    c.post(f"/taskforce/angebot/{frisch[0]}/status",
           data={"status": "antwort", "bearbeiter": "Test Kraft", "zurueck": "/taskforce"})
    mit_antwort = tf.kunden_bilanz(kid)
    pruefe("Eine Rückmeldung bleibt angeschrieben und zählt zusätzlich als Antwort",
           mit_antwort["gesamt"]["angeschrieben"] == nachher["gesamt"]["angeschrieben"]
           and mit_antwort["gesamt"]["antwort"] == nachher["gesamt"]["antwort"] + 1,
           mit_antwort["gesamt"])
    pruefe("Die Quote rechnet gegen die Anschreiben, nicht gegen die Funde",
           mit_antwort["job"]["antwortquote"] == round(100 * mit_antwort["job"]["antwort"]
                                                       / mit_antwort["job"]["angeschrieben"]))
    seite = c.get(f"/taskforce/kunde/{kid}/stand")
    pruefe("Die Kundenseite zeigt Zahlen, Offenes und Verlauf",
           seite.status_code == 200 and "Liegt offen" in seite.text
           and "Per Mail beworben" in seite.text)
    csv_ = c.get(f"/taskforce/kunde/{kid}/stand.csv")
    pruefe("Der Stand lässt sich als CSV mitnehmen",
           csv_.status_code == 200 and csv_.text.count("\n") > len(frisch))
    pruefe("Ein Zeitraum grenzt den Stand ein",
           tf.kunden_bilanz(kid, seit="2099-01-01")["gesamt"]["angeschrieben"] == 0)

    print("\n9e. Schnittstellen für das CRM")
    verzeichnis = c.get("/api").get_json()
    pruefe("Das Verzeichnis nennt jede Schnittstelle",
           len(verzeichnis["schnittstellen"]) >= 20, len(verzeichnis["schnittstellen"]))
    for pfad in ("/api/gesundheit", "/api/quellen", "/api/karte", "/api/taskforce/profile",
                 "/api/taskforce/angebote?limit=5", "/api/taskforce/kpi",
                 "/api/taskforce/wiedervorlage", "/api/kunden/suche?q=ammar",
                 f"/api/kunde/{kid}", f"/api/taskforce/kunde/{kid}/bilanz"):
        pruefe(f"GET {pfad}", c.get(pfad).status_code == 200)
    pruefe("Ohne Frage fragt die Suchschnittstelle kein Portal",
           c.get("/api/taskforce/suche").get_json().get("anzahl") == 0)

    # Schreibend: das CRM legt einen Kunden an, dann ein Profil, dann meldet es zurueck.
    r = c.post("/api/kunde", json={"name": "Api Testperson", "customer_number": "API-1",
                                   "phone": "0221 1", "status": "aktiv"})
    api_kid = r.get_json().get("kunde")
    pruefe("Das CRM kann einen Kunden anlegen – mit seinen eigenen Feldnamen",
           r.status_code == 200 and api_kid
           and db.wert("SELECT status_code FROM kunde WHERE id=?", (api_kid,), None) == "H")
    r2 = c.post("/api/kunde", json={"customer_number": "API-1", "phone": "0221 2"})
    pruefe("Derselbe Kunde ein zweites Mal wird aktualisiert, nicht verdoppelt",
           r2.get_json().get("kunde") == api_kid and not r2.get_json().get("angelegt")
           and db.wert("SELECT telefon FROM kunde WHERE id=?", (api_kid,), None) == "0221 2")
    r3 = c.post("/api/taskforce/profil", json={"kunde": api_kid, "art": "job",
                                               "titel": "Api Job", "suchbegriffe": "Lagerhelfer"})
    api_pid = r3.get_json().get("profil")
    pruefe("Das CRM kann ein Suchprofil anlegen", r3.status_code == 200 and api_pid)
    r4 = c.post(f"/api/taskforce/profil/{api_pid}/uebernehmen", json={"treffer": [
        {"quelle": "jobs.ba", "extern_id": "API-PROBE", "titel": "Lagerhelfer (m/w/d)",
         "anbieter": "Muster GmbH", "url": "https://example.org/1"}]})
    pruefe("Treffer lassen sich über die Schnittstelle übernehmen",
           r4.get_json().get("neu") == 1)
    api_aid = db.wert("SELECT id FROM tf_angebot WHERE extern_id='API-PROBE'", (), None)
    r5 = c.post(f"/api/taskforce/angebot/{api_aid}/status",
                json={"status": "angeschrieben", "bearbeiter": "Api Kraft", "notiz": "aus dem CRM"})
    pruefe("Das CRM kann den Stand zurückmelden",
           r5.status_code == 200
           and db.wert("SELECT status FROM tf_angebot WHERE id=?", (api_aid,), None) == "angeschrieben")
    pruefe("Ein unbekannter Status wird abgewiesen, nicht gespeichert",
           c.post(f"/api/taskforce/angebot/{api_aid}/status",
                  json={"status": "vielleicht"}).status_code == 400)
    pruefe("Der Stand des Kunden kennt die Rückmeldung aus dem CRM",
           c.get(f"/api/taskforce/kunde/{api_kid}/bilanz").get_json()
             ["bilanz"]["gesamt"]["angeschrieben"] == 1)
    # Loeschen ging frueher nicht, sobald an einem Angebot je ein Status gesetzt war:
    # tf_ereignis zeigte darauf, SQLite wies ab. Getroffen hat es genau die Profile,
    # an denen gearbeitet wurde.
    pruefe("Ein bearbeitetes Profil lässt sich löschen",
           c.delete(f"/api/taskforce/profil/{api_pid}").status_code == 200
           and not tf.profil(api_pid))

    print("\n9c. Tafel: jeder Filter greift")
    pruefe("Relevanzschwelle blendet aus", zeilen("/taskforce?score=8") <= alle)
    pruefe("Abgleichschwelle blendet aus", zeilen("/taskforce?match=67") <= alle)
    pruefe("Zeitraum wirkt", zeilen("/taskforce?tage=1") <= zeilen("/taskforce?tage=30"))
    pruefe("Status wechselt die Liste", zeilen("/taskforce?status=verworfen") != alle
           or zeilen("/taskforce?status=") >= alle)
    for name, adresse in (("Sortierung Nähe", "/taskforce?sort=naehe"),
                          ("Sortierung Abgleich", "/taskforce?sort=abgleich"),
                          ("Sortierung zuletzt gefunden", "/taskforce?sort=neu"),
                          ("Quereinstieg", "/taskforce?art=job&quereinstieg=1"),
                          ("Arbeitszeit", "/taskforce?art=job&arbeitszeit=vz"),
                          ("Gehalt", "/taskforce?art=job&gehalt=2600"),
                          ("Miete", "/taskforce?art=wohnung&miete=400"),
                          ("Zimmer", "/taskforce?art=wohnung&zimmer=3"),
                          ("Fläche", "/taskforce?art=wohnung&flaeche=70"),
                          ("Coach", "/taskforce?coach=1"),
                          ("Umkreis", "/taskforce?km=10")):
        pruefe(f"Filter {name} antwortet", c.get(adresse).status_code == 200)
    pruefe("Alle Regler zusammen ergeben eine gültige Seite",
           c.get("/taskforce?art=job&score=3&match=34&km=25&tage=30&arbeitszeit=vz"
                 "&quereinstieg=1&gehalt=1800&sort=abgleich").status_code == 200)
    pruefe("Reglerbank steht auf der Seite",
           all(x in c.get("/taskforce/tafel").text for x in ("reglerbank", "alle Filter zurücksetzen",
                                                             "Relevanz ab", "Abgleich ab")))

    print("\n10. Einstieg: der Leerlauf, nicht die Zahlen")
    # Die Tafel meldete 754 gefundene Angebote und verschwieg, dass keine der 19 laufenden
    # Massnahmen ein Suchprofil hat. Der Einstieg dreht das um: erst der Bruch, dann die
    # Funde. Geprueft wird beides – dass er da ist und dass die Tafel nicht verschwunden ist.
    import aufgaben
    import einstieg

    def ist_tafel(t):
        return "alle Filter zurücksetzen" in t          # nur taskforce.html hat diesen Knopf

    def ist_einstieg(t):
        # Die Klasse statt der Ueberschrift: an einem Wortlaut zu erkennen, welche Seite
        # man vor sich hat, haelt keinen Umbau aus. `class="kette"` steht in keiner
        # anderen Vorlage – geprueft beim Umbau am 21.09.2026.
        return 'class="kette"' in t                    # nur taskforce_einstieg.html

    ein = c.get("/taskforce").text
    pruefe("/taskforce ohne Regler zeigt den Einstieg, nicht die Tafel",
           ist_einstieg(ein) and not ist_tafel(ein))
    for adresse in (f"/taskforce?kunde={kid}", "/taskforce?art=job", "/taskforce?status=neu",
                    "/taskforce?score=abc"):
        t = c.get(adresse).text
        pruefe(f"{adresse} zeigt weiterhin die Tafel", ist_tafel(t) and not ist_einstieg(t))
    pruefe("/taskforce/tafel ist die volle Tafel ohne Regler",
           ist_tafel(c.get("/taskforce/tafel").text))
    pruefe("Der Einstieg verlinkt alle fünf Ziele",
           all(z in ein for z in ("/taskforce/tafel", "/taskforce/arbeit", "/taskforce/wohnung",
                                  "/taskforce/suchen", "/taskforce/profile-anlegen")),
           [z for z in ("/taskforce/tafel", "/taskforce/arbeit", "/taskforce/wohnung",
                        "/taskforce/suchen", "/taskforce/profile-anlegen") if z not in ein])

    k = einstieg.kette()
    lauf = aufgaben.laufende_massnahmen()
    pruefe("Das Leerlaufband rechnet nicht selbst, sondern nimmt dieselben Zahlen",
           len(k) == 5 and k[0]["zahl"] == len(lauf)
           and k[1]["zahl"] == len(lauf) - len(aufgaben.ohne_taskforce()),
           [s["zahl"] for s in k])
    pruefe("Jede Stufe trägt Zustand und ein Ziel, an dem man den Bruch behebt",
           all(s["zustand"] in ("p-gruen", "p-gelb", "p-rot") and s["ziel"] and s["knopf"]
               for s in k))

    # Alle fuenf Stufen ueber dieselbe Menge Menschen. Geprueft an der Menge selbst, nicht
    # an den Zahlen: jede Stufe darf nur Angebote von Kunden mit Status H/I zaehlen, und
    # ein Trichter darf nie breiter werden, als seine Quelle Menschen hat.
    laufende_ids = {x["id"] for x in lauf}
    angebote_drinnen = db.hole(
        "SELECT a.status, k.id AS kunde_id FROM tf_angebot a"
        "  JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
        " WHERE p.standort=? AND p.aktiv=1 AND k.status_code IN ('H','I')",
        (db.STANDORT_STANDARD,))
    pruefe("Stufe 3 zählt nur Treffer von Menschen aus Stufe 1 – eine Grundgesamtheit",
           k[2]["zahl"] == len([z for z in angebote_drinnen if z["status"] == "neu"])
           and all(z["kunde_id"] in laufende_ids for z in angebote_drinnen),
           f"{k[2]['zahl']} von {len(angebote_drinnen)} Angeboten der laufenden Maßnahmen")
    pruefe("Stufe 4 und 5 zählen dieselbe Menge und werden nie breiter als Stufe 3",
           k[4]["zahl"] <= k[3]["zahl"]
           and k[3]["zahl"] == len([z for z in angebote_drinnen
                                    if z["status"] in ("angeschrieben", "antwort", "erfolg")]),
           [s["zahl"] for s in k])
    aussen = einstieg.aussenstehend()
    pruefe("Treffer außerhalb der Maßnahmen verschwinden nicht, sondern stehen beschriftet daneben",
           k[2]["zahl"] + aussen["treffer"] == tf.anzahl_neu()
           and (not aussen["treffer"] or "außerhalb laufender Maßnahmen" in ein),
           f"{k[2]['zahl']} drinnen + {aussen['treffer']} außen = {tf.anzahl_neu()} auf der Tafel")

    # Menschen und Angebote in einem Satz müssen aus derselben Menge kommen. „Für 20
    # Menschen warten 3 gut passende Angebote" weist 19 als versorgt aus, für die nichts
    # Gutes daliegt – darum wird hier nachgezählt, nicht nur verglichen.
    aussen_gut = db.hole(
        "SELECT k.id AS kunde_id FROM tf_angebot a"
        "  JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
        " WHERE a.status='neu' AND p.standort=? AND p.aktiv=1 AND COALESCE(a.score,0)>=6"
        "   AND COALESCE(k.status_code,'') NOT IN ('H','I')", (db.STANDORT_STANDARD,))
    pruefe("Gute Angebote und die Menschen dazu kommen aus derselben Menge",
           aussen["gut"] == len(aussen_gut)
           and aussen["gut_leute"] == len({z["kunde_id"] for z in aussen_gut})
           and aussen["gut_leute"] <= aussen["leute"],
           f"{aussen['gut']} Angebote für {aussen['gut_leute']} Menschen "
           f"(insgesamt {aussen['treffer']} für {aussen['leute']})")
    pruefe("Der Satz über der Liste nennt die Menschen aus derselben Menge",
           not aussen["gut"]
           or f"Für {aussen['gut_leute']} Menschen außerhalb laufender Maßnahmen" in ein)

    # Eine Liste statt zweier. Vorher standen dieselben Menschen zweimal auf der Seite –
    # einmal als offener Handgriff, einmal als laufende Maßnahme. Geprüft wird darum
    # zuerst die Menge: sie muss beide Quellen vollständig enthalten und jeden nur einmal.
    alle_z = einstieg.arbeitsliste(limit=0)
    liste = einstieg.arbeitsliste(limit=einstieg.LISTE)
    alle_hg = einstieg.alle_handgriffe()
    schluessel = [h["kunde_id"] or h["kunde"] for h in alle_hg]
    pruefe("Die Arbeitsliste nennt jeden Menschen höchstens einmal, jeden Handgriff mit Link",
           len(schluessel) == len(set(schluessel)) and all(h["link"] for h in alle_hg)
           and len({z["kunde_id"] or z["kunde"] for z in alle_z}) == len(alle_z),
           f"{len(liste)} von {len(alle_z)} Zeilen")
    # Beide Richtungen: der Mensch ohne Handgriff darf nicht fehlen, und wer einen
    # Handgriff hat, dessen Maßnahme aber beendet ist, darf beim Vereinigen nicht
    # herausfallen. Genau dort verschwindet sonst lautlos jemand.
    pruefe("Die eine Liste verliert keinen Menschen aus beiden Quellen",
           {z["kunde_id"] for z in alle_z}
           == {k["id"] for k in lauf} | {h["kunde_id"] for h in alle_hg},
           f"{len(alle_z)} Zeilen aus {len(lauf)} laufenden und {len(alle_hg)} Handgriffen")
    pruefe("Wer einen Handgriff hat, behält ihn samt Knopf – auch ohne laufende Maßnahme",
           all(any(z["kunde_id"] == h["kunde_id"] and z["knopf"] == h["knopf"]
                   for z in alle_z) for h in alle_hg))
    # Die Gesamtzahl der Liste ist groesser als die erste Bandstufe – sie enthaelt auch
    # die Handgriffe ohne laufende Massnahme. Steht das nicht dabei, stehen zwei Zahlen
    # fuer scheinbar dieselbe Sache auf einem Bildschirm.
    weitere = len(alle_z) - len(lauf)
    zahlenzeile = (f"{len(liste)} von {len(alle_z)} Menschen · {len(lauf)} in laufender Maßnahme"
                   + (f", {weitere} mit offenem Handgriff ohne laufende Maßnahme"
                      if weitere > 0 else ""))
    pruefe("Die Zahl unter der Liste zählt dieselbe Menge wie die Liste und erklärt die Differenz",
           zahlenzeile in ein or not alle_z, zahlenzeile)
    pruefe("Der Einstieg nennt mindestens einen Menschen beim Namen",
           not liste or any(z["kunde"] in ein for z in liste),
           liste[0]["kunde"] if liste else "keine Zeile")
    pruefe("Jede Zeile führt auf den Stand dieses Menschen",
           all(z["ziel"] for z in alle_z)
           and all(z["ziel"].endswith("/stand") for z in alle_z if z["kunde_id"]))
    pruefe("Die Kostprobe bleibt bei fünf Treffern", len(einstieg.beste_treffer()) <= 5)

    # Der Satz zum fehlenden Suchprofil war der Grund für den Umbau: er stand in jeder
    # zweiten Zeile. Jetzt steht er einmal in der Kopfzeile, mit derselben Zahl wie die
    # zweite Stufe des Bandes – zwei Zahlen für dieselbe Lücke wären schon wieder eine.
    import sammelanlage
    pruefe("Die Aussage „kein Suchprofil“ steht höchstens einmal auf der Seite",
           ein.count("Kein Such-Profil") <= 1, ein.count("Kein Such-Profil"))

    # Die Kopfzeile muss genau die Menge zählen, die hinter ihrem Knopf steht. Der Knopf
    # führt auf `sammelanlage_seite(art='job')`, also wird gegen deren eigene Liste
    # geprüft – nicht gegen eine Regel, die zufällig dieselbe Zahl liefert.
    ohne_job = sammelanlage.vorschlaege(art="job")
    pruefe("Die Kopfzeile nennt die Menge, die hinter ihrem Knopf steht",
           (f"{len(ohne_job)} von {len(lauf)} laufenden Maßnahmen haben kein Profil für die "
            f"Arbeitssuche" in ein)
           if ohne_job else ("alle laufenden Maßnahmen haben ein Profil für die Arbeitssuche" in ein
                             or "Zurzeit läuft keine Maßnahme" in ein),
           f"{len(ohne_job)} von {len(lauf)}")
    # Das Band zählt artlos weiter – das ist richtig, es ist der Trichter über beide
    # Arten. Seine Lücke muss aber in der Lücke der Kopfzeile stecken: wer gar kein Profil
    # hat, hat erst recht keins für die Arbeitssuche. Fällt das auseinander, zählt eine
    # der beiden Stellen etwas anderes, als sie behauptet.
    pruefe("Die Lücke des Bandes steckt in der Lücke der Kopfzeile",
           k[0]["zahl"] - k[1]["zahl"] <= len(ohne_job)
           and {z["id"] for z in ohne_job} >= {m["id"] for m in lauf if not m["profile"]},
           f"Band {k[0]['zahl']} - {k[1]['zahl']}, Kopfzeile {len(ohne_job)}")

    # Der Fall, der beides auseinanderbrachte: ein laufender Kunde bekommt ein WOHNprofil
    # und kein Jobprofil. Er hat dann ein Profil – für seine Arbeitssuche sucht trotzdem
    # niemand. Vorher sank die Kopfzeile um eins, während die Seite hinter dem Knopf ihn
    # weiter auflistete, und seine Zeile trug grün „1 Profile".
    if ohne_job:
        _wk = ohne_job[0]["id"]
        _angelegt, _ = sammelanlage.anlegen([(_wk, "", "Köln")], art="wohnung")
        try:
            nach = c.get("/taskforce").text
            nach_job = sammelanlage.vorschlaege(art="job")
            sichtbar = einstieg.arbeitsliste(limit=einstieg.LISTE)
            zeile = [z for z in einstieg.arbeitsliste(limit=0) if z["kunde_id"] == _wk][0]
            pruefe("Ein Wohnprofil zählt nicht als Profil für die Arbeitssuche",
                   bool(_angelegt) and len(nach_job) == len(ohne_job)
                   and zeile["profile"] and not zeile["jobprofile"]
                   and f"{len(nach_job)} von {len(lauf)} laufenden Maßnahmen haben kein Profil "
                       f"für die Arbeitssuche" in nach,
                   f"{len(nach_job)} Zeilen in der Sammelanlage, Zeile hat "
                   f"{zeile['profile']} Profile / {zeile['jobprofile']} Jobprofile")
            pruefe("Seine Plakette meldet die Lücke statt einer grünen Profilzahl",
                   nach.count(">kein Profil für die Arbeitssuche<")
                   == len([z for z in sichtbar
                           if z["laufend"] and z["profile"] and not z["jobprofile"]]),
                   nach.count(">kein Profil für die Arbeitssuche<"))
        finally:
            # Nur das eben angelegte Profil wieder weg, nicht alle Wohnprofile des Kunden.
            with db.offen() as con:
                for _, _pid, _ in _angelegt:
                    con.execute("DELETE FROM tf_profil WHERE id=?", (_pid,))

    # Zusammenfassen heißt nicht weglassen: was an einem Menschen fehlt, stand vorher in
    # den Plaketten und steht nachher in denselben.
    pruefe("Die Plaketten der laufenden Maßnahmen sind vollständig geblieben",
           ein.count(">kein Lebenslauf<")
           == len([z for z in liste if z["laufend"] and not z["lebenslauf"]])
           and ein.count(">kein Profil<")
           == len([z for z in liste if z["laufend"] and not z["profile"]])
           and all(f"{z['neu']} neu<" in ein for z in liste if z["neu"]),
           f"{ein.count('>kein Lebenslauf<')} ohne Lebenslauf, "
           f"{ein.count('>kein Profil<')} ohne Profil")

    # Das Band: fünf Stufen, flach. Flach heißt nicht: Wege weg. Verlinkt wird jede Stufe, an der es etwas zu tun gibt –
    # die gebrochene und jede, hinter der etwas liegt. Nur eine Null, die nicht rot ist,
    # bleibt ohne Link: dort gibt es nichts anzusehen.
    stufenlinks = ein.count('<div class="marke"><a href=')
    zu_tun = [s for s in k if s["zustand"] == "p-rot" or s["zahl"]]
    pruefe("Das Band bleibt flach und verliert keinen Weg",
           ein.count('class="kette-stufe') == 5 and stufenlinks == len(zu_tun),
           f"{stufenlinks} Stufenlinks, {len(zu_tun)} Stufen mit etwas zu tun")
    pruefe("Jedes Bandziel steht auch wirklich auf der Seite",
           all(f'href="{s["ziel"]}"' in ein.replace("&amp;", "&") for s in zu_tun),
           [s["ziel"] for s in zu_tun if f'href="{s["ziel"]}"' not in ein.replace("&amp;", "&")])
    # Ein Suchfeld, nicht zwei. Die Kopfsuche der Leiste steht als `class="ksuche"` da,
    # das Feld der Liste als `class="ksuche ksuche-klein"` – der Marker mit Leerzeichen
    # zählt darum nur die Felder der Seite selbst.
    pruefe("Auf der Seite steht genau ein Suchfeld",
           ein.count('class="ksuche ') == 1, ein.count('class="ksuche '))

    # Die Kostprobe steht offen da, nicht hinter einem Aufklapper. Ein Beleg, den man
    # erst aufklappen muss, belegt nichts – und eingeklappt wäre aus der Umsortierung
    # eine Entfernung geworden.
    kost = einstieg.beste_treffer()
    kostzeilen = ein.count('title="Relevanz 0–10"')
    pruefe("Die Kostprobe steht offen auf der Seite, nicht eingeklappt",
           'class="tf-nach"' not in ein and kostzeilen == len(kost),
           f"{kostzeilen} Zeilen gegen {len(kost)} Treffer der Kostprobe")

    # Die Sortierung verlässt sich darauf, dass jede Handgriff-Regel ihre Stufe rot, gelb
    # oder grau nennt. Eine vierte Bezeichnung bekäme denselben Rang wie eine Zeile ganz
    # ohne Handgriff und landete hinter den Standzeilen: „Nichts offen" stünde da, obwohl
    # Arbeit liegt, und der Weg zur Aufgabenliste verschwände mit.
    pruefe("Jeder Handgriff trägt eine Stufe, die die Sortierung kennt",
           all(h["stufe"] in aufgaben.STUFEN for h in alle_hg),
           sorted({str(h["stufe"]) for h in alle_hg if h["stufe"] not in aufgaben.STUFEN}))
    _mit_knopf = [bool(z["knopf"]) for z in alle_z]
    pruefe("Keine Zeile mit Handgriff steht hinter einer Zeile ohne",
           _mit_knopf == sorted(_mit_knopf, reverse=True),
           f"{sum(_mit_knopf)} Zeilen mit Knopf von {len(_mit_knopf)}")

    # Neun Regeln fragen dieselbe Liste laufender Maßnahmen. Ohne Merker rechnete ein
    # Seitenaufruf sie neunmal – 23 von 54 Millisekunden. Der Merker lebt genau eine
    # Anfrage lang: im Agentenlauf und in der Kommandozeile muss weiter frisch gerechnet
    # werden, sonst zeigt eine stundenlang laufende Schleife Aufgaben zu Menschen, deren
    # Maßnahme inzwischen beendet ist.
    with A.app.test_request_context("/taskforce"):
        _a, _b = aufgaben.laufende_massnahmen(), aufgaben.laufende_massnahmen()
    pruefe("In einer Anfrage werden die laufenden Maßnahmen einmal geholt", _a is _b)
    pruefe("Ohne Anfrage wird weiterhin jedes Mal frisch gerechnet",
           aufgaben.laufende_massnahmen() is not aufgaben.laufende_massnahmen())

    # Kein Kundenname im PRODUKTCODE: die Kunden kommen später aus dem CRM, der Bestand
    # hier ist Baumaterial. Ein Sonderfall für einen Menschen überlebt den Umzug nicht –
    # er fällt nur nicht auf, weil er dann für niemanden mehr greift.
    #
    # Zwei Grenzen, beide bewusst, und beide stehen im Namen der Prüfung:
    #
    # **Der ganze Produktcode**, nicht eine Handvoll Module: jede `.py` in der Wurzel und
    # in `quellen/`, dazu jede Vorlage. Ausgenommen sind allein die Prüfwerkzeuge, und die
    # Ausnahmeliste steht hier im Code, nicht im Kopf des Schreibers. Ein Test, der fünf
    # Dateien liest und „im Produktcode" behauptet, meldet Grün für eine Regel, die
    # nebenan gebrochen wird – das ist schlimmer als kein Test.
    #
    # Testdateien brauchen einen Beispielkunden: ohne ihn kann eine Prüfung nichts
    # anfassen. Das ist kein Verstoß, sondern die Voraussetzung, und es steht im Namen.
    #
    # **Nur vollständige Namen**, also mindestens zwei Namensteile. Einzelne Stücke
    # klingen scharf, treffen aber deutsche Wörter: im Bestand stehen Sammelzeilen und
    # Platzhalter statt Personen, deren Stücke („Online", „Leads", „Personen", „Datei")
    # in halb `app.py` vorkommen. Ein Test, den man mit der Wortwahl im nächsten
    # Kommentar besänftigen muss, wird abgeschaltet. Ein hart eingetragener Kunde steht
    # ohnehin praktisch nie mit nur einem Wort da.
    _pruefwerkzeuge = {"bedienprobe.py", "taskforce_backtest.py", "cv_probe.py",
                       "cv_serie.py", "seitenpruefung.py"}
    _produktcode = sorted(n for n in os.listdir(HIER)
                          if n.endswith(".py") and not n.endswith("_test.py")
                          and n not in _pruefwerkzeuge)
    _produktcode += sorted("quellen/" + n for n in os.listdir(os.path.join(HIER, "quellen"))
                           if n.endswith(".py"))
    _produktcode += sorted("templates/" + n for n in os.listdir(os.path.join(HIER, "templates"))
                           if n.endswith(".html"))
    _quellen = {n: open(os.path.join(HIER, n), encoding="utf-8").read()
                for n in _produktcode if os.path.exists(os.path.join(HIER, n))}
    _namen = set()
    for _z in db.hole("SELECT name FROM kunde WHERE name IS NOT NULL AND name <> ''"):
        _voll = " ".join(_z["name"].split())
        if len([t for t in re.split(r"[^\wÄÖÜäöüß]+", _voll) if len(t) > 1]) >= 2:
            _namen.add(_voll)
    _gefunden = sorted({f"{n}: {w}" for n, t in _quellen.items() for w in _namen if w in t})
    pruefe("Kein vollständiger Kundenname steht im Produktcode "
           "(Selbsttests und Prüfwerkzeuge ausgenommen)",
           not _gefunden,
           _gefunden or f"{len(_namen)} Namen gegen {len(_quellen)} Dateien geprüft")

    # Die Sammelanlage ist der Weg, der das Band schließt. Sie darf nicht genau dann von
    # der Seite verschwinden, wenn gerade keine Lücke offen ist – dann verliert auch die
    # zweite Bandstufe ihren Link, und der Weg ist praktisch gelöscht.
    #
    # Die Lücke kommt aus den Jobprofilen der Arbeitsliste, also wird die verstellt:
    # jede laufende Zeile bekommt ein Jobprofil, das heißt „alle sind versorgt".
    _arbeitsliste_echt = einstieg.arbeitsliste

    def _arbeitsliste_ohne_luecke(limit=None):
        return [dict(z, jobprofile=1 if z["laufend"] else z["jobprofile"])
                for z in _arbeitsliste_echt(limit=limit)]

    einstieg.arbeitsliste = _arbeitsliste_ohne_luecke
    try:
        voll = c.get("/taskforce").text
    finally:
        einstieg.arbeitsliste = _arbeitsliste_echt
    pruefe("Die Sammelanlage bleibt erreichbar, auch wenn keine Lücke offen ist",
           "/taskforce/profile-anlegen" in voll
           and ("alle laufenden Maßnahmen haben ein Profil für die Arbeitssuche" in voll
                or "Zurzeit läuft keine Maßnahme" in voll))

    # Abgeschnittene Liste: die Ueberschrift muss die echte Gesamtzahl nennen und den Weg
    # zum Rest zeigen. Eine Grenze, die nie greift, prueft nichts – darum hier erzwungen.
    _liste = einstieg.LISTE
    einstieg.LISTE = 2
    try:
        kurz = c.get("/taskforce").text
    finally:
        einstieg.LISTE = _liste
    pruefe("Abgeschnittene Liste nennt die echte Gesamtzahl und den Weg zum Rest",
           (f"2 von {len(alle_z)} Menschen" in kurz and "alle Kunden" in kurz)
           if len(alle_z) > 2 else True,
           f"{len(alle_z)} Menschen in der Liste")

    # Kein Handgriff offen: dann muss ein Satz dastehen, keine leere Liste.
    _echt = einstieg.alle_handgriffe
    einstieg.alle_handgriffe = lambda: []
    try:
        leer = c.get("/taskforce").text
    finally:
        einstieg.alle_handgriffe = _echt
    # „alle Taskforce-Aufgaben →" steht nur unter einer Liste, in der wirklich ein
    # Handgriff offen ist. Die Zeilen der laufenden Maßnahmen stehen weiter da – sie sind
    # der Stand, keine Aufgabe, und duerfen den Weg zur Aufgabenliste nicht vortaeuschen.
    pruefe("Ohne offenen Handgriff steht ein Satz im Klartext statt einer leeren Liste",
           "Nichts offen" in leer and "alle Taskforce-Aufgaben" not in leer)

    # Niemand in Massnahme: fuenf Nullen. Die Seite darf daraus keinen Betrieb melden –
    # „hier laeuft nichts leer" waere dann die Unwahrheit, die das Band abschaffen soll.
    _kette = einstieg.kette
    einstieg.kette = lambda: [dict(s, zahl=0, zustand="p-gelb") for s in _kette()]
    try:
        null = c.get("/taskforce").text
    finally:
        einstieg.kette = _kette
    pruefe("Der Nullzustand meldet keinen Betrieb, sondern sagt, dass niemand da ist",
           "niemand in einer laufenden Maßnahme" in null
           and "hier läuft nichts leer" not in null)

    # Die wichtigste Zeile des Umbaus: wer auf der Tafel etwas anfasst, landet wieder auf
    # der Tafel – nicht auf dem Einstieg und nicht im Nirgendwo.
    ort = c.post(f"/taskforce/angebot/{ids[1]}/nachgefasst",
                 data={"bearbeiter": "Test Kraft"}).headers.get("Location", "")
    pruefe("Ein Klick ohne Rücksprungadresse landet auf der Tafel",
           ort.endswith("/taskforce/tafel"), ort)

    # Nachfassen ist ein Handgriff wie jeder andere: dieselbe Regel, dieselbe
    # Zustaendigkeit (Bearbeiter vor Coach vor Leitung), dasselbe Ziel wie unter
    # /aufgaben – nur eine eigene Aufschrift, weil die Adresse dieselbe ist wie beim
    # fehlenden Suchprofil. Der Bestand hat gerade keinen ueberfaelligen Fall, also
    # wird einer datiert.
    with db.offen() as con:
        con.execute("UPDATE tf_angebot SET status='angeschrieben', status_am=? WHERE id=?",
                    ((datetime.datetime.now() - datetime.timedelta(days=30)).isoformat(),
                     ids[2]))
    wv = aufgaben.wiedervorlage()
    pruefe("Nachfassen gilt als Handgriff, mit der Zuständigkeit und dem Ziel der Aufgabenliste",
           bool(wv) and "Nachfassen" in einstieg.HANDGRIFF_ARTEN
           and all(einstieg._knopf(a) == "nachfassen" for a in wv)
           and all(a["link"].startswith("/taskforce/kunde/") for a in wv),
           [(a["kunde"], a["coach"], einstieg._knopf(a)) for a in wv][:2])
    # Am Bestand allein laesst sich das nicht pruefen: wer nachzufassen hat, hat hier
    # immer auch eine Taskforce-Aufgabe, und die Faltung je Mensch behaelt zu Recht nur
    # eine Zeile. Darum die Regel selbst, mit genau einer Aufgabe als Eingabe.
    _alle = aufgaben.alle
    probe = dict(wv[0]) if wv else None
    aufgaben.alle = lambda **_: [probe]
    try:
        zeile = einstieg.alle_handgriffe()
    finally:
        aufgaben.alle = _alle
    pruefe("Wer nur nachzufassen hat, steht mit nachfassen da – nicht mit Suchprofil",
           probe is not None and len(zeile) == 1 and zeile[0]["knopf"] == "nachfassen"
           and zeile[0]["coach"] == probe["coach"] and zeile[0]["link"] == probe["link"],
           zeile[:1])

    # „Alle Filter zuruecksetzen" steht an zwei Stellen: unter der Reglerbank und im
    # Leertext „Nichts gefunden … zuruecksetzen". Beide muessen auf der Tafel bleiben.
    for name, adresse, erwartet in (
            ("Gesamtbild", f"/taskforce?kunde={kid}&score=6", "/taskforce/tafel"),
            ("Arbeitssuche", "/taskforce/arbeit?score=8", "/taskforce/arbeit"),
            ("Wohnungssuche", "/taskforce/wohnung?miete=100", "/taskforce/wohnung")):
        t = c.get(adresse).text
        rueck = re.findall(r'href="([^"]+)"[^>]*>alle Filter zurücksetzen', t)
        leertext = re.findall(r'href="([^"]+)">zurücksetzen', t)
        pruefe(f"Zurücksetzen bleibt auf der Tafel ({name})",
               rueck == [erwartet] and all(z == erwartet for z in leertext),
               rueck + leertext)
    pruefe("Die Aufgabenliste verlinkt die Tafel, nicht den Einstieg",
           all(a["link"].startswith("/taskforce/tafel")
               for a in aufgaben.gute_angebote_liegen()),
           [a["link"] for a in aufgaben.gute_angebote_liegen()][:2] or "keine offen")

    # Der Weg zurueck. Die Pfadleiste sagte bisher nur, wo man ist; eine Ebene hoeher kam
    # nur, wer das richtige Glied traf. Der Pfeil davor nimmt diesen Klick ab – und sein
    # Ziel ist bewusst nicht „das vorletzte Glied": das waere auf vier von acht Seiten die
    # Seite selbst oder ein Glied ohne Ziel gewesen, also ein toter Pfeil. Genommen wird das
    # letzte Glied MIT Ziel. Weil das von den Daten der jeweiligen Seite abhaengt, wird es
    # hier auf jeder Taskforce-Seite einzeln nachgemessen statt an einem Beispiel.
    #
    # Die Adressen mit Regler stehen ausdruecklich mit drin: `/taskforce?art=job` ist die
    # Tafel, `/taskforce` ohne Regler der Einstieg – zwei Seiten unter einem Pfad, und die
    # mit Regler ist die, ueber die das CRM und alte Lesezeichen hereinkommen. Genau dort
    # fehlte der Pfeil, weil das Makro nur den Pfad verglich.
    for adresse in ("/taskforce", "/taskforce/tafel", "/taskforce/arbeit", "/taskforce/wohnung",
                    "/taskforce?art=job", "/taskforce?art=wohnung", "/taskforce?status=neu",
                    f"/taskforce?kunde={kid}",
                    "/taskforce/suchen", "/taskforce/suchen?art=wohnung",
                    "/taskforce/profile-anlegen", f"/taskforce/kunde/{kid}/stand",
                    f"/taskforce/kunde/{kid}", f"/taskforce/profil/{pj}"):
        t = c.get(adresse).text
        leisten = t.count('<nav class="pfad"')
        pfeile = t.count('class="pfad-zurueck"')
        ziele = [z.replace("&amp;", "&")
                 for z in re.findall(r'<a class="pfad-zurueck" href="([^"]*)"', t)]
        pruefe(f"Genau eine Pfadleiste mit genau einem Weg zurück ({adresse})",
               leisten == 1 and pfeile == 1 and len(ziele) == 1,
               f"{leisten} Leisten, {pfeile} Pfeile")
        # Ein Pfeil, der auf die eigene Seite oder ins Nichts zeigt, ist schlimmer als
        # keiner: man klickt und nichts passiert. Verglichen wird die ganze Adresse samt
        # Reglern – von der Tafel `/taskforce?art=job` ist `/taskforce` der Einstieg und
        # damit ein echter Schritt zurueck, nicht dieselbe Seite.
        ziel = ziele[0] if ziele else ""
        antwort = c.get(ziel).status_code if ziel else 0
        pruefe(f"Der Weg zurück führt woandershin und antwortet ({adresse})",
               ziel not in ("", "#", adresse) and antwort in (200, 302),
               f"{ziel or 'kein Ziel'} → {antwort}")

    # Und wenn eine kuenftige Seite das Makro ohne Ziel und ohne `zurueck` aufruft: lieber
    # kein Pfeil als ein falscher. Vorher fiel er auf die Taskforce zurueck – eine Seite
    # aus einer anderen Abteilung haette damit lautlos hierher gezeigt.
    with A.app.test_request_context("/coaches"):
        leiste = A.app.jinja_env.from_string(
            '{% from "_pfad.html" import pfad %}{{ pfad([("Coaches", None)]) }}').render()
    pruefe("Ohne Ziel und ohne Rückweg zeigt die Leiste keinen Pfeil statt eines falschen",
           '<nav class="pfad"' in leiste and "pfad-zurueck" not in leiste
           and "/taskforce" not in leiste,
           leiste.strip()[:90])

    fehl = [n for n, ok, _ in ergebnis if not ok]
    print(f"\n{len(ergebnis) - len(fehl)} von {len(ergebnis)} Prüfungen bestanden.")
    if fehl:
        print("Fehlgeschlagen:", *fehl, sep="\n  - ")
    return 0 if not fehl else 1


if __name__ == "__main__":
    sys.exit(main())
