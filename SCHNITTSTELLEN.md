# Die Schnittstellen der Portale

Stand 30.09.2026. Ausgelesen aus `taskforce.py` (Registrierung in `QUELLEN`, Zeile 1644).
Zehn Quellen: acht für Stellen, zwei für Wohnungen.

**Drei Arten von Zugang**, und der Unterschied ist wichtig für die Bewertung:

- **Amtlich/offiziell** — dokumentierte Schnittstelle, stabil: Bundesagentur, Arbeitnow, Adzuna, Jooble
- **Gelesene Seite** — wir rufen die normale Suchseite ab und lesen die Treffer heraus:
  Indeed, StepStone, meinestadt, Kleinanzeigen. Das kann ein Portal jederzeit ändern oder sperren.
- **Postfach** — ImmoScout24 schickt Suchauftrags-Mails, wir lesen das Postfach. Kein Login beim Portal.

---

## Stellen

### 1 · Bundesagentur für Arbeit — `jobs.ba`

| | |
|---|---|
| Endpunkt | `https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs` |
| Anmeldung | Kopfzeile `X-API-Key: jobboerse-jobsuche` |
| Schlüssel geheim? | **Nein.** Öffentlicher Schlüssel der BA-App, steht so im Code (`taskforce.py:930`) |
| Antwort | JSON, Trefferliste unter `ergebnisliste`, Kennung `referenznummer` bzw. `hashId` |
| Einzelabruf | `…/pc/v4/jobdetails/<hashId>`, gleiche Kopfzeile |
| Stellenlink | `https://www.arbeitsagentur.de/jobsuche/jobdetail/<referenznummer>` |

Die Schnittstelle filtert selbst — was sie kann, schieben wir hinein statt nachträglich auszusortieren
(`ba_parameter`, Zeile 934): Ort, Umkreis, Veröffentlichungszeitraum, Arbeitszeit, Zeitarbeit,
Befristung, Behinderung.

> **Der Ortsname braucht hier den Umlaut** („Köln", nicht „Koeln") — anders als bei StepStone
> und meinestadt. Das gilt unverändert.

**Das Zeitfenster steht seit dem 30.09.2026 auf 30 Tagen** (`BA_TAGE`, `taskforce.py`). Vorher
stand es auf 14, und das kostete vier Fünftel der Treffer. Gemessen am 30.09.2026, dieselbe Suche
(Lagerhelfer, Köln, 25 km), derselbe Moment, nur dieser eine Parameter verändert:

| `veroeffentlichtseit` | Treffer |
|---|---|
| 7 | 6 |
| **14** (der alte Vorgabewert) | **8** |
| 21 | 38 |
| **30** (jetzt) | **38** |
| ohne den Parameter | 38 |

Ab 21 Tagen ist der ganze Bestand da — die BA hält nicht mehr vor, der Parameter schneidet dann
nichts mehr weg. 30 ist deshalb dasselbe Ergebnis wie „ohne Zeitfenster", aber als Zahl, die
dasteht und sich ändern lässt, statt als weggelassener Parameter, dessen Vorgabe die BA jederzeit
anders setzen kann. Über den Adapter gemessen, zweimal abwechselnd: **8 → 38 Treffer.**

Was **nicht** angefasst wurde: `zeitarbeit=false` bleibt. Das ist Hauspolitik, keine Technik —
Improfy vermittelt keine Zeitarbeit. Eine Prüfung hält das jetzt fest, damit es beim nächsten
Aufmachen des Zeitfensters nicht nebenbei mitkippt.

> **Zwei Beobachtungen am Rand, nicht repariert:** Die BA schließt unter Last einzelne
> Verbindungen (`WinError 10054`); der Adapter hat keinen zweiten Versuch, und in der Direktsuche
> steht dann „nicht erreichbar" für die ganze Quelle. Zweitens nimmt sie `veroeffentlichtseit`
> auch über 100 an, ohne zu widersprechen.

### 2 · Indeed — `jobs.indeed`

| | |
|---|---|
| Endpunkt | `https://de.indeed.com/jobs?…` (normale Suchseite) |
| Anmeldung | keine |
| Zustand | Bot-Schutz. Erster Abruf meist 403, danach antwortet die Seite oft doch |

**Diese Quelle wird bewusst nicht wiederbelebt.** Indeed sperrt uns mit HTTP 403 aus. Das zu
umgehen hieße, die Bot-Erkennung auszutricksen — andere Kopfzeilen, Wartezeiten, wechselnde
Kennungen. Das macht dieses Haus nicht: Wer eine Sperre umgeht, arbeitet gegen den ausdrücklichen
Willen des Anbieters, und ein Portal, das uns nicht will, ist keine verlässliche Grundlage für die
Vermittlung. **Der ehrliche Ersatz ist Adzuna und Jooble** (Nummer 7 und 8) — zwei dokumentierte
Schnittstellen, die dasselbe Feld abdecken und uns ausdrücklich einladen.

Der Adapter bleibt, wie er ist: Er meldet den 403 im Klartext und hat sein eigenes Zeitbudget
(`BUDGET_BILDSCHIRM = 5 s`), sonst hielte seine Warteleiter (0 / 5 / 12 Sekunden) die ganze Suche
auf — das kostete vorher 17,2 Sekunden je Suche für null Treffer.

> **Gemessen am 30.09.2026, sechs Abrufe hintereinander** (Lagerhelfer, Köln): der erste 403, die
> fünf danach je **13 Treffer**. Die Aussage „sperrt uns dauerhaft" stimmt so also nicht mehr —
> offenbar trägt der Cookie aus dem ersten Versuch. Das ändert nichts an der Entscheidung, aber es
> erklärt, warum dieselbe Suche mal 143 und mal 176 Treffer zeigt. Geändert wurde an Indeed
> nichts.

### 3 · StepStone — `jobs.stepstone`

| | |
|---|---|
| Endpunkt | `https://www.stepstone.de/jobs/<beruf>/in-<ort>?radius=…&page=…` |
| Anmeldung | keine |
| Besonderheit | Ort und Beruf stehen in der **Adressbahn**, nicht als Parameter |

> **Der Umlaut ist hier tödlich, und zwar lautlos.** Ein prozentkodierter Umlaut führt zu einer
> gültigen Seite mit **leerer** Trefferliste — kein Fehler, keine Meldung. „Köln" ergab 0 Treffer,
> „koeln" 50. Deshalb läuft alles, was in die Adressbahn geht, durch `_slug()` mit Umschrift
> (ä→ae, ö→oe, ü→ue, ß→ss). Das gilt auch für den Suchbegriff: „Bürokauffrau" → `buerokauffrau`.

### 4 · jobs.meinestadt.de — `jobs.meinestadt`

| | |
|---|---|
| Endpunkt | `https://jobs.meinestadt.de/<stadt>/suche?…` |
| Anmeldung | keine |
| Besonderheit | Stadt in der Adressbahn, gleiche Umschrift wie StepStone |

Gemessen der langsamste Lieferant: rund 3,9 von 4,0 Sekunden einer Jobsuche gehen auf sein Konto.

### 5 · Kleinanzeigen Jobs — `jobs.kleinanzeigen`

| | |
|---|---|
| Endpunkt | `https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=…&locationStr=…` |
| Anmeldung | keine |
| Antwort | HTML; die Einzelanzeige trägt `application/ld+json`, daraus lesen wir Titel und Preis |
| Seiten | zwei je Suche |

Größte Einzelquelle: rund 53 von 140 Treffern einer Kölner Suche.
`veroeffentlicht` liefert dieses Portal grundsätzlich **nicht** — das Feld bleibt leer.

### 6 · Arbeitnow — `jobs.arbeitnow`

| | |
|---|---|
| Endpunkt | `https://www.arbeitnow.com/api/job-board-api?…` |
| Anmeldung | keine |
| Antwort | JSON |
| Zustand | Weltweites Board. Am 30.09.2026: **326 Stellen**, davon **4** im Kölner Raum |

**Das Portal beachtet unsere Suchparameter nicht.** Gemessen am 30.09.2026: `search=Lagerhelfer&
location=Köln`, `search=Reinigungskraft&location=Köln` und `search=Lagerhelfer&location=München`
liefern **dieselben 326 Stellen**. Der lokale Filter in `jobs_arbeitnow` ist also nicht
Feinschliff, sondern der einzige, den es gibt.

Er verglich `"köln"` **mit Umlaut** gegen das `location`-Feld. Die vier Kölner Stellen stehen dort
als `['Köln', 'Köln', 'Köln', 'Cologne']` — der englische Name fiel damit immer durch, still und
ohne Meldung. Seit dem 30.09.2026 läuft der Vergleich über `ort_passt`: Umlaut, Umschrift und
englischer Name finden zusammen (`Köln`/`Koeln`/`Cologne`, ebenso München/Munich,
Nürnberg/Nuremberg, Hannover/Hanover, Braunschweig/Brunswick, Frankfurt am Main/Frankfurt).

Gemessen an derselben Rohantwort, vorher → nachher:

| Suche | vorher | nachher |
|---|---|---|
| Lagerhelfer, Köln | 0 | **0** |
| Reinigungskraft, Köln | 0 | **0** |
| Manager, Köln | 0 | **1** (die Stelle steht als „Cologne") |
| Chief, „Koeln" geschrieben | 0 | **2** |

**Für unsere Berufe bleibt es bei null, und das ist ein ehrliches Ergebnis:** Auf dem ganzen Board
stehen heute vier Kölner Stellen, und keine davon ist Lager, Reinigung oder Pflege. Repariert ist
der stille Ausfall, nicht die Trefferzahl. Die Quelle bleibt drin, weil sie nichts kostet und
sichtbar ihre Null meldet.

> Ein **leeres** Ortsfeld zählt weiterhin als „passt nicht" (18 der 326 Stellen haben keins). Auf
> einem weltweiten Board ist eine Stelle ohne Ortsangabe keine Kölner Stelle.

### 7 · Adzuna — `jobs.adzuna`

| | |
|---|---|
| Endpunkt | `https://api.adzuna.com/v1/api/jobs/de/search/1?…` |
| Anmeldung | `ADZUNA_APP_ID` und `ADZUNA_APP_KEY` in der `.env` |
| Zustand | **Nicht verbunden** — Zugangsdaten fehlen. Der Adapter ist anschlussfertig |
| Zu holen bei | `https://developer.adzuna.com/signup` — Formular, Mail bestätigen, Schlüssel steht sofort im eigenen Bereich |
| Was es kostet | nichts, keine Kreditkarte. Laut Nutzungsbedingungen 25 Abrufe/Minute, 250/Tag, 1.000/Woche, 2.500/Monat |

Sammelt viele Portale auf einmal; der größte Zugewinn unter den unverbundenen Quellen und
zusammen mit Jooble der ehrliche Ersatz für Indeed.

**Gegengelesen am 30.09.2026** gegen developer.adzuna.com/docs/search: Endpunkt, Parameter
(`app_id`, `app_key`, `what`, `where`, `distance`, `results_per_page`, `max_days_old`) und
Antwortform (`results[]` mit `id`, `title`, `company.display_name`, `location.display_name`,
`created`, `redirect_url`, `contract_time`) stimmen mit dem Adapter überein. Der Ort geht in den
**Abfrageteil**, behält also den Umlaut — richtig so.

**Ein falscher Schlüssel ergibt keine stille Null mehr.** Gemessen mit einem erfundenen
Schlüssel: HTTP **401** mit `{"display":"Authorisation failed","exception":"AUTH_FAIL"}`. Das
fingen `direktsuche` und `lauf` als „nicht erreichbar" ab — die falsche Auskunft, sie schickt den
Leser ins Netz statt in die `.env`. Jetzt steht da:

```
Adzuna (viele Portale): Adzuna weist den Zugang ab (HTTP 401). ADZUNA_APP_ID und
ADZUNA_APP_KEY in der .env prüfen – neu holen bei developer.adzuna.com. Möglich ist
auch, dass das kostenlose Kontingent aufgebraucht ist.
```

Der Satz zum Kontingent steht da, weil beide Anbieter eins haben (250 Abrufe/Tag bzw. 500
insgesamt) und ein erschöpftes Kontingent genauso aussieht wie ein falscher Schlüssel.

Ein 500 des Anbieters wird ausdrücklich **nicht** als Schlüsselproblem ausgegeben — sonst sucht
der Nächste am falschen Ende.

### 8 · Jooble — `jobs.jooble`

| | |
|---|---|
| Endpunkt | `https://de.jooble.org/api/<schlüssel>` (POST, JSON) |
| Anmeldung | `JOOBLE_KEY` in der `.env` |
| Zustand | **Nicht verbunden** — Schlüssel fehlt |
| Zu holen bei | `https://de.jooble.org/api/about` — Formular (Name, Position, Mail, Webseite, Telefon), Schlüssel kommt **per Mail nach einer Prüfung**, nicht sofort |
| Was es kostet | nichts. Die Dokumentation nennt für den kostenlosen Schlüssel aber ein Kontingent von **500 Abrufen insgesamt** — nicht je Monat, sondern über die ganze Laufzeit |

Meta-Suche über viele Börsen.

**Gegengelesen am 30.09.2026** gegen die REST-Dokumentation im Jooble-Hilfecenter: Landes-Subdomain
(`de.jooble.org`), POST mit `Content-Type: application/json`, Rumpf `keywords`, `location`,
`radius`, `page`, Antwort `totalCount` und `jobs[]` mit `id`, `title`, `company`, `location`,
`link`, `salary`, `source`, `updated` — alles so, wie der Adapter es liest.

> **Dabei ist ein Fehler aufgefallen, der die Quelle auch mit gültigem Schlüssel nie hätte
> anlaufen lassen.** Unsere Kopfzeilen fragen mit `Accept-Encoding: gzip`, und Jooble antwortet
> auch so — `_post_json` packte aber nicht aus (anders als `_get`). `json.loads` hätte die
> gepackten Bytes bekommen und wäre umgefallen. Belegt am gzip-gepackten Rumpf der 403-Antwort,
> der einzigen, die sich ohne Schlüssel messen lässt. Repariert am 30.09.2026.

**Ein falscher Schlüssel** ergibt HTTP **403** (gemessen) und meldet sich jetzt im Klartext:
„Jooble weist den Zugang ab (HTTP 403). JOOBLE_KEY in der .env prüfen – neu holen bei
de.jooble.org/api/about."

> **Vor dem Dauerbetrieb prüfen:** 500 Abrufe insgesamt sind bei einem Abruf je Suchbegriff und
> Lauf schnell aufgebraucht. Vor dem Scharfschalten bei Jooble nachfragen, was ein größeres
> Kontingent kostet.

---

## Wohnungen

### 9 · ImmoScout24 über Suchauftrags-Mails — `wohnung.mail`

| | |
|---|---|
| Zugang | IMAP, **kein Portal-Login** |
| Server | `imap.ionos.de:993` (SSL) |
| Benutzer | volle Mailadresse, z. B. `coach@improfy.de` |
| Konfiguration | `TF_IMAP_HOST`, `TF_IMAP_USER`, `TF_IMAP_PASSWORT`, `TF_IMAP_ORDNER` |
| Zustand | Verbunden, sobald die drei Werte gesetzt sind (`imap_konfiguriert`, Zeile 1432) |

So funktioniert es: Bei ImmoScout24 werden Suchaufträge angelegt, die ihre Treffer per Mail schicken.
Wir lesen das Postfach und werten die **Nur-Text-Fassung** der Mail aus — die ist deutlich
verlässlicher als das HTML. Damit braucht es weder einen ImmoScout-Zugang noch eine kostenpflichtige
Schnittstelle.

> **Achtung, das ist der Zugang, der als Einziger ein echtes Passwort braucht.** Es gehört in die
> `.env` und nirgendwo sonst hin — nicht ins Repo, nicht in einen Chat.

### 10 · Kleinanzeigen Mietwohnungen — `wohnung.kleinanzeigen`

| | |
|---|---|
| Endpunkt | `https://www.kleinanzeigen.de/s-suchanfrage.html?…` (dieselbe Suche wie bei Jobs) |
| Anmeldung | keine |
| Seiten | drei je Suche |
| Preisfeld | aus dem Anzeigentext, intern auf 19 Zeichen begrenzt |

---

## Was für alle Quellen gilt

- **Abruf** über `_get()` (Zeile 167) mit 25 Sekunden Zeitgrenze je Aufruf.
- **Parallel:** Alle Quellen einer Suchart werden gleichzeitig gefragt (`ThreadPoolExecutor`).
  Die Suche dauert so lange wie die langsamste Quelle, nicht wie ihre Summe — gemessen 4,0 s
  statt 40 s. *Noch nicht parallel:* „beides" fragt Stellen und Wohnungen nacheinander
  (6,5 s = 4,0 + 2,5).
- **Obergrenze:** 200 Treffer je Direktsuche, bei „beides" 100 je Art.
- **Dubletten** werden über Quellen hinweg erkannt (Titel und Anbieter), der erste Fund gilt.
- **Sichtbarkeit:** Jede Suche zeigt an, welche Quelle wie viele Treffer geliefert hat — auch die
  Nullen. Das war die Lehre aus dem StepStone-Ausfall: Ein Portal, das lautlos nichts liefert,
  fällt sonst wochenlang niemandem auf.

## Welche Zugangsdaten überhaupt nötig sind

| Quelle | Braucht | Steht in |
|---|---|---|
| Bundesagentur | nichts (öffentlicher Schlüssel) | fest im Code |
| Indeed, StepStone, meinestadt, Kleinanzeigen (beide) | nichts | — |
| Arbeitnow | nichts | — |
| Adzuna | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | `.env` |
| Jooble | `JOOBLE_KEY` | `.env` |
| ImmoScout24 | `TF_IMAP_HOST`, `TF_IMAP_USER`, `TF_IMAP_PASSWORT` | `.env` |

Acht von zehn Quellen brauchen **gar keine Zugangsdaten**. Vorlage für die übrigen: `env.beispiel`
— dort steht seit dem 30.09.2026 über beiden Blöcken, **wo** man den Zugang holt und **was** er
kostet.

---

## Die Gegenmessung vom 30.09.2026

Eine echte Kölner Arbeitssuche über `direktsuche` („Lagerhelfer", Köln, 25 km), zweimal im selben
Prozess, dieselbe Minute — nur das Zeitfenster der BA verändert:

| Quelle | vorher (14 Tage) | nachher (30 Tage) |
|---|---|---|
| Kleinanzeigen Jobs | 54 | 53 |
| StepStone | 50 | 50 |
| jobs.meinestadt.de | 40 | 40 |
| **Bundesagentur** | **8** | **38** |
| Indeed | 0 (403) | 14 |
| Arbeitnow | 0 | 0 |
| **nach Reglern und Dubletten** | **143** | **176** |

Die Bundesagentur ist die Quelle, die das Jobcenter anerkennt — sie liefert jetzt 38 statt 8.
Indeeds 14 sind Zufall und gehören nicht zu dieser Änderung (siehe Nummer 2); ohne sie liegt der
Zugewinn bei rund 30 Treffern je Suche, alle von der BA.

**Geändert wurde an drei Stellen im Code:**

| Was | Wo | Wirkung |
|---|---|---|
| Zeitfenster der BA 14 → 30 Tage | `taskforce.BA_TAGE` | BA 8 → 38 Treffer |
| Ortsvergleich über Schreibweisen | `taskforce.ort_passt` | Arbeitnow: „Cologne" fällt nicht mehr still weg |
| Klartext statt stiller Null bei abgewiesenem Schlüssel | `taskforce._zugang_pruefen` | Adzuna/Jooble sagen, welches Feld fehlt |
| Gepackte POST-Antwort auspacken | `taskforce._post_json` | Jooble wäre sonst auch mit Schlüssel nicht angelaufen |

Geprüft in `taskforce_test.py`, Abschnitt 17 — jede dieser Regeln mit einer eigenen Prüfung, und
für jede ist belegt, dass sie rot wird, wenn man die Regel im Code zurückdreht.
