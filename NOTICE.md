# Hinweise zu Herkunft und Drittprojekten

## Moblin (MIT-Lizenz)

Die DJI-Kopplung (`dji_daemon.py`) folgt dem Ablauf, den das Projekt
[Moblin](https://github.com/eerimoq/moblin) von Erik Moqvist für DJI-Kameras
per Bluetooth umsetzt (Ordner `Moblin/Integrations/Dji/`). Nachrichtenformat,
Konstanten und Ablauf wurden von dort nach Python übertragen. Gegenprobe mit
[dimadesu/dji-remote](https://github.com/dimadesu/dji-remote) (MIT).
Das Protokoll stammt aus dem Nachbau dieser Projekte und ist von DJI nicht
dokumentiert.

### Moblin – MIT License

Copyright (c) 2023 Erik Moqvist

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## DJI-Dienst (MIT-Lizenz)

Sitzungsablauf, Verbindungsliste, Befehle und die Oberfläche der DJI-Kameras (`dji_daemon.py`, Karten in `web/index.html`)
folgen dem DJI-Dienst von Bittersweet1987, hier an IRL4YOU BOX angepasst (Dienstbenutzer mit Schutzeinstellungen, Token der
lokalen Schnittstelle, Rollenprofile, Schlüssel `dji-xxxxxx`, Verbindungssperre, Übernahme der Kameras der früheren Version,
deutsche Texte). Der Urheber hat die Verwendung unter der MIT-Lizenz erlaubt (Lizenzdatei in seinem Projekt vom 4. Oktober 2026).

Die Bibliothek [bleak](https://github.com/hbldh/bleak) (MIT) und ihre Abhängigkeiten werden bei der Installation per `pip`
geladen und nicht mitgeliefert.

### DJI-Dienst – MIT License

Copyright (c) 2026 Bittersweet

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Linux-Kernel (GPL-2.0), Ordner `bluetooth-src/`

Der Ordner `bluetooth-src/` enthält **unveränderte** Quelldateien des Linux-Kernels v5.10.160 (`drivers/bluetooth/btusb.c`, `btintel.h`, `btbcm.h`, `btrtl.h`) unter der
**GPL-2.0** (Lizenztext in `bluetooth-src/COPYING`). Sie gehören nicht zur MIT-Lizenz dieses Projekts. `install/pipbox-btdriver.py` baut daraus auf der Box das Modul `btusb` mit einigen
zusätzlichen USB-Kennungen für Realtek-Sticks; die einzige Änderung am Quelltext sind zusätzliche Tabellenzeilen, die der Helfer beim Bau einfügt. Das so gebaute Modul steht wie der Kernel
unter der GPL-2.0; der Quelltext dazu ist dieser Ordner samt `pipbox-btdriver.py`.

## BELABOX

Unabhängiges Zusatzprojekt, kein Produkt von BELABOX, Radxa oder DJI.
Es ändert keine Dateien von BELABOX. Es nutzt die vorhandene Paketverwaltung
und liest das BELABOX-Passwort nur zur Anmeldeprüfung.

### SRTLA-Sender (AGPL-3.0)

Der Ordner `srtla/` enthält zwei Patches für `srtla_send.c` aus [BELABOX/srtla](https://github.com/BELABOX/srtla)
(Commit 37862da, GNU Affero General Public License v3). Die Patches und das damit gebaute Programm stehen ebenfalls unter
AGPL-3.0; der Quelltext ist der Upstream-Commit plus diese Patches (siehe `srtla/README.md`). Das Original-Programm des
BELABOX-Pakets bleibt unverändert unter `/usr/bin` liegen; der gepatchte Sender wird nach `/usr/local/bin` installiert.

### belacoder-Encoder mit tolerantem Regler und Stall-Wächter (GPL-3.0)

Der Ordner `belacoder/` enthält zwei Patches für `belacoder.c` aus [BELABOX/belacoder](https://github.com/BELABOX/belacoder)
(Commit ccce9ca, GNU General Public License v3). Die Patches und das damit gebaute Programm stehen ebenfalls unter GPL-3.0; der
Quelltext ist der Upstream-Commit plus diese Patches (siehe `belacoder/README.md`). Das Original-Programm des BELABOX-Pakets bleibt
unverändert unter `/usr/bin` liegen; das gepatchte wird nach `/opt/pipbox/bin` installiert und nur von der Sendekette dieses Pakets benutzt.

### WLAN

Die WLAN-Karte der Oberfläche legt über `nmcli` (NetworkManager) Verbindungsprofile an, wenn Sie ein Netz verbinden.
Das Passwort wird nur an `nmcli` übergeben und von diesem Projekt nicht gespeichert.

## Aussehen der kleinen Bilder (Anregung)

Bedienkonzept und Wertebereiche für Beschnitt in Pixeln, Rahmen (Dicke, Farbe, Deckkraft, Eckenrundung), Deckkraft je kleinem Bild und das
Ein-/Ausblenden folgen der Anregung von Bittersweet1987 (Issue und Erweiterung in seinem Projekt, MIT-Lizenz). Die Umsetzung ist eigen: Der
Zeichenkern in `gst/gstpbpip.c` und die Oberfläche wurden für den Baustein dieser Software neu geschrieben, Programmcode wurde nicht übernommen.

## Original-Oberfläche der BELABOX (AGPL-3.0)

IRL4YOU BOX läuft neben der Original-Oberfläche der BELABOX (belaUI) und verändert sie nicht. Aus ihr wird **kein Quelltext** verwendet. Die Karte
"Entwickler" liest nur zwei Einstellungsdateien dieser Oberfläche (`setup.json`: Name des SSH-Benutzers, `config.json`: ob ein SSH-Passwort erzeugt
wurde und welches) und schaltet den SSH-Dienst des Systems (`systemctl start/stop ssh`) mit eigenem Code; das Passwort erzeugt weiterhin die
Original-Oberfläche. Auch das BELABOX-Passwort wird nur zum Anmelden mitbenutzt.
