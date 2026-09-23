# -*- coding: utf-8 -*-
"""Die Arbeitskopie der Datenbank für Selbsttests und Serienläufe – an einer Stelle.

    import pruefkopie
    os.environ["IMPROFY_OS_DB"] = pruefkopie.anlegen("improfy_os_cv_test.db")
    os.environ["OS_SICHERUNG_ORDNER"] = pruefkopie.papierkorb("sicherungen")
    os.environ["OS_AUSGABE_ORDNER"] = pruefkopie.papierkorb("ausgabe")

**Warum nicht `shutil.copy`.** Eine SQLite-Datei, die gerade geschrieben wird, kopiert
sich als Datei in einem Zwischenzustand: halbe Seiten, ein Journal, das nicht dazu passt.
Im OS läuft im Alltag ein Vorschau-Server auf dem Echtbestand, und `journal_mode` ist
`delete` – dann liegt genau dieser Fall vor. `sqlite3.Connection.backup()` geht dagegen
über die Datenbankschicht und liefert immer einen in sich stimmigen Stand; `betrieb.py`
hält es für die nächtliche Sicherung schon so.

**Warum ein eigenes Modul und nicht eine Funktion in `betrieb`.** Die Kopie muss
angelegt sein, **bevor** `IMPROFY_OS_DB` gesetzt und `app` importiert wird. `betrieb`
zieht `datenbank` mit herein, und das bindet seinen Pfad beim Import – der Selbsttest
liefe dann gegen den Echtbestand. Dieses Modul kennt nur die Standardbibliothek.

Die Quelle wird ausdrücklich **nur lesend** geöffnet (`mode=ro`): `improfy_os.db` darf
von keinem Testlauf angefasst werden, auch nicht versehentlich. Fehlt sie ganz, bricht
`anlegen` mit `KeinBestand` ab und sagt, was zu tun ist – ein blankes `sqlite3.connect`
legte an dieser Stelle eine leere neue Datei an, und der Lauf lief auf Nichts.

**Jeder Lauf bekommt seine eigene Zieldatei.** Vorher lag die Kopie unter ihrem blanken
Namen im Temp-Ordner, für jeden Arbeitsbaum derselbe. Zwei Läufe gleichzeitig – der
Selbsttest hier und einer aus einem anderen Arbeitsbaum – setzten sich damit auf
dieselbe Datei: Der zweite blieb im `backup()` hängen, weil der erste sie hielt. Jetzt
steht in jedem Pfad der Arbeitsbaum und die Prozessnummer.

**Und es wird nicht mehr endlos gewartet.** Hielt jemand die Zieldatei, lief
`Connection.backup()` bei SQLITE_BUSY ohne Ende weiter – 45 Sekunden ohne eine Zeile
Ausgabe bis ins Zeitlimit des Aufrufers, und niemand wusste, woran es lag. Ein
Zeitlimit bricht das ab und sagt, was zu tun ist.

**Und es bleibt nichts liegen.** Jede Kopie ist der vollständige personenbezogene
Bestand, 2,4 MB je Stück. Weil jeder Lauf seinen eigenen Ordner bekommt, sammelten sich
die Ordner beendeter Läufe im Temp-Verzeichnis an – gemessen am 22.09.2026 vierzehn
Stück mit 46 MB Kundendaten. Zwei Wege räumen das jetzt ab: Der eigene Ordner geht beim
Programmende (`atexit`), und beim Anlegen werden die Ordner weggeräumt, deren
Prozessnummer nicht mehr lebt. Ordner **laufender** Läufe bleiben stehen – sonst
zieht ein Lauf dem anderen die Datenbank unter den Füßen weg.

**Nicht nur die Datenbank muss umgelenkt werden.** Zwei weitere Ordner leitet das OS
aus seinem eigenen Verzeichnis ab, und beide treffen echte Daten: `sicherungen/` –
ein Testlauf, der sichert, legt einen Schnappschuss der Testdatenbank dorthin, und
`aufraeumen()` wirft bei sieben Ständen je eine echte Nachtsicherung heraus – und
`ausgabe/lebenslaeufe/`, wo jeder Lauf zwei Dateien mit echtem Kundennamen ablegte
(belegt am 21.09.2026: Riegel entfernt, Test gelaufen, beide Dateien wieder da). Weil
der Dateiname Kundennummer und Datum trägt, überschreibt ein Testlauf ein am selben
Tag echt gebautes Dokument desselben Menschen. `OS_SICHERUNG_ORDNER` und
`OS_AUSGABE_ORDNER` zeigen deshalb über `papierkorb()` in den Ordner dieses Laufs –
dieselbe Prozessnummer, dasselbe Aufräumen, eine Stelle statt sieben.
"""
import atexit
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import time

HIER = os.path.dirname(os.path.abspath(__file__))
QUELLE = os.path.join(HIER, "improfy_os.db")

# Wie lange der Kopiervorgang höchstens dauern darf. Über eine lokale Platte ist das
# eine Sache von Sekunden; wer länger braucht, wartet nicht auf die Platte, sondern
# auf eine gehaltene Sperre.
# Gemessen wird die Frist nur zwischen zwei Häppchen: Läuft ein Häppchen in SQLITE_BUSY,
# schläft SQLite erst seine Runde zu Ende (`sqlite3_busy_timeout`, hier die Vorgabe von
# 5 Sekunden). Die Frist überzieht deshalb um bis zu eine solche Runde – bei 30 Sekunden
# sind es rund 32. Genauer geht es ohne eigenen Faden nicht, und genauer muss es nicht
# sein: Die Zahl steht in der Meldung, und zwei Sekunden ändern an der Ursache nichts.
ZEITGRENZE = 30.0
# In Häppchen kopieren, damit die Frist überhaupt geprüft werden kann: Der Fortschritt
# wird nach jedem Schritt gemeldet, bei `pages=0` (alles auf einmal) gäbe es nur einen.
# Bei einer Datenbank mit weniger als so vielen Seiten ist beides dasselbe – die
# Kundendatenbank hat rund 600, also mehrere Häppchen. Kleiner gewählt hieße öfter
# nachsehen und länger kopieren, ohne dass jemand etwas davon hätte.
SEITEN_JE_SCHRITT = 256
# Der gemeinsame Ort aller Arbeitskopien. Steht hier, weil zwei Dinge ihn brauchen:
# das Anlegen und das Aufräumen.
SAMMELORDNER = os.path.join(tempfile.gettempdir(), "improfy-pruefkopie")


class Zeitueberschreitung(RuntimeError):
    """Die Kopie kam nicht durch – meist hält ein anderer Lauf die Zieldatei."""


class KeinBestand(RuntimeError):
    """Es gibt keine Datenbank, von der eine Arbeitskopie zu ziehen wäre.

    Ein eigener Fehler, weil die nackte SQLite-Meldung an dieser Stelle in die Irre
    führt: `sqlite3.connect` auf eine fehlende Datei legt **ohne** `mode=ro` eine leere
    neue an – der Lauf läuft dann auf Nichts, statt zu scheitern –, und **mit** `mode=ro`
    sagt sie nur „unable to open database file", ohne zu verraten, welche Datei gemeint
    ist und warum sie fehlt. Der Fall tritt auf einem frisch geklonten Rechner sofort
    ein: `*.db` ist gitignoriert, die Kundendatenbank kommt also nicht mit dem Repo."""


def leseadresse(quelle=None):
    """Die URI, mit der die Quelle geöffnet wird – **nur lesend**.

    Steht hier und nicht mitten in `anlegen`, damit der Selbsttest genau die Adresse
    prüfen kann, mit der wirklich geöffnet wird. Als URI muss der Pfad mit
    Schrägstrichen stehen, auch unter Windows."""
    quelle = quelle or QUELLE
    return "file:%s?mode=ro" % quelle.replace("\\", "/").replace("?", "%3f")


def ordner():
    """Der Ablageort der Arbeitskopien dieses Laufs: Temp / Arbeitsbaum / Prozessnummer.

    Der Name trägt beides, weil beides schon Schaden gemacht hat: Zwei Arbeitsbäume
    greifen sonst nach derselben Datei, und zwei Läufe aus demselben Baum ebenso. Die
    kurze Prüfsumme kommt dazu, weil zwei Arbeitsbäume gleich heißen können, wenn sie
    in verschiedenen Ordnern liegen."""
    ziel = _eigener_pfad()
    os.makedirs(ziel, exist_ok=True)
    return ziel


def papierkorb(name):
    """Ein Unterordner dieses Laufs für alles, was ein Testlauf schreibt – Pfad zurück.

    Gedacht für `OS_SICHERUNG_ORDNER` und `OS_AUSGABE_ORDNER` (siehe Modulkopf). Beide
    Variablen werden beim Import von `betrieb` bzw. `lebenslauf_bauen` und `cv_pdf`
    gelesen, sie müssen also **vor** dem Import von `app` gesetzt sein – genau wie
    `IMPROFY_OS_DB`.

    Der Ordner liegt unter `ordner()`, trägt damit Arbeitsbaum und Prozessnummer im
    Pfad und geht mit ihm am Programmende weg. Ein eigenes `atexit` je Aufrufer
    braucht es deshalb nicht; das stand vorher siebenmal einzeln im Repo."""
    ziel = os.path.join(ordner(), name)
    os.makedirs(ziel, exist_ok=True)
    return ziel


def _eigener_pfad():
    """Derselbe Pfad wie `ordner()`, nur ohne ihn anzulegen – fürs Aufräumen."""
    kurz = re.sub(r"[^A-Za-z0-9]+", "-", os.path.basename(HIER))[:24] or "repo"
    stelle = hashlib.sha1(HIER.encode("utf-8")).hexdigest()[:6]
    return os.path.join(SAMMELORDNER, "%s-%s-%d" % (kurz, stelle, os.getpid()))


def _lebt(nummer):
    """Läuft der Prozess mit dieser Nummer noch?

    `os.kill(pid, 0)` ist hier **nicht** zu gebrauchen: Unter Windows ist `os.kill`
    kein Nachfragen, sondern ein `TerminateProcess` – die Nachfrage würde den Prozess
    erschlagen, und genau die laufenden Läufe wären die Opfer. Gefragt wird deshalb
    über `OpenProcess`/`WaitForSingleObject`.

    **Kommt kein Griff zurück, heißt das zweierlei**, und nur eines davon erlaubt das
    Wegräumen: Entweder gibt es den Prozess nicht mehr (`ERROR_INVALID_PARAMETER`, 87)
    – oder wir dürfen nicht hinsehen, weil er einem anderen Konto gehört oder erhöht
    läuft (`ERROR_ACCESS_DENIED`, 5). Hier stand einmal die Begründung, im zweiten Fall
    bleibe der Ordner ohnehin stehen, „weil das Löschen an der offenen Datei scheitert".
    Das stimmt nicht: `anlegen` schließt beide Verbindungen, und `datenbank.offen()`
    öffnet nur kurz – zwischen zwei Abfragen hält niemand die Datei fest, und der
    Ordner eines **lebenden** Laufs wäre weg. Im Zweifel gilt er deshalb als lebend."""
    if nummer == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        griff = kernel.OpenProcess(0x00100000, False, nummer)   # SYNCHRONIZE
        if not griff:
            return kernel.GetLastError() != 87   # nur „gibt es nicht" heißt beendet
        try:
            # 0 = WAIT_OBJECT_0: der Prozess ist beendet. Alles andere heißt, er läuft.
            return kernel.WaitForSingleObject(griff, 0) != 0
        finally:
            kernel.CloseHandle(griff)
    try:
        os.kill(nummer, 0)          # auf POSIX nur eine Nachfrage, kein Signal
    except OSError:
        return False
    return True


def aufraeumen():
    """Die Ordner beendeter Läufe wegräumen. Gibt die entfernten Ordner zurück.

    Jede Arbeitskopie ist der ganze Kundenbestand; was ein abgestürzter oder
    abgebrochener Lauf liegen lässt, soll nicht bis zum nächsten Neustart des Rechners
    im Temp-Ordner stehen bleiben. Erkennungsmerkmal ist die Prozessnummer am Ende des
    Ordnernamens – lebt sie nicht mehr, kann der Ordner weg. Was sich nicht entfernen
    lässt (unter Windows hält eine offene Datei den Ordner fest), bleibt kommentarlos
    liegen: Aufräumen darf keinen Lauf zum Scheitern bringen.

    **Erkannt wird nur, was dieses Modul selbst anlegt.** Das Muster hieß einmal
    `-(\\d+)$` und traf damit jeden Namen, der auf Bindestrich und Ziffern endet: Ein
    danebenliegender Ordner `messung-2026` mit Messwerten wurde weggeräumt. Jetzt muss
    davor die sechsstellige Prüfsumme des Arbeitsbaums stehen, also genau die Form aus
    `_eigener_pfad` – `name-a1b2c3-31972`."""
    weg = []
    if not os.path.isdir(SAMMELORDNER):
        return weg
    for name in os.listdir(SAMMELORDNER):
        pfad = os.path.join(SAMMELORDNER, name)
        treffer = re.search(r"^.+-[0-9a-f]{6}-(\d+)$", name)
        if not os.path.isdir(pfad) or not treffer or _lebt(int(treffer.group(1))):
            continue
        shutil.rmtree(pfad, ignore_errors=True)
        if not os.path.exists(pfad):
            weg.append(pfad)
    return weg


def _eigenen_ordner_raeumen():
    """Am Programmende die eigene Arbeitskopie entfernen.

    `ignore_errors`, weil beim Aufräumen von `atexit` nicht sicher ist, dass jede
    Datenbankverbindung schon geschlossen ist. Bleibt dann etwas stehen, holt es der
    nächste Lauf über `aufraeumen` – die Prozessnummer lebt dann nicht mehr."""
    eigener = _eigener_pfad()
    if os.path.isdir(eigener):
        shutil.rmtree(eigener, ignore_errors=True)


atexit.register(_eigenen_ordner_raeumen)

_geraeumt = False       # ob dieser Lauf schon nach fremden Resten gesehen hat


def anlegen(name, quelle=None, zeitgrenze=ZEITGRENZE):
    """Legt die Kopie an und gibt ihren Pfad zurück.

    `name` ist ein Dateiname (dann liegt die Kopie im Ordner dieses Laufs, siehe
    `ordner`) oder ein ganzer Pfad. Ein alter Stand wird vorher weggeräumt, damit nichts
    von einem früheren Lauf durchscheint."""
    quelle = quelle or QUELLE
    # **Fehlt der Bestand, wird hier abgebrochen – mit Auskunft.** Weiter unten stünde
    # sonst „unable to open database file" ohne Dateinamen, und jede Prüfung des Laufs
    # fiele danach mit derselben Meldung um. Auf einem frisch geklonten Rechner ist das
    # der Normalfall, nicht die Ausnahme.
    if not os.path.isfile(quelle):
        raise KeinBestand(
            "Die Datenbank %s gibt es nicht – ohne sie gibt es nichts zu kopieren.\n"
            "  `*.db` ist gitignoriert: Auf einem frisch geklonten Rechner muss die\n"
            "  Datenbank erst dazugelegt werden (Sicherung aus `sicherungen/` oder\n"
            "  Kopie vom Arbeitsrechner). Ein leerer Bestand ist kein Ersatz: Die\n"
            "  Selbsttests messen an echten Zeilen." % quelle)
    # Beim ersten Anlegen dieses Laufs die Hinterlassenschaften beendeter Läufe wegräumen.
    # Einmal reicht; zwischendurch stirbt kein Lauf, und ein Verzeichnislauf je Kopie
    # wäre nur Arbeit ohne Ertrag.
    global _geraeumt
    if not _geraeumt:
        _geraeumt = True
        aufraeumen()
    ziel = name if os.path.isabs(name) else os.path.join(ordner(), name)

    # Das Wegräumen kann unter Windows an einer geöffneten Datei scheitern. Das ist
    # **kein** belangloser Fall: Genau dann hängt gleich auch das Kopieren. Der Grund
    # wird deshalb festgehalten und in die Fehlermeldung gelegt, statt verschluckt.
    belegt = None
    if os.path.exists(ziel):
        try:
            os.remove(ziel)
        except OSError as e:
            belegt = e

    frist = time.monotonic() + max(0.0, zeitgrenze)

    def wacht(_stand, rest, gesamt):
        if time.monotonic() >= frist:
            raise Zeitueberschreitung(
                "Die Arbeitskopie kam in %.0f Sekunden nicht durch: %s\n"
                "  %s von %s Seiten fehlten noch.\n"
                "  Meist hält ein anderer Lauf oder ein offener Vorschau-Server die "
                "Datei. Laufende Python-Prozesse prüfen und den Ordner %s leeren."
                % (zeitgrenze, ziel, rest, gesamt, os.path.dirname(ziel))
                + ("\n  Schon das Wegräumen der alten Datei ging nicht: %s" % belegt
                   if belegt else ""))

    von = sqlite3.connect(leseadresse(quelle), uri=True)
    try:
        nach = sqlite3.connect(ziel)
        try:
            von.backup(nach, pages=SEITEN_JE_SCHRITT, progress=wacht)
        finally:
            nach.close()
    finally:
        von.close()
    return ziel
