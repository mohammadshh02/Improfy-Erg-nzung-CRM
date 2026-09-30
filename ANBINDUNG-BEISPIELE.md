# Anbindung — fertige Beispiele zum Kopieren

Stand 30.09.2026. Alle Feldnamen sind aus `api.py` ausgelesen, nicht erfunden.
Ersetzen Sie `https://os.improfy.de` durch die Adresse, unter der das Modul läuft,
und `<TOKEN>` durch den Wert von `OS_API_TOKEN`.

**Anmeldung:** Kopfzeile `Authorization: Bearer <TOKEN>`. Alternativ `X-API-Token: <TOKEN>`
oder `?token=<TOKEN>`. Nur nötig, wenn das Modul im Netz lauscht (`IMPROFY_OS_PASSWORT` gesetzt);
lokal ohne Passwort ist kein Token erforderlich.

---

## 1 · Lebt es? (der erste Aufruf, ohne Risiko)

```bash
curl -s https://os.improfy.de/api/gesundheit \
  -H "Authorization: Bearer <TOKEN>"
```

Antwortet mit dem Zustand des Moduls und welche Portale angebunden sind. Wenn das geht,
stimmen Adresse und Token.

---

## 2 · Einen Kunden übergeben — der wichtigste Aufruf

Das CRM führt die Kunden. Dieser Aufruf legt einen Kunden an oder aktualisiert ihn.
Erkannt wird über die **Kundennummer**, erst wenn die fehlt, über den Namen.
**Überschrieben wird nur, was mitgeschickt wird** — ein Feld, das Sie weglassen, bleibt stehen.

### curl

```bash
curl -s -X POST https://os.improfy.de/api/kunde \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
        "customer_number": "32102//0071158",
        "name":            "Vorname Nachname",
        "phone":           "0221 1234567",
        "email":           "person@example.de",
        "language":        "Arabisch",
        "address":         "Köln",
        "case_worker":     "Jobcenter Köln",
        "measure":         "4Steps",
        "status":          "aktiv"
      }'
```

### PHP / Laravel

```php
use Illuminate\Support\Facades\Http;

$antwort = Http::withToken(config('services.improfy_os.token'))
    ->acceptJson()
    ->post(config('services.improfy_os.basis') . '/api/kunde', [
        'customer_number' => $kunde->kundennummer,
        'name'            => $kunde->name,
        'phone'           => $kunde->telefon,
        'email'           => $kunde->email,
        'language'        => $kunde->sprache,
        'address'         => $kunde->standort,
        'case_worker'     => $kunde->jobcenter,
        'measure'         => $kunde->massnahme,
        'status'          => $kunde->status,     // siehe Statuswörter unten
    ]);

if ($antwort->status() === 409) {
    // Kundennummer doppelt oder Name schon vergeben — die Antwort nennt die Sätze.
    Log::warning('Improfy-OS: nicht eindeutig', $antwort->json());
}
```

### Die Felder

| Sie schicken | Wird bei uns | Pflicht |
|---|---|---|
| `customer_number` | `kundennummer` | eines von beiden |
| `name` | `name` | eines von beiden |
| `phone` | `telefon` | nein |
| `email` | `email` | nein |
| `language` | `sprache` | nein |
| `address` | `stadt` | nein |
| `case_worker` | `ort_jc` | nein |
| `measure` | `massnahme` | nein |
| `status` | `status_code` | nein |

**Die acht erlaubten Statuswörter** (alles andere wird mit 400 abgewiesen, damit ein
unbekanntes Wort nicht stillschweigend den Statuscode löscht):

```
angelegt · bewilligt · aktiv · zwischenbericht · abschlussphase · abgeschlossen · pausiert · abgebrochen
```

### Die Antworten

| Code | Bedeutung |
|---|---|
| `200` | `{"angelegt": false, "geaendert": ["telefon"], "kunde": 42}` — aktualisiert |
| `200` | `{"angelegt": true, "kunde": 121}` — neu angelegt |
| `400` | Feld hat den falschen Typ oder ein unbekanntes Statuswort. Die Antwort sagt welches. |
| `409` | Kundennummer nicht eindeutig oder Name schon vergeben. **Es wurde nichts geändert** — die Antwort nennt die betroffenen Sätze, damit Sie entscheiden können. |
| `401` | Token fehlt oder falsch |

> **Bekannte Lücke:** Der zuständige **Coach** wird noch nicht übernommen — das Feld kennt die
> Route nicht. Sagen Sie uns, ob Sie je Kunde eine Nutzer-Kennung oder nur einen Namen liefern,
> dann ziehen wir es nach.

---

## 3 · Ein Suchprofil anlegen

Damit die Job- oder Wohnungssuche für einen Kunden läuft, braucht es ein Suchprofil.

```bash
curl -s -X POST https://os.improfy.de/api/taskforce/profil \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
        "kunde":        42,
        "art":          "job",
        "titel":        "Lagerhelfer Köln",
        "suchbegriffe": "Lagerhelfer, Kommissionierer, Produktionshelfer",
        "ort":          "Köln",
        "umkreis_km":   25,
        "arbeitszeit":  "vz",
        "aktiv":        1
      }'
```

Für Wohnungen:

```bash
curl -s -X POST https://os.improfy.de/api/taskforce/profil \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
        "kunde":       42,
        "art":         "wohnung",
        "titel":       "2 Zimmer Köln",
        "ort":         "Köln",
        "umkreis_km":  15,
        "max_miete":   750,
        "min_zimmer":  2,
        "min_flaeche": 45
      }'
```

**Felder:** `kunde`, `art` (`job` oder `wohnung`), `titel`, `suchbegriffe`, `ort`, `umkreis_km`,
`arbeitszeit`, `zeitarbeit`, `max_miete`, `min_zimmer`, `min_flaeche`, `suchauftrag`, `notiz`,
`aktiv`, `quellen`.

`quellen` leer lassen heißt „alle angebundenen Portale". Wer einschränken will, schickt eine Liste:
`["jobs.ba", "jobs.stepstone"]`. Eine Quelle, die es nicht gibt, wird mit 400 abgewiesen.

> **Bekannte Lücke:** `kriterien` als verschachteltes Objekt (`{"kriterien": {"wbs": true}}`)
> wird zurzeit angenommen und **nicht gespeichert**. Bis das behoben ist, bitte die einzelnen
> `k_*`-Felder benutzen.

---

## 4 · Was die Suche gefunden hat, abholen

```bash
# Alle neuen Angebote eines Kunden
curl -s "https://os.improfy.de/api/taskforce/angebote?kunde=42&status=neu&limit=100" \
  -H "Authorization: Bearer <TOKEN>"

# Die Bilanz für einen Kunden — wie viele angeschrieben, wie viele Rückmeldungen
curl -s "https://os.improfy.de/api/taskforce/kunde/42/bilanz" \
  -H "Authorization: Bearer <TOKEN>"

# Wiedervorlage: wo ist seit N Tagen nichts passiert
curl -s "https://os.improfy.de/api/taskforce/wiedervorlage?tage=7" \
  -H "Authorization: Bearer <TOKEN>"
```

`GET /api/taskforce/kunde/<id>/bilanz` ist die Zahl, die das Jobcenter sehen will:
wie viele Stellen und Wohnungen für diesen Menschen bearbeitet wurden.

---

## 5 · Einen Status setzen (nachdem jemand beworben hat)

```bash
# Einzeln
curl -s -X POST https://os.improfy.de/api/taskforce/angebot/1234/status \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"status": "beworben", "bearbeiter": "M. Payinda", "notiz": "Mail raus"}'

# Im Stapel
curl -s -X POST https://os.improfy.de/api/taskforce/angebote/status \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"ids": [1234, 1235, 1236], "status": "beworben"}'
```

---

## 6 · Eine Suche sofort ausführen (ohne Profil)

```bash
curl -s "https://os.improfy.de/api/taskforce/suche?art=job&was=Lagerhelfer&wo=K%C3%B6ln&km=25" \
  -H "Authorization: Bearer <TOKEN>"
```

Antwortet mit `{"stand": …, "anzahl": …, "daten": [ … ]}` und braucht rund vier Sekunden.

**Die Treffer übernehmen** — hier ist der häufigste Anfängerfehler:

```bash
# FALSCH — das Array direkt zurückschicken gibt 400
-d '[{"quelle":"jobs.ba", … }]'

# RICHTIG — die Liste muss "treffer" heißen
curl -s -X POST https://os.improfy.de/api/taskforce/profil/7/uebernehmen \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"treffer": [{"quelle":"jobs.ba","extern_id":"12345","titel":"Lagerhelfer", "url":"https://..."}]}'
```

Antwort: `{"uebergeben": 12, "neu": 9, "schon_da": 3, "abgewiesen": 0}`.
Höchstens **1000 Treffer je Aufruf**, darüber `413` mit Angabe der Grenze.

---

## 7 · Der Ablauf im Ganzen

```
1.  CRM legt Kunden an          →  POST /api/kunde
2.  CRM legt Suchprofil an      →  POST /api/taskforce/profil
3.  Das Modul sucht nachts von selbst  (kein Aufruf nötig)
4.  CRM holt die Treffer        →  GET  /api/taskforce/angebote?kunde=…&status=neu
5.  Mensch bewirbt sich, CRM meldet  →  POST /api/taskforce/angebot/<id>/status
6.  CRM holt die Bilanz fürs Jobcenter →  GET /api/taskforce/kunde/<id>/bilanz
```

Schritt 3 braucht keinen Aufruf — die Agenten laufen nach Zeitplan. Wer sofort suchen will:
`POST /api/taskforce/profil/<id>/lauf`.

---

## 8 · Drei Dinge, die beim ersten Versuch Zeit kosten

1. **Der Körper muss ein JSON-Objekt sein**, keine Liste. `{"treffer": [...]}`, nicht `[...]`.
2. **Zahlen als Text sind in Ordnung** (`"umkreis_km": "25"`), aber eine absurd große Zahl als
   Text lässt die Route zurzeit abstürzen. Das ist in Arbeit.
3. **Bei `409` wurde nichts geändert.** Die Antwort nennt die betroffenen Sätze; wiederholen
   hilft nicht, die Dublette muss aufgelöst werden.

Bei Fragen: `GET /api/` listet alle Wege mit Kurzbeschreibung auf.
