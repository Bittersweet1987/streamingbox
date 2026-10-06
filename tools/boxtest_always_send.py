#!/usr/bin/env python3
"""Gesamttest "alle Kameras immer bereit" mit dem ECHTEN Sende-Dienst (pipbox_send.Sender: prepare, Zubringer, belacoder, Auswahl) auf der Box,
ohne Netz und ohne die Einstellungen der Box zu berühren (eigene Ordner unter /var/tmp/alwayssend, pipbox-send muss aus sein).

Statt srtla_send läuft ein Platzhalter; belacoder sendet per SRT an einen lokalen Empfänger (srt-live-transmit), der in eine Datei schreibt. Geprüft wird,
dass der Encoder wirklich Daten an die SRT-Seite liefert (Bytes je Zeitabschnitt), dass belacoder nie neu startet und was der Sende-Dienst meldet.
Drei Testkameras (Schlüssel tsta, tstb) senden an den RTMP-Server der Box:

   0 s   tsta sendet (Hauptkamera), tstb (kleines Bild) nicht; Sende-Dienst startet
  15 s   tstb beginnt
  30 s   tsta hört auf   -> tstb wird Hauptbild
  45 s   tsta kehrt zurück
  60 s   Ende

Mit der Umgebungsvariablen REAL="schlüssel1,schlüssel2[,schlüssel3]" und DAUER=Sekunden läuft derselbe Aufbau mit ECHTEN Kameras (die schon an die Box senden) statt der
Testkameras, ohne Start/Stopp von Kameras; gemessen werden dann Bytes am Ausgang, Last, Temperatur, Taktfrequenz und die Wechsel des Hauptbildes.

Aufruf: boxtest_always_send.py [Bausteinordner=/opt/pipbox/gst] [Programmordner=/opt/pipbox]
Danach die Testkameras tsta/tstb aus der Kameraliste der Box entfernen (RTMP-Quellen legen sie an)."""
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from unittest import mock

PLUG = sys.argv[1] if len(sys.argv) > 1 else "/opt/pipbox/gst"
CODE = sys.argv[2] if len(sys.argv) > 2 else "/opt/pipbox"
sys.path.insert(0, CODE)
import server  # noqa: E402
import pipbox_send as ps  # noqa: E402

T = "/var/tmp/alwayssend"
shutil.rmtree(T, ignore_errors=True)
for d in ("state", "work", "run"):
    os.makedirs(f"{T}/{d}")
PORT = 9101
json.dump({"servers": [{"id": "abcdef01", "name": "T", "host": "example.org", "port": 5000, "streamid": ""}], "selected": "abcdef01",
           "settings": {"uplinks": ["eth0"]}}, open(f"{T}/state/srtla.json", "w"))
REAL = [k for k in os.environ.get("REAL", "").split(",") if k]
DAUER = int(os.environ.get("DAUER", "60"))
CFG = {"type": "pip", "main": "tsta", "pip": "tstb", "corner": 3, "size_pct": 25, "always_ready": True, "audio": "main",
       "main_delay_ms": 0, "pip_delay_ms": 0}
if REAL:
    CFG.update(main=REAL[0], pip=REAL[1], pip2=REAL[2] if len(REAL) > 2 else "", corner2=2, size_pct2=25)
json.dump(CFG, open(f"{T}/state/pipeline.json", "w"))
for f, v in (("view", "0 -1 0"),):
    open(f"{T}/state/{f}", "w").write(v + "\n")

server.PLUGIN_SO = f"{PLUG}/libgstpbpip.so"
patches = [mock.patch.object(ps, "STATE", f"{T}/state"), mock.patch.object(ps, "WORK", f"{T}/work"), mock.patch.object(ps, "RUN", f"{T}/run"),
           mock.patch.object(ps, "STATUS", f"{T}/run/status.json"), mock.patch.object(ps, "STATS", f"{T}/run/belacoder-stats.txt"),
           mock.patch.object(ps, "BC_STATS", f"{T}/run/belacoder-stats.json"), mock.patch.object(ps, "PLUGIN_DIR", PLUG),
           mock.patch.object(ps, "LISTEN_PORT", PORT), mock.patch.object(server, "CAM_LIVE", f"{T}/run/cam-live"),
           mock.patch.object(server, "SWAP_STATE", f"{T}/run/swap-state"), mock.patch.object(server, "VIEW_STATE", f"{T}/run/view-state"),
           mock.patch.object(server, "iface_ips", lambda: [{"iface": "eth0", "ip": "10.0.0.2"}])]
for p in patches:
    p.start()

_build = server.PipelineStore.build


def build(self, cfg=None):
    """Pipeline-Text wie im Betrieb, nur zeigt pbctl auf die Dateien dieses Tests (nicht auf /var/lib/pipbox)."""
    text = _build(self, cfg)
    return text.replace("pbctl name=pbctl", f"pbctl name=pbctl file={T}/state/main-delay-ms select-file={T}/state/main-select state-file={T}/run/swap-state "
                        f"view-file={T}/state/view view-state-file={T}/run/view-state")


server.PipelineStore.build = build
_args = ps.Sender.args


def args(self, name):
    return ["sleep", "3600"] if name == "srtla_send" else _args(self, name)      # kein Netz: srtla_send ersetzt, belacoder sendet an den lokalen Empfänger


ps.Sender.args = args
ps.Sender.wait_links_ready = lambda self, timeout=20: time.sleep(1)       # kein srtla_send, der Wege meldet

t0 = time.time()
def now():
    return time.time() - t0
def say(m):
    print("%5.1f s  %s" % (now(), m), flush=True)

cams = {}
def cam(key, pattern):
    cmd = (f"gst-launch-1.0 -q videotestsrc is-live=true pattern={pattern} ! video/x-raw,width=1920,height=1080,framerate=30/1,format=NV12 ! "
           f"mpph264enc bitrate=6000000 gop=30 header-mode=each-idr ! h264parse ! queue ! mux. "
           f"audiotestsrc is-live=true freq={440 if key == 'tsta' else 660} ! audio/x-raw,rate=48000,channels=2 ! avenc_aac bitrate=128000 ! aacparse ! queue ! mux. "
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

recv = subprocess.Popen(["srt-live-transmit", f"srt://127.0.0.1:{PORT}?mode=listener&latency=2000", "file://con"],
                        stdout=open(f"{T}/out.ts", "wb"), stderr=open(f"{T}/srt.log", "w"))
time.sleep(1)
if not REAL:
    cam("tsta", "smpte")
time.sleep(3)                                         # der RTMP-Server braucht einen Augenblick, bis er die Kamera meldet
sv, lat, mn, mx, ips, plan = ps.prepare()
say(f"prepare: Modus immer bereit = {plan['always']}, Hinweis = {plan.get('always_note')}")
s = ps.Sender(sv, lat, ips, plan)
th = threading.Thread(target=s.run, daemon=True)
t0 = time.time()
th.start()

def size():
    try:
        return os.path.getsize(f"{T}/out.ts")
    except OSError:
        return 0

def status():
    try:
        return json.load(open(f"{T}/run/status.json"))
    except (OSError, ValueError):
        return {}

samples = []
events = {} if REAL else {15: lambda: cam("tstb", "ball"), 30: lambda: stop_cam("tsta"), 45: lambda: cam("tsta", "smpte")}
done = set()
last = 0
def sysinfo():
    try:
        la = open("/proc/loadavg").read().split()[0]
        tp = int(open("/sys/class/thermal/thermal_zone0/temp").read()) // 1000
        mh = "/".join(str(int(open(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq").read()) // 1000) for i in (0, 4, 6))
        return f"Last {la}, {tp} C, MHz {mh}"
    except OSError:
        return ""


while now() < (DAUER if REAL else 60):
    for t, fn in events.items():
        if now() >= t and t not in done:
            done.add(t)
            fn()
    sz = size()
    samples.append((now(), sz))
    if int(now()) % 5 == 0 and int(now()) != last:
        last = int(now())
        st = status()
        fo = st.get("failover", {})
        say(f"{sysinfo()} | Ausgang {sz // 1024} KB gesamt | Zustand {st.get('state')} | Neustarts {st.get('restarts')} | Kameras mit Bild {fo.get('live')} | Hauptbild {fo.get('main')}")
    time.sleep(0.5)

restarts = dict(s.restarts)
s.stop_ev.set()
th.join(timeout=30)
for k in list(cams):
    stop_cam(k)
recv.terminate()

span = DAUER if REAL else 60
print("\nBytes je 5 s am SRT-Ausgang (Encoder -> Empfänger):")
for lo in range(0, span, 5):
    a = next((b for t, b in samples if t >= lo), 0)
    b = next((b for t, b in samples if t >= lo + 5), samples[-1][1])
    print("  %2d bis %2d s: %6d KB" % (lo, lo + 5, (b - a) // 1024))
ok = all(((next((b for t, b in samples if t >= lo + 5), samples[-1][1]) - next((b for t, b in samples if t >= lo), 0)) > 50 * 1024) for lo in range(5, span - 5, 5))
print("Neustarts (belacoder/srtla_send):", restarts)
print("Encoder lieferte in jedem Zeitabschnitt (ab 5 s):", ok, "| ohne Neustart:", not any(restarts.values()))
if os.path.exists(f"{T}/out.ts"):
    print("\nZeitstempel der Ausgabe:")
    pg = f"{CODE}/tools/pes_gaps.py" if os.path.exists(f"{CODE}/tools/pes_gaps.py") else "/var/tmp/gstdev/pes_gaps.py"
    subprocess.run([sys.executable, pg, f"{T}/out.ts"])
sys.exit(0 if ok and not any(restarts.values()) else 1)
