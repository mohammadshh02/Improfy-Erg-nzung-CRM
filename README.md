# Improfy-Ergänzung zum CRM

Vier Abteilungen, die das Improfy-CRM (`crm.improfy.de`) nicht hat. Alles, was das CRM
selbst kann – Kunden, Termine, Unterrichtseinheiten, Dokumente, Profiling, Qualität,
Abrechnung, Nutzer und Standorte – ist hier **bewusst nicht drin**.

## Die vier Abteilungen

**1 · Taskforce** — Job- und Wohnungssuche je Kunde. Zehn Portale (Bundesagentur über die
offizielle Schnittstelle, Indeed, StepStone, jobs.meinestadt.de, Kleinanzeigen, Arbeitnow,
Adzuna, Jooble; Wohnungen über ImmoScout24-Suchauftragsmails und Kleinanzeigen). Dubletten
über Quellen hinweg werden erkannt, jedes Angebot bekommt eine Relevanz 0–10 und einen
Abgleich gegen das Kundenprofil. 13 Job-Regler, 22 Wohnungs-Regler. Läuft nachts von selbst.

> **Warum das ins CRM gehört:** Modul 4 „Jobmatch" beschreibt genau diese Arbeit —
> Stellenrecherche, Kandidatenprofil in den Portalen, aktive Ansprache — und das CRM rechnet
> sie ab, hat aber kein Werkzeug dafür. Das Pflichtdokument *Vermittlungsaktivitäten*
> (Modul 4 & Turbofy) entsteht hier nebenbei, statt am Ende zusammengeschrieben zu werden.
> Die Wohnungssuche gehört zu All Care § 16k.

**2 · Lebenslauf** — Alte Unterlagen ablegen (PDF, Word, OpenOffice, frühere Improfy-Excel),
das Formular füllt sich selbst. Heraus kommen die Improfy-Excel-Vorlage und ein designtes PDF
in sechs Vorlagen.

> **Warum das ins CRM gehört:** Dort ist der Lebenslauf ein Pflichtdokument mit einem
> Hochladen-Knopf. Erstellt wird er nicht — heute macht das ein Designer von Hand.

**3 · Vertriebstrichter** — Von der WhatsApp-Anfrage bis zum Gutschein: Anfrage → Antrag
vorbereitet → Antrag raus (👍 im Raum „Anträge AVGS") → Bewilligung → Kunde. Mit hängenden
Anträgen, Quote je Person und Monat und der Durchlaufzeit je Jobcenter. Kommo (WhatsApp)
ist angeschlossen.

> **Warum das ins CRM gehört:** Das CRM beginnt bei „Kunde anlegen". Die Strecke davor
> steuert ein Standortleiter täglich.

**4 · Mitarbeiter-Spur** — Wer war wann angemeldet, von welchem Gerät, was hat er geändert.
Tagesspur, angefasste Kunden, jeder einzelne Schritt.

> **Warum das ins CRM gehört:** Das CRM führt Nutzer und Kundenzahlen, aber nicht, was
> jemand tatsächlich getan hat.

Dazu als Unterbau: Konten mit drei Rollen, Änderungsprotokoll, nächtliche Sicherung,
Zeitsteuerung, Aufgabenliste je Mitarbeiter und die Übersicht der Außenanbindungen.

## Starten

```
pip install -r requirements.txt
python -X utf8 app.py
```

Läuft auf `http://localhost:8100`. Ohne gesetztes Passwort lauscht es nur auf diesem Rechner.

## Einstellen (`.env`, siehe `env.beispiel`)

| Schlüssel | wofür |
|---|---|
| `IMPROFY_OS_PASSWORT` | gemeinsames Passwort, bis das erste Konto angelegt ist |
| `KOMMO_TOKEN` | WhatsApp-Anfragen aus Kommo lesen |
| `TF_IMAP_HOST/USER/PASSWORT` | ImmoScout24-Suchauftragsmails |
| `TF_CHAT_WEBHOOK` | Alarm bei neuen Treffern in den Google-Chat-Raum |
| `TF_LAUF_UHRZEIT` | wann die Agenten nachts laufen (Vorgabe 05:30, `aus` schaltet ab) |
| `OS_SICHERUNG_UHRZEIT/STAENDE/ORDNER` | nächtliche Sicherung |
| `CRM_URL` | Adresse des CRM für die Verweise (Vorgabe crm.improfy.de) |
| `ADZUNA_APP_ID/KEY`, `JOOBLE_KEY` | zusätzliche Jobquellen, freiwillig |

**Zugangsdaten gehören in die `.env`, nie in den Code.** Die Datei ist ignoriert.

## Konten

Solange kein Konto angelegt ist, gilt das gemeinsame Passwort. **Das erste Konto schaltet auf
persönliche Anmeldung um** — deshalb zuerst das eigene Konto als *Leitung* anlegen, sonst
sperrt man sich aus. Drei Rollen: `leitung` (darf alles), `mitarbeiter` (alles Fachliche),
`lesen` (nur schauen).

## Selbsttests

```
python -X utf8 os_test.py            # jede Seite, Gestaltung, Konten, Betrieb
python -X utf8 taskforce_test.py     # Taskforce von der Quelle bis zum Treffer
python -X utf8 lebenslauf_test.py    # Unterlagen einlesen, Excel, designtes PDF
```

Alle drei laufen gegen eine Kopie der Datenbank und fassen den Echtbestand nicht an.
**Vor jedem Push alle drei laufen lassen.**

## Die Naht zum CRM

Das CRM ist die Wahrheit. Dieses Paket liest von dort und schreibt genau drei Dinge zurück:

1. das fertige Lebenslauf-PDF in seinen Pflicht-Slot
2. das Vermittlungsaktivitäten-PDF in seinen Pflicht-Slot
3. je Kunde einen Kurzstatus („12 Bewerbungen · 3 Antworten · 1 Zusage")

**Dafür fehlen noch zwei Dinge vom CRM:** eine lesende Schnittstelle (Kunde, Module,
Gutschein, UE-Stand) mit Token und ein Weg, eine fertige PDF in einen Ablage-Slot zu legen.
Bis dahin führt das Paket einen eigenen, gespiegelten Kundenstand (`kunde`-Tabelle).

## Was hier bewusst fehlt

Kunden­verwaltung, Termine, Unterrichtseinheiten, Monatsblatt, Qualitätsmanagement,
Dokumentenprüfung, Gutscheinliste, Rechnungen, HubSpot, Drive-Ordner, Leadlisten des alten
Systems. Das kann das CRM, und zwar besser — es doppelt zu führen schafft nur zwei Wahrheiten.

Das vollständige Backoffice-System, aus dem dieses Paket stammt, liegt weiterhin unter
`Improfy-OS-Complete` und wird genutzt, solange HubSpot läuft.

## Was das Paket nie tut

- nach außen schreiben: alle Portale, Kommo und das CRM werden nur gelesen. Einzige Ausnahme
  ist der Chat-Alarm bei neuen Treffern.
- ImmoScout24 abgreifen: die Wohnungen kommen ausschließlich über die offiziellen
  Suchauftrags-Mails, nicht über das Portal.
- Menschen überwachen: die Mitarbeiter-Spur hält fachliche Änderungen fest, keine
  Tastenanschläge, Mauswege oder Bildschirmfotos. Die Tagesspanne ist keine Arbeitszeit.

Das Repository enthält personenbezogene Kundendaten, sobald die Datenbank angelegt ist —
**privat halten.** Die Datenbank selbst ist ignoriert und wird nie mitgegeben.
