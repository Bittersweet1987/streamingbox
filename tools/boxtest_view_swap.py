#!/usr/bin/env python3
"""Test pbctl mit Umschalter (Tausch ohne Unterbrechung): Tonquelle im Betrieb wechseln, zusammen mit Tausch, Ausblenden und Stumm. Auf der Box ausführen."""
import glob, os, signal, subprocess, sys, time, struct, math
D = "/var/tmp/pbtest"
ENV = dict(os.environ, GST_PLUGIN_PATH=D)
W, H = 640, 360
res = []

def check(name, ok, info=""):
    res.append(ok); print(("PASS " if ok else "FAIL ") + name + (" | " + info if info else ""), flush=True)

def put(path, text):
    tmp = path + ".tmp"; open(tmp, "w").write(text + "\n"); os.replace(tmp, path)

def state(name):
    try: return open(D + "/" + name).read().strip()
    except OSError: return None

def wait_state(name, want, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if state(name) == want: return True
        time.sleep(0.1)
    return False

def last_frame():
    for f in reversed(sorted(glob.glob(D + "/v*.raw"), key=os.path.getmtime)[-5:]):
        d = open(f, "rb").read()
        if len(d) == W * H: return d
    return None

def px(frame):
    return frame[(H - 60) * W + (W - 90)], frame[(H // 2) * W + W // 2]       # (kleines Bild unten rechts, Mitte des Hauptbildes)

def freq(secs=0.5):
    n = int(48000 * 2 * 2 * secs)
    d = open(D + "/a.raw", "rb").read()[-n:]
    s = struct.unpack("<%dh" % (len(d) // 2), d[: len(d) // 2 * 2])
    left = s[0::2]
    cross = sum(1 for a, b in zip(left, left[1:]) if (a < 0) != (b < 0))
    rms = math.sqrt(sum(x * x for x in left) / max(1, len(left)))
    return cross / 2.0 / secs, rms

def near(f, target): return abs(f - target) < 60

for f in glob.glob(D + "/v*.raw") + glob.glob(D + "/a.raw") + [D + "/sel", D + "/sel-state", D + "/view", D + "/view-state"]:
    try: os.remove(f)
    except OSError: pass
put(D + "/sel", "0 1 15 15")
caps_v = "video/x-raw,format=NV12,width=640,height=360,framerate=30/1"
caps_s = "video/x-raw,format=NV12,width=160,height=90,framerate=30/1"
caps_a = "audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved"
pipe = ("pbpipsel name=vsel tag-offset=true force-key=true state=%d ! identity name=v_delay signal-handoffs=TRUE ! video/x-raw,format=NV12 ! "
        "pbpipmix name=pipmix follow-tag=true corner=3 width-pct=25 ! videoconvert ! video/x-raw,format=GRAY8 ! multifilesink location=%s/v%%05d.raw max-files=40  "
        "videotestsrc is-live=true pattern=black ! %s ! vsel.sink_0  videotestsrc is-live=true pattern=white ! %s ! vsel.sink_1  "
        "videotestsrc is-live=true pattern=black ! %s ! pbpipsink slot=3  videotestsrc is-live=true pattern=white ! %s ! pbpipsink slot=0  "
        "audiotestsrc is-live=true wave=sine freq=440 volume=0.5 ! %s ! asel.sink_0  audiotestsrc is-live=true wave=sine freq=880 volume=0.5 ! %s ! asel.sink_1  "
        "pbpipsel name=asel state=0 ! volume name=avol ! filesink location=%s/a.raw  "
        "pbctl name=pbctl selector=vsel audio-selector=asel audio-pos=-1 select-file=%s/sel state-file=%s/sel-state view-file=%s/view view-state-file=%s/view-state") % (
    0 | 1 << 4 | 15 << 8 | 15 << 12, D, caps_v, caps_v, caps_s, caps_s, caps_a, caps_a, D, D, D, D, D)
p = subprocess.Popen(["gst-launch-1.0", "-q"] + pipe.split(), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(2.5)

fr = last_frame(); f, r = freq()
check("1 Anfang: Hauptbild schwarz, kleines Bild weiß (Kamera 2), Ton folgt dem Hauptbild (Kamera 1: 440 Hz)", px(fr)[0] > 200 and px(fr)[1] < 30 and near(f, 440) and r > 3000, "%s %.0f Hz %.0f" % (px(fr), f, r))
check("1b Anfangszustand gemeldet", state("view-state") == "0 -1 0", str(state("view-state")))

put(D + "/view", "0 0 0")                                        # Ton von der Kamera an Stelle 1 (= Kamera 2, 880 Hz)
check("2a Tonquelle 0 gemeldet", wait_state("view-state", "0 0 0"), str(state("view-state")))
time.sleep(0.7); f, r = freq(0.4)
check("2b Ton ist jetzt Kamera 2 (880 Hz), Bild unverändert", near(f, 880) and r > 3000 and px(last_frame())[0] > 200, "%.0f Hz %.0f" % (f, r))

put(D + "/view", "0 -1 0")
check("3a zurück auf Hauptbild gemeldet", wait_state("view-state", "0 -1 0"), str(state("view-state")))
time.sleep(0.7); f, r = freq(0.4)
check("3b Ton wieder Kamera 1 (440 Hz)", near(f, 440), "%.0f Hz" % f)

put(D + "/sel", "1 0 15 15")                                     # Tausch: Kamera 2 wird Hauptbild, Kamera 1 klein; Ton folgt dem Hauptbild
check("4a Tausch gemeldet", wait_state("sel-state", "1 0 15 15"), str(state("sel-state")))
time.sleep(0.8); fr = last_frame(); f, r = freq(0.4)
check("4b nach Tausch: Hauptbild weiß, Ton Kamera 2 (880 Hz)", px(fr)[1] > 200 and near(f, 880), "%s %.0f Hz" % (px(fr), f))

put(D + "/view", "0 0 0")                                        # Ton von Stelle 1 = jetzt Kamera 1 (440 Hz)
wait_state("view-state", "0 0 0"); time.sleep(0.7); f, r = freq(0.4)
check("5 nach Tausch: Tonquelle Stelle 1 = Kamera 1 (440 Hz)", near(f, 440), "%.0f Hz" % f)

put(D + "/sel", "0 1 15 15")                                     # zurücktauschen: Ton bleibt an Stelle 1 = Kamera 2 (880 Hz)
wait_state("sel-state", "0 1 15 15"); time.sleep(0.8); f, r = freq(0.4)
check("6 Rücktausch: Ton bleibt an Stelle 1 (Kamera 2, 880 Hz)", near(f, 880), "%.0f Hz" % f)

put(D + "/view", "1 0 1")                                        # kleines Bild aus und stumm zusammen
check("7a ausgeblendet und stumm gemeldet", wait_state("view-state", "1 0 1"), str(state("view-state")))
time.sleep(0.8); fr = last_frame(); f, r = freq(0.4)
check("7b Bild weg und Ton stumm", px(fr)[0] < 30 and r < 50, "%s rms %.1f" % (px(fr), r))
put(D + "/view", "0 -1 0")
wait_state("view-state", "0 -1 0"); time.sleep(0.8); fr = last_frame(); f, r = freq(0.4)
check("8 alles zurück: Bild da, Ton Kamera 1 (440 Hz)", px(fr)[0] > 200 and near(f, 440) and r > 3000, "%s %.0f Hz %.0f" % (px(fr), f, r))
p.send_signal(signal.SIGINT)
try: out = p.communicate(timeout=5)[0].decode("utf-8", "replace")
except subprocess.TimeoutExpired:
    p.kill(); out = p.communicate()[0].decode("utf-8", "replace")
check("9 kein Absturz, keine Fehlermeldung", "ERROR" not in out and "Segmentation" not in out, out[:200].replace("\n", " "))
print("ERGEBNIS: %d von %d bestanden" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
