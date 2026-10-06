#!/usr/bin/env python3
"""Aufzeichnung des Ausgangs (MPEG-TS, z. B. aus tools/boxtest_always_send.py) Bild für Bild prüfen: Läuft das Bild über die ganze Dauer, ohne Ausfälle?

Dekodiert die Datei mit GStreamer auf 192x108 Graustufen und meldet je Zeitfenster (Standard 10 s):
  fps         Bilder je Sekunde laut Zeitstempel, größte Lücke zwischen zwei Bildern
  schwarz     Anteil ganz schwarzer Bilder (alle Bildpunkte unter 24) am Fenster
  ecke        je Ecke (ol, or, ul, ur): Anteil der Bilder, in denen ein Viertelbreite mal Viertelhöhe großer Bereich in der Ecke schwarz ist (kleines Bild fehlt)
  steht       längste Folge völlig gleicher Bilder (Standbild) in Bildern
Danach eine Zusammenfassung mit allen auffälligen Fenstern. Außerdem schreibt es mit --jpg DIR alle 30 s ein Standbild (640x360) zum Ansehen.

Aufruf: analyze_stream.py out.ts [--fenster 10] [--jpg ordner]      (auf der Box, mit nice, braucht gst-launch-1.0 und den Hardware-Decoder oder avdec_h265; die Sendekette kodiert HEVC)"""
import os
import re
import subprocess
import sys
import threading

args = sys.argv[1:]
if not args:
    sys.exit(__doc__)
path = args[0]
win = float(args[args.index("--fenster") + 1]) if "--fenster" in args else 10.0
jpg = args[args.index("--jpg") + 1] if "--jpg" in args else None
W, H = 192, 108
FR = W * H
DEC = "mppvideodec" if subprocess.run(["gst-inspect-1.0", "mppvideodec"], capture_output=True).returncode == 0 else "avdec_h265"
cmd = (f"gst-launch-1.0 -v filesrc location={path} ! tsdemux name=d d. ! video/x-h265 ! h265parse ! {DEC} ! videoconvert ! videoscale ! video/x-raw,format=GRAY8,width={W},height={H} ! "
       f"identity name=t silent=false ! fdsink fd=1 sync=false").split()
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
pts = []
def reader():
    for line in p.stderr:
        m = re.search(rb"pts: (\d+):(\d+):(\d+\.\d+)", line)
        if m and b"GstIdentity:t" in line:
            h, mi, s = m.groups()
            pts.append(int(h) * 3600 + int(mi) * 60 + float(s))
threading.Thread(target=reader, daemon=True).start()

def corner_black(buf, c):
    rows = range(0, H // 4) if c[0] == "o" else range(H - H // 4, H)
    x0, x1 = (0, W // 4) if c[1] == "l" else (W - W // 4, W)
    return all(max(buf[r * W + x0:r * W + x1]) < 24 for r in rows)

CORNERS = ("ol", "or", "ul", "ur")
frames = []                  # (pts, ganz schwarz, [Ecke schwarz], gleich wie voriges)
prev = None
n = 0
while True:
    buf = p.stdout.read(FR)
    if len(buf) < FR:
        break
    black = max(buf) < 24
    same = buf == prev
    frames.append((black, [corner_black(buf, c) for c in CORNERS], same))
    prev = buf
    n += 1
p.wait()
t = pts[:len(frames)]
if len(t) < len(frames):
    print(f"Warnung: nur {len(t)} Zeitstempel für {len(frames)} Bilder; ohne Zeitstempel wird 30 Bilder/s angenommen")
    t = [i / 30 for i in range(len(frames))]
if not frames:
    sys.exit("keine Bilder dekodiert")
t0 = t[0]
print(f"{len(frames)} Bilder, {t[-1] - t0:.1f} s, Decoder {DEC}")
print("Fenster      fps  größte Lücke  schwarz  Ecke ol/or/ul/ur (schwarz)   steht (Bilder)")
bad = []
i = 0
while i < len(frames):
    j = i
    while j < len(frames) and t[j] - t0 < (int((t[i] - t0) // win) + 1) * win:
        j += 1
    seg = frames[i:j]
    ts = t[i:j]
    span = max(ts[-1] - ts[0], 0.001)
    fps = (len(seg) - 1) / span if len(seg) > 1 else 0
    gap = max((b - a for a, b in zip(ts, ts[1:])), default=0)
    bl = sum(1 for f in seg if f[0]) / len(seg)
    cs = [sum(1 for f in seg if f[1][k]) / len(seg) for k in range(4)]
    run = longest = 0
    for f in seg:
        run = run + 1 if f[2] else 0
        longest = max(longest, run)
    lo = int((ts[0] - t0) // win) * int(win)
    print("%4d-%4d s  %5.1f  %6.2f s     %4.0f%%   %s   %d" % (lo, lo + int(win), fps, gap, bl * 100, "/".join("%3.0f%%" % (c * 100) for c in cs), longest))
    if fps < 27 or gap > 0.2 or bl > 0.02 or longest > 15:
        bad.append((lo, fps, gap, bl, longest))
    i = j
print("\nAuffällige Fenster (unter 27 Bilder/s, Lücke über 0,2 s, schwarze Bilder über 2 %, Standbild über 15 Bilder):", bad if bad else "keine")
if jpg:
    os.makedirs(jpg, exist_ok=True)
    c2 = (f"gst-launch-1.0 -q filesrc location={path} ! tsdemux name=d d. ! video/x-h265 ! h265parse ! {DEC} ! videoconvert ! videorate ! video/x-raw,framerate=1/30 ! videoscale ! "
          f"video/x-raw,width=640,height=360 ! jpegenc ! multifilesink location={jpg}/bild_%03d.jpg").split()
    subprocess.run(c2, check=False)
    print("Standbilder:", jpg)
