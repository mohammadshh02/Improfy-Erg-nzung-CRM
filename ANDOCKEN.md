# Andocken ans CRM — Übergabe an die CRM-Entwicklung

Stand 23.09.2026. Dieses Dokument richtet sich an die Person, die `crm.improfy.de`
entwickelt. Es sagt, was dieses Modul mitbringt, was es vom CRM braucht und was
zu tun ist, damit es dort läuft.

Ansprechpartner auf unserer Seite: **Masoud Payinda, Standortleiter Köln.**

---

## 1 · Was das hier ist

Eine Ergänzung zum CRM, kein zweites CRM. Zwei Werkzeuge, die das CRM heute nicht hat:

- **Taskforce** — Job- und Wohnungssuche je Kunde über zehn Portale, mit Dublettenerkennung,
  Relevanzbewertung, Abgleich gegen das Kundenprofil und Wiedervorlage. Läuft nachts von selbst.
  Deckt Modul 4 „Jobmatch" ab, das das CRM abrechnet, aber nicht abbildet.
- **Lebenslauf** — Erstellung als Excel und PDF, mit Fotos und Vorlagenprüfung.

**Alles Kundenbezogene gehört dem CRM.** Stammdaten, Akte, Termine, Unterrichtseinheiten,
Gutscheine, Abrechnung, Nutzer, Standorte: das führt das CRM, und dieses Modul baut es
ausdrücklich nicht nach. Doppelte Kundendaten sind im QM ein Befund, keine Funktion.

---

## 2 · Was zu tun ist, damit es andockt

Das Modul liest die Kundendaten aus dem CRM, sobald zwei Werte gesetzt sind. Der Adapter
dafür ist fertig: `quellen/crm.py`.

```
CRM_BASIS=https://crm.improfy.de
CRM_TOKEN=<Lesezugang>
CRM_STANDORT=Köln
CRM_PFAD_KUNDEN=/api/kunden      # falls im CRM anders benannt
CRM_PFAD_NUTZER=/api/nutzer      # falls im CRM anders benannt
```

Fehlt der Zugang, ändert sich nichts — das Modul läuft mit seinen bisherigen Quellen weiter.
Die Pfade sind einstellbar und die Feldnamen werden tolerant gelesen, weil wir nicht wissen,
wie die Schnittstelle des CRM tatsächlich heißt.

**Die einzige offene Frage an Sie:** Unter welcher Adresse und mit welcher Anmeldung gibt das
CRM seine Kunden und Nutzer heraus, und wie sehen die Felder aus? `/api/kunden` ist eine
Annahme von uns, keine Zusage von Ihnen. Sobald das feststeht, ist das Andocken eine
Konfigurationsänderung, kein Umbau.

**Richtung: nur lesen.** Der Adapter schreibt nie ins CRM. Das ist die ganze
Sicherheitszusage — eine lesende Anbindung kann Ihre Akte nicht beschädigen.

### Was das Modul als Kunde braucht

Kennung, Kundennummer, Name, Standort, Status, zuständiger Coach, Sprache. Optional Telefon
und E-Mail. Mehr nicht. Die Zuordnung zwischen Ihrer Kennung und unserer läuft über
`crm_kunde` (`crm_id` ↔ `kunde_id`, Brücke ist die Kundennummer).

### Was mit unseren Daten passiert

Die 120 Kundensätze in diesem Repo sind **Beispieldaten aus der Entwicklung** und werden beim
Andocken ersetzt. Sie müssen nichts davon übernehmen oder migrieren.

---

## 3 · Die Schnittstellen, die dieses Modul anbietet

26 Wege unter `/api`, JSON, für schreibende Aufrufe mit Token gesichert
(`OS_API_TOKEN`; mit `OS_API_HERKUNFT` lässt sich die erlaubte Herkunft einschränken).
Jeder schreibende Aufruf landet im Protokoll.

| Bereich | Wege |
|---|---|
| Betrieb | `GET /api/gesundheit` · `GET /api/quellen` · `GET /api/karte` |
| Kunden | `GET /api/kunden` · `GET /api/kunden/suche` · `GET /api/kunde/<id>` · `POST /api/kunde` |
| Suchprofile | `GET /api/taskforce/profile` · `GET,POST,DELETE /api/taskforce/profil[/<id>]` · `POST …/lauf` |
| Angebote | `GET /api/taskforce/angebote` · `GET /api/taskforce/angebot/<id>` · `POST …/status` · `POST …/angebote/status` · `POST …/abgleich` |
| Auswertung | `GET /api/taskforce/kpi` · `GET /api/taskforce/wiedervorlage` · `GET /api/taskforce/laeufe` · `GET /api/taskforce/kunde/<id>/bilanz` |
| Suche | `GET /api/taskforce/suche` · `POST /api/taskforce/profil/<id>/uebernehmen` |

`GET /api/gesundheit` ist der schnellste Weg zu prüfen, ob das Modul lebt und welche
Portale angebunden sind.

### Zwei Hinweise für den ersten Aufruf

- **Der Körper muss ein JSON-Objekt sein.** Wer die Trefferliste aus `GET /api/taskforce/suche`
  nimmt und als Array zurückschickt, bekommt 400 mit Begründung — nicht weil das unvernünftig
  wäre, sondern weil `treffer` benannt sein muss: `{"treffer": [ … ]}`.
- **Höchstens 1000 Treffer je Aufruf**, darüber 413 mit Angabe der Grenze. Eine normale Suche
  liefert rund 150.

### Zwei bekannte Stellen, an denen gerade gearbeitet wird

Ehrlichkeit vor Politur — beide sind gemessen und in Arbeit (Zweig `api-haertung`):

1. **Eine Zahl als Text umgeht die Größenprüfung.** `{"umkreis_km": 25}` ist geprüft,
   `{"umkreis_km": "25"}` nicht. Normale Werte sind unbetroffen; eine absurd große Zahl
   als Text lässt die Route abstürzen.
2. **`kriterien` als verschachteltes Objekt wird angenommen und verworfen.**
   `{"kriterien": {"wbs": true}}` antwortet mit Erfolg und speichert nichts. Bis das behoben
   ist, bitte die einzelnen `k_*`-Felder benutzen.

---

## 4 · Betrieb

- Python 3.12, Flask, SQLite. Abhängigkeiten in `requirements.txt`.
- Start: `python -X utf8 app.py --port <port>`. Ohne `IMPROFY_OS_PASSWORT` lauscht es nur
  auf 127.0.0.1 und fragt niemanden nach Anmeldung; mit Passwort im Netz.
- Rollen: `leitung` und `mitarbeiter` (`konten.py`).
- Die nächtlichen Agentenläufe hängen an `TF_LAUF_UHRZEIT`, die Sicherung an
  `OS_SICHERUNG_UHRZEIT` und `OS_SICHERUNG_STAENDE`.
- Alle Zugangsdaten gehören in `.env` (Vorlage: `env.beispiel`). Die `.env` ist
  gitignoriert und enthält nie echte Werte im Repo.

### Selbsttests

Vier Stück, vor jeder Änderung laufen zu lassen:

```
python -X utf8 os_test.py          123 Prüfungen
python -X utf8 taskforce_test.py   302 Prüfungen
python -X utf8 lebenslauf_test.py  139 Prüfungen
python -X utf8 bedienprobe.py       26 Prüfungen
```

Sie legen sich über `pruefkopie.py` eine eigene Arbeitskopie der Datenbank an und lenken
Sicherungs- und Ausgabeordner in einen Papierkorb um — ein Testlauf fasst echte Daten nicht an.
`taskforce_test.py` fragt echte Portale ab und kann deshalb rot werden, wenn ein Portal
gerade drosselt; das ist dann kein Fehler im Code (siehe `docs/wissen/` im Wissens-Repo).

**Hausregel:** Eine Prüfung gilt erst als Prüfung, wenn belegt ist, dass sie rot werden kann.
Der übliche Weg ist, den Produktivcode in einer Kopie absichtlich zu verbiegen und zu sehen,
ob der Test anschlägt.

---

## 5 · Was beim Andocken aus der Oberfläche verschwindet

Sobald das CRM die Kundendaten führt, sind diese Bereiche hier überflüssig und werden
ausgebaut: **Vertrieb, Kunden, Coaches**. Der Bereich **Einrichtung**
(CRM-Anbindung, Außenanbindung, Betrieb, Protokoll, Konten) bleibt im Code, wird aber nur
noch der Rolle `leitung` gezeigt.

Was bleibt: **Übersicht, Aufgaben, Taskforce, Lebenslauf.**

---

## 6 · Was hier bewusst nicht drin ist

Kundenakte, Termine, Unterrichtseinheiten, Gutscheine, Profiling und Erhebung, Dokumente,
Rechnungen, Nutzerverwaltung, Standorte. Das kann das CRM, und es soll es behalten.
Vor jeder neuen Funktion hier gilt die Frage: *Steht das schon im CRM?* Wenn ja, wird es
nicht gebaut, sondern von dort gelesen.
