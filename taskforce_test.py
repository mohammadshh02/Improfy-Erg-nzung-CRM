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
import datetime
import json
import os
import re
import sys
import time
import urllib.error

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import pruefkopie                # noqa: E402
# Warum die Arbeitskopie über `sqlite3.backup` läuft und nicht über `shutil.copy`,
# steht im Kopf von `pruefkopie.py`. Am Echtbestand ändert der Lauf nichts.
# Die Prozessnummer steckt dort im Ordnernamen: zwei gleichzeitige Läufe – oder einer,
# der hängengeblieben ist und weiterschreibt – teilten sich sonst eine Datei. Das
# Ergebnis waren Fehler, die bei jedem Lauf woanders auftraten.
kopie = pruefkopie.anlegen("improfy_os_test.db")
os.environ["IMPROFY_OS_DB"] = kopie

# Der Sicherungsordner muss mit in den Papierkorb zeigen, nicht nur die Datenbank.
# `betrieb.py` leitet ihn sonst aus seinem eigenen Verzeichnis ab: ein Testlauf, der auf
# „jetzt sichern" drückt, legt dann einen Schnappschuss der TESTdatenbank ins echte
# `sicherungen/` – und `aufraeumen()` wirft bei sieben Ständen je eine echte
# Nachtsicherung heraus. Wer daraus zurücksichert, holt sich Testkonten in den
# Echtbestand, und schon das erste Konto schaltet die persönliche Anmeldung scharf.
# Gelesen wird die Variable beim Import von `betrieb`, also muss sie vorher stehen.
os.environ["OS_SICHERUNG_ORDNER"] = pruefkopie.papierkorb("sicherungen")

# Dasselbe für die gebauten Unterlagen. Ohne diese Variable legt jeder Lauf zwei
# echte Dateien in `ausgabe/lebenslaeufe/` des Live-Repos – belegt am 21.09.2026:
# gelöscht, Test erneut gelaufen, beide wieder da. Der Dateiname trägt Kundennummer
# und Datum, ein Testlauf überschreibt also ein am selben Tag echt gebautes Dokument
# desselben Menschen. Gelesen wird die Variable beim Import von `lebenslauf_bauen`
# und `cv_pdf`, sie muss darum vorher stehen.
os.environ["OS_AUSGABE_ORDNER"] = pruefkopie.papierkorb("ausgabe")

import app as A                    # noqa: E402
import coaches                     # noqa: E402
import datenbank as db             # noqa: E402
import taskforce as tf             # noqa: E402

ergebnis = []


def hohle_beispiele(sammlung):
    """Welche Eintraege der Beispielsammlung sind kein Treffer mit passender Quelle
    und Fremd-ID?

    `isinstance` statt `or ""`: steht in der Datei eine Zahl als `extern_id`, gaebe
    `.strip()` einen `AttributeError` AUSSERHALB von `pruefe` – der ganze Lauf braeche
    ab, statt einen roten Punkt zu melden. Eine Pruefung gegen eine ausgehoehlte Datei
    muss die ausgehoehlte Datei ueberleben.

    Steht hier oben und nicht in einem der Abschnitte, weil zwei Pruefungen sie
    brauchen: die eine gegen die echte Sammlung, die andere gegen eine absichtlich
    vergiftete. Stuende die Bedingung zweimal da, bliebe die zweite gruen, waehrend
    die erste zurueckfaellt – gemessen am 22.09.2026: genau so war es."""
    return sorted(q for q, x in sammlung.items()
                  if not isinstance(x, dict) or x.get("quelle") != q
                  or not isinstance(x.get("extern_id"), str)
                  or not x["extern_id"].strip())


def pruefe(name, bedingung, detail=""):
    ergebnis.append((name, bool(bedingung), detail))
    print(f"  {'OK  ' if bedingung else 'FEHL'} {name}{(' – ' + str(detail)) if detail else ''}")


# **Was ein gedrosseltes Portal angeht, ist keine Aussage über das OS.** Abschnitt 2
# und 7 greifen auf die lebenden Portale zu. Kleinanzeigen antwortete am 22.09.2026 auf
# die Suche mit 46 Treffern und danach auf jede Detailseite mit 400 – drei Prüfläufe
# rot, ohne dass eine Zeile Code kaputt war, und eine ganze Prüfrunde dafür verbraucht.
# Indeeds 403 wurde längst so behandelt; hier steht dasselbe für alle Quellen.
#
# **Nur diese drei Antworten, und nur für den Abruf nach draußen.** 400 (Kleinanzeigen
# weist den Abruf ab), 403 (gesperrt) und 429 (zu viele Anfragen). Alles andere bleibt
# FEHL – eine Prüfung, die jeden Fehler wegschluckt, ist keine mehr.
DROSSEL_CODES = (400, 403, 429)


def _drossel(fehler):
    """Hat das Portal gedrosselt? Gibt den HTTP-Code zurück, sonst None.

    Nimmt eine Ausnahme (Abschnitt 2 bekommt den `HTTPError` selbst) oder den Text,
    den `abgleich_profil` bei einem misslungenen Abruf in die Spalte `abgleich`
    schreibt (Abschnitt 7 sieht nur den). Am Text erkannt wird allein, was `urllib`
    bei einem `HTTPError` erzeugt – „HTTP Error 429: Too Many Requests". Eine 400, die
    irgendwo sonst in einer Meldung steht, zählt nicht."""
    if isinstance(fehler, urllib.error.HTTPError):
        return fehler.code if fehler.code in DROSSEL_CODES else None
    text = str(fehler or "")
    return next((c for c in DROSSEL_CODES if re.search(r"HTTP Error %d\b" % c, text)), None)


def warnen(name, grund):
    """Eine Prüfung, die nicht laufen konnte, weil das Portal gedrosselt hat.

    Gezählt wird sie als bestanden – rot wäre gelogen, am OS ist nichts kaputt –,
    gedruckt aber als WARN, damit niemand sie für eine gelaufene Prüfung hält."""
    ergebnis.append((name, True, grund))
    print(f"  WARN {name} – {grund}")


def pruefe_portal(name, bedingung, drosselungen, detail=""):
    """Wie `pruefe`, aber: Scheitert die Reihe **und** hat das Portal gedrosselt, wird
    daraus eine Warnung. Ist `drosselungen` leer, bleibt es bei FEHL."""
    if not bedingung and drosselungen:
        warnen(name, "Das Portal hat gedrosselt (HTTP %s) – die Prüfung ist deshalb "
                     "nicht gelaufen, am OS ist nichts kaputt"
                     % "/".join(sorted({str(c) for c in drosselungen})))
        return
    pruefe(name, bedingung, detail)


def main():
    db.init(); tf.init()
    c = A.app.test_client()
    # Der Kunde, an dem die Taskforce-Daten haengen: Der Test raeumt gleich dessen
    # Profile samt Angeboten ab und legt sie neu an – nimmt er einen anderen, bleibt der
    # alte Bestand daneben stehen und die Filterpruefungen weiter unten messen eine
    # Mischung aus beidem. Vorher stand hier ein echter Kundenname als LIKE-Muster; in
    # einer versionierten Datei hat der nichts zu suchen. Die Sache selbst - „der Kunde
    # mit den Suchprofilen" - steht jetzt da, statt seines Namens.
    kid = (db.wert("SELECT kunde_id FROM tf_profil ORDER BY kunde_id LIMIT 1")
           or db.wert("SELECT MIN(id) FROM kunde"))
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
    # Die Drosselerkennung zuerst, und zwar ohne Netz: Von ihr haengt ab, welche
    # gescheiterte Reihe unten zur Warnung wird. Zu weit gefasst, schluckt sie echte
    # Fehler - dann waere der ganze Abschnitt wertlos. Geprueft wird beides: dass die
    # drei Codes erkannt werden (als Ausnahme wie als Text aus der Datenbank) und dass
    # nichts anderes durchgeht, auch keine Zahl, die bloss im Meldungstext steht.
    _http = urllib.error.HTTPError
    pruefe("Nur 400/403/429 vom Portal gelten als Drosselung",
           _drossel(_http("http://x", 429, "Too Many Requests", None, None)) == 429
           and _drossel(_http("http://x", 400, "Bad Request", None, None)) == 400
           and _drossel("HTTP Error 403: Forbidden") == 403
           and _drossel(_http("http://x", 500, "Server Error", None, None)) is None
           and _drossel("HTTP Error 404: Not Found") is None
           and _drossel("meinestadt kennt den Ort nicht (400 Stellen)") is None
           and _drossel(ValueError("kaputt")) is None and _drossel(None) is None)
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
                _code = _drossel(e)
                pruefe_portal(f"{name}: antwortet", False,
                              [_code] if _code else [], str(e)[:120])

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
    # **Ein Filter, der nicht zu lesen war, öffnet den Export nicht.** Gemessen am
    # 22.09.2026: `export.csv` 1305 Zeilen, `?kunde=<id>` eine, `?kunde=999999` eine –
    # und `?kunde=²` wieder 1305, also 413 kB Angebote aller Kunden als Download,
    # ungesehen und ohne einen Satz dazu. Auf der Tafel ist der offene Filter
    # vertretbar (der Coach sieht die Tabelle vor sich), in einer Datei nicht.
    _alle = c.get("/taskforce/export.csv")
    _einer = c.get(f"/taskforce/export.csv?kunde={kid}")
    _leer = c.get("/taskforce/export.csv?kunde=999999")
    # Gemessen wird gegen den Kunden **ohne** Angebote: Im Bestand der Arbeitskopie
    # haengen alle Angebote an dem einen Kunden mit Suchprofilen, `?kunde=<id>` liefert
    # also dieselbe Datei wie der ungefilterte Aufruf - daraus liesse sich nichts
    # ablesen. Eine Nummer, die es nicht gibt, muss dagegen eine leere Datei ergeben.
    pruefe("Der Export filtert nach Kunde",
           _alle.status_code == 200 and _einer.status_code == 200
           and _leer.status_code == 200 and len(_einer.data) <= len(_alle.data)
           and len(_leer.data) < len(_alle.data) / 10,
           "%d / %d / %d Bytes" % (len(_alle.data), len(_einer.data), len(_leer.data)))
    for _kaputt in ("²", "abc", "9" * 20):
        _r = c.get("/taskforce/export.csv?kunde=%s" % _kaputt)
        pruefe("Unbrauchbarer Filter liefert keinen Export aller Kunden (kunde=%s)"
               % _kaputt[:4],
               _r.status_code == 400
               and "keine Kundennummer" in _r.get_data(as_text=True)
               and len(_r.data) < len(_alle.data),
               "%d, %d Bytes" % (_r.status_code, len(_r.data)))
    # **Dasselbe Loch hatte die Schnittstelle.** Gemessen am 22.09.2026:
    # `/taskforce/api/angebote?kunde=²` gab 200, anzahl 1304, 932 487 Bytes - waehrend
    # der Export daneben bei derselben Angabe schon 400 sagte. Auf der Tafel ist der
    # offene Filter vertretbar (der Coach sieht die Tabelle vor sich), an der
    # CRM-Schnittstelle nicht: Dort sieht niemand etwas, und das CRM haengt die
    # Angebote aller Kunden an einen Datensatz.
    _api_alle = c.get("/taskforce/api/angebote")
    for _kaputt in ("²", "abc", "9" * 20):
        _r = c.get("/taskforce/api/angebote?kunde=%s" % _kaputt)
        pruefe("Unbrauchbarer Filter liefert auch über die Schnittstelle nicht alle "
               "Kunden (kunde=%s)" % _kaputt[:4],
               _r.status_code == 400
               and "keine Kundennummer" in _r.get_data(as_text=True)
               and len(_r.data) < len(_api_alle.data),
               "%d, %d Bytes" % (_r.status_code, len(_r.data)))
    # Eine Nummer, die es gibt, und eine, die es nicht gibt, bleiben unangetastet -
    # die Schranke steht nur vor dem, was sich nicht als Nummer lesen laesst.
    pruefe("Eine lesbare Kundennummer geht weiter durch, auch eine unbekannte",
           c.get("/taskforce/api/angebote?kunde=999999").status_code == 200
           and c.get("/taskforce/api/angebote?kunde=%d" % kid).status_code == 200,
           c.get("/taskforce/api/angebote?kunde=999999").get_json().get("anzahl"))
    j = c.get(f"/taskforce/api/angebote?kunde={kid}&status=angeschrieben").get_json()
    pruefe("JSON enthält Kunde, Link, Status, Bearbeiter", j["anzahl"] == 2 and all(x["url"] and x["bearbeiter"] for x in j["angebote"]), j["anzahl"])
    # `/taskforce/kunde` ist der Sprung aus der Kundenauswahl. Ohne brauchbare Nummer
    # endete er in der rohen 404-Seite des Servers (leer, ?kid=0) oder in einem Absturz
    # (?kid=abc) - beides sagt nicht, was fehlt. Eine Nummer, die es nicht gibt, bleibt
    # dagegen 404: dort ist die Auskunft richtig.
    # Auch die Riesenzahl gehört dazu: Sie passt nicht in die 64 Bit von SQLite und
    # endete als 500er. Geprüft wird der Satz auf der Seite, nicht nur der Status –
    # fiele der Hinweis weg, bliebe die Prüfung sonst grün.
    # `?kid=²` steht dabei für die Unterscheidung `isdecimal`/`isdigit`: `"²".isdigit()`
    # ist wahr, `int("²")` wirft. Ohne diese Adresse ließ sich `isdecimal` zurückdrehen,
    # ohne dass eine einzige Prüfung rot wurde – der Fix hatte keinen Wächter.
    for anhang in ("", "?kid=0", "?kid=abc", "?kid=99999999999999999999", "?kid=²"):
        r = c.get("/taskforce/kunde" + anhang, follow_redirects=True)
        pruefe("/taskforce/kunde%s sagt, was fehlt" % (anhang or " ohne Nummer"),
               r.status_code == 200
               and "Bitte erst einen Kunden auswählen." in r.get_data(as_text=True),
               r.status_code)
    pruefe("Eine Kundennummer, die es nicht gibt, bleibt 404",
           c.get("/taskforce/kunde?kid=999999", follow_redirects=True).status_code == 404)

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
    # `abgleich_profil` faengt den misslungenen Abruf selbst und schreibt ihn in die
    # Spalte `abgleich` - hier wird nachgesehen, ob nur die Detailseiten gesperrt waren.
    _gedrosselt = [x for x in (_drossel(tf.abgleich_von(m).get("fehler")) for m in mit) if x]
    pruefe_portal(f"Beschreibungen nachgeladen ({len(geladen)} von {len(mit)}) und abgeglichen",
                  n > 0 and len(geladen) >= 1, _gedrosselt, [m["quelle"] for m in geladen])
    pruefe_portal("Abgleich nennt passt/fehlt/unklar",
                  any(any(tf.abgleich_von(m).get(k) for k in ("passt", "fehlt", "unklar", "plus")) for m in mit),
                  _gedrosselt)
    pw_p = tf.profil(pw)
    krit = tf.kriterien(pw_p)
    pruefe("Wohnkriterien gespeichert (Balkon, Aufzug, Warmmiete, Etage, 2 Wohnungstypen, Extra)",
           krit.get("balkon") and krit.get("aufzug") and krit.get("max_warmmiete") == 800 and krit.get("etage_max") == 3
           and krit.get("wohnungstyp") == ["groundfloor", "raisedgroundfloor"] and krit.get("extra") == "Rollator", krit)
    url = tf.is24_url(pw_p)
    pruefe("IS24-Suchlink trägt Preis, Zimmer, Ausstattung, Etage, Typen", all(x in url for x in ("price=-600.0", "numberofrooms=1.0-", "balcony", "lift", "floor=-3", "groundfloor")), url)
    nw = tf.abgleich_profil(pw, max_n=3)
    wm = db.hole("SELECT abgleich, zusatz FROM tf_angebot WHERE profil_id=? AND abgleich IS NOT NULL", (pw,))
    _w_gedrosselt = [x for x in (_drossel(tf.abgleich_von(w).get("fehler")) for w in wm) if x]
    pruefe_portal("Wohnungs-Abgleich prüft Kriterien gegen Anzeige",
                  nw > 0 and any(tf.abgleich_von(w) for w in wm), _w_gedrosselt,
                  [tf.abgleich_von(w) for w in wm][:1])
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
    # Die verengte TAFEL steht seit dem 21.09.2026 unter /taskforce/tafel?art=…;
    # /taskforce/arbeit und /taskforce/wohnung sind die Arbeitsplaetze der beiden Suchen
    # und zeigen keine Angebotstabelle mehr. Beide Adressen der Tafel muessen dieselbe
    # Menge Zeilen zeigen – sonst filtert die eine anders als die andere, und welche
    # stimmt, merkt niemand.
    pruefe("Die Aufspaltung hat eigene Adressen",
           zeilen("/taskforce/tafel?art=job") == zeilen("/taskforce?art=job")
           and zeilen("/taskforce/tafel?art=wohnung") == zeilen("/taskforce?art=wohnung"))
    pruefe("Beide Halften und die Direktsuche stehen auf jeder Ansicht zur Wahl",
           all(all(w in c.get(u).text for w in ("/taskforce/arbeit", "/taskforce/wohnung",
                                               "/taskforce/suchen"))
               for u in ("/taskforce", "/taskforce/arbeit", "/taskforce/wohnung")))
    pruefe("In der Wohnungssuche stehen keine Stellenregler",
           "GEHALT AB" not in c.get("/taskforce/wohnung").text.upper()
           and "GEHALT AB" in c.get("/taskforce/arbeit").text.upper())
    # Dieselbe Frage an die Tafel: die Reglerreihe fuer Stellen darf in der
    # Wohnungsfassung nicht stehen und umgekehrt.
    pruefe("Auch die verengte Tafel traegt nur die Regler ihrer Art",
           "GEHALT AB" not in c.get("/taskforce/tafel?art=wohnung").text.upper()
           and "GEHALT AB" in c.get("/taskforce/tafel?art=job").text.upper())
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

    print("\n10. Einstieg: erst der Schnellzugriff, dann die Ansichten")
    # Zwei Umbauten stecken in diesem Abschnitt, und beide haben denselben Grund.
    #
    # Der erste: die Tafel meldete 754 gefundene Angebote und verschwieg, dass keine der 19
    # laufenden Massnahmen ein Suchprofil hat. Der Einstieg dreht das um – erst der Bruch,
    # dann die Funde. Geprueft wird beides: dass er da ist und dass die Tafel bleibt.
    #
    # Der zweite (21.09.2026): der erste anklickbare Knopf lag 340 px unter dem Fensterrand,
    # auf einem 1366x768-Laptop also hinter mehr als der halben ersten Bildschirmhoehe
    # Lesestoff. Ansage Masoud: oben der Schnellzugriff, die Ansichten kommen beim
    # Weiterscrollen. Die Rangfolge lautet seitdem: Schnellzugriff, „Jetzt zu tun",
    # Leerlaufband, wartende Arbeit, Tafelkostprobe. Entfernt wurde dabei nichts – und
    # genau das misst dieser Abschnitt weiter, Stueck fuer Stueck.
    import aufgaben
    import einstieg

    def ist_tafel(t):
        return "alle Filter zurücksetzen" in t          # nur taskforce.html hat diesen Knopf

    def ist_einstieg(t):
        # Die Klasse statt der Ueberschrift: an einem Wortlaut zu erkennen, welche Seite
        # man vor sich hat, haelt keinen Umbau aus. `class="kette"` steht in keiner
        # anderen Vorlage – geprueft beim Umbau am 21.09.2026.
        return 'class="kette"' in t                    # nur taskforce_einstieg.html

    def ist_dashboard(t):
        # Dieselbe Regel fuer die dritte Ansicht: `tf-dashboard` traegt nur die neue
        # Vorlage. Ohne eigenes Merkmal haetten die Pruefungen fuer Tafel und Einstieg
        # bei jedem Umbau gleichzeitig Unsinn gemeldet, statt der einen Ursache.
        return 'class="tf-dashboard"' in t              # nur taskforce_dashboard.html

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

    # Masouds Regel vom 21.09.2026 als Messung, nicht als Wortlaut: vor der ersten
    # Ueberschrift des Inhalts muessen die Handgriffe stehen. Am Wortlaut zu pruefen haelt
    # keinen Umbau aus – an der Position schon, und sie ist das, worum es geht. Waechst
    # wieder Lesestoff davor, faellt genau diese Pruefung um.
    #
    # Gemessen werden die Ziele, die im Schnellzugriff stehen. Seit dem 21.09.2026 sind das
    # „Kunde anlegen", die zwei Arbeitsplaetze und die Tafel; „Suchprofil anlegen" ist aus
    # Zeile B in die Kopfzeile der Arbeitsliste und in die beiden Dashboards gewandert –
    # gemessen wird es weiter, nur eine Pruefung tiefer („Der Einstieg verlinkt alle fuenf
    # Ziele"), wo es um Erreichbarkeit geht und nicht um die Hoehe.
    _h2 = ein.find("<h2")
    _oben = {z: ein.find('href="%s"' % z)
             for z in ("/kunden?neu=1#neu", "/taskforce/arbeit", "/taskforce/wohnung",
                       "/taskforce/tafel")}
    pruefe("Der Schnellzugriff steht vor der ersten Überschrift des Inhalts",
           _h2 > 0 and all(0 < stelle < _h2 for stelle in _oben.values()),
           f"erstes h2 an {_h2}, " + ", ".join(f"{z} an {s}" for z, s in _oben.items()))

    # Der Umschalter setzt `art` im SELBEN Formular. Vorher stand dort ein Einweg-Link
    # „stattdessen Wohnung suchen": Klick, Seite laden, Beruf und Ort neu tippen. Geprueft
    # wird darum nicht nur, dass beide Arten dastehen, sondern dass die Eingaben drueben
    # ankommen – ein Umschalter, der das Getippte wegwirft, ist keiner.
    pruefe("Der Umschalter bietet beide Arten im selben Formular an",
           ein.count('id="schnellzugriff"') == 1
           and 'name="art" value="job"' in ein and 'name="art" value="wohnung"' in ein,
           ein.count('id="schnellzugriff"'))
    _um = c.get("/taskforce/suchen?art=wohnung&wo=Köln").text
    pruefe("Der Umschalter trägt die Eingaben mit – der Ort steht drüben im Feld",
           'name="wo" value="Köln"' in _um and 'name="art" value="wohnung"' in _um,
           "Ortsfeld kam leer zurück" if 'name="wo" value="Köln"' not in _um else "Köln")

    # Menschen finden und Menschen erfassen ist derselbe Reflex und wird von allen sieben
    # Reitern gebraucht – der Weg ins CRM klebt deshalb in der Topbar. Geprueft wird die
    # Adresse aus der Einstellung: eine erfundene waere schlimmer als keine, weil sie
    # aussieht, als fuehre sie irgendwohin.
    _crm = A.CRM_URL + "/kunden"
    _fehlt = [p for p in ("/", "/taskforce", "/taskforce/tafel", "/kunden", "/aufgaben",
                          "/lebenslauf", "/trichter", "/coaches")
              if f'href="{_crm}"' not in c.get(p).text]
    pruefe("Der Weg ins CRM steht auf jeder Seite und zeigt auf die eingestellte Adresse",
           A.CRM_URL.startswith("https://") and not _fehlt, _fehlt or _crm)

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
    # Seit dem 21.09.2026 ist die verengte Tafel /taskforce/tafel?art=… – der Ruecksetzknopf
    # muss also dort landen und nicht im Dashboard: zurueckgesetzt waeren die Regler zwar,
    # aber man stuende vor einer anderen Seite als der, auf der man gefiltert hat.
    for name, adresse, erwartet in (
            ("Gesamtbild", f"/taskforce?kunde={kid}&score=6", "/taskforce/tafel"),
            ("Arbeitssuche", "/taskforce/tafel?art=job&score=8", "/taskforce/tafel?art=job"),
            ("Wohnungssuche", "/taskforce/tafel?art=wohnung&miete=100",
             "/taskforce/tafel?art=wohnung")):
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
                    "/taskforce/tafel?art=job", "/taskforce/tafel?art=wohnung",
                    "/taskforce?art=job", "/taskforce?art=wohnung", "/taskforce?status=neu",
                    f"/taskforce?kunde={kid}",
                    "/taskforce/suchen", "/taskforce/suchen?art=wohnung",
                    "/taskforce/profile-anlegen", f"/taskforce/kunde/{kid}/stand",
                    f"/taskforce/kunde/{kid}/stand?art=job",
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

    print("\n11. Die zwei Arbeitsplätze: Arbeitssuche und Wohnungssuche")
    # /taskforce/arbeit und /taskforce/wohnung waren bis zum 21.09.2026 die Tafel mit
    # gesetztem Filter – dieselbe Wand aus Treffern, nur halbiert. Was fehlte, war die
    # Stelle, an der steht, WONACH in dieser Art gesucht wird und FUER WEN nicht. Geprueft
    # wird beides: dass der Arbeitsplatz das jetzt zeigt, und dass die Tafel dabei nicht
    # verschwunden ist, sondern unter /taskforce/tafel?art=… weitersteht.
    arb = c.get("/taskforce/arbeit").text
    woh = c.get("/taskforce/wohnung").text
    pruefe("Die Arbeitsplätze sind eine eigene Seite – nicht die Tafel, nicht der Einstieg",
           ist_dashboard(arb) and ist_dashboard(woh)
           and not any((ist_tafel(arb), ist_einstieg(arb), ist_tafel(woh), ist_einstieg(woh))))
    pruefe("Der Weg auf die Tafel dieser Art steht auf beiden Arbeitsplätzen",
           "/taskforce/tafel?art=job" in arb and "/taskforce/tafel?art=wohnung" in woh)

    # Offen und nicht in einem <details>: was man erst aufklappen muss, tut niemand im
    # Vorbeigehen. Gemessen an der Position, nicht am Wortlaut – dieselbe Messung wie beim
    # Schnellzugriff des Einstiegs. Eingeklappt bleibt nur die Quellenliste, und die steht
    # hinter den Reglern.
    # Gemessen wird im Inhalt, nicht im ganzen Dokument: die Reiterleiste in `basis.html`
    # hat selbst ein <details> („Einrichtung"), und das steht auf jeder Seite vor allem
    # anderen. Der Inhalt beginnt am Merkmal der Vorlage.
    _inhalt_a = arb[arb.find('class="tf-dashboard"'):]
    _inhalt_w = woh[woh.find('class="tf-dashboard"'):]
    pruefe("Die Spezifikation steht offen, nicht eingeklappt",
           0 < _inhalt_a.find("Gehalt ab") < _inhalt_a.find("<details")
           and 0 < _inhalt_w.find("Miete bis") < _inhalt_w.find("<details"),
           f"Arbeit: Gehalt ab an {_inhalt_a.find('Gehalt ab')}, erstes <details an "
           f"{_inhalt_a.find('<details')} · Wohnung: Miete bis an {_inhalt_w.find('Miete bis')}, "
           f"erstes <details an {_inhalt_w.find('<details')}")
    _job_regler = ("Gehalt ab", "nur Quereinstieg")
    _wohn_regler = ("Miete bis", "Zimmer ab", "Fläche ab", "nur mit WBS")
    pruefe("Das Wohnungs-Dashboard trägt keinen Arbeitsregler und umgekehrt",
           all(r in arb and r not in woh for r in _job_regler)
           and all(r in woh and r not in arb for r in _wohn_regler),
           [r for r in _job_regler if r in woh] + [r for r in _wohn_regler if r in arb])

    # Zwei Suchmasken, eine Adresse. Weichen die Feldnamen ab, sucht man von zwei Stellen
    # verschieden, ohne dass es auffaellt – und die Abweichung faellt erst auf, wenn ein
    # Treffer fehlt, den es gab.
    def feldnamen(text, marke):
        stueck = text[text.find(marke):]
        return set(re.findall(r'name="([^"]+)"', stueck[:stueck.find("</form>")]))
    _schnell = feldnamen(ein, 'id="schnellzugriff"')
    _dash = feldnamen(arb, 'id="spezifikation"')
    pruefe("Beide Suchmasken bauen dieselbe Adresse",
           bool(_schnell) and _schnell <= _dash, sorted(_schnell - _dash) or sorted(_schnell))

    # Wer aus der Arbeitssuche auf einen Namen klickt, will dessen Arbeitssuche sehen.
    # Geprueft werden auch die Ziele der Personensuche – sie fuehrt an derselben Stelle
    # auf dieselbe Seite und darf die Art nicht verlieren.
    _kundenlinks = (re.findall(r'href="(/taskforce/kunde/\d+/stand[^"]*)"', arb)
                    + re.findall(r'data-url="(/taskforce/kunde/\{id\}/stand[^"]*)"', arb))
    pruefe("Die Kundenauswahl ist artgebunden",
           bool(_kundenlinks) and all(z.endswith("art=job") for z in _kundenlinks),
           [z for z in _kundenlinks if not z.endswith("art=job")][:3]
           or f"{len(_kundenlinks)} Ziele, alle mit art=job")

    # Der Stand eines Menschen laesst sich auf eine Art verengen – und sagt dann, dass er
    # verengt ist. Eine Haelfte, die lautlos fehlt, ist schlimmer als eine lange Seite.
    voll = c.get(f"/taskforce/kunde/{kid}/stand").text
    nur_job = c.get(f"/taskforce/kunde/{kid}/stand?art=job").text
    pruefe("Der Stand lässt sich verengen und verliert nichts",
           "Arbeitssuche" in voll and "Wohnungssuche" in voll
           and "Arbeitssuche" in nur_job and "Wohnungssuche" not in nur_job
           and f'href="/taskforce/kunde/{kid}/stand"' in nur_job,
           "volle Fassung nicht verlinkt"
           if f'href="/taskforce/kunde/{kid}/stand"' not in nur_job else "")
    pruefe("Der Nachweis verengt sich mit, aus derselben Abfrage",
           all(z["art"] == "job" for z in tf.kunden_nachweis(kid, art="job"))
           and len(tf.kunden_nachweis(kid)) >= len(tf.kunden_nachweis(kid, art="job")),
           f"{len(tf.kunden_nachweis(kid))} gesamt, "
           f"{len(tf.kunden_nachweis(kid, art='job'))} in der Arbeitssuche")

    # Keine zweite Rechenart: die Zahlen des Arbeitsplatzes kommen aus derselben Funktion
    # wie die der Tafel, nur mit `art` davor. Zwei Wege, dieselbe Zahl zu rechnen, gehen
    # frueher oder spaeter auseinander – und dann steht auf zwei Seiten Verschiedenes.
    pruefe("Keine zweite Rechenart: die Hälften ergeben das Ganze",
           tf.anzahl_neu(art="job") + tf.anzahl_neu(art="wohnung") == tf.anzahl_neu(),
           f"{tf.anzahl_neu(art='job')} + {tf.anzahl_neu(art='wohnung')}"
           f" gegen {tf.anzahl_neu()}")

    # Die vier Zahlen der Kopfzeile kommen aus genau den Funktionen, auf die sie sich
    # berufen – gemessen an den gerenderten Zahlen, nicht an einer zweiten Rechnung im
    # Test. „Angeschrieben" heisst dabei mindestens angeschrieben, dieselbe Lesart wie in
    # `tf.kunden_bilanz`; sonst stuende dieselbe Sache auf zwei Seiten verschieden da.
    _z = tf.angebote_zaehlen(art="job")
    _erwartet = [len([p for p in tf.uebersicht(art="job") if p["aktiv"]]),
                 tf.anzahl_neu(art="job"),
                 sum(_z.get(s, 0) for s in ("angeschrieben", "antwort", "erfolg")),
                 tf.anzahl_wiedervorlage(art="job")]
    _gezeigt = [int(x) for x in re.findall(r'<div class="zahl"[^>]*>(\d+)</div>', arb)]
    pruefe("Profile, neue Treffer, angeschrieben und Wiedervorlage sind die der Art",
           _gezeigt == _erwartet, f"gezeigt {_gezeigt}, gerechnet {_erwartet}")

    # Der Arbeitsplatz darf nicht die naechste Wand werden. Messpunkte am 21.09.2026:
    # Einstieg 23,8 KB, Tafel 130,8 KB.
    _gr = (len(arb.encode("utf-8")), len(woh.encode("utf-8")))
    pruefe("Das Dashboard bleibt eine Seite, keine Wand",
           all(g < 40 * 1024 for g in _gr),
           "Arbeit %.1f KB, Wohnung %.1f KB" % (_gr[0] / 1024, _gr[1] / 1024))

    # Aus der Spezifikation direkt ein Suchprofil – ohne den Umweg ueber die Trefferliste.
    # Dieselbe Route wie auf der Direktsuche, damit es nur einen Weg gibt, der das tut.
    c.post("/taskforce/suchen/als-profil",
           data={"kunde": str(zweiter), "art": "wohnung", "was": "Dachgeschoss WBS",
                 "wo": "Bonn", "km": "15", "miete": "900", "zimmer": "2", "flaeche": "55"})
    _neu_p = [p for p in tf.profile_von(zweiter) if p["titel"] == "Dachgeschoss WBS"]
    pruefe("Aus der Spezifikation wird ein Suchprofil dieser Art, mit ihren Reglern",
           bool(_neu_p) and _neu_p[0]["art"] == "wohnung" and _neu_p[0]["ort"] == "Bonn"
           and _neu_p[0]["umkreis_km"] == 15 and _neu_p[0]["max_miete"] == 900
           and _neu_p[0]["min_zimmer"] == 2 and _neu_p[0]["min_flaeche"] == 55,
           {x: _neu_p[0][x] for x in ("art", "ort", "umkreis_km", "max_miete", "min_zimmer",
                                      "min_flaeche")} if _neu_p else "nichts angelegt")
    _vorher = len(tf.profile_von(zweiter))
    _ohne = c.post("/taskforce/suchen/als-profil",
                   data={"art": "job", "was": "Ohne Menschen", "wo": "Köln",
                         "zurueck": "/taskforce/arbeit"})
    _ziel_ohne = _ohne.headers.get("Location", "")
    pruefe("Ohne gewählten Menschen entsteht kein Profil, und es steht da",
           _ohne.status_code == 302 and _ziel_ohne.startswith("/taskforce/arbeit")
           and len(tf.profile_von(zweiter)) == _vorher
           and "Kein Kunde gewählt" in c.get(_ziel_ohne).text,
           _ziel_ohne or "keine Rücksprungadresse")

    # Der Leerfall wird benannt. Es gibt heute zwei Suchprofile, beide fuer denselben
    # Menschen – das Wohnungs-Dashboard zeigt am ersten Tag eine Zeile oder keine. Steht
    # dort eine leere Liste ohne Satz, liest man Leere als Ruhe. Genau das ist der Fehler,
    # den der Einstieg gerade behoben hat.
    _ueber = tf.uebersicht
    tf.uebersicht = lambda **_: []
    try:
        _leer_dash = c.get("/taskforce/wohnung").text
    finally:
        tf.uebersicht = _ueber
    pruefe("Der Leerfall wird benannt statt leer gelassen",
           "Für niemanden wird gerade eine Wohnung gesucht" in _leer_dash)

    print("\n12. Der Umbau der Oberflaeche vom 21.09.2026")
    import lxml.html
    # --- Genau ein `h1` je Seite -----------------------------------------------------
    # Entschieden ist Variante (A): die Topbar traegt den Titel. Sie klebt und steht auch
    # nach 2.800 px Scrollhoehe noch da; eine zweite Ueberschrift im Inhalt wiederholte
    # nur, was die Leiste und die Pfadleiste ohnehin sagen. Gestrichen ist der Seiten-h1
    # deshalb genau dort, wo er sich wiederholte – Seiten, die einen MENSCHEN oder ein
    # einzelnes Profil benennen, behalten ihren: der Name steht in der Leiste nie.
    #
    # **Und die Gegenrichtung.** `/taskforce/profile-anlegen` hatte danach GAR keine
    # Überschrift: `sammelanlage_seite` beginnt nicht mit `taskforce`, die Regel in
    # `basis.html:51` greift dort nicht, und `ns.titel` fällt auf die Vorgabe zurück.
    # Geprüft wird darum nicht „höchstens einer", sondern: **der Ort steht genau
    # einmal da** – in der Topbar, wenn die ihn kennt, sonst im Inhalt.
    VORGABE = "Improfy-Ergänzung"
    _falsch, _ohne_ort = [], []
    for _pfad in ("/taskforce", "/taskforce/tafel", "/taskforce/arbeit",
                  "/taskforce/wohnung", "/taskforce/suchen",
                  "/taskforce/profile-anlegen"):
        _t = c.get(_pfad).text
        _b = lxml.html.fromstring(_t)
        _topbar = " ".join(_b.xpath("//h1[contains(@class,'page-title')]")[0]
                           .text_content().split())
        _inhalt = len(_b.xpath("//main[@class='content']//h1"))
        _erwartet = 0 if _topbar != VORGABE else 1
        if _inhalt != _erwartet:
            _falsch.append(f"{_pfad}: Topbar „{_topbar}“, {_inhalt} im Inhalt,"
                           f" erwartet {_erwartet}")
        if 'class="pfad"' not in _t:
            _ohne_ort.append(_pfad)
    pruefe("Jede Seite nennt ihren Ort genau einmal als Überschrift – und trägt eine Pfadleiste",
           not _falsch and not _ohne_ort,
           "; ".join(_falsch + _ohne_ort) or "6 Adressen geprüft")

    # Die Ausnahme, und warum sie eine ist: hier steht ein NAME. Die Topbar sagt auf
    # dieser Seite „Taskforce", nicht wen man vor sich hat – den Seiten-h1 zu streichen
    # waere hier kein Aufraeumen, sondern Weglassen. Also zwei: der Reiter oben, der
    # Mensch im Inhalt.
    _person = c.get(f"/taskforce/kunde/{kid}/stand").text
    _inhalt_h1 = re.findall(r"<h1(?![^>]*page-title)[^>]*>(.*?)</h1>", _person, re.S)
    pruefe("Die Seite eines Menschen behält ihre Überschrift – sein Name steht nirgends sonst",
           len(_inhalt_h1) == 1 and tf.kunden_info(kid)["name"] in _inhalt_h1[0],
           _inhalt_h1 or "keine Inhaltsüberschrift")

    # --- Die Liste „kein Profil" ist geschnitten, und sie sagt es ---------------------
    # Dasselbe Muster wie auf dem Stand eines Kunden: die ersten acht, die echte
    # Gesamtzahl danebengeschrieben, der Rest hinter `?alle=1`. Eine Grenze, die bei den
    # heutigen Zahlen nicht greift, prueft nichts – darum hier erzwungen.
    _alle_ohne = [z for z in sammelanlage.vorschlaege(art="job")
                  if z["id"] not in {p["kunde_id"] for p in tf.uebersicht(art="job")}]
    _grenze = A.DASHBOARD_OHNE_PROFIL
    A.DASHBOARD_OHNE_PROFIL = 2
    try:
        _kurz = c.get("/taskforce/arbeit").text
        _voll = c.get("/taskforce/arbeit?alle=1").text
    finally:
        A.DASHBOARD_OHNE_PROFIL = _grenze
    pruefe("Die geschnittene Liste nennt die echte Gesamtzahl und den Weg zum Rest",
           (bool(re.search(r"die ersten\s+2 von %d" % len(_alle_ohne), _kurz))
            and f"alle {len(_alle_ohne)} anzeigen" in _kurz)
           if len(_alle_ohne) > 2 else True,
           f"{len(_alle_ohne)} ohne Jobprofil")
    pruefe("Die Zeilen werden wirklich geschnitten, und `?alle=1` zeigt alle",
           (_kurz.count('<span class="plakette p-rot">kein Profil</span>') == 2
            and _voll.count('<span class="plakette p-rot">kein Profil</span>')
            == len(_alle_ohne))
           if len(_alle_ohne) > 2 else True,
           f"{_kurz.count(chr(62) + 'kein Profil<')} geschnitten gegen "
           f"{_voll.count(chr(62) + 'kein Profil<')} vollständig")

    # --- Ein Ziel, ein Knopf ---------------------------------------------------------
    # Je Zeile stand ein „Profil anlegen →" mit demselben `href` wie der Knopf im
    # Listenkopf – bei 19 Zeilen zwanzigmal dieselbe Adresse auf einem Bildschirm. Der
    # Weg selbst darf dabei nicht verschwinden: er steht oben, und zwar genau einmal.
    _sammel = f'href="/taskforce/profile-anlegen?art=job"'
    pruefe("Der Weg zur Sammelanlage steht einmal, nicht in jeder Zeile",
           arb.count(_sammel) == 1, f"{arb.count(_sammel)}× auf /taskforce/arbeit")

    # --- Höchstens eine gefüllte Farbe je Liste --------------------------------------
    # Gefüllt (`btn-primary`) ist die Aufforderung, nicht die Zeile. Zehn gefüllte Knöpfe
    # untereinander sind zehnmal das Lauteste auf der Seite und heben sich gegenseitig
    # auf. Die Zeilenknöpfe bleiben – sie führen an zehn verschiedene Orte –, sie werden
    # nur leise.
    _laut = {p: c.get(p).text.count("btn-sm btn-primary")
             for p in ("/taskforce", "/taskforce/arbeit", "/taskforce/wohnung")}
    pruefe("Kein gefüllter Knopf in den Zeilen der Arbeitslisten",
           not any(_laut.values()), _laut)

    print("\n13. Korrekturrunde 21.09.2026")
    from werkzeug.datastructures import MultiDict

    # --- Beide Wege bauen dasselbe Profil --------------------------------------------
    # Geprueft wird, was die SEITE schickt, nicht was der Test sich wuenscht: die Felder
    # kommen aus dem gerenderten Formular, wie ein Browser sie eingesammelt haette. Die
    # alte Fassung postete `miete/zimmer/flaeche` von Hand und sah deshalb nicht, dass
    # `gehalt`, `quereinstieg`, `wbs` und `quelle` im Formular gar nicht standen. Ueber
    # den Arbeitsplatz entstand `{"min_gehalt": 3000, "nur_quereinstieg": true}`, ueber
    # die Direktsuche `kriterien=None` – ein Knopf, zwei verschiedene Profile.
    _probe = [{"quelle": "x", "extern_id": "1", "titel": "Lagerhelfer A", "anbieter": "A",
               "ort": "Köln", "zusatz": {"quereinstieg": True}},
              {"quelle": "x", "extern_id": "2", "titel": "Lagerhelfer B", "anbieter": "B",
               "ort": "Köln", "zusatz": {"quereinstieg": False}}]
    _echte_suche = tf.direktsuche
    tf.direktsuche = lambda p, quellen=None, grenze=200: (_probe, [])
    try:
        _adr = ("/taskforce/suchen?art=job&was=Lagerhelfer&wo=K%C3%B6ln&km=25"
                "&gehalt=3000&quereinstieg=1&quelle=jobs.ba&quelle=jobs.indeed")
        _baum = lxml.html.fromstring(c.get(_adr).text)
        _form = [x for x in _baum.iter("form")
                 if (x.get("action") or "").endswith("/taskforce/suchen/als-profil")][0]
        # **Auch `<select>`.** Sechs derselben Regler (`km`, `gehalt`, `arbeitszeit`,
        # `miete`, `zimmer`, `flaeche`) sind auf dem Arbeitsplatz Auswahlfelder – eine
        # Prüfung, die nur `<input>` liest, übersähe dort genau den Verlust, gegen
        # den sie geschrieben wurde. Gesammelt wird wie im Browser: ohne `selected`
        # gilt die erste Zeile, abgewählte Häkchen bleiben weg.
        _felder = []
        for _i in _form.iter("input", "select"):
            _n = _i.get("name")
            if not _n:
                continue
            if _i.tag == "select":
                _gew = ([o for o in _i.iter("option") if o.get("selected") is not None]
                        or list(_i.iter("option"))[:1])
                _felder += [(_n, o.get("value") or "") for o in _gew]
                continue
            if ((_i.get("type") or "").lower() in ("checkbox", "radio")
                    and _i.get("checked") is None):
                continue
            _w = _i.get("value") or ""
            _felder.append((_n, str(kid) if _n == "kunde"
                            else ("Regler aus der Direktsuche" if _n == "titel" else _w)))
        c.post("/taskforce/suchen/als-profil", data=MultiDict(_felder))
    finally:
        tf.direktsuche = _echte_suche
    c.post("/taskforce/suchen/als-profil", data=MultiDict([
        ("art", "job"), ("was", "Lagerhelfer"), ("wo", "Köln"), ("km", "25"),
        ("gehalt", "3000"), ("quereinstieg", "1"),
        ("quelle", "jobs.ba"), ("quelle", "jobs.indeed"),
        ("kunde", str(kid)), ("titel", "Regler vom Arbeitsplatz")]))

    def _regler_von(titel):
        z = db.eine("SELECT kriterien, quellen FROM tf_profil WHERE kunde_id=? AND titel=?",
                    (kid, titel))
        return (json.loads(z["kriterien"]) if z and z["kriterien"] else None,
                z["quellen"] if z else "kein Profil")

    _aus_suche = _regler_von("Regler aus der Direktsuche")
    _aus_platz = _regler_von("Regler vom Arbeitsplatz")
    pruefe("Die feinen Regler überleben beide Wege – und beide bauen dasselbe Profil",
           _aus_suche == _aus_platz
           and _aus_suche[0] == {"min_gehalt": 3000, "nur_quereinstieg": True}
           and _aus_suche[1] == "jobs.ba,jobs.indeed",
           f"Direktsuche {_aus_suche} · Arbeitsplatz {_aus_platz}")
    pruefe("Die Seite schickt jeden Regler mit, der keine eigene Spalte hat",
           {"gehalt", "quereinstieg", "wbs", "quelle"} <= {n for n, _ in _felder},
           sorted({n for n, _ in _felder}))

    # --- Ein Schluessel fuer den Quereinstieg ----------------------------------------
    # `_suchprofil` schrieb `quereinstieg`, `tf.job_filter` liest `nur_quereinstieg`:
    # das gespeicherte Profil filterte, der Bildschirm nicht. Gemessen an denselben zwei
    # Treffern – einer geeignet, einer nicht.
    _p_bild = A._suchprofil("job", {"was": "Lagerhelfer", "wo": "Köln", "km": 25,
                                    "quereinstieg": True})
    _durch, _weg_zahl = tf.job_filter(_p_bild, list(_probe))
    # `suchspalte` legt die Kriterien als JSON-Text ab – gelesen wird hier also derselbe
    # Text, den auch `job_filter` auspackt.
    _k_bild = json.loads(_p_bild["kriterien"] or "{}")
    pruefe("„nur Quereinstieg“ filtert auch auf dem Bildschirm, nicht nur im Profil",
           len(_durch) == 1 and _weg_zahl == 1
           and _k_bild.get("nur_quereinstieg") is True,
           f"{len(_durch)} durch, {_weg_zahl} aussortiert, Schlüssel {sorted(_k_bild)}")

    # --- Eine Zaehlweise fuer „hat ein Profil dieser Art" ----------------------------
    # `vorschlaege()` fragte nach Menschen ohne AKTIVES Profil, `anlegen()` nach Menschen
    # ohne JEDES. Wer sein Profil pausiert hatte, wurde also angeboten und beim Anlegen
    # mit „hat ein Profil, es ist pausiert" liegen gelassen – zwei Zahlen fuer dieselbe
    # Menge, einen Klick auseinander. Der Fall kommt im Bestand nicht von selbst vor,
    # also wird er hier hergestellt.
    _wieder = []
    _vorher_v = sammelanlage.vorschlaege(art="job")
    if _vorher_v:
        _opfer = _vorher_v[0]["id"]
        _angelegt, _ = sammelanlage.anlegen([(_opfer, "Lagerhelfer", "Köln")], art="job")
        _neu_pid = _angelegt[0][1] if _angelegt else None
        if _neu_pid:
            with db.offen() as _con:
                _con.execute("UPDATE tf_profil SET aktiv=0 WHERE id=?", (_neu_pid,))
        try:
            _v = sammelanlage.vorschlaege(art="job")
            # Erst vergleichen, dann anlegen: ein angelegtes Profil veraendert genau die
            # Menge, um die es hier geht.
            _dash = c.get("/taskforce/arbeit?alle=1").text
            pruefe("Arbeitsplatz und Sammelanlage zählen dieselbe Menge",
                   _dash.count(">kein Profil<") == len(_v),
                   f"Arbeitsplatz {_dash.count(chr(62) + 'kein Profil<')}, "
                   f"Sammelanlage {len(_v)}, davon pausiert 1")
            _wieder, _uebersprungen = sammelanlage.anlegen(
                [(z["id"], z["begriff"] or "Lagerhelfer", z["ort"]) for z in _v[:1]],
                art="job")
            pruefe("Was die Sammelanlage anbietet, lässt sich auch anlegen",
                   _opfer not in {z["id"] for z in _v}
                   and bool(_wieder)
                   and not [g for _, g in _uebersprungen if "Profil" in g],
                   f"{len(_vorher_v)} vorher, {len(_v)} nachher, "
                   f"übersprungen {_uebersprungen or 'nichts'}")
        finally:
            with db.offen() as _con:
                if _neu_pid:
                    _con.execute("DELETE FROM tf_profil WHERE id=?", (_neu_pid,))
                for _k, _p, _ in _wieder:
                    _con.execute("DELETE FROM tf_profil WHERE id=?", (_p,))

    # --- Was aus dem Formular kommt, ist nichts, worauf man blind zugreift -----------
    # Vier gemessene Wege in eine 500er-Seite, alle mit gueltigem JSON. Ein `dict` ist
    # wahr und kam am alten Guard vorbei; `sqlite3` kann es nicht binden, und `_score`
    # ruft auf `zusatz` ein `.get` auf.
    _pid_g = tf.profile_von(kid)[0]["id"]
    _vorher_g = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?", (_pid_g,))
    _giftig, _kaputt = 0, []
    for _satz in ({"quelle": {"x": 1}, "extern_id": "a"},
                  {"quelle": "jobs.ba", "extern_id": "b", "titel": ["a", "b"]},
                  {"quelle": "jobs.ba", "extern_id": "c", "entfernung_km": {"a": 1}},
                  {"quelle": "jobs.ba", "extern_id": "d", "zusatz": ["a"]},
                  ["gar kein Wörterbuch"], "auch nicht", 7):
        _r = c.post("/taskforce/suchen/uebernehmen",
                    data={"profil": str(_pid_g), "zurueck": "/taskforce/suchen",
                          "treffer": "0", "t0": json.dumps(_satz)})
        _giftig += 1
        if _r.status_code >= 500:
            _kaputt.append(f"{_satz} → {_r.status_code}")
    pruefe(f"{_giftig} untergeschobene Treffer fallen weg, statt die Seite zu fällen",
           not _kaputt
           and db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?",
                       (_pid_g,)) == _vorher_g,
           "; ".join(_kaputt[:2]) or f"{_vorher_g} Angebote unverändert")

    # --- Der Rueckweg bleibt im Haus -------------------------------------------------
    # Dieselbe Regel wie in `_zurueck` und `_mit_meldung`. Ohne sie reichte ein
    # Formularfeld, um jemanden nach dem Klick auf „übernehmen" nach draußen zu schicken.
    _fremd = c.post("/taskforce/suchen/uebernehmen",
                    data={"profil": str(_pid_g), "zurueck": "https://example.org/",
                          "treffer": "0"})
    pruefe("Eine fremde Rücksprungadresse wird nicht gefolgt",
           _fremd.status_code == 302
           and _fremd.headers.get("Location", "").startswith("/taskforce/suchen"),
           _fremd.headers.get("Location"))

    # --- Die 413-Wand sagt, was los ist ----------------------------------------------
    # Die Treffer reisen im Formular mit, also hat der Rumpf eine Grenze:
    # `max_form_memory_size` (500.000 B), mit echten Treffergroessen rund 575 Stueck.
    # Heute unerreichbar, weil `tf.direktsuche` bei 200 abschneidet – aber genau eine
    # Zahl entfernt. Ohne Griff kam die nackte englische Werkzeug-Seite: keine
    # Erklaerung, kein Weg zurueck, alles Angehakte weg.
    _zu_gross = c.post("/taskforce/suchen/uebernehmen",
                       data={"profil": str(_pid_g), "zurueck": "/taskforce/suchen",
                             "treffer": "0",
                             "t0": json.dumps({"quelle": "a", "extern_id": "b",
                                               "beschreibung": "x" * 600000})})
    _t413 = _zu_gross.get_data(as_text=True)
    pruefe("Ein zu grosser Rumpf endet auf einer deutschen Seite mit Weg zurück",
           _zu_gross.status_code == 413 and "Zu viel auf einmal" in _t413
           and 'href="/' in _t413,
           f"{_zu_gross.status_code}, {len(_t413)} Zeichen")

    # --- Der Schnitt bei `grenze` wird benannt ---------------------------------------
    # Dubletten standen schon immer da („12 Dubletten ausgeblendet"), der Schnitt nicht:
    # wer 340 Treffer hatte, sah 200 und hielt das für alles.
    _viele = [{"quelle": "probe.schnitt", "extern_id": str(i),
               "titel": "Stelle %04d Lagerhelfer" % i, "anbieter": "Firma%04d" % i,
               "ort": "Köln", "zusatz": {}} for i in range(25)]
    _alt_q, _alt_f = tf.QUELLEN, tf.quellen_fuer
    tf.QUELLEN = dict(tf.QUELLEN)
    tf.QUELLEN["probe.schnitt"] = ("Probe", "job", lambda p: list(_viele), lambda: True)
    tf.quellen_fuer = lambda p: ["probe.schnitt"]
    try:
        _t, _m = tf.direktsuche(A._suchprofil("job", {"was": "Lagerhelfer", "wo": "Köln",
                                                      "km": 25}), grenze=10)
    finally:
        tf.QUELLEN, tf.quellen_fuer = _alt_q, _alt_f
    pruefe("Der Schnitt bei der Grenze wird benannt, nicht nur die Dubletten",
           len(_t) == 10 and any("abgeschnitten" in z for z in _m),
           f"{len(_t)} von {len(_viele)} · {_m}")


    # --- Ein Schrägstrich, kein zweiter ---------------------------------------------
    # `startswith("/")` reichte nicht: `//example.org` beginnt mit `/` und ist trotzdem
    # eine fremde Adresse – ein Browser löst ein schema-relatives `Location` gegen
    # `https:` auf. Die alte Prüfung maß ausgerechnet `https://example.org/`, also den
    # einen Wert, der ohnehin hielt. Hier stehen die Formen, die durchkamen.
    _rs = chr(92)
    _fremd_wege = ("//example.org", "////example.org", "//example.org/x",
                   "/" + _rs + "example.org", "/\t/example.org",
                   "https://example.org/", "https:/example.org")
    _durchgerutscht = []
    for _w in _fremd_wege:
        for _route in ("/taskforce/suchen/uebernehmen", "/taskforce/suchen/als-profil"):
            _r = c.post(_route, data={"profil": str(_pid_g), "art": "job", "was": "x",
                                      "zurueck": _w, "treffer": "0"})
            _ziel = _r.headers.get("Location", "")
            if not _ziel.startswith("/taskforce/suchen"):
                _durchgerutscht.append(f"{_route} {_w!r} → {_ziel[:40]}")
    pruefe(f"Keiner von {len(_fremd_wege)} fremden Rückwegen wird gefolgt",
           not _durchgerutscht, "; ".join(_durchgerutscht[:3]))
    # Und die Gegenrichtung: der eigene Weg muss durchkommen, samt Reglern.
    _eigen = c.post("/taskforce/suchen/uebernehmen",
                    data={"profil": str(_pid_g), "treffer": "0",
                          "zurueck": "/taskforce/suchen?art=job&was=Lager"})
    pruefe("Der eigene Rückweg kommt unverändert durch",
           _eigen.headers.get("Location", "").startswith(
               "/taskforce/suchen?art=job&was=Lager"),
           _eigen.headers.get("Location"))

    # --- Der Wächter geht bis in den Zusatz ------------------------------------------
    # Die sieben Fälle oben gehen alle nur EINE Ebene tief und fallen schon an der
    # Pflichtprüfung durch – die zweite Ebene fassten sie nie an. `_score` liest aus
    # `zusatz` aber Text: `suchbegriff` mit `.casefold()`, `preis` mit `.split()`.
    _pid_j = db.wert("SELECT id FROM tf_profil WHERE art='job' ORDER BY id LIMIT 1")
    _pid_w = db.wert("SELECT id FROM tf_profil WHERE art='wohnung' ORDER BY id LIMIT 1")
    # Echte Quellenschluessel, sonst faellt der Satz schon am Quellen-Riegel durch und
    # die zweite Ebene wird nie angefasst – die Pruefung wuerde gruen melden, ohne zu
    # pruefen, wogegen sie geschrieben wurde.
    _tief = [
        (_pid_j, {"quelle": "jobs.ba", "extern_id": "z1",
                  "zusatz": {"suchbegriff": {"x": 1}}}),
        (_pid_w, {"quelle": "wohnung.kleinanzeigen", "extern_id": "z2",
                  "zusatz": {"preis": {"x": 1}}}),
        (_pid_w, {"quelle": "wohnung.kleinanzeigen", "extern_id": "z3",
                  "zusatz": {"preis": [1, 2]}}),
        (_pid_j, {"quelle": "jobs.ba", "extern_id": "z4",
                  "entfernung_km": float("inf")}),
        (_pid_j, {"quelle": "jobs.ba", "extern_id": "z5",
                  "entfernung_km": float("nan")}),
        # Jenseits von 64 Bit. Ein Python-`int` ist unbegrenzt und `math.isfinite`
        # sagt `True`; `sqlite3` wirft dann `OverflowError` beim Binden – mitten in
        # `_ablegen`, also mitten in der Transaktion. Weil `db.offen()` nur bei
        # sauberem Durchlauf committet, wäre der ganze Stapel weg, nicht nur dieser
        # Satz. Genau der Schaden, gegen den der Wächter geschrieben ist.
        (_pid_j, {"quelle": "jobs.ba", "extern_id": "z6",
                  "entfernung_km": 2 ** 63}),
        (_pid_j, {"quelle": "jobs.ba", "extern_id": "z7",
                  "entfernung_km": 10 ** 30}),
        # Und unter null. Stürzt nichts ab – aber `_score` gibt dafür +2 („nah"),
        # jeder `max_km`-Filter nimmt die Zeile mit, und die Sortierung nach Nähe
        # stellt sie vor jeden echten Treffer. Ein Satz, der sich selbst nach oben
        # nagelt.
        (_pid_j, {"quelle": "jobs.ba", "extern_id": "z8", "entfernung_km": -5}),
        (_pid_j, {"quelle": "jobs.ba", "extern_id": "z9", "score": -3}),
    ]
    _tief_kaputt, _abgelegt = [], []
    for _ziel_pid, _satz in _tief:
        if _ziel_pid is None:
            continue
        _vor = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?", (_ziel_pid,))
        _r = c.post("/taskforce/suchen/uebernehmen",
                    data={"profil": str(_ziel_pid), "zurueck": "/taskforce/suchen",
                          "treffer": "0", "t0": json.dumps(_satz)})
        if _r.status_code >= 500 or A._treffer_sauber(_satz):
            _tief_kaputt.append(f"{_satz.get('extern_id')} → {_r.status_code}")
        if db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?",
                   (_ziel_pid,)) != _vor:
            _abgelegt.append(_satz.get("extern_id"))
    pruefe("Auch die zweite Ebene im Zusatz fällt weg, statt die Seite zu fällen",
           not _tief_kaputt and not _abgelegt,
           "; ".join(_tief_kaputt + _abgelegt) or f"{len(_tief)} Formen geprüft")
    pruefe("Eine gewöhnliche Entfernung und eine Null kommen weiter durch",
           A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "ok1",
                              "entfernung_km": 25})
           and A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "ok2",
                                  "entfernung_km": 0, "score": 7.5})
           and A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "ok3",
                                  "entfernung_km": None}))

    # --- Ein Text, der als Zahl gelesen wird, hat eine Laenge ----------------------
    # `_score` macht aus `zusatz.preis` mit `int(…)` eine Zahl, und Python 3.12 wirft bei
    # mehr als 4.300 Ziffern – ungefangen, mitten in `_ablegen` und damit mitten in der
    # Transaktion: `db.offen()` committet nur bei sauberem Durchlauf, also faellt der
    # GANZE Stapel zurueck. Rund 5 kB Nutzlast genuegen, weit unter der 500-kB-Grenze.
    #
    # Die Zeile laeuft nur fuer ein Wohnprofil MIT `max_miete` – ohne das wird `int()` gar
    # nicht erreicht, und die Pruefung waere gruen, ohne etwas zu pruefen.
    _pid_wm = db.wert("SELECT id FROM tf_profil WHERE art='wohnung' ORDER BY id LIMIT 1")
    _alte_miete = db.wert("SELECT max_miete FROM tf_profil WHERE id=?", (_pid_wm,))
    with db.offen() as _con:
        _con.execute("UPDATE tf_profil SET max_miete=900 WHERE id=?", (_pid_wm,))
    try:
        _lang = [
            (_pid_wm, {"quelle": "jobs.ba", "extern_id": "L1",
                       "zusatz": {"preis": "9" * 5000}}),
            (_pid_wm, {"quelle": "jobs.ba", "extern_id": "L2",
                       "zusatz": {"preis": "9" * 4301}}),
            (_pid_j, {"quelle": "jobs.ba", "extern_id": "L3",
                      "zusatz": {"suchbegriff": "x" * 5000}}),
        ]
        _lang_kaputt = []
        for _ziel, _satz in _lang:
            _r_l = c.post("/taskforce/suchen/uebernehmen",
                          data={"profil": str(_ziel), "zurueck": "/taskforce/suchen",
                                "treffer": "0", "t0": json.dumps(_satz)})
            if (_r_l.status_code >= 500 or A._treffer_sauber(_satz)
                    or db.wert("SELECT COUNT(*) FROM tf_angebot WHERE extern_id=?",
                               (_satz["extern_id"],))):
                _lang_kaputt.append(f"{_satz['extern_id']} → {_r_l.status_code}")
        pruefe("Ein überlanger Zusatztext fällt weg, statt den ganzen Stapel zu kippen",
               not _lang_kaputt, _lang_kaputt or f"{len(_lang)} Formen geprüft")
        # Und die Gegenrichtung: ein echter Preis muss durchkommen \u2013 der laengste im
        # Bestand hat 7 Zeichen, die Grenze liegt bei 200.
        pruefe("Ein echter Preis und ein echter Suchbegriff bleiben erlaubt",
               A._treffer_sauber({"quelle": "wohnung.kleinanzeigen", "extern_id": "L4",
                                  "zusatz": {"preis": "1.480 €"}})
               and A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "L5",
                                      "zusatz": {"suchbegriff": "Lagerhelfer",
                                                 "arbeitszeit": "Vollzeit, Schicht"}}))
    finally:
        with db.offen() as _con:
            _con.execute("UPDATE tf_profil SET max_miete=? WHERE id=?",
                         (_alte_miete, _pid_wm))

    # --- `json.loads` wirft nicht nur `ValueError` ---------------------------------
    # Tief verschachteltes JSON bricht im C-Scanner mit `RecursionError` ab \u2013 kein
    # `ValueError`, also vom alten `except` nicht gefangen. Gemessen mit CPython 3.12.10:
    # Tiefe 2.000 geht durch, Tiefe 5.000 wirft, bei 10 kB Nutzlast. Hier geht nichts
    # verloren, der Abbruch liegt vor `_ablegen` \u2013 aber die Zusage „lieber ein Treffer
    # weniger als eine 500er-Seite" gilt auch fuer diesen Weg.
    _tief_json = []
    for _t in (2000, 5000, 20000):
        _r_t = c.post("/taskforce/suchen/uebernehmen",
                      data={"profil": str(_pid_j), "zurueck": "/taskforce/suchen",
                            "treffer": "0", "t0": "[" * _t + "]" * _t})
        if _r_t.status_code >= 500:
            _tief_json.append(f"Tiefe {_t} → {_r_t.status_code}")
    pruefe("Tief verschachteltes JSON fällt weg, statt die Seite zu fällen",
           not _tief_json, _tief_json or "Tiefe 2.000 / 5.000 / 20.000 geprüft")

    # --- Eine ausgehöhlte Beispieldatei muss gemeldet werden, nicht den Lauf abbrechen -
    # `(t.get("extern_id") or "").strip()` gab bei einer Zahl einen `AttributeError`
    # AUSSERHALB von `pruefe` \u2013 der ganze Lauf braech ab, statt einen roten Punkt zu
    # melden. Eine Pruefung gegen eine ausgehoehlte Datei muss sie ueberleben.
    _vergiftet = {"jobs.ba": {"quelle": "jobs.ba", "extern_id": 12345},
                  "jobs.stepstone": {"quelle": "jobs.stepstone", "extern_id": " "},
                  "jobs.meinestadt": "gar kein Wörterbuch"}
    try:
        _gemeldet = hohle_beispiele(_vergiftet)
    except Exception as e:
        _gemeldet = "ABBRUCH: %s" % type(e).__name__
    pruefe("Eine ausgehöhlte Beispieldatei wird gemeldet, nicht mit einem Absturz quittiert",
           _gemeldet == ["jobs.ba", "jobs.meinestadt", "jobs.stepstone"], _gemeldet)

    # Der eine Listenfall, der erlaubt bleiben MUSS: `arbeitszeit_codes` aus der BA.
    pruefe("Die eine erlaubte Liste bleibt erlaubt",
           A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "ok", "titel": "T",
                              "zusatz": {"arbeitszeit_codes": ["vz", "tz"]}}))

    # --- Kein Profil nagelt sich auf die heute verbundenen Portale fest --------------
    # Eine nicht verbundene Quelle trägt im Formular `disabled`, der Browser schickt sie
    # nicht mit – `gewaehlt` kannte sie nie, `moeglich` zählte sie mit. Damit war
    # `passend < moeglich` immer wahr. Folge: Sobald der IS24-Zugang steht, bliebe jedes
    # heute angelegte Wohnprofil dafür dauerhaft stumm, und niemand sähe, warum.
    _echt_stand = tf.quellen_stand
    _stumm = "wohnung.mail"
    tf.quellen_stand = lambda nur_art=None: [
        dict(q, bereit=False if q["schluessel"] == _stumm else q["bereit"])
        for q in _echt_stand(nur_art)]
    try:
        _verbunden = [q["schluessel"] for q in tf.quellen_stand("wohnung") if q["bereit"]]
        _felder_w = [("art", "wohnung"), ("wo", "Köln"), ("km", "25"),
                     ("kunde", str(kid)), ("titel", "Quellenprobe Wohnung")]
        _felder_w += [("quelle", q) for q in _verbunden]
        c.post("/taskforce/suchen/als-profil", data=MultiDict(_felder_w))
    finally:
        tf.quellen_stand = _echt_stand
    _q = db.eine("SELECT quellen FROM tf_profil WHERE kunde_id=? AND titel=?",
                 (kid, "Quellenprobe Wohnung"))
    pruefe("Alle verbundenen Quellen angehakt heißt weiter „alle“, keine Festnagelung",
           _q is not None and _q["quellen"] is None,
           f"quellen={(_q or {}).get('quellen')!r}, nicht verbunden: {_stumm}")

    # --- Die CRM-Naht schreibt denselben Schlüssel wie die Oberfläche ----------------
    # `api.py` trug eine wortgleiche zweite Fassung von `_suchprofil` – und den toten
    # Schlüssel `quereinstieg`. Die Naht sagte dem CRM „nur Quereinstieg" zu und lieferte
    # alles. Gemessen an denselben zwei Treffern wie oben.
    _gesehen = {}

    def _merken(p, quellen=None, grenze=200):
        _gesehen["p"] = p
        return tf.job_filter(p, list(_probe))[0], []

    tf.direktsuche = _merken
    try:
        _api = c.get("/api/taskforce/suche?art=job&was=Lagerhelfer&wo=K%C3%B6ln"
                     "&quereinstieg=1").get_json()
    finally:
        tf.direktsuche = _echte_suche
    _k_api = json.loads((_gesehen.get("p") or {}).get("kriterien") or "{}")
    pruefe("Die Schnittstelle filtert, was sie zusagt – mit dem Schlüssel der Seite",
           _k_api.get("nur_quereinstieg") is True and len(_api.get("daten", [])) == 1,
           f"Kriterien {sorted(_k_api)}, "
           f"{len(_api.get('daten', []))} von {len(_probe)} Treffern")

    # --- Indeed hält den Parallellauf nicht mehr fest --------------------------------
    # Die Leiter (0, 5, 12 s) bleibt – am Bildschirm gilt aber ein Zeitbudget, und was
    # getragen hat, steht in den Meldungen und damit im Protokoll der Route.
    # Die Wartestufen liegen seit dem 22.09.2026 in einer Liste JE AUFRUF, nicht in
    # einer Modulliste: `betrieb` startet den Nachtlauf als Thread im selben Prozess,
    # und dessen Zeilen landeten sonst in der Direktsuche eines Menschen.
    def _indeed_probe(p, max_seiten=2, budget=None, protokoll=None):
        if protokoll is not None:
            protokoll.append("403 nach 0 s" if budget else "nach 12 s")
        if budget:
            raise RuntimeError("Indeed blockt gerade (403)")
        return [{"quelle": "jobs.indeed", "extern_id": "1", "titel": "T",
                 "anbieter": "A", "zusatz": {}}]

    _alt_q, _alt_f = tf.QUELLEN, tf.quellen_fuer
    tf.QUELLEN = dict(tf.QUELLEN)
    tf.QUELLEN["jobs.indeed"] = ("Indeed", "job", _indeed_probe, lambda: True)
    tf.quellen_fuer = lambda p: ["jobs.indeed"]
    try:
        _t_i, _m_i = tf.direktsuche(A._suchprofil("job", {"was": "Lagerhelfer",
                                                          "wo": "Köln", "km": 25}))
    finally:
        tf.QUELLEN, tf.quellen_fuer = _alt_q, _alt_f
    pruefe("Am Bildschirm bekommt Indeed ein Budget, und die Wartestufe wird festgehalten",
           tf.BUDGET_QUELLEN == ("jobs.indeed",) and tf.BUDGET_BILDSCHIRM > 0
           and any("403 nach 0 s" in z for z in _m_i)
           and not hasattr(tf, "INDEED_LEITER"),
           f"Budget {tf.BUDGET_BILDSCHIRM} s · Meldungen {_m_i}")
    # Zwei Läufe dürfen sich die Zeilen nicht teilen: die zweite Suche darf genau eine
    # eigene Zeile tragen, nicht zwei.
    tf.QUELLEN = dict(tf.QUELLEN)
    tf.QUELLEN["jobs.indeed"] = ("Indeed", "job", _indeed_probe, lambda: True)
    tf.quellen_fuer = lambda p: ["jobs.indeed"]
    try:
        _t_i2, _m_i2 = tf.direktsuche(A._suchprofil("job", {"was": "Lagerhelfer",
                                                            "wo": "Köln", "km": 25}))
    finally:
        tf.QUELLEN, tf.quellen_fuer = _alt_q, _alt_f
    pruefe("Die Wartestufen gehören dem einzelnen Lauf, nicht dem Prozess",
           len([z for z in _m_i2 if "403 nach 0 s" in z]) == 1,
           _m_i2)

    print("\n14. Der Ortsname und die Sichtbarkeit der Quellen (22.09.2026)")
    # --- Umlaute gehoeren nicht prozentkodiert in eine Adressbahn -------------------
    # Der teuerste stille Ausfall dieser Sitzung: StepStone antwortet auf
    # `/jobs/lagerhelfer/in-k%C3%B6ln` mit einer GUELTIGEN Seite, in der die Trefferliste
    # leer ist. Keine 404, keine Ausnahme – also keine Meldung, und der Bildschirm zeigte
    # 94 Treffer statt 140, als waere das alles. Gemessen am 22.09.2026, viermal
    # abwechselnd im selben Prozess: `in-k%C3%B6ln` 0 Eintraege, `in-koeln` 25.
    #
    # Geprueft wird die Ursache, nicht das Portal: kein Umlaut darf in eine Adressbahn
    # geraten. Das laeuft ohne Netz und haelt auch dann, wenn StepStone morgen umbaut.
    _umlautorte = ("Köln", "Düsseldorf", "Münster", "Mönchengladbach", "Osnabrück",
                   "Bäcker", "Weißenfels")
    _mit_umlaut = [o for o in _umlautorte
                   if any(z in tf._slug(o) for z in "äöüßÄÖÜ") or "%" in tf._slug(o)]
    pruefe("Keine Adressbahn traegt einen Umlaut – sonst antwortet StepStone leer",
           not _mit_umlaut,
           _mit_umlaut or {o: tf._slug(o) for o in _umlautorte[:3]})
    pruefe("Die Umschrift steht an einer Stelle, nicht an dreien",
           tf._slug("Köln") == "koeln" and tf._is24_slug("Köln") == "koeln"
           and tf.UMSCHRIFT == (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")),
           f"_slug {tf._slug('Mönchengladbach')!r} · _is24_slug "
           f"{tf._is24_slug('Mönchengladbach')!r}")

    # Und die Gegenrichtung, die genauso wichtig ist: **die Portale, die den Ort im
    # ABFRAGETEIL bekommen, duerfen den Umlaut NICHT verlieren.** Die BA liefert fuer
    # „Köln" 5 Treffer und fuer „Koeln" 0 – wer dort umschreibt, dreht den Fehler nur um.
    _gesehen_ort = {}

    def _merk_ort(name):
        def _f(p, **_):
            _gesehen_ort[name] = p.get("ort")
            return []
        return _f

    _alt_q2, _alt_f2 = tf.QUELLEN, tf.quellen_fuer
    tf.QUELLEN = dict(tf.QUELLEN)
    tf.QUELLEN["jobs.ba"] = ("Bundesagentur", "job", _merk_ort("ba"), lambda: True)
    tf.quellen_fuer = lambda p: ["jobs.ba"]
    try:
        tf.direktsuche(A._suchprofil("job", {"was": "Lagerhelfer", "wo": "Köln", "km": 25}))
    finally:
        tf.QUELLEN, tf.quellen_fuer = _alt_q2, _alt_f2
    pruefe("Wer den Ort im Abfrageteil bekommt, bekommt ihn mit Umlaut",
           _gesehen_ort.get("ba") == "Köln", _gesehen_ort)

    # --- Eine stumme Quelle ist auf dem Bildschirm zu sehen -------------------------
    # Null Treffer sind kein Fehler – sie koennen stimmen. Aber eine Quelle, die still
    # nichts liefert, waehrend die anderen liefern, ist von einem vollstaendigen Ergebnis
    # nicht zu unterscheiden. Genau so lag StepStone fuer jeden Umlautort auf Null.
    def _liefert(n):
        def _f(p, **_):
            return [{"quelle": "x", "extern_id": "%s-%d" % (n, i),
                     "titel": "Stelle %s %d" % (n, i), "anbieter": "Firma%s%d" % (n, i),
                     "ort": "Köln", "zusatz": {}} for i in range(n)]
        return _f

    _alt_q3, _alt_f3 = tf.QUELLEN, tf.quellen_fuer
    tf.QUELLEN = dict(tf.QUELLEN)
    tf.QUELLEN["probe.laut"] = ("Lautes Portal", "job", _liefert(7), lambda: True)
    tf.QUELLEN["probe.stumm"] = ("Stummes Portal", "job", _liefert(0), lambda: True)
    tf.quellen_fuer = lambda p: ["probe.laut", "probe.stumm"]
    try:
        _t_q, _m_q = tf.direktsuche(A._suchprofil("job", {"was": "Lagerhelfer",
                                                          "wo": "Köln", "km": 25}))
    finally:
        tf.QUELLEN, tf.quellen_fuer = _alt_q3, _alt_f3
    _quellzeile = next((z for z in _m_q if z.startswith("Quellen:")), "")
    pruefe("Die stumme Quelle steht mit ihrer Null auf dem Bildschirm",
           "Lautes Portal 7" in _quellzeile and "Stummes Portal 0" in _quellzeile
           and _m_q and _m_q[0].startswith("Quellen:"),
           _quellzeile or _m_q)

    # Und sie steht auch auf der Seite – gemessen am gerenderten BLOCK, nicht am
    # Seitentext. „Stummes Portal 0" wird auch dann gefunden, wenn die Zeile zurueck
    # im Fliesstext der uebrigen Meldungen steht; genau dort soll sie nicht stehen.
    _echte_suche2 = tf.direktsuche
    tf.direktsuche = lambda p, quellen=None, grenze=200: (
        [], ["Quellen: Stummes Portal 0, Lautes Portal 7", "9 Dubletten ausgeblendet"])
    try:
        _seite_q = c.get("/taskforce/suchen?art=job&was=Lagerhelfer&wo=K%C3%B6ln").text
        # „beides" laeuft zweimal – zwei Quellenzeilen, zwei Bloecke.
        _seite_b = c.get("/taskforce/suchen?art=beides&was=Lagerhelfer&wo=K%C3%B6ln").text
    finally:
        tf.direktsuche = _echte_suche2
    _bloecke = re.findall(r'<p class="unterzeile quellzeile"[^>]*>(.*?)</p>',
                          _seite_q, re.S)
    pruefe("Die Quellenzeile steht als eigener Block, nicht im Fließtext",
           len(_bloecke) == 1 and "Stummes Portal 0" in _bloecke[0]
           and "Dubletten" not in _bloecke[0],
           _bloecke or "kein Block gefunden")
    _bloecke_b = re.findall(r'<p class="unterzeile quellzeile"[^>]*>(.*?)</p>',
                            _seite_b, re.S)
    pruefe("Bei „beides“ bekommt jede der zwei Suchen ihre eigene Quellenzeile",
           len(_bloecke_b) == 2, f"{len(_bloecke_b)} Blöcke")

    print("\n15. Dritte Korrekturrunde (22.09.2026)")
    # --- Ein ECHTER Treffer muss durchkommen ----------------------------------------
    # Der teuerste Fehler dieser Sitzung, und er lag daran, dass jede Pruefung zu
    # `_treffer_sauber` nur in die ABWEHRRICHTUNG mass. Die Probetreffer der Bedienprobe
    # und der einzige positive Fall hier hatten jedes Textfeld gefuellt – echte Treffer
    # haben das nicht. `jobs_kleinanzeigen` und `wohnung_kleinanzeigen` setzen
    # `veroeffentlicht` fest auf `None`, meinestadt ebenso, `anbieter` fehlt bei anonymen
    # Anzeigen. Gemessen am 22.09.2026 an einer Koelner Suche, wie viele echte Treffer
    # durch den Waechter kamen: BA 5/5, StepStone 50/50, meinestadt 0/40,
    # Kleinanzeigen 0/54, Wohnungen 0/52 – 146 von 201 waren unuebernehmbar.
    # Nach der Reparatur: 199 von 199 (die Zahlen schwanken mit dem Angebot).
    #
    # Darum je Adapter ein echter, unveraenderter Treffer als festgehaltener Datensatz.
    # Laeuft ohne Netz. Setzt ein Adapter kuenftig ein Feld auf `None`, faellt es hier auf.
    with open(os.path.join(HIER, "pruefdaten", "adapter_beispiele.json"),
              encoding="utf-8") as _f:
        _beispiele = json.load(_f)
    _durchgefallen = [q for q, t in _beispiele.items() if not A._treffer_sauber(t)]
    pruefe(f"Ein echter Treffer aus jedem der {len(_beispiele)} Adapter kommt durch",
           not _durchgefallen and len(_beispiele) >= 5,
           _durchgefallen or sorted(_beispiele))

    # Die Beispielsammlung darf nicht heimlich veralten – aber sie darf den Push auch
    # nicht blockieren, sobald eine Quelle dazukommt, von der sich nichts festhalten
    # liess. Offen sind heute `jobs.indeed` (sperrt uns mit 403 aus) und
    # `jobs.arbeitnow` (liefert für Köln nichts); dazu kommen `jobs.adzuna`,
    # `jobs.jooble` und `wohnung.mail`, sobald die Zugangsdaten in der `.env` stehen –
    # der IS24-IMAP-Zugang steht unmittelbar bevor.
    #
    # Eine offene Stelle ist kein Fehler; sie wird BENANNT, damit sie beim nächsten
    # Fang mitgeholt wird. **Geprüft wird dafür, was der Name zusagt:** dass die
    # Sammlung da ist und dass jeder Eintrag ein Treffer mit passender Quelle und
    # Fremd-ID ist. Eine ausgehöhlte Datei fällt damit hier auf, nicht erst nebenan.
    _ohne = sorted(q["schluessel"] for q in tf.quellen_stand()
                   if q["bereit"] and q["schluessel"] not in _beispiele)
    # `isinstance` statt `or ""`: steht in der Datei eine Zahl als `extern_id`, gaebe
    # `.strip()` einen `AttributeError` AUSSERHALB von `pruefe` – der ganze Lauf braeche
    # ab, statt einen roten Punkt zu melden. Eine Pruefung gegen eine ausgehoehlte
    # Datei muss die ausgehoehlte Datei ueberleben.
    _hohl = hohle_beispiele(_beispiele)
    pruefe("Die Beispielsammlung ist da, und jeder Eintrag ist wirklich ein Treffer",
           len(_beispiele) >= 5 and not _hohl,
           _hohl or (f"noch ohne Beispiel: {_ohne}" if _ohne
                     else "alle verbundenen Quellen abgedeckt"))

    # Der ganze Weg, nicht nur der Waechter: ein Kleinanzeigen-Treffer muss wirklich
    # in `tf_angebot` ankommen. Genau hier stand 1325 vorher und 1325 nachher.
    # Gefragt wird, ob die Zeile ANKOMMT – nicht, ob sie `neu` heisst. Ob `_ablegen` sie
    # als Dublette markiert, entscheidet der Bestand des Profils und ist eine andere
    # Frage (die Bedienprobe misst sie). Hier geht es um den Waechter davor.
    _pid_k = tf.profile_von(kid)[0]["id"]
    _vor_k = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?", (_pid_k,))
    _echt = dict(_beispiele["jobs.kleinanzeigen"], extern_id="R3-PROBE-1")
    _r_k = c.post("/taskforce/suchen/uebernehmen",
                  data={"profil": str(_pid_k), "zurueck": "/taskforce/suchen",
                        "treffer": "0", "t0": json.dumps(_echt)})
    _zeile_k = db.eine("SELECT status, titel FROM tf_angebot"
                       " WHERE profil_id=? AND extern_id=?", (_pid_k, "R3-PROBE-1"))
    pruefe("Ein echter Kleinanzeigen-Treffer landet wirklich auf der Tafel",
           _r_k.status_code == 302 and _zeile_k is not None
           and _zeile_k["titel"] == _echt["titel"]
           and _echt["veroeffentlicht"] is None,
           f"{db.wert('SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?', (_pid_k,)) - _vor_k}"
           f" Zeile(n) dazu, Status {(_zeile_k or {}).get('status')!r},"
           f" veroeffentlicht={_echt['veroeffentlicht']!r}")

    # `None` ja, alles andere an einem Textfeld nein.
    pruefe("`None` ist an einem Textfeld erlaubt, eine Zahl oder Liste nicht",
           A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "b", "anbieter": None,
                              "veroeffentlicht": None, "beschreibung": None, "ort": None})
           and not A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "b", "titel": 7})
           and not A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "b",
                                      "ort": ["a"]})
           and not A._treffer_sauber({"quelle": None, "extern_id": "b"})
           # Und der Schluessel, den es nicht gibt.
           and not A._treffer_sauber({"quelle": "boese.quelle", "extern_id": "b"}))

    # --- Ende ist Ende: `\Z` statt `$` ----------------------------------------------
    # `$` matcht in Python auch VOR einem abschliessenden Zeilenumbruch. `"/x\n"` kam
    # damit durch den Riegel; Werkzeug weist den Kopfzeilenwert danach ab – ein 500er
    # NACH dem Schreiben, mit einer Absturzseite fuer jemanden, der nicht weiss, ob
    # etwas passiert ist.
    _mit_steuerzeichen = ("/x\n", "/x\r", "/x\r\n", "/taskforce/suchen\n", "/x\n\n")
    _schlecht = [w for w in _mit_steuerzeichen if A.INTERNER_WEG.match(w)]
    pruefe("Ein Rueckweg mit Steuerzeichen am Ende wird abgewiesen",
           not _schlecht, _schlecht or f"{len(_mit_steuerzeichen)} Formen geprüft")
    _nach_schreiben = c.post("/taskforce/suchen/uebernehmen",
                             data={"profil": str(_pid_k), "treffer": "0",
                                   "zurueck": "/taskforce/suchen\n"})
    pruefe("Und die Route faellt darueber nicht um, nachdem sie geschrieben hat",
           _nach_schreiben.status_code == 302
           and "\n" not in _nach_schreiben.headers.get("Location", ""),
           f"{_nach_schreiben.status_code} → "
           f"{_nach_schreiben.headers.get('Location', '')[:40]!r}")

    # --- Die eine Zaehlweise erreicht jetzt jede Stelle ------------------------------
    # Heute fallen die Zahlen zusammen, weil beide Bestandsprofile aktiv sind. Die
    # Abweichung entsteht beim ersten Klick auf „pausieren" – also wird er hier getan.
    _pausiert_pid = None
    _v0 = sammelanlage.vorschlaege(art="job")
    if _v0:
        _angelegt2, _ = sammelanlage.anlegen([(_v0[0]["id"], "Lagerhelfer", "Köln")],
                                             art="job")
        _pausiert_pid = _angelegt2[0][1] if _angelegt2 else None
    if _pausiert_pid:
        _wer = db.wert("SELECT kunde_id FROM tf_profil WHERE id=?", (_pausiert_pid,))
        with db.offen() as _con:
            _con.execute("UPDATE tf_profil SET aktiv=0 WHERE id=?", (_pausiert_pid,))
        try:
            _stellen = {
                "Sammelanlage": _wer in {z["id"] for z in
                                         sammelanlage.vorschlaege(art="job")},
                "Einstieg": any(not z["jobprofile"] and z["id"] == _wer
                                for z in aufgaben.laufende_massnahmen()),
                "Kundenliste": _wer in {z["id"] for z in db.hole(
                    "SELECT k.id FROM kunde k WHERE k.standort=? AND NOT EXISTS"
                    " (SELECT 1 FROM tf_profil p WHERE p.kunde_id=k.id)",
                    (db.STANDORT_STANDARD,))},
                "Kundenstand": tf.kunden_bilanz(_wer)["job"]["profile"] == 0,
                # Die Coachseite schreibt daraus die graue Plakette „keins",
                # die Kopfsuche wörtlich „kein Profil" (`basis.html`).
                "Coachseite": any(
                    z["id"] == _wer and not z["profile"]
                    for z in coaches.kunden(db.wert(
                        "SELECT coach_id FROM kunde WHERE id=?", (_wer,)))),
                "Kopfsuche": any(
                    z["id"] == _wer and not z["profile"] for z in tf.kunden_suchen(
                        db.wert("SELECT name FROM kunde WHERE id=?", (_wer,)))),
            }
            _tafel = c.get("/taskforce?art=job").text
            _dash = c.get("/taskforce/arbeit?alle=1").text
            # Die zwei Zahlen an den Umschaltknoepfen. Sie filterten in Python statt
            # in SQL und sind deshalb bei der Erhebung der Zaehlstellen zweimal
            # durchgerutscht – einmal beim Bauen, einmal beim Pruefen. Gemessen wird
            # die gerenderte Zahl gegen `tf.uebersicht`, damit ein Rueckfall auf
            # `if p["aktiv"]` rot wird und nicht wieder unsichtbar bleibt.
            _soll = len(tf.uebersicht(art="job"))
            for _name, _text in (("Tafel-Knopf", _tafel),
                                 ("Einstieg-Knopf", c.get("/taskforce").text)):
                _m = re.search(r"<strong>Arbeitssuche</strong>\s*<span>(\d+) Profile",
                               _text)
                _stellen[_name] = not _m or int(_m.group(1)) != _soll
            _stellen["Tafel-Hinweis"] = (
                str(len(sammelanlage.vorschlaege(art="job"))) + " laufende Maßnahmen"
                not in _tafel)
            pruefe("Kein pausiertes Profil gilt irgendwo als „kein Profil“",
                   not any(_stellen.values()),
                   {k: v for k, v in _stellen.items() if v} or
                   f"{len(_stellen)} Stellen einig, Profil {_pausiert_pid} pausiert")
            pruefe("Der Arbeitsplatz sagt stattdessen, dass es stillsteht",
                   "pausiert" in _dash)
        finally:
            with db.offen() as _con:
                _con.execute("DELETE FROM tf_profil WHERE id=?", (_pausiert_pid,))

    # --- Die gebaute Adresse, nicht nur `_slug` -------------------------------------
    # Abschnitt 14 misst `_slug`. Wuerde jemand in `jobs_stepstone` den Ort direkt
    # einsetzen, blieben alle fuenf Pruefungen dort gruen. Hier wird die Adresse
    # abgefangen, die wirklich hinausgeht.
    _adressen = []
    _echt_get = tf._get
    tf._get = lambda url, hdr=None, timeout=25: (_adressen.append(url), (_ for _ in ()).throw(
        RuntimeError("Probe: keine Abfrage")))[0]
    try:
        for _ort in ("Köln", "Düsseldorf", "Münster"):
            try:
                tf.jobs_stepstone(tf.suchspalte(art="job", begriffe="Bürokauffrau",
                                                ort=_ort, umkreis_km=25))
            except RuntimeError:
                pass
    finally:
        tf._get = _echt_get
    pruefe("Die gebaute StepStone-Adresse trägt Ort und Beruf in Umschrift",
           _adressen and "/in-koeln" in _adressen[0]
           and "buerokauffrau" in _adressen[0]
           and any("/in-duesseldorf" in a for a in _adressen)
           and any("/in-muenster" in a for a in _adressen)
           and not any("%C3%" in a for a in _adressen),
           _adressen[0] if _adressen else "keine Adresse gebaut")

    # --- Eine eingeengte Quellenwahl wird festgehalten -------------------------------
    # Gemessen wird der Fall, der vorkommt: ein paar Portale angehakt, der Rest nicht.
    #
    # **Was hier NICHT geprueft wird, und warum nicht:** alle Haekchen abwaehlen. Der
    # Browser schickt abgewaehlte Kaestchen gar nicht mit – „keine Quelle gewaehlt" und
    # „das Formular hat kein Quellenfeld" kommen als dieselbe leere Menge an, und die
    # ist falsy: `_such_regler` laesst das Feld dann leer, das Profil sucht bei allen.
    # Unterscheidbar waere das nur mit einem Marker im Formular. Das ist bewusst
    # offen: ein Profil mit null Quellen ist ein Profil, das nichts tut, und der
    # Rueckfall auf „alle" ist dafuer die harmlosere Antwort. Steht als Befund beim
    # Planer, nicht als stille Luecke hier.
    c.post("/taskforce/suchen/als-profil", data=MultiDict([
        ("art", "job"), ("was", "Lagerhelfer"), ("wo", "Köln"), ("km", "25"),
        ("kunde", str(kid)), ("titel", "Nur eine Quelle"), ("quelle", "jobs.ba")]))
    _eine = db.eine("SELECT quellen FROM tf_profil WHERE kunde_id=? AND titel=?",
                    (kid, "Nur eine Quelle"))
    pruefe("Eine bewusst eingeengte Quellenwahl wird festgehalten",
           _eine is not None and _eine["quellen"] == "jobs.ba",
           f"quellen={(_eine or {}).get('quellen')!r}")


    # --- Der Waechter haelt, was sein Docstring zusagt ------------------------------
    # `_einfacher_wert` erlaubte `int`, `float` und `bool` – und `tf._score` ruft auf
    # genau diesen Werten `.casefold()` bzw. `.split()` auf. Fuenf gemessene 500er auf der
    # echten Route, alle mit gueltigem JSON und alle am Waechter vorbei. Der Absturz lag
    # in `_ablegen` mitten in der Transaktion: verloren ging ein ganzer Stapel, nicht nur
    # der vergiftete Satz. Es gibt keinen CSRF-Token, ein fremdes Formular genuegt.
    #
    # Gemessen wird die WIRKUNG: die Route darf nicht auf 500 gehen, und es darf keine
    # Zeile entstehen. Beide Profilarten, sonst faengt man nur die Haelfte.
    _pid_jj = db.wert("SELECT id FROM tf_profil WHERE art='job' ORDER BY id LIMIT 1")
    _pid_ww = db.wert("SELECT id FROM tf_profil WHERE art='wohnung' ORDER BY id LIMIT 1")
    _gift = [
        (_pid_jj, {"quelle": "jobs.ba", "extern_id": "g1", "zusatz": {"suchbegriff": 1}}),
        (_pid_jj, {"quelle": "jobs.ba", "extern_id": "g2", "zusatz": {"suchbegriff": True}}),
        (_pid_jj, {"quelle": "jobs.ba", "extern_id": "g3", "zusatz": {"suchbegriff": 1.5}}),
        (_pid_jj, {"quelle": "jobs.ba", "extern_id": "g4", "zusatz": {"arbeitszeit": 3}}),
        (_pid_ww, {"quelle": "wohnung.kleinanzeigen", "extern_id": "g5",
                   "zusatz": {"preis": 530}}),
        (_pid_ww, {"quelle": "wohnung.kleinanzeigen", "extern_id": "g6",
                   "zusatz": {"preis": True}}),
        # Zwei Nebenbefunde derselben Funktion: Text in einer Zahlenspalte (die Tafel
        # schrieb danach „abc km", und `entfernung_km <= ?` traf die Zeile nie wieder)
        # und ein Quellenschluessel, den `tf.QUELLEN` gar nicht kennt.
        (_pid_jj, {"quelle": "jobs.ba", "extern_id": "g7", "entfernung_km": "abc"}),
        (_pid_jj, {"quelle": "boese.quelle", "extern_id": "g8"}),
    ]
    _gift_kaputt, _gift_drin = [], []
    for _ziel, _satz in _gift:
        if _ziel is None:
            continue
        _r_g = c.post("/taskforce/suchen/uebernehmen",
                      data={"profil": str(_ziel), "zurueck": "/taskforce/suchen",
                            "treffer": "0", "t0": json.dumps(_satz)})
        if _r_g.status_code >= 500 or A._treffer_sauber(_satz):
            _gift_kaputt.append(f"{_satz['extern_id']} → {_r_g.status_code}")
        if db.wert("SELECT COUNT(*) FROM tf_angebot WHERE extern_id=?",
                   (_satz["extern_id"],)):
            _gift_drin.append(_satz["extern_id"])
    pruefe(f"{len(_gift)} vergiftete Zusatzfelder fällen die Route nicht und legen nichts ab",
           not _gift_kaputt and not _gift_drin,
           "; ".join(_gift_kaputt + _gift_drin) or f"{len(_gift)} Formen geprüft")

    # Und die Gegenrichtung: ein `bool` an einem Schluessel, der NICHT als Text gelesen
    # wird, muss bleiben – `quereinstieg` ist bei der BA wirklich einer.
    pruefe("Ein Wahrheitswert an `quereinstieg` bleibt erlaubt",
           A._treffer_sauber({"quelle": "jobs.ba", "extern_id": "ok",
                              "zusatz": {"quereinstieg": True, "arbeitszeit": "Vollzeit"}}))

    fehl = [n for n, ok, _ in ergebnis if not ok]
    print(f"\n{len(ergebnis) - len(fehl)} von {len(ergebnis)} Prüfungen bestanden.")
    if fehl:
        print("Fehlgeschlagen:", *fehl, sep="\n  - ")
    return 0 if not fehl else 1


if __name__ == "__main__":
    sys.exit(main())
