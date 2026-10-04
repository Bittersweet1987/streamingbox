# IRL4YOU PIP / IRL4YOU BOX

**Webseite:** [irl4you.de](https://irl4you.de) · **Discord:** [Community beitreten](https://discord.gg/nrBCEarMup) (Fragen, Fehler, Ideen)

**Version 0.9.42 (Beta).** Zusatzpaket für eine BELABOX mit eigener Weboberfläche: Kameras (RTMP und DJI per Bluetooth), Bild-in-Bild mit bis zu vier Kameras, Hauptbild wechseln, Upload über mehrere Leitungen (SRTLA), Software-Update und mehr. Es läuft **getrennt von der Original-Oberfläche** der BELABOX.

## Installation auf der Box

Voraussetzung: eine BELABOX mit dem BELABOX-Image (getestet: Radxa ROCK 5B+ und Orange Pi 5 Plus), Internet auf der Box und ein
Terminal auf der Box (SSH oder Tastatur). Nicht während einer Übertragung installieren.

**Schritt 1: BELABOX-Passwort.** Hat die BELABOX noch kein Passwort (frisches Image), zuerst in der BELABOX-Oberfläche
(`http://<Adresse der Box>/`) eines festlegen. Die Oberfläche dieses Pakets meldet sich mit demselben Passwort an; einen eigenen Setup-Code gibt es nicht.

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
- **RTMP-Kameras:** neue Streams werden automatisch erkannt; Kameras lassen sich umbenennen, Rollen zuweisen.
- **DJI-Kameras per Bluetooth** (Protokoll nach Moblin, MIT): Suche, Koppeln, WLAN und RTMP-Ziel übergeben, Start. Pro Kamera
  Auflösung, fps, Bitrate und Stabilisierung. Ein eigener Dienst (`pipbox-dji`) hält die Verbindungen, verbindet nach
  Ausfällen neu, überwacht, ob der Stream ankommt, folgt Änderungen der Box-Adresse im Kameranetz und heilt einen hängenden
  Bluetooth-Adapter.
- **SRTLA-Serverliste:** mehrere Server speichern und per Auswahl umschalten (Stream-ID wird nie angezeigt).
- **Pipeline:** eine Kamera oder Bild-in-Bild mit bis zu drei kleinen Bildern (vier Kameras), Ecke und Größe wählbar, Ton von
  jeder Kamera. Die kleinen Bilder lassen sich in einer Vorschau frei verschieben (oder als Ecke wählen). Ein kleiner eigener GStreamer-Baustein (`gst/`) schreibt die kleinen Bilder in einem Durchgang direkt in
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
- **Fernzugriff (freiwillig):** Über Tailscale von unterwegs, nur im privaten Netz, nie öffentlich. Einrichten direkt in der
  Oberfläche; Anleitung: [ANLEITUNG-Fernzugriff.md](ANLEITUNG-Fernzugriff.md).
- **Software-Update:** In der Oberfläche nach neuen Versionen suchen und installieren. Die letzten 5 Versionen bleiben
  gesichert; man kann gezielt auf eine Version wechseln, auch auf eine ältere (gesichert oder als Release `vX.Y.Z` auf
  GitHub). Nicht während einer Übertragung.
- **System-Updates** der BELABOX über einen getrennten Root-Helfer mit festen Aktionen.
- **Automatisch live gehen** nach dem Start der Box (Schalter in der Live-Karte, standardmäßig aus): einmal pro Start, sobald eine Kamera sendet.
- **Streammodus** (Knopf im Kopf der Seite und in der Live-Karte): blendet Adressen, Namen und Protokolle aus, wenn der Bildschirm mitgefilmt wird. Die Einstellung merkt sich nur der Browser.
- **Mindestanteil je Sendeweg** (bei "Alle Leitungen gleichzeitig nutzen"): Jeder geeignete Weg bekommt mindestens 10 Prozent der Pakete, damit auch ein schwächerer Weg (Mobilfunk neben DSL, Starlink neben 5G) warm bleibt und bei einem Ausfall des besten nicht erst anlaufen muss. Siehe `srtla/README.md`.
- **Kamera-Ampel:** Der Punkt vor jeder Kamera in der Kameraliste zeigt den Zustand auf einen Blick. **Grün**: sendet und ist im Bild. **Gelb**: sendet, ist aber noch nicht oder nicht mehr im
  Bild (zum Beispiel beim Wiederverbinden: eine zurückgekehrte Kamera wird erst nach 60 s stabilem Signal wieder aufgenommen, damit eine wackelige Kamera nicht dauernd den Encoder neu startet).
  **Rot**: kein Signal. **Grau**: Status unbekannt. Beim Darüberfahren erscheint eine kurze Erklärung. Die Ampel steht auch in der Karte "Status" (Name, Punkt und aktuelle Eingangsbitrate), damit man die Kameraliste nicht öffnen muss.
- **Ampel für die Sendewege** (Kasten "Upload" in der Karte "Status"): **Grün** = der Weg trägt Pakete, **Gelb** = verbunden, aber in Reserve (Laufzeit zu hoch oder unruhig), **Rot** = nicht verbunden oder kein Netz, **Grau** = keine Sendung.
- **Stabile Bitrate über gebündelte Mobilfunkleitungen:** Der Encoder bekommt einen toleranteren Regler (kleiner Patch auf BELABOX/belacoder, siehe
  `belacoder/README.md`), damit die Bitrate nach einer kurzen Überlast wieder hochkommt, und einen Stall-Wächter, der nur den Ausgang prüft (ein kurzer Aussetzer einer kleinen Kamera beendet die Sendung nicht mehr). Auch die Einstellungen des Empfängers (SRT-Latenz, Umordnungstoleranz) beeinflussen die Bitrate.
- **Protokolle, in zwei Stufen** (Karte "Protokolle: Speicherkarte schonen"). *Sparsam* (Standard bei neuen Installationen): Journal
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

Dann `http://127.0.0.1:8780/` öffnen.

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

MIT, siehe [LICENSE](LICENSE) und [NOTICE.md](NOTICE.md) (enthält die Lizenz von Moblin, dessen DJI-Protokoll hier
nachgebaut wurde). **Ausnahme:** Der Ordner `srtla/` (Patch auf BELABOX/srtla und der damit gebaute Sender) steht unter
AGPL-3.0, wie das Original.
