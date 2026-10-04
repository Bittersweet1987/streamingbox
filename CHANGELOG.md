# Änderungen

## 0.9.42 (Beta)
- Behoben: **System-Updates scheiterten nach einem unterbrochenen Paketlauf** mit "E: dpkg was interrupted, you must manually run 'dpkg --configure -a' to correct the problem" (Meldung eines Nutzers; typisch nach Stromausfall, Neustart oder abgebrochenem Update mitten im Paketlauf).
  Der Update-Helfer erkennt jetzt die Reste eines unterbrochenen Laufs (Dateien in `/var/lib/dpkg/updates` oder halb eingerichtete Pakete laut `dpkg --audit`), schließt ihn vor dem Update mit `dpkg --configure -a` und `apt-get -f install` ab und macht dann mit dem Update weiter.
  Läuft gerade ein anderer Paketvorgang (zum Beispiel eine automatische Aktualisierung), fasst er nichts an und meldet das. Gelingt die Reparatur nicht, steht in der Oberfläche der Befehl für die Konsole statt der letzten apt-Zeilen; auch für "Could not get lock" gibt es jetzt eine verständliche Meldung.
  Das Software-Update dieses Projekts (Karte "Software-Update") war davon nie betroffen.
- Tests: Erkennung (Reste, halb eingerichtete Pakete, belegte Sperre), Reparaturablauf, Meldungen, `do_run` mit Reparatur und Abbruch.

## 0.9.41 (Beta)
- Geändert: **Auf einer frischen BELABOX gibt es keinen Setup-Code und kein zweites Passwort mehr.** Die Oberfläche benutzt das Passwort der BELABOX (belaUI). Hat die BELABOX noch keins, steht auf der Anmeldeseite, dass es zuerst in der BELABOX-Oberfläche festgelegt werden muss;
  die Seite wartet darauf und zeigt die Anmeldung von selbst, sobald das Passwort da ist. Die Datei `/var/lib/pipbox/setup-code` wird auf einer BELABOX nicht mehr angelegt (eine alte wird beim Start gelöscht). Ein eigenes Passwort, das eine frühere Version gesetzt hat, bleibt gültig.
  Nur ohne belaUI (Entwicklung, Demo) gilt weiter das eigene Passwort mit Setup-Code.
- Tests: Anmeldearten (frische BELABOX wartet ohne Code, Passwort der BELABOX, eigenes Passwort ohne belaUI, früheres eigenes Passwort bleibt gültig, alte Codedatei wird gelöscht).

## 0.9.40 (Beta)
- Behoben: **Nach einem Tausch ohne Unterbrechung startete die automatische Umschaltung den Encoder etwa 3 Sekunden später doch neu** (gefunden im ersten Test mit echten Kameras: Einbruch im Upload nach dem Wechsel der Kamera). Die Sendekette führte ihre Einstellung nach dem Tausch zwar nach,
  die Reihenfolge der genutzten Kameras (die Anordnung) aber nicht; die Automatik hielt die neue Reihenfolge für eine neue Anordnung. Jetzt folgt auch die Anordnung der neuen Reihenfolge, der Tausch bleibt ohne Neustart.
- Tests: Regressionstest, der nach dem Tausch die Schleife der Automatik laufen lässt und eine neue Anordnung ausschließt, und der danach einen echten Ausfall prüft.
- Neu: Das Einspielen räumt einmalig die Testquellen `tst-a` bis `tst-d` aus der Kameraliste (eine frühere Version hat sie automatisch als Kamera aufgenommen, weil sie bei Tests an die Box gesendet wurden). Der Dienst steht dabei still; andere Kameras bleiben unberührt, auch solche mit ähnlichem Schlüssel.
  Wer Testquellen an die Box sendet, nimmt Schlüssel mit `test-` am Anfang: Die werden gar nicht erst als Kamera aufgenommen.

## 0.9.39 (Beta)
- Neu (experimentell, standardmäßig aus): **Hauptbild tauschen ohne Unterbrechung.** Im Bildaufbau wählt "Hauptbild tauschen ohne Unterbrechung" die Tauschgruppe: *aus* (wie bisher: der Encoder startet neu, etwa 5 Sekunden ohne Bild), *Hauptbild und erstes kleines Bild* oder
  *alle Kameras*. Ist die Gruppe gewählt, bekommt jede ihrer Kameras zwei Zweige (groß für das Hauptbild, klein für das Bild-in-Bild), das ergibt bei vier Kameras im Bild 6 statt 4 Dekodierungen (bei *alle Kameras* 8). Ein neuer Umschalter im Baustein
  (`pbpipsel`) wählt das Hauptbild; der Encoder läuft weiter, der Strom zum Empfänger reißt nicht ab. Die Zeitstempel laufen beim Umschalten lückenlos weiter (der Encoder sieht keinen Sprung), der Ton wird im selben Schritt mit umgeschaltet, und der Encoder bekommt
  beim Schnitt einen vollständigen Bildanfang. Welche Kamera Hauptbild ist und welche Kameras an welcher Stelle kleiner erscheinen, schreibt der Umschalter in jedes Bild; `pbpipmix` liest es dort, Hauptbild und kleine Bilder wechseln also im selben Bild.
  Das ist ein harter Schnitt; eine Überblendung ist geplant.
- Wie der Tausch läuft: `POST /api/pipeline/swap` schreibt eine kleine Datei (`/var/lib/pipbox/main-select`, vier Zahlen), die `pbctl` alle 0,1 s liest; der Baustein meldet den eingestellten Zustand zurück (`/run/pipbox-send/swap-state`), erst dann gilt der Tausch
  als übernommen. Kommt keine Rückmeldung, sendet die neue Hauptkamera nicht, weicht die Anordnung vom Aufbau ab (Notbetrieb) oder ist eine der Kameras nicht in der Tauschgruppe, gilt wie bisher der Neustart. Die Sendekette führt ihre Einstellung für die automatische
  Umschaltung nach, damit ein späterer Kameraausfall das Hauptbild nicht auf den alten Stand zurücksetzt. Die Verzögerung gehört weiter zur Kamera (die Steuerdatei folgt der Reihenfolge beim Aufbau); der Plan steht im Status der Sendekette (`swap`).
- Baustein: Platz 3 für das kleine Bild der Hauptkamera, neues Element `pbpipsel`, `pbctl` liest die Umschaltdatei und stellt die Warteschlangen je Kamera (`cam0` bis `cam3`) um. Wird der Baustein beim Update neu gebaut, ändert sich ohne die neue Einstellung nichts.
- Gemessen mit Testbildern per RTMP auf der Box (kein echter Kameratest): Hauptbild und alle drei kleinen Bilder wechseln richtig, die Zeitstempel von Bild und Ton laufen durch (kein Sprung, keine Lücke), die Rückmeldung kommt in unter 0,7 s. Last des Prozesses: 4 Dekodierungen
  ohne Tausch 43 bis 51 % eines Kerns, 6 Dekodierungen 46 bis 48 %, 8 Dekodierungen 54 bis 55 %, Temperatur 36 bis 38 °C. **Noch nicht geprüft:** mit echten Kameras, mit der Übertragung zum Empfänger und über längere Zeit. Beide Kameras der Gruppe sollten dieselbe Auflösung senden.
- Tests: Pipeline-Text (6 und 8 Dekodierungen, Warteschlangen, Ton), Umschaltzeile, Reihenfolge der Verzögerungen, Übergabe an die laufende Sendekette (mit und ohne Rückmeldung), Nachführen der Einstellung, Anfangszustand.
- Die Auswahl im UI heißt "Hauptbild tauschen ohne Unterbrechung (experimentell)".

## 0.9.38 (Beta)
- Behoben: Im Kasten "System" waren der sichtbare Abstand von der Oberkante bis zur Überschrift (gemessen bis zur Oberkante der Buchstaben: 15 Pixel) und der Abstand vom unteren Balken bis zur Unterkante (11 Pixel) nicht gleich. Der Innenabstand der
  Kästen ist jetzt oben 8 und unten 12 Pixel; sichtbar sind es dadurch oben und unten gleich 13 Pixel.

## 0.9.37 (Beta)
- Geändert: Auch die gewählte Hauptkamera hat in der Auswahl des Hauptbilds den Punkt vor dem Namen (grün = sendet, rot = fehlt), mit einem dunklen Rand, damit er auf der hellen Füllung gut zu sehen ist.

## 0.9.36 (Beta)
- Geändert: Die Knöpfe für die Auswahl des Hauptbilds haben jetzt dieselbe Eckenrundung wie alle anderen Knöpfe und Eingabefelder (8 Pixel statt vollständig rund), dieselbe Rahmenfarbe wie die zweiten Knöpfe und für die gewählte Kamera dieselbe Füllfarbe wie
  die Hauptknöpfe (zum Beispiel "Live gehen"). Karten bleiben bei 14, Kästen in der Karte "Status" bei 10 Pixeln.

## 0.9.35 (Beta)
- Geändert: Die Auswahl des Hauptbilds sieht jetzt aus wie ein Schalter: Unter der Überschrift "Hauptbild" stehen die Kameras, die im Bild sind, als Knöpfe nebeneinander (feste Reihenfolge der Kameraliste). Die Kamera, die gerade das Hauptbild ist, ist
  hervorgehoben; ein Klick auf eine andere macht sie zum Hauptbild. Der Punkt vor dem Namen zeigt, ob die Kamera sendet. Das ersetzt die Zeile "Hauptbild tauschen mit:" aus 0.9.34.

## 0.9.34 (Beta)
- Geändert: **Hauptbild gegen eine frei gewählte Kamera tauschen.** In der Live-Karte steht (bei Bild-in-Bild) "Hauptbild tauschen mit:" und ein Knopf je Kamera, die gerade als kleines Bild im Bild ist (zum Beispiel "⇄ Action 6").
  Ein Klick tauscht das Hauptbild mit genau dieser Kamera; Kamera und Verzögerung bleiben beisammen, Ecke, Größe, Position und die Wahl des Tons bleiben am Platz. Der Knopf "Bilder tauschen" aus 0.9.33 (immer das erste kleine Bild) entfällt.
  `POST /api/pipeline/swap` nimmt dafür `{"with": "<Kamera-Schlüssel>"}`; ohne Angabe gilt das erste kleine Bild.
- Tests: Tausch mit dem zweiten und dritten kleinen Bild, Ablehnung einer Kamera, die nicht im Bild ist, und der Hauptkamera selbst.

## 0.9.33 (Beta)
- Neu: **Hauptbild und kleines Bild tauschen** (Knopf "⇄ Bilder tauschen" in der Live-Karte, sichtbar, wenn ein Bild-in-Bild mit mindestens einem kleinen Bild eingestellt ist). Der Tausch vertauscht das Hauptbild
  mit dem ersten kleinen Bild. Kamera und Verzögerung bleiben beisammen; Ecke, Größe, Position und die Wahl des Tons (Hauptbild oder kleines Bild) bleiben am Platz. Läuft die Sendung, startet der Encoder dafür neu und das
  Bild ist etwa 5 Sekunden unterbrochen (nach Rückfrage); sonst wird nur die Einstellung getauscht. Das ist die erste Stufe des Szenenwechsels; ein Tausch ohne Neustart mit Überblendung ist geplant (siehe README).
  Neu: `POST /api/pipeline/swap`.
- Tests: Tausch von Kamera und Verzögerung, doppelter Tausch, Ablehnung ohne kleines Bild.

## 0.9.32 (Beta)
- Geändert: Im Kasten "Upload" sitzt der Strich über der Zeile "Summe" jetzt direkt an der Zeile und braucht keinen eigenen Platz mehr. Die Summenzeile ist so hoch wie die anderen Zeilen und liegt auf der gleichen Höhe wie
  die Zeilen im Kasten "Kameras".

## 0.9.31 (Beta)
- Geändert: Abstände in der Karte "Status". Zwischen dem Kartenkopf und den Kästen sind es jetzt 10 statt 22 Pixel (eine leere Meldungsfläche belegt keinen Platz mehr), zwischen der Überschrift eines Kastens und seiner
  ersten Zeile 4 statt 8 Pixel. Im Kasten "System" ist der Abstand über der Überschrift und unter dem unteren Balken gleich (je 11 Pixel).

## 0.9.30 (Beta)
- Neu: **Ampel für die Sendewege** im Kasten "Upload" der Karte "Status". Der Punkt vor jedem Netz zeigt: **grün** = der Weg trägt Pakete, **gelb** = verbunden, aber in Reserve (Laufzeit zu hoch oder zu unruhig, der Sender nutzt ihn kaum),
  **rot** = nicht verbunden oder kein Netz (zum Beispiel ein ausgefallenes WLAN), **grau** = keine Sendung. Beim Darüberfahren steht die Laufzeit. Die Daten stammen aus der Datei, die der Sender ohnehin alle paar Sekunden schreibt;
  der Server liest sie nur, das kostet praktisch keine Rechenleistung.
- Tests: Zuordnung "genutzt/Reserve/aus", veraltete und fehlende Datei.

## 0.9.29 (Beta)
- Geändert: Im Kasten "System" der Karte "Status" ist der Abstand zwischen der Beschriftung und der großen Zahl (CPU und Arbeitsspeicher) kleiner. Auch die Zahlen sind etwas kleiner (24 statt 26 Pixel) und die Zeilen enger. Der Kasten ist dadurch rund 13 Prozent niedriger und passt besser zu den Kästen "Kameras" und "Upload". Die Zeilen der Kameras haben jetzt dieselben Abstände und dieselbe Schrift wie die Zeilen unter "Upload".

## 0.9.28 (Beta)
- Neu: Im Kasten "Kameras" der Karte "Status" steht hinter jeder Kamera die aktuelle Eingangsbitrate in Mbit/s (bei einer Kamera ohne Signal ein Strich). So fällt eine schwach sendende Kamera auf, bevor sie ausfällt.

## 0.9.27 (Beta)
- Neu: Die Karte "Status" zeigt jetzt die Kameras mit Ampel, nur Name und farbiger Punkt (grün = im Bild, gelb = sendet, aber noch nicht im Bild, rot = kein Signal, grau = unbekannt). Man sieht
  den Zustand der Kameras so, ohne die Karte "Kameras" zu öffnen. Beim Darüberfahren steht die Erklärung.

## 0.9.26 (Beta)
- Behoben: **Der Encoder starb beim Rauswurf einer Kamera durch das Signal SIGPIPE (Code -13) statt sich geordnet zu beenden.** Der Sendedienst ignoriert SIGPIPE, Python setzt es aber beim Start eines
  Kindprozesses auf "tödlich" zurück. Jedes beobachtete Code -13 folgte 1 s auf den Rauswurf einer Kamera durch den RTMP-Server (vermutlich schreibt librtmp beim Schließen noch einmal in den toten Socket); der Encoder
  meldete sich dabei nicht geordnet vom SRT-Server ab, und der Wiederanlauf verzögerte sich um rund 4 s. Jetzt bleibt SIGPIPE für belacoder ignoriert; er endet über den normalen Fehlerweg (Code 0).
- Behoben: **Die RTMP-Leerlaufgrenze von 15 s (0.9.18) ging bei einem Update des BELABOX-Pakets `belabox-rtmp-server` stillschweigend verloren.** Die Datei gehört dem Paket und ist keine dpkg-Konfigurationsdatei;
  ein Update setzt sie wieder auf 4 s. Neu: `install/pipbox-nginx-guard.sh` übernimmt die Änderung (aus `install.sh` herausgelöst), und ein apt-Haken (`/etc/apt/apt.conf.d/99pipbox-nginx`) ruft es nach jedem
  Paketlauf auf, auch nach Updates über die BELABOX-Oberfläche. nginx wird nur neu geladen, wenn gerade nicht gesendet wird (das Neuladen trennt alle Kameras); sonst gilt der Wert ab dem nächsten Start von nginx.
  `install.sh uninstall` entfernt den Haken und stellt die Datei wieder her.
- Neu im Protokoll: beim Ende des Encoders steht jetzt das Signal (`Code -13, Signal SIGPIPE`), die letzte erkannte Meldung mit dem Elementnamen (`... (rtmpsrc1)`; nie Adressen oder Schlüssel).
- Geändert: In der Karte "Status" steht die Meldung "Alles im grünen Bereich" nicht mehr, denn die Live-Karte zeigt sie schon. Gibt es Warnungen, erscheinen sie weiter in beiden Karten.
- Tests: `tools/test_send_hardening.py` (SIGPIPE-Verhalten mit echten Kindprozessen, Protokollzeilen, Schutzskript mit Attrappen für nginx, Einbindung in `install.sh`).

## 0.9.25 (Beta)
- Geändert: Die Karte "Status" zeigt CPU und Arbeitsspeicher jetzt in einem gemeinsamen Kasten "System". Die Liste der einzelnen CPU-Kerne mit ihren Taktfrequenzen entfällt; geblieben sind der Gesamtwert, der
  Hinweis "höchster Kern", die Temperatur und (falls vorhanden) die Lüfter-Ansteuerung. Das spart Platz. Die Messwerte je Kern liefert der Server weiterhin (`/api/metrics`).

## 0.9.24 (Beta)
- Neu: **Mindestanteil je Sendeweg** bei der Verteilung "alle" (Schalter "Alle Leitungen gleichzeitig nutzen, auch langsamere"). Bisher bekam der beste Weg fast alles (bei einem Test zu Hause 94 %, die
  beiden Mobilfunkwege 4 % und 2 %), und die schwächeren Wege waren nicht eingefahren, wenn der beste ausfiel. Jetzt bekommt jeder **geeignete** Weg (innerhalb des Laufzeitabstands, frische Messung,
  freies Fenster) mindestens 10 Prozent der Pakete. Das gilt in beide Richtungen: schwächerer Mobilfunk neben gutem DSL ebenso wie schwächeres Starlink neben gutem 5G. Ein Weg außerhalb des
  Laufzeitabstands oder mit vollem Fenster bekommt keinen Mindestanteil. Bei "beste" gilt nichts davon. Neu: `srtla/srtla_send-min-share.patch` (AGPL-3.0, zweiter Patch nach der Wegewahl),
  `install.sh` baut den Sender neu, wenn ein Patch neuer ist (schlägt der Bau fehl, bleibt der bisherige). Der Sendedienst setzt `SRTLA_MIN_SHARE_PCT=10` nur bei "alle", die Änderung der Verteilung
  gilt wie bisher nach dem nächsten "Live gehen".
- Tests: Prüfprogramm `srtla/test_min_share.c` (Aufteilung 80/10/10 bei 10 Prozent, 100 Prozent beim besten Weg ohne Mindestanteil, Weg außerhalb des Abstands und Weg mit vollem Fenster ohne Anteil),
  Zuordnung der Umgebungsvariable zur Verteilung "alle".

## 0.9.23 (Beta)
- Geändert: Die Kameraliste zeigt den Zustand nur noch über die Farbe des Punktes (grün, gelb, rot, grau), der Text "im Bild" / "wartet noch ..." ist weg. Beim Darüberfahren (am Handy: langer Tipp)
  erscheint die Erklärung als Hinweis.

## 0.9.22 (Beta)
- Geändert: Der Punkt vor jeder Kamera zeigt jetzt den Zustand auf einen Blick: **grün** = sendet und ist im Bild, **gelb** = sendet, ist aber noch nicht (oder nicht mehr) im Bild,
  **rot** = kein Signal, **grau** = Status unbekannt. Der Text dahinter ("im Bild", "wartet noch 40 s, dann im Bild") bleibt als Erklärung.

## 0.9.21 (Beta)
- Neu: **Die Kameraliste zeigt, ob eine Kamera im Bild ist.** Bisher hieß grün nur "sendet an den RTMP-Server". Fällt eine Kamera aus und kehrt zurück, nimmt
  die Box sie erst nach 60 s stabilem Signal wieder ins Bild auf (damit eine wackelige Kamera nicht dauernd den Encoder neu startet). Jetzt steht hinter jeder
  Kamera "im Bild", "wartet noch 40 s, dann im Bild" oder "nicht im Bild" (orange), solange gesendet wird und die automatische Umschaltung läuft. Die Sendekette meldet
  dafür die verbleibende Wartezeit je Kamera in `status.json` (`failover.wait`).
- Tests: Wartezeit der Umschaltung, Statusdatei, Zuordnung "im Bild/wartet/aus" je Kamera.

## 0.9.20 (Beta)
- Geändert: Der "Sendemodus" (Knopf im Kopf der Seite und in der Live-Karte, blendet Adressen, Namen und Protokolle aus) heißt jetzt "Streammodus". Die Einstellung im Browser bleibt erhalten.

## 0.9.19 (Beta)
- Behoben: **Ein Aussetzer einer kleinen Kamera beendete die ganze Sendung.** Der Stall-Wächter von belacoder las die Position der gesamten Pipeline
  (das Maximum aller Senken, auch der kleinen Kameras mit den rohen Zeitstempeln ihrer Kamera-Sitzung) und beendete den Encoder, wenn die älteste kleine
  Kamera 2 bis 4 s nichts lieferte, obwohl Hauptbild und Ausgang liefen (Meldung "Das Eingangsbild stockte"). Neu: `belacoder/belacoder-stall-output.patch`
  (GPL-3.0, wie der Regler-Patch). Es zählt nur noch der Ausgang des Encoders, und der Wächter schlägt erst nach rund 6 bis 8 s ohne Fortschritt an.
  Die kleine Kamera verschwindet bei einem Aussetzer nur kurz aus dem Bild. `install.sh` baut belacoder neu (nur wenn ein Patch neuer ist; schlägt der Bau fehl,
  bleibt die bisherige Fassung) und legt einmal eine Sicherung `belacoder.vor-stallpatch` an. Die Live-Karte meldet jetzt "Der Ausgang stockte", wenn der Ausgang
  selbst stand.
- Neu: Das Journal (`journalctl -u pipbox-send`) nennt jetzt den Grund, den belacoder vor einem Ende meldet (zum Beispiel "Der Ausgang stockte"), einmal je
  Ereignis und nur als fester Text (nie Adressen oder Stream-ID). Bisher stand dort nur "belacoder beendet (Code 0)".
- Tests: Meldungstexte des Stall-Wächters und Journal-Eintrag je Ereignis.

## 0.9.18 (Beta)
- Behoben: **Kurze Aussetzer einer Kamera warfen sie aus dem Bild und der Encoder startete mehrfach neu.** Der RTMP-Server der BELABOX entfernt eine
  Kamera, die 4 s lang nichts schickt (`drop_idle_publisher 4s`); DJI-Kameras setzen im WLAN gelegentlich 4 bis 10 s aus (gemessen: 15 Rauswürfe in
  rund 90 Minuten bei einer Kamera). Jedes Mal startete der Encoder zweimal vergeblich neu, bevor die Kamera aus dem Bild genommen wurde, rund 10 s ohne Bild.
  Neu: `install.sh` setzt die Grenze auf 15 s. Das ist die einzige Änderung an einer BELABOX-Datei (`99-belabox-rtmp.conf`); Sicherung als
  `99-belabox-rtmp.conf.vor-pipbox`, `install.sh uninstall` stellt sie wieder her. Beim ersten Einspielen lädt nginx neu und die Kameras
  verbinden sich kurz neu. Nach einem Update des BELABOX-Pakets `belabox-rtmp-server` kann die Datei wieder auf 4 s stehen; dann `install.sh` erneut ausführen.
  Fehlt eine Kamera wirklich, schaltet die Box nach dem Ende des Encoders sofort um (vorher erst nach 5 s und zwei Fehlstarts).
- Behoben: Mit nur einer sendenden Kamera blendete der Encoder seine Regelwerte (`b:`, `rtt:`, `bs:`) oben rechts ins Bild ein. Die Einblendung ist entfernt.
- Tests: Sofort-Umschaltung nach Encoder-Ende (fehlende Kamera, alle da, Statistik unlesbar, keine Kamera, Rückkehr nach 60 s).

## 0.9.17 (Beta)
- Doku: Hinweis zum Empfänger in README und Änderungsliste gekürzt. Keine Änderung am Programm.

## 0.9.16 (Beta)
- Geändert: Der Hilfetext zum Schalter "Automatisch live gehen" steht nicht mehr dauerhaft in der Live-Karte, sondern erscheint nach einem Tipp auf das kleine "?"
  neben dem Schalter. Die Zeile bleibt so schlank.

## 0.9.15 (Beta)
- Neu: **Automatisch live gehen nach dem Start der Box** (Schalter in der Live-Karte, standardmäßig aus). Einmal pro Start der Box wartet die Box, bis ein
  SRTLA-Server gewählt ist und mindestens eine Kamera sendet (dieselben Voraussetzungen wie "Live gehen"), und startet dann die Sendung. Wiederholt
  den Versuch alle 20 Sekunden und gibt nach 10 Minuten auf, mit Angabe des Grundes in der Live-Karte. "Live beenden" oder von Hand starten bricht die
  Automatik für diesen Start ab; ein Neustart der Oberfläche (zum Beispiel durch ein Update) startet die Sendung nicht noch einmal. Die Einstellung steht in
  `autostart.json` im Zustandsordner (Rechte 0600), der Zeitpunkt der letzten Automatik über die Boot-Kennung des Systems.
- Tests: neun Fälle für die Automatik (einmal pro Start, Warten auf Kamera, Aufgeben, Abbrechen, neuer Start, Einstellung speichern und prüfen).

## 0.9.14 (Beta)
- Behoben: **Bitrate bricht ein und bleibt unten hängen.** Zwei Ursachen, beide gefunden und mit Messungen belegt:
  1. **Regler im Encoder (belacoder):** Der Original-Regler senkt die Bitrate schon bei normalem Mobilfunk-Rauschen der RTT (15 bis 30 ms) um
     mindestens 100 kbit/s, erhöht aber nur bei fast tiefstem RTT und nur um 30 kbit/s plus 3 Prozent pro halbe Sekunde. Er fällt dadurch nach
     einer kurzen Überlast auf das Minimum und kommt nicht mehr hoch. Neu: `belacoder/` mit einem kleinen Patch (GPL-3.0, Upstream-Commit
     `ccce9ca`): 30 ms Mindestabstand bei der RTT-Schwelle, kleinere Senkungsschritte bei niedriger Bitrate, mehr Toleranz beim Erhöhen. Die
     Schnellbremse bei echtem Stau bleibt unverändert. `install.sh` baut ihn nach `/opt/pipbox/bin/belacoder`; die Sendekette nimmt ihn, wenn er
     da ist, sonst das Original aus dem BELABOX-Paket (schlägt der Bau fehl, ändert sich nichts).
  2. **Empfänger:** Auch die Einstellungen des SRT-Empfängers (Wartezeit bei Verlustmeldungen) beeinflussen die Bitrate; zu große Werte lassen die
     Bestätigungen ausbleiben, und der Encoder senkt die Bitrate.
- Doku: NOTICE (belacoder-Patch, GPL-3.0), README (Empfänger-Hinweis).

## 0.9.13 (Beta)
- Neu: **Protokolle in zwei Stufen** (Karte "Protokolle: Speicherkarte schonen", getrennter Root-Helfer `pipbox-logmode.py` mit fester
  Liste "sparsam" / "ausfuehrlich"). *Sparsam*: Journal nur im Arbeitsspeicher (`Storage=volatile`, 20 MB) und Zustandsprotokoll
  in `/run` (höchstens 1 MB): im Normalbetrieb schreibt das Paket kaum etwas auf die Speicherkarte, nach einem Absturz oder
  Stromausfall bleibt aber keine Spur. *Ausführlich*: wie bisher (Journal dauerhaft, höchstens 30 MB und 7 Tage;
  Zustandsprotokoll auf der Karte, alle 10 s). Neue Installationen starten mit "sparsam"; Boxen mit dem früheren
  dauerhaften Journal bleiben beim Update auf "ausführlich". Die Modus-Datei ist `/etc/pipbox/logmode`, die Journal-Einstellung
  heißt jetzt `pipbox-journal.conf` (vorher `pipbox-persistent.conf`). Die Deinstallation entfernt beides.
- Doku: Installationsanleitung mit `wget` im README (Herunterladen, Entpacken, `install.sh`, Anmeldung, Setup-Code).
- Tests erweitert (Modus-Wechsel, Helfer, Zustandsprotokoll folgt dem Modus).

## 0.9.12 (Beta)
Sicherheit, Ressourcenverbrauch und Aufräumen nach einer unabhängigen Prüfung des Quelltexts.
- Sicherheit: Die Root-Helfer lesen ihre Anforderungsdateien im Ordner des Benutzers `pipbox` nur noch ohne Verweisen
  (Symlinks) zu folgen, nur bis 4 KB und nur normale Dateien; unbekannte Inhalte werden nicht mehr ins Protokoll geschrieben.
  Status- und Protokolldateien des Update-Helfers, die Verzögerungsdatei und der Arbeitsordner der Sendekette werden ohne
  Verweise geschrieben. Die Zahlenfelder des Bildaufbaus (Ecken, Größe, Position, Verzögerung) werden vor dem Erzeugen des
  Pipeline-Textes zu Zahlen in festen Bereichen gezwungen. Eine negative Anfragelänge wird abgelehnt.
  "WLAN vergessen/ersetzen" lässt das Profil des Kameranetzes unangetastet. Vorabversionen von GitHub werden nicht mehr
  als Wechselziel angeboten.
- Weniger Last: Die Seite fragt im Hintergrund-Tab nichts ab und zugeklappte Karten nur selten (DJI, Update, Netze, Fernzugriff,
  WLAN, Bildaufbau); die Abfragen überlappen nicht mehr. Der Server merkt sich teure Ergebnisse kurz: Netzadressen (3 s),
  Dienststatus und nginx-Statistik (1,5 s), Messwerte (1,5 s), die Paketliste (bis sich die Datei ändert) und tailscale (20 s).
  Die Sendekette liest die Netzliste nur noch alle 6 s statt alle 2 s.
- Weniger Schreiben auf die Speicherkarte: Das Zustandsprotokoll schreibt alle 10 s statt alle 2 s und erzwingt das Speichern
  nur alle 30 s (bei plötzlichem Stromausfall fehlen bis zu 30 s). Das Journal speichert alle 5 Minuten statt alle 5 Sekunden.
- Update-Prüfung bei GitHub höchstens alle 6 Stunden und nie während einer Übertragung (der Knopf "Suchen" fragt immer nach).
- Behoben: Stürzt `srtla_send` ab, startet es mit der Einstellung "alle Leitungen" wieder (die 300-ms-Spanne ging verloren).
  Fehlt der gepatchte Sender, wartet der Start nicht mehr 20 Sekunden umsonst.
- Doku: KONZEPT.md neu geschrieben (ohne überholte und interne Teile), Lizenzhinweis zu `srtla/` (AGPL-3.0), Hinweise zur
  Deinstallation und zu `install/optional/`, korrigierte Kommentare und Angaben (300 ms, PBKDF2, Port).
- Tests erweitert (Verweise in Anforderungsdateien, Zahlenprüfung, Arbeitsordner).

## 0.9.11 (Beta)
- Neu: **Sendemodus** (Knopf im Kopf der Seite und in der Live-Karte). Er blendet Adressen, Namen und Protokolle aus, die beim
  Filmen des Bildschirms nicht zu sehen sein sollen: SRTLA-Server (Adresse), Fernzugriff, WLAN-Name und Netzliste, DJI-Bluetooth-
  Adressen, Technik- und Update-Protokolle. Wird nur im Browser gemerkt. Private IP-Adressen der Netzwerkkarten bleiben sichtbar.
- Neu: **Hinweis "Noch nicht übernommen".** Einstellungen, die erst nach einem Neustart der Sendung wirken (SRTLA-Server,
  Bitrate, Latenz, Verteilung), werden erkannt: nach dem Speichern erscheint ein Fenster mit "Stream neu starten" / "Später",
  in der Live-Karte bleibt ein gelber Hinweis. Die Sendekette meldet dafür beim Start Prüfsummen ihrer Einstellungen (ohne Stream-ID).
- Neu: **Gesamt-Upload in der Live-Karte** (grün ab 1,5 Mbit/s, orange ab 0,5, sonst rot). Server-Name klein darunter statt Adresse.
- Neu: Fuß der Seite mit Version, Hinweis auf Beta, Lizenz, Webseite, Discord, Quellcode und Drittprojekten. Die Versionsanzeige oben entfällt.
- Neu: Die Karte "SRTLA und WLAN" ist geteilt in "SRTLA: Server, Bitrate und Latenz" und "Netze zum Senden und WLAN".
- Verbessert: **Netze zum Senden wirken sofort**, auch während der Sendung (srtla_send liest die Liste per Signal neu).
- Behoben: **Fehlalarm beim Start der Sendung.** Der Encoder startete eine Sekunde nach srtla_send und gab nach wenigen Sekunden
  auf (ein automatischer Neustart pro Start, Meldung "Verbindung abgebrochen"). Jetzt wartet er, bis ein Weg zum Server steht.
  Meldungen in der Live-Karte sind verständlicher und verschwinden nach drei Minuten.
- Behoben: **DJI "Kamera nicht mehr sichtbar".** Die Bluetooth-Suche läuft jetzt während des Verbindens weiter; veraltete Einträge
  werden früher vergessen.
- Behoben: Ein nicht mehr vorhandenes Netz (z. B. frühere eth1) blockierte alle Änderungen an den Sendewegen. Das Häkchen speichert
  nur noch die Netzauswahl und überschreibt keine anderen Einstellungen mehr (Mindestbitrate sprang zurück).
- Geändert: Virtuelle Netze (Tailscale, Docker u. ä.) erscheinen nicht mehr als Sendeweg. Bei "Alle Leitungen gleichzeitig" gilt
  300 statt 150 ms Spanne. Namen: "USB-Router (eth2)", "Bildaufbau" statt "Pipeline".
- Tests erweitert (Prüfsummen, Teilspeichern, Netzfilter).

## 0.9.10 (Beta)
- Neu: **Kleine Bilder frei verschieben.** In der Pipeline-Karte gibt es eine Vorschau, in der die kleinen Bilder mit Maus oder Finger
  an jede Stelle gezogen werden. Die Position wird als Promille des Verschiebewegs gespeichert (das Bild bleibt immer im Bild);
  die vier Ecken und "unten Mitte" bleiben als Voreinstellungen. Ecke "frei (verschiebbar)" braucht den neuen Überlagerungs-Baustein
  (wird beim Update automatisch neu gebaut). Eigene Umsetzung. Auf der Orange Pi 5 Plus mit
  künstlichen Testbildern an sechs Positionen auf den Pixel genau geprüft; mit echten Kameras noch nicht.
- Neu: **Box herunterfahren und neu starten** in der Oberfläche (Karte "Box ausschalten", mit Rückfrage und Warnung während einer
  Übertragung). Root-Helfer `pipbox-power.py` mit fester Liste (poweroff, reboot); schließt vorher das Protokoll sauber ab, damit nach
  dem Ausschalten nichts fehlt. Einschalten geht weiter nur über Strom oder Taste.
- Verbessert: **Installation auf einem frischen BELABOX-Image.** `install.sh` installiert fehlende Pakete (bluez, python3-dbus,
  python3-gi) und legt die Gruppe "bluetooth" an; vorher fehlten sie auf der Orange Pi.
- Geändert: Netzwerkkarten heißen nach ihrer Art ("Ethernet (eth0)", "USB-Netz, z. B. USB-Router (eth1)", "WLAN (wlan0)") statt nach
  festen Annahmen über die ROCK 5B+.
- Neu: Tests für freie Position, Ausschalter und die neuen Prüfungen.

## 0.9.9 (Beta)
- Behoben: **Ruckeln im Bild bei drei Kameras.** Zwei getrennte Einblend-Bausteine beschrieben das Hauptbild nacheinander
  (rund 3 verlorene Bilder pro Sekunde). Jetzt zeichnet ein einziger Baustein alle kleinen Bilder in einem Durchgang
  (`pbpipmix` mit `slot2/corner2/slot3/corner3`). Gemessen mit drei Kameras: 0 Frame-Drops bei ca. 13 Mbit/s.
  Der Überlagerungs-Baustein wird beim Software-Update automatisch neu gebaut.
- Neu: **Vierte Kamera** (drittes kleines Bild) mit eigener Ecke und eigener Verzögerung. Die Automatik zum Umschalten
  bei Kameraausfall kennt alle vier Plätze. Der **Ton** lässt sich von jeder der vier Kameras wählen.
- Neu: **Latenzbewusster SRTLA-Sender** (`srtla/`, Patch auf BELABOX/srtla Commit 37862da, AGPL-3.0). Er misst Laufzeit und
  Jitter jeder Leitung und sendet über die guten; langsamere Leitungen bleiben als Reserve. Das verhindert den Einbruch der
  Bitrate bei Leitungen mit unterschiedlicher Laufzeit (z. B. DSL + Mobilfunk). Wird von `install.sh` gebaut und nach
  `/usr/local/bin/srtla_send` installiert (das Original bleibt als Rückfall unter `/usr/bin`). Schlägt der Bau fehl, bleibt
  der Original-Sender. Einstellung **Verteilung auf die Sendewege**: "Beste Leitung bevorzugen" (Standard) oder "Alle
  Leitungen gleichzeitig nutzen".
- Neu: **WLAN-Karte** (Hotspot als weiterer Sendeweg): Netze suchen, verbinden, trennen, vergessen; Verbindungsstatus mit
  Netzname, IP und Signal; "Als Sendeweg nutzen" speichert sofort. Root-Helfer `pipbox-wifi.py` (feste Aktionen, strenge
  Prüfung, Passwort nur über stdin an nmcli, nie gespeichert). Das Kameranetz wird nie angefasst.
- Neu: **Upload-Anzeige** zeigt alle verbundenen Netzwerkkarten (Ethernet, WLAN, USB/Mobilfunk) und die Summe.
- Geändert: **Oberfläche neu geordnet.** Live steht oben, alle Karten sind zusammenklappbar (Zustand wird im Browser
  gemerkt, "Alle zu-/aufklappen" in der Kopfzeile). Status fasst Meldungen, CPU, Arbeitsspeicher und Upload zusammen,
  Kameras (RTMP + DJI) und "Senden und Netze" (WLAN + SRTLA) sind je eine Karte. Pipeline-Karte: Kamera, Ecke und Verzögerung
  stehen pro Bild untereinander. Sendewege als Zeilen mit Live-Upload.
- Behoben: Eine DJI-Kamera, die nur noch als Rest in der Bluetooth-Liste steht (z. B. Action 6 vor der Kopplung), wird nicht
  mehr als verbindbar behandelt; die Fehlermeldung nennt jetzt den Grund. Hinweis: Neue Kameras erst im Kopplungsmodus
  verbinden; ggf. Werkseinstellung, wenn die Kamera keine Kopplungsabfrage zeigt.
- Neu: Tests `tools/test_ui_backend.py` (vier Kameras, Tonauswahl, Verteilung, WLAN-Anfragen und -Prüfungen).

## 0.9.8 (Beta)
- Verbessert: **Kameras kommen nach einem Ausfall zuverlässiger zurück.** Die Kameras verbinden sich nach einem Ausfall nacheinander statt
  gleichzeitig (ein Bluetooth-Chip bricht parallele Verbindungsversuche gegenseitig ab: "le-connection-abort-by-local", "Dienste der Kamera
  nicht aufgelöst"). Höchstens eine neue Verbindung je Wächter-Durchlauf. Ist das Kameranetz (USB-Router) weg, wird nicht neu verbunden und
  keine Wartezeit verbraucht; kommt es wieder, werden die Wartezeiten zurückgesetzt und nach 8 s die Kameras neu verbunden.
- Neu: Tests `tools/test_daemon.py` (Wächter und Verbindungssperre, ohne Bluetooth).

## 0.9.7 (Beta)
- Neu: **Automatisches Umschalten bei Kameraausfall.** Fällt eine genutzte Kamera länger als 5 Sekunden aus, sendet die Box mit den
  übrigen weiter (fehlt das Hauptbild, wird das kleine Bild zum Hauptbild). Eine zurückgekehrte Kamera kommt erst nach 60 Sekunden
  stabilem Signal zurück, damit die Übertragung nicht pendelt. Läuft keine Kamera, wartet die Box ruhig, statt dauernd neu zu starten.
  Nur der Encoder startet beim Umschalten neu, die Verbindung zum Server bleibt. Die Verzögerung folgt der Kamera, nicht dem Platz.
  Schalter "Automatisch umschalten" in der Pipeline-Karte (Standard: an), Hinweis "Notbetrieb" in der Live-Karte.
  Gilt ab dem nächsten "Live gehen".
- Neu: Position **"unten Mitte"** für das kleine Bild. Sie erscheint in der Auswahl, sobald der Überlagerungs-Baustein neu gebaut
  ist (beim Software-Update automatisch); mit dem alten Baustein wird sie nicht angeboten.
- Neu: Tests in `tools/` (Umschaltlogik, Positionsfunktion).

## 0.9.6 (Beta)
- Behoben: Die Karte "System-Updates" zeigte nach einem Neustart der Box weiter gelb "Neustart…". Der Zustand "Neustart" wird jetzt
  als erledigt gewertet, sobald die Box seit dem Befehl neu gestartet ist (Boot-Kennung, sonst Zeitvergleich).

## 0.9.5 (Beta)
- Geändert: In der Kameraliste (RTMP) stehen Kameras ohne Signal unten, die sendenden oben.

## 0.9.4 (Beta)
- Neu: Die CPU-Karte zeigt die Lüfter-Ansteuerung in Prozent ("Lüfter: 24 % (Ansteuerung)"). Das ist der Sollwert (PWM), keine gemessene
  Drehzahl: die ROCK 5B Plus hat keinen Drehzahlanschluss am Lüfter. Ohne erkennbaren Lüfter bleibt die Zeile ausgeblendet.
- Geändert: In der Pipeline-Auswahl steht nur noch der Kameraname. Der Schlüssel in Klammern erscheint nur, wenn zwei Kameras denselben Namen haben.

## 0.9.3 (Beta)
- Behoben: Verbundene DJI-Kameras fehlten nach der Bluetooth-Suche in der Liste. Dadurch ließen sich ihre Einstellungen nicht
  ändern und sie nicht stoppen. Jetzt bleiben alle Kameras sichtbar, die laufen sollen, auch nach einem Neustart des Dienstes.
- Neu: Die Box merkt sich einmal gesehene DJI-Kameras. Ausgeschaltete stehen unten in der Liste, ihre Einstellungen bleiben änderbar;
  starten lassen sie sich erst, wenn sie nach dem Einschalten gefunden wurden.
- Geändert: Die Bildrate in der Kameraliste steht immer einfach als "30 fps", ohne den Zusatz "(eingestellt)".

## 0.9.2 (Beta)
- Protokolle begrenzt: Journal höchstens 30 MB und 7 Tage, Zustandsprotokoll höchstens 4 MB (etwa 1,5 Tage), Protokolle der Update-
  und Fernzugriff-Helfer je 512 KB. Das Journal wird dauerhaft gespeichert, damit nach einem Ausfall Spuren bleiben.

## 0.9.1 (Beta)
- Neu: **Fernzugriff über Tailscale** direkt aus der Oberfläche (freiwillig): installieren, mit dem eigenen Konto verbinden,
  die Oberfläche nur im privaten Netz freigeben, trennen, abmelden. Knöpfe zu den Stores für Android, iPhone, Mac, Windows, Linux.
  Anleitung: `ANLEITUNG-Fernzugriff.md`. Funnel (öffentlich) wird nie eingeschaltet, die Karte warnt, falls es aktiv ist.

## 0.9.0 (Beta)
- Erste Beta-Version. Aus der Alpha-Phase heraus: stabiler Betrieb mit drei Kameras in der Pipeline getestet.
- Neu: Software-Update aus der Oberfläche (Suchen, Installieren). Die letzten 5 Versionen bleiben gesichert; man kann gezielt
  auf eine Version wechseln, auch auf eine ältere (gesichert oder als Release-Marke vX.Y.Z auf GitHub).
- Neu: Verzögerung für Hauptbild und beide kleine Bilder per Regler, live ohne Neustart der Sendekette.
- Neu: Kamera umbenennen, Auswahl ohne doppelte Kameras oder Ecken, Ausgangswerte nach Rolle (Hauptbild 1080p/8 Mbit/s,
  kleine Bilder 720p/4 Mbit/s).
- Neu: Zustandsprotokoll für den Fall eines Totalausfalls, Hinweis in der Oberfläche, wenn das Kameranetz fehlt.
- Verbessert: Kamera-Dienst folgt der Adresse der Box im Kameranetz, sucht gemeinsam und heilt hängende Bluetooth-Adapter.

## 0.1.0 (Alpha)
- Weboberfläche, RTMP-Kameras, DJI-Kopplung per Bluetooth, SRTLA-Serverliste, Pipeline, eigene Sendekette.
