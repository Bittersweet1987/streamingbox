# Änderungen

## 0.9.57 (Beta)
Mehrere Meldungen aus den GitHub-Issues #4 bis #6 und eine Anzeigekorrektur im Status.
- Behoben (Issue #5): **Die Zwischenüberschrift „DJI-Kameras (Bluetooth)“ stand über der Hauptverbindung, die für alle RTMP-Kameras gilt** (Handy, Drohne, DJI-Kamera und so weiter), und ließ sie nach einer DJI-Einstellung aussehen. Im Menü „Kameras“ gibt es jetzt einen eigenen Abschnitt **„Hauptverbindung“** (Hinweistext, Auswahl und Adresszeile); darunter steht „DJI-Kameras (Bluetooth)“ nur noch mit dem, was wirklich zu den DJI-Kameras gehört (Adapter-Probleme, Kameras, Suche). Der Hinweis oben verweist jetzt auf „Hauptverbindung“.
- Geändert (Issue #4): **Beim Anlegen einer Kamera gibt es keine Auswahl „Weitere / Hauptbild / Bild-in-Bild“ mehr.** Welche Kamera Hauptbild oder Bild-in-Bild ist, wählt man im „Bildaufbau“; die Auswahl beim Anlegen passte dazu nicht mehr.
- Geändert (Issue #6): **Bluetooth-Sticks stehen im Menü „Netze zum Senden und WLAN“ als eigener Abschnitt „Bluetooth (für DJI-Kameras)“ nach der WLAN-Verbindung.** Jeder laufende Adapter ist mit Name, Hersteller und USB-Kennung gelistet („ASUS USB-BT500 (Realtek, 0b05:190e) · bereit“). Ein Stick, der steckt, aus dem der Kernel aber keinen Adapter macht (Treiber fehlt oder wird nicht unterstützt), steht dort ebenfalls, rot, mit dem Hinweis, was zu tun ist; so sieht man, dass er noch nicht kompatibel ist. Die Meldung „Bluetooth-Adapter: … Treiber für … ist eingerichtet“ in der DJI-Karte entfällt, wenn alles läuft; die DJI-Karte zeigt nur noch Probleme.
- Geändert (Issue #6): **Die Namen der WLAN-Sticks fehlten** in „WLAN-Verbindung“. Jetzt steht hinter dem Namen der Netzwerkkarte der Name des Sticks, wie er sich meldet (zum Beispiel „wlan0 · 802.11ac NIC (Realtek, 0bda:c811)“), bei einer eingebauten Karte der Treiber; auch in der Auswahl „WLAN-Karte“.
- Behoben: **Die Zahlen bei „Upload (Netze zum Senden)“ im Status wackelten**, weil sich ihre Breite beim Ändern der Werte verschob. Die Zeilen (Netz, senden, empfangen, Summe) stehen jetzt in einem gemeinsamen Raster mit festen Spalten und gleich breiten Ziffern, wie die Akku-Spalte bei den Kameras. Die Summenzeile heißt „Summe (2)“ (Zahl der Netze, Hinweistext „Summe über 2 Netze“), damit sie nicht mehr umbricht.
- Hinweis zum Einspielen: Diese Version ändert `dji.py` (Namen der Sticks); der Bluetooth-Dienst wird dabei einmal neu gestartet, die Kameras verbinden sich danach wieder von selbst. Das Update läuft nicht während einer Sendung.
- Tests: Namen aus /sys für Bluetooth-Adapter und WLAN-Karten (mit den Werten der echten Box), Reihenfolge der Abschnitte, Bluetooth-Abschnitt und entfallene Meldung, keine Rollenauswahl mehr, Raster der Upload-Zeilen.

## 0.9.56 (Beta)
- Geändert: **Akku-Spalte in "Status, Kameras" wackelt nicht mehr und kollidiert nicht mehr mit den Zahlen.** Akku und Datenrate haben jetzt feste Spaltenbreiten (Ziffern gleich breit), sodass sich nichts verschiebt, wenn das Ladesymbol erscheint, der Akkustand von 99 auf 100 % springt oder sich die Datenrate ändert. Der Akku sitzt damit ein Stück weiter links, mit Abstand zur Datenrate.
- Geändert: **Die Überschrift "Akku" steht in derselben Zeile wie "Kameras"**, in derselben blauen Schrift. Ohne Akkustand (zum Beispiel nur Handys) gibt es weiter nur "Kameras" und keine Akku-Spalte.
- Tests angepasst (feste Spaltenbreiten, gemeinsame Überschriftzeile).

## 0.9.55 (Beta)
- **Korrektur zu 0.9.54: Das Ladesymbol der Action 4 beruhte auf einer falschen Deutung.** In 0.9.54 galt Byte 2 der Statusnachricht als "Kabel steckt". Das war falsch: Die Bytes 1 bis 2 sind die **Akkuspannung** in mV (4400 am Kabel bei vollem Akku, etwa 4250 bis 4300 im Batteriebetrieb); Byte 2 wechselte nur, weil die Spannung die Stufengrenze 4352 mV überquerte. Bei niedrigerem Akku (unter 4096 mV) hätte 0.9.54 fälschlich ein Ladesymbol gezeigt.
  **Jetzt gilt der Strom aus dem Akku (Bytes 5 bis 8, vorzeichenbehaftet, in mA):** Aus dem Akku läuft die streamende Action 4 mit etwa -650 bis -1100 mA; steckt das Kabel, ist der Strom 0 bis -5 mA (die Kamera wird vom Kabel versorgt, der Akku wird nicht entladen), geladen würde positiv. Das Symbol 🔌 erscheint, wenn der Strom über -100 mA liegt. Das passt zu allen Wechseln am 4. Oktober (Kabel abgezogen und wieder angesteckt, mehrfach, mit Spannung und Strom gegen die Uhrzeit geprüft).
  Auch bei vollem oder fast vollem Akku und gestecktem Kabel zeigt es das Symbol: Die Kamera streamt dann aus dem Kabel und lädt nicht nach. Der Hinweis nennt es "am Ladekabel (lädt oder wird versorgt)". Weiter nur für die Action 4; bei anderen Modellen bleibt es unbekannt.
- Tests: Spannungs- und Stromwerte wie an der echten Kamera (Kabel steckt, abgezogen, angesteckt bei 94 %, Laden positiv), und ein Test gegen den Fehler von 0.9.54 (keine Spannung allein, auch nicht 3900 oder 4400 mV, ergibt "am Kabel").

## 0.9.54 (Beta)
- Neu (Issue #3, Rest): **Ladesymbol 🔌 für die Osmo Action 4 (in dieser Version mit falscher Erkennung, korrigiert in 0.9.55).** Die Statusnachricht der Kamera zeigt es: Byte 2 steht auf 0x11, solange das Ladekabel steckt, und auf 0x10, sobald es abgezogen ist. Das wurde auf einer echten Box gesehen: Beim Abziehen sprang das Byte, beim Wackeln am Kabel wechselte es mehrfach hin und her, und der Akkustand fiel danach von 100 auf 99 %. **Nur für die Action 4**, an der es beobachtet wurde; bei allen anderen Modellen bleibt "lädt" unbekannt (kein Symbol), nie geraten.
  Wer eine andere Kamera hat und das Laden zeigen möchte: Ladekabel abziehen und wieder anstecken und die Zeilen "Statusnachricht" aus dem Journal des DJI-Dienstes schicken.
- Geändert: **Akku im Status als eigene Spalte mit Überschrift.** In "Status, Kameras" steht der Akku jetzt in einer Spalte mit der kleinen Überschrift "Akku", mittig; Überschrift und Werte liegen übereinander. Die Spaltenbreiten richten sich nach dem Inhalt (ein gemeinsames Raster für Name, Akku und Datenrate): Ein langer Kameraname wird mit Auslassungspunkten gekürzt, statt Akku und Datenrate zu verschieben. Hat keine Kamera einen Akkustand (zum Beispiel nur Handys), gibt es die Spalte nicht.
- Geändert: **Das Journal des DJI-Dienstes ist übersichtlicher.** Statusnachrichten stehen nur noch drin, wenn sich ein Byte ändert, das nicht ständig schwankt (die Kamera schickt alle paar Sekunden eine neue Nachricht, in der einzelne Bytes dauernd wechseln). Vorher waren es einige hundert Zeilen pro halbe Stunde.
- Geändert: **Klarere Meldung nach dem Streamstart.** Meldet Bluetooth "getrennt", kommen aber weiter Statusnachrichten (Akku) von der Kamera, steht dort: "Der Stream läuft; die Steuerung per Bluetooth ist beendet, Statusmeldungen (Akku) kommen weiter" (so ist es bei der Action 4: BlueZ stellt die Verbindung selbst wieder her, bleak bemerkt das nicht). Der Akkustand bleibt dabei aktuell.
- Hinweis: Diese Version ändert den DJI-Dienst: Beim Einspielen werden die Kameras einmal getrennt und bauen ihren Stream neu auf. Nicht während einer Übertragung einspielen.
- Tests: Laden bei der Action 4 (Kabel an, ab, wieder an), unbekannt bei anderen Modellen, Journal ohne Dauerschwankungen (73 Tests für den DJI-Dienst).

## 0.9.53 (Beta)
Drei Wünsche aus den Issues auf GitHub (#1, #2, #3) und der Live-Knopf in der Kopfleiste.
- Neu (#1): **Updates werden nach dem Start der Box gesucht und angezeigt.** Die Box sucht nach dem Start von selbst (die GitHub-Abfrage nach 30 Sekunden, die Systemupdates nach 2 Minuten statt vorher 10) und **immer einmal nach jedem Start**, auch wenn die letzte Suche noch keine 6 Stunden her war. Öffnet jemand die Oberfläche (Anmeldung), wird gleich gesucht, ohne die zwei Minuten abzuwarten (einmal je Start, still).
  Fehlversuche (Router und Mobilfunk sind nach dem Start oft noch nicht da) werden in der ersten halben Stunde nach dem Start schneller wiederholt: die GitHub-Abfrage nach 3 Minuten, die Systemsuche nach 5 Minuten (später wie bisher 30 Minuten beziehungsweise 1 Stunde). Das Ergebnis zeigen die gelben Punkte "Oberfläche" und "System" in der Kopfleiste. Wie bisher nie während einer Übertragung (dann kein Mobilfunkverkehr für die Paketlisten); läuft nach dem Start sofort eine Sendung, wird erst danach gesucht.
- Geändert (#2): **Funnel ohne Zeitgrenze, ohne verwirrenden Hinweistext.** Die öffentliche Freigabe bleibt jetzt an, bis sie mit "Öffentliche Freigabe beenden" abgeschaltet wird, und besteht auch nach einem Neustart der Box fort (Tailscale behält sie). Die Grenze von 8 Stunden und der Zeitgeber, der sie beendete, sind weg (`pipbox-funnel-guard`; beim Einspielen wird er abgeschaltet und entfernt). Die rote Warnung und die Rückfrage vor dem Einschalten sagen das ausdrücklich.
  Die Zeile "Weitere Geräte in Ihrem Netz: …" (mit vielen "funnel-ingress-node") ist ganz entfernt, auch im Server.
- Neu (#3): **Akkustand der DJI-Kameras im Status unter "Kameras".** Neben dem Namen steht der Akku in Prozent: über 75 % grün, unter 25 % rot, sonst die Standardfarbe. Ein Steckersymbol zeigt, wenn die Kamera lädt (siehe unten). Auch in der DJI-Karte gelten die Farben.
  Verliert die Box die Bluetooth-Verbindung zur Kamera (die Action 4 trennt sie gleich nach dem Streamstart), bleibt der zuletzt gemeldete Wert stehen, wird blasser und zeigt im Hinweis, wie alt er ist ("Stand vor 25 Min.").
  **Laden:** Ob die Statusnachricht der Kamera "lädt" enthält, ist nicht bekannt, deshalb wird das Steckersymbol erst gezeigt, wenn der Dienst es sicher weiß; es wird nie geraten. Zur Klärung schreibt der Dienst jede geänderte Statusnachricht (außer dem Akkustand selbst) als Hexwert ins Journal ("Statusnachricht …"): Beim Anstecken oder Abziehen des Ladekabels sieht man, welches Byte sich ändert.
- Neu: **Live-Knopf in der Kopfleiste (immer sichtbar).** Er zeigt den Zustand der Sendung: "Live gehen" (nicht live), "● LIVE" (rot, es wird gesendet), "Verbinde …" und "Wird beendet…" (orange, gesperrt). Ein Klick startet die Sendung (mit derselben Rückfrage wie in der Live-Karte, mit Ziel) oder beendet sie (mit Rückfrage "Die Sendung jetzt beenden?"). Kann die Sendung nicht starten, ist der Knopf gesperrt und der Hinweis nennt die Gründe (zum Beispiel "Kein SRTLA-Server ausgewählt").
  Die graue Anzeige daneben hieß bisher "live" und meinte nur die Verbindung der Seite zur Box; das wurde mit der Sendung verwechselt und heißt jetzt "verbunden".
- Geändert: **Die Update-Hinweise stehen als Knöpfe in der Kopfleiste** ("Update Oberfläche" und "Update System", gelb umrandet, nur sichtbar, wenn es etwas gibt; ein Klick öffnet die zugehörige Karte). Vorher waren es kleine Punkte neben dem Titel. Auf dem Handy steht nur "Oberfläche" und "System".
- Geändert: **Einheitliche Kopfleiste.** Alle Elemente oben (Update-Hinweise, Zuklappen, Streammodus, Live-Knopf, Anzeige der Verbindung) haben jetzt dieselbe Form (abgerundetes Rechteck wie die übrigen Knöpfe), Höhe (30 Pixel) und Schrift; vorher mischten sich Rechtecke und Pillen.
- Geändert: **"Abmelden" steht nicht mehr in der Kopfleiste**, sondern in der Karte "Box ausschalten und abmelden" (neben "Herunterfahren" und "Neu starten"). Die Funktion bleibt, weil eine mit "Angemeldet bleiben" gemerkte Anmeldung 30 Tage gilt und man sich auf einem fremden Gerät abmelden können muss.
- Hinweis: Diese Version ändert den DJI-Dienst (Akkustand): Beim Einspielen werden die Kameras einmal getrennt und bauen ihren Stream neu auf. Nicht während einer Übertragung einspielen.
- Tests: Kopfleiste (Live-Knopf nutzt den bestehenden Weg, Abmelden in der Ausschalten-Karte), Suche nach dem Start (Wartezeit, immer einmal je Start, schnellere Wiederholung, Anmeldung), Funnel (keine Zeitgrenze, alter Zeitgeber wird entfernt und tut nichts, keine Geräteliste), Akkustand (bleibt nach Bluetooth-Verlust mit Alter, aus dem Zustand des Dienstes in die Kameraliste, Farbgrenzen in der Oberfläche geprüft).

## 0.9.52 (Beta)
- Neu: **Die Adresse jeder Kamera in der Liste "RTMP-Kameras" gilt für ihre eigene Verbindung**, nicht nur für die Hauptverbindung. Eine Kamera oder ein Handy, das über eine andere Verbindung der Box sendet (zum Beispiel einen zweiten Router, um die Last zu verteilen), zeigt die Adresse der Box in diesem Netz.
  - **Andere Kameras (zum Beispiel ein Handy):** In ihrer Zeile gibt es die Auswahl "Verbindung" mit allen Verbindungen der Box (mit Adresse, feste Zweitadressen wie bei der Hauptverbindung). Standard ist die Hauptverbindung. Ist die gewählte Verbindung gerade nicht da, gilt bis zu ihrer Rückkehr die Hauptverbindung; die Wahl bleibt gespeichert.
  - **DJI-Kameras:** Die Zeile zeigt die Verbindung aus der DJI-Karte der Kamera ("in der DJI-Karte gewählt"), ohne etwas doppelt einzustellen; hat die Kamera keine gewählt, steht dort die Hauptverbindung. Der Server verweigert, die Verbindung einer DJI-Kamera in der Liste zu setzen.
  - Die Auswahl "Netzwerk der Kamera" im Bereich "DJI-Kameras" heißt jetzt "Hauptverbindung" und gilt für alle Kameras ohne eigene Verbindung.
- Tests: Adresse je Verbindung (Standard, eigene Wahl, feste Zweitadresse, zurück zur Hauptverbindung, unbekannte und verschwundene Verbindung), DJI-Kamera nach ihrer Karte ("Manuell", nichts gewählt, Verbindung gerade nicht da), andere Kameras bleiben unberührt.
- Hinweis: Der DJI-Dienst ändert sich in dieser Version nicht; beim Einspielen werden die Kameras nicht neu verbunden.

## 0.9.51 (Beta)
Korrekturen zu 0.9.50 nach dem ersten Test mit echten Kameras auf der Box.
- Behoben: **"device not found" beim Verbinden** (Action 5 Pro, Action 6). BlueZ vergisst eine Kamera, sobald die Suche endet; die Vorlage beendete die Suche vor dem Verbinden. Jetzt läuft die Suche, bis die Verbindung steht, und endet erst dann. Dasselbe hatte schon die frühere Version gemessen.
- Behoben: **Der Stream wurde bei Bluetooth-Verlust neu gestartet.** Die Action 4 trennt Bluetooth gleich nach dem Streamstart (die frühere Version hat das nie geprüft, nur ob der Stream ankommt). Die Vorlage beendete dann die Sitzung und baute alles neu auf, mit "Stopp, Vorbereiten, Start": das Bild flackerte alle 25 Sekunden.
  Jetzt läuft ein ankommender Stream unverändert weiter; die Karte zeigt "Der Stream läuft, die Bluetooth-Verbindung ist getrennt". Endet der Stream, beginnt die Sitzung von vorn. Ohne Bluetooth kann "Trennen" den Stream nicht beenden (kein Stopp-Befehl möglich): Er läuft, bis die Kamera ausgeschaltet wird.
- Behoben: **Dauerschleife nach dem Bluetooth-Verlust** (Fehler beim ersten Entwurf dieser Korrektur): Der Dienst belegte einen ganzen Kern und blockierte sich selbst, Verbindungsaufbauten dauerten 11 bis 19 Sekunden statt etwa 3 Sekunden. Ein Test weist die Schleife nach.
- Geändert: **Wiederholung gestaffelt** (8, 8, 15, dann 30 Sekunden statt immer 8): Ständiges Suchen stört die schon stehenden Verbindungen.
- Neu: **Dieselbe Verbindung, dasselbe WLAN.** Wählt eine weitere Kamera eine Verbindung, an der schon eine andere Kamera ihr WLAN eingegeben hat, werden Name und Passwort angeboten (eingetragen und in ihrer Liste). Eine Kamera mit eigenem WLAN behält es; die Kameras bleiben sonst unabhängig voneinander ("Manuell" und andere Verbindungen bekommen nichts angeboten).
  Gemerkt wird, sobald Name und Passwort eingegeben sind und sobald die Kamera das WLAN angenommen hat.
- Neu: **Bildaufbau zeigt so viele Fenster wie Kameras da sind** (Hauptbild und höchstens drei kleine Bilder): Bei zwei Kameras zwei Fenster, bei drei Kameras drei, ab vier alle vier. Ein Fenster mit schon gewählter Kamera bleibt sichtbar.
- Geändert: Verständliche Meldungen statt "TimeoutError:" bei Zeitüberschreitung, "nicht mehr sichtbar", Abbruch beim Einrichten; die Warnung von `bleak` 3.x im Journal ist weg (der Adapter wird je nach `bleak`-Version angegeben); das Journal nennt die Dauer der Verbindungsschritte ("Zeiten: Verbinden und Dienste …").
- Bestätigt auf der Box: Eine Action 4 verbindet in etwa 3 Sekunden bis "Koppeln", das WLAN wird übergeben, der Stream läuft. **Noch nicht bestätigt:** Action 5 Pro und Action 6 mit der Korrektur der Suche. Tests: 68 für den DJI-Dienst (alle grün).

## 0.9.50 (Beta)
- Neu: **DJI-Anbindung neu aufgebaut: Verbindung je Kamera aus den vorhandenen Verbindungen der Box wählbar, schnellere Wiederholung, bessere Statusmeldungen.**
  Der Bluetooth-Dienst (`pipbox-dji`, `dji_daemon.py`) folgt jetzt dem Ablauf des DJI-Dienstes von Bittersweet1987 (Bibliothek `bleak` statt direkter BlueZ-Aufrufe), angepasst an dieses Projekt (siehe NOTICE.md).
  - **Verbindung je Kamera:** Jede DJI-Karte hat eine Auswahl "Verbindung der Kamera" mit allen aktiven Verbindungen der Box. WLAN-Hotspot und WLAN-Netze: Name und Passwort holt der Dienst selbst aus NetworkManager. Alle anderen Verbindungen (Ethernet, USB-Router, Modem): WLAN-Name und Passwort der Kamera einmal eintragen und speichern (mehrere gespeicherte Netze je Kamera wählbar); die Adresse der Box kommt aus der gewählten Verbindung. "Manuell" für alles von Hand.
    Solange die Kamera verbunden ist oder sendet, ist ihre Verbindung gesperrt. Kann der Dienst ein WLAN-Passwort nicht aus NetworkManager lesen, sagt er das und verlangt die Eingabe von Hand. Passwörter gehen nie an den Browser.
  - **Statusmeldungen:** Zustand je Kamera mit Schritt (zum Beispiel "Koppelt (3/6)"), Detailtext, Akkustand und, nach einem Fehler, dem Grund samt Sekunden bis zum nächsten Versuch. Während eine Kamera verbindet oder gesucht wird, fragt die Seite jede Sekunde nach (sonst alle 2 Sekunden bei offener Karte).
  - **Schneller:** Fehlversuche werden alle 8 Sekunden wiederholt (vorher 10, 20, 40, 60 Sekunden), die Suche beim Hinzufügen dauert 8 statt 30 Sekunden, es gibt keine festen Pausen mehr zwischen den Schritten.
  - **Eine Kamera nach der anderen verbindet** (danach laufen alle parallel): Gleichzeitiges Verbinden bricht auf dem Funkchip ab (`le-connection-abort-by-local`, auf der Box mit vier Kameras gemessen). Das Dokument zur Vorlage hatte diese Sperre nicht, sie bleibt hier erhalten.
  - **Bleibt wie bisher:** eigener Dienst getrennt von der Oberfläche (eine Kamera streamt nur, solange die Box die Bluetooth-Verbindung hält), Token für die lokale Schnittstelle (nur der Benutzer `pipbox` kann es lesen), Benutzer `pipbox` mit den Schutzeinstellungen des Dienstes, Rollenprofile (erste Kamera Hauptbild: 1080p, 30, 8 Mbit/s; weitere Bild-in-Bild: 720p, 30, 4 Mbit/s), Schlüssel `dji-xxxxxx` (kein Zusammenstoß mit anderen Kameras), Sperre für Action 2, PIN `mbln` (schon gekoppelte Kameras fragen nicht neu), Nachrichtenkodierung für Action 6 und Pocket 4 byte-genau wie bei Moblin.
  - **Übernahme:** Beim ersten Start des neuen Dienstes werden die Kameras der früheren Version samt Namen, Einstellungen, gespeichertem WLAN, gewähltem Kameranetz und "soll laufen" übernommen (`dji-cameras.json`, nur für den Dienst lesbar). Die alten Dateien bleiben liegen.
  - **Installation:** `install.sh` richtet `bleak` mit `pip` ein (für Ubuntu 22.04 gibt es kein Paket; braucht Internet). Gelingt das nicht, bricht die Installation ab, bevor etwas verändert wurde, und der Update-Helfer stellt die vorige Version wieder her.
    Beim Einspielen werden die Kameras einmal getrennt (der Dienst startet neu) und verbinden sich danach von selbst wieder: **nicht während einer Übertragung einspielen.**
  - **Oberfläche:** Neue Karte je Kamera (zuklappbar, der Zustand wird gemerkt), Suche mit "Hinzufügen". Die Auswahl der Rolle (Hauptbild, Bild-in-Bild, Weitere) in der Liste "RTMP-Kameras" ist entfallen: Sie steht schon im Bereich "Bildaufbau". Die Rolle beim Anlegen einer Kamera von Hand bleibt.
    Die Schnittstelle der Seite zum Server heißt jetzt `/api/dji/cmd` (nur die elf bekannten Befehle und Felder gehen durch); die alten Wege (`/api/dji/start`, `stop`, `wifi`, `settings`) sind weg.
  - **Nicht getestet** (nur mit echter Kamera und Box möglich): bleak und BlueZ auf der Box mit dem eingeschränkten Dienstbenutzer, das Lesen der WLAN-Zugangsdaten aus NetworkManager als dieser Benutzer, das Verhalten der echten Kameras mit diesem Ablauf, die Zeitersparnis. Getestet ist der gesamte Ablauf gegen eine nachgestellte Kamera (56 Tests) und die Kodierung Byte für Byte gegen die bisher an echten Kameras bewährte.

## 0.9.49 (Beta)
- Behoben: **Fehlmeldung "Der Stick … wurde erkannt, aber der Kernel hat keinen Bluetooth-Adapter daraus gemacht" bei einem Stick, der einwandfrei läuft** (zum Beispiel ASUS USB-BT500 mit laufendem Adapter). Ursache: Die Zuordnung zwischen Stick und Adapter ging über die Kennung, die BlueZ für den Adapter meldet. Das ist aber in der Regel nur die BlueZ-Standardkennung (`usb:v1D6Bp0246`, Linux Foundation), nie die des Sticks; so galt jeder Stick als "nicht erkannt".
  Jetzt wird die USB-Kennung des Sticks aus `/sys/class/bluetooth` gelesen (vom Adapter aufwärts bis zum USB-Gerät); ein eingebauter Adapter hat keins und gilt weiter als "eingebaut". Die Standardkennung wird nie mehr für den Stick gehalten. Die Warnung für einen Stick ohne Adapter (zum Beispiel UGREEN mit BARROT-Chip) bleibt.
  Der Fehler war seit 0.9.43 drin: Die Tests hatten die Kennung des Sticks in der BlueZ-Meldung angenommen. Sie bilden jetzt die echten Werte nach (BlueZ-Standardkennung, Adapter unter dem USB-Gerät). Gegen die echte Box geprüft (nur gelesen): ASUS-Stick ergibt keine Warnung.
- Tests: Kennung aus /sys, eingebauter Adapter, nicht vorhandener Adapter, Stick mit BlueZ-Standardkennung ohne Warnung, Stick ohne Adapter weiter mit Hinweis.

## 0.9.48 (Beta)
- Behoben: **Die gelben Punkte in der Kopfleiste ("Oberfläche" und "System") blieben dauerhaft sichtbar**, auch wenn es keine Updates gab. Ursache: Die Darstellung der Punkte (`display`) überstimmte das Attribut `hidden`, mit dem sie ausgeblendet werden. Jetzt gilt für die ganze Seite eine feste Regel `[hidden]{display:none}`.
  Dadurch verschwindet auch die Zeile "Adresse im privaten Netz" mit dem leeren Link in der Karte Fernzugriff, solange Tailscale keine Adresse bereitstellt (sie wurde bisher ebenfalls immer gezeigt).
- Geändert: **Beide Abfragen laufen alle 6 Stunden von selbst.** Die Suche nach Systemupdates (bisher einmal am Tag) und die Abfrage der neuen Oberflächen-Version bei GitHub: Beide Punkte stimmen so auch, wenn gerade niemand die Seite offen hat. Der Server fragt jetzt auch ohne geöffnete Seite bei GitHub nach.
  Ein Fehlversuch (kein Internet) wird früher wiederholt: die Systemsuche nach 1 Stunde, die GitHub-Abfrage nach 30 Minuten (vorher erst nach 6 Stunden). **Nie während einer Übertragung** (kein Mobilfunkverkehr dafür); nach dem Start der Box frühestens nach 10 Minuten (Systemsuche) beziehungsweise 90 Sekunden (GitHub). Die Suche lädt nur Paketlisten, installiert nichts.
- Test: Die Seite muss die feste Regel enthalten, die Punkte sind im Quelltext ausgeblendet. Geprüft im Browser mit der tatsächlichen Sichtbarkeit (nicht nur dem Attribut): ohne Updates beide weg, mit Updates sichtbar, bei Fehler weg.

## 0.9.47 (Beta)
- Neu: **Zweiter gelber Punkt in der Kopfleiste für die BELABOX-Systemupdates** (Ubuntu- und BELABOX-Pakete). Die Kopfleiste zeigt jetzt bis zu zwei Punkte hinter dem Titel: "Oberfläche" (neue Version von IRL4YOU BOX, seit 0.9.46) und "System" (Systemupdates liegen bereit).
  Beide sind **nur sichtbar, wenn etwas ansteht**, sonst aus. Beim Darüberfahren steht, was ansteht (bei den Systemupdates Zahl der Pakete, Stand der Suche, ob danach ein Neustart nötig ist, und dass das Einspielen während einer Übertragung nicht geht); ein Klick öffnet die zugehörige Karte. Auf schmalen Bildschirmen steht nur der Punkt, ohne Text.
  Der Punkt "System" erscheint nicht, solange ein Update läuft oder die letzte Aktion fehlgeschlagen ist.
- Neu: **Tägliche stille Suche nach Systemupdates.** Bisher suchte die Box nur auf Knopfdruck, der Punkt hätte also nur das Ergebnis der letzten Suche gezeigt. Jetzt fordert die Oberfläche höchstens **einmal am Tag** eine Suche an (frühestens 10 Minuten nach dem Start der Box, nach einer Suche ohne Ergebnis erst nach 6 Stunden wieder,
  **nie während einer Übertragung** und nie, während schon etwas läuft). Der Root-Helfer bekommt dafür das Stichwort `autocheck` (nur der Server legt es ab, über die Schnittstelle ist es nicht erreichbar): Er sucht wie "Nach Updates suchen", zeigt dabei aber nicht "läuft" und meldet keinen Fehler,
  wenn die Paketliste nicht erreichbar ist (kein Internet): Der bisherige Zustand der Karte bleibt dann unverändert. Es werden nur Paketlisten geladen, nichts installiert.
- Neu: **"Angemeldet bleiben" auf der Anmeldeseite**, wie in der BELABOX-Oberfläche. Mit Haken gilt die Anmeldung 30 Tage und **übersteht Updates und Neustarts der Oberfläche**: Man muss sich danach nicht neu anmelden. Ohne Haken bleibt es bei 12 Stunden und die Sitzung endet mit einem Neustart.
  Auf der Platte liegt nur ein SHA-256 des Sitzungsschlüssels (`sessions.json`, nur für den Dienst lesbar), nie der Schlüssel selbst. Ändert sich das Passwort (BELABOX-Passwort oder eigenes), enden alle gemerkten Sitzungen. "Abmelden" beendet auch eine gemerkte Sitzung.
- Neu: **Feste Kopfleiste.** Die Leiste mit Titel, Update-Punkten, Streammodus und Abmelden bleibt beim Scrollen oben stehen und verdeckt keine Inhalte (ein Sprung zu einer Karte, etwa über den gelben Punkt, landet unterhalb der Leiste). Auf dem Handy ist die Leiste zweizeilig und braucht etwa 84 px.
- Geändert: **Vorschau (`--demo`) ohne Setup-Code.** Das Demo-Passwort ist von Anfang an gesetzt und wird auf der Anmeldeseite vorausgefüllt, es genügt ein Klick auf "Anmelden". Das gilt nur mit `--demo` auf dem eigenen Rechner (Adresse 127.0.0.1) und nur im Speicher; auf einer BELABOX gilt unverändert das BELABOX-Passwort.
- Tests: Angemeldet bleiben (übersteht Neustart, normale Sitzung nicht, nur Hash und Rechte 0600 auf der Platte, Abmelden, Ablauf nach 30 Tagen, Passwortwechsel). Demo-Passwort (kein Setup-Code, nichts auf der Platte, ersetzt nie das BELABOX-Passwort, ohne `--demo` keins). Wann die Suche fällig ist (nie geprüft, nach einem Tag, kürzlich geprüft, Übertragung, Anforderung liegt noch, kurz nach dem Start, Pause nach Fehlversuch), Anforderungsdatei mit dem festen Stichwort, Helfer (Fehler lässt den alten Zustand, nichts während der Übertragung, Erfolg wie bei "Suchen").

## 0.9.46 (Beta)
- Neu: **Gelber Punkt in der Kopfleiste, wenn es ein Update für die Oberfläche gibt.** Er steht hinter dem Titel "IRL4YOU BOX" und erscheint, sobald auf GitHub eine neuere Version liegt (auch bei zugeklappter Karte "Software-Update"). Beim Darüberfahren steht die neue und die jetzige Version
  (und, dass das Einspielen während einer Übertragung nicht geht, falls gesendet wird); ein Klick öffnet die Karte "Software-Update". Der Punkt verschwindet, wenn die Box aktuell ist. Die Abfrage bei GitHub bleibt wie bisher: höchstens alle 6 Stunden und nie während einer Übertragung.
  Er meint nur das Update dieser Oberfläche, nicht die System-Updates der BELABOX.

## 0.9.45 (Beta)
- Neu: **Treiber für Realtek-Bluetooth-Sticks, die der Kernel 5.10 nicht kennt (TP-Link UB500 u. a.), richtet die Box beim Einstecken selbst ein.** Ohne ihn starten diese Sticks ohne Firmware, finden keine Kameras und wirken tot. Beim Einstecken (udev-Regel) oder beim Start (Zeitgeber nach 3 Minuten, danach alle 15 Minuten)
  prüft der neue Root-Helfer `pipbox-btdriver.py`, ob ein solcher Stick steckt; wenn ja, baut er aus den mitgelieferten, **unveränderten Kernelquellen** (`bluetooth-src/`, v5.10.160, GPL-2.0, SHA-256 geprüft) das Modul `btusb` neu, mit zusätzlichen Zeilen `BTUSB_REALTEK` für `2357:0604` (TP-Link UB500), `2550:8761`, `2c4e:0115` (Mercusys MA530), `0bda:8771`, `0bda:a725`, `2b89:8761` und
  `0b05:190e` (ASUS USB-BT500; löst nichts aus, bekommt aber die Firmware), spielt es nach `/lib/modules/<Kernel>/updates/btusb.ko` ein und lädt Bluetooth neu. Probebau auf der Orange Pi 5 Plus: 7 Sekunden, `vermagic`, Abhängigkeiten und Alias-Tabelle identisch mit dem Standardmodul.
- Sicherheitsnetz: nur für den Kernel 5.10.160; nichts aus dem Internet; **nie während einer Übertragung** (das Neuladen trennt Bluetooth, der Helfer wartet); nach dem Laden müssen ein Adapter da sein, das neue Modul wirklich laufen und die Firmware ohne Fehler laden, sonst wird alles zurückgerollt und für diese Kombination nicht noch einmal versucht (der Standardtreiber läuft weiter);
  das Standardmodul des Kernels wird nie überschrieben; `install.sh uninstall` entfernt das eingespielte Modul. Ohne passenden Stick tut der Helfer nichts (auf Boxen mit dem ASUS-Stick ändert sich nichts).
- Neu: Die Karte "DJI-Kameras (Bluetooth)" zeigt den Zustand des Treibers ("wird gebaut", "wartet auf das Ende der Übertragung", "eingerichtet", Fehler mit Grund).
- Weiter nicht unterstützt: UGREEN Bluetooth 5.4 und 6.0 (BARROT-Chip): Dafür müsste der Kern des Kernels (`hci_core`) geändert werden, das lässt sich so nicht nachladen; die Oberfläche sagt es im Klartext.
- Geändert: Lizenzhinweis für die mitgelieferten Kernelquellen in `NOTICE.md` und `bluetooth-src/README.md`.
- Tests: Quellen und Patch (nur zusätzliche Zeilen, idempotent), Erkennung, kompletter Ablauf mit nachgestellten Bausteinen (Erfolg, kein Adapter, Firmwarefehler, altes Modul läuft noch, Bau schlägt fehl, keine Schleife, Warten während der Übertragung), Bau, Rückbau, Verdrahtung (udev, Einheiten, Installer).
  **Noch nicht mit einem echten TP-Link-Stick ausprobiert**; der Nutzer mit den Sticks testet.

## 0.9.44 (Beta)
- Neu: **Knopf "Öffentlich im Internet freigeben (Funnel)" in der Karte "Fernzugriff"** (sichtbar, wenn die Box mit Tailscale verbunden ist). Damit erreicht auch jemand **ohne Tailscale-App** die Oberfläche. Das ist ausdrücklich eine **Ausnahme von der bisherigen Regel "nie öffentlich"** und deshalb vorsichtig gebaut:
  Aus ist der Standard, ein Klick braucht eine ausführliche Warnung und die Bestätigung (der Server verlangt zusätzlich das Merkmal `public`); solange Funnel an ist, steht eine rote Warnung mit der Uhrzeit des Endes in der Karte; **nach 8 Stunden beendet ein Zeitgeber die Freigabe von selbst**
  (`pipbox-funnel-guard.timer`, alle 5 Minuten und 2 Minuten nach dem Start; die Zeitgrenze liegt im RAM, nach einem Neustart gilt eine noch aktive Freigabe als abgelaufen und wird beendet); "Öffentliche Freigabe beenden" schaltet sofort ab, die Freigabe im privaten Netz bleibt.
  Der Root-Helfer bekommt die Stichworte `funnel_on` und `funnel_off` (weiter nur feste Stichworte, keine Eingaben der Oberfläche). Der Wächter ändert nur Funnel-Freigaben für die Oberfläche der Box, keine fremden. Verlangt Tailscale, Funnel für das Netz einmal zu erlauben, zeigt die Karte den Link dazu.
  Beim Beenden setzt Tailscale alle Freigaben zurück; die Box stellt nur die der Oberfläche im privaten Netz wieder her.
- Neu: Hinter dem Tailscale-Proxy (Serve und Funnel) zählt für die Sperre nach falschen Anmeldungen die Adresse des Absenders (letzter Eintrag von `X-Forwarded-For`, nur wenn die Anfrage vom Proxy auf der Box selbst kommt). Vorher hätte ein Angreifer über Funnel alle anderen mit ausgesperrt, weil für den Server alle von `127.0.0.1` kamen.
- Behoben: **Die Karte der System-Updates klappte von selbst zu**, sobald keine Updates (mehr) anstanden, auch beim Laden der Seite. Sie öffnet sich weiter von selbst, wenn etwas ansteht (Updates, Fehler, Neustart nötig), klappt aber nie mehr von selbst zu.
- Doku: `ANLEITUNG-Fernzugriff.md` beschreibt Funnel, seine Risiken und das automatische Ende; README und Sicherheitshinweise angepasst.
- Tests: Helfer (Zeitgrenze, Wächter, fremde Freigaben, Fehlermeldung "Funnel nicht erlaubt"), Server (Bestätigungspflicht, Demo), Absenderadresse hinter dem Proxy, Installer-Einheiten. Mit echtem Tailscale und echtem Funnel **noch nicht ausprobiert**; die Befehle (`tailscale funnel --bg --yes 8780`, `tailscale funnel reset`) stammen aus der Hilfe der installierten Version 1.102.4.

## 0.9.43 (Beta)
- Neu: **Die Oberfläche zeigt, welcher Bluetooth-Adapter für die DJI-Kameras läuft, und erkennt Sticks, aus denen der Kernel keinen Adapter macht.** Unter der Kartenüberschrift "DJI-Kameras (Bluetooth)" steht jetzt der laufende Adapter (USB-Kennung); steckt ein Stick, der keinen
  Adapter ergibt, steht dort der Grund, und die Suche meldet statt "Kein Bluetooth-Adapter gefunden" den Hinweis. Erkannt wird über `/sys` (Schnittstellenklasse Bluetooth oder Hersteller Barrot), nur lesend.
- Bluetooth-Sticks (Ergebnis einer Recherche, noch nicht mit den Sticks selbst geprüft): Der TP-Link UB500 hat denselben Chip wie der getestete ASUS USB-BT500 (Realtek RTL8761B/BUV) und sollte wie dieser über die allgemeine Bluetooth-Klasse laufen. Die UGREEN-Sticks "Bluetooth 5.4" und "6.0" (Modell CM748)
  enthalten einen **BARROT**-Chip (`33fa:0010`/`33fa:0012`), den der Kernel 5.10 der BELABOX nicht unterstützt: Der Chip bleibt bei der Einrichtung hängen, es entsteht kein Adapter. Laut Berichten ist das erst in Linux 6.18 (und den Langzeitzweigen 6.12.58, 6.6.117) behoben. Ein eigener Kernel-Treiber wird bewusst nicht
  mitgeliefert: Ein Fehler darin könnte die ganze Box abstürzen lassen, und der nötige Teil des Kernels (`hci_core`) lässt sich ohne Neubau des Kernels nicht ändern. Die README nennt die geprüften und die ungeeigneten Sticks.
- Tests: Erkennung aus einem nachgestellten `/sys`-Baum, Zuordnung Adapter und Stick, Hinweise.

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
