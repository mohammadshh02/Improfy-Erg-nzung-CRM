# -*- coding: utf-8 -*-
"""Der Einstieg der Taskforce – den Leerlauf zeigen statt Zahlen zu zeigen.

**Warum das gefehlt hat.** Die Tafel meldet 754 gefundene Angebote. Was sie nicht meldet:
dass davon nichts bei einem Menschen in einer laufenden Maßnahme ankommt. Wer die Tafel
aufschlägt, sieht Fülle und übersieht den Stillstand – genau derselbe Fehler, den
`aufgaben.py` für den Träger schon einmal korrigiert hat: ein Bericht, den niemand in eine
Handlung übersetzt, ändert nichts.

Dieses Modul rechnet darum nichts Neues aus. Es stellt Vorhandenes so nebeneinander, dass
die Lücke sichtbar wird:

  `kette()`           die fünf Stufen von der laufenden Maßnahme bis zur Antwort in einer
                      Reihe, mit jeder Stelle markiert, an der nichts weiterfließt.
  `aussenstehend()`   die Treffer, die *nicht* zu dieser Menge gehören – sie verschwinden nicht.
  `arbeitsliste()`    **was die Seite als Liste zeigt:** ein Mensch, eine Zeile – Name, Coach,
                      was fehlt, ein Knopf. Vereinigt aus den beiden Hälften darunter.
  `alle_handgriffe()` die eine Hälfte: wer einen offenen Taskforce-Handgriff hat.
  `laufende()`        die andere: die Menschen, für die gesucht werden muss, mit ihrem Stand.
  `beste_treffer()`   eine Kostprobe, damit der Einstieg nicht nur aus Zahlen besteht.

**Eine Grundgesamtheit für das ganze Band.** Alle fünf Stufen zählen dieselbe Menge
Menschen: die laufenden Maßnahmen (`aufgaben.LAUFEND`, Statuscode H und I). Das ist keine
Kosmetik, sondern der Unterschied zwischen einem Trichter und einer Zahlensammlung. Vorher
zählten Stufe 1–2 die laufenden Maßnahmen und Stufe 3–5 alle aktiven Suchprofile ohne
Rücksicht auf den Kundenstatus – auf dem Echtbestand kam dabei „0 Menschen haben ein
Suchprofil, aber 754 Treffer laufen" heraus. Als Trichter ist das arithmetisch unmöglich,
und es war genau die Unordnung, die diese Seite beseitigen soll.

Treffer außerhalb dieser Menge werden deshalb nicht weggelassen, sondern beschriftet
danebengestellt: `aussenstehend()`. Beide Hälften zusammen ergeben wieder die Zahl der
Tafel – der Selbsttest rechnet das nach. Es gibt also weiterhin keine zweite Rechenart.

Steht getrennt von `app.py` (1450 Zeilen), damit der Selbsttest die Rechnung direkt
anfassen kann, ohne eine Seite aufzurufen.
"""
import aufgaben
import datenbank as db
import taskforce as tf

# Wie viel auf einen Bildschirm passt. Bewusst keine Zahl aus dem heutigen Datenstand:
# eine Grenze, die zufällig genau so groß ist wie der Bestand, schneidet nie sichtbar ab
# und fällt darum erst auf, wenn ab dem ersten Überschreiten ein Mensch lautlos fehlt.
# `HANDGRIFFE` stand hier, solange Handgriffe und laufende Maßnahmen zwei Listen waren.
# Seit es eine Liste ist, begrenzt `LISTE` sie – eine zweite Grenze, die nichts begrenzt,
# liest sich wie eine Regel und ist keine.
LISTE = 10
KOSTPROBE = 5

# Die Arten der Aufgabenliste, die an der Taskforce hängen. „Nachfassen" gehört dazu:
# ein Anschreiben ohne zweiten Kontakt ist dieselbe verschenkte Arbeit wie ein fehlendes
# Suchprofil, nur eine Stufe später.
HANDGRIFF_ARTEN = ("Taskforce", "Nachfassen")


# ------------------------------------------------------------- Das Leerlaufband
def _kundenbedingung(drinnen=True):
    """SQL-Stück und Werte für „gehört zu einer laufenden Maßnahme" – oder eben nicht.

    `COALESCE` statt bloßem `NOT IN`, weil ein Kunde ohne Statuscode sonst durch beide
    Hälften fällt und die Summe nicht mehr die Zahl der Tafel ergibt."""
    platzhalter = ",".join("?" * len(aufgaben.LAUFEND))
    if drinnen:
        return f"k.status_code IN ({platzhalter})", tuple(aufgaben.LAUFEND)
    return f"COALESCE(k.status_code,'') NOT IN ({platzhalter})", tuple(aufgaben.LAUFEND)


def _je_status(drinnen=True):
    """Angebote je Status, getrennt nach Kundenstatus.

    Dieselbe Bedingung wie `tf.angebote_zaehlen()` (Standort, aktives Profil), nur
    zusätzlich am Kundenstatus geteilt."""
    wo, werte = _kundenbedingung(drinnen)
    return {z["status"]: z["n"] for z in db.hole(
        "SELECT a.status, COUNT(*) AS n"
        "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
        " WHERE p.standort=? AND p.aktiv=1 AND " + wo + " GROUP BY a.status",
        (db.STANDORT_STANDARD,) + werte)}


def aussenstehend():
    """Neue Treffer, die keinem Menschen in einer laufenden Maßnahme gehören.

    Sie sind nicht falsch, sie gehören nur nicht in diesen Trichter – etwa zu jemandem,
    dessen Maßnahme beendet ist, dessen Suchprofil aber weiterläuft. Weglassen wäre
    dasselbe Verschweigen, das dieses Band abschaffen soll; darum stehen sie beschriftet
    unter dem Band.

    `gut` sind davon die, die sich zu lesen lohnen: Relevanz ab 6 – dieselbe Schwelle wie
    `aufgaben.gute_angebote_liegen()`. Keine zweite Rechenart, nur dieselbe Zahl an einer
    zweiten Stelle. `treffer` und `leute` bleiben unverändert: an ihnen rechnet der
    Selbsttest nach, dass drinnen + außen wieder die Zahl der Tafel ergibt.

    **Zu `gut` gehört `gut_leute`, und das ist kein Zierrat.** `leute` zählt jeden, für
    den irgendein neuer Treffer liegt; `gut` zählt nur die hoch bewerteten Angebote. Ein
    Satz, der beide Zahlen zusammenspannt – „für 20 Menschen warten 3 gut passende
    Angebote" –, stellt 19 Menschen als versorgt dar, für die nichts Gutes daliegt. Wer
    über die guten Angebote spricht, zählt die Menschen aus derselben Menge."""
    wo, werte = _kundenbedingung(drinnen=False)
    z = db.eine(
        "SELECT COUNT(*) AS treffer, COUNT(DISTINCT k.id) AS leute,"
        "       SUM(CASE WHEN COALESCE(a.score,0)>=6 THEN 1 ELSE 0 END) AS gut,"
        "       COUNT(DISTINCT CASE WHEN COALESCE(a.score,0)>=6 THEN k.id END) AS gut_leute"
        "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
        " WHERE a.status='neu' AND p.standort=? AND p.aktiv=1 AND " + wo,
        (db.STANDORT_STANDARD,) + werte)
    return {"treffer": (z["treffer"] if z else 0) or 0,
            "leute": (z["leute"] if z else 0) or 0,
            "gut": (z["gut"] if z else 0) or 0,
            "gut_leute": (z["gut_leute"] if z else 0) or 0}


def _zustand(zahl, vorher):
    """Rot ist der Bruch: davor kam etwas an, hier nicht mehr.

    Gelb heißt, dass auch davor schon nichts ankam – die Stufe ist nicht kaputt, sie
    wartet auf die davor. Grün heißt schlicht: hier fließt etwas."""
    if zahl:
        return "p-gruen"
    return "p-rot" if vorher else "p-gelb"


def kette():
    """Die fünf Stufen vom Menschen bis zur Antwort – alle über dieselbe Menge Menschen.

    Das Ziel jeder Stufe ist kein Bericht, sondern die Seite, auf der man den Bruch behebt."""
    lauf = laufende_massnahmen()
    z = _je_status(drinnen=True)
    stufen = [
        ("in laufender Maßnahme", len(lauf),
         "Menschen in einer Maßnahme – für sie muss gesucht werden",
         "/kunden", "Kundenliste öffnen"),
        ("mit Suchprofil", len([k for k in lauf if k["profile"]]),
         "ohne Suchprofil sucht der Agent für diesen Menschen nichts",
         "/taskforce/profile-anlegen", "Profile anlegen"),
        ("neue Treffer für sie", z.get("neu", 0),
         "gefunden, aber von niemandem angefasst",
         "/taskforce/tafel", "Treffer ansehen"),
        ("angeschrieben", sum(z.get(s, 0) for s in ("angeschrieben", "antwort", "erfolg")),
         "ein gefundenes Angebot nützt erst etwas, wenn jemand schreibt",
         "/taskforce/tafel", "Angebote anschreiben"),
        ("Antworten", sum(z.get(s, 0) for s in ("antwort", "erfolg")),
         "zurückgekommen ist bisher das hier – nachfassen hilft",
         "/taskforce/tafel?status=angeschrieben", "Angeschriebene nachfassen"),
    ]
    reihe, vorher = [], None
    for name, zahl, satz, ziel, knopf in stufen:
        reihe.append({"name": name, "zahl": zahl, "zustand": _zustand(zahl, vorher),
                      "satz": satz, "ziel": ziel, "knopf": knopf})
        vorher = zahl
    return reihe


# ----------------------------------------------------------- Ein Mensch, ein Handgriff
def _knopf(a):
    """Die Aufschrift sagt, was der Knopf tut – nicht, wie die Regel heißt.

    Die Art zuerst: „Nachfassen" führt auf dieselbe Adresse wie ein fehlendes Suchprofil,
    meint aber etwas völlig anderes. Nach der Adresse allein stünde dort „Suchprofil"."""
    if a["art"] == "Nachfassen":
        return "nachfassen"
    link, titel = a["link"], a["titel"] or ""
    if "/lebenslauf" in link:
        return "Lebenslauf"
    if link.startswith("/taskforce/kunde/"):
        return "Suchprofil" if "Such-Profil" in titel else "Taskforce-Akte"
    if link.startswith("/taskforce"):
        return "Angebote ansehen"
    return "Akte öffnen"


def alle_handgriffe():
    """Jeder Mensch mit einem offenen Taskforce-Handgriff – genau einmal, dringendster zuerst.

    Wer fünf Lücken hat, stünde sonst fünfmal da und verdrängte vier andere Menschen.

    Quelle ist ausschließlich `aufgaben.alle()`: dieselben Regeln, dieselbe Zuständigkeit
    (Bearbeiter vor Coach vor Leitung), dieselben Ziele wie unter /aufgaben. Eine eigene
    Wiedervorlage-Abfrage hier hatte den Coach übersprungen – derselbe Vorgang hatte dann
    auf zwei Seiten zwei Zuständige."""
    je_person = {}
    for a in aufgaben.alle():
        if a["art"] not in HANDGRIFF_ARTEN or not a.get("link"):
            continue                       # ohne Ziel kein Knopf – und ohne Knopf keine Zeile
        schluessel = a.get("kunde_id") or a.get("kunde") or a["link"]
        vorher = je_person.get(schluessel)
        if vorher is None or (aufgaben.STUFEN.get(a["stufe"], 9)
                              < aufgaben.STUFEN.get(vorher["stufe"], 9)):
            je_person[schluessel] = a
    zeilen = sorted(je_person.values(),
                    key=lambda a: (aufgaben.STUFEN.get(a["stufe"], 9), a["kunde"] or ""))
    return [{"kunde": a["kunde"] or "—", "kunde_id": a.get("kunde_id"),
             "coach": a["coach"] or aufgaben.LEITUNG, "fehlt": a["titel"],
             "stufe": a["stufe"], "link": a["link"], "knopf": _knopf(a)}
            for a in zeilen]


# ----------------------------------------------------------- Wer gerade in Maßnahme ist
def laufende_massnahmen():
    """Die laufenden Maßnahmen – geliehen aus den Aufgabenregeln, nicht nachgebaut.

    Dieselbe Liste, aus der `ohne_taskforce` und `ohne_lebenslauf` ihre Aufgaben machen.
    Eine eigene Abfrage hier würde irgendwann anders zählen als die Aufgabenliste."""
    return aufgaben.laufende_massnahmen()


def _neu_je_kunde():
    """Neue Treffer je Mensch – dieselbe Bedingung wie das Band, nur ohne Kundenfilter."""
    return {z["kunde_id"]: z["neu"] for z in db.hole(
        "SELECT k.id AS kunde_id, COUNT(*) AS neu"
        "  FROM tf_angebot a JOIN tf_profil p ON p.id=a.profil_id JOIN kunde k ON k.id=p.kunde_id"
        " WHERE a.status='neu' AND p.aktiv=1 AND p.standort=? GROUP BY k.id",
        (db.STANDORT_STANDARD,))}


def laufende(limit=None):
    """Die Menschen in einer laufenden Maßnahme, mit dem, was an ihnen fehlt.

    `limit` schneidet ab, damit der Einstieg auf einen Bildschirm passt. Wie viele es
    insgesamt sind, steht in der ersten Stufe des Bandes – die Seite nennt beide Zahlen
    und verlinkt weiter, sonst fehlt ab der Grenze ein Mensch lautlos.

    `limit=0` heißt ganze Liste, nicht leere Liste – dieselbe Schranke wie in
    `arbeitsliste()`, die von hier ihre laufende Hälfte holt. Eine Liste, aus der man
    vereinigt, darf nicht vorher abgeschnitten sein."""
    neu = _neu_je_kunde()
    zeilen = [{"kunde_id": k["id"], "kunde": k["name"],
               "coach": k["coach"] or aufgaben.LEITUNG,
               "profile": k["profile"], "jobprofile": k["jobprofile"],
               "lebenslauf": bool(k["lebenslaeufe"]),
               "neu": neu.get(k["id"], 0),
               "ziel": f"/taskforce/kunde/{k['id']}/stand"}
              for k in laufende_massnahmen()]
    return zeilen[:LISTE] if limit is None else (zeilen[:limit] if limit else zeilen)


def arbeitsliste(limit=None):
    """Eine Zeile je Mensch – der Handgriff und der Stand desselben Menschen nebeneinander.

    Vorher standen dieselben Leute zweimal auf der Seite: einmal als offener Handgriff,
    einmal als laufende Maßnahme mit ihren Plaketten. Wer beides hatte, stand doppelt; wer
    nur eins hatte, sah aus wie zwei verschiedene Sorten Arbeit. Es ist eine Menge, darum
    ist es eine Liste.

    Vereinigt wird über `kunde_id`, und zwar in beide Richtungen: Wer einen Handgriff hat,
    aber keine laufende Maßnahme mehr, fällt **nicht** heraus – er steht mit
    `laufend=False` drin. Ein Handgriff ohne Kundennummer behält seine eigene Zeile,
    unterschieden über sein Ziel – genau so, wie `alle_handgriffe()` ihn vorher schon von
    den anderen unterscheidet. Über den Namen ginge das nicht: der ist dort „—", sobald
    keiner dasteht, und zwei Namenlose würden zu einem verschmelzen.

    `profile`, `jobprofile` und `lebenslauf` stehen nur für laufende Maßnahmen fest – nur
    dort sind sie gemessen (`aufgaben.laufende_massnahmen()`). Außerhalb bleiben sie
    `None`: „kein Profil" zu behaupten, wo nichts gezählt wurde, wäre eine erfundene
    Angabe. `jobprofile` steht neben `profile`, weil ein Wohnprofil zwar ein Profil ist,
    für die Arbeitssuche aber nichts sucht.

    `sammel` markiert die Zeilen, deren fehlendes Suchprofil schon in der Kopfzeile der
    Liste steht. In der Zeile bleibt der Satz dann weg – einmal gesagt genügt.

    `limit` ist hier die Schranke selbst: `None` oder `0` heißt ganze Liste. Die Seite holt
    alles und schneidet erst danach ab, damit die Zahl unter der Liste dieselbe Menge
    zählt, aus der die Liste ihre Zeilen nimmt."""
    zeilen = {}
    for m in laufende(limit=0):
        zeilen[m["kunde_id"]] = {
            "kunde": m["kunde"], "kunde_id": m["kunde_id"], "coach": m["coach"],
            "stufe": None, "profile": m["profile"], "jobprofile": m["jobprofile"],
            "lebenslauf": m["lebenslauf"],
            "neu": m["neu"], "laufend": True, "fehlt": None, "knopf": None,
            "link": None, "ziel": m["ziel"], "sammel": False}
    # Erst holen, wenn wirklich jemand ohne laufende Maßnahme auftaucht: die laufenden
    # bringen ihre Trefferzahl schon aus `laufende()` mit, aus derselben Abfrage.
    neu = None
    for h in alle_handgriffe():
        schluessel = h["kunde_id"] if h["kunde_id"] is not None else ("ziel", h["link"])
        z = zeilen.get(schluessel)
        if z is None:
            if neu is None:
                neu = _neu_je_kunde()
            z = zeilen[schluessel] = {
                "kunde": h["kunde"], "kunde_id": h["kunde_id"], "coach": h["coach"],
                "stufe": None, "profile": None, "jobprofile": None, "lebenslauf": None,
                "neu": neu.get(h["kunde_id"], 0), "laufend": False, "fehlt": None,
                "knopf": None, "link": None,
                "ziel": (f"/taskforce/kunde/{h['kunde_id']}/stand"
                         if h["kunde_id"] is not None else h["link"]),
                "sammel": False}
        z["stufe"] = h["stufe"]
        z["fehlt"] = h["fehlt"]
        z["link"] = h["link"]
        z["knopf"] = h["knopf"]
        # Genau die Zeilen, deren Satz oben in der Kopfzeile steht: laufende Maßnahme,
        # kein Suchprofil, und der Handgriff ist eben dieses fehlende Profil.
        z["sammel"] = bool(z["laufend"] and not z["profile"] and z["knopf"] == "Suchprofil")
    liste = sorted(zeilen.values(),
                   key=lambda z: (aufgaben.STUFEN.get(z["stufe"], 9), z["kunde"] or ""))
    return liste[:limit] if limit else liste


def beste_treffer(limit=None):
    """Eine Kostprobe von der Tafel – damit der Einstieg nicht nur Zahlen zeigt."""
    return tf.neue_angebote(limit=KOSTPROBE if limit is None else limit)
