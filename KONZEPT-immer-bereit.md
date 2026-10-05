# Eingänge immer bereit (Issue #19, Notbetrieb abschaffen)

Stand: umgesetzt in 0.9.111 (5. Oktober 2026), als Schalter "Alle Kameras immer bereit (Beta)", Standard aus. Anlass: Rückmeldung von Bittersweet1987 in Issue #19.

## Ziel (seine Punkte)
1. Jedes Bildfeld der Sendekette läuft immer, auch wenn keine Kamera sendet (unsichtbar).
2. Eine Kamera erscheint sofort, wenn ihr Stream ankommt, ohne Neustart der Sendung. Tauschen, Ein-/Ausblenden, Ton wechseln gehen jederzeit.
3. Fällt die Hauptkamera aus, übernimmt die nächste Kamera mit echtem Stream. Kommt die ursprüngliche zurück, übernimmt sie wieder; die Ersatzkamera geht zurück auf ihr eigenes Bild.
4. Fußleiste und Tonwahl zeigen nur Kameras mit aktivem Stream. Rahmen verschwinden, wenn eine Kamera nicht sendet.

## Warum das heute nicht geht
Jede Kamera ist in der Pipeline eine feste Quelle (`rtmpsrc ! flvdemux`). Ohne Stream scheitert die Quelle sofort ("Failed to read any data from stream", gemessen), und endet ein Stream, beendet das den Encoder. Darum startet die Box bei einer fehlenden Kamera neu (Notbetrieb) und nimmt eine neue Kamera erst nach 60 s und einem Neustart auf.

## Lösung: Zubringer je Kamera
Vor die Sendekette tritt je Kamera ein kleiner Zubringer-Prozess (`gst-launch`), der den Stream aus dem RTMP-Server holt und lokal weiterreicht:

    nginx (rtmp://127.0.0.1/publish/<Schlüssel>)
      -> Zubringer (je Kamera, startet bei Fehler/Ende des Streams nach 1 s neu)
         Bild: h264parse -> rtph264pay (config-interval=1) -> udpsink 127.0.0.1:<Port>
         Ton:  aacparse -> avdec_aac -> S16LE 48 kHz Stereo -> udpsink 127.0.0.1:<Port+1>
      -> Sendekette: udpsrc (endet nie) -> rtph264depay -> h264parse -> Decoder ... (wie bisher)

Die Sendekette hat dadurch **nie** eine Quelle, die ausfällt: Kein Neustart bei Kamerawechsel. Der Ton wird im Zubringer in rohes PCM gewandelt, weil die AAC-Einstellungen je Kamera und Neuverbindung wechseln können (RTP-Kopfdaten hätten das nicht verkraftet; im Versuch "not-negotiated").

Gemessen auf der Box (Teststream 1080p30, 8 Mbit/s): Bild 30 Bilder/s, Ton 47 Puffer/s; Stream 8 s unterbrochen: Lücke 9,3 s, danach sofort wieder da, Sendekette läuft durch.

## Bausteine
1. **Baustein `pbpipsel`** (gst/gstpbpip.c): hält den Ausgang am Leben, wenn der gewählte Eingang nichts liefert.
   - Bild: nach 250 ms ohne Bild schwarze Bilder im Takt (Format des letzten Bildes, vor dem ersten 1920x1080 NV12).
   - Ton: Stille, sobald das Bild der Kamera ausbleibt (der DJI-Ton kommt stoßweise bis 1,7 s, darum nicht schon bei Ton-Lücken).
   - Zeit läuft nie rückwärts (beim Zurückkehren des echten Eingangs).
2. **`pbctl`** schreibt je Kamera, ob Bilder ankommen (`cam-live`, vier Zahlen).
3. **Bildaufbau-Erzeuger** (`server.py`): Modus "immer bereit" nur bei Art "Bild in Bild" mit Tausch-Plugin; alle eingestellten Kameras (bis 4) in der Tauschgruppe; Eingänge über udpsrc.
4. **Sende-Dienst** (`pipbox_send.py`): startet/überwacht die Zubringer, startet die Kette, sobald mindestens eine Kamera sendet, und verteilt danach nur noch live: Hauptkamera = erste lebende nach der Reihenfolge Hauptbild, kleine Bilder; nicht lebende Plätze leer. Keine Neustarts mehr bei Kamera-Ausfall oder -Rückkehr.
5. **Oberfläche**: Schalter "Alle Kameras immer bereit (Beta)" (Standard aus), Fußleiste/Ton nur mit lebenden Kameras, Notbetrieb-Hinweis entfällt in diesem Modus.

## Was nicht geändert wird
Ohne den Schalter bleibt alles wie in 0.9.110 (Release v0.9.110 als Rücksprungpunkt).

## Grenzen
- Mit echten Kameras und laufender Sendung nicht geprüft (nur Testbilder auf der Box).
- Jede Kamera wird in diesem Modus doppelt dekodiert (groß und klein), wie bei der Tauschgruppe heute; mehr CPU bei vier Kameras.
- Schwarzbild/Stille statt Notbetrieb: Fällt die letzte Kamera aus, sendet die Box Schwarz mit Stille weiter (statt zu warten).

## Messung auf der Box (5. Oktober 2026, Testbilder)
Drei synthetische 1080p30-Kameras (`tools/boxtest_always.py`): keine Neustarts in 55 s; Ausfall der Hauptkamera: Übernahme nach rund 1,9 s, Rückkehr nach 3 s Hysterese nach rund 4,6 s; Bild etwa 30 Bilder/s mit kurzen Einbrüchen an den Umschaltstellen; Rechenlast Sendekette etwa 40 % eines Kerns, Zubringer je etwa 5 %.
Nicht umgesetzt: Ton-Ausweichen auf eine andere Kamera, wenn die Kamera mit dem gewählten Ton ausfällt (es kommt Stille).
