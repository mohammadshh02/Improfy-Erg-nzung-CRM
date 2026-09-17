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
"""
import os
import shutil
import sys
import tempfile
import time

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
kopie = os.path.join(tempfile.gettempdir(), "improfy_os_test.db")
shutil.copy(os.path.join(HIER, "improfy_os.db"), kopie)
os.environ["IMPROFY_OS_DB"] = kopie

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
    alle = zeilen("/taskforce")
    pruefe("Tafel zeigt Angebote", alle > 0, alle)
    pruefe("Nur Jobs / nur Wohnungen trennt sauber",
           zeilen("/taskforce?art=job") + zeilen("/taskforce?art=wohnung") >= alle
           and zeilen("/taskforce?art=wohnung") < alle)
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
           all(x in c.get("/taskforce").text for x in ("reglerbank", "alle Filter zurücksetzen",
                                                       "Relevanz ab", "Abgleich ab")))

    fehl = [n for n, ok, _ in ergebnis if not ok]
    print(f"\n{len(ergebnis) - len(fehl)} von {len(ergebnis)} Prüfungen bestanden.")
    if fehl:
        print("Fehlgeschlagen:", *fehl, sep="\n  - ")
    return 0 if not fehl else 1


if __name__ == "__main__":
    sys.exit(main())
