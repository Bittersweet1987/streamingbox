#!/bin/bash
# Dauertest "Umschalten im Betrieb" mit dem ECHTEN belacoder und den echten Kameras, ohne Netz: Die Pipeline-Datei der Sendekette
# (/var/tmp/pipbox/pipeline, entsteht beim Start der Sendung) läuft mit einem lokalen SRT-Empfänger (srt-live-transmit). pbctl liest seine
# Dateien aus /var/tmp/e2e2, die Box-Einstellungen bleiben unberührt. Wechselt Ton (und mit MIX=1 auch Hauptbild/Kleinbilder) alle 8 s und meldet,
# nach dem wievielten Wechsel belacoder wegen "Pipeline stall detected" endet. Ausgangspunkt: Mit dem Baustein vor 0.9.91 endete belacoder nach 13
# bis 22 Wechseln, weil die Ausgangszeit des Tons bei jedem Wechsel hinter die Echtzeit zurückfiel.
# Nur bei angehaltener Sendung (systemctl stop pipbox-send), als root. Danach die Sendung wieder starten.
# Aufruf: boxtest_switch_belacoder.sh [Bausteinordner=/opt/pipbox/gst] [Anzahl Wechsel=40]      (MIX=1: auch Bildtausch)
set -u
PLUG=${1:-/opt/pipbox/gst}; N=${2:-40}
D=/var/tmp/e2e2
[ -f /var/tmp/pipbox/pipeline ] || { echo "FEHLER: /var/tmp/pipbox/pipeline fehlt (die Sendung muss einmal gelaufen sein)"; exit 2; }
systemctl is-active --quiet pipbox-send && { echo "FEHLER: pipbox-send läuft; vorher anhalten"; exit 2; }
rm -rf $D; mkdir -p $D
cp /var/tmp/pipbox/pipeline $D/pipeline
sed -i "s#pbctl name=pbctl#pbctl name=pbctl file=$D/delay select-file=$D/select state-file=$D/swap-state view-file=$D/view view-state-file=$D/view-state#" $D/pipeline
cat /var/lib/pipbox/main-delay-ms > $D/delay; echo "0 1 2 15" > $D/select; echo "0 -1 0" > $D/view
printf '300000\n12000000' > $D/bitrate
srt-live-transmit "srt://127.0.0.1:9101?mode=listener&latency=4000" file://con > /dev/null 2> $D/srt.log &
SRT=$!
sleep 1
GST_PLUGIN_PATH=$PLUG /opt/pipbox/bin/belacoder $D/pipeline 127.0.0.1 9101 -d 0 -b $D/bitrate -l 4000 -s test > $D/bc.log 2>&1 &
BC=$!
sleep 30
kill -0 $BC 2>/dev/null || { echo "FEHLER: belacoder läuft nach dem Start nicht"; tail -5 $D/bc.log; kill $SRT; exit 2; }
seq=("0 0 0" "0 1 0" "0 -1 0" "0 1 0" "0 0 0" "0 -1 0")
sels=("0 1 2 15" "1 0 2 15" "2 0 1 15" "0 2 1 15" "1 2 0 15" "0 1 2 15")
rc=0
for i in $(seq 1 $N); do
  if [ "${MIX:-0}" = 1 ] && [ $((i % 3)) = 0 ]; then
    v="Tausch ${sels[$(( (i / 3) % 6 ))]}"; echo "${sels[$(( (i / 3) % 6 ))]}" > $D/select.tmp; mv $D/select.tmp $D/select
  else
    v="Ton ${seq[$((i % 6))]}"; echo "${seq[$((i % 6))]}" > $D/view.tmp; mv $D/view.tmp $D/view
  fi
  sleep 8
  if ! kill -0 $BC 2>/dev/null; then echo "FAIL: belacoder beendet nach Wechsel $i ($v): $(tail -2 $D/bc.log | head -1)"; rc=1; break; fi
done
[ $rc = 0 ] && echo "PASS: $N Wechsel ohne Stillstand"
kill $BC 2>/dev/null; sleep 2; kill $SRT 2>/dev/null
exit $rc
