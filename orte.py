# -*- coding: utf-8 -*-
"""Zerlegt das Feld `ort_jc` in Stadt, Postleitzahl und Jobcenter.

In den Listen steht beides in einer Zelle, und zwar in jeder denkbaren Form:

    "51103 Köln – JC Köln"
    "Siegen – JC Siegen (Dienststelle 381)"
    "JC Jülich (Kreis Düren)"
    "JC Essen (alt) → Jobcenter gewechselt"
    "weit außerhalb – JC unbekannt"

Solange das so bleibt, laesst sich nicht beantworten, aus welcher Stadt die
Kunden kommen - und genau das ist die Frage, an der sich Zustaendigkeiten
entscheiden. Mehrere Absagen im Bestand haengen daran, dass ein auswaertiges
Jobcenter einen Koelner Traeger nicht bezahlt.

Der Parser ist bewusst konservativ: was er nicht sicher erkennt, bleibt leer
statt geraten zu werden. Eine falsche Stadt waere schlimmer als keine.
"""
import re

# Wortteile, die nie eine Stadt sind
KEINE_STADT = {
    "jc", "jobcenter", "agentur", "arbeit", "unbekannt", "alt", "neu",
    "weit", "außerhalb", "ausserhalb", "dienststelle", "kreis", "aör", "aor",
    # Vermerke, die wie ein Ortsname aussehen, aber keiner sind
    "ba-kunde", "ba-kundin", "ba", "messe", "lt", "wohnhaft", "hubspot",
}

PLZ = re.compile(r"\b(\d{5})\b")
# "JC Köln", "Jobcenter Wuppertal", "JC Rhein-Erft"
JC_STADT = re.compile(r"(?:JC|Jobcenter)\s+([A-ZÄÖÜ][\wÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß\-]+)?)")


def _saeubern(text):
    text = re.sub(r"\(.*?\)", " ", text)        # Klammerzusätze raus
    text = re.sub(r"[→>]+.*$", " ", text)       # "→ Jobcenter gewechselt"
    return " ".join(text.split()).strip(" -–—,;")


def zerlege(ort_jc):
    """-> (plz, stadt, jobcenter). Was unklar ist, bleibt None."""
    if not ort_jc or not ort_jc.strip():
        return None, None, None
    roh = ort_jc.strip()

    plz = None
    m = PLZ.search(roh)
    if m:
        plz = m.group(1)

    # Die Zelle trennt Wohnort und Jobcenter meist mit einem Gedankenstrich.
    teile = re.split(r"\s[–—-]\s", roh, maxsplit=1)
    links = _saeubern(teile[0])
    rechts = _saeubern(teile[1]) if len(teile) > 1 else ""

    jobcenter = rechts or (links if re.match(r"^(JC|Jobcenter|Agentur)", links) else None)

    # Stadt: erst aus dem linken Teil, sonst aus dem Namen des Jobcenters.
    stadt = None
    kandidat = PLZ.sub("", links).strip()
    woerter = [w for w in kandidat.split() if w.casefold() not in KEINE_STADT]
    if woerter and not re.match(r"^(JC|Jobcenter|Agentur)", kandidat):
        stadt = " ".join(woerter)
    if not stadt and jobcenter:
        m = JC_STADT.search(jobcenter)
        if m:
            wort = m.group(1).strip()
            if wort.split()[0].casefold() not in KEINE_STADT:
                stadt = wort

    if stadt:
        stadt = stadt.strip(" ,.;-–")
        if len(stadt) < 3 or stadt.casefold() in KEINE_STADT:
            stadt = None
    return plz, stadt, (jobcenter or None)


def nachtragen():
    """Füllt plz/stadt/jobcenter für alle Kunden. Gibt (gefüllt, offen) zurück."""
    import datenbank
    with datenbank.offen() as con:
        vorhanden = {r[1] for r in con.execute("PRAGMA table_info(kunde)")}
        for spalte in ("plz", "stadt", "jobcenter"):
            if spalte not in vorhanden:
                con.execute(f"ALTER TABLE kunde ADD COLUMN {spalte} TEXT")

        gefuellt = offen = 0
        for r in con.execute("SELECT id, ort_jc FROM kunde").fetchall():
            plz, stadt, jc = zerlege(r["ort_jc"])
            con.execute("UPDATE kunde SET plz=?, stadt=?, jobcenter=? WHERE id=?",
                        (plz, stadt, jc, r["id"]))
            if stadt:
                gefuellt += 1
            else:
                offen += 1
    return gefuellt, offen
