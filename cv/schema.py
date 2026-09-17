"""
Gemeinsames Daten-Schema (der "Vertrag") zwischen Extraktion und Ausfüllen.
Beide Skripte (extract_cv.py und fill_cv.py) benutzen genau diese Felder.
"""

# JSON-Schema für die KI-Extraktion (Anthropic structured outputs).
# additionalProperties:false + required sind für strikte Validierung nötig.
CV_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "vorname": {"type": "string", "description": "Nur der/die Vorname(n)"},
        "nachname": {"type": "string", "description": "Nur der/die Nachname(n)/Familienname"},
        "geschlecht": {"type": "string", "description": "m, w oder d (aus Namen/Foto/Anrede ableiten)"},
        "angestrebter_job": {
            "type": "string",
            "description": "Aussagekräftige Ziel-Jobbezeichnung, aus dem CV abgeleitet",
        },
        "geburtsdatum": {"type": "string", "description": "Format TT.MM.JJJJ, sonst leer"},
        "mobil": {"type": "string", "description": "Telefon-/Handynummer, sonst leer"},
        "email": {"type": "string", "description": "E-Mail-Adresse, sonst leer"},
        "adresse": {"type": "string", "description": "Format: Strasse Nr, PLZ Ort"},
        "fuehrerschein": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "vorhanden": {"type": "boolean"},
                "klasse": {"type": "string", "description": "z.B. B; leer wenn unbekannt"},
                "eu": {"type": "boolean", "description": "true = EU/DE-Führerschein"},
            },
            "required": ["vorhanden", "klasse", "eu"],
        },
        "berufserfahrung": {
            "type": "array",
            "description": "Neueste zuerst. Max. 7 Stationen.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "zeitraum": {"type": "string", "description": "mm.yyyy - mm.yyyy oder 'seit mm.yyyy'"},
                    "firma": {"type": "string", "description": "Vollständiger Firmenname + Ort"},
                    "jobtitel": {"type": "string"},
                    "taetigkeiten": {
                        "type": "array",
                        "description": "2-4 Stichpunkte",
                        "items": {"type": "string"},
                    },
                },
                "required": ["zeitraum", "firma", "jobtitel", "taetigkeiten"],
            },
        },
        "bildung": {
            "type": "array",
            "description": "Höchster/neuester Abschluss zuerst. Max. 4 Einträge.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "zeitraum": {"type": "string"},
                    "abschluss": {"type": "string", "description": "Art des Abschlusses / der Ausbildung"},
                    "institution": {"type": "string", "description": "Schule/Uni/Betrieb + Ort"},
                    "note": {"type": "string", "description": "leer wenn unbekannt"},
                },
                "required": ["zeitraum", "abschluss", "institution", "note"],
            },
        },
        "zusatzqualifikationen": {
            "type": "array",
            "description": "Zertifikate, Lizenzen (z.B. §34a). Leer wenn keine.",
            "items": {"type": "string"},
        },
        "sprachen": {
            "type": "array",
            "description": "Deutsch IMMER zuerst. Sprache in GROSSBUCHSTABEN.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sprache": {"type": "string"},
                    "niveau": {"type": "string", "description": "Muttersprache/fließend/gute Kenntnisse/Grundkenntnisse"},
                },
                "required": ["sprache", "niveau"],
            },
        },
        "edv_kenntnisse": {
            "type": "array",
            "description": "Max. 3-5 Programme. Leer wenn keine im CV.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "programm": {"type": "string"},
                    "sterne": {"type": "integer", "description": "1-5"},
                },
                "required": ["programm", "sterne"],
            },
        },
        "soft_skills": {
            "type": "array",
            "description": "Max. 8 passende Eigenschaften, aus dem Werdegang abgeleitet.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "eigenschaft": {"type": "string"},
                    "sterne": {"type": "integer", "description": "3-5 (nur ab 3 aufführen)"},
                },
                "required": ["eigenschaft", "sterne"],
            },
        },
        "ueber_mich": {
            "type": "string",
            "description": "Fertiger 'Über mich'-Text in Ich-Form, freundlich, 4-6 Absätze, mit Gruß und Namen am Ende.",
        },
        "hobbys": {"type": "string", "description": "Komma-getrennt; leer wenn nicht im CV"},
    },
    "required": [
        "vorname", "nachname", "geschlecht", "angestrebter_job", "geburtsdatum", "mobil",
        "email", "adresse", "fuehrerschein", "berufserfahrung", "bildung",
        "zusatzqualifikationen", "sprachen", "edv_kenntnisse", "soft_skills",
        "ueber_mich", "hobbys",
    ],
}
