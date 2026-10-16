# Änderungen

Ältere Versionen (0.1.0 bis 0.9.199) stehen in [CHANGELOG-Archiv.md](CHANGELOG-Archiv.md).

## 1.0.57
- Die Verzögerung der Kameras verschiebt jetzt wirklich Bild und Ton (bis 3000 ms, vorher ohne Wirkung, höchstens 750). Nötig: belacoder wird beim Update neu gebaut.

## 1.0.56
- Behoben: Bei starkem Bitratenabfall konnten Bild und Ton aussetzen. Der Regler reagiert jetzt früher (Meldung von Bittersweet, Issue #1).
- Behoben: Die DJI Osmo Action 6 riss etwa jede Minute ab und war nie als sendend erkannt. Die Kamera hängt dem Streamnamen ein Leerzeichen an, das wird jetzt abgefangen.

## 1.0.55
- **Öffentliche Statistik für OBS und Homepage (Wunsch von Bittersweet, 16. Oktober 2026).** Neuer Abschnitt „Statistik öffentlich“ in der Karte „Fernzugriff“: Ein Schalter gibt **nur die Statistik** (Sendewege, Bitrate, Verlust, Temperaturen, Kameras, Meldungen; auswählbar) über Tailscale Funnel auf Port 8443 frei. Die Oberfläche der Box (Port 443) bleibt davon unberührt und kann privat bleiben. Die Adresse ist fest, auch wenn die Box ihr Netz wechselt (Mobilfunk), und der Betrachter braucht kein Tailscale. Die Seite `statistik.html` (zum Herunterladen mit eingetragener Adresse, oder als Seitenadresse direkt in OBS) verbindet sich per WebSocket und zeigt jede Sekunde den Stand; Farben, Schwellen, Namen der Sendewege und Teile lassen sich in der Adresse einstellen (`?every=2&bg=transparent&show=net,temp&names=eth1:Vodafone`). Zugriff nur mit einem geheimen Schlüssel in der Adresse (erneuerbar), höchstens 3 Betrachter gleichzeitig, Zugriffsbegrenzung je Absender; im Schnappschuss stehen nie IP-/MAC-Adressen, Stream-ID, Serveradresse oder WLAN-Namen. Technik: kleiner eigener Statistik-Server auf `127.0.0.1:8781` im Webserver der Box (`/ws`, `/stats`, `/health`, die Seite); alles andere antwortet mit 404. Last: auf der Box von Bittersweet mit 3 Betrachtern über die öffentliche Adresse zusammen nur wenige Prozent eines Kerns.
- **Freigaben getrennt:** „Freigabe beenden“ und „Öffentliche Freigabe beenden“ entfernen nur noch die eigene Freigabe (je Port, vorher setzte `funnel reset` alle zurück). Die Selbstheilung nach Stromausfall (1.0.54) prüft Oberfläche und Statistik getrennt, mit eigenem Merkzettel.
- Für die Statistik fragt die Box nur ab, ob gesendet wird, ohne die Prozessliste und ohne „welche Kamera ist im Bild“ (Berechnung 15 bis 40 ms statt 60 ms).

## 1.0.54
- **Gerundete Ecken gehen jetzt auch bei „Alle Kameras immer bereit“ (Meldung von MyRealThailand, 16. Oktober 2026).** Die Compositor-Engine kannte keine Rundung: Radius und Rahmen wirkten dort nicht. Neu im Plugin `gstpbpip`: `pbtap` sichert die Ecken des Hauptbildes unter den gerundeten kleinen Bildern, `pbround` rundet hinter dem Compositor und zeichnet den Rahmen (Rundung mit oder ohne Rahmen und Beschnitt). Ein älteres Plugin ohne die Elemente läuft wie bisher. Gemessen auf der Box von Marco: Compositor und Encoder weiter bei rund 30 Bildern/s.
- **Tailscale: öffentliche Freigabe nach einem Stromausfall (Meldung von MyRealThailand, 16. Oktober 2026).** Nach einem Stromausfall (die Box hat keine Echtzeituhr) blieb die Freigabe tot, bis man sie aus- und wieder einschaltete. (1) Selbstheilung: Ein Zeitgeber prüft jede Minute und wendet die öffentliche Freigabe neu an, aber nur wenn sie gewollt ist (Merkzettel nur für root), Tailscale „Running“ meldet und die Uhr stimmt: einmal nach dem Start, nach über 5 Minuten ohne Netz und bei fehlgeschlagener Prüfung von außen (höchstens alle 10 Minuten, 6 Mal je Stunde). Sprang die Uhr nach dem Start von tailscaled, startet er einmal neu. (2) Startreihenfolge: tailscaled startet nach Netz und Zeitsynchronisation. (3) Das Protokollpaket zeigt Tailscale-Start, Zustand, Uhr und Selbstheilung.

## 1.0.53
- Übernommen aus IRL4YOU 1.0.53: Die Verzögerung der Kameras wirkte unter 450 ms nicht mehr (`hold_delays` in `pipbox_live.py`); jetzt bleiben die eingestellten Unterschiede erhalten. Neu: optionaler Helfer `install/optional/pipbox-88x2bu.py` (neuerer WLAN-Treiber für RTL8812BU/8822BU, nicht Teil von `install.sh`). Test `tools/test_live.py` angepasst.

## 1.0.52
- **Kameras: gleiche Reihenfolge in Kopfzeile, Übersicht und Kameraliste; eine sendende Kamera ist nie „aus (gewollt)“ (Meldung von Bittersweet, 15. Oktober 2026).** (1) Die Punkte in der Kopfzeile der Chat-Karte und die Übersicht „Kameras“ folgten der Reihenfolge der Anlage, die Kameraliste dagegen einer eigenen Sortierung (Kameras mit Signal zuerst, ohne Signal erst nach zwei Minuten nach unten, gewollt ausgeschaltete ganz unten). Jetzt gilt überall dieselbe Regel (`camSorted` in der Oberfläche). (2) `server.py` stufte eine DJI-Kamera als „aus (gewollt)“ ein, sobald ihr automatisches Verbinden aus war und der Bluetooth-Dienst sie nicht als streamend meldete. Nach einem Neustart des Dienstes (Update) kennt er eine weiterlaufende Kamera nicht mehr (`idle`), sie sendet aber weiter an die Box: Sie stand dann als „aus (gewollt)“ da und hatte keinen Punkt in der Kopfzeile. Jetzt gilt eine Kamera, die an die Box sendet (`publishing`), nie als „aus (gewollt)“, auch ohne automatisches Verbinden; wer „Trennen“ gedrückt hat, bleibt „aus“. Tests: `tools/test_dji_service.py` (Kamera sendet ohne automatisches Verbinden), `tools/test_ui_layout.py` (`CameraOrderEverywhere`).

## 1.0.51
- **Facecam: Der Ton bleibt bei der Facecam, auch wenn das Hauptbild wechselt (Wunsch von Bittersweet, 15. Oktober 2026).** Neues Untermenü **Facecam** unter „Bildaufbau“ mit den Knöpfen „Kleines Bild 1“, „Kleines Bild 2“, „Kleines Bild 3“ und „Keine Facecam“; unter jedem Knopf steht die Kamera, die dort gespeichert ist. Die Wahl gilt sofort (ohne „Bildaufbau speichern“), der Ton wechselt live wie bei der Fußleiste. Gespeichert wird die **Kamera**, nicht der Platz (`facecam` in `pipeline.json`, `POST /api/pipeline/facecam`): Beim Tausch des Hauptbilds bleibt der Ton bei ihr, auch wenn sie selbst zum Hauptbild getauscht wird (der Ton geht mit ihr ins Hauptbild und beim Zurücktauschen wieder mit). Eine von Hand in der Fußleiste gewählte Tonquelle gilt bis zum nächsten Tausch des Hauptbilds, dann geht der Ton wieder zur Facecam. **Ohne Facecam** gilt die bisherige Logik: Der Ton folgt dem wechselnden Hauptbild. Beim Speichern des Bildaufbaus folgt die Facecam ihrem kleinen Bild (eine andere Kamera dort wird die neue Facecam); eine deaktivierte Kamera kann nicht die Facecam sein. Die Facecam allein ändert den Aufbau nicht, die Sendung startet dafür nie neu.
  - **Zusätzlich:** Liegt der Ton auf einem kleinen Bild, dessen Kamera nicht im Bild ist (ausgefallen oder selbst als Ersatz Hauptbild geworden), spielt die Engine „Alle Kameras immer bereit“ jetzt den Ton des Hauptbilds statt Stille (`pipbox_live.py`).
  - **Vorschau:** „Vorschau auch von außen erlauben (verbraucht Upload)“ verschwindet mit gesetztem Haken wie der Haken zur Rechenleistung; das Zahnrad ⚙ in der Kopfzeile der Karte holt beide Schalter zurück.
  - Neue Texte nur Deutsch und Englisch. Tests: `tools/test_ui_backend.py` (`Facecam`, 11), `tools/test_live.py` (`FacecamAudio`, 2), `tools/test_ui_layout.py` (3). Auf der Box von Marco eingespielt (Oberfläche und Knöpfe angesehen; ein Wechsel mit zwei laufenden Kameras wurde noch nicht durchgespielt).

## 1.0.50
- Kameraliste am Handy: Die Ziel-Linie beim Ziehen bleibt sichtbar (auch beim Hochziehen); englische Texte für den Anfasser.
- Behoben: Eine mit „Ausschalten“ getrennte DJI-Kamera blieb nach einem Neustart nicht aus. Der Zustand steht jetzt in der Konfiguration; „Verbinden“ hebt ihn auf.
- CHANGELOG gekürzt: je Version nur noch kurze Stichpunkte.

## 1.0.49
- **Bildaufbau: Automatisches Umschalten, Tausch ohne Unterbrechung und „Alle Kameras immer bereit“ sind zuklappbar (Wunsch von Bittersweet, 15. Oktober 2026).** Die drei Einstellungen unter „Bildaufbau“ stehen jetzt in einem Bereich „Umschalten und Tausch der Kameras“, der beim Öffnen der Seite **zugeklappt** ist. „Bildaufbau speichern“ bleibt darunter und immer sichtbar. Der Bereich erscheint in Optionen → Menüpunkte anzeigen als Untermenü von „Bildaufbau“ und lässt sich dort ausblenden. Die Einstellungen selbst sind unverändert und gelten wie bisher für die ganze Box (`pipeline.json`). Neue Texte nur Deutsch und Englisch. Test: `tools/test_ui_layout.py` (`PipelineSwitchOptions`).

## 1.0.48
- **„Vorschau auch von außen erlauben (verbraucht Upload)“ steht jetzt in der Karte „Vorschau“ (Wunsch von Bittersweet, 15. Oktober 2026).** Der Schalter war bisher unter „Verbindungen → Zugriff auf diese Oberfläche“; jetzt steht er in der Vorschau-Karte unter dem Haken zur Rechenleistung, und der Hinweistext (Upload, 320 Pixel, ein Zuschauer) steht hinter einem „i“ neben dem Schalter. Die Meldung der Vorschau, wenn sie von außen aus ist, verweist auf die Karte „Vorschau“ statt auf die Verbindungen. Die Einstellung selbst (`/api/previewaccess`) ist unverändert. Neue Texte nur Deutsch und Englisch. Test: `tools/test_ui_layout.py` angepasst.

## 1.0.47
- **Ansichten für Desktop und Mobil, auf der Box gespeichert (Wunsch von Bittersweet, 15. Oktober 2026).** Menüpunkte anzeigen, Reihenfolge der Hauptmenüs und Design gelten jetzt je Ansicht: **Desktop** und **Mobil** haben jeweils ihre eigene Auswahl (zum Beispiel am Rechner ohne Chat und Vorschau, am Handy mit beiden). Beide stehen dauerhaft auf der Box (`layout.json`) und gelten für alle Geräte; im Browser liegt nur eine Zwischenkopie, damit die Seite sofort richtig aussieht. **Welche Ansicht ein Gerät zeigt:** automatisch nach der Fensterbreite (bis 620 Pixel = Mobil, dieselbe Grenze wie die Handy-Kopfleiste), oder von Hand „Immer Desktop“ oder „Immer Mobil“ (gilt nur für dieses Gerät). Unter Optionen wählt man oben, **für welche Ansicht** man bearbeitet; „Von Mobil/Desktop übernehmen“ kopiert die andere Ansicht. Reihenfolge und Sichtbarkeit bleiben getrennte Listen mit Untermenüs wie bisher. Gespeichert werden nur Abweichungen vom Standard, neue Menüs erscheinen von selbst in beiden Ansichten.
  - **Erster Start:** Der bisherige Stand des Browsers (`pb_layout`, `pb_design`) wird für beide Ansichten übernommen, aber nur, wenn auf der Box noch nichts liegt (ein zweites Gerät überschreibt nichts). Ein Wechsel der Fensterbreite (Drehen) schaltet die Ansicht um; Änderungen eines anderen Geräts werden beim Zurückkehren zur Seite übernommen.
  - **Nicht dabei:** Hell/Dunkel und die Fußleiste am Handy bleiben je Browser. Die Menüeinstellung ist (noch) nicht Teil von „Einstellungen sichern“.
  - **Server:** `LayoutStore` (`GET/POST /api/layout`, streng geprüft: nur Namen aus Kleinbuchstaben, Ziffern und `_`, höchstens 200 je Liste, Design `klar`/`kompakt`/leer). Seit 0.9.176 lag die Einstellung nur im Browser, weil eine gemeinsame Liste beim Umstellen am Rechner auch das Handy veränderte; jetzt trennen die zwei Ansichten das sauber.
  - **Behoben:** Die Namen der Menüs in der Liste trugen die Hilfe-Knöpfe „i“ als Text (zum Beispiel „Box ausschalten und abmeldeni“); sie werden jetzt ohne Knöpfe und Zähler gelesen.
  - Neue Texte nur Deutsch und Englisch. Tests: `tools/test_ui_layout.py` (Ansichten), `tools/test_ui_backend.py` (`LayoutViews`, 7 Tests), `tools/test_designs.py` angepasst. Auf der Box von Marco eingespielt; die Bedienung wurde dort noch nicht durchgespielt.

## 1.0.46
- Kameraliste am Handy: Ziehen am Anfasser klappt zuverlässig, „Ausschalten“ und „Entfernen“ stehen in einer eigenen Zeile und ragen nicht mehr aus der Karte, Anfasser größer.

## 1.0.45
- Kameras per Anfasser (⋮⋮) verschieben: am Handy statt der Pfeile, am Rechner bleiben die Pfeile. Die Reihenfolge bleibt gespeichert und bestimmt die Kamera-Knöpfe in der Fußleiste.
- Reihenfolge der Liste: Kameras mit Signal, dann ohne Signal, ganz unten die gewollt ausgeschalteten.

## 1.0.44
- Getrennte oder ausgeschaltete DJI-Kameras stehen in der DJI-Liste unten.

## 1.0.43
- Ausgeschaltete Kameras fehlen in der Status-Übersicht und bei den Punkten der Chat-Karte; in der Kameraliste bleiben sie mit „aus (gewollt)“.

## 1.0.42
- Kameras bewusst ausschalten: Knopf „Ausschalten“/„Einschalten“ in der Kameraliste, Markierung „aus (gewollt)“, keine Störungsmeldung mehr. „Entfernen“ steht daneben.

## 1.0.41
- Bluetooth: „Treiber jetzt einrichten“ ist neutral gefärbt, wenn der Treiber schon läuft.

## 1.0.40
- Das Update baut belacoder auch neu, wenn sich nur das Bauskript ändert (Korrektur zu 1.0.39).

## 1.0.39
- Kennung des belacoder bleibt bei „sb12“ (Korrektur zu 1.0.38): Mit „sb13“ nahm die Engine einen zu großen Puffer, das Hauptbild ruckelte beim Wechsel.

## 1.0.38
- **Ton und Bild bleiben nach dem Ausfall der Hauptkamera nicht mehr weg (Meldung von Bittersweet, 15. Oktober 2026).** Mit „Alle Kameras immer bereit“ sprang das Hauptbild beim Ausfall einer Kamera zwar auf die nächste, der Ton kam aber nicht mit, und in OBS stand das Bild. **Ursache (am Protokoll des Ton-Umschalters gemessen):** Hört eine Kamera auf zu senden, schickt ihr Zweig ein Ende-Signal (EOS) durch den Ton-Weg. Der Umschalter `asel` gibt das EOS seines **aktiven** Eingangs an Opus-Encoder und Muxer weiter, und dort ist es endgültig: etwa 150 ms später stellte der Regler `asel` zwar auf die andere Kamera, aber hinter dem Umschalter war der Ton-Weg schon beendet (im Datenstrom fehlte die Tonspur ganz, auch bei Stille als Quelle). Die Anzeige in der Fußleiste nannte trotzdem die neue Tonquelle. **Jetzt:** Neuer Patch `belacoder/belacoder-audio-eos.patch` (belacoder-Kennung `…-sb13`): Das EOS wird am letzten Puffer jedes Ton-Zweigs abgefangen und erreicht den Umschalter nie; das Ende einer Kamera behandelt weiter `feed_stop` (Zweig anhalten, Ton zurück auf Stille). Mit `SB_FEED_AUDIO_EOS=0` abschaltbar. `install.sh` baut belacoder neu, sobald ein Patch neuer ist (braucht Internet, git und gcc; sonst bleibt die bisherige Fassung). **Gemessen auf der Testbox (S23 und Osmo):** vor dem Patch fehlte die Tonspur (PID 66) nach dem Ausfall des S23 vollständig; mit dem Patch laufen Bild und Ton (von der Osmo) weiter, und der Ton von Hand auf die Osmo zu stellen funktionierte schon vorher.

## 1.0.37
- Hauptbild wechseln ohne Stockung: Die Ausgabe fällt beim Tausch nicht mehr kurz auf 5 Bilder/s. Neuer belacoder-Patch, das Update baut belacoder neu (Internet nötig, einige Minuten).

## 1.0.36
- Protokolle wachsen nicht mehr unbegrenzt: „Sparsam“ schreibt kein nginx-Zugriffsprotokoll, „Ausführlich“ begrenzt und komprimiert die Protokolle täglich.

## 1.0.35
- Änderungsverlauf („i“ bei der Version) lädt auch während der Sendung, nur auf Klick.
- Alle zwölf Sprachen auf Stand; Zustandsprotokoll rundet die Last richtig.

## 1.0.34
- Hotspot bleibt auf seinem WLAN-Stick, auch wenn die Namen wlan0/wlan1/wlan2 wechseln (an die Hardware-Adresse gebunden, Migration beim Update).
- Neuer Knopf „Löschen“ für den Hotspot einer Karte.

## 1.0.33
- Start der Sendung ohne Anlauf mit 4 bis 5 Bildern/s: Die Bildgröße wird vor dem Start gesetzt.
- Ton wechselt mit, wenn die Kamera mit dem Ton deaktiviert wird (Meldung von Bittersweet).
- Die Update-Knöpfe sind nur gelb, wenn ein Update bereitliegt.

## 1.0.32
- Upload-Anzeige zeigt die Nutzdaten der Sendung statt der Summe der Netzkarten.
- Weicher Start der Sendung vorbereitet (ausgeschaltet).

## 1.0.31
- Zurückgenommen: Kopie des ganzflächigen Hauptbilds aus 1.0.30 (grüner Streifen oben im Bild).

## 1.0.30
- Hauptbild: belacoder kopiert auch das ganzflächige Bild (Engine „sb13“), um Schübe der Kamera abzufangen. In 1.0.31 zurückgenommen.

## 1.0.29
- Hauptbild ruckelt nicht mehr, wenn die Kamera ihre Bilder in Schüben sendet (größerer Mindestpuffer).
- Tests auf dem Mac repariert.

## 1.0.28
- HDMI-/USB-Kamera: Der wachsende Versatz des Bildes ist behoben.

## 1.0.27
- Verzugs-Schutz: Ein dauerhaft zu spätes oder zu frühes Kamerabild wird kurz aus der Sendekette genommen und neu ausgerichtet.
- Sperre nach einem Wechsel: eigener kleiner Text, erst nach dem grünen Hinweis.

## 1.0.26
- Kamera kommt nach dem Netzwechsel zurück: Der Start wartet, bis nginx den alten Stream beendet hat.
- Nach einem Wechsel zwei Minuten Sperre; eine Kamera ohne Signal bleibt zwei Minuten oben in der Liste.
- Hängt nur ein Kamerabild, wird nur dieses zurückgesetzt.

## 1.0.25
- Beim Kamerawechsel wird der Zweig zuerst aus der Sendekette genommen, danach beendet die Kamera ihren Stream.

## 1.0.24
- Netzwechsel-Hinweis: Der Erfolg verschwindet nach 15 Sekunden, Fehler bleiben stehen.

## 1.0.23
- Kleiner Hinweis zum Netzwechsel unter der Zeile einer DJI-Kamera.

## 1.0.22
- DJI-Kamera verbindet nach einem Wechsel der Verbindung wieder.

## 1.0.21
- „i“ bei der installierten Version zeigt den Änderungsverlauf.

## 1.0.20
- DJI-Kameras zeigen in der Verbindungs-Auswahl nur noch WLAN-Schnittstellen.

## 1.0.19
- Der Knopf „Neu verbinden“ (↻) ist bei DJI-Kameras wieder zu sehen.

## 1.0.18
- DJI-Kameras: Netzwechsel und Liste der gespeicherten Netze.

## 1.0.17
- Hauptbild ruckelt nicht mehr bei großen Verzögerungen: „Alle Kameras immer bereit“ begrenzt Verzögerungen auf 750 ms.

## 1.0.16
- Aussetzer des Hauptbilds bei großen Verzögerungsunterschieden beseitigt.

## 1.0.15
- Behoben: Das Hauptbild ruckelte und der Durchsatz sank, wenn die Verzögerungen der Kameras nicht zum Puffer passten.
- Neu: Systemwarnungen im Twitch-Chat (Issue #67).

## 1.0.14
- Bluetooth-Treiber: Realtek-Sticks wie der RTL8761B werden nicht mehr von `rtk_btusb` festgehalten.

## 1.0.13
- Bluetooth-Treiber wartet auf die Meldung der Firmware (Realtek RTL8761B).

## 1.0.12
- Die Warnung „zwei Verbindungen zum selben Netzwerk“ kommt nicht mehr zu oft.

## 1.0.11
- Kameras fielen aus, wenn zwei Netzkarten im selben Netz hingen: neuer Dienst und Warnung.
- „Kamera entfernen“ hebt bei DJI-Kameras auch die Bluetooth-Kopplung auf.

## 1.0.10
- Eine entfernte DJI-Kamera verschwindet sofort aus der Liste. Enthält 1.0.9.

## 1.0.9
- Live-Karte am Rechner: weniger Abstand zwischen Verlauf und Megabit-Zahl.

## 1.0.8
- WLAN-Profile folgen der Karte statt ihrem Namen (Ursache einer nicht mehr funktionierenden DJI Action 4).

## 1.0.7
- Live-Karte am Rechner: Platz gespart, die Temperatur der Box steht dabei.

## 1.0.6
- Der Hinweis über der kleinen Vorschau von außen verschwindet nach 15 Sekunden.

## 1.0.5
- Hinweis oben, wenn noch kein SRTLA-Server eingetragen ist.

## 1.0.4
- Bluetooth auf der ROCK 5B Plus: Der Chip auf dem Board läuft von selbst.

## 1.0.3
- DJI-Kameras: Ein laufender Stream wird nicht mehr grundlos unterbrochen.

## 1.0.2
- Bluetooth-Treiber: Reparatur beim Start und Knopf „Treiber jetzt einrichten“.

## 1.0.1
- Fernzugriff: fester Link zur Freischaltung von HTTPS.

## 1.0.0
- Version 1.0 ohne „Beta“.

## 0.9.231 (Beta)
- Die Vorschau von außen lässt sich auch über Tailscale einschalten und zeigt den echten Grund, wenn sie nicht geht.

## 0.9.230 (Beta)
- Modell-Liste der DJI-Kameras gekürzt; Übersetzungen und README-Stand nachgetragen.

## 0.9.229 (Beta)
- README und Anleitungen verlinken irl4you.de.

## 0.9.228 (Beta)
- Hinweis, wenn der Bildaufbau fehlt.

## 0.9.227 (Beta)
- DJI-Kamera: „Netze suchen“ zeigt den Grund an.

## 0.9.226 (Beta)
- README: Die Osmo Pocket 3 steht in der Liste der getesteten Kameras.

## 0.9.225 (Beta)
- Änderungsliste gekürzt; Übersetzungen nachgetragen.

## 0.9.224 (Beta)
- README stark gekürzt; neue Anleitung `ANLEITUNG-Hardware.md`.

## 0.9.223 (Beta)
- Vorschau von außen: standardmäßig aus, mit Erlaubnis nur klein und für einen Zuschauer.

## 0.9.222 (Beta)
- Karte „Live“: Der Verlauf steht auch am Rechner dabei; am Handy wechseln die Chat-Zeilen nur noch leicht zwischen hell und dunkel.

## 0.9.221 (Beta)
- Übersetzungen nachgetragen.

## 0.9.220 (Beta)
- Chat: Abzeichen als kleine Symbole statt Wörter.

## 0.9.219 (Beta)
- Nach der Anmeldung verschwindet die Anzeige sanft; Chat am Rechner mit schmaleren Zeilen; Anmelde-Auswahl aufgeräumt.

## 0.9.218 (Beta)
- Übersetzungen nachgetragen. Neue Texte je Version nur auf Deutsch und Englisch, die anderen Sprachen folgen gesammelt.

## 0.9.217 (Beta)
- Das Feld „Kanal“ steht in den Einstellungen statt dauerhaft oben.

## 0.9.216 (Beta)
- Haupt- und Unterpunkte sind zu unterscheiden.

## 0.9.215 (Beta)
- Chat: Das Feld „Kanal (Konto, auf dem gestreamt wird)“ steht oben im Chat.

## 0.9.214 (Beta)
- Der Kanal legt fest, wer Streamer ist.

## 0.9.213 (Beta)
- Twitch-Chat: normale Zuschauer-Anmeldung, getrennt von Moderator und Streamer; der Moderator-Status wird bei Twitch geprüft.

## 0.9.212 (Beta)
- Twitch: Moderatoren melden sich mit dem eigenen Konto an („Als Moderator anmelden“, „Ich bin der Streamer“).
- Das „i“ steht direkt hinter der Überschrift; Osmo Pocket 3 als geprüft dokumentiert.

## 0.9.211 (Beta)
- Chat-Befehle: Die Antwort ist zu sehen.

## 0.9.210 (Beta)
- Twitch-Chat: neue Befehle; „Moderation einschalten“ holt alle Rechte auf einmal; Befehle des Kanalinhabers nur im Browser des Streamers.

## 0.9.209 (Beta)
- DJI Osmo Pocket 3: Ladesymbol 🔌.

## 0.9.208 (Beta)
- Sicherung: Aufklapper „Was wird gesichert?“ vor dem Herunterladen.

## 0.9.207 (Beta)
- Netzwerkkarten folgen der MAC: Hotspots und eigene Namen kommen mit.

## 0.9.206 (Beta)
- Netzwerkkarten folgen im laufenden Betrieb der MAC-Adresse.

## 0.9.205 (Beta)
- Sicherung: Netzwerkkarten werden über die MAC-Adresse zugeordnet.

## 0.9.204 (Beta)
- Nach dem Einspielen einer Sicherung: Hinweis „Nach dem Einspielen noch zu erledigen“; Dokumentation angepasst.

## 0.9.203 (Beta)
- Einspielen einer Sicherung mit Fortschrittsanzeige; Meldung „WLAN-Helfer hat nicht rechtzeitig geantwortet“ behoben.

## 0.9.202 (Beta)
- `setup.sh`: Link und Neustart-Hinweis am Ende deutlich.

## 0.9.201 (Beta)
- Komplett-Einrichtung mit einem Befehl (`install/setup.sh`).

## 0.9.200 (Beta)
- „Problem melden / Wunsch äußern“ steht unter Optionen; Übersetzungen nachgetragen.
