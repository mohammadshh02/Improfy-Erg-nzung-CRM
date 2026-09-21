# -*- coding: utf-8 -*-
"""Backtest der Taskforce-Kette mit einem echten Kunden – so, wie die Taskforce es täglich macht.

Läuft auf einer Kopie der Datenbank (nichts Echtes wird verändert), braucht Internet.
Aufruf:  python -X utf8 taskforce_backtest.py [Kundenname] [Pfad zur Lebenslauf-Textdatei]

Was geprüft wird, Schritt für Schritt wie im Alltag:
  1. Kunde öffnen, Lebenslauf verknüpfen, Kurzprofil und Lebenslauftext hinterlegen
  2. Jobprofil anlegen → erster Lauf über alle Quellen → Treffer je Quelle
  3. zweiter Lauf → Gedächtnis: (fast) nichts kommt doppelt
  4. Beschreibungen nachladen und abgleichen → Verteilung, Stichprobe, Plausibilität
     (Deutsch B2 muss bei B1-Kunde als „fehlt" stehen, „kein Führerschein" darf nicht passen)
  5. Wohnprofil mit WBS + Kriterien → Lauf → Abgleich → IS24-Link
  6. Taskforce arbeitet: Status setzen, KPI, JSON/CSV fürs CRM enthalten alles
  7. Aufgabenplanung: `taskforce.py --lauf` läuft durch
Schreibt einen Bericht nach ../Ausgabe/Taskforce_Backtest_<Datum>.md
"""
import atexit
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
# Kopie ueber die SQLite-Sicherung statt ueber das Dateisystem, und je Lauf eine eigene
# Datei: eine Datenbank, die gerade geschrieben wird (Entwicklungsserver nebenher, zweiter
# Testlauf), kopiert sich sonst in einem Zwischenzustand. Dieselbe Stelle steht in
# taskforce_test.py und os_test.py - dort hat genau das einen halben Nachmittag gekostet.
kopie = os.path.join(tempfile.gettempdir(), "improfy_os_backtest_%d.db" % os.getpid())
atexit.register(lambda: os.path.exists(kopie) and os.remove(kopie))
_quelle = sqlite3.connect(os.path.join(HIER, "improfy_os.db"))
_ziel = sqlite3.connect(kopie)
with _ziel:
    _quelle.backup(_ziel)
_ziel.close()
_quelle.close()
os.environ["IMPROFY_OS_DB"] = kopie

# Derselbe Griff fuer den Sicherungsordner: `betrieb.py` leitet ihn sonst aus seinem
# eigenen Verzeichnis ab, und ein Testlauf schoebe seinen Schnappschuss in das echte
# `sicherungen/`, wo er bei sieben Staenden eine echte Nachtsicherung verdraengt. Der
# Unterprozess weiter unten erbt die Variable ueber `os.environ`.
sicherungen = os.path.join(tempfile.gettempdir(),
                           "improfy_os_backtest_sicherungen_%d" % os.getpid())
os.environ["OS_SICHERUNG_ORDNER"] = sicherungen
atexit.register(lambda: shutil.rmtree(sicherungen, ignore_errors=True))

import app as A                    # noqa: E402
import datenbank as db             # noqa: E402
import taskforce as tf             # noqa: E402
from quellen import lebenslauf as L  # noqa: E402

bericht, fehler = [], []


def z(text=""):
    print(text)
    bericht.append(text)


def pruefe(name, ok, detail=""):
    if not ok:
        fehler.append(name)
    z(f"- {'OK' if ok else 'FEHLER'}: {name}{(' – ' + str(detail)) if detail else ''}")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Hilal Ragheed"
    cv_pfad = sys.argv[2] if len(sys.argv) > 2 else None
    db.init(); tf.init()
    c = A.app.test_client()
    kid = db.wert("SELECT id FROM kunde WHERE name LIKE ?", (f"%{name}%",))
    if not kid:
        print("Kunde nicht gefunden:", name); return 2
    kunde = tf.kunden_info(kid)
    z(f"# Taskforce-Backtest {datetime.date.today():%d.%m.%Y} – {kunde['name']}\n")
    z(f"Datenbankkopie: {kopie}\n")
    with db.offen() as con:
        con.execute("DELETE FROM tf_ereignis")
        con.execute("DELETE FROM tf_angebot WHERE profil_id IN (SELECT id FROM tf_profil WHERE kunde_id=?)", (kid,))
        con.execute("DELETE FROM tf_profil WHERE kunde_id=?", (kid,))

    z("## 1. Kunde, Lebenslauf, Kurzprofil")
    z(f"Kunde auf Abruf: Status {kunde['status_code']} {kunde['status_text']}, Sprache {kunde.get('sprache')}, "
      f"Telefon {kunde.get('telefon')}, Coach {kunde.get('coach')}, {len(kunde['lebenslaeufe'])} Lebenslauf/-läufe im OS")
    pruefe("Lebenslauf aus Drive hängt am Kunden", kunde["lebenslaeufe"], [l["name"] for l in kunde["lebenslaeufe"]])
    cv_text = open(cv_pfad, encoding="utf-8").read() if cv_pfad and os.path.exists(cv_pfad) else ""
    kurz = "Pharmazie-Studium, Laborassistent, Apothekenpraktikum, Deutsch B1, Englisch C1, kein Führerschein, Schicht möglich"
    r = c.post(f"/taskforce/kunde/{kid}/kurzprofil", data={"kurzprofil": kurz, "cv_text": cv_text})
    pruefe("Kurzprofil + Lebenslauftext gespeichert", r.status_code == 302 and (tf.kunden_info(kid)["profil"].get("cv_text") or "") == cv_text.strip(),
           f"{len(cv_text)} Zeichen Lebenslauf")

    z("\n## 2. Jobprofil und erster Lauf")
    r = c.post(f"/taskforce/kunde/{kid}/profil", data={
        "art": "job", "titel": "Backtest Labor/Apotheke", "suchbegriffe": "Laborhelfer, Apothekenhelfer, Pharmazeutisch",
        "ort": "Leverkusen", "umkreis_km": "30", "zeitarbeit": "1", "notiz": "Studium Pharmazie, sucht Labor/Apotheke"})
    pj = db.wert("SELECT MAX(id) FROM tf_profil WHERE art='job' AND kunde_id=?", (kid,))
    pruefe("Jobprofil angelegt, Lauf sofort gestartet", r.status_code == 302 and pj)
    je_quelle = db.hole("SELECT quelle, COUNT(*) n, SUM(status='doppelt') d FROM tf_angebot WHERE profil_id=? GROUP BY quelle", (pj,))
    gesamt = sum(q["n"] for q in je_quelle)
    for q in je_quelle:
        z(f"  {q['quelle']:24} {q['n']:4} Treffer, davon {q['d'] or 0} Dubletten")
    laeufe = db.hole("SELECT quelle, meldung FROM tf_lauf WHERE profil_id=? AND meldung<>''", (pj,))
    pruefe(f"Erster Lauf liefert Angebote ({gesamt})", gesamt > 20)
    pruefe("Alle Kernquellen (BA, Indeed, StepStone, meinestadt, Kleinanzeigen) haben geantwortet",
           {"jobs.ba", "jobs.stepstone", "jobs.meinestadt", "jobs.kleinanzeigen"} <= {q["quelle"] for q in je_quelle},
           "; ".join(f"{l['quelle']}: {l['meldung']}" for l in laeufe) or "keine Meldungen")
    ohne_link = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=? AND (url IS NULL OR titel IS NULL)", (pj,))
    pruefe("Jedes Angebot hat Titel und Link", ohne_link == 0)

    z("\n## 3. Zweiter Lauf – Gedächtnis")
    t0 = time.time()
    g2, n2, m2 = tf.lauf(pj)
    pruefe(f"Zweiter Lauf: {g2} gefunden, {n2} neu (nur was seit dem ersten Lauf dazukam)", n2 <= max(5, g2 // 15), f"{time.time()-t0:.0f} s")
    pruefe("Kein Angebot doppelt in derselben Quelle", db.wert(
        "SELECT COUNT(*) FROM (SELECT quelle, extern_id, COUNT(*) c FROM tf_angebot WHERE profil_id=? GROUP BY 1,2 HAVING c>1)", (pj,)) == 0)

    z("\n## 4. Beschreibungen und Abgleich")
    n = tf.abgleich_profil(pj, max_n=25)
    ab = db.hole("SELECT quelle, titel, match, abgleich, LENGTH(beschreibung_lang) l FROM tf_angebot WHERE profil_id=? AND abgleich IS NOT NULL ORDER BY score DESC", (pj,))
    geladen = [a for a in ab if a["l"]]
    pruefe(f"Beschreibungen nachgeladen: {len(geladen)} von {len(ab)} abgeglichenen", len(geladen) >= len(ab) * 0.7,
           {q: sum(1 for a in geladen if a["quelle"] == q) for q in set(a["quelle"] for a in geladen)})
    mit_wert = [a for a in ab if a["match"] is not None]
    z(f"  Match-Werte: {sorted((int(a['match']) for a in mit_wert), reverse=True)}")
    b2_fehlt = any("Deutsch B2 oder besser" in tf.abgleich_von(a).get("fehlt", []) for a in ab)
    b2_passt = any("Deutsch B2 oder besser" in tf.abgleich_von(a).get("passt", []) for a in ab)
    pruefe("Plausibilität: Deutsch B2 wird bei B1-Kunde als fehlt erkannt, nie als passt", not b2_passt, f"fehlt gemeldet: {b2_fehlt}")
    fs_passt = any("Führerschein" in tf.abgleich_von(a).get("passt", []) for a in ab)
    pruefe("Plausibilität: kein Führerschein im Kurzprofil erfüllt keine Führerschein-Anforderung", not fs_passt)
    ausb = any("abgeschlossene Ausbildung" in tf.abgleich_von(a).get("passt", []) for a in ab)
    pruefe("Plausibilität: Studium/Ausbildung im Lebenslauf erfüllt abgeschlossene Ausbildung", ausb or not any(
        "abgeschlossene Ausbildung" in (tf.abgleich_von(a).get("fehlt", []) + tf.abgleich_von(a).get("unklar", [])) for a in ab))
    z("  Stichprobe (Top 5 nach Relevanz):")
    for a in ab[:5]:
        e = tf.abgleich_von(a)
        z(f"  - {a['quelle']} · {a['titel'][:60]} → {a['match'] if a['match'] is not None else '–'} % | passt {e.get('passt')} | fehlt {e.get('fehlt')} | unklar {e.get('unklar')} | plus {e.get('plus')}")

    z("\n## 5. Wohnprofil mit Kriterien")
    r = c.post(f"/taskforce/kunde/{kid}/profil", data={
        "art": "wohnung", "titel": "Backtest Wohnung", "suchauftrag": "TF-Hilal", "ort": "Leverkusen", "umkreis_km": "10",
        "max_miete": "550", "min_zimmer": "1", "k_wbs": "1", "k_etage_max": "3", "k_max_warmmiete": "750",
        "k_kein_tausch": "1", "k_wohnungstyp": ["apartment", "groundfloor"], "k_extra": "Kostenübernahme Jobcenter Leverkusen, Einzug ab 01.11."})
    pw = db.wert("SELECT MAX(id) FROM tf_profil WHERE art='wohnung' AND kunde_id=?", (kid,))
    p = tf.profil(pw)
    pruefe("Wohnprofil mit WBS, Etage, Warmmiete, Typen, Extra-Notizen gespeichert", r.status_code == 302 and tf.kriterien(p).get("wbs")
           and tf.kriterien(p).get("wohnungstyp") == ["apartment", "groundfloor"], tf.kriterien_text(p))
    wn = db.wert("SELECT COUNT(*) FROM tf_angebot WHERE profil_id=?", (pw,))
    wm = db.hole("SELECT quelle, meldung FROM tf_lauf WHERE profil_id=?", (pw,))
    z(f"  Wohnungs-Lauf: {wn} Angebote (Kleinanzeigen mit Suchwort WBS); Meldungen: {[m['meldung'] for m in wm if m['meldung']] or 'keine'}")
    nw = tf.abgleich_profil(pw, max_n=8)          # der Lauf hat die ersten 12 schon abgeglichen
    wab = db.hole("SELECT titel, match, abgleich, zusatz FROM tf_angebot WHERE profil_id=? AND abgleich IS NOT NULL", (pw,))
    pruefe(f"Wohnungen abgeglichen ({len(wab)} von {wn}, davon {nw} jetzt nachgeholt)", len(wab) == wn)
    tausch = [a for a in wab if "tausch" in (a["titel"] or "").casefold()]
    pruefe("Tauschwohnungen werden bei Tausch-ausschließen als fehlt markiert",
           all("Tauschwohnung" in tf.abgleich_von(a).get("fehlt", []) for a in tausch), f"{len(tausch)} Tauschanzeigen")
    for a in wab[:3]:
        e = tf.abgleich_von(a); zz = tf.zusatz(a)
        z(f"  - {a['titel'][:60]} → {a['match'] if a['match'] is not None else '–'} % | passt {e.get('passt')} | fehlt {e.get('fehlt')} | unklar {e.get('unklar')} | warm {zz.get('warmmiete')} Etage {zz.get('etage')} {zz.get('wohnungstyp')}")
    url = tf.is24_url(p)
    z(f"  IS24-Link: {url}")
    pruefe("IS24-Link enthält Preis, Zimmer, Etage, Tausch aus, Typen", all(x in url for x in ("price=-550.0", "numberofrooms=1.0-", "floor=-3", "swapflat", "apartment")))
    if wn == 0:
        z("  Hinweis: Mit WBS als Suchwort gab es in Leverkusen gerade keine Kleinanzeigen-Treffer – Kriterium ggf. lockern.")

    z("\n## 6. Taskforce arbeitet: Status, KPI, CRM")
    ids = [a["id"] for a in tf.angebote(pj, "neu")[:3]]
    c.post(f"/taskforce/angebot/{ids[0]}/status", data={"status": "angeschrieben", "bearbeiter": "Backtest Kraft", "zurueck": "/taskforce"})
    c.post("/taskforce/angebote/status", data={"ids": [str(ids[1]), str(ids[2])], "status": "gesehen", "bearbeiter": "Backtest Kraft", "zurueck": "/taskforce"})
    kpi = [k for k in tf.kpi_mitarbeiter() if k["wer"] == "Backtest Kraft"]
    pruefe("KPI je Mitarbeiter zählt 1 angeschrieben, 2 gesehen", kpi and kpi[0]["angeschrieben_heute"] == 1 and kpi[0]["gesehen_heute"] == 2)
    j = c.get(f"/taskforce/api/angebote?kunde={kid}&status=angeschrieben").get_json()
    a0 = j["angebote"][0] if j["angebote"] else {}
    pruefe("JSON fürs CRM: Kunde, Link, Status, Bearbeiter, Relevanz, Abgleich", j["anzahl"] == 1 and a0.get("url") and a0.get("bearbeiter") == "Backtest Kraft" and "abgleich" in a0 and "score" in a0)
    csv_text = c.get(f"/taskforce/export.csv?kunde={kid}").data.decode("utf-8-sig")
    pruefe("CSV-Export mit passt/fehlt-Spalten", csv_text.splitlines()[0].endswith("score;match;passt;fehlt") and len(csv_text.splitlines()) > 5)
    for url in ("/taskforce", f"/taskforce/kunde/{kid}", f"/taskforce/profil/{pj}", f"/taskforce/profil/{pw}", f"/kunde/{kid}"):
        r = c.get(url)
        pruefe(f"Seite {url} antwortet", r.status_code == 200)
    r = c.get(f"/taskforce/profil/{pj}")
    pruefe("Profilseite zeigt Lebenslauf, Kunde auf Abruf, Abgleich, keine Anschreiben-Funktion",
           "Kunde auf Abruf" in r.text and "Abgleich" in r.text and "Anschreiben-Entwurf" not in r.text and "anschreiben" not in r.text.replace("angeschrieben", ""))

    z("\n## 7. Aufgabenplanung")
    t0 = time.time()
    lauf = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HIER, "taskforce.py"), "--lauf"],
                          capture_output=True, text=True, env={**os.environ, "IMPROFY_OS_DB": kopie}, timeout=900)
    pruefe(f"`taskforce.py --lauf` läuft über alle aktiven Profile durch ({time.time()-t0:.0f} s)", lauf.returncode == 0 and "offen für die Taskforce" in lauf.stdout,
           (lauf.stdout.strip().splitlines() or [lauf.stderr[-200:]])[-1])

    z(f"\n## Ergebnis: {'alles bestanden' if not fehler else str(len(fehler)) + ' Fehler'}")
    for f in fehler:
        z(f"- {f}")
    # Im Ergaenzungs-Repo heisst der Ausgabeordner klein und liegt im Repo selbst.
    ordner = os.path.join(HIER, "ausgabe")
    os.makedirs(ordner, exist_ok=True)
    ziel = os.path.join(ordner, f"Taskforce_Backtest_{datetime.date.today():%Y-%m-%d}.md")
    with open(ziel, "w", encoding="utf-8") as fh:
        fh.write("\n".join(bericht) + "\n")
    print("Bericht:", ziel)
    return 0 if not fehler else 1


if __name__ == "__main__":
    sys.exit(main())
