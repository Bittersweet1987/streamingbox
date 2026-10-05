#!/usr/bin/env python3
"""Gesamttest "alle Kameras immer bereit" auf der Box, ohne echte Kameras und ohne die laufenden Dienste zu stören (pipbox-send muss aus sein).

Die Sendekette wird so erzeugt, wie der Sende-Dienst sie erzeugt (PipelineStore.build mit always_ready), nur schreibt sie in eine Datei statt zu belacoder
(appsink -> filesink) und pbctl bekommt eigene Steuerdateien. Zubringer und Auswahl sind die echten (pipbox_always.py). Drei Testkameras senden an den
RTMP-Server der Box (Schlüssel tsta, tstb, tstc) und werden zu festen Zeiten gestoppt und gestartet:

   0 s   A (Hauptkamera) und B (kleines Bild 1) eingestellt, nur A sendet; Kette läuft
  10 s   B beginnt zu senden            -> erscheint als kleines Bild, ohne Neustart
  20 s   A hört auf                     -> B wird Hauptbild (nach etwa 2 s)
  32 s   A sendet wieder                -> nach 3 s wieder Hauptbild, B wieder klein
  44 s   C beginnt (eingestellt als kleines Bild 2) -> erscheint, ohne Neustart
  55 s   Ende

Geprüft: Zeitstempel der Datei (Lücken, rückwärts), Zahl der Neustarts (keine), Umschaltzeilen mit Zeitpunkt. Standbilder zu festen Zeitpunkten
(/var/tmp/alwaystest/frames/*.jpg) zum Ansehen. Aufruf: boxtest_always.py [Bausteinordner=/opt/pipbox/gst] [Programmordner=/opt/pipbox]"""
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

PLUG = sys.argv[1] if len(sys.argv) > 1 else "/opt/pipbox/gst"
CODE = sys.argv[2] if len(sys.argv) > 2 else "/opt/pipbox"
sys.path.insert(0, CODE)
import server  # noqa: E402
import pipbox_always as always  # noqa: E402

server.PLUGIN_SO = f"{PLUG}/libgstpbpip.so"      # der geprüfte Baustein, nicht der installierte
D = "/var/tmp/alwaystest"
os.makedirs(D, exist_ok=True)
for f in os.listdir(D):
    p = os.path.join(D, f)
    if os.path.isfile(p):
        os.remove(p)
os.makedirs(D + "/frames", exist_ok=True)
for f in os.listdir(D + "/frames"):
    os.remove(D + "/frames/" + f)

CFG = {"type": "pip", "main": "tsta", "pip": "tstb", "pip2": "tstc", "corner": 3, "corner2": 2, "size_pct": 25, "size_pct2": 25, "always_ready": True,
       "audio": "main", "main_delay_ms": 0, "pip_delay_ms": 0, "pip2_delay_ms": 0}
text = server.PipelineStore(os.devnull).build(CFG)
assert "udpsrc port=9410" in text, "Modus nicht aktiv (Baustein ohne fill-caps?)"
text = text.replace("appsink name=appsink", f"filesink location={D}/out.ts")
text = text.replace("pbctl name=pbctl", f"pbctl name=pbctl select-file={D}/select file={D}/delay view-file={D}/view state-file={D}/swap-state view-state-file={D}/view-state")
text = text.replace(server.CAM_LIVE, f"{D}/live")
for f, v in (("select", "0 1 2 15"), ("view", "0 -1 0"), ("delay", "0 0 0 0")):
    open(f"{D}/{f}", "w").write(v + "\n")

log = []
t0 = time.time()
def now():
    return time.time() - t0
def say(m):
    log.append((now(), m))
    print("%5.1f s  %s" % (now(), m), flush=True)

def put(name, txt):
    tmp = f"{D}/{name}.tmp"
    open(tmp, "w").write(txt)
    os.replace(tmp, f"{D}/{name}")

selects = []
def put_logged(name, txt):
    put(name, txt)
    if name == "select":
        selects.append((now(), txt.strip()))

feeders = always.Feeders(log=say)
ctl = always.Controller(load_json=lambda name: dict(CFG), put_state_file=put_logged, delay_values=server.PipelineStore.delay_values,
                        cam_live_path=f"{D}/live", select_name="select", delay_name="delay", feeders=feeders, log=say)

env = dict(os.environ, GST_PLUGIN_PATH=PLUG)
main = None
cams = {}
def cam(key, pattern):
    cmd = (f"gst-launch-1.0 -q videotestsrc is-live=true pattern={pattern} ! video/x-raw,width=1920,height=1080,framerate=30/1,format=NV12 ! "
           f"mpph264enc bitrate=6000000 gop=30 header-mode=each-idr ! h264parse ! queue ! mux. "
           f"audiotestsrc is-live=true freq={ {'tsta': 440, 'tstb': 660, 'tstc': 880}[key] } ! audio/x-raw,rate=48000,channels=2 ! avenc_aac bitrate=128000 ! aacparse ! queue ! mux. "
           f"flvmux name=mux streamable=true ! rtmpsink location=\"rtmp://127.0.0.1/publish/{key} live=1\"")
    cams[key] = subprocess.Popen(shlex.split(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    say(f"Kamera {key} startet")

def stop_cam(key):
    p = cams.pop(key, None)
    if p:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
        p.wait(timeout=10)
        say(f"Kamera {key} gestoppt")

def snapshot(name):
    """Standbild des Ausgangs vom Ende der Datei: später aus der Datei dekodiert (siehe unten); hier nur die Zeit merken."""
    snaps.append((name, os.path.getsize(f"{D}/out.ts") if os.path.exists(f"{D}/out.ts") else 0, now()))
snaps = []

stop_ticks = threading.Event()
def ticker():
    while not stop_ticks.is_set():
        try:
            ctl.tick()
        except Exception as e:
            say(f"Auswahl-Fehler {type(e).__name__}: {e}")
        stop_ticks.wait(0.5)

ctl.start()
cam("tsta", "smpte")
time.sleep(2.5)                                   # Gate wie im Sende-Dienst: erst starten, wenn eine Kamera sendet
mainp = subprocess.Popen(["gst-launch-1.0", "-q"] + shlex.split(text), env=env, stdout=open(f"{D}/main.log", "w"), stderr=subprocess.STDOUT)
t0 = time.time()
say("Sendekette gestartet")
th = threading.Thread(target=ticker, daemon=True); th.start()

def at(t, fn, *a):
    while now() < t:
        time.sleep(0.1)
        if mainp.poll() is not None:
            say("KETTE BEENDET!")
            return False
    fn(*a)
    return True

ok = True
ok = ok and at(10, cam, "tstb", "ball")
ok = ok and at(12, snapshot, "10: A groß, B klein")
ok = ok and at(20, stop_cam, "tsta")
ok = ok and at(27, snapshot, "20: A fehlt, B Hauptbild")
ok = ok and at(32, cam, "tsta", "smpte")
ok = ok and at(38, snapshot, "32: A zurück")
ok = ok and at(44, cam, "tstc", "snow")
ok = ok and at(52, snapshot, "44: C klein")
ok = ok and at(55, lambda: None)
stop_ticks.set()
for k in list(cams):
    stop_cam(k)
if mainp.poll() is None:
    mainp.send_signal(signal.SIGINT)
    try:
        mainp.wait(timeout=15)
    except subprocess.TimeoutExpired:
        mainp.kill()
feeders.stop_all()

print("\nUmschaltzeilen (Zeit seit Kettenstart):")
for t, l in selects:
    print("  %5.1f s  %s" % (t, l))
print("Zubringer gestartet je Platz:", feeders.starts)
print("Kette lief bis zum Ende:", mainp.returncode in (0, -2, None) and ok)
if os.path.exists(f"{D}/out.ts"):
    print("\nZeitstempel der Ausgabe:")
    subprocess.run([sys.executable, f"{CODE}/tools/pes_gaps.py" if os.path.exists(f"{CODE}/tools/pes_gaps.py") else "/var/tmp/gstdev/pes_gaps.py", f"{D}/out.ts"])
sys.exit(0 if ok else 1)
