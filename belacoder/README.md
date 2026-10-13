# belacoder mit toleranterem Bitraten-Regler, Stall-Wächter am Ausgang und Kamera-Zweigen

Fünf Patches für `belacoder.c`, in dieser Reihenfolge angewendet von `build.sh`: `belacoder-jitter-tolerant.patch` (Bitraten-Regler), `belacoder-stall-output.patch` (Stall-Wächter, siehe unten) und `belacoder-stats.patch` (schreibt einmal je Sekunde Kennzahlen in eine JSON-Datei, wenn `BELACODER_STATS_FILE` gesetzt ist; für "Details" im Status). Der vierte, `belacoder-live-feeds.patch` (von Bittersweet1987, aus seinem Projekt `streamingbox`, mit der Steuerung `pipbox_live.py` des Pakets), fügt Kamera-Zweige `sbf0` bis `sbf7` hinzu, die im laufenden Betrieb gestartet und gestoppt werden, einen Steuerkanal (`-C`), eine Statistikdatei (`-S`) und einen Ausrichtungspuffer (`-A`). Er ist die Grundlage der Engine für "Alle Kameras immer bereit" (Kennung `-sb11`, mindestens `-sb10` nötig). Er steht wie belacoder unter GPL-3.0.

`belacoder-jitter-tolerant.patch` ändert die Funktion `update_bitrate()` in `belacoder.c` aus
[BELABOX/belacoder](https://github.com/BELABOX/belacoder) (Commit `ccce9ca33c8e425b33353500b95795101e847964`, Lizenz **GPL-3.0**). Alle
Patches stehen unter derselben Lizenz (GPL-3.0, nicht MIT wie der Rest dieses Pakets); der Quelltext des gebauten Programms ist:
Upstream-Commit plus diese Patches. Der Upstream-Quelltext selbst liegt nicht in diesem Paket, `build.sh` holt ihn von GitHub.

## Warum

Bei gebündelten Mobilfunkleitungen schwankt die RTT normal um 15 bis 30 ms. Der Original-Regler senkt die Bitrate schon, wenn die RTT
nur etwa 15 Prozent über ihrem Mittel liegt, und zwar immer um mindestens 100 kbit/s. Erhöhen darf er nur, wenn die RTT fast genau
auf ihrem Tiefstwert liegt, und dann nur um 30 kbit/s plus 3 Prozent pro halbe Sekunde. Nach einer kurzen Überlast (RTT-Anstieg) fällt er
deshalb auf den kleinsten Wert und bleibt dort hängen, obwohl die Leitung ein Mehrfaches hergäbe (gemessen: 1,5 statt über 12 Mbit/s,
minutenlang). Ein Neustart des Encoders behebt es nur, bis zur nächsten Überlast.

## Was der Patch ändert

- Die RTT-Schwelle zum Senken hat einen festen Mindestabstand von 30 ms über dem Mittel (`RTT_DECR_TOL_MS`).
- Die Schwelle zum Erhöhen lässt 8 ms über dem Tiefstwert zu (`RTT_INCR_TOL_MS`), und der mittlere RTT-Anstieg darf bis 0,5 ms je Messung
  betragen (vorher 0,01).
- Der leichte Senkungsschritt ist höchstens 10 Prozent der aktuellen Bitrate (mindestens 30 kbit/s) statt immer 100 kbit/s.
- Unverändert: die Schnellbremse bei echtem Stau (RTT über einem Fünftel der SRT-Latenz, großer Sendepuffer) und alle Schwellen für den Sendepuffer.

## Stall-Wächter: nur der Ausgang zählt (`belacoder-stall-output.patch`)

Upstream fragt alle 2 s die Position der **ganzen Pipeline** ab und beendet belacoder, wenn sie sich zweimal nicht bewegt hat
(Meldung `Pipeline stall detected`). In GStreamer ist die Position einer Pipeline aber das **Maximum der Positionen aller Senken**. Beim
Bild-in-Bild gehören dazu die Senken der kleinen Kameras; sie melden die rohen Zeitstempel ihrer Kamera-Sitzung (nicht die Laufzeit der
Pipeline). Entscheidend ist damit die kleine Kamera mit der ältesten Sitzung: Setzte sie 2 bis 4 s lang aus (bei DJI-Kameras im WLAN
normal), endete die ganze Sendung, obwohl Hauptbild und Ausgang einwandfrei liefen. Der Mischer blendet ein ausgefallenes kleines Bild ohnehin
nach 2 s aus.

Neu: Sobald der Ausgang (`appsink`) eine Position meldet, zählt nur noch er. Der Wächter schlägt erst nach drei Prüfungen in Folge ohne
Fortschritt an (rund 6 bis 8 s, `STALL_SAME_MAX`; Upstream: 1), damit kurze Aussetzer der Hauptkamera nicht häufiger zum Neustart führen als
vorher. In den ersten Sekunden, solange der Ausgang noch nichts geliefert hat, gilt wie bisher die ganze Pipeline. Die Meldung lautet jetzt
`Pipeline stall detected (output)` beziehungsweise `(pipeline)`; `pipbox_send.py` erkennt beide (und die alte ohne Zusatz). Ein Aussetzer
einer kleinen Kamera lässt jetzt nur ihr Bild verschwinden. Steht dagegen der Ausgang selbst still (zum Beispiel weil die Hauptkamera
nichts mehr liefert oder der Encoder hängt), startet der Wächter den Encoder neu; vorher war das nur der Fall, solange keine kleine Kamera
Daten lieferte.

Nicht geändert: Entfernt der RTMP-Server eine Kamera (`drop_idle_publisher`), endet der Encoder wie bisher, und die Kameraumschaltung
reagiert darauf. Rückweg: `belacoder.vor-stallpatch` (Sicherung der Fassung davor, legt `build.sh` einmal an) mit `mv -f` über
`/opt/pipbox/bin/belacoder` legen.

## Bauen und Benutzen

`sudo sh build.sh` (installiert nach `/opt/pipbox/bin/belacoder`; `BELACODER_DEBUG=1` baut mit Ausgabe der Reglerwerte). Die Sendekette
nimmt dieses Programm, wenn es existiert, sonst das Original aus `/usr/bin`. Rückweg: `/opt/pipbox/bin/belacoder` löschen.

## Bilder vor dem Vergrößern kopieren (`belacoder-frame-copy.patch`, Issue #35)

Der Hardware-Dekoder (`mppvideodec`) legt seine Bilder in Speicher ohne Zwischenspeicher der CPU ab. Die CPU liest daraus sehr langsam, und der Skalierer (`videoscale`, nearest) liest beim Vergrößern jedes Quellpixel mehrfach (ein Mal je Ausgabepixel). Gemessen auf einer Orange Pi 5 Plus (RK3588) mit einem 720p-Bild, das auf 1080p vergrößert wird: **43 ms je Bild direkt aus dem Dekoderspeicher** (mehr als die 33 ms eines Bildes bei 30 Bildern/s), **3,5 ms nach einer einmaligen Kopie** in normalen Speicher (die Kopie allein 1 ms). Auf einer Box mit 4 GB Speicher stand der Zweig dadurch auf einem Kern bei 100 %, das Bild kam 1 s zu spät und der Ausgang blieb stehen.

Der Patch hängt an die Warteschlange `<Zweig>_lq` (hinter dem Dekoder) eine Prüfung: Wird das Bild größer als es ist (Ziel laut `<Zweig>_scc`), kopiert sie es einmal in normalen Speicher (mit Zeitstempeln und Metadaten). Bilder, die gleich groß bleiben oder verkleinert werden, laufen unverändert weiter (dort kostet die Kopie mehr, als sie spart). `SB_FRAME_COPY=0` schaltet es ab.
