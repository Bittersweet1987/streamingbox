#!/usr/bin/env python3
"""HDMI-Dienst (pipbox-hdmi.service): speist den HDMI-Eingang der Box als Kamera in den RTMP-Eingang der Box ein.

Eine HDMI-Kamera wird dadurch zu einer ganz normalen Kamera ("rtmp://127.0.0.1/publish/<Schlüssel>", wie eine RTMP- oder
DJI-Kamera): Sie steht in der Kameraliste, im Status und im Bildaufbau, kann Hauptbild oder kleines Bild sein und
springt im Notbetrieb ein. Die Sendekette bleibt unverändert.

Der Dienst liest den Zustand des HDMI-Empfängers (Kernel, debugfs), startet bei anliegendem Signal einen gst-launch-Prozess
(Bild: v4l2src -> videorate -> mpph264enc (Hardware), Ton: HDMI-Ton oder Stille -> AAC, dazu flvmux -> rtmpsink) und startet ihn neu,
wenn er endet oder das Signal wechselt. Ohne Signal läuft nichts.

Läuft als root (die Hardware-Kodierer, /dev/hdmirx und /dev/snd sind nur für root zugänglich), getrennt von der Weboberfläche.
Schnittstelle: nur 127.0.0.1, JSON-Zeilen über TCP. Jede Anfrage braucht das Token aus <state>/hdmi-token (nur der Benutzer pipbox
kann es lesen). Der Dienst nimmt nur geprüfte Zahlen und feste Auswahlen entgegen und baut den Befehl selbst (keine Shell).
"""
import argparse
import asyncio
import collections
import hmac
import json
import logging
import os
import pwd
import re
import secrets
import signal
import subprocess
import time
import urllib.request

LISTEN_HOST, LISTEN_PORT = "127.0.0.1", 9102
RTMP_PORT = 1935
RTMP_APP = "publish"
STAT_URL = "http://127.0.0.1:1936/"                      # nginx-rtmp-Statistik
HDMI_DEVICE = "/dev/hdmirx"
HDMI_STATUS = "/sys/kernel/debug/hdmirx/status"          # Zustand des HDMI-Empfängers (nur root)
HDMI_AUDIO = "hw:CARD=rockchiphdmiin"                    # ALSA-Karte des HDMI-Tons
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")       # wie in der Weboberfläche
DEFAULTS = {"enabled": False, "key": "hdmi", "bitrate": 8000, "fps": 30, "audio": "hdmi"}
BITRATE_RANGE = (1000, 20000)                            # kbit/s
FPS_CHOICES = (25, 30)
AUDIO_CHOICES = ("hdmi", "none")
NICE = 10                                                # die Einspeisung darf der Sendekette nie Rechenzeit wegnehmen
GRACE = 15.0                                             # so lange darf der Stream brauchen, bis nginx ihn zeigt
STALL = 12.0                                             # so lange darf er danach fehlen, bevor neu gestartet wird
BACKOFF = (2.0, 4.0, 8.0, 15.0, 30.0)                    # Wartezeit nach einem Ende des Prozesses
STABLE = 30.0                                            # danach zählt der Lauf als stabil (Zähler der Fehlstarts wird gelöscht)
TICK = 1.0

log = logging.getLogger("pipbox-hdmi")


# ---------------------------------------------------------------- reine Funktionen (ohne Hardware, gut testbar)
def parse_hdmirx_status(text):
    """Liest /sys/kernel/debug/hdmirx/status. Ergebnis: {"plugged", "locked", "width", "height", "fps", "interlaced", "format", "depth"}.
    "locked" ist nur wahr, wenn alle Kanäle gelockt sind und ein Timing da ist."""
    out = {"plugged": False, "locked": False, "width": 0, "height": 0, "fps": 0.0, "interlaced": False, "format": "", "depth": 0}
    if not isinstance(text, str):
        return out
    m = re.search(r"^status:\s*(\S+)", text, re.M)
    out["plugged"] = bool(m and m.group(1).lower() == "plugin")
    lock = re.findall(r"(?:Clk-Ch|Ch\d)\s*:\s*(\w+)", text)
    lock = [x for x in lock if x.lower() in ("lock", "unlock", "nolock")]
    m = re.search(r"^Timing:\s*(\d+)x(\d+)([pi])(\d+(?:\.\d+)?)", text, re.M)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
        out["interlaced"] = m.group(3) == "i"
        out["fps"] = float(m.group(4))
    m = re.search(r"^Color Format:\s*(\S+)", text, re.M)
    out["format"] = m.group(1) if m else ""
    m = re.search(r"^Color Depth:\s*(\d+)", text, re.M)
    out["depth"] = int(m.group(1)) if m else 0
    out["locked"] = bool(out["plugged"] and lock and all(x.lower() == "lock" for x in lock) and out["width"] and out["height"])
    return out


def signal_id(sig):
    """Was sich ändern darf, ohne dass die Einspeisung neu starten muss: nichts. Wechselt das Bildformat, startet sie neu."""
    return (sig.get("width"), sig.get("height"), round(float(sig.get("fps") or 0)), bool(sig.get("interlaced")))


def clean_settings(d, current=None):
    """Geprüfte Einstellungen. Unbekannte Schlüssel werden ignoriert, falsche Werte gemeldet (ValueError mit kurzem deutschem Text)."""
    cur = dict(DEFAULTS)
    cur.update(current or {})
    if not isinstance(d, dict):
        raise ValueError("Ungültige Anfrage")
    out = dict(cur)
    if "enabled" in d:
        if not isinstance(d["enabled"], bool):
            raise ValueError("Einschalten: ja oder nein")
        out["enabled"] = d["enabled"]
    if "key" in d:
        k = d["key"]
        if not isinstance(k, str) or not KEY_RE.match(k) or k.startswith("test-"):
            raise ValueError("Schlüssel: nur a-z, 0-9, - und _, höchstens 32 Zeichen, nicht mit test- beginnen")
        out["key"] = k
    if "bitrate" in d:
        b = d["bitrate"]
        if isinstance(b, bool) or not isinstance(b, int) or not BITRATE_RANGE[0] <= b <= BITRATE_RANGE[1]:
            raise ValueError("Bitrate: %d bis %d kbit/s" % BITRATE_RANGE)
        out["bitrate"] = b
    if "fps" in d:
        if isinstance(d["fps"], bool) or d["fps"] not in FPS_CHOICES:
            raise ValueError("Bildrate: 25 oder 30")
        out["fps"] = d["fps"]
    if "audio" in d:
        if d["audio"] not in AUDIO_CHOICES:
            raise ValueError("Ton: HDMI-Ton oder ohne Ton")
        out["audio"] = d["audio"]
    return out


def feeder_argv(cfg, rtmp_port=RTMP_PORT, rtmp_app=RTMP_APP, device=HDMI_DEVICE, audio_device=HDMI_AUDIO):
    """Befehl der Einspeisung als Liste (keine Shell). Alle Werte kommen aus clean_settings()."""
    cfg = clean_settings(cfg)
    if not (isinstance(device, str) and re.match(r"^/[A-Za-z0-9_./-]{1,120}$", device)) or "/../" in device + "/":
        raise ValueError("Gerät ungültig")
    if not (isinstance(audio_device, str) and re.match(r"^hw:CARD=[A-Za-z0-9_]{1,40}$", audio_device)):
        raise ValueError("Tongerät ungültig")
    if not (isinstance(rtmp_app, str) and re.match(r"^[a-z0-9_]{1,20}$", rtmp_app)) or not isinstance(rtmp_port, int):
        raise ValueError("RTMP-Ziel ungültig")
    v = ["v4l2src", "device=" + device, "!", "videorate", "!", "video/x-raw,framerate=%d/1" % cfg["fps"], "!",
         "mpph264enc", "bitrate=%d" % (cfg["bitrate"] * 1000), "gop=%d" % cfg["fps"], "!",
         "h264parse", "config-interval=-1", "!", "queue", "!", "mux."]
    if cfg["audio"] == "hdmi":
        a = ["alsasrc", "device=" + audio_device, "!", "audioconvert", "!", "voaacenc", "bitrate=128000", "!", "aacparse", "!", "queue", "!", "mux."]
    else:
        a = ["audiotestsrc", "wave=silence", "is-live=true", "!", "audio/x-raw,rate=48000,channels=2", "!",
             "voaacenc", "bitrate=128000", "!", "aacparse", "!", "queue", "!", "mux."]
    sink = ["flvmux", "name=mux", "streamable=true", "!", "rtmpsink", "location=rtmp://127.0.0.1:%d/%s/%s" % (rtmp_port, rtmp_app, cfg["key"])]
    return ["gst-launch-1.0", "-q"] + v + a + sink


def rtmp_publishing(key, stat_url=None):
    """True, wenn gerade jemand zu rtmp://<Box>/publish/<key> sendet (nginx-rtmp-Statistik). None, wenn sie nicht lesbar ist."""
    try:
        with urllib.request.urlopen(stat_url or STAT_URL, timeout=2) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"<stream>\s*<name>%s</name>[\s\S]*?</stream>" % re.escape(key), body)
    return bool(m and "<publishing/>" in m.group(0))


def friendly_error(lines):
    """Kurzer deutscher Text aus den letzten Zeilen von gst-launch."""
    text = " ".join(lines)[-600:]
    low = text.lower()
    if "busy" in low and ("alsa" in low or "audio" in low or "snd" in low):
        return "Das Tongerät ist belegt"
    if "no such device" in low or "cannot identify device" in low or "could not open" in low and "hdmirx" in low:
        return "HDMI-Gerät nicht gefunden"
    if "timings invalid" in low or "not lock" in low or "streamon" in low:
        return "Kein HDMI-Signal"
    if "rtmp" in low and ("connect" in low or "could not" in low or "failed" in low):
        return "Keine Verbindung zum RTMP-Eingang der Box"
    if "mpp" in low and ("fail" in low or "error" in low):
        return "Der Hardware-Kodierer meldet einen Fehler"
    return "Die Einspeisung wurde beendet" + (": " + re.sub(r"\s+", " ", lines[-1])[:120] if lines else "")


# ---------------------------------------------------------------- Dienst
class Daemon:
    def __init__(self, state_dir, rtmp_port=RTMP_PORT, rtmp_app=RTMP_APP, stat_url=STAT_URL, device=HDMI_DEVICE,
                 status_file=HDMI_STATUS, audio_device=HDMI_AUDIO, read_status=None, spawn=None, publishing=None, clock=time.monotonic):
        self.state_dir = state_dir
        self.config_file = os.path.join(state_dir, "hdmi.json")
        self.token_path = os.path.join(state_dir, "hdmi-token")
        self.rtmp_port, self.rtmp_app, self.stat_url = rtmp_port, rtmp_app, stat_url
        self.device, self.status_file, self.audio_device = device, status_file, audio_device
        self._read_status = read_status or self._read_status_file
        self._spawn = spawn or self._spawn_process
        self._publishing = publishing or (lambda key: rtmp_publishing(key, self.stat_url))
        self.clock = clock
        self.token = self._load_token()
        self.cfg = self.load()
        self.state, self.message = "off", ""
        self.signal = parse_hdmirx_status("")
        self.signal_known = False
        self.proc = None
        self.tail = collections.deque(maxlen=12)
        self.started = 0.0
        self.sig_at_start = None
        self.fails = 0
        self.next_try = 0.0
        self.published = None
        self.missing_since = None
        self.restarts = 0
        self.closing = False
        self._reader = None

    # -- Dateien
    def _load_token(self):
        try:
            with open(self.token_path) as f:
                t = f.read().strip()
            if len(t) >= 32:
                return t
        except OSError:
            pass
        t = secrets.token_urlsafe(32)
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t + "\n")
        try:                                    # die Weboberfläche (Benutzer pipbox) muss es lesen können
            pw = pwd.getpwnam("pipbox")
            os.chown(self.token_path, pw.pw_uid, pw.pw_gid)
        except (KeyError, PermissionError):
            pass
        return t

    def load(self):
        try:
            with open(self.config_file) as f:
                saved = json.load(f)
            return clean_settings(saved)
        except (OSError, ValueError):
            return dict(DEFAULTS)

    def save(self):
        tmp = self.config_file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f, indent=1)
        os.replace(tmp, self.config_file)

    # -- Hardware
    @property
    def available(self):
        return os.path.exists(self.device)

    def _read_status_file(self):
        with open(self.status_file) as f:
            return f.read(4096)

    async def read_signal(self):
        loop = asyncio.get_event_loop()
        try:
            text = await loop.run_in_executor(None, self._read_status)
        except Exception:
            self.signal_known = False
            return parse_hdmirx_status("")
        self.signal_known = True
        return parse_hdmirx_status(text)

    async def _spawn_process(self, argv):
        return await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: os.nice(NICE))

    async def _drain(self, proc):
        """Die letzten Zeilen von stderr merken (für die Fehlermeldung)."""
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                self.tail.append(line.decode("utf-8", "replace").strip())
        except Exception:
            pass

    async def _start(self, sig):
        argv = feeder_argv(self.cfg, self.rtmp_port, self.rtmp_app, self.device, self.audio_device)
        self.tail.clear()
        try:
            self.proc = await self._spawn(argv)
        except Exception as e:
            self.proc = None
            self._failed("Start nicht möglich: %s" % (str(e) or e.__class__.__name__))
            return
        self.started = self.clock()
        self.sig_at_start = signal_id(sig)
        self.published, self.missing_since = None, None
        self.state, self.message = "starting", ""
        if getattr(self.proc, "stderr", None) is not None:
            self._reader = asyncio.ensure_future(self._drain(self.proc))
        log.info("Einspeisung gestartet (%dx%d@%s, %d kbit/s, Schlüssel %s)", sig.get("width") or 0, sig.get("height") or 0,
                 sig.get("fps") or "?", self.cfg["bitrate"], self.cfg["key"])

    async def _stop(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), 3)
                except Exception:
                    pass
            except ProcessLookupError:
                pass
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        log.info("Einspeisung beendet")

    def _failed(self, message):
        self.fails += 1
        wait = BACKOFF[min(self.fails - 1, len(BACKOFF) - 1)]
        self.next_try = self.clock() + wait
        self.state, self.message = "error", message
        log.warning("%s (nächster Versuch in %d s)", message, wait)

    # -- ein Durchgang der Überwachung
    async def tick(self):
        if self.closing:
            return
        if not self.available:
            await self._stop()
            self.state, self.message = "unavailable", "Diese Box hat keinen HDMI-Eingang"
            return
        sig = await self.read_signal()
        self.signal = sig
        running = self.proc is not None and self.proc.returncode is None
        if self.proc is not None and not running:             # der Prozess ist von selbst zu Ende gegangen
            rc = self.proc.returncode
            if self._reader is not None:
                try:
                    await asyncio.wait_for(self._reader, 1)
                except Exception:
                    pass
            self.proc, self._reader = None, None
            if self.clock() - self.started >= STABLE:
                self.fails = 0
            self._failed(friendly_error(list(self.tail)) if rc else "Die Einspeisung wurde beendet")
            self.restarts += 1
            return
        if not self.cfg["enabled"]:
            if running:
                await self._stop()
            self.state, self.message, self.fails = "off", "", 0
            return
        if self.signal_known and not sig["locked"]:
            if running:
                await self._stop()
            self.state, self.message, self.fails = "waiting", "Kein HDMI-Signal", 0
            return
        if running:
            if self.signal_known and signal_id(sig) != self.sig_at_start:
                log.info("Bildformat hat gewechselt, die Einspeisung startet neu")
                await self._stop()
                self.next_try = self.clock()
                self.state = "starting"
                return
            await self._watch_stream()
            return
        if self.clock() < self.next_try:
            return                                            # Wartezeit nach einem Fehler: Zustand "error" bleibt stehen
        await self._start(sig)

    async def _watch_stream(self):
        now = self.clock()
        loop = asyncio.get_event_loop()
        pub = await loop.run_in_executor(None, self._publishing, self.cfg["key"])
        self.published = pub
        age = now - self.started
        if pub is None:                                       # Statistik nicht lesbar: dem Prozess vertrauen
            if age >= 5:
                self.state, self.message = "streaming", ""
            return
        if pub:
            self.missing_since = None
            self.state, self.message = "streaming", ""
            if age >= STABLE:
                self.fails = 0
            return
        if age < GRACE:
            self.state = "starting"
            return
        if self.missing_since is None:
            self.missing_since = now
        if now - self.missing_since >= STALL:
            log.warning("Der Stream erscheint nicht im RTMP-Eingang, die Einspeisung startet neu")
            await self._stop()
            self.restarts += 1
            self._failed("Der Stream kommt nicht in der Box an")

    # -- Schnittstelle
    def status(self):
        s = {"ok": True, "available": self.available, "state": self.state, "message": self.message, "settings": dict(self.cfg),
             "signal": {k: self.signal[k] for k in ("plugged", "locked", "width", "height", "fps", "interlaced", "format", "depth")},
             "signal_known": self.signal_known, "publishing": self.published, "restarts": self.restarts}
        return s

    async def handle(self, req):
        cmd = req.get("cmd")
        if cmd == "status":
            return self.status()
        if cmd == "set":
            new = clean_settings(req.get("settings"), self.cfg)
            changed = new != self.cfg
            self.cfg = new
            self.save()
            if changed and self.proc is not None:             # neue Werte gelten sofort: Einspeisung neu starten
                await self._stop()
                self.next_try = self.clock()
            if changed:
                self.fails = 0
            return self.status()
        if cmd == "restart":
            await self._stop()
            self.fails, self.next_try = 0, self.clock()
            return self.status()
        return {"error": "Unbekannter Befehl"}

    async def client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                req = None
                try:
                    req = json.loads(line)
                    if not isinstance(req, dict):
                        raise ValueError("Ungültige Anfrage")
                    if not hmac.compare_digest(str(req.get("token", "")), self.token):
                        resp = {"error": "kein Zugriff"}
                    else:
                        resp = await self.handle(req)
                except Exception as e:
                    resp = {"error": str(e) or "Ungültige Anfrage"}
                resp["reply_to"] = req.get("id") if isinstance(req, dict) else None
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def supervise(self):
        while not self.closing:
            try:
                await self.tick()
            except Exception:
                log.exception("Fehler in der Überwachung")
            await asyncio.sleep(TICK)

    async def shutdown(self):
        self.closing = True
        await self._stop()

    async def main(self, host=LISTEN_HOST, port=LISTEN_PORT):
        server = await asyncio.start_server(self.client, host, port)
        log.info("bereit auf %s:%d, HDMI-Eingang: %s", host, port, "vorhanden" if self.available else "fehlt")
        asyncio.ensure_future(self.supervise())
        async with server:
            await server.serve_forever()


async def amain(args):
    daemon = Daemon(args.state, args.rtmp_port, args.rtmp_app, args.stat_url, args.device, args.status_file, args.audio_device)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    main_task = asyncio.ensure_future(daemon.main(LISTEN_HOST, args.port))
    stopper = asyncio.ensure_future(stop.wait())
    await asyncio.wait({main_task, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if stop.is_set():
        log.info("wird beendet")
        try:
            await asyncio.wait_for(daemon.shutdown(), 10)
        except Exception as e:
            log.info("Beenden unvollständig: %s", e)
        logging.shutdown()
        os._exit(0)
    stopper.cancel()
    main_task.cancel()
    if not main_task.cancelled() and main_task.done() and main_task.exception():
        raise main_task.exception()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="/var/lib/pipbox")
    ap.add_argument("--port", type=int, default=LISTEN_PORT)
    ap.add_argument("--rtmp-port", type=int, default=RTMP_PORT)
    ap.add_argument("--rtmp-app", default=RTMP_APP)
    ap.add_argument("--stat-url", default=STAT_URL)
    ap.add_argument("--device", default=HDMI_DEVICE)
    ap.add_argument("--status-file", default=HDMI_STATUS)
    ap.add_argument("--audio-device", default=HDMI_AUDIO)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
