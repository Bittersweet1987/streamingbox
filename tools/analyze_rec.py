#!/usr/bin/env python3
"""Wertet einen Mitschnitt (MPEG-TS vom SRT-Ausgang der Sendekette) aus: lückenlos? verschiedene Bilder je Sekunde? Inhalt der Bildflächen über die Zeit?

    python3 analyze_rec.py out.ts [--regions regions.json] [--png-at 5,20,40] [--png-dir dir]

Braucht ffmpeg und ffprobe (auf dem PC). Ausgabe: Kennzahlen, eine Tabelle je Sekunde (Bilder, verschiedene Bilder, Helligkeit der Flächen) und eine Liste
der Verstöße (Lücken über 100 ms, Sekunden mit weniger als 25 verschiedenen Bildern, Flächen, die länger als 1 s schwarz oder eingefroren sind,
während sie sichtbar sein sollen). regions.json: {"name": [x, y, w, h, [[von_s, bis_s], ...]]}: die Zeitfenster, in denen die Fläche Bild zeigen soll.
"""
import hashlib
import json
import subprocess
import sys

W, H = 192, 108                       # Auswertungsgröße (1/10 von 1920x1080)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def packets(path, kind):
    out = run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "packet=pts_time,size", "-of", "csv=p=0", path]).decode()
    pts = []
    for line in out.splitlines():
        a = line.split(",")
        try:
            pts.append(float(a[0]))
        except (ValueError, IndexError):
            pass
    return sorted(pts)


def gaps(pts, limit):
    return [(a, b - a) for a, b in zip(pts, pts[1:]) if b - a > limit]


def frames(path):
    """Alle Bilder als Graustufen W x H (Rohdaten), mit der Zeit je Bild aus ffprobe."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-an", "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-vsync", "passthrough", "-"],
                         capture_output=True, check=True).stdout
    n = len(raw) // (W * H)
    return [raw[i * W * H:(i + 1) * W * H] for i in range(n)]


def region_stats(frame, rect):
    x, y, w, h = (int(round(v / 10.0)) for v in rect)
    x, y, w, h = max(0, x), max(0, y), max(1, min(w, W - x)), max(1, min(h, H - y))
    rows = [frame[(y + r) * W + x:(y + r) * W + x + w] for r in range(h)]
    flat = b"".join(rows)
    mean = sum(flat) / len(flat)
    var = sum((v - mean) ** 2 for v in flat) / len(flat)
    return mean, var, hashlib.md5(flat).hexdigest()


def main():
    path = sys.argv[1]
    regions = {}
    png_at, png_dir = [], "."
    args = sys.argv[2:]
    while args:
        a = args.pop(0)
        if a == "--regions":
            regions = json.load(open(args.pop(0)))
        elif a == "--png-at":
            png_at = [float(x) for x in args.pop(0).split(",") if x]
        elif a == "--png-dir":
            png_dir = args.pop(0)
    vp, ap = packets(path, "v"), packets(path, "a")
    t0 = min(vp[0], ap[0] if ap else vp[0])
    dur = vp[-1] - vp[0]
    print(f"Video: {len(vp)} Pakete, {dur:.1f} s, {len(vp) / max(dur, 1e-9):.2f} Bilder/s, Audio: {len(ap)} Pakete")
    vg, ag = gaps(vp, 0.1), gaps(ap, 0.1)
    print(f"Lücken im Video > 100 ms: {len(vg)} (größte {max([g for _, g in vg], default=0):.3f} s); im Audio > 100 ms: {len(ag)} (größte {max([g for _, g in ag], default=0):.3f} s)")
    for t, g in vg[:30]:
        print(f"  Video-Lücke bei {t - t0:7.2f} s: {g * 1000:.0f} ms")
    for t, g in ag[:30]:
        print(f"  Audio-Lücke bei {t - t0:7.2f} s: {g * 1000:.0f} ms")
    fr = frames(path)
    n = min(len(fr), len(vp))
    print(f"Bilder dekodiert: {len(fr)}")
    secs = {}
    prev = None
    for i in range(n):
        s = int(vp[i] - vp[0])
        d = secs.setdefault(s, {"n": 0, "new": 0, "reg": {k: [] for k in regions}})
        d["n"] += 1
        if prev is None or fr[i] != prev:
            d["new"] += 1
        prev = fr[i]
        for name, spec in regions.items():
            d["reg"][name].append(region_stats(fr[i], spec[:4]))
    bad = []
    print("\n s  Bilder neu  " + "  ".join(f"{k:>14}" for k in regions))
    for s in sorted(secs):
        d = secs[s]
        cells = []
        for name, spec in regions.items():
            st = d["reg"][name]
            mean = sum(x[0] for x in st) / len(st)
            var = sum(x[1] for x in st) / len(st)
            changing = len({x[2] for x in st}) > 1
            cells.append(f"{mean:5.0f}/{var:5.0f}{'~' if changing else ' '}")
            for lo, hi in (spec[4] if len(spec) > 4 else []):
                if lo + 2 <= s <= hi - 2 and (var < 20 or not changing):
                    bad.append((s, name, "schwarz/leer" if var < 20 else "eingefroren"))
        print(f"{s:3d}  {d['n']:5d} {d['new']:4d}   " + "  ".join(f"{c:>14}" for c in cells))
        if d["new"] < 25 and 0 < s < max(secs):
            bad.append((s, "Ausgang", f"nur {d['new']} verschiedene Bilder"))
    print("\nVerstöße:")
    for b in bad:
        print("  ", b)
    print("  keine" if not bad else f"  {len(bad)} Stück")
    for t in png_at:
        out = f"{png_dir}/rec_{int(t):04d}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", path, "-frames:v", "1", out], check=True)
        print("Bild:", out)
    return 1 if (vg or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
