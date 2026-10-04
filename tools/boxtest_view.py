#!/usr/bin/env python3
"""Test des Bausteins pbctl (Ansicht im Betrieb) mit Testbildern und Testton, ohne Kameras, ohne Installation. Auf der Box ausführen."""
import glob, os, signal, subprocess, sys, time, struct, math
D = "/var/tmp/pbtest"
ENV = dict(os.environ, GST_PLUGIN_PATH=D)
W, H = 640, 360
res = []

def check(name, ok, info=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" | " + info if info else ""), flush=True)

def clean():
    for f in glob.glob(D + "/v*.raw") + glob.glob(D + "/a*.raw") + [D + "/view", D + "/view-state"]:
        try: os.remove(f)
        except OSError: pass

def put(text):
    tmp = D + "/view.tmp"
    open(tmp, "w").write(text + "\n"); os.replace(tmp, D + "/view")

def state():
    try: return open(D + "/view-state").read().strip()
    except OSError: return None

def wait_state(want, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if state() == want: return True
        time.sleep(0.1)
    return False

def last_frame():
    fs = sorted(glob.glob(D + "/v*.raw"), key=os.path.getmtime)
    for f in reversed(fs[-5:]):
        d = open(f, "rb").read()
        if len(d) == W * H: return d
    return None

def corner_value(frame):                                 # Mitte des kleinen Bildes unten rechts und Mitte des Hauptbildes
    return frame[(H - 60) * W + (W - 90)], frame[(H // 2) * W + W // 2]

def audio_rms(secs=0.3):
    f = D + "/a.raw"
    n = int(48000 * 2 * 2 * secs)
    d = open(f, "rb").read()[-n:]
    s = struct.unpack("<%dh" % (len(d) // 2), d[: len(d) // 2 * 2])
    return math.sqrt(sum(x * x for x in s) / max(1, len(s)))

def run(pipeline):
    clean()
    p = subprocess.Popen(["gst-launch-1.0", "-q"] + pipeline.split("\n"), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p

def stop(p):
    p.send_signal(signal.SIGINT)
    try: out = p.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        p.kill(); out = p.communicate()[0]
    return out.decode("utf-8", "replace")

def args(s):
    return s.split()

BASE = ("videotestsrc is-live=true pattern=black ! video/x-raw,format=NV12,width=%d,height=%d,framerate=30/1 ! "
        "pbpipmix name=pipmix corner=3 width-pct=25 %s ! videoconvert ! video/x-raw,format=GRAY8 ! multifilesink location=" + D + "/v%%05d.raw max-files=40  "
        "videotestsrc is-live=true pattern=white ! video/x-raw,format=NV12,width=160,height=90,framerate=30/1 ! pbpipsink slot=0  "
        "audiotestsrc is-live=true wave=sine freq=440 volume=0.5 ! audio/x-raw,format=S16LE,rate=48000,channels=2 ! volume name=avol ! filesink location=" + D + "/a.raw  "
        "pbctl name=pbctl view-file=" + D + "/view view-state-file=" + D + "/view-state")

def launch(style=""):
    clean()
    cmd = ["gst-launch-1.0", "-q"] + (BASE % (W, H, style)).split()
    return subprocess.Popen(cmd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

# ---------------------------------------------------------------- 1: ohne Datei, nichts verändert, Zustand wird gemeldet
p = launch()
time.sleep(2.0)
st = state(); fr = last_frame()
check("1a Start ohne Datei meldet Anfangszustand", st == "0 -1 0", str(st))
check("1b kleines Bild ist sichtbar (weiß), Hauptbild schwarz", fr is not None and corner_value(fr)[0] > 200 and corner_value(fr)[1] < 30, str(corner_value(fr) if fr else None))
check("1c Ton läuft", audio_rms() > 3000, "%.0f" % audio_rms())
# ---------------------------------------------------------------- 2: ausblenden
put("1 -1 0")
check("2a Zustand 1 -1 0 gemeldet", wait_state("1 -1 0"), str(state()))
time.sleep(0.6); fr = last_frame()
check("2b kleines Bild ist weg (schwarz)", fr is not None and corner_value(fr)[0] < 30, str(corner_value(fr) if fr else None))
# ---------------------------------------------------------------- 3: wieder einblenden
put("0 -1 0")
check("3a Zustand 0 -1 0 gemeldet", wait_state("0 -1 0"), str(state()))
time.sleep(0.8); fr = last_frame()
check("3b kleines Bild ist wieder da (weiß)", fr is not None and corner_value(fr)[0] > 200, str(corner_value(fr) if fr else None))
# ---------------------------------------------------------------- 4: stumm
put("0 -1 1")
check("4a Zustand 0 -1 1 gemeldet", wait_state("0 -1 1"), str(state()))
time.sleep(0.8)
check("4b Ton ist stumm", audio_rms(0.3) < 50, "%.1f" % audio_rms(0.3))
put("0 -1 0")
check("4c entstummt gemeldet", wait_state("0 -1 0"), str(state()))
time.sleep(0.8)
check("4d Ton läuft wieder", audio_rms(0.3) > 3000, "%.0f" % audio_rms(0.3))
# ---------------------------------------------------------------- 5: ungültige Dateien ändern nichts
for bad in ("kaputt", "9 -1 0", "0 5 0", "0 -1 2", "-1 -1 0", ""):
    put(bad); time.sleep(0.4)
check("5 ungültige Werte ändern nichts", state() == "0 -1 0" and corner_value(last_frame())[0] > 200, str(state()))
# ---------------------------------------------------------------- 6: Ton ohne Umschalter bleibt wie gebaut
put("0 1 0")
time.sleep(0.5)
check("6 Tonquelle ohne Umschalter bleibt -1", state() == "0 -1 0", str(state()))
out = stop(p)
check("7 kein Absturz, keine Fehlermeldung", "ERROR" not in out and "Segmentation" not in out, out[:200].replace("\n", " "))

# ---------------------------------------------------------------- 8: schon ausgeblendet gebaut (style1="op=0") und mit weiterem Stil
p = launch('style1="op=0,bw=4,bc=ff0000"')
time.sleep(2.0)
fr = last_frame()
check("8a ausgeblendet gebaut: Zustand 1 -1 0", state() == "1 -1 0", str(state()))
check("8b ausgeblendet gebaut: kein Bild", corner_value(fr)[0] < 30, str(corner_value(fr)))
put("0 -1 0"); wait_state("0 -1 0"); time.sleep(0.8); fr = last_frame()
check("8c einblenden: weißes Bild mit rotem Rahmen zurück", corner_value(fr)[0] > 100, str(corner_value(fr)))
put("1 -1 0"); wait_state("1 -1 0"); time.sleep(0.6); fr = last_frame()
check("8d wieder ausblenden", corner_value(fr)[0] < 30, str(corner_value(fr)))
stop(p)

# ---------------------------------------------------------------- 9: Pipeline ohne volume und ohne Mischer: kein Absturz, Zustand ehrlich
clean()
cmd = ["gst-launch-1.0", "-q"] + ("videotestsrc is-live=true ! fakesink  audiotestsrc is-live=true ! fakesink  pbctl name=pbctl view-file=%s/view view-state-file=%s/view-state" % (D, D)).split()
p = subprocess.Popen(cmd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(1.2); put("7 -1 1"); time.sleep(0.8)
check("9 ohne Mischer und volume: Zustand bleibt 0 -1 0, kein Absturz", state() == "0 -1 0" and p.poll() is None, str(state()))
out = stop(p)
print("ERGEBNIS: %d von %d bestanden" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
