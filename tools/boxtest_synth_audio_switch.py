#!/usr/bin/env python3
"""Synthetischer Test des Ton-Umschalters (gst/gstpbpip.c) ohne Hardware, darf auch neben einer laufenden Sendung laufen (kein Decoder/Encoder): 3 Tonquellen (verschiedene Töne, Verzögerungen 0/1860/1600 ms), Bild dummy,
pbctl schaltet den Ton nach Dateien; stoßweise Ankunft wie bei DJI-Ton (audiobuffersplit). Schreibt /var/tmp/synth/out.raw (S16LE) für die Prüfung auf Knackser. Prüft die Ausgangszeit des Tons (Lücken, Rückwärts, Abstand zur Echtzeit) und Pegelsprünge."""
import os, re, subprocess, sys, time, shlex, threading
D = "/var/tmp/synth"; os.makedirs(D, exist_ok=True)
# Aufruf: boxtest_synth_audio_switch.py [Bausteinordner=/opt/pipbox/gst] [Anzahl Wechsel=12]
PLUG = sys.argv[1] if len(sys.argv) > 1 else "/opt/pipbox/gst"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 12
HOLD = 4.0
DEL = [0, 1860, 1600]
FREQ = [440, 660, 880]
BURST = [int(x) for x in os.environ.get("BURST", "1024,19200,38400").split(",")]      # Samples je Quellpuffer: große Puffer = stoßweise Ankunft (wie DJI-Ton)
Q = "queue max-size-time=10000000000 max-size-buffers=1000 max-size-bytes=41943040"
parts = []
for i in range(3):
    parts.append(f"videotestsrc is-live=true ! video/x-raw,format=NV12,width=160,height=90,framerate=15/1 ! {Q} ! vsel.sink_{i}")
    parts.append(f"audiotestsrc is-live=true wave=sine freq={FREQ[i]} samplesperbuffer={BURST[i]} volume=0.5 ! audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved ! audiobuffersplit output-buffer-duration=1024/48000 ! "
                 f"{Q} name=aq{i} min-threshold-time={DEL[i]*1000000} ! asel.sink_{i}")
parts.append("pbpipsel name=vsel tag-offset=true force-key=true state=%d ! fakesink sync=false" % (0 | 1 << 4 | 2 << 8 | 15 << 12))
parts.append("pbpipsel name=asel state=0 ! identity name=chk silent=false ! filesink location=" + D + "/out.raw")
parts.append(f"pbctl name=pbctl selector=vsel audio-selector=asel audio-pos=-1 cam0=aq0:a cam1=aq1:a cam2=aq2:a file={D}/delay select-file={D}/select state-file={D}/swap-state view-file={D}/view view-state-file={D}/view-state")
for f, v in (("delay", "0 1860 1600 0"), ("select", "0 1 2 15"), ("view", "0 -1 0")):
    open(f"{D}/{f}", "w").write(v + "\n")
cmd = ["gst-launch-1.0", "-v"] + shlex.split(" ".join(parts))
env = dict(os.environ, GST_PLUGIN_PATH=PLUG)
rows = []
p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="ignore")
def rd():
    for l in p.stdout:
        if "GstIdentity:chk" in l: rows.append((time.time(), l))
threading.Thread(target=rd, daemon=True).start()
time.sleep(14)
seq = ["0 0 0", "0 1 0", "0 -1 0", "0 1 0", "0 0 0", "0 -1 0"]
t0 = time.time(); marks = []
for i in range(N):
    v = seq[i % 6]
    open(f"{D}/view.tmp", "w").write(v + "\n"); os.replace(f"{D}/view.tmp", f"{D}/view")
    marks.append((time.time() - t0, v))
    time.sleep(HOLD)
    if p.poll() is not None: print("Pipeline beendet!"); break
p.terminate(); p.wait(timeout=10); time.sleep(0.5)
def ts(x):
    h, m, s = x.split(":"); return int(h) * 3600 + int(m) * 60 + float(s)
pts = []
walls = []
for wt, l in rows:
    m = re.search(r"GstIdentity:chk:.*?\((\d+) bytes, dts: ([\d:.]+|none), pts: ([\d:.]+|none), duration: ([\d:.]+|none)", l)
    if m and m.group(3) != "none": pts.append((ts(m.group(3)), ts(m.group(4)) if m.group(4) != "none" else 0.0, int(m.group(1)))); walls.append(wt)
print("Ton-Puffer", len(pts), "Dauer", round(pts[-1][0] - pts[0][0], 1), "s; Schaltungen", len(marks))
gaps = [(round(pts[i][0], 3), round(pts[i][0] - (pts[i-1][0] + pts[i-1][1]), 4)) for i in range(1, len(pts)) if abs(pts[i][0] - (pts[i-1][0] + pts[i-1][1])) > 0.003]
print("Unstetigkeiten der Ausgangszeit (>3 ms):", len(gaps), gaps[:12])
dur = pts[-1][0] + pts[-1][1] - pts[0][0]
print("Summe der Puffer %.2f s gegen Zeitspanne %.2f s" % (sum(x[1] for x in pts), dur))
silent = sum(1 for x in pts if x[1] > 0.0201 and x[2] == 0)
print("Rückwärts:", sum(1 for i in range(1, len(pts)) if pts[i][0] < pts[i-1][0]))

# Abstand Ausgangszeit zur Echtzeit über die Zeit (Drift)
base = walls[0] - pts[0][0]
lag = [(walls[i] - pts[i][0] - base) for i in range(len(pts))]
for k in range(0, len(pts), len(pts) // 10 or 1):
    print("  t=%5.1f s  Echtzeit-Abstand %+.3f s" % (walls[k] - walls[0], lag[k]))
print("Abstand min %+.3f max %+.3f (Schwankung %.3f s)" % (min(lag), max(lag), max(lag) - min(lag)))
