# IRL4YOU PIP / IRL4YOU BOX

**Webseite:** [irl4you.de](https://irl4you.de) · **Discord:** [Community beitreten](https://discord.gg/nrBCEarMup) (Fragen, Fehler, Ideen)

**Version 0.9.70 (Beta).** Zusatzpaket für eine BELABOX mit eigener Weboberfläche: Kameras (RTMP und DJI per Bluetooth), Bild-in-Bild mit bis zu vier Kameras, Hauptbild wechseln, Upload über mehrere Leitungen (SRTLA), Software-Update und mehr. Es läuft **getrennt von der Original-Oberfläche** der BELABOX.

## Installation auf der Box

Voraussetzung: eine BELABOX mit dem BELABOX-Image (getestet: Radxa ROCK 5B+ und Orange Pi 5 Plus), Internet auf der Box und ein
Terminal auf der Box (SSH oder Tastatur). Nicht während einer Übertragung installieren.

**Schritt 1: BELABOX-Passwort.** Hat die BELABOX noch kein Passwort (frisches Image), zuerst in der BELABOX-Oberfläche
(`http://<Adresse der Box>/`) eines festlegen. Die Oberfläche dieses Pakets meldet sich mit demselben Passwort an; einen eigenen Setup-Code gibt es nicht. Mit dem Haken "Angemeldet bleiben" bleibt die Anmeldung 30 Tage bestehen, auch über Updates hinweg.

**Schritt 2: Installieren.** Auf der Box im Terminal:

```sh
cd /tmp
wget -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz
tar xzf irl4you-pip.tar.gz
cd irl4you-pip-main
sudo sh install/install.sh
```

Die Installation lädt fehlende Pakete nach und baut den Bild-in-Bild-Baustein und den SRTLA-Sender selbst; das kann einige
Minuten dauern.
Dazu gehört die Bluetooth-Bibliothek `bleak` für den DJI-Dienst (per `pip`, braucht Internet). Gelingt das nicht, bricht die
Installation mit einer Meldung ab, bevor etwas verändert wurde.

**Schritt 3: Anmelden.** Im Browser `http://<Adresse der Box>:8780` öffnen und mit dem BELABOX-Passwort anmelden. Ist in Schritt 1
noch kein Passwort gesetzt worden, steht auf der Seite, dass es zuerst in der BELABOX-Oberfläche festgelegt werden muss; die Seite
wartet darauf und zeigt die Anmeldung dann von selbst.

Spätere Versionen spielt die Karte "Software-Update" in der Oberfläche ein, ein erneutes Installieren ist nicht nötig.

Das Paket schreibt nach der Installation sehr wenig auf die Speicherkarte ("Protokolle: sparsam"). Für die Fehlersuche lässt
sich in der Karte "Protokolle" die ausführliche Stufe einschalten.

Rückweg: `sudo sh install/install.sh uninstall`.
Die Deinstallation entfernt Dienste und Programme. Liegen bleiben der Zustand (`/var/lib/pipbox`), die Sicherungen
(`/var/lib/pipbox-backup`), die Protokolle (`/var/log/pipbox-*.log`) und der Benutzer `pipbox`. Die Journal-Einstellung
und `/etc/pipbox` werden entfernt.

Optional, **nicht automatisch installiert** (`install/optional/`, nur für den Aufbau des Entwicklers auf der ROCK 5B+):
`pipbox-net.service` hält die feste Zweitadresse 192.168.80.50 für ein Kameranetz an `eth1`, und
`80-pipbox-no-internal-bt.rules` schaltet das eingebaute Bluetooth-Modul ab, damit nur ein USB-Bluetooth-Stick genutzt wird.
Der Knopf für **System-Updates** installiert echte Systemupdates (Kernel, BELABOX-Pakete) und kann die Box nach einem
Stromausfall unbrauchbar machen. Nur ohne laufende Übertragung und mit stabiler Stromversorgung benutzen.

## Stand und Test

Getestet auf einer Orange Pi 5 Plus (BELABOX-Image) mit vier DJI-Kameras (zwei Osmo Action 4,
Action 5 Pro, Action 6) gleichzeitig: Hauptbild und drei kleine Bilder bei rund 13 Mbit/s, die Box war dabei zu rund 70 Prozent im
Leerlauf (CPU im Mittel etwa 25 bis 30 Prozent, 35 bis 37 °C). Ein Dauertest über gut acht Stunden am 4. Oktober 2026 lief mit allen vier Kameras
ohne Aussetzer und ohne Neustart der Sendekette, nachdem das Kamera-WLAN auf WPA2 und 5 GHz umgestellt war (siehe "Hinweise zum Kamera-WLAN").
Auf der Radxa ROCK 5B+ wurde nur ein älterer Stand getestet (0.9.10).

Das Paket ändert nur eine Einstellung des RTMP-Servers der BELABOX (Leerlaufgrenze für Kameras, mit Sicherung und Rückweg; ein kleiner apt-Haken stellt sie nach einem Update des BELABOX-Pakets wieder her) und sonst keine
BELABOX-Dateien, damit BELABOX-Updates weiter möglich bleiben. Eigene Weboberfläche mit Anmeldung über das
vorhandene BELABOX-Passwort (nur ohne belaUI, etwa in der Entwicklung, gilt ein eigenes Passwort).

## Was geht

- **Status:** Drei Kästen nebeneinander. *System*: CPU (Gesamtwert und höchster Kern), Temperatur, Lüfter und Arbeitsspeicher. *Kameras*: je Kamera Name,
  **Ampel** und aktuelle Eingangsbitrate. *Upload*: je verbundener Netzwerkkarte Datenrate samt **Ampel** und Summe (Ethernet, WLAN, USB-/Mobilfunk-Router). Warnungen
  erscheinen darüber; sind keine da, bleibt der Platz leer.
- **RTMP-Kameras:** neue Streams werden automatisch erkannt; Kameras lassen sich umbenennen. Jede Kamera (zum Beispiel ein Handy) kann über eine eigene Verbindung der Box senden, etwa einen zweiten Router, um die Last zu verteilen; die angezeigte Adresse gilt dann für diese Verbindung.
- **DJI-Kameras per Bluetooth** (Protokoll nach Moblin, MIT): Suche, Koppeln, WLAN und RTMP-Ziel übergeben, Start, mit Statusmeldungen je Schritt.
  **Je Kamera wählbare Verbindung** aus den vorhandenen Verbindungen der Box: WLAN-Hotspot und WLAN-Netze mit Name und Passwort aus NetworkManager, alle anderen
  (Ethernet, USB-Router, Modem) mit einmal eingetragenem und gespeichertem WLAN der Kamera. Pro Kamera Auflösung, fps, Bitrate und Stabilisierung.
  Ein eigener Dienst (`pipbox-dji`, Bibliothek `bleak`) hält die Verbindungen, verbindet nach Ausfällen neu, überwacht, ob der Stream ankommt, und
  verbindet mehrere Kameras nacheinander (gleichzeitig bricht auf dem Funkchip ab).
- **SRTLA-Serverliste:** mehrere Server speichern und per Auswahl umschalten (Stream-ID wird nie angezeigt).
- **Pipeline:** eine Kamera oder Bild-in-Bild mit bis zu drei kleinen Bildern (vier Kameras), Ecke und Größe wählbar, Ton von
  jeder Kamera. Die kleinen Bilder lassen sich in einer Vorschau frei verschieben (oder als Ecke wählen). Je kleinem Bild lassen sich Skalierung (1 bis 100 %), Ein-/Ausblenden, Beschnitt (Pixel links, rechts, oben, unten, bezogen auf 1920 x 1080), Eckenrundung und ein Rahmen (Dicke, Farbe, Deckkraft) einstellen; die Vorschau zeigt es sofort. Das Hauptbild liest der Baustein dafür nur dort, wo etwas durchscheinen muss. Ein kleiner eigener GStreamer-Baustein (`gst/`) schreibt die kleinen Bilder in einem Durchgang direkt in
  das Hauptbild. Fällt eine Kamera aus, schaltet die Box automatisch auf die übrigen um (das dauert etwa 5 Sekunden ohne Bild) und nimmt die Kamera erst nach 60 Sekunden stabilem Signal wieder auf.
- **Hauptbild wählen:** Bei Bild-in-Bild steht in der Live-Karte unter "Hauptbild" ein Schalter mit den Kameras, die im Bild sind. Die aktuelle Hauptkamera ist hervorgehoben, ein Klick auf eine andere macht sie zum Hauptbild und tauscht die beiden (Kamera und Verzögerung bleiben beisammen, Ecke, Größe und Ton bleiben am Platz). Läuft die Sendung, startet der Encoder dafür neu und das Bild ist etwa 5 Sekunden unterbrochen. **Experimentell:** Wählt man im Bildaufbau "Hauptbild tauschen ohne Unterbrechung" (Hauptbild und erstes kleines Bild, oder alle Kameras), läuft der Tausch ohne Neustart des Encoders als harter Schnitt, Ton inklusive. Dafür dekodiert die Box jede Kamera der Gruppe zweimal (groß und klein, bei vier Kameras 6 statt 4, bei "alle" 8 Dekodierungen). Im Heimnetz mit vier DJI-Kameras geprüft (mehrere Tausche in Folge, ohne Neustart und ohne Einbruch im Upload); noch nicht über längere Zeit und unterwegs. Die Kameras der Gruppe sollten dieselbe Auflösung senden. Eine Überblendung ist geplant.
- **Gleichlauf:** Verzögerung für Hauptbild und jedes kleine Bild per Regler (0 bis 3000 ms), bei laufender Sendekette ohne
  Neustart änderbar. Zum Abgleichen liegt eine Stoppuhr unter `tools/stopwatch.html`.
- **Ausgangswerte (frei änderbar):** Hauptbild 1080p/30 fps/8 Mbit/s, kleine Bilder 720p/30 fps/4 Mbit/s (nach der Rolle in
  der Pipeline), gleiche Stabilisierung bei allen Kameras, Hauptbild um 450 ms verzögert (Schätzung).
- **Live gehen / beenden:** eigene Sendekette (`belacoder` + `srtla_send`) als getrennter Root-Dienst mit strenger Prüfung
  aller Werte. Der gebaute `srtla_send` ist ein **latenzbewusster Patch** auf BELABOX/srtla (siehe `srtla/`, AGPL-3.0): er
  misst Laufzeit und Jitter je Leitung und verhindert so den Bitrate-Einbruch bei Leitungen mit unterschiedlicher Laufzeit.
  Verteilung wählbar: beste Leitung bevorzugen (Standard) oder alle gleichzeitig.
- **WLAN / Hotspot als Sendeweg:** Netze suchen, verbinden, trennen und als Sendeweg wählen, direkt in der Oberfläche
  (Root-Helfer mit festen Aktionen; das Passwort wird von diesem Projekt nicht gespeichert, NetworkManager legt es im WLAN-Profil ab).
- **Box ausschalten:** Herunterfahren und Neu starten direkt in der Oberfläche (Root-Helfer mit fester Liste, Protokoll wird vorher sauber geschlossen).
- **Fernzugriff (freiwillig):** Über Tailscale von unterwegs, standardmäßig nur im privaten Netz (nur Geräte in Ihrem Tailscale-Konto). Einrichten direkt in der
  Oberfläche; Anleitung: [ANLEITUNG-Fernzugriff.md](ANLEITUNG-Fernzugriff.md). Wer auch **ohne Tailscale-App** von überall zugreifen will, kann auf
  ausdrücklichen Knopfdruck **Funnel** einschalten (öffentlich im Internet, nur durch das BELABOX-Passwort geschützt, rote Warnung, bleibt bis zum Beenden an, auch nach einem Neustart).
- **Software-Update:** In der Oberfläche nach neuen Versionen suchen und installieren. Die letzten 5 Versionen bleiben
  gesichert; man kann gezielt auf eine Version wechseln, auch auf eine ältere (gesichert oder als Release `vX.Y.Z` auf
  GitHub). Nicht während einer Übertragung. Ein **gelber Punkt in der Kopfleiste** zeigt, wenn eine neuere Version da ist. Ein zweiter Punkt zeigt, wenn **Systemupdates** der BELABOX bereitliegen; dafür sucht die Box nach jedem Start und danach alle 6 Stunden still nach (nie während einer Übertragung). Ohne Updates sind beide Punkte aus.
- **System-Updates** der BELABOX über einen getrennten Root-Helfer mit festen Aktionen. Die Box sucht kurz nach jedem Start und danach alle 6 Stunden von selbst nach Updates (nur die Paketliste, nie während einer Übertragung); der gelbe Punkt "System" in der Kopfleiste zeigt, wenn welche bereitliegen.
- **Automatisch live gehen** nach dem Start der Box (Schalter in der Live-Karte, standardmäßig aus): einmal pro Start, sobald eine Kamera sendet.
- **Streammodus** (Knopf im Kopf der Seite und in der Live-Karte): blendet Adressen, Namen und Protokolle aus, wenn der Bildschirm mitgefilmt wird. Die Einstellung merkt sich nur der Browser.
- **Mindestanteil je Sendeweg** (bei "Alle Leitungen gleichzeitig nutzen"): Jeder geeignete Weg bekommt mindestens 10 Prozent der Pakete, damit auch ein schwächerer Weg (Mobilfunk neben DSL, Starlink neben 5G) warm bleibt und bei einem Ausfall des besten nicht erst anlaufen muss. Siehe `srtla/README.md`.
- **Kamera-Ampel:** Der Punkt vor jeder Kamera in der Kameraliste zeigt den Zustand auf einen Blick. **Grün**: sendet und ist im Bild. **Gelb**: sendet, ist aber noch nicht oder nicht mehr im
  Bild (zum Beispiel beim Wiederverbinden: eine zurückgekehrte Kamera wird erst nach 60 s stabilem Signal wieder aufgenommen, damit eine wackelige Kamera nicht dauernd den Encoder neu startet).
  **Rot**: kein Signal. **Grau**: Status unbekannt. Beim Darüberfahren erscheint eine kurze Erklärung. Die Ampel steht auch in der Karte "Status" (Name, Punkt und aktuelle Eingangsbitrate), damit man die Kameraliste nicht öffnen muss.
- **Ampel für die Sendewege** (Kasten "Upload" in der Karte "Status"): **Grün** = der Weg trägt Pakete, **Gelb** = verbunden, aber in Reserve (Laufzeit zu hoch oder unruhig), **Rot** = nicht verbunden oder kein Netz, **Grau** = keine Sendung.
- **Stabile Bitrate über gebündelte Mobilfunkleitungen:** Der Encoder bekommt einen toleranteren Regler (kleiner Patch auf BELABOX/belacoder, siehe
  `belacoder/README.md`), damit die Bitrate nach einer kurzen Überlast wieder hochkommt, und einen Stall-Wächter, der nur den Ausgang prüft (ein kurzer Aussetzer einer kleinen Kamera beendet die Sendung nicht mehr). Auch die Einstellungen des Empfängers (SRT-Latenz, Umordnungstoleranz) beeinflussen die Bitrate.
- **Entwickler** (Karte "Entwickler"): SSH-Zugang ein- und ausschalten und das SSH-Passwort anzeigen, wie in der Original-Oberfläche der BELABOX. Der Schalter
  startet und beendet nur den SSH-Dienst; Passwort, Schlüssel und Einstellungen von SSH bleiben unberührt (das Passwort erzeugt die Original-Oberfläche).
- **WLAN-Hotspot** (Karte "Verbindungen"): macht aus einem WLAN-Stick ein eigenes WLAN der Box (Name, Passwort, 2,4 oder 5 GHz, Kanal), zum Beispiel für
  DJI-Kameras oder ein Handy. Die Kamera übernimmt Name und Passwort selbst. Mit NetworkManager umgesetzt; **mit echtem Stick und Kamera noch nicht geprüft**.
- **Protokolle herunterladen** (Karte "Protokolle", Knopf "Protokolle herunterladen"): eine Textdatei mit den Meldungen der Box für die Fehlersuche
  oder ein GitHub-Issue. Passwörter, Stream-ID, Servername, WLAN-Namen sowie IP- und MAC-Adressen werden vorher durch Platzhalter ersetzt (vor dem Weitergeben
  trotzdem kurz durchsehen).
- **Protokolle, in zwei Stufen** (Karte "Protokolle"). *Sparsam* (Standard bei neuen Installationen): Journal
  und Zustandsprotokoll nur im Arbeitsspeicher, die Speicherkarte wird geschont, nach einem Absturz oder Stromausfall bleibt aber
  keine Spur. *Ausführlich* (zur Fehlersuche): Journal dauerhaft (30 MB/7 Tage) und Zustandsprotokoll
  (`/var/log/pipbox-health.log`, alle 10 Sekunden, höchstens 4 MB), damit nach einem Totalausfall sichtbar bleibt, was kurz
  davor los war. Boxen, die vor 0.9.13 installiert wurden, bleiben beim Update auf "ausführlich".

## Hinweise zum Kamera-WLAN (aus dem Betrieb)

DJI-Kameras setzen im WLAN gelegentlich für einige Sekunden mit den Daten aus. Was in unserem Aufbau (GL.iNet-Router mit Mobilfunk, vier Kameras, Orange Pi 5 Plus) geholfen hat:

- **Nur 5 GHz, WPA2, 20 MHz Kanalbreite:** Nach der Umstellung von WPA3 auf WPA2 gab es über Stunden keinen Aussetzer mehr (vorher bei einer Kamera etwa einen pro Minute). Ein Kanalwechsel allein
  (44 auf 36 und zurück) brachte nichts. WPA2 mit AES und einem langen Passwort ist sicher genug für ein reines Kameranetz.
- **Kanal 36 bis 48 (kein DFS):** Die Osmo Action 5 Pro unterstützt im 5-GHz-Band laut Datenblatt nur 5150 bis 5250 und 5725 bis 5850 MHz, also nicht die DFS-Kanäle in der Mitte. Nach unserem Kenntnisstand (bitte selbst prüfen) sind in Deutschland 36 bis 64
  nur für den Innenbereich freigegeben und 149 bis 165 für private WLANs nicht.
- **RTMP-Leerlaufgrenze:** Der RTMP-Server der BELABOX wirft eine Kamera, die 4 Sekunden lang nichts schickt, aus dem Bild (`drop_idle_publisher 4s`); jedes Mal startet der Encoder dann neu. Dieses Paket setzt
  die Grenze auf 15 Sekunden (mit Sicherung `99-belabox-rtmp.conf.vor-pipbox`) und stellt sie nach einem Update des BELABOX-Pakets per apt-Haken wieder her. Die Aussetzer der Kamera selbst beseitigt das nicht, sie
  laufen nur durch, ohne dass etwas neu startet.
- **Ampeln:** In der Karte "Status" zeigt die Ampel der Kameras, ob eine Kamera im Bild ist (grün), gerade wieder aufgenommen wird (gelb) oder fehlt (rot). Die Ampel der Sendewege zeigt, welcher Weg trägt (grün),
  in Reserve steht (gelb) oder fehlt (rot).

## Bluetooth-Stick für die DJI-Kameras

Die eingebauten Bluetooth-Module der Boxen empfangen schlecht, darum ist ein USB-Stick besser. Der Abschnitt "Bluetooth" in der Karte "Verbindungen" zeigt, welche Bluetooth-Sticks laufen (mit Name und USB-Kennung), ob ein Treiber gerade eingerichtet wird, und meldet einen Stick, aus dem der Kernel keinen Adapter macht (nicht unterstützt oder ohne Treiber).

| Stick | Chip | Stand |
|---|---|---|
| ASUS USB-BT500 (`0b05:190e`) | Realtek RTL8761B | **getestet**, läuft auf der Orange Pi 5 Plus (auch ohne Zusatztreiber) |
| TP-Link UB500 (`2357:0604`) | Realtek RTL8761BUV | der Kernel 5.10 kennt ihn nicht; **die Box richtet den Treiber beim Einstecken selbst ein** (siehe unten). **Noch nicht an der Box mit diesem Stick geprüft** |
| weitere Realtek-Sticks (`2550:8761`, `2c4e:0115` Mercusys MA530, `0bda:8771`, `0bda:a725`, `2b89:8761`) | Realtek RTL8761B | wie der UB500 (Treiber automatisch), nicht geprüft |
| UGREEN Bluetooth 5.4 und 6.0 (CM748, `33fa:0010`/`33fa:0012`) | BARROT BR8654/BR8554 | **geht auf dem Kernel 5.10 der BELABOX nicht**: der Chip bleibt bei der Einrichtung hängen, es entsteht kein Adapter. Laut Berichten ist das erst ab Linux 6.18 (und den Langzeitzweigen ab 6.12.58 und 6.6.117) behoben. Die Oberfläche erkennt diesen Stick und sagt es |

**Automatischer Treiber für Realtek-Sticks.** Manche Realtek-Sticks kennt der Kernel 5.10 nicht in seiner Tabelle: Sie starten ohne Firmware, finden keine Kameras und wirken tot. Steckt so ein Stick (Liste oben) beim Einstecken oder beim Start, baut die Box aus den mitgelieferten Original-Quellen des Kernels
(`bluetooth-src/`, v5.10.160, GPL-2.0, unverändert) das Modul `btusb` neu, mit zusätzlichen Kennungen, spielt es nach `/lib/modules/<Kernel>/updates/` ein und lädt es. Das dauert wenige Minuten. Dabei gilt:

- Gebaut wird nur auf dem passenden Kernel (5.10.160) und mit den vorhandenen Kernel-Headern; die Quellen werden vor dem Bau per SHA-256 geprüft, es wird nichts aus dem Internet geholt.
- **Nie während einer Übertragung** (das Neuladen trennt Bluetooth kurz); die Box wartet, bis die Übertragung beendet ist.
- Nach dem Laden muss ein Adapter da sein, das neue Modul wirklich laufen und die Firmware ohne Fehler laden; sonst wird alles zurückgerollt und für diese Kombination nicht noch einmal versucht. Es läuft dann mit dem Standardtreiber weiter. Das Standardmodul des Kernels wird nie überschrieben.
- Nach einem Kernel-Update gilt der Treiber nicht mehr; die Box prüft nach dem Start und alle 15 Minuten und richtet ihn, wenn für den neuen Kernel vorbereitet, neu ein. Deinstallation und "Rückweg": Datei `/lib/modules/<Kernel>/updates/btusb.ko` löschen (macht `install.sh uninstall`).
- Nach dem Wechsel auf einen anderen Stick fragt eine Kamera eventuell einmal nach der Kopplung (neue Bluetooth-Adresse).

## Was noch fehlt oder ungetestet ist

- Langzeitstabilität über mehr als acht Stunden und mit mehreren Kameras im Dauerbetrieb im Freien. Es gab unerklärte Totalausfälle der Box (zuletzt zwei in der Nacht zum 2. Oktober 2026, ohne
  Fehlermeldung im Protokoll); Verdacht: Stromversorgung, wenn ein USB-Router am USB-C-Port der Box hängt, nicht bewiesen. Auf der Orange Pi 5 Plus lief der aktuelle Stand zuletzt über Stunden ohne Ausfall.
- Ungetestet: Pocket 3 und weitere DJI-Modelle (Protokoll vorhanden, nie mit echter Kamera). Eine neue oder zurückgesetzte Kamera muss im Kopplungsmodus sein und die Kopplungsabfrage bestätigen.
- Mehrere Sendewege: Der Mindestanteil je Weg (10 Prozent bei "alle") wurde bisher nur zu Hause getestet, wo einer der drei Wege das Heimnetz zum Empfänger ist (1 ms) und kaum über DSL läuft. Ein
  schwächerer Weg neben einem guten, etwa Starlink neben 5G, ist nicht geprüft. Gleiches gilt für unterwegs über Stunden.
- Die Action 5 Pro und die Action 6 fallen im WLAN öfter aus als die beiden Action 4 (Ursache offen: Kamera, Firmware oder Funkumgebung).
- Geplant, nicht gebaut: Überblendung beim Wechsel zwischen Hauptbild und kleinem Bild (heute ein harter Schnitt, ohne den Tausch ohne Unterbrechung kostet er etwa 5 Sekunden Bild), eigene
  Empfangsprozesse je Kamera, damit ein Kameraausfall den Encoder nicht anhält, und HDMI- oder USB-Kameras als Quelle.

Siehe [KONZEPT.md](KONZEPT.md) und [CHANGELOG.md](CHANGELOG.md).

## Ansehen ohne Box (Demo-Werte)

```sh
python3 server.py --demo
```

Dann `http://127.0.0.1:8780/` öffnen. In der Demo ist das Passwort schon eingetragen (einfach "Anmelden" klicken); sie läuft nur auf dem eigenen Rechner.

## Software-Update

Die Oberfläche vergleicht ihre `VERSION` mit der Datei auf GitHub (`IRL4YOU/irl4you-pip`, Zweig `main`). Das Einspielen
macht ein getrennter Root-Helfer (`pipbox-swupdate`). Er lädt nur von dieser festen Adresse per HTTPS, prüft das Archiv
streng (nur normale Dateien, keine Pfade nach außen, Größe begrenzt, Python- und Installationsskript fehlerfrei,
Versionsnummer neuer), sichert die jetzige Version, führt `install.sh` aus und rollt bei einem Fehler automatisch
zurück. Wie bei jedem Update wird dabei Code aus dem Repository als root ausgeführt: Vertraue also dem Repository.

## Sicherheit

Zugangsdaten (BELABOX-Passwort, SRTLA-Stream-ID, WLAN-Daten der Kamera) liegen nur auf der Box in `/var/lib/pipbox` mit
eingeschränkten Rechten und gehören nicht in dieses Repository. Root-Helfer nehmen nur feste Stichworte an und prüfen
alles erneut.

## Lizenz

MIT, siehe [LICENSE](LICENSE) und [NOTICE.md](NOTICE.md) (enthält die Lizenzen von Moblin, dessen DJI-Protokoll hier
nachgebaut wurde, und vom DJI-Dienst, dessen Ablauf übernommen wurde). **Ausnahme:** Der Ordner `srtla/` (Patch auf BELABOX/srtla und der damit gebaute Sender) steht unter
AGPL-3.0, wie das Original.
