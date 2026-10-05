#!/usr/bin/env python3
"""Box-Test des Füllens in pbpipsel ("immer bereit", gst/gstpbpip.c) ohne Kameras und ohne laufende Sendung.

Aufbau wie in der Sendekette: je Kamera ein Sender (H.264 per RTP und PCM-Ton per UDP über Loopback, wie die Zubringer), eine Kette mit udpsrc,
Hardware-Decoder, Bild-Umschalter vsel, Ton-Umschalter asel und pbctl. Die Sender werden zu festen Zeiten gestoppt und gestartet, der Umschalter
auf eine Kamera gestellt, die noch nichts sendet. Geprüft wird am Ausgang: Zeitstempel ohne Rückwärtssprung und ohne Lücke, schwarze Bilder und
Stille, solange der gewählte Eingang fehlt, echte Bilder danach, und die Datei mit den Kameras mit Bild.

Aufruf auf der Box: boxtest_fill.py [Bausteinordner=/opt/pipbox/gst]
Schreibt in /var/tmp/fillt. Berührt weder /var/lib/pipbox noch laufende Dienste (eigene Ports 9400 bis 9407)."""
import os
import re
import shlex
import signal
import subprocess
import sys
import time

D = "/var/tmp/fillt"
PLUG = sys.argv[1] if len(sys.argv) > 1 else "/opt/pipbox/gst"
W, H, FPS = 640, 360, 30
FRAME = W * H * 3 // 2
VP = [9400, 9402]
AP = [9401, 9403]
os.makedirs(D, exist_ok=True)
for f in os.listdir(D):
    os.remove(os.path.join(D, f))
for f, v in (("select", "0 1 15 15"), ("view", "0 -1 0"), ("delay", "0 0 0 0")):
    open(f"{D}/{f}", "w").write(v + "\n")

VCAPS = "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000"
ACAPS = "audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved"
env = dict(os.environ, GST_PLUGIN_PATH=PLUG)
Q = "queue max-size-time=3000000000 max-size-buffers=1000 max-size-bytes=41943040"
parts = []
for i in range(2):
    parts.append(f'udpsrc port={VP[i]} buffer-size=8388608 do-timestamp=true caps="{VCAPS}" ! rtph264depay ! h264parse ! {Q} ! mppvideodec ! video/x-raw,format=NV12 ! vsel.sink_{i}')
    parts.append(f'udpsrc port={AP[i]} buffer-size=1048576 do-timestamp=true caps="{ACAPS}" ! {Q} ! asel.sink_{i}')
parts.append(f'pbpipsel name=vsel tag-offset=true force-key=true state=0 fill-caps="video/x-raw,format=NV12,width={W},height={H},framerate={FPS}/1" ! '
             f"identity name=vchk silent=false ! filesink location={D}/v.raw")
parts.append(f'pbpipsel name=asel state=0 fill-caps="{ACAPS}" ! identity name=achk silent=false ! filesink location={D}/a.raw')
parts.append(f"pbctl name=pbctl selector=vsel audio-selector=asel audio-pos=-1 cam0={Q.split()[0]} file={D}/delay select-file={D}/select state-file={D}/swap-state "
             f"view-file={D}/view view-state-file={D}/view-state live-file={D}/live")
main = subprocess.Popen(["gst-launch-1.0", "-v"] + shlex.split(" ".join(parts)), env=env, stdout=open(f"{D}/main.log", "w"), stderr=subprocess.STDOUT)


def sender(i, pattern):
    """Eine Kamera: Bild (H.264, RTP) und Ton (PCM) an die Ports der Kette."""
    cmd = (f"gst-launch-1.0 -q videotestsrc is-live=true pattern={pattern} ! video/x-raw,width={W},height={H},framerate={FPS}/1,format=NV12 ! "
           f"mpph264enc bitrate=2000000 gop=30 header-mode=each-idr ! h264parse ! rtph264pay config-interval=1 pt=96 ! udpsink host=127.0.0.1 port={VP[i]} sync=false "
           f"audiotestsrc is-live=true freq={440 + 220 * i} volume=0.5 ! {ACAPS} ! audiobuffersplit output-buffer-duration=1024/48000 ! udpsink host=127.0.0.1 port={AP[i]} sync=false")
    return subprocess.Popen(shlex.split(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def stop(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except OSError:
        pass
    p.wait(timeout=10)


def live():
    try:
        return open(f"{D}/live").read().split()
    except OSError:
        return []


events = []
t0 = time.time()
def mark(txt):
    events.append((time.time() - t0, txt, live()))
    print("%5.1f s  %s | Kameras mit Bild: %s" % (time.time() - t0, txt, " ".join(live())))

time.sleep(2)
mark("Start, noch keine Kamera")
s0 = sender(0, "white"); time.sleep(6); mark("Kamera 0 sendet (weiß)")
stop(s0); mark("Kamera 0 gestoppt"); time.sleep(5); mark("5 s ohne Kamera")
s0 = sender(0, "white"); time.sleep(5); mark("Kamera 0 wieder da")
open(f"{D}/select.tmp", "w").write("1 0 15 15\n"); os.replace(f"{D}/select.tmp", f"{D}/select")
time.sleep(4); mark("auf Kamera 1 gestellt, die nichts sendet")
s1 = sender(1, "ball"); time.sleep(5); mark("Kamera 1 sendet (Ball)")
stop(s1); stop(s0); time.sleep(1)
main.send_signal(signal.SIGINT); time.sleep(2)
try:
    main.wait(timeout=10)
except subprocess.TimeoutExpired:
    main.kill()


def ts(x):
    h, m, s = x.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def pts_list(name):
    out = []
    for l in open(f"{D}/main.log", errors="ignore"):
        m = re.search(rf"GstIdentity:{name}:.*?\((\d+) bytes, dts: ([\d:.]+|none), pts: ([\d:.]+|none), duration: ([\d:.]+|none)", l)
        if m and m.group(3) != "none":
            out.append((ts(m.group(3)), ts(m.group(4)) if m.group(4) != "none" else 0.0, int(m.group(1))))
    return out


ok = True
v = pts_list("vchk")
a = pts_list("achk")
print("\nBild-Puffer:", len(v), " Ton-Puffer:", len(a))
if not v or not a:
    print("FEHLER: keine Ausgabe")
    sys.exit(1)
vgaps = [(round(v[i][0], 2), round(v[i][0] - v[i - 1][0], 3)) for i in range(1, len(v)) if v[i][0] - v[i - 1][0] > 0.07 or v[i][0] < v[i - 1][0]]
agaps = [(round(a[i][0], 2), round(a[i][0] - (a[i - 1][0] + a[i - 1][1]), 3)) for i in range(1, len(a)) if abs(a[i][0] - (a[i - 1][0] + a[i - 1][1])) > 0.03 or a[i][0] < a[i - 1][0]]
print("Bild: Lücken über 70 ms oder rückwärts:", vgaps[:10], "(%d)" % len(vgaps))
print("Ton:  Unstetigkeiten über 30 ms oder rückwärts:", agaps[:10], "(%d)" % len(agaps))
span = v[-1][0] - v[0][0]
print("Bildrate gesamt: %.1f Bilder/s über %.1f s" % (len(v) / span, span))
ok = ok and not vgaps and len(v) / span > 27

# Helligkeit je Bild und Pegel des Tons über die Zeit
raw = open(f"{D}/v.raw", "rb").read()
n = len(raw) // FRAME
print("Bilder in der Datei:", n, "(Puffer:", len(v), ")")
luma = [raw[i * FRAME + 1000] for i in range(n)]            # ein Bildpunkt der Y-Ebene
t_first = v[0][0]
def phase(t0_, t1_):
    sel = [luma[i] for i in range(min(n, len(v))) if t0_ <= v[i][0] - t_first < t1_]
    return sel
def kind(sel):
    if not sel:
        return "keine"
    black = sum(1 for y in sel if y < 30)
    return "%d von %d schwarz" % (black, len(sel))
# Zeitachse der Ereignisse: relativ zum ersten Bild
off = events[1][0] - 0.0
for (t, txt, lv) in events[1:]:
    pass
print("Helligkeit (schwarz = Y unter 30), grobe Abschnitte seit dem ersten Bild:")
for lo in range(0, int(span), 3):
    print("  %2d bis %2d s: %s" % (lo, lo + 3, kind(phase(lo, lo + 3))))
aud = open(f"{D}/a.raw", "rb").read()
import struct
bps = 48000 * 2 * 2
print("Ton: Pegel je Sekunde (Spitze):", end=" ")
peaks = []
for sec in range(0, len(aud) // bps):
    chunk = aud[sec * bps:(sec + 1) * bps]
    pk = max(abs(x) for x in struct.unpack("<%dh" % (len(chunk) // 2), chunk[:len(chunk) // 2 * 2])[::97])
    peaks.append(pk)
print(peaks)
sys.exit(0 if ok else 1)
