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

> **Zwei gemessene Hinweise.** Der Ortsname braucht hier den **Umlaut** („Köln", nicht „Koeln") —
> anders als bei StepStone und meinestadt. Und der Vorgabewert `veroeffentlichtseit=14` kostet
> rund 83 % der Treffer: gemessen 6 statt 35 bei sonst gleicher Suche. Die BA hält ohnehin nicht
> mehr als 30 Tage vor.

### 2 · Indeed — `jobs.indeed`

| | |
|---|---|
| Endpunkt | `https://de.indeed.com/jobs?…` (normale Suchseite) |
| Anmeldung | keine |
| Zustand | **Sperrt uns dauerhaft mit HTTP 403.** In jeder Messung 0 Treffer |

Wegen der Sperre hat die Quelle am Bildschirm ein eigenes Zeitbudget (`BUDGET_BILDSCHIRM = 5 s`),
sonst hielte ihre Warteleiter (0 / 5 / 12 Sekunden) die ganze Suche auf. Das kostete vorher
17,2 Sekunden je Suche für null Treffer.

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
| Zustand | Liefert für Köln **0 Treffer** — weltweites Board mit 250 Stellen, davon 3 im Kölner Raum |

> Die Null ist ehrlich, aber der lokale Ortsfilter vergleicht `"köln"` **mit Umlaut** gegen das
> `location`-Feld. Schreibt das Portal „Cologne", fällt jeder Treffer still weg. Ungeprüft.

### 7 · Adzuna — `jobs.adzuna`

| | |
|---|---|
| Endpunkt | `https://api.adzuna.com/v1/api/jobs/de/search/1?…` |
| Anmeldung | `ADZUNA_APP_ID` und `ADZUNA_APP_KEY` in der `.env` |
| Zustand | **Nicht verbunden** — Zugangsdaten fehlen |
| Zu holen bei | developer.adzuna.com, kostenlos für geringe Mengen |

Sammelt viele Portale auf einmal; wäre der größte Zugewinn unter den unverbundenen Quellen.

### 8 · Jooble — `jobs.jooble`

| | |
|---|---|
| Endpunkt | `https://de.jooble.org/api/<schlüssel>` (POST, JSON) |
| Anmeldung | `JOOBLE_KEY` in der `.env` |
| Zustand | **Nicht verbunden** — Schlüssel fehlt |
| Zu holen bei | jooble.org/api/about, kostenlos auf Anfrage |

Meta-Suche über viele Börsen.

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

Acht von zehn Quellen brauchen **gar keine Zugangsdaten**. Vorlage für die übrigen: `env.beispiel`.
