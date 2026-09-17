# -*- coding: utf-8 -*-
"""Die Datenkarte des CRM – was es führt, wie es heißt, wo unsere PDFs hingehören.

**Woher das stammt.** Am 17.09.2026 am laufenden System nachgesehen, nicht geraten und
nicht aus dem Quelltext gelesen (auf das Repository `improfy-hub/improfy_crm` hat unser
Konto keinen Zugriff). Das CRM ist eine **Laravel-Anwendung mit Inertia.js** – und das ist
der entscheidende Fund für die Anbindung:

> **Jede Seite des CRM liefert bereits JSON.** Wer eine Seite mit den Kopfzeilen
> `X-Inertia: true` und `X-Inertia-Version: <version>` abruft, bekommt statt HTML die
> Daten, aus denen die Seite gebaut wird. Das CRM hat also faktisch schon eine
> Schnittstelle – sie muss nicht erst gebaut werden.

Die Einschränkung: Diese Antworten hängen an der **Sitzung** (Cookie), nicht an einem
Token. Für einen Dienst, der nachts von selbst läuft, ist das zu wenig – dafür braucht es
vom CRM-Entwickler einen Token-Zugang. Aber: Er muss dafür keine neue Schnittstelle
erfinden, sondern nur die vorhandenen Antworten mit einem Token absichern. Das ist der
Unterschied zwischen einem Nachmittag und zwei Wochen Arbeit.

Diese Datei ist bewusst nur **Wissen**, kein Code, der etwas tut: die Feldnamen, die
Statuswerte, die Ablagefächer. Damit lässt sich die Anbindung bauen, sobald der Zugang
steht – und in der Zwischenzeit sieht man, was wohin gehört.
"""

# --------------------------------------------------------------- Technischer Aufbau
AUFBAU = {
    "framework": "Laravel + Inertia.js",
    "kopfzeilen": {"X-Inertia": "true", "X-Inertia-Version": "<aus dem data-page-Attribut>",
                   "X-Requested-With": "XMLHttpRequest"},
    "antwort": "JSON mit component, props, url, version",
    "anmeldung": "Sitzungscookie (kein Token) – genau hier fehlt noch etwas",
    "seiten": ["/dashboard", "/kunden", "/kunden/<id>", "/kalender", "/dokumente", "/verwaltung"],
    "fehler_409": "falsche oder fehlende X-Inertia-Version",
}

# ------------------------------------------------------------------ Standorte
STANDORTE = {1: "Berlin", 2: "Hamburg", 3: "Köln", 4: "Frankfurt", 5: "Stuttgart", 6: "Mannheim"}

# ------------------------------------------------- Felder eines Kunden (/kunden/<id>)
KUNDE_FELDER = [
    "id", "salutation", "name", "customer_number", "bg_number", "phone", "email",
    "birth_date", "address", "language", "cost_bearer", "case_worker", "branch", "coach",
    "extra_coaches", "measure", "status", "status_label", "requirements", "notes",
    "avgs", "modules", "appointments", "status_logs", "survey", "profilings",
]
LISTE_FELDER = ["id", "name", "salutation", "customer_number", "branch", "coach",
                "measure", "status", "status_label", "account"]

# Das Unterrichtseinheiten-Konto je Kunde. `remaining` ist der Auftragsbestand –
# bewilligte, noch nicht geleistete UE. Mal dem Satz je UE ergibt das den
# vertraglich gesicherten Umsatz, den ein Käufer als Erstes sehen will.
KONTO_FELDER = ["approved", "planned", "completed", "cancelled", "booked", "remaining",
                "percent", "completed_percent", "planned_percent", "is_exhausted",
                "is_running_low"]

# ----------------------------------------------------------------- Statuswerte
# Links das CRM, rechts der Buchstabe, den unser Bestand führt. Die Zuordnung ist
# bewusst grob: Das CRM kennt den Weg genauer, unsere Buchstaben stammen aus der
# alten Excel-Welt und verschwinden mit ihr.
STATUS = {
    "angelegt": "A", "bewilligt": "F", "aktiv": "H", "zwischenbericht": "I",
    "abschlussphase": "I", "abgeschlossen": "K", "pausiert": "I", "abgebrochen": "L",
}
STATUS_TEXT = {
    "angelegt": "Angelegt", "bewilligt": "Bewilligt", "aktiv": "Aktiv",
    "zwischenbericht": "Zwischenbericht fällig", "abschlussphase": "Abschlussphase",
    "abgeschlossen": "Abgeschlossen", "pausiert": "Pausiert", "abgebrochen": "Abgebrochen",
}
LAEUFT = ("aktiv", "zwischenbericht", "abschlussphase")

# ------------------------------------------------------------- Ablage (20 Pflichtpunkte)
# Drei Gruppen, je Fach eine Kennung. Die mit Stern sind Pflicht.
ABLAGE = {
    "massnahme": ["erhebungsbogen*", "profiling*", "avgs*", "traegerbestaetigung*",
                  "bewilligung*", "tn_bericht*", "tn_vertrag*", "zielvereinbarung*",
                  "protokolle*", "anwesenheit*", "empfangsbestaetigung*",
                  "datenschutz* (nicht bei All Care)", "abschlussbericht*",
                  "vermittlung* (Modul 4 & Turbofy)", "gespraechsnotiz"],
    "dokumente": ["extern"],
    "endergebnis": ["tn_bescheinigung*", "bewerbungsfotos* (Modul 2 & Turbofy)",
                    "lebenslauf* (Modul 2, 4 & Turbofy)", "lebenslauf_alt",
                    "bewerbungsvideo", "anschreiben* (Modul 4 & Turbofy)",
                    "abbruchmeldung", "ki_auswertung* (Modul 2, 4 & Turbofy)"],
}
MAX_KB = 15360      # 15 MB je Datei, vom CRM vorgegeben

# **Hier docken wir an.** Was dieses Paket erzeugt und in welches Fach es gehört:
UNSERE_FAECHER = {
    "lebenslauf": "endergebnis/lebenslauf",
    "lebenslauf_alt": "endergebnis/lebenslauf_alt",
    "bewerbungsfoto": "endergebnis/bewerbungsfotos",
    "vermittlungsaktivitaeten": "massnahme/vermittlung",
}

# Pflichtfächer, die heute niemand automatisch füllt – hier liegt der nächste Nutzen:
NOCH_VON_HAND = ["endergebnis/anschreiben", "endergebnis/ki_auswertung"]


def bericht():
    """Was die Anbindung braucht – in einem Satz je Punkt, für den CRM-Entwickler."""
    return [
        ("Schnittstelle vorhanden",
         "Jede Seite liefert über Inertia bereits JSON. Es muss keine neue API entstehen."),
        ("Was fehlt: Token",
         "Die JSON-Antworten hängen an der Sitzung. Ein Dienst, der nachts läuft, braucht "
         "einen Token-Zugang – am einfachsten dieselben Antworten, zusätzlich mit Token "
         "abgesichert."),
        ("Was fehlt: Datei in ein Fach legen",
         "Ein Weg, eine fertige PDF in ein Ablagefach zu legen "
         "(endergebnis/lebenslauf, massnahme/vermittlung), höchstens 15 MB."),
        ("Was wir liefern könnten",
         "Lebenslauf, alter Lebenslauf, Bewerbungsfoto und – wenn gewünscht – die "
         "Vermittlungsaktivitäten aus der Taskforce."),
        ("Bonus für die Leitung",
         "Das Feld account.remaining je Kunde ist der Auftragsbestand. Über alle Kunden "
         "summiert und mit dem UE-Satz multipliziert ergibt das den gesicherten Umsatz."),
    ]
