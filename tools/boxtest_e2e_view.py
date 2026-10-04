#!/usr/bin/env python3
"""Ende-zu-Ende-Test der Ansicht im Betrieb (Issue #19) mit der ECHTEN Sendekette: Pipeline-Text aus server.py (so, wie ihn die Box ab
0.9.78 baut), vier RTMP-Quellen am lokalen nginx (Testbilder in Vollfarben, Testton in vier Lautstärken, kodiert wie von einer Kamera:
H.264 und AAC), echter Hardware-Decoder und -Encoder, der echte Baustein pbctl. Nur belacoder, SRT und die Dienste fehlen: der Ausgang ist eine
Datei. Danach wird die Datei dekodiert und geprüft, welche Bilder zu sehen und welche Tonspur zu hören war.

Auf der Box ausführen (nicht während einer Sendung): python3 boxtest_e2e_view.py swap|classic [--keep]
Schreibt nur nach /var/tmp/e2e, fasst /var/lib/pipbox und /run/pipbox-send nicht an. Eigene RTMP-Schlüssel e2e-a bis e2e-d.
Echte Kameras ersetzt das nicht (andere Encoder, Profile, Tonformat)."""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True                                          # nichts in /opt/pipbox ablegen
sys.path.insert(0, "/opt/pipbox")
import server  # noqa: E402

server.VIEW_ENABLED = True                                              # so, wie ab 0.9.78

D = "/var/tmp/e2e"
KEYS = ["e2e-a", "e2e-b", "e2e-c", "e2e-d"]
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]       # Kamera A rot (Hauptbild), B grün, C blau, D gelb
GAINS = [0.8, 0.4, 0.2, 0.1]                                           # Tonpegel je Kamera: -4,9 / -10,9 / -16,9 / -22,9 dB (Sinus)
DB = [20 * __import__("math").log10(g * 0.7071) for g in GAINS]
HOLD = 8.0                                                              # so lange bleibt jeder Schritt stehen
FPS = 2
MODE = (sys.argv[1] if len(sys.argv) > 1 else "swap")
KEEP = "--keep" in sys.argv
procs = []
res = []


def check(name, ok, info=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" | " + info if info else ""), flush=True)


def spawn(args, **kw):
    p = subprocess.Popen(args, **kw)
    procs.append(p)
    return p


def put(name, text):
    tmp = f"{D}/{name}.tmp"
    with open(tmp, "w") as f:
        f.write(text + "\n")
    os.replace(tmp, f"{D}/{name}")


def read(name):
    try:
        return open(f"{D}/{name}").read().split()
    except OSError:
        return None


def wait_for(name, want, t=3.0):
    end = time.monotonic() + t
    t0 = time.monotonic()
    while time.monotonic() < end:
        if read(name) == want.split():
            return time.monotonic() - t0
        time.sleep(0.05)
    return None


def publisher(i):
    c = COLORS[i]
    argb = 0xff000000 | c[0] << 16 | c[1] << 8 | c[2]
    args = ["gst-launch-1.0", "-q",
            "videotestsrc", "is-live=true", "pattern=solid-color", f"foreground-color={argb}", "!",
            "video/x-raw,width=1280,height=720,framerate=30/1", "!", "mpph264enc", "!", "h264parse", "!", "flvmux", "name=m", "streamable=true", "!",
            "rtmpsink", f'location="rtmp://127.0.0.1:1935/publish/{KEYS[i]} live=1"',
            "audiotestsrc", "is-live=true", "wave=sine", "freq=440", f"volume={GAINS[i]}", "!", "audio/x-raw,rate=48000,channels=2", "!",
            "audioconvert", "!", "voaacenc", "bitrate=128000", "!", "aacparse", "!", "m."]
    return spawn(args, stdout=subprocess.DEVNULL, stderr=open(f"{D}/pub{i}.log", "w"))


def config():
    cfg = dict(server.PipelineStore.DEFAULT)
    cfg.update(type="pip", main=KEYS[0], pip=KEYS[1], pip2=KEYS[2], pip3=KEYS[3], corner=3, corner2=2, corner3=0, size_pct=25, size_pct2=25,
               size_pct3=25, audio="main", swap_cams=4 if MODE == "swap" else 0, main_delay_ms=300, pip_delay_ms=300, pip2_delay_ms=300, pip3_delay_ms=300)
    return cfg


def pipeline_text(cfg):
    text = server.PipelineStore(os.devnull).build(cfg)
    assert "appsink name=appsink" in text and "pbctl name=pbctl" in text and "volume name=avol" in text, "Pipeline-Text ohne pbctl/avol"
    text = text.replace("appsink name=appsink", f"filesink location={D}/out.ts")
    paths = f"file={D}/delay select-file={D}/select state-file={D}/swap-state view-file={D}/view view-state-file={D}/view-state"
    return text.replace("pbctl name=pbctl", "pbctl name=pbctl " + paths)


def frames_colors(path_glob_dir):
    """Je Bild: Anteil der Bildpunkte in der Nähe von A bis D."""
    out = []
    for f in sorted(os.listdir(path_glob_dir)):
        if not (f.startswith("f") and f.endswith(".rgb")):
            continue
        d = open(f"{path_glob_dir}/{f}", "rb").read()
        n = len(d) // 3
        cnt = [0, 0, 0, 0]
        for k in range(0, n):
            r, g, b = d[3 * k], d[3 * k + 1], d[3 * k + 2]
            for j, (cr, cg, cb) in enumerate(COLORS):
                if (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2 < 90 * 90:
                    cnt[j] += 1
                    break
        out.append([c / n for c in cnt])
    return out


def audio_levels():
    r = subprocess.run(["gst-launch-1.0", "-m", "filesrc", f"location={D}/out.ts", "!", "tsdemux", "name=d", "d.", "!", "queue", "!", "opusdec", "!",
                        "audioconvert", "!", "level", "interval=500000000", "post-messages=true", "!", "fakesink"],
                       capture_output=True, text=True, timeout=120)
    open(f"{D}/level.txt", "w").write(r.stdout[:3000] + "\n---\n" + r.stderr[:1000])
    lv = []
    for m in re.finditer(r"endtime=\(guint64\)(\d+).*?rms=\(GValueArray\)< ([^,>]+)", r.stdout):
        try:
            lv.append((int(m.group(1)) / 1e9, float(m.group(2))))
        except ValueError:
            lv.append((int(m.group(1)) / 1e9, -200.0))
    return lv


def decode_frames():
    shutil.rmtree(f"{D}/fr", ignore_errors=True)
    os.makedirs(f"{D}/fr")
    subprocess.run(["gst-launch-1.0", "-q", "filesrc", f"location={D}/out.ts", "!", "tsdemux", "!", "h265parse", "!", "mppvideodec", "!", "videoconvert", "!",
                    "videorate", "!", f"video/x-raw,framerate={FPS}/1", "!", "videoscale", "!", "video/x-raw,width=160,height=90", "!", "videoconvert", "!",
                    "video/x-raw,format=RGB", "!", "multifilesink", f"location={D}/fr/f%04d.rgb"], capture_output=True, timeout=180)
    return frames_colors(f"{D}/fr")


def main():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    cfg = config()
    text = pipeline_text(cfg)
    open(f"{D}/pipeline.txt", "w").write(text)
    for i in range(4):
        publisher(i)
    time.sleep(4)
    check("vier Testquellen senden an nginx", all(p.poll() is None for p in procs), "; ".join(open(f"{D}/pub{i}.log").read().strip()[:80] for i in range(4)))
    put("view", server.PipelineStore.view_line(cfg))
    if MODE == "swap":
        put("select", server.PipelineStore.swap_plan(cfg)["line"])
    put("delay", " ".join(map(str, server.PipelineStore.delay_values(cfg))))
    env = dict(os.environ, GST_PLUGIN_PATH="/opt/pipbox/gst")
    log = open(f"{D}/chain.log", "w")
    chain = spawn(["gst-launch-1.0", "-e"] + shlex.split(text), env=env, stdout=log, stderr=subprocess.STDOUT)
    t0 = time.monotonic()
    tg = None
    while time.monotonic() - t0 < 30:
        if chain.poll() is not None:
            break
        try:
            if os.path.getsize(f"{D}/out.ts") > 100000:
                tg = time.monotonic()
                break
        except OSError:
            pass
        time.sleep(0.05)
    check("Sendekette läuft und schreibt Ausgabe", tg is not None and chain.poll() is None, "" if tg else open(f"{D}/chain.log").read()[-400:])
    if tg is None:
        return
    check("pbctl meldet die Anfangsansicht", wait_for("view-state", server.PipelineStore.view_line(cfg), 5) is not None, str(read("view-state")))
    steps = [("Anfang: alles sichtbar, Ton Hauptbild", None, "0 -1 0", None, dict(vis=[1, 1, 1, 1], main=0, aud=0))]
    if MODE == "swap":
        steps += [
            ("Klein 1 ausgeblendet", None, "1 -1 0", None, dict(vis=[1, 0, 1, 1], main=0, aud=0)),
            ("alle kleinen Bilder aus", None, "7 -1 0", None, dict(vis=[1, 0, 0, 0], main=0, aud=0)),
            ("alle wieder an, Ton von Klein 1", None, "0 0 0", None, dict(vis=[1, 1, 1, 1], main=0, aud=1)),
            ("Ton von Klein 2", None, "0 1 0", None, dict(vis=[1, 1, 1, 1], main=0, aud=2)),
            ("Ton von Klein 3", None, "0 2 0", None, dict(vis=[1, 1, 1, 1], main=0, aud=3)),
            ("stumm", None, "0 2 1", None, dict(vis=[1, 1, 1, 1], main=0, aud="stumm")),
            ("wieder laut", None, "0 2 0", None, dict(vis=[1, 1, 1, 1], main=0, aud=3)),
            ("Tausch Hauptbild mit Klein 1, Ton folgt dem Hauptbild", "1 0 2 3", "0 -1 0", None, dict(vis=[1, 1, 1, 1], main=1, aud=1)),
            ("zurücktauschen, Klein 1 ausgeblendet", "0 1 2 3", "1 -1 0", None, dict(vis=[1, 0, 1, 1], main=0, aud=0)),
        ]
    else:
        steps += [
            ("Klein 1 ausgeblendet", None, "1 -1 0", None, dict(vis=[1, 0, 1, 1], main=0, aud=0)),
            ("alle kleinen Bilder aus", None, "7 -1 0", None, dict(vis=[1, 0, 0, 0], main=0, aud=0)),
            ("alle wieder an", None, "0 -1 0", None, dict(vis=[1, 1, 1, 1], main=0, aud=0)),
            ("stumm", None, "0 -1 1", None, dict(vis=[1, 1, 1, 1], main=0, aud="stumm")),
            ("wieder laut", None, "0 -1 0", None, dict(vis=[1, 1, 1, 1], main=0, aud=0)),
        ]
    times = [(steps[0], 0.0)]
    time.sleep(HOLD - (time.monotonic() - tg) if HOLD > time.monotonic() - tg else 0)
    for st in steps[1:]:
        name, sel, view, _, exp = st
        w = time.monotonic()
        if sel:
            put("select", sel)
            a = wait_for("swap-state", sel, 4)
            check(f"{name}: pbctl bestätigt den Tausch", a is not None, f"{a and round(a, 2)} s, Zustand {read('swap-state')}")
        put("view", view)
        a = wait_for("view-state", view, 4)
        check(f"{name}: pbctl bestätigt die Ansicht", a is not None, f"{a and round(a, 2)} s, Zustand {read('view-state')}")
        times.append((st, w - tg))
        time.sleep(HOLD)
        if chain.poll() is not None:
            check("Sendekette läuft weiter", False, open(f"{D}/chain.log").read()[-400:])
            return
    check("Sendekette läuft nach allen Änderungen ohne Neustart weiter", chain.poll() is None)
    chain.send_signal(signal.SIGINT)
    try:
        chain.wait(timeout=15)
    except subprocess.TimeoutExpired:
        chain.kill()
    txt = open(f"{D}/chain.log").read()
    check("keine Fehlermeldung der Sendekette", "ERROR" not in txt and "WARNING: erroneous" not in txt, "" if "ERROR" not in txt else txt[-300:])
    for p in procs:
        if p.poll() is None and p is not chain:
            p.terminate()
    with open(f"{D}/times.json", "w") as f:
        json.dump([[st[0], st[4], t] for st, t in times], f)
    analyze(times)


def analyze(times):
    # Auswertung der Ausgabedatei
    fr = decode_frames()
    lv = audio_levels()
    with open(f"{D}/series.json", "w") as f:
        json.dump({"frames": fr, "levels": lv}, f)
    check("Ausgabe enthält Bild und Ton", len(fr) > 10 and len(lv) > 10, f"{len(fr)} Bilder, {len(lv)} Pegelwerte")
    # Die Zeit in der Ausgabedatei läuft der Uhr voraus (beim Start liefert die Kette erst einen Rückstau schneller als in Echtzeit):
    # aus dem ersten Schritt, der das Bild von Kamera B ausblendet, den Versatz ablesen. Der Ton liegt etwa 0,7 s später.
    t2 = times[1][1]
    hit = next((k / FPS for k, f in enumerate(fr) if k / FPS > t2 and f[1] < 0.008), None)
    check("Versatz zwischen Schreibzeit und Ausgabe bestimmbar", hit is not None and 0 <= hit - t2 < 8, "Bild wirkt %s s nach dem Schreiben" % (hit and round(hit - t2, 1)))
    if hit is None:
        return
    off_v = hit - t2
    for name, exp, t in [(a[0][0], a[0][4], a[1]) for a in times]:
        lo, hi = t + off_v + 1.5, t + off_v + 6.5                             # Fenster in der Mitte des Schritts (nach der Umstellung, vor der nächsten)
        sel_f = [f for k, f in enumerate(fr) if lo <= k / FPS + 0.25 <= hi]
        sel_a = sorted(v for (tt, v) in lv if lo + 0.7 <= tt <= hi + 0.7)
        if not sel_f or not sel_a:
            check(f"{name}: Auswertung", False, "keine Daten im Fenster")
            continue
        avg = [sum(f[j] for f in sel_f) / len(sel_f) for j in range(4)]
        big = max(range(4), key=lambda j: avg[j])
        ok = big == exp["main"] and avg[big] > 0.5
        for j in range(4):
            if j == exp["main"]:
                continue
            ok = ok and ((avg[j] > 0.025) if exp["vis"][j] else (avg[j] < 0.008))
        check(f"{name}: Bild", ok, "Anteil rot/grün/blau/gelb " + "/".join(f"{x * 100:.1f}%" for x in avg))
        med = sel_a[len(sel_a) // 2]
        if exp["aud"] == "stumm":
            ok = med < -60
        else:
            ok = abs(med - DB[exp["aud"]]) < 3.5
        check(f"{name}: Ton", ok, f"Pegel {med:.1f} dB (erwartet {'stumm' if exp['aud'] == 'stumm' else round(DB[exp['aud']], 1)})")


try:
    if MODE == "analyze":                                                   # nur die Auswertung wiederholen (nach --keep)
        saved = json.load(open(f"{D}/times.json"))
        MODE = "swap"
        analyze([((n, None, None, None, e), t) for n, e, t in saved])
    else:
        main()
finally:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    time.sleep(1)
    for p in procs:
        if p.poll() is None:
            p.kill()
    if not KEEP:
        shutil.rmtree(f"{D}/fr", ignore_errors=True)
        try:
            os.remove(f"{D}/out.ts")
        except OSError:
            pass
    print("%d von %d Prüfungen bestanden" % (sum(res), len(res)))
    sys.exit(0 if res and all(res) else 1)
