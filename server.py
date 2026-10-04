#!/usr/bin/env python3
"""IRL4YOU BOX: eigene Oberfläche, Sendesteuerung und Auslastungsanzeige, getrennt von belaUI.

Nur Python-Standardbibliothek. Läuft auf der Box (liest /proc und /sys) und
mit --demo auch auf dem Mac (erzeugte Beispielwerte) zur Ansicht.

Der Dienst lauscht auf dem Port 8780 (install/pipbox.service: --host 0.0.0.0, erreichbar im lokalen Netz);
Fernzugriff von unterwegs läuft über `tailscale serve`.
"""
import argparse
import hashlib
import ipaddress
import hmac
import json
import math
import os
import random
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dji                         # Namen von USB-Sticks (WLAN, Bluetooth) aus /sys, liegt neben dieser Datei

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# Echte Netzwerkkarten für die Upload-Anzeige (Ethernet, WLAN, USB-/Mobilfunk-Modems). Virtuelles (Tailscale, Docker,
# Brücken) bleibt draußen, sonst würde der Verkehr doppelt gezählt. Angezeigt wird, was gerade verbunden ist.
NET_PREFIXES = ("eth", "en", "wlan", "wl", "usb", "wwan", "ppp", "rndis")
NET_SKIP = ("docker", "veth", "br-", "virbr", "tailscale", "tun", "tap", "lo")

# Ampelgrenzen (Rohwerte, später anhand echter Messungen festlegen)
LIMITS = {"temp_warn": 70.0, "temp_crit": 80.0, "cpu_warn": 85.0, "core_warn": 90.0, "mem_warn": 85.0}


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


class Sampler:
    """Berechnet Raten aus Zählerdifferenzen zwischen zwei Abfragen."""

    def __init__(self, demo):
        self.demo = demo
        self.prev_cpu = None
        self.prev_net = None
        self.prev_t = None
        self._lock = threading.Lock()

    def cpu_times(self):
        out = []
        for line in (read("/proc/stat", "") or "").splitlines():
            if line.startswith("cpu") and line[3:4].isdigit():
                v = list(map(int, line.split()[1:]))
                idle = v[3] + (v[4] if len(v) > 4 else 0)
                out.append((sum(v), idle))
        return out

    @staticmethod
    def fan_pwm():
        """Ansteuerung des Lüfters (PWM 0..255) oder None. Das ist der Sollwert, keine gemessene Drehzahl:
        die Box hat keinen Drehzahlanschluss am Lüfter."""
        try:
            for d in sorted(os.listdir("/sys/class/hwmon")):
                base = f"/sys/class/hwmon/{d}"
                if read(base + "/name", "").strip() == "pwmfan":
                    v = read(base + "/pwm1")
                    return int(v) if v is not None and 0 <= int(v) <= 255 else None
        except (OSError, ValueError):
            pass
        return None

    def net_bytes(self):
        res = {}
        for line in (read("/proc/net/dev", "") or "").splitlines()[2:]:
            name, _, rest = line.partition(":")
            name = name.strip()
            if name.startswith(NET_PREFIXES) and not name.startswith(NET_SKIP) and \
                    (read(f"/sys/class/net/{name}/operstate", "") or "").strip() in ("up", "unknown"):
                f = rest.split()
                res[name] = (int(f[0]), int(f[8]))
        return res

    def sample(self):
        """Messwerte. Liegt die letzte Messung keine 1,5 s zurück (zweiter Tab, mehrere Abfragen), gilt sie weiter:
        sonst würden die Raten über sehr kurze Zeiträume berechnet und die Arbeit doppelt gemacht."""
        if self.demo:
            return self.sample_demo()
        with self._lock:
            hit = getattr(self, "_last", None)
            if hit is not None and time.monotonic() - hit[0] < 1.5:
                return hit[1]
            val = self.sample_real()
            self._last = (time.monotonic(), val)
            return val

    def sample_real(self):
        now = time.time()
        cpu, net = self.cpu_times(), self.net_bytes()
        cores, rates = [], {}
        if self.prev_cpu and len(self.prev_cpu) == len(cpu):
            for (t1, i1), (t0, i0) in zip(cpu, self.prev_cpu):
                dt = max(t1 - t0, 1)
                cores.append(round(100.0 * (1 - (i1 - i0) / dt), 1))
        dt_s = (now - self.prev_t) if self.prev_t else None
        if self.prev_net and dt_s:
            for n, (rx, tx) in net.items():
                p = self.prev_net.get(n)
                if p:
                    rates[n] = {"rx_mbit": round((rx - p[0]) * 8 / dt_s / 1e6, 2),
                                "tx_mbit": round((tx - p[1]) * 8 / dt_s / 1e6, 2)}
        self.prev_cpu, self.prev_net, self.prev_t = cpu, net, now

        freqs = []
        i = 0
        while True:
            v = read(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
            if v is None:
                break
            freqs.append(int(v) // 1000)
            i += 1
        temps = []
        z = 0
        while True:
            v = read(f"/sys/class/thermal/thermal_zone{z}/temp")
            if v is None:
                break
            temps.append(int(v) / 1000.0)
            z += 1
        mem = {}
        for line in (read("/proc/meminfo", "") or "").splitlines():
            k, _, v = line.partition(":")
            mem[k] = int(v.split()[0]) if v.split() else 0
        total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
        blocked = 0
        for line in (read("/proc/stat", "") or "").splitlines():
            if line.startswith("procs_blocked"):
                blocked = int(line.split()[1])
        return self.finish(cores, freqs, max(temps) if temps else None,
                           total, avail, rates, blocked, self.fan_pwm())

    def sample_demo(self):
        t = time.time()
        cores = [round(max(2, min(99, 45 + 35 * math.sin(t / 7 + k) +
                                  random.uniform(-6, 6))), 1) for k in range(8)]
        freqs = [1800 if k >= 4 else 1416 for k in range(8)]
        temp = 52 + 8 * math.sin(t / 30) + random.uniform(-0.5, 0.5)
        rates = {"eth0": {"rx_mbit": 0.4, "tx_mbit": round(6 + random.uniform(-1, 1), 2)},
                 "eth1": {"rx_mbit": round(13 + random.uniform(-1, 1), 2),
                          "tx_mbit": round(5 + random.uniform(-1, 1), 2)}}
        return self.finish(cores, freqs, temp, 16 * 1024 * 1024,
                           int(7.4 * 1024 * 1024), rates, 0, int(110 + 40 * math.sin(t / 30)))

    def finish(self, cores, freqs, temp, total_kb, avail_kb, rates, blocked, fan_pwm=None):
        cpu_avg = round(sum(cores) / len(cores), 1) if cores else None
        cpu_max = max(cores) if cores else None
        mem_used = round(100.0 * (1 - avail_kb / total_kb), 1) if total_kb else None
        alerts = []
        if temp is not None and temp >= LIMITS["temp_crit"]:
            alerts.append({"level": "crit", "text": f"Temperatur {temp:.0f} °C"})
        elif temp is not None and temp >= LIMITS["temp_warn"]:
            alerts.append({"level": "warn", "text": f"Temperatur {temp:.0f} °C"})
        if cpu_avg is not None and cpu_avg >= LIMITS["cpu_warn"]:
            alerts.append({"level": "warn", "text": f"CPU-Last {cpu_avg:.0f} %"})
        elif cpu_max is not None and cpu_max >= LIMITS["core_warn"]:
            alerts.append({"level": "warn", "text": f"Ein CPU-Kern ist fast voll ({cpu_max:.0f} %)"})
        if mem_used is not None and mem_used >= LIMITS["mem_warn"]:
            alerts.append({"level": "warn", "text": f"RAM {mem_used:.0f} % belegt"})
        if blocked > 0:
            alerts.append({"level": "warn", "text": f"{blocked} Prozess(e) blockiert (D-State)"})
        return {"time": int(time.time()), "demo": self.demo,
                "cpu": {"cores": cores, "avg": cpu_avg, "max": cpu_max, "freq_mhz": freqs},
                "temp_c": None if temp is None else round(temp, 1),
                "fan_pct": None if fan_pwm is None else round(100.0 * fan_pwm / 255),   # Ansteuerung, keine Drehzahl
                "mem": {"used_pct": mem_used, "avail_mb": avail_kb // 1024,
                        "total_mb": total_kb // 1024},
                "net": rates, "procs_blocked": blocked, "alerts": alerts,
                # Platzhalter bis die Pipeline echte Werte liefert
                "encoder": {"fps": 29.9, "bitrate_mbit": 9.8, "max_mbit": 12}
                if self.demo else None}


IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def lan_ip():
    """IPv4-Adresse des Standard-Netzwerkwegs (für Adressen, die Kameras nutzen)."""
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("203.0.113.1", 9))  # sendet nichts, wählt nur den Weg
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except OSError:
        return "127.0.0.1"


NET_LABELS = {}   # Namen ergeben sich aus der Art der Karte (siehe net_label), nicht aus festen Annahmen über die Box


def net_label(name):
    """Anzeigename einer Netzwerkkarte: WLAN, USB-Router oder Ethernet."""
    if name.startswith(("wl",)):
        return f"WLAN ({name})"
    try:
        if "/usb" in os.path.realpath(f"/sys/class/net/{name}/device"):
            return f"USB-Router ({name})"
    except OSError:
        pass
    return f"Ethernet ({name})"


FIXED_ALIAS = {"eth1": "192.168.80.50"}   # feste Zweitadressen (setzt pipbox-net.service)


def fixed_ip(name):
    """Feste Zweitadresse einer Schnittstelle, wenn sie dort gerade gesetzt ist."""
    want = FIXED_ALIAS.get(name)
    if not want:
        return None
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", name],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return want if f" {want}/" in out else None


_TTL = {}


def ttl_cached(key, ttl, fn):
    """Ergebnis von fn() für ttl Sekunden merken. Spart Programmstarts und Dateilesen bei den vielen Abfragen der Oberfläche."""
    now = time.monotonic()
    hit = _TTL.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _TTL[key] = (now, val)
    return val


def iface_ips():
    """IPv4-Adressen der Netzwerkschnittstellen, 3 s zwischengespeichert (jede Abfrage startete sonst `ip`)."""
    return [dict(x) for x in ttl_cached("iface_ips", 3.0, _iface_ips_raw)]


def _iface_ips_raw():
    """IPv4-Adressen der Netzwerkschnittstellen (nur Linux), ohne lo/Container."""
    try:
        import fcntl
        import struct
        names = sorted(os.listdir("/sys/class/net"))
    except (ImportError, OSError):
        return []
    out = []
    for name in names:
        if name == "lo" or name.startswith(("docker", "veth", "br-", "virbr", "p2p", "tailscale", "tun", "tap", "wg", "zt")):
            continue          # virtuelle Netze (Tailscale, Container, VPN) sind keine Sendewege und keine Kameranetze
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ip = socket.inet_ntoa(fcntl.ioctl(sk.fileno(), 0x8915,
                                              struct.pack("256s", name[:15].encode()))[20:24])
            sk.close()
        except OSError:
            continue
        out.append({"iface": name, "ip": ip, "cam_ip": fixed_ip(name) or ip, "label": NET_LABELS.get(name) or net_label(name)})
    return out


LINKS_FILE = "/run/pipbox-send/srtla-links.txt"


def uplink_states(selected):
    """Ampel je Sendeweg aus der Datei des Senders (alle paar Sekunden eine Zeile je Weg): {Schnittstelle: {"state", "srtt"}}.
    "an" = der Weg trägt Pakete, "reserve" = verbunden, wird aber nicht genutzt (Laufzeit zu hoch oder zu unruhig), "aus" = nicht
    verbunden oder ohne Netz. Leeres Ergebnis, wenn keine frischen Daten da sind (Sendung aus)."""
    try:
        if time.time() - os.stat(LINKS_FILE).st_mtime > 20:
            return {}
        with open(LINKS_FILE) as f:
            lines = f.readlines()[-60:]
    except OSError:
        return {}
    last = {}
    for ln in lines:
        m = re.match(r"\S+ links: (\S+) srtt=(-?\d+)ms .*? (genutzt|reserve) ", ln)
        if m:
            last[m.group(1)] = (int(m.group(2)), m.group(3))
    by_ip = {o["ip"]: o["iface"] for o in iface_ips()}
    seen = {by_ip[ip]: v for ip, v in last.items() if ip in by_ip}
    out = {}
    for n in selected:
        v = seen.get(n)
        if v is None:
            out[n] = {"state": "aus", "srtt": None}
        else:
            out[n] = {"state": "an" if v[1] == "genutzt" else "reserve", "srtt": v[0] if v[0] >= 0 else None}
    return out


class NetChoice:
    """Welches Netzwerk die Kameras zur Box nutzen (bestimmt die RTMP-Adresse)."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.iface = json.load(f).get("iface")
        except (OSError, ValueError):
            self.iface = None

    def options(self):
        return iface_ips()

    def ip(self):
        for o in self.options():
            if o["iface"] == self.iface:
                return o.get("cam_ip") or o["ip"]
        return lan_ip()   # Standardweg, wenn nichts gewählt oder Schnittstelle weg

    def select(self, iface):
        if iface not in [o["iface"] for o in self.options()]:
            raise ValueError("Unbekannte Schnittstelle oder keine IPv4-Adresse")
        self.iface = iface
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"iface": iface}, f)
        os.replace(tmp, self.path)

    def status(self):
        return {"options": self.options(), "selected": self.iface, "ip": self.ip()}


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class SrtlaStore:
    """SRTLA-Server (Name, Adresse, Port, Stream-ID), Auswahl und Sendeeinstellungen.

    Gespeichert in <state>/srtla.json. Eine Liste mit Dropdown ist das, was die
    Original-BELABOX nicht kann. Die Werte werden hier streng geprüft, denn der
    spätere Sende-Dienst läuft mit Root-Rechten und liest dieselbe Datei.
    """
    DEFAULT_SETTINGS = {"min_kbps": 300, "max_kbps": 12000, "latency_ms": 4000, "uplinks": ["eth0", "eth1"],
                        "spread": "best"}

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"servers": [], "selected": None, "settings": dict(self.DEFAULT_SETTINGS)}
        try:
            with open(path) as f:
                loaded = json.load(f)
            self.data.update({k: loaded[k] for k in ("servers", "selected") if k in loaded})
            self.data["settings"].update(loaded.get("settings", {}))
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def check(req):
        name = str(req.get("name", "")).strip()
        host = str(req.get("host", "")).strip().lower()
        sid = str(req.get("streamid", "")).strip()
        try:
            port = int(req.get("port"))
        except (TypeError, ValueError):
            raise ValueError("Port: Zahl von 1 bis 65535")
        if not 1 <= len(name) <= 40:
            raise ValueError("Name: 1 bis 40 Zeichen")
        if not HOST_RE.match(host):
            raise ValueError("Adresse: nur Buchstaben, Ziffern, Punkt und Bindestrich (ohne rtmp:// oder Leerzeichen)")
        if not 1 <= port <= 65535:
            raise ValueError("Port: Zahl von 1 bis 65535")
        if len(sid) > 512 or any(ord(c) < 32 or ord(c) > 126 for c in sid):
            raise ValueError("Stream-ID: höchstens 512 sichtbare Zeichen")
        return {"name": name, "host": host, "port": port, "streamid": sid}

    def public(self):
        """Ohne Stream-ID: sie ist ein Zugangsschlüssel und bleibt auf der Box."""
        with self.lock:
            return {"servers": [{"id": s["id"], "name": s["name"], "host": s["host"], "port": s["port"],
                                 "has_streamid": bool(s.get("streamid"))} for s in self.data["servers"]],
                    "selected": self.data["selected"], "settings": dict(self.data["settings"])}

    def add(self, req):
        item = self.check(req)
        with self.lock:
            item["id"] = secrets.token_hex(4)
            self.data["servers"].append(item)
            if not self.data["selected"]:
                self.data["selected"] = item["id"]
            self.save()
        return item["id"]

    def update(self, sid, req):
        with self.lock:
            cur = next((s for s in self.data["servers"] if s["id"] == sid), None)
            if not cur:
                raise KeyError(sid)
            merged = {"name": cur["name"], "host": cur["host"], "port": cur["port"],
                      "streamid": cur.get("streamid", "")}
            merged.update({k: v for k, v in req.items() if k in merged and not (k == "streamid" and v in (None, ""))})
            if req.get("clear_streamid"):
                merged["streamid"] = ""
            cur.update(self.check(merged))
            self.save()

    def remove(self, sid):
        with self.lock:
            n = len(self.data["servers"])
            self.data["servers"] = [s for s in self.data["servers"] if s["id"] != sid]
            if len(self.data["servers"]) == n:
                raise KeyError(sid)
            if self.data["selected"] == sid:
                self.data["selected"] = self.data["servers"][0]["id"] if self.data["servers"] else None
            self.save()

    def select(self, sid):
        with self.lock:
            if not any(s["id"] == sid for s in self.data["servers"]):
                raise ValueError("Server nicht gefunden")
            self.data["selected"] = sid
            self.save()

    def set_settings(self, req, valid_ifaces):
        with self.lock:
            cur = dict(self.data["settings"])
        # Fehlende Felder behalten ihren gespeicherten Wert (z. B. beim Anhaken eines Netzes wird nur die Netzliste geschickt),
        # sonst überschreibt ein veralteter Stand der Seite andere Einstellungen.
        try:
            mn = int(req.get("min_kbps", cur.get("min_kbps")))
            mx = int(req.get("max_kbps", cur.get("max_kbps")))
            lat = int(req.get("latency_ms", cur.get("latency_ms")))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Bitrate und Latenz müssen Zahlen sein")
        if not 100 <= mn < mx <= 20000:
            raise ValueError("Bitrate: Minimum ab 100, kleiner als Maximum, Maximum höchstens 20000 kbit/s")
        if not 100 <= lat <= 10000:
            raise ValueError("Latenz: 100 bis 10000 ms")
        ups = [u for u in req.get("uplinks", []) if isinstance(u, str)]
        if any(not re.fullmatch(r"[A-Za-z0-9._-]{1,15}", u) for u in ups):
            raise ValueError("Ungültiger Name eines Netzwerks")
        with self.lock:
            before = set(self.data["settings"].get("uplinks", []))
        # Neu angehakte Netze müssen es geben. Schon gespeicherte, die gerade fehlen (Router abgezogen), bleiben stehen,
        # sonst ließe sich nichts mehr ändern, solange ein altes Netz in der Liste hängt.
        if any(u not in valid_ifaces and u not in before for u in ups) or not any(u in valid_ifaces for u in ups):
            raise ValueError("Mindestens ein vorhandenes Netzwerk als Sendeweg wählen")
        spread = req.get("spread", cur.get("spread", "best"))
        if spread not in ("best", "all"):
            raise ValueError("Verteilung: beste Leitung bevorzugen oder alle gleichzeitig")
        with self.lock:
            self.data["settings"] = {"min_kbps": mn, "max_kbps": mx, "latency_ms": lat, "uplinks": sorted(set(ups)),
                                     "spread": spread}
            self.save()


PIP_CORNERS = ("oben links", "oben rechts", "unten links", "unten rechts", "unten Mitte", "frei (verschiebbar)")
FREE = 5                      # Nummer von "frei": Position aus x/y (Promille)
PLUGIN_SO = "/opt/pipbox/gst/libgstpbpip.so"
SEND_STATUS = "/run/pipbox-send/status.json"
SWAP_SELECT = "main-select"            # Datei im Zustandsordner: welche Kamera ist Hauptbild (liest der Baustein pbctl)
SWAP_STATE = "/run/pipbox-send/swap-state"   # hier meldet pbctl zurück, was er eingestellt hat
_CENTER = {"mtime": None, "ok": True, "free": True}


def pip_corners():
    """Wählbare Positionen. "unten Mitte" und "frei" nur, wenn der installierte Baustein sie kennt (sonst landet das Bild oben links)."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _CENTER["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _CENTER.update(mtime=mt, ok=b"4 unten Mitte" in data, free=b"5 frei" in data)
    except OSError:
        return PIP_CORNERS          # kein Baustein (Entwicklungsrechner, Demo): alle anzeigen
    if not _CENTER["ok"]:
        return PIP_CORNERS[:4]
    return PIP_CORNERS if _CENTER["free"] else PIP_CORNERS[:5]


_MULTI = {"mtime": None, "ok": True}


def plugin_multi():
    """Kennt der installierte Baustein den gemeinsamen Mischer für bis zu drei kleine Bilder (Eigenschaft "slot3")?
    Ein alter Baustein (z. B. wenn der Neubau beim Update scheiterte) bekommt die alte Form mit zwei Mischern."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _MULTI["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                _MULTI.update(mtime=mt, ok=b"slot3" in f.read())
    except OSError:
        return True
    return _MULTI["ok"]


_SWAP = {"mtime": None, "ok": True}


def plugin_swap():
    """Kennt der installierte Baustein den Umschalter für den Tausch ohne Unterbrechung (pbpipsel)? Ohne Baustein (Entwicklungsrechner) ja."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _SWAP["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _SWAP.update(mtime=mt, ok=b"pbpipsel" in data and b"follow-tag" in data)
    except OSError:
        return True
    return _SWAP["ok"]


_STYLE = {"mtime": None, "ok": True}


def plugin_style():
    """Kennt der installierte Baustein Beschnitt, Deckkraft und Rahmen je kleinem Bild (Eigenschaften "style1" bis "style3")?
    Ein alter Baustein (z. B. wenn der Neubau beim Update scheiterte) würde die Eigenschaft nicht kennen und die Sendekette
    nicht starten: dann bleibt sie aus dem Pipeline-Text weg. Ohne Baustein (Entwicklungsrechner) ja."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _STYLE["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _STYLE.update(mtime=mt, ok=b"style1" in data and b"style3" in data)
    except OSError:
        return True
    return _STYLE["ok"]


# Aussehen der kleinen Bilder (Deckkraft, Beschnitt, Rahmen), je Stelle 1 bis 3 im Bild-in-Bild. Beschnitt in Pixeln eines Bildes von
# 1920 x 1080 (so groß ist die Bezugsgröße jeder Kamera; der Baustein rechnet auf die verkleinerte Größe um), Rahmenbreite und Rundung
# in Pixeln eines 1920 Pixel breiten Hauptbildes.
STYLE_SLOTS = ("1", "2", "3")
CROP_REF_W, CROP_REF_H, CROP_KEEP = 1920, 1080, 32          # Bezugsgröße des Beschnitts, so viele Pixel bleiben mindestens stehen
STYLE_DEFAULT = {"visible": True, "opacity": 100, "crop": {"l": 0, "r": 0, "t": 0, "b": 0},
                 "border": {"enabled": False, "width": 6, "color": "#ffffff", "opacity": 100, "radius": 12}}
STYLE_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _style_copy(st):
    return {"visible": st["visible"], "opacity": st["opacity"], "crop": dict(st["crop"]), "border": dict(st["border"])}


def clean_style(src, old=None, strict=False):
    """Eine Stileinstellung prüfen und ergänzen. strict (Anfrage der Oberfläche): Fehler werden gemeldet. Sonst (gespeicherte Datei,
    pipeline.json gehört dem Benutzer pipbox, build() läuft auch als root): alles wird in feste Bereiche gezwungen, es gelangt nie
    ein Text in den Pipeline-Text. old: bisherige Werte für fehlende Felder."""
    st = _style_copy(old if isinstance(old, dict) and "crop" in old and "border" in old else STYLE_DEFAULT)
    if src is None:
        src = {}
    if not isinstance(src, dict):
        if strict:
            raise ValueError("Aussehen des kleinen Bildes: ungültige Angabe")
        src = {}

    def num(v, lo, hi, cur, what):
        if isinstance(v, bool) or not isinstance(v, (int, float)) and not (isinstance(v, str) and v.strip().lstrip("-").isdigit()):
            if strict:
                raise ValueError(f"{what} muss eine Zahl sein")
            return cur
        n = int(float(v))
        if strict and not lo <= n <= hi:
            raise ValueError(f"{what}: {lo} bis {hi}")
        return max(lo, min(hi, n))
    if "visible" in src:
        if isinstance(src["visible"], bool):
            st["visible"] = src["visible"]
        elif strict:
            raise ValueError("Kleines Bild zeigen: ja oder nein")
    if "opacity" in src:
        st["opacity"] = num(src["opacity"], 0, 100, st["opacity"], "Deckkraft")
    crop = src.get("crop")
    if isinstance(crop, dict):
        for k, lbl in (("l", "links"), ("r", "rechts"), ("t", "oben"), ("b", "unten")):
            if k in crop:
                st["crop"][k] = num(crop[k], 0, 1900, st["crop"][k], f"Beschnitt {lbl}") // 2 * 2     # gerade: Chroma ist halb so groß
    elif crop is not None and strict:
        raise ValueError("Beschnitt: ungültige Angabe")
    c = st["crop"]
    for a, b, total in (("l", "r", CROP_REF_W), ("t", "b", CROP_REF_H)):
        room = total - CROP_KEEP
        if c[a] + c[b] > room:
            if strict:
                raise ValueError(f"Beschnitt: {'links plus rechts' if a == 'l' else 'oben plus unten'} höchstens {room} Pixel "
                                 f"(es bleiben mindestens {CROP_KEEP} Pixel stehen)")
            k = room / float(c[a] + c[b])
            c[a], c[b] = int(c[a] * k) // 2 * 2, int(c[b] * k) // 2 * 2
    border = src.get("border")
    if isinstance(border, dict):
        b = st["border"]
        if "enabled" in border:
            if isinstance(border["enabled"], bool):
                b["enabled"] = border["enabled"]
            elif strict:
                raise ValueError("Rahmen: ein oder aus")
        if "width" in border:
            b["width"] = num(border["width"], 1, 40, b["width"], "Rahmendicke")
        if "color" in border:
            if isinstance(border["color"], str) and STYLE_COLOR_RE.match(border["color"]):
                b["color"] = border["color"].lower()
            elif strict:
                raise ValueError("Rahmenfarbe: Farbe wie #ffffff")
        if "opacity" in border:
            b["opacity"] = num(border["opacity"], 10, 100, b["opacity"], "Rahmendeckkraft")
        if "radius" in border:
            b["radius"] = num(border["radius"], 0, 60, b["radius"], "Eckenrundung")
    elif border is not None and strict:
        raise ValueError("Rahmen: ungültige Angabe")
    return st


def clean_styles(src, old=None, strict=False):
    """Die Stile der Stellen 1 bis 3 (Schlüssel "1", "2", "3")."""
    if src is not None and not isinstance(src, dict):
        if strict:
            raise ValueError("Aussehen der kleinen Bilder: ungültige Angabe")
        src = None
    old = old if isinstance(old, dict) else {}
    return {k: clean_style((src or {}).get(k), old.get(k), strict) for k in STYLE_SLOTS}


def style_text(st):
    """Eigenschaft style<N> des Bausteins (Format siehe gst/gstpbpip.c), nur mit Werten, die vom Standard abweichen; leer, wenn alles
    Standard ist. st ist eine geprüfte Stileinstellung (clean_style): nur Zahlen und eine Hex-Farbe gelangen in den Text."""
    parts = []
    op = st["opacity"] if st["visible"] else 0
    if op != 100:
        parts.append(f"op={int(op)}")
    for key, name in (("l", "cl"), ("r", "cr"), ("t", "ct"), ("b", "cb")):
        if st["crop"][key]:
            parts.append(f"{name}={int(st['crop'][key])}")
    b = st["border"]
    if b["enabled"]:
        parts.append(f"bw={int(b['width'])}")
        parts.append(f"bc={b['color'][1:]}")
        if b["opacity"] != 100:
            parts.append(f"bo={int(b['opacity'])}")
        if b["radius"]:
            parts.append(f"br={int(b['radius'])}")
    return ",".join(parts)


def pip_size(pct):
    """Breite/Höhe des kleinen Bildes in Pixel (gerade, durch 16 teilbar in der Breite)."""
    w = max(16, int(round(1920 * pct / 100.0 / 16.0)) * 16)
    h = int(round(w * 9 / 16.0 / 2.0)) * 2
    return w, h


DEFAULT_MAIN_DELAY_MS = 450   # Ausgangswert (Schätzung, per Regler anpassbar): das Hauptbild wartet so lange, damit die kleinen Bilder zeitlich passen
DEFAULT_PIP_DELAY_MS = 0      # kleine Bilder: zusätzliche Wartezeit je Bild (Feinabgleich, wenn eine Kamera mehr hinterherhinkt)
DELAY_KEYS = ("main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "pip3_delay_ms")
FRAME_MS = 33                 # eine Warteschlange gibt erst nach der Schwelle frei: beim Bild ein Bild zugeben


PENDING_LABELS = (("server", "SRTLA-Server"), ("bitrate", "Bitrate"), ("latency", "Latenz"), ("spread", "Verteilung"))


def srtla_signature(data):
    """Kurze Prüfsummen der Einstellungen, die erst beim Start der Sendekette wirken (ohne die Stream-ID preiszugeben).
    Die Sendekette merkt sich ihre Werte beim Start; weichen die gespeicherten davon ab, ist ein Neustart nötig."""
    cur = next((x for x in data.get("servers", []) if x.get("id") == data.get("selected")), None) or {}
    st = {**SrtlaStore.DEFAULT_SETTINGS, **data.get("settings", {})}

    def h(*a):
        return hashlib.sha256(json.dumps(a, sort_keys=True).encode()).hexdigest()[:12]
    try:
        mn, mx, lat = int(st["min_kbps"]), int(st["max_kbps"]), int(st["latency_ms"])
    except (TypeError, ValueError):
        mn = mx = lat = 0
    return {"server": h(cur.get("id"), cur.get("host"), cur.get("port"), cur.get("streamid")),
            "bitrate": h(mn, mx), "latency": h(lat), "spread": h("all" if st.get("spread") == "all" else "best")}


class PipelineStore:
    """Welche Pipeline gesendet wird und welche Kameras sie nutzt.

    Typ "single": eine Kamera. Typ "pip": Hauptbild plus kleines Bild. Die Pipeline
    ist ein Text für belacoder, abgeleitet von BELABOX' Standard-Pipeline für RTMP
    (h265_rtmp_localhost_publish_live_30fps). Das kleine Bild verkleinert der
    Hardware-Decoder selbst; unser Baustein schreibt es nur in das Hauptbild
    (lesen wäre auf diesem Chip zu langsam).
    """
    DEFAULT = {"type": "single", "main": "", "pip": "", "corner": 3, "size_pct": 25, "audio": "main",
               "pip2": "", "corner2": 2, "pip3": "", "corner3": 0, "x": 500, "y": 500, "x2": 500, "y2": 500, "x3": 500, "y3": 500, "main_delay_ms": DEFAULT_MAIN_DELAY_MS,
               "pip_delay_ms": DEFAULT_PIP_DELAY_MS, "pip2_delay_ms": DEFAULT_PIP_DELAY_MS,
               "pip3_delay_ms": DEFAULT_PIP_DELAY_MS, "auto_failover": True, "swap_cams": 0}
    Q = "queue max-size-time=10000000000 max-size-buffers=1000 max-size-bytes=41943040"

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.cfg = dict(self.DEFAULT)
        try:
            with open(path) as f:
                self.cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.cfg["styles"] = clean_styles(self.cfg.get("styles"))        # ältere Dateien kennen sie noch nicht

    def save(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f, indent=1)
        os.replace(tmp, self.path)
        self.write_delay_file()

    def write_delay_file(self):
        """Wert für die laufende Sendekette (liest unser Baustein pbctl alle 0,3 s). Atomar ersetzen. Läuft die Sendekette im Tausch-Betrieb,
        gilt die Reihenfolge der Kameras beim Aufbau (die Verzögerung gehört zur Kamera, nicht zum Platz)."""
        if self.path == os.devnull:
            return
        d = os.path.join(os.path.dirname(os.path.abspath(self.path)), "main-delay-ms")
        try:
            base = None
            try:
                with open(SEND_STATUS) as f:
                    base = (json.load(f).get("swap") or {}).get("cams")
            except (OSError, ValueError, AttributeError):
                pass
            vals = self.delay_values(self.cfg, base if isinstance(base, list) else None)
            with open(d + ".tmp", "w") as f:
                f.write(" ".join(map(str, vals)) + "\n")
            os.chmod(d + ".tmp", 0o644)
            os.replace(d + ".tmp", d)
        except OSError:
            pass

    SLOT_DELAY = {"pip": "pip_delay_ms", "pip2": "pip2_delay_ms", "pip3": "pip3_delay_ms"}

    def swap_main_pip(self, with_key=None):
        """Hauptbild gegen eine Kamera tauschen, die gerade als kleines Bild im Bild ist (Szenenwechsel, Stufe 1). Ohne Angabe
        das erste kleine Bild. Kamera und Verzögerung bleiben beisammen (die Verzögerung gehört zur Kamera); Ecke, Größe,
        Position und die Wahl des Tons (Hauptbild oder kleines Bild) bleiben am Platz."""
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip" or not c.get("pip") or not c.get("main"):
                raise ValueError("Zum Tauschen braucht es ein Hauptbild und ein kleines Bild")
            slot = "pip"
            if with_key:
                slot = next((k for k in self.SLOT_DELAY if c.get(k) == with_key), None)
                if slot is None:
                    raise ValueError("Diese Kamera ist gerade nicht als kleines Bild im Bild")
            dk = self.SLOT_DELAY[slot]
            c["main"], c[slot] = c[slot], c["main"]
            c["main_delay_ms"], c[dk] = c.get(dk, 0), c.get("main_delay_ms", 0)
            self.save()

    def set(self, req, camera_keys):
        t = req.get("type")
        if t not in ("single", "pip"):
            raise ValueError("Art des Bildaufbaus unbekannt")
        main = str(req.get("main", ""))
        if main not in camera_keys:
            raise ValueError("Hauptkamera: bitte eine vorhandene Kamera wählen")
        cfg = {"type": t, "main": main, "pip": "", "corner": 3, "size_pct": 25, "audio": "main",
               "pip2": "", "corner2": 2, "pip3": "", "corner3": 0, "x": 500, "y": 500, "x2": 500, "y2": 500, "x3": 500, "y3": 500, "main_delay_ms": DEFAULT_MAIN_DELAY_MS,
               "pip_delay_ms": DEFAULT_PIP_DELAY_MS, "pip2_delay_ms": DEFAULT_PIP_DELAY_MS,
               "pip3_delay_ms": DEFAULT_PIP_DELAY_MS, "auto_failover": req.get("auto_failover", True) is not False, "swap_cams": 0}
        cfg["styles"] = clean_styles(req.get("styles"), self.cfg.get("styles"), strict=True)       # Deckkraft, Beschnitt, Rahmen je kleinem Bild
        if t == "pip":
            try:
                swap = int(req.get("swap_cams", 0) or 0)
            except (TypeError, ValueError):
                raise ValueError("Tausch: aus, 2 oder 4 Kameras")
            if swap not in (0, 2, 4):
                raise ValueError("Tausch: aus, 2 oder 4 Kameras")
            cfg["swap_cams"] = swap
            pipk = str(req.get("pip", ""))
            if pipk not in camera_keys or pipk == main:
                raise ValueError("Kleines Bild: eine andere vorhandene Kamera wählen")
            try:
                corner, pct = int(req.get("corner", 3)), int(req.get("size_pct", 25))
            except (TypeError, ValueError):
                raise ValueError("Ecke und Größe müssen Zahlen sein")
            if corner not in range(len(pip_corners())) or not 15 <= pct <= 40:
                raise ValueError("Position aus der Liste wählen, Größe 15 bis 40 Prozent der Bildbreite")
            audio = req.get("audio", "main")
            if audio not in ("main", "pip", "pip2", "pip3"):
                raise ValueError("Ton: Hauptbild oder eines der kleinen Bilder")
            cfg.update(pip=pipk, corner=corner, size_pct=pct, audio=audio)
            for key in ("x", "y", "x2", "y2", "x3", "y3"):
                try:
                    val = int(req.get(key, 500))
                except (TypeError, ValueError):
                    raise ValueError("Position muss eine Zahl sein")
                cfg[key] = max(0, min(1000, val))
            for key, dflt in (("main_delay_ms", DEFAULT_MAIN_DELAY_MS), ("pip_delay_ms", DEFAULT_PIP_DELAY_MS),
                              ("pip2_delay_ms", DEFAULT_PIP_DELAY_MS), ("pip3_delay_ms", DEFAULT_PIP_DELAY_MS)):
                try:
                    delay = int(req.get(key, dflt)) if req.get(key) != "" else 0
                except (TypeError, ValueError):
                    raise ValueError("Verzögerung muss eine Zahl sein (Millisekunden)")
                if not 0 <= delay <= 3000:
                    raise ValueError("Verzögerung: 0 bis 3000 ms")
                cfg[key] = delay
            pip2 = str(req.get("pip2", "") or "")
            if pip2:
                try:
                    corner2 = int(req.get("corner2", 2))
                except (TypeError, ValueError):
                    raise ValueError("Ecke muss eine Zahl sein")
                if pip2 not in camera_keys or pip2 in (main, pipk):
                    raise ValueError("Zweites kleines Bild: eine weitere vorhandene Kamera wählen")
                if corner2 not in range(len(pip_corners())) or (corner2 == corner and corner != FREE):
                    raise ValueError("Zweites kleines Bild: eine andere Position als das erste wählen")
                cfg.update(pip2=pip2, corner2=corner2)
            else:
                cfg["pip2_delay_ms"] = 0
            pip3 = str(req.get("pip3", "") or "")
            if pip3 and pip2:
                try:
                    corner3 = int(req.get("corner3", 0))
                except (TypeError, ValueError):
                    raise ValueError("Ecke muss eine Zahl sein")
                if pip3 not in camera_keys or pip3 in (main, pipk, pip2):
                    raise ValueError("Drittes kleines Bild: eine weitere vorhandene Kamera wählen")
                if corner3 not in range(len(pip_corners())) or (corner3 != FREE and corner3 in (corner, cfg["corner2"])):
                    raise ValueError("Drittes kleines Bild: eine andere Position als die ersten beiden wählen")
                cfg.update(pip3=pip3, corner3=corner3)
            else:
                cfg["pip3_delay_ms"] = 0
            if cfg["audio"] == "pip2" and not cfg["pip2"] or cfg["audio"] == "pip3" and not cfg["pip3"]:
                raise ValueError("Ton: dieses kleine Bild ist nicht gewählt")
        with self.lock:
            self.cfg = cfg
            self.save()

    @classmethod
    def _safe_cfg(cls, c):
        """Zahlenfelder zu Zahlen in festen Bereichen zwingen. build() läuft über pipbox_send auch als root, und die
        Datei pipeline.json gehört dem Benutzer pipbox: ein eingeschleuster Text darf nie in den Pipeline-Text gelangen."""
        out = dict(c)

        def num(k, lo, hi):
            try:
                v = int(out.get(k, cls.DEFAULT[k]))
            except (TypeError, ValueError):
                v = cls.DEFAULT[k]
            out[k] = max(lo, min(hi, v))
        for k in ("corner", "corner2", "corner3"):
            num(k, 0, len(PIP_CORNERS) - 1)
        num("size_pct", 15, 40)
        for k in ("x", "y", "x2", "y2", "x3", "y3"):
            num(k, 0, 1000)
        for k in ("main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "pip3_delay_ms"):
            num(k, 0, 3000)
        num("swap_cams", 0, 4)
        if out["swap_cams"] not in (2, 4):
            out["swap_cams"] = 0
        out["type"] = "pip" if out.get("type") == "pip" else "single"
        out["styles"] = clean_styles(out.get("styles"))
        return out

    @classmethod
    def _layout(cls, c):
        """Welche kleinen Bilder wirklich dabei sind: (pip, pip2, pip3, multi). c ist eine geprüfte Einstellung (_safe_cfg)."""
        pip = c["type"] == "pip" and bool(KEY_RE.match(c.get("pip", "")))
        pip2 = bool(pip and c.get("pip2") and KEY_RE.match(c["pip2"]) and c.get("corner2") in range(len(PIP_CORNERS))
                    and (c["corner2"] != c["corner"] or c["corner"] == FREE))
        multi = plugin_multi()
        pip3 = bool(multi and pip2 and c.get("pip3") and KEY_RE.match(c["pip3"])
                    and c.get("corner3") in range(len(PIP_CORNERS)) and (c["corner3"] == FREE or c["corner3"] not in (c["corner"], c["corner2"])))
        return pip, pip2, pip3, multi

    @classmethod
    def swap_plan(cls, cfg):
        """Tausch ohne Unterbrechung (Hauptbild gegen eine Kamera tauschen, ohne den Encoder neu zu starten): Plan oder None.
        Jede Kamera der Tauschgruppe bekommt zwei Zweige (groß und klein), ein Umschalter wählt das Hauptbild. Gruppe: Hauptbild und
        erstes kleines Bild (swap_cams = 2) oder alle (4). cams: Kameras in der Reihenfolge des Aufbaus (Hauptbild, kleine Bilder 1 bis 3);
        state/line: Anfangszustand (Hauptkamera, dann Kamera an Stelle 1 bis 3, 15 = keine), audio_pos: -1 Ton folgt dem Hauptbild,
        0 bis 2 Ton der Kamera an dieser Stelle; asel: Ton läuft ebenfalls über einen Umschalter."""
        c = cls._safe_cfg(cfg)
        if c["type"] != "pip" or c["swap_cams"] not in (2, 4) or not KEY_RE.match(c.get("main", "")) or not plugin_swap():
            return None
        pip, pip2, pip3, multi = cls._layout(c)
        if not pip or not multi:
            return None
        cams = [c["main"], c["pip"]] + ([c["pip2"]] if pip2 else []) + ([c["pip3"]] if pip3 else [])
        if len(set(cams)) != len(cams):
            return None
        group = min(c["swap_cams"], len(cams))
        audio = c.get("audio", "main")
        if audio == "pip2" and not pip2 or audio == "pip3" and not pip3 or audio not in ("main", "pip", "pip2", "pip3"):
            audio = "main"
        pos = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}[audio]
        nums = [0, 1, 2 if pip2 else 15, 3 if pip3 else 15]
        return {"cams": cams, "group": group, "audio_pos": pos, "asel": pos < 0 or pos + 1 < group,
                "state": nums[0] | nums[1] << 4 | nums[2] << 8 | nums[3] << 12, "line": " ".join(map(str, nums))}

    @staticmethod
    def swap_line(slots, cams, group):
        """Umschaltzeile für die laufende Sendekette aus der Belegung (Hauptbild, kleine Bilder 1 bis 3; "" = leer) und den Kameras
        beim Aufbau. Der Tausch geht nur innerhalb der Gruppe; alles andere bleibt am Platz. None, wenn das nicht passt."""
        slots = [(s or "") for s in slots] + [""] * (4 - len(slots))
        if len(cams) < 2 or group < 2 or group > len(cams) or slots[len(cams):] != [""] * (4 - len(cams)):
            return None
        if sorted(slots[:group]) != sorted(cams[:group]):
            return None
        if any(slots[i] != cams[i] for i in range(group, len(cams))):
            return None
        return " ".join([str(cams.index(slots[0]))] + [str(cams.index(s)) if s else "15" for s in slots[1:]])

    @staticmethod
    def delay_values(cfg, base=None):
        """Verzögerungen in ms für die Steuerdatei, in der Reihenfolge der Kameras beim Aufbau der Sendekette (base: deren Schlüssel;
        sonst die Reihenfolge der Einstellung). Die Verzögerung gehört zur Kamera, nicht zum Platz."""
        if cfg.get("type") != "pip":
            return [0, 0, 0, 0]
        slots = ("main", "pip", "pip2", "pip3")
        by_key = {cfg[s]: cfg.get(d, 0) for s, d in zip(slots, DELAY_KEYS) if cfg.get(s)}
        order = list(base) if base else [cfg.get(s) for s in slots]
        vals = []
        for k in (order + [None] * 4)[:4]:
            try:
                vals.append(max(0, min(3000, int(by_key.get(k, 0) or 0))) if k else 0)
            except (TypeError, ValueError):
                vals.append(0)
        return vals

    def build(self, cfg=None, rtmp_port=1935, rtmp_app="publish"):
        """Pipeline-Text für belacoder. Die Schlüssel sind geprüft (a-z, 0-9, -, _)."""
        c = self._safe_cfg(cfg or self.cfg)
        if not KEY_RE.match(c.get("main", "")):
            return ""
        q = self.Q
        base = f"rtmp://127.0.0.1:{rtmp_port}/{rtmp_app}"
        pip, pip2, pip3, multi = self._layout(c)

        def xy(cfg_, kx, ky, corner_):
            if corner_ != FREE:
                return ""
            return f" {kx}={int(cfg_.get(kx, 500))} {ky}={int(cfg_.get(ky, 500))}"
        sty = self._style_props(c)
        out = []
        # Hauptbild (samt Ton) verzögern: die kleinen Bilder treffen dann zeitlich besser auf das Hauptbild.
        # Die Wartezeit sitzt NACH dem Auspacken (dort haben die Pakete die Zeitstempel der Kamera) und gilt für
        # Bild und Ton gleich, damit beide zueinander synchron bleiben (gemessen: Abweichung unter einem Bild).
        delay = 0
        if c["type"] == "pip":
            try:
                delay = max(0, min(3000, int(c.get("main_delay_ms", DEFAULT_MAIN_DELAY_MS))))
            except (TypeError, ValueError):
                delay = 0
        qa = q + (f" min-threshold-time={delay * 1000000}" if delay else "")
        qv = q + (f" min-threshold-time={(delay + FRAME_MS) * 1000000}" if delay else "")
        if pip:
            qv, qa = qv + " name=mainq_v", qa + " name=mainq_a"

        def small(name, key):
            d = 0
            try:
                d = max(0, min(3000, int(c.get(key, DEFAULT_PIP_DELAY_MS) or 0)))
            except (TypeError, ValueError):
                d = 0
            th = d + FRAME_MS if d else 0
            return (f"queue name={name} max-size-time={(th + 500) * 1000000} max-size-buffers={0 if d else 30} "
                    f"leaky=downstream" + (f" min-threshold-time={th * 1000000}" if d else ""))
        plan = self.swap_plan(c) if pip else None
        if plan:
            return self._build_dual(c, plan, base, small, xy, pip2, pip3, sty)
        out.append(f"rtmpsrc location={base}/{c['main']} do-timestamp=true !\nflvdemux name=demux\n")
        if pip:
            # Kein videorate/textoverlay: sie halten das Hauptbild fest, dann wäre das Hineinschreiben
            # nicht mehr in-place (gemessen: bremst). Die Kameras liefern ohnehin 30 fps.
            v = (f"demux.video !\n{qv} !\nidentity name=v_delay signal-handoffs=TRUE ! h264parse ! mppvideodec !\n"
                 "video/x-raw,format=NV12 !\n"
                 f"pbpipmix name=pipmix corner={c['corner']}{xy(c, 'x', 'y', c['corner'])} width-pct={c['size_pct']}{sty(0, 1)}"
                 + (f" slot2=1 corner2={c['corner2']}{xy(c, 'x2', 'y2', c['corner2'])}{sty(1, 2)}" if pip2 and multi else "")
                 + (f" slot3=2 corner3={c['corner3']}{xy(c, 'x3', 'y3', c['corner3'])}{sty(2, 3)}" if pip3 else "") + " !\n"
                 + (f"pbpipmix name=pipmix2 slot=1 corner={c['corner2']}{xy(c, 'x', 'y', c['corner2'])} width-pct={c['size_pct']}{sty(1, 1)} !\n" if pip2 and not multi else "") +
                 "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n")
        else:
            v = (f"demux.video !\n{q} !\nidentity name=v_delay signal-handoffs=TRUE ! h264parse ! mppvideodec !\n"
                 "videorate ! video/x-raw,framerate=30/1,format=NV12 ! queue !\n")
        v += ("mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
              f"h265parse config-interval=-1 ! {q} ! mux.\n")
        out.append(v)
        opus = ("audioconvert ! audioresample quality=10 sinc-filter-mode=1 ! opusenc bitrate=128000 ! opusparse")
        audio_sel = c.get("audio", "main") if pip else "main"
        if audio_sel == "pip2" and not pip2 or audio_sel == "pip3" and not pip3 or audio_sel not in ("main", "pip", "pip2", "pip3"):
            audio_sel = "main"
        main_audio = audio_sel == "main"
        mute = "queue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n"
        def small_audio(demux, sel):
            if audio_sel == sel:
                return (f"{demux}.audio !\n{q} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n"
                        f"{opus} ! {q} ! mux.\n")
            return f"{demux}.audio !\n{mute}"
        if main_audio:
            out.append(f"demux.audio !\n{qa} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n"
                       f"{opus} ! {q} ! mux.\n")
        else:
            out.append("demux.audio !\nqueue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n")
        if pip:
            w, h = pip_size(c["size_pct"])
            out.append(f"rtmpsrc location={base}/{c['pip']} do-timestamp=true !\nflvdemux name=pdemux\n")
            out.append(f"pdemux.video !\n{small('pipq_v', 'pip_delay_ms')} !\n"
                       f"h264parse ! mppvideodec width={w} height={h} !\nvideo/x-raw,format=NV12 !\n"
                       "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink\n")
            if pip2:
                out.append(f"rtmpsrc location={base}/{c['pip2']} do-timestamp=true !\nflvdemux name=p2demux\n")
                out.append(f"p2demux.video !\n{small('pip2q_v', 'pip2_delay_ms')} !\n"
                           f"h264parse ! mppvideodec width={w} height={h} !\nvideo/x-raw,format=NV12 !\n"
                           "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot=1\n")
                out.append(small_audio("p2demux", "pip2"))
            if pip3:
                out.append(f"rtmpsrc location={base}/{c['pip3']} do-timestamp=true !\nflvdemux name=p3demux\n")
                out.append(f"p3demux.video !\n{small('pip3q_v', 'pip3_delay_ms')} !\n"
                           f"h264parse ! mppvideodec width={w} height={h} !\nvideo/x-raw,format=NV12 !\n"
                           "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot=2\n")
                out.append(small_audio("p3demux", "pip3"))
            out.append(small_audio("pdemux", "pip"))
        if pip:
            # Steuerung: liest die Verzögerung aus /var/lib/pipbox/main-delay-ms und stellt die beiden Queues im Betrieb um
            out.append("pbctl name=pbctl video-queue=mainq_v audio-queue=mainq_a pip-queue=pipq_v"
                       + (" pip2-queue=pip2q_v" if pip2 else "") + (" pip3-queue=pip3q_v" if pip3 else "") + "\n")
        out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
        return "\n".join(out)

    @staticmethod
    def _style_props(c):
        """Funktion (Stelle 0 bis 2, Nummer der Eigenschaft) -> Text " styleN=\"...\"" für pbpipmix, leer bei Standardaussehen oder wenn der
        installierte Baustein die Eigenschaft nicht kennt."""
        texts = [style_text(c["styles"][k]) for k in STYLE_SLOTS] if plugin_style() else ["", "", ""]

        def sty(pos, n):
            return f' style{n}="{texts[pos]}"' if texts[pos] else ""
        return sty

    def _build_dual(self, c, plan, base, small, xy, pip2, pip3, sty):
        """Pipeline für den Tausch ohne Unterbrechung (siehe swap_plan). Jede Kamera der Gruppe liefert ihr Bild zweimal: groß in den
        Umschalter vsel (Hauptbild), klein in einen eigenen Platz des Bild-in-Bild (Platz = Kamera + 3 mod 4: Kamera 0 -> 3, 1 -> 0 ...).
        Welche Kamera gerade Hauptbild ist und wo die anderen kleiner erscheinen, schreibt vsel in jedes Bild; pbpipmix liest es dort.
        Der Ton läuft bei Bedarf über einen zweiten Umschalter asel, den pbctl im selben Schritt mitstellt. Die Wartezeit gehört je
        Kamera zu allen ihren Warteschlangen (vfq = Bild groß, vsq = Bild klein, aq = Ton)."""
        q = self.Q
        cams, group, ap = plan["cams"], plan["group"], plan["audio_pos"]
        w, h = pip_size(c["size_pct"])
        out = []
        out.append(f"pbpipsel name=vsel tag-offset=true force-key=true state={plan['state']} !\n"
                   "identity name=v_delay signal-handoffs=TRUE !\nvideo/x-raw,format=NV12 !\n"
                   f"pbpipmix name=pipmix follow-tag=true corner={c['corner']}{xy(c, 'x', 'y', c['corner'])} width-pct={c['size_pct']}{sty(0, 1)}"
                   + (f" slot2=1 corner2={c['corner2']}{xy(c, 'x2', 'y2', c['corner2'])}{sty(1, 2)}" if pip2 else "")
                   + (f" slot3=2 corner3={c['corner3']}{xy(c, 'x3', 'y3', c['corner3'])}{sty(2, 3)}" if pip3 else "") + " !\n"
                   "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n"
                   "mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
                   f"h265parse config-interval=-1 ! {q} ! mux.\n")
        opus = "audioconvert ! audioresample quality=10 sinc-filter-mode=1 ! opusenc bitrate=128000 ! opusparse"
        mute = "queue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n"
        queues = []
        for i, key in enumerate(cams):
            d = int(c.get(DELAY_KEYS[i], 0) or 0)
            items = [f"vsq{i}:s"]
            out.append(f"rtmpsrc location={base}/{key} do-timestamp=true !\nflvdemux name=dm{i}\n")
            small_chain = (f"{small(f'vsq{i}', DELAY_KEYS[i])} !\nh264parse ! mppvideodec width={w} height={h} !\nvideo/x-raw,format=NV12 !\n"
                           f"queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot={(i + 3) % 4}\n")
            if i < group:
                qv = f"{q} name=vfq{i}" + (f" min-threshold-time={(d + FRAME_MS) * 1000000}" if d else "")
                out.append(f"dm{i}.video !\ntee name=vt{i}\n")
                out.append(f"vt{i}. !\n{qv} !\nh264parse ! mppvideodec !\nvideo/x-raw,format=NV12 !\nvsel.sink_{i}\n")
                out.append(f"vt{i}. !\n{small_chain}")
                items.insert(0, f"vfq{i}:v")
            else:
                out.append(f"dm{i}.video !\n{small_chain}")
            qa = f"{q} name=aq{i}" + (f" min-threshold-time={d * 1000000}" if d else "")
            if plan["asel"] and i < group:
                out.append(f"dm{i}.audio !\n{qa} !\naacparse ! avdec_aac ! audioconvert ! audioresample quality=10 sinc-filter-mode=1 !\n"
                           f"audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved ! asel.sink_{i}\n")
                items.append(f"aq{i}:a")
            elif not plan["asel"] and i == ap + 1:
                out.append(f"dm{i}.audio !\n{qa} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n{opus} ! {q} ! mux.\n")
                items.append(f"aq{i}:a")
            else:
                out.append(f"dm{i}.audio !\n{mute}")
            queues.append(f" cam{i}={','.join(items)}")
        if plan["asel"]:
            out.append(f"pbpipsel name=asel state={0 if ap < 0 else ap + 1} !\nidentity name=a_delay signal-handoffs=TRUE !\n"
                       f"opusenc bitrate=128000 ! opusparse ! {q} ! mux.\n")
        out.append("pbctl name=pbctl selector=vsel" + (" audio-selector=asel" if plan["asel"] else "") + f" audio-pos={ap}" + "".join(queues) + "\n")
        out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
        return "\n".join(out)

    def status(self, cams):
        keys = [c["key"] for c in cams]
        return {"config": dict(self.cfg, styles=clean_styles(self.cfg.get("styles"))), "plugin_style": plugin_style(), "cameras": [{"key": c["key"], "name": c["name"], "state": c.get("state", "unknown")} for c in cams],
                "corners": list(pip_corners()), "preview": self.build() if self.cfg.get("main") in keys else "",
                "needs_plugin": self.cfg.get("type") == "pip", "plugin_present": os.path.exists("/opt/pipbox/gst/libgstpbpip.so")}


class SendControl:
    """Live gehen / beenden. Die eigentliche Sendekette läuft als Root-Dienst (pipbox-send);
    wir stellen nur Zustand und Voraussetzungen dar und legen eine Anforderungsdatei ab."""
    STATUS = "/run/pipbox-send/status.json"
    SWAP_WAIT = 2.0               # so lange auf die Rückmeldung des Bausteins warten (Sekunden)

    def __init__(self, state_dir, srtla, pipeline, cams, demo=False):
        self.req = os.path.join(state_dir, "send-request")
        self.srtla, self.pipeline, self.cams, self.demo = srtla, pipeline, cams, demo

    def _active(self):
        if self.demo:
            return False
        hit = getattr(self, "_act", None)
        if hit is not None and time.monotonic() - hit[0] < 1.5:      # mehrere Abfragen pro Takt teilen sich ein systemctl
            return hit[1]
        try:
            r = subprocess.run(["systemctl", "is-active", "pipbox-send.service"],
                               capture_output=True, text=True, timeout=4)
            val = r.stdout.strip() in ("active", "activating", "deactivating")
        except (OSError, subprocess.TimeoutExpired):
            val = False
        self._act = (time.monotonic(), val)
        return val

    def _detail(self):
        try:
            with open(self.STATUS) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def picture(self):
        """Welche eingestellte Kamera ist gerade im Bild? {Schlüssel: ("an"|"wartet"|"aus", Sekunden bis zur Aufnahme)}
        oder None, wenn nicht gesendet wird oder keine Automatik läuft."""
        if not self._active():
            return None
        fo = self._detail().get("failover")
        if not fo:
            return None
        layout, wait = set(fo.get("layout") or []), fo.get("wait") or {}
        out = {}
        for k in fo.get("configured") or []:
            if k in layout:
                out[k] = ("an", 0)
            elif k in wait:
                out[k] = ("wartet", int(wait[k]))
            else:
                out[k] = ("aus", 0)
        return out

    def reasons(self):
        r = []
        pub = self.srtla.public()
        if not pub["selected"]:
            r.append("Kein SRTLA-Server ausgewählt")
        cfg = self.pipeline.cfg
        if not cfg.get("main"):
            r.append("Kein Bildaufbau gespeichert")
        else:
            live = {c["key"]: c.get("state") for c in self.cams.listing("")}
            keys = [k for k in (cfg["main"], cfg.get("pip", ""), cfg.get("pip2", ""), cfg.get("pip3", "")) if k]
            if cfg.get("auto_failover", True) and len(set(keys)) > 1:
                # Automatisch umschalten: es genügt, wenn mindestens eine Kamera sendet
                if not any(live.get(k) == "live" for k in keys):
                    r.append("Keine Kamera sendet gerade")
            else:
                for k in sorted(set(keys)):
                    if live.get(k) != "live":
                        r.append(f"Kamera {k} sendet gerade nicht")
        if cfg.get("type") == "pip" and not os.path.exists("/opt/pipbox/gst/libgstpbpip.so"):
            r.append("Bild-in-Bild: der Überlagerungs-Baustein ist noch nicht installiert")
        return r

    def status(self):
        active = self._active()
        d = self._detail()
        pub = self.srtla.public()
        sel = next((s for s in pub["servers"] if s["id"] == pub["selected"]), None)
        state = d.get("state") if active else ("refused" if d.get("state") == "refused" else "stopped")
        out = {"active": active, "state": state or "stopped", "message": d.get("message", ""), "last": d.get("last", ""), "last_age": d.get("last_age"),
               "since": d.get("since"), "restarts": d.get("restarts"), "delay_live": bool(d.get("delay_live")), "delay_live_pips": bool(d.get("delay_live_pips")),
               "server": sel and {"name": sel["name"], "host": sel["host"], "port": sel["port"]}}
        out["failover"] = d.get("failover") if active else None
        sw = d.get("swap") if active else None
        out["swap_group"] = [k for k in sw["cams"][:int(sw.get("group", 0))] if isinstance(k, str)] if isinstance(sw, dict) and isinstance(sw.get("cams"), list) else []
        applied = d.get("applied") if active else None
        cur = srtla_signature(self.srtla.data)
        out["pending"] = [lab for k, lab in PENDING_LABELS if isinstance(applied, dict) and k in applied and applied[k] != cur[k]]
        out["reasons"] = [] if active else self.reasons()
        if not active and belacoder_running():
            out["reasons"].append("Es läuft schon ein belacoder (z. B. über die BELABOX-Oberfläche)")
        out["can_start"] = not active and not out["reasons"]
        return out

    def request(self, action, confirm):
        if action == "start":
            if not confirm:
                raise ValueError("Bestätigung fehlt")
            st = self.status()
            if st["active"]:
                raise ValueError("Es wird schon gesendet")
            if not st["can_start"]:
                raise ValueError("Senden nicht möglich: " + "; ".join(st["reasons"]))
        elif action == "restart":
            why = [] if self.demo else self.reasons()
            if why:
                raise ValueError("Neustart nicht möglich: " + "; ".join(why))
        elif action != "stop":
            raise ValueError("Unbekannte Aktion")
        self._act = None
        if self.demo:
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


    def delay_live(self):
        """Hat die laufende Sendekette den Steuerbaustein (Verzögerung des Hauptbildes ohne Neustart änderbar)?"""
        d = self._detail()
        # Im Notbetrieb (Anordnung weicht von der Einstellung ab) passen die Regler nicht zu den Plätzen: neu starten
        return bool(self._active() and d.get("delay_live") and not (d.get("failover") or {}).get("degraded"))

    def delay_live_pips(self):
        """Dasselbe für die Verzögerung der kleinen Bilder (neuere Version des Steuerbausteins)."""
        d = self._detail()
        return bool(self._active() and d.get("delay_live_pips") and not (d.get("failover") or {}).get("degraded"))

    def swap_live(self):
        """Tausch des Hauptbilds in der laufenden Sendekette übernehmen lassen, ohne Neustart (nur im Tausch-Betrieb und wenn die
        Anordnung der Kameras noch zum Aufbau passt). Wahr, wenn der Baustein den neuen Zustand zurückgemeldet hat; sonst bleibt der Neustart."""
        if self.demo or not self._active():
            return False
        d = self._detail()
        sw = d.get("swap")
        if not isinstance(sw, dict) or not isinstance(sw.get("cams"), list) or (d.get("failover") or {}).get("degraded"):
            return False
        cfg = self.pipeline.cfg
        slots = [cfg.get(k) for k in ("main", "pip", "pip2", "pip3")]
        try:
            line = PipelineStore.swap_line(slots, sw["cams"], int(sw.get("group", 0)))
            if line is None:
                return False
            live = {c["key"]: c.get("state") for c in self.cams.listing("")}
            if live.get(cfg.get("main")) != "live":          # die neue Hauptkamera sendet nicht: lieber neu starten (Notbetrieb)
                return False
        except (TypeError, ValueError, KeyError):
            return False
        folder = os.path.dirname(self.req)
        tmp = os.path.join(folder, SWAP_SELECT + ".tmp")
        t0 = time.time()
        try:
            with open(tmp, "w") as f:
                f.write(line + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(folder, SWAP_SELECT))
        except OSError:
            return False
        end = time.monotonic() + self.SWAP_WAIT
        while time.monotonic() < end:                       # der Baustein liest alle 0,1 s und meldet den Zustand zurück
            try:
                if os.stat(SWAP_STATE).st_mtime >= t0 - 0.2:
                    with open(SWAP_STATE) as f:
                        if f.read().split() == line.split():
                            return True
            except OSError:
                pass
            time.sleep(0.1)
        return False

    def restart_if_live(self):
        """Nach geänderter Pipeline: läuft die Sendekette, wird sie kurz neu gestartet.
        Vorher wird geprüft, ob die neue Pipeline überhaupt starten kann; sonst läuft die alte weiter.
        Gibt (neu gestartet?, Hinweis) zurück."""
        if not self._active():
            return False, ""
        why = self.reasons()
        if why:
            return False, "Gespeichert, aber die laufende Übertragung wurde nicht neu gestartet: " + "; ".join(why)
        self.request("restart", True)
        return True, "Gespeichert. Die Übertragung wird jetzt kurz neu gestartet."


KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ROLES = ("main", "pip", "extra")


class CameraStore:
    """RTMP-Kameras: Name, Rolle und Stream-Schlüssel in einer JSON-Datei.

    Die Kamera sendet an rtmp://<Box>:1935/<app>/<Schlüssel>. Ob sie gerade
    sendet, kommt aus der nginx-rtmp-Statistik (--rtmp-stat-url); ohne diese
    Adresse ist der Status "unbekannt".
    """

    def __init__(self, path, app, stat_url, demo):
        self.path, self.app, self.stat_url, self.demo = path, app, stat_url, demo
        self.lock = threading.Lock()
        self.cams = []
        self.ipfn = lan_ip
        self.ifaces = iface_ips     # in Tests ersetzbar
        try:
            with open(path) as f:
                self.cams = json.load(f)
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cams, f, indent=1)
        os.replace(tmp, self.path)

    def add(self, name, key, role):
        name = (name or "").strip()[:40]
        if not name:
            raise ValueError("Name fehlt")
        if role not in ROLES:
            raise ValueError("Rolle ungültig")
        key = (key or "").strip().lower() or "cam-" + secrets.token_hex(3)
        if not KEY_RE.match(key):
            raise ValueError("Schlüssel: nur a-z, 0-9, - und _, höchstens 32 Zeichen")
        with self.lock:
            if any(c["key"] == key for c in self.cams):
                raise ValueError("Schlüssel existiert schon")
            if role in ("main", "pip") and any(c["role"] == role for c in self.cams):
                raise ValueError("Diese Rolle ist schon vergeben")
            cam = {"id": secrets.token_hex(4), "name": name, "key": key, "role": role}
            self.cams.append(cam)
            self.save()
        return cam

    def remove(self, cam_id):
        with self.lock:
            n = len(self.cams)
            self.cams = [c for c in self.cams if c["id"] != cam_id]
            if len(self.cams) != n:
                self.save()
            return len(self.cams) != n

    def auto_add(self):
        """Unbekannte, sendende Streams automatisch als Kamera aufnehmen."""
        live = self.live_streams() or {}
        added = []
        with self.lock:
            known = {c["key"] for c in self.cams}
            for key in sorted(live):
                if key in known or not KEY_RE.match(key) or key.startswith("test-"):
                    continue   # "test-…" sind Testquellen und gehören nicht in die Kameraliste
                roles = {c["role"] for c in self.cams}
                role = "main" if "main" not in roles else "pip" if "pip" not in roles else "extra"
                cam = {"id": secrets.token_hex(4), "name": f"Kamera {key}", "key": key,
                       "role": role, "auto": True}
                self.cams.append(cam)
                added.append(cam)
            if added:
                self.save()
        return added

    def host_for_iface(self, name):
        """Adresse der Box in der Verbindung <name> (feste Zweitadresse, sonst die der Schnittstelle) oder None, wenn es sie nicht gibt."""
        for o in self.ifaces():
            if o["iface"] == name:
                return o.get("cam_ip") or o["ip"]
        return None

    def update(self, cam_id, name=None, role=None, iface=None):
        with self.lock:
            cam = next((c for c in self.cams if c["id"] == cam_id), None)
            if not cam:
                raise KeyError(cam_id)
            if iface is not None:
                if not isinstance(iface, str):
                    raise ValueError("Verbindung ungültig")
                if iface and self.host_for_iface(iface) is None:
                    raise ValueError("Unbekannte Verbindung oder keine IPv4-Adresse")
                if iface:
                    cam["iface"] = iface
                else:
                    cam.pop("iface", None)                 # leer: wieder die Hauptverbindung
            if name is not None:
                name = name.strip()[:40]
                if not name:
                    raise ValueError("Name fehlt")
                if any(c["id"] != cam_id and c["name"].casefold() == name.casefold() for c in self.cams):
                    raise ValueError("Diesen Namen hat schon eine andere Kamera")
                cam["name"] = name
            if role is not None:
                if role not in ROLES:
                    raise ValueError("Rolle ungültig")
                if role in ("main", "pip") and any(
                        c["role"] == role and c["id"] != cam_id for c in self.cams):
                    raise ValueError("Diese Rolle ist schon vergeben")
                cam["role"] = role
            self.save()
            return cam

    def live_streams(self):
        """Dict Schlüssel -> {fps, mbit} oder None wenn Statistik nicht lesbar."""
        if self.demo:
            return {c["key"]: {"fps": 30.0, "mbit": round(6 + random.random() * 2, 1)}
                    for i, c in enumerate(self.cams) if i % 3 != 2}
        if not self.stat_url:
            return None
        hit = getattr(self, "_live", None)
        if hit is not None and time.monotonic() - hit[0] < 1.5:      # mehrere Abfragen pro Takt teilen sich eine Statistik
            return hit[1]
        res = self._live_read()
        self._live = (time.monotonic(), res)
        return res

    def _live_read(self):
        try:
            with urllib.request.urlopen(self.stat_url, timeout=2) as r:
                root = ET.fromstring(r.read())
        except (OSError, ET.ParseError):
            return None
        res = {}
        for app in root.iter("application"):
            if app.findtext("name") != self.app:
                continue
            for st in app.iter("stream"):
                if st.find("publishing") is None:
                    continue
                bw = (int(st.findtext("bw_video") or 0) + int(st.findtext("bw_audio") or 0))
                fps = st.findtext("meta/video/frame_rate")
                key = st.findtext("name")
                res[key] = {"fps": float(fps) if fps else None, "mbit": round(bw / 1e6, 1)}
        return res

    def listing(self, host, addr_for=None):
        """Kameras mit Adresse und Zustand. Die Adresse gilt für die Verbindung der Kamera: DJI-Kameras nach ihrer Karte
        (addr_for(Schlüssel) -> (Adresse, Verbindung) oder None), andere Kameras nach der gewählten Verbindung (Feld iface),
        sonst nach der Hauptverbindung (Kameras lösen keine .local-Namen auf, darum immer eine IP-Adresse)."""
        main = self.ipfn()
        live = self.live_streams()
        out = []
        for c in self.cams:
            st = None if live is None else live.get(c["key"])
            via, src, host = None, "main", main
            over = addr_for(c["key"]) if addr_for else None
            if over and over[0]:
                host, via, src = over[0], over[1], "dji"
            elif c.get("iface") and self.host_for_iface(c["iface"]):
                host, via, src = self.host_for_iface(c["iface"]), c["iface"], "own"
            out.append({**c, "via": via, "via_src": src,
                        "url": f"rtmp://{host}:1935/{self.app}/{c['key']}",
                        "state": "unknown" if live is None else
                                 ("live" if st else "offline"),
                        "fps": st and st["fps"], "mbit": st and st["mbit"]})
        return out


SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 30 * 24 * 3600     # "Angemeldet bleiben": überlebt Updates und Neustarts
MAX_FAILS, FAIL_WINDOW = 5, 300


class Auth:
    """Ein Passwort für die Oberfläche.

    Auf einer BELABOX gilt das Passwort der belaUI (bcrypt-Hash in deren config.json, nur gelesen). Hat die BELABOX noch keins
    (frisches Image), wartet die Oberfläche darauf: Es wird zuerst in der BELABOX-Oberfläche festgelegt, hier gibt es dann
    weder einen Setup-Code noch ein zweites Passwort ("belabox-wartet").
    Nur ohne belaUI (Entwicklung, Demo): eigenes Passwort. Erster Start: Der Server schreibt einen Setup-Code in
    <state>/setup-code (Rechte 0600, Besitzer pipbox). Wer den Code kennt, legt im Browser das Passwort fest; danach wird die
    Codedatei gelöscht. Passwort nur als PBKDF2-HMAC-SHA256-Hash. Ein eigenes Passwort aus einer früheren Version bleibt gültig.
    Demo (--demo auf dem eigenen Rechner): Das Passwort ist von Anfang an gesetzt (DEMO_PASSWORD, nur im Speicher), ohne Setup-Code;
    die Anmeldeseite füllt es vor. Nie auf einer Box: dort gilt immer das BELABOX-Passwort.
    "Angemeldet bleiben": Eine solche Sitzung gilt 30 Tage und übersteht Neustarts der Oberfläche (z. B. nach einem Update). Auf der Platte
    liegt nur ein SHA-256 des Sitzungsschlüssels (<state>/sessions.json, Rechte 0600) mit der Kennung des Passworts: Ändert sich das
    Passwort (BELABOX oder eigenes), sind diese Sitzungen ungültig. Ohne Haken bleibt die Sitzung nur im Speicher (12 Stunden).
    """

    BELA_JS = ("const b=require(process.argv[1]);let d='';"
               "process.stdin.on('data',c=>d+=c).on('end',()=>"
               "process.exit(b.compareSync(d,process.argv[2])?0:1))")

    DEMO_PASSWORD = "demo"

    def __init__(self, state_dir, bela_config=None, demo=False):
        self.bela_config = bela_config  # belaUI config.json: BELABOX-Passwort mitbenutzen
        self.demo = bool(demo)
        self.dir = state_dir
        self.path = os.path.join(state_dir, "auth.json")
        self.code_path = os.path.join(state_dir, "setup-code")
        self.sessions = {}
        self.store_path = os.path.join(state_dir, "sessions.json")
        self.fails = {}
        self.lock = threading.Lock()
        os.makedirs(state_dir, exist_ok=True)
        self.cfg = None
        try:
            with open(self.path) as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            pass
        self.setup_code = None
        if self.demo and not self.bela_hash():
            salt = secrets.token_bytes(16)       # Vorschau: Passwort von Anfang an gesetzt, nichts auf der Platte
            self.cfg = {"salt": salt.hex(), "hash": self._hash(self.DEMO_PASSWORD, salt).hex()}
            try:
                os.remove(self.code_path)
            except OSError:
                pass
        elif not self.cfg and not self.bela_hash() and not self.bela_pending():
            self.setup_code = secrets.token_urlsafe(6)
            fd = os.open(self.code_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.setup_code + "\n")
        else:
            try:
                os.remove(self.code_path)     # ein Code von früher ist nicht mehr nötig
            except OSError:
                pass

    def _cred(self):
        """Kennung des gültigen Passworts: ändert es sich, werden gemerkte Sitzungen ungültig."""
        h = self.bela_hash() or ("demo" if self.demo else (self.cfg or {}).get("hash")) or ""   # Demo: Salt ist bei jedem Start neu
        return hashlib.sha256(("pbcred:" + h).encode()).hexdigest()

    @staticmethod
    def _tok_id(tok):
        return hashlib.sha256(tok.encode()).hexdigest()

    def _remembered(self):
        try:
            with open(self.store_path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _save_remembered(self, d):
        tmp = self.store_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.store_path)

    def bela_hash(self):
        """bcrypt-Hash des BELABOX-Passworts (nur gelesen) oder None."""
        if not self.bela_config:
            return None
        try:
            with open(self.bela_config) as f:
                h = json.load(f).get("password_hash")
        except (OSError, ValueError):
            return None
        return h if isinstance(h, str) and h.startswith("$2") else None

    def bela_ok(self, pw, h):
        """Prüft mit dem Node.js/bcrypt von belaUI; Passwort nur über stdin."""
        mod = os.path.join(os.path.dirname(self.bela_config), "node_modules", "bcrypt")
        try:
            r = subprocess.run(["node", "-e", self.BELA_JS, mod, h],
                               input=pw.encode(), timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("BELABOX-Passwortprüfung nicht möglich")
        if r.returncode not in (0, 1):
            raise RuntimeError("BELABOX-Passwortprüfung nicht möglich")
        return r.returncode == 0

    def bela_pending(self):
        """Läuft die Oberfläche auf einer BELABOX, die noch kein Passwort hat? (belaUI ist da, ihre Konfiguration enthält aber keinen Hash)"""
        if not self.bela_config or self.bela_hash():
            return False
        return os.path.isdir(os.path.dirname(os.path.abspath(self.bela_config)))

    @property
    def mode(self):
        if self.bela_hash():
            return "belabox"
        if self.demo and self.cfg:
            return "demo"
        if self.cfg:
            return "own"                      # eigenes Passwort aus einer früheren Version bleibt gültig
        return "belabox-wartet" if self.bela_pending() else "own"

    @property
    def configured(self):
        return bool(self.cfg) or self.mode == "belabox"

    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 300000, dklen=32)

    def throttled(self, ip):
        now = time.time()
        with self.lock:
            f = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            self.fails[ip] = f
            return len(f) >= MAX_FAILS

    def fail(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    WAITING_MSG = "Auf der BELABOX ist noch kein Passwort gesetzt. Bitte zuerst in der BELABOX-Oberfläche eines festlegen."

    def set_password(self, code, pw, ip):
        if self.mode == "belabox-wartet":
            raise ValueError(self.WAITING_MSG)
        if self.configured or not self.setup_code:
            raise ValueError("Passwort ist schon gesetzt")
        if self.throttled(ip):
            raise PermissionError("Zu viele Versuche, bitte 5 Minuten warten")
        if not hmac.compare_digest(code or "", self.setup_code):
            self.fail(ip)
            raise ValueError("Setup-Code falsch")
        if len(pw or "") < 10:
            raise ValueError("Passwort: mindestens 10 Zeichen")
        salt = secrets.token_bytes(16)
        self.cfg = {"salt": salt.hex(), "hash": self._hash(pw, salt).hex()}
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f)
        os.replace(tmp, self.path)
        self.setup_code = None
        try:
            os.remove(self.code_path)
        except OSError:
            pass

    def login(self, pw, ip, remember=False):
        if not self.configured:
            raise ValueError(self.WAITING_MSG if self.mode == "belabox-wartet" else "Noch kein Passwort gesetzt")
        if self.throttled(ip):
            raise PermissionError("Zu viele Versuche, bitte 5 Minuten warten")
        bh = self.bela_hash()
        if bh:
            good = self.bela_ok(pw or "", bh)
        else:
            good = hmac.compare_digest(self._hash(pw or "", bytes.fromhex(self.cfg["salt"])),
                                       bytes.fromhex(self.cfg["hash"]))
        if not good:
            self.fail(ip)
            raise ValueError("Passwort falsch")
        tok = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {t: e for t, e in self.sessions.items() if e > now}
            self.sessions[tok] = now + SESSION_SECONDS
            if remember:
                keep = {k: v for k, v in self._remembered().items()
                        if isinstance(v, list) and len(v) == 2 and v[0] > now and v[1] == self._cred()}
                keep[self._tok_id(tok)] = [now + REMEMBER_SECONDS, self._cred()]
                try:
                    self._save_remembered(keep)
                except OSError:
                    pass          # dann gilt die Sitzung wie ohne Haken
        return tok

    def valid(self, tok):
        if not tok:
            return False
        with self.lock:
            if self.sessions.get(tok, 0) > time.time():
                return True
            e = self._remembered().get(self._tok_id(tok))
            return bool(isinstance(e, list) and len(e) == 2 and e[0] > time.time() and e[1] == self._cred())

    def logout(self, tok):
        with self.lock:
            self.sessions.pop(tok or "", None)
            r = self._remembered()
            if r.pop(self._tok_id(tok or ""), None) is not None:
                try:
                    self._save_remembered(r)
                except OSError:
                    pass


KEY_PACKAGES = ("belabox-linux-rk3588", "belaui", "belacoder", "belabox-rk3588")


def belacoder_running():
    try:
        for d in os.listdir("/proc"):
            if d.isdigit() and (read(f"/proc/{d}/comm", "") or "").strip() == "belacoder":
                return True
    except OSError:
        pass
    return False


def installed_versions():
    """Versionen wichtiger Pakete aus /var/lib/dpkg/status (nur lesend)."""
    try:
        st = os.stat("/var/lib/dpkg/status")
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    hit = _TTL.get("dpkg")
    if hit is not None and hit[0] == sig and sig is not None:
        return dict(hit[1])
    res = {}
    for block in (read("/var/lib/dpkg/status", "") or "").split("\n\n"):
        f = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
        if f.get("Package") in KEY_PACKAGES and "installed" in f.get("Status", "") and "not-installed" not in f.get("Status", ""):
            res[f["Package"]] = f.get("Version", "?")
    _TTL["dpkg"] = (sig, dict(res))
    return res


VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}(-[a-z0-9.]{1,16})?$")


def vkey(v):
    """Sortierschlüssel einer Versionsnummer (Release vor Vorabversion)."""
    core, _, pre = v.partition("-")
    return tuple(int(x) for x in core.split(".")), (1, ()) if not pre else (0, tuple(pre.split(".")))


class Remote:
    """Fernzugriff über Tailscale. Lesen darf dieser Dienst (tailscale status), Verändern macht der Root-Helfer
    pipbox-remote.py über eine Auslösedatei mit einem Stichwort aus fester Liste. Funnel (öffentlich im Internet) gibt es nur auf
    ausdrückliche Anforderung (funnel_on mit Bestätigung "public"); sie hat keine Zeitgrenze und bleibt auch nach einem Neustart der
    Box bis zum Beenden bestehen."""
    STATUS = "/run/pipbox-remote/status.json"
    ACTIONS = ("install", "login", "down", "serve_on", "serve_off", "funnel_on", "funnel_off", "logout")

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "remote-request")
        self.demo = demo
        self.fake = {}

    def _ts(self, *args):
        """tailscale-Abfrage, 20 s zwischengespeichert (das Go-Programm zu starten kostet spürbar CPU). Ändert der Root-Helfer
        etwas (seine Statusdatei bekommt eine neue Zeit), gilt der Speicher als abgelaufen."""
        try:
            sig = os.stat(self.STATUS).st_mtime_ns
        except OSError:
            sig = None
        key = ("ts", args)
        hit = _TTL.get(key)
        if hit is not None and hit[2] == sig and time.monotonic() - hit[0] < 20:
            return hit[1]
        try:
            r = subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=6)
            val = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            val = {}
        _TTL[key] = (time.monotonic(), val, sig)
        return val

    def status(self):
        if self.demo:
            d = {"installed": True, "backend": "Running", "connected": True, "name": "irl4you-box.demo.ts.net",
                 "ip": "100.64.0.1", "tailnet": "demo",
                 "serve": bool(self.fake.get("serve")), "url": "https://irl4you-box.demo.ts.net/" if self.fake.get("serve") else "",
                 "funnel": bool(self.fake.get("funnel")),
                 "state": "idle", "step": "", "message": self.fake.get("message", ""), "login_url": "",
                 "hint_url": "", "helper_installed": True}
            return d
        installed = bool(shutil.which("tailscale"))
        out = {"installed": installed, "backend": "", "connected": False, "name": "", "ip": "", "tailnet": "",
               "serve": False, "url": "", "funnel": False, "state": "idle", "step": "", "message": "", "login_url": "",
               "hint_url": "", "helper_installed": os.path.exists("/etc/systemd/system/pipbox-remote.path")}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        out.update(state=h.get("state", "idle"), step=h.get("step", ""), message=h.get("message", ""),
                   login_url=h.get("login_url", ""), hint_url=h.get("hint_url", ""))
        if h.get("time") and out["state"] not in ("working",) and time.time() - h["time"] > 6 * 3600:
            out["message"] = ""
        if not installed:
            return out
        d = self._ts("status", "--json")
        out["backend"] = d.get("BackendState", "")
        out["connected"] = out["backend"] == "Running"
        me = d.get("Self") or {}
        out["name"] = str(me.get("DNSName", "")).rstrip(".")
        ips = d.get("TailscaleIPs") or []
        out["ip"] = next((i for i in ips if ":" not in i), "")
        out["tailnet"] = (d.get("CurrentTailnet") or {}).get("Name", "")
        if d.get("AuthURL"):
            out["login_url"] = d["AuthURL"]
        if out["connected"]:
            out["login_url"] = ""
            sv = self._ts("serve", "status", "--json")
            web = sv.get("Web") or {}
            for host, cfg in web.items():
                for h2 in (cfg.get("Handlers") or {}).values():
                    if "127.0.0.1:%d" % 8780 in str(h2.get("Proxy", "")):
                        out["serve"], out["url"] = True, "https://" + host.replace(":443", "") + "/"
            out["funnel"] = any((sv.get("AllowFunnel") or {}).values())
        return out

    def request(self, action, confirm, public=False):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        if action == "funnel_on" and public is not True:
            raise ValueError("Die öffentliche Freigabe braucht eine ausdrückliche Bestätigung")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Fernzugriff-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working":
            raise ValueError("Es läuft schon eine Aktion")
        if action == "install" and st["installed"]:
            raise ValueError("Tailscale ist schon installiert")
        if action != "install" and not st["installed"]:
            raise ValueError("Tailscale ist noch nicht installiert")
        if action == "funnel_on" and not st["connected"]:
            raise ValueError("Zuerst mit Tailscale verbinden")
        if action == "funnel_off" and not st["funnel"]:
            raise ValueError("Die öffentliche Freigabe ist nicht an")
        if self.demo:
            if action == "serve_on":
                self.fake = {"serve": True, "message": "Demo: freigegeben."}
            elif action == "serve_off":
                self.fake = {"serve": False, "message": "Demo: beendet."}
            elif action == "funnel_on":
                self.fake = {"serve": True, "funnel": True, "message": "Demo: öffentlich freigegeben."}
            elif action == "funnel_off":
                self.fake = {"serve": True, "funnel": False, "message": "Demo: öffentliche Freigabe beendet."}
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


class DeviceNames:
    """Eigene Namen für WLAN- und Bluetooth-Sticks (der Stick kennt seinen Handelsnamen, zum Beispiel Logilink, meist nicht). Schlüssel: die USB-Kennung
    ("usb:0bda:c811"; zwei gleiche Sticks teilen sich den Namen), sonst die Schnittstelle ("if:wlan0") oder die Adresse des Bluetooth-Adapters
    ("bt:AA:BB:CC:DD:EE:FF"). Die Datei steht im Zustandsordner; es sind keine Geheimnisse."""
    KEY_RE = re.compile(r"^(usb:[0-9a-f]{4}:[0-9a-f]{4}|if:[a-z0-9]{2,15}|bt:([0-9A-F]{2}:){5}[0-9A-F]{2})$")

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "device-names.json")
        self.lock = threading.Lock()

    def _all(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if isinstance(k, str) and self.KEY_RE.match(k) and isinstance(v, str)} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def key(usb_id="", iface="", address=""):
        if usb_id:
            return "usb:" + usb_id
        if iface:
            return "if:" + iface
        return "bt:" + address.upper() if address else ""

    def label(self, key, default):
        return self._all().get(key) or default

    def set(self, key, name):
        """Name setzen (leer = Standardname). Prüft Schlüssel und Name streng."""
        if not isinstance(key, str) or not self.KEY_RE.match(key):
            raise ValueError("Gerät unbekannt")
        if not isinstance(name, str):
            raise ValueError("Name ungültig")
        name = " ".join(name.split())
        if len(name) > 40 or any(not ch.isprintable() for ch in name):
            raise ValueError("Name: höchstens 40 Zeichen, keine Sonderzeichen")
        with self.lock:
            d = self._all()
            if name:
                d[key] = name
            else:
                d.pop(key, None)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            with os.fdopen(fd, "w") as f:
                json.dump(d, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)


def label_bluetooth(st, names):
    """Anzeigenamen der Bluetooth-Adapter und der Sticks ohne Adapter in der Antwort des Bluetooth-Dienstes: eigener Name (names), sonst der
    Standardname aus der Meldung des Sticks (Hersteller davor, wenn der Name nur eine Standardbezeichnung ist)."""
    for a in st.get("adapters") or []:
        a["key"] = DeviceNames.key(a.get("usb_id", ""), "", a.get("address", ""))
        default = dji.device_label(a.get("name", ""), a.get("vendor", "")) or ("Eingebauter Bluetooth-Adapter" if not a.get("usb_id") else "Bluetooth-Stick")
        a["label"] = names.label(a["key"], default) if names and a["key"] else default
        a["custom"] = a["label"] != default
    for x in st.get("adapter_problems") or []:
        x["key"] = DeviceNames.key(x.get("id", ""))
        default = dji.device_label(x.get("name", ""), "")
        x["label"] = names.label(x["key"], default) if names and x["key"] else default
    return st


class Wifi:
    """WLAN-Verbindung (z. B. Handy-Hotspot als weiterer Sendeweg). Lesen darf dieser Dienst, Verbinden macht der
    Root-Helfer pipbox-wifi.py über eine Auslösedatei (0600, wird dort sofort gelöscht). Das Passwort liegt nur
    dort und wird nie gespeichert oder ausgegeben; nmcli legt das Profil an."""
    STATUS = "/run/pipbox-wifi/status.json"
    ACTIONS = ("scan", "connect", "forget", "disconnect")

    def __init__(self, state_dir, demo, netchoice, names=None):
        self.req = os.path.join(state_dir, "wifi-request")
        self.demo, self.netchoice, self.names = demo, netchoice, names

    def cards(self):
        out = []
        try:
            names = sorted(os.listdir("/sys/class/net"))
        except OSError:
            return out
        ips = {o["iface"]: o["ip"] for o in iface_ips()}
        for n in names:
            if os.path.isdir(f"/sys/class/net/{n}/wireless") and not n.startswith("p2p"):
                info = dji.netdev_info(n)                          # Name des WLAN-Sticks (z. B. "802.11ac NIC"), USB-Kennung, Treiber
                out.append({"iface": n, "ip": ips.get(n, ""), "camera_net": n == self.netchoice.iface,
                            "up": (read(f"/sys/class/net/{n}/operstate", "") or "").strip() == "up",
                            "ssid": "", "signal": None, "name": info["name"], "vendor": info["vendor"],
                            "usb_id": info["usb_id"], "driver": info["driver"]})
                self._name(out[-1])
        if out:                                        # Name und Signal des verbundenen Netzes (Profilname = SSID)
            try:
                r = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev"], capture_output=True, text=True, timeout=4)
                for line in r.stdout.splitlines():
                    dev, _, con = line.partition(":")
                    for c in out:
                        if c["iface"] == dev and c["ip"]:
                            c["ssid"] = con.replace("\\:", ":")
                for c in out:
                    if c["ssid"]:
                        r = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list", "ifname", c["iface"],
                                            "--rescan", "no"], capture_output=True, text=True, timeout=4)
                        for line in r.stdout.splitlines():
                            if line.startswith("*:") and line[2:].isdigit():
                                c["signal"] = int(line[2:])
            except (OSError, subprocess.TimeoutExpired):
                pass
        return out

    def _name(self, card):
        """Schlüssel und Anzeigename der Karte: eigener Name, sonst der Standardname aus der Meldung des Sticks."""
        card["key"] = DeviceNames.key(card.get("usb_id", ""), card["iface"])
        default = dji.device_label(card.get("name", ""), card.get("vendor", ""))
        card["label"] = self.names.label(card["key"], default) if self.names else default
        card["custom"] = bool(self.names and card["label"] != default)

    def status(self):
        if self.demo:
            demo_card = {"iface": "wlan0", "ip": "10.0.0.5", "camera_net": False, "up": True, "ssid": "Demo-Hotspot", "signal": 80,
                         "name": "802.11ac NIC", "vendor": "Realtek", "usb_id": "0bda:c811", "driver": "rtl8821cu"}
            self._name(demo_card)
            return {"helper_installed": True, "cards": [demo_card],
                    "state": "idle", "message": "", "scan": {"iface": "wlan0", "nets": [
                        {"ssid": "Demo-Hotspot", "signal": 80, "security": "WPA2", "in_use": False},
                        {"ssid": "Mein Handy", "signal": 62, "security": "WPA2 WPA3", "in_use": False},
                        {"ssid": "Gast", "signal": 31, "security": "offen", "in_use": False}]}, "saved": []}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        return {"helper_installed": os.path.exists("/etc/systemd/system/pipbox-wifi.path"), "cards": self.cards(),
                "state": h.get("state", "idle"), "message": h.get("message", ""), "scan": h.get("scan") or {},
                "saved": h.get("saved") or [], "time": h.get("time", 0)}

    def request(self, d):
        action = d.get("action")
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der WLAN-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working" and time.time() - st.get("time", 0) < 90:
            raise ValueError("Es läuft schon eine Aktion")
        iface = str(d.get("iface", ""))
        if action in ("scan", "connect", "disconnect"):
            card = next((c for c in st["cards"] if c["iface"] == iface), None)
            if not card:
                raise ValueError("Diese WLAN-Karte gibt es nicht")
            if card["camera_net"]:
                raise ValueError("Diese Karte ist das Kameranetz")
        req = {"action": action, "iface": iface}
        if action in ("connect", "forget"):
            ssid = d.get("ssid")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
                raise ValueError("Netzname fehlt oder ist zu lang")
            req["ssid"] = ssid
        if action == "connect":
            pw = d.get("password", "")
            if not isinstance(pw, str) or len(pw) > 64:
                raise ValueError("Passwort ungültig")
            req.update(password=pw, hidden=d.get("hidden") is True)
        if self.demo:
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(req, f)


class Power:
    """Box herunterfahren oder neu starten. Dieser Dienst hat keine Root-Rechte: er legt nur ein Stichwort aus fester Liste
    in eine Auslösedatei, der Root-Helfer pipbox-power.py führt es aus."""
    ACTIONS = ("poweroff", "reboot")

    def __init__(self, state_dir, demo, send):
        self.req = os.path.join(state_dir, "power-request")
        self.demo, self.send = demo, send

    def status(self):
        sending = False if self.demo else bool(belacoder_running() or self.send._active())
        return {"helper_installed": self.demo or os.path.exists("/etc/systemd/system/pipbox-power.path"), "sending": sending}

    def request(self, action, confirm):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        if not self.status()["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (install.sh erneut ausführen)")
        if self.demo:
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


class AutoStart:
    """Automatisch live gehen, nachdem die Box gestartet wurde (Einstellung, standardmäßig aus).

    Nur einmal pro Start der Box: Nach dem Hochfahren wartet die Box, bis ein SRTLA-Server gewählt ist und mindestens eine
    Kamera sendet (dieselben Voraussetzungen wie "Live gehen"), und startet dann genau so, als wäre "Live gehen" gedrückt worden.
    "Live beenden" vor dem Start bricht ab; ein Neustart der Oberfläche (zum Beispiel durch ein Update) startet die Sendung
    nicht noch einmal. Findet sich innerhalb von WAIT_S Sekunden keine Kamera, gibt die Box auf und zeigt den Grund."""
    WAIT_S = 600
    RETRY_S = 20

    def __init__(self, state_dir, send, demo=False, wait_s=None, poll_s=5.0):
        self.path = os.path.join(state_dir, "autostart.json")
        self.send, self.demo = send, demo
        self.wait_s = self.WAIT_S if wait_s is None else wait_s
        self.poll_s = poll_s
        self.lock = threading.Lock()
        self.cancel_ev = threading.Event()
        self.data = {"enabled": False, "boot": ""}
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.data["enabled"] = d.get("enabled") is True
            self.data["boot"] = str(d.get("boot", ""))[:64]
        except (OSError, ValueError):
            pass
        self.phase, self.message, self.until = "aus", "", None

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @staticmethod
    def boot_id():
        return (read("/proc/sys/kernel/random/boot_id", "demo") or "demo").strip()

    def enabled(self):
        return self.data["enabled"]

    def set_enabled(self, on):
        if not isinstance(on, bool):
            raise ValueError("ja oder nein")
        with self.lock:
            self.data["enabled"] = on
            self._save()
        if not on:
            self.cancel("ausgeschaltet")

    def cancel(self, why="abgebrochen"):
        """Wartet die Box noch auf den Automatik-Start, wird er für diesen Start der Box abgebrochen."""
        with self.lock:
            if self.phase == "wartet":
                self.cancel_ev.set()
                self.phase, self.message = "abgebrochen", why
                self.data["boot"] = self.boot_id()
                self._save()

    def status(self):
        return {"enabled": self.data["enabled"], "phase": self.phase, "message": self.message, "until": self.until}

    def _finish(self, phase, message=""):
        with self.lock:
            if self.phase == "wartet":
                self.phase, self.message = phase, message
            self.data["boot"] = self.boot_id()
            self._save()

    def run(self):
        """Läuft einmal im Hintergrund, solange die Oberfläche läuft."""
        if not self.data["enabled"]:
            return
        if self.data["boot"] == self.boot_id():
            with self.lock:
                self.phase = "erledigt"
            return
        with self.lock:
            self.phase, self.message = "wartet", "Wartet auf eine sendende Kamera"
            self.until = time.time() + self.wait_s
        deadline = time.monotonic() + self.wait_s
        last_try, reasons = None, []       # None: noch kein Versuch (die Uhr zählt ab Hochfahren, 0 wäre nicht 'lange her')
        while time.monotonic() < deadline and not self.cancel_ev.is_set():
            try:
                st = self.send.status()
            except Exception:
                st = {}
            if st.get("active"):
                self._finish("gestartet", "Läuft")
                return
            reasons = st.get("reasons") or reasons
            if st.get("can_start") and (last_try is None or time.monotonic() - last_try >= self.RETRY_S):
                last_try = time.monotonic()
                try:
                    self.send.request("start", True)
                    with self.lock:
                        self.message = "Startet"
                except Exception as e:
                    reasons = [str(e)]
            self.cancel_ev.wait(self.poll_s)
        if self.cancel_ev.is_set():
            return
        self._finish("aufgegeben", "Automatischer Start aufgegeben: " + ("; ".join(reasons) if reasons else "keine sendende Kamera"))


class LogMode:
    """Protokoll-Modus: "sparsam" (Journal und Zustandsprotokoll nur im Arbeitsspeicher, schont die Speicherkarte, nach einem
    Absturz bleibt keine Spur) oder "ausfuehrlich" (dauerhaft, zur Fehlersuche). Dieser Dienst hat keine Root-Rechte: er legt
    nur ein Stichwort aus fester Liste in eine Auslösedatei, der Root-Helfer pipbox-logmode.py stellt um."""
    MODES = ("sparsam", "ausfuehrlich")
    FILE = "/etc/pipbox/logmode"

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "logmode-request")
        self.demo = demo
        self.fake = "ausfuehrlich"

    def status(self):
        if self.demo:
            return {"mode": self.fake, "helper_installed": True}
        mode = (read(self.FILE, "") or "").strip()
        # Ohne Datei (ältere Installation) schreibt alles wie bisher dauerhaft: das entspricht "ausfuehrlich"
        return {"mode": mode if mode in self.MODES else "ausfuehrlich",
                "helper_installed": os.path.exists("/etc/systemd/system/pipbox-logmode.path")}

    def request(self, mode):
        if mode not in self.MODES:
            raise ValueError("Unbekannter Modus")
        if not self.status()["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if self.demo:
            self.fake = mode
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mode + "\n")


class SwUpdate:
    """Software-Update von IRL4YOU BOX aus dem eigenen GitHub-Repository.

    Dieser Dienst hat keine Root-Rechte. Er prüft nur, ob auf GitHub eine neuere VERSION liegt, und legt auf Wunsch
    eine Auslösedatei mit einem festen Stichwort (install / rollback) ab. Laden, Prüfen und Einspielen macht der
    getrennte Root-Helfer pipbox-swupdate.py.
    """
    CHECK_EVERY = 6 * 3600     # Sekunden zwischen zwei Abfragen bei GitHub
    RETRY_AFTER_ERROR = 30 * 60   # ein Fehlversuch (kein Internet) wird früher wiederholt
    EARLY_SECONDS = 30 * 60       # in der ersten halben Stunde nach dem Start (Router und Mobilfunk brauchen oft einige Minuten) ...
    RETRY_EARLY = 3 * 60          # ... wird ein Fehlversuch schon nach 3 Minuten wiederholt
    RAW = "https://raw.githubusercontent.com/IRL4YOU/irl4you-pip/main/"
    API = "https://api.github.com/repos/IRL4YOU/irl4you-pip/releases?per_page=30"
    STATUS = "/run/pipbox-swupdate/status.json"
    BACKUP = "/var/lib/pipbox-backup"
    STAGE = "Beta"

    def __init__(self, state_dir, demo, send):
        self.req = os.path.join(state_dir, "swupdate-request")
        self.demo, self.send = demo, send
        self.lock = threading.Lock()
        self.cache, self.cache_t = None, 0.0
        self.started = time.time()
        self.rel_cache, self.rel_t = [], 0.0
        here = os.path.dirname(os.path.abspath(__file__))
        self.version = (read(os.path.join(here, "VERSION"), "") or "0.0.0").strip()
        self.fake = {}

    def _get(self, name, limit):
        req = urllib.request.Request(self.RAW + name, headers={"User-Agent": "irl4you-box"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read(limit + 1)[:limit].decode("utf-8", "replace")

    @staticmethod
    def _first_section(text):
        lines, out, started = text.splitlines(), [], False
        for l in lines:
            if l.startswith("## "):
                if started:
                    break
                started = True
            if started:
                out.append(l.rstrip())
            if len(out) >= 25:
                break
        return "\n".join(out)

    @classmethod
    def _sections_since(cls, text, current, max_sections=6, max_lines=160):
        """Die Änderungen aller Versionen, die neuer sind als die installierte (neueste zuerst), aus dem Text der CHANGELOG.md. Wer mehrere
        Versionen übersprungen hat, sieht so alles, was neu ist. Ist keine neuer (oder die Überschriften sind unbekannt), gilt der erste Abschnitt."""
        def num(v):
            m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v or "")
            return tuple(int(x) for x in m.groups()) if m else None
        cur = num(current)
        out, keep, sections = [], False, 0
        for l in text.splitlines():
            if l.startswith("## "):
                m = re.match(r"^##\s+(\d+\.\d+\.\d+)", l)
                v = num(m.group(1)) if m else None
                keep = v is not None and cur is not None and v > cur
                if keep:
                    sections += 1
                    if sections > max_sections:
                        break
            if keep:
                out.append(l.rstrip())
                if len(out) >= max_lines:
                    break
        return "\n".join(out) if out else cls._first_section(text)

    def check(self, force=False):
        """Fragt die neueste Version auf GitHub ab (höchstens alle 6 Stunden und nie während einer Übertragung, außer force)."""
        with self.lock:
            early = time.time() - self.started < self.EARLY_SECONDS
            wait = (self.RETRY_EARLY if early else self.RETRY_AFTER_ERROR) if self.cache and self.cache.get("error") else self.CHECK_EVERY
            if not force and self.cache and (time.time() - self.cache_t < wait or self.send._active()):
                return self.cache
            if not force and not self.cache and not self.demo and self.send._active():
                return {"checked_at": None}          # während der Übertragung nicht über das Mobilfunknetz nachfragen
        res = {"checked_at": int(time.time())}
        if self.demo:
            res.update(latest="0.9.1", notes="## 0.9.1 (Demo)\n- Beispiel für eine neue Version.")
        else:
            try:
                v = self._get("VERSION", 64).strip()
                if not VERSION_RE.match(v):
                    raise ValueError("ungültige Versionsnummer")
                res["latest"] = v
                try:
                    res["notes"] = self._sections_since(self._get("CHANGELOG.md", 60000), self.version)
                except OSError:
                    res["notes"] = ""
            except (OSError, ValueError):
                res["error"] = "GitHub ist nicht erreichbar oder lieferte keine gültige Versionsnummer."
        with self.lock:
            self.cache, self.cache_t = res, time.time()
        return res

    def auto_loop(self):
        """Fragt von selbst nach (alle 6 Stunden, check() hält das ein und fragt nie während einer Übertragung), damit der Punkt
        "Oberfläche" in der Kopfleiste auch dann stimmt, wenn gerade niemand die Seite offen hat."""
        time.sleep(30)
        while True:
            try:
                self.check()
            except Exception as e:                # nie den Dienst beenden
                print("swupdate auto_check:", e)
            time.sleep(60 if time.time() - self.started < self.EARLY_SECONDS else 15 * 60)

    def releases(self, force=False):
        """Veröffentlichte Versionen (Releases) auf GitHub: [{version, date, notes}], höchstens alle 6 Stunden, nie während einer Übertragung."""
        if not force and (time.time() - self.rel_t < self.CHECK_EVERY or (not self.demo and self.send._active())):
            return self.rel_cache
        out = []
        if self.demo:
            out = [{"version": "0.9.1", "date": "2026-10-02", "notes": "Demo"}, {"version": "0.9.0", "date": "2026-10-01", "notes": "Demo"}]
        else:
            try:
                req = urllib.request.Request(self.API, headers={"User-Agent": "irl4you-box", "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read(400000))
                for x in data if isinstance(data, list) else []:
                    v = str(x.get("tag_name", "")).lstrip("v")
                    if VERSION_RE.match(v) and not x.get("draft") and not x.get("prerelease"):
                        out.append({"version": v, "date": str(x.get("published_at", ""))[:10],
                                    "notes": str(x.get("body") or "")[:300]})
            except (OSError, ValueError):
                out = self.rel_cache                                 # alte Liste behalten, wenn GitHub nicht antwortet
        with self.lock:
            self.rel_cache, self.rel_t = out, time.time()
        return out

    def backups(self):
        """Lokal gesicherte Versionen, neueste zuerst (die Verzeichnisse sind lesbar, enthalten nur Programmdateien)."""
        if self.demo:
            return ["0.9.0"]
        out = []
        try:
            for n in os.listdir(self.BACKUP):
                p = os.path.join(self.BACKUP, n)
                if VERSION_RE.match(n) and os.path.isdir(os.path.join(p, "opt-pipbox")):
                    out.append((os.stat(p).st_mtime, n))
            legacy = (read(os.path.join(self.BACKUP, "VERSION"), "") or "").strip()      # frühere Ablage (eine Version)
            if VERSION_RE.match(legacy) and os.path.isdir(os.path.join(self.BACKUP, "opt-pipbox")) \
                    and legacy not in [n for _, n in out]:
                out.append((os.stat(os.path.join(self.BACKUP, "VERSION")).st_mtime, legacy))
        except OSError:
            pass
        return [n for _, n in sorted(out, reverse=True)]

    def versions(self, force=False):
        """Alle wählbaren Versionen: gesichert und/oder auf GitHub, neueste Versionsnummer zuerst."""
        local = self.backups()
        rel = {r["version"]: r for r in self.releases(force)}
        res = []
        for v in sorted(set(local) | set(rel) | {self.version}, key=vkey, reverse=True):
            res.append({"version": v, "local": v in local, "github": v in rel, "current": v == self.version,
                        "date": rel.get(v, {}).get("date", ""), "notes": rel.get(v, {}).get("notes", ""),
                        "relation": "aktuell" if v == self.version else ("neuer" if vkey(v) > vkey(self.version) else "älter")})
        return res

    def status(self, force=False):
        chk = self.check(force)
        if self.demo:
            st = dict(self.fake) or {}
        else:
            try:
                with open(self.STATUS) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
        latest = chk.get("latest")
        out = {"current": self.version, "stage": self.STAGE, "latest": latest, "notes": chk.get("notes", ""),
               "error": chk.get("error", ""), "checked_at": chk.get("checked_at"),
               "newer": bool(latest and vkey(latest) > vkey(self.version)),
               "state": st.get("state", "idle"), "step": st.get("step", ""), "message": st.get("message", ""),
               "frm": st.get("frm", ""), "to": st.get("to", "")}
        if st.get("state") in ("installing", "refused", "done", "rolledback", "failed") and st.get("time") \
                and st["state"] != "installing" and time.time() - st["time"] > 6 * 3600:
            out["state"], out["message"] = "idle", ""             # alte Meldung nach 6 Stunden ausblenden
        bk = [v for v in self.backups() if v != self.version]
        out["backup_version"] = bk[0] if bk else ""                  # Ziel des Knopfes "Zurück"
        out["versions"] = self.versions(force)
        out["sending"] = False if self.demo else bool(belacoder_running() or self.send._active())
        out["helper_installed"] = self.demo or os.path.exists("/etc/systemd/system/pipbox-swupdate.path")
        return out

    def request(self, action, confirm, version=None, older=False):
        if action not in ("install", "rollback", "switch"):
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Update-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["sending"]:
            raise ValueError("Es wird gerade gesendet. Bitte zuerst die Übertragung beenden.")
        if st["state"] == "installing":
            raise ValueError("Ein Update läuft bereits")
        if action == "install" and not st["newer"]:
            raise ValueError("Es gibt keine neuere Version")
        if action == "rollback" and not st["backup_version"]:
            raise ValueError("Es gibt keine gesicherte Version")
        if action == "switch":
            v = str(version or "").strip().lstrip("v")
            if not VERSION_RE.match(v):
                raise ValueError("Bitte eine Version wie 0.9.0 angeben")
            known = {x["version"]: x for x in st["versions"]}
            if v not in known or not (known[v]["local"] or known[v]["github"]):
                raise ValueError(f"Version {v} ist weder gesichert noch auf GitHub veröffentlicht")
            if v == self.version:
                raise ValueError(f"Version {v} ist schon installiert")
            if vkey(v) < vkey(self.version) and older is not True:
                raise ValueError("Das ist eine ältere Version. Bitte ausdrücklich bestätigen.")
            version = v
        if self.demo:
            self.fake = {"state": "done", "message": "Demo: Update vorgetäuscht.", "time": int(time.time())}
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n" + (version + "\n" if action == "switch" else ""))


class Updates:
    """Systemupdates über den getrennten Root-Helfer (pipbox-update.py).

    Dieser Dienst hat keine Root-Rechte. Er legt nur eine Auslösedatei mit einem
    Stichwort aus fester Liste ab; der Helfer führt dann die festen Aktionen aus.
    """
    MODES = ("check", "dry", "run", "reboot")

    def __init__(self, state_dir, demo):
        self.demo = demo
        self.req = os.path.join(state_dir, "update-request")
        self.status_path = os.path.join(state_dir, "update-status.json")
        self.log_path = os.path.join(state_dir, "update.log")
        self.fake = {}
        self.lock = threading.Lock()
        self.last_try = 0.0              # wann zuletzt eine stille Suche angefordert wurde (Schleife und Anmeldung teilen sich das)

    def boot_id(self):
        return (read("/proc/sys/kernel/random/boot_id", "demo") or "demo").strip()

    def reboot_done(self, st):
        """Ist die Box seit dem Neustart-Befehl schon wieder gestartet? Erst die Boot-Kennung, sonst die Zeit."""
        rb = st.get("reboot_boot_id")
        if rb and rb != self.boot_id():
            return True
        try:
            up = float((read("/proc/uptime", "") or "0").split()[0])
            return bool(st.get("finished")) and st["finished"] < time.time() - up
        except (ValueError, IndexError, TypeError):
            return False

    def status(self):
        if self.demo:
            st = dict(self.fake) or {"state": "never"}
            versions = {"belabox-linux-rk3588": "20241120-2", "belaui": "20250518-5",
                        "belacoder": "20250518-1"}
            log = st.pop("log", "")
            kernel = "5.10.160-belabox (Demo)"
        else:
            try:
                with open(self.status_path) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {"state": "never"}
            versions = installed_versions()
            log = ""
            try:
                with open(self.log_path) as f:
                    log = "".join(f.readlines()[-60:])
            except OSError:
                pass
            kernel = os.uname().release
        st["reboot_pending"] = bool(st.get("reboot_required")) and st.get("reboot_boot_id") == self.boot_id()
        if st.get("state") == "rebooting" and self.reboot_done(st):
            # Der Zustand "Neustart" stammt von vor dem letzten Start der Box und ist längst erledigt
            st["state"] = "done"
            st["message"] = "Die Box ist nach dem Neustart wieder oben."
        st["versions"] = versions
        st["kernel_running"] = kernel
        st["streaming"] = belacoder_running() if not self.demo else False
        st["helper_installed"] = self.demo or os.path.exists("/etc/systemd/system/pipbox-update.path")
        st["log"] = log
        return st

    def request(self, mode, confirm):
        if mode not in self.MODES:
            raise ValueError("Unbekannte Aktion")
        if mode in ("run", "reboot") and not confirm:
            raise ValueError("Bestätigung fehlt")
        cur = self.status()
        if cur.get("state") == "running":
            raise ValueError("Es läuft bereits eine Aktion")
        if mode in ("run", "reboot") and cur["streaming"]:
            raise ValueError("Übertragung läuft. Bitte zuerst den Stream beenden.")
        if not cur["helper_installed"]:
            raise ValueError("Update-Helfer ist nicht installiert")
        if self.demo:
            threading.Thread(target=self._fake, args=(mode,), daemon=True).start()
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mode + "\n")

    AUTO_EVERY = 6 * 3600         # so oft sucht die Box von selbst nach Systemupdates (nie während einer Übertragung)
    AUTO_RETRY = 3600             # nach einer Suche ohne Ergebnis (kein Internet) erst nach einer Stunde wieder
    AUTO_AFTER_BOOT = 2 * 60      # nach dem Start der Box zwei Minuten abwarten (Dienste und Netz kommen hoch), dann suchen
    EARLY_UPTIME = 30 * 60        # in der ersten halben Stunde nach dem Start (Router und Mobilfunk brauchen oft einige Minuten) ...
    AUTO_RETRY_EARLY = 5 * 60     # ... wird ein Fehlversuch schon nach 5 Minuten wiederholt

    @classmethod
    def auto_check_due(cls, st, now, uptime, streaming, request_pending, last_try, helper=True):
        """Ist es Zeit für die stille Suche nach Systemupdates (alle 6 Stunden)? Reine Rechnung (testbar)."""
        if not helper or streaming or request_pending or uptime < cls.AUTO_AFTER_BOOT:
            return False
        if st.get("state") in ("running", "rebooting"):
            return False
        retry = cls.AUTO_RETRY_EARLY if uptime < cls.EARLY_UPTIME else cls.AUTO_RETRY
        if last_try and now - last_try < retry:
            return False
        last = st.get("last_check") or 0
        if last < now - uptime:
            return True                # seit dem Start der Box noch nicht gesucht: immer einmal nach dem Start
        return now - last >= cls.AUTO_EVERY

    def auto_check(self, now=None, last_try=None, present=False):
        """Legt, wenn es Zeit ist, die Anforderung "autocheck" ab. True, wenn angefordert wurde. Der Helfer sucht still: ohne Zustand
        "läuft", ohne Fehlermeldung bei fehlendem Internet; das Ergebnis erscheint wie bei der Suche per Knopf (gelber Punkt in der Kopfleiste).
        present: Jemand hat die Oberfläche geöffnet (Anmeldung): dann nicht erst AUTO_AFTER_BOOT nach dem Start abwarten."""
        if self.demo:
            return False
        try:
            uptime = float((read("/proc/uptime", "") or "0").split()[0])
        except (ValueError, IndexError):
            uptime = 0.0
        if present:
            uptime = max(uptime, float(self.AUTO_AFTER_BOOT))
        st = self.status()
        if last_try is None:
            last_try = self.last_try
        if not self.auto_check_due(st, now or time.time(), uptime, st.get("streaming", False), os.path.exists(self.req), last_try,
                                   helper=st.get("helper_installed", False)):
            return False
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("autocheck\n")
        self.last_try = time.time()
        return True

    def auto_loop(self):
        time.sleep(30)
        while True:
            try:
                self.auto_check()
            except Exception as e:                # nie den Dienst beenden
                print("auto_check:", e)
            # in der ersten halben Stunde nach dem Start jede Minute nachsehen (die Suche selbst ist erst nach AUTO_AFTER_BOOT fällig)
            try:
                early = float((read("/proc/uptime", "") or "0").split()[0]) < self.EARLY_UPTIME
            except (ValueError, IndexError):
                early = False
            time.sleep(60 if early else 15 * 60)

    def _fake(self, mode):
        f = self.fake
        f.update(state="running", mode=mode, message="")
        time.sleep(1.5)
        if mode == "run":
            total = 12
            for i in range(total + 1):
                f["progress"] = {"downloading": min(i * 2, total), "unpacking": max(0, i - 4),
                                 "setting_up": max(0, i - 8), "total": total}
                f["log"] = f"Get:{i} http://repo.example ...\nUnpacking paket-{i} ...\n"
                time.sleep(0.6)
            f.update(state="done", message="Update abgeschlossen.", reboot_required=True,
                     reboot_boot_id=self.boot_id(), available=0, belabox=[], packages=[])
        elif mode == "reboot":
            f.update(state="rebooting", message="Neustart wird eingeleitet. (Demo)")
        else:
            f.update(state="done", mode=mode, available=224, download="412 MB",
                     packages=["belaui", "belacoder", "belabox-linux-rk3588", "libc6"],
                     belabox=["belaui", "belacoder", "belabox-linux-rk3588"],
                     would_reboot=True, last_check=int(time.time()), held=[])


DJI_COMMANDS = ("state", "wifi_options", "scan", "add", "update", "use_saved", "delete_saved", "remove",
                "connect", "disconnect", "reconnect")
DJI_FIELDS = ("addr", "name", "model", "kind", "wifi_ifname", "ssid", "password", "ip", "resolution", "fps", "bitrate",
              "stabilization", "autoconnect")


class DjiService:
    """Dünne Schicht zum Bluetooth-Dienst (pipbox-dji, dji_daemon.py): leitet die Befehle der Oberfläche als JSON-Zeilen an
    127.0.0.1:9101 weiter (mit dem Token, das nur der Benutzer pipbox lesen kann), trägt neue Kameras in die Kameraliste ein
    und liefert den Zustand. Passwörter der Kamera-WLANs liefert der Dienst nie, hier kommen nur Namen an."""
    MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    HOST, PORT = "127.0.0.1", 9101

    # Ausgangswerte nach Rolle (nur für neue Kameras): das Hauptbild bekommt mehr, die kleinen Bilder weniger, weil die Box
    # sie ohnehin auf 480x270 verkleinert. Spart WLAN und Rechenzeit.
    PROFILES = {"main": {"resolution": "1080p", "fps": 30, "bitrate": 8000},
                "pip": {"resolution": "720p", "fps": 30, "bitrate": 4000}}

    def __init__(self, state_dir, cams, rtmp_app, rtmp_port, demo=False):
        self.config_path = os.path.join(state_dir, "dji-cameras.json")
        self.token_path = os.path.join(state_dir, "dji-token")
        self.cams, self.app, self.port = cams, rtmp_app, rtmp_port
        self.pipeline = None      # wird von main() gesetzt: Rolle der Kamera in der Pipeline
        self.demo = demo
        self.fake = {"cameras": {}, "scan": [], "scanning": False} if demo else None
        self._next_id = 1

    def role_for_key(self, key):
        """main / pip nach der gespeicherten Pipeline, sonst nach der Rolle in der Kameraliste."""
        cfg = getattr(self.pipeline, "cfg", None) or {}
        if cfg.get("main") == key:
            return "main"
        if key in (cfg.get("pip"), cfg.get("pip2"), cfg.get("pip3")):
            return "pip"
        cam = next((c for c in self.cams.cams if c["key"] == key), None) if self.cams else None
        return {"main": "main", "pip": "pip"}.get((cam or {}).get("role"))

    def _free_role(self):
        roles = {c["role"] for c in self.cams.cams}
        return "main" if "main" not in roles else "pip" if "pip" not in roles else "extra"

    def _call(self, req, timeout=8):
        """Eine Anfrage an den Bluetooth-Dienst (eine JSON-Zeile hin, die Antwort mit passender Nummer zurück)."""
        if self.demo:
            return self._fake_call(req)
        try:
            with open(self.token_path) as f:
                tok = f.read().strip()
        except OSError:
            raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) ist noch nicht bereit")
        rid = self._next_id = self._next_id + 1
        line = (json.dumps(dict(req, token=tok, id=rid)) + "\n").encode()
        try:
            with socket.create_connection((self.HOST, self.PORT), timeout=timeout) as sk:
                sk.settimeout(timeout)
                sk.sendall(line)
                buf = b""
                while True:
                    while b"\n" in buf:
                        one, buf = buf.split(b"\n", 1)
                        try:
                            resp = json.loads(one)
                        except ValueError:
                            continue
                        if isinstance(resp, dict) and resp.get("reply_to") == rid:
                            if resp.get("error"):
                                raise ValueError(str(resp["error"]))
                            return resp
                    chunk = sk.recv(65536)
                    if not chunk:
                        raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) hat nicht geantwortet")
                    buf += chunk
                    if len(buf) > 1 << 20:
                        raise RuntimeError("Antwort des Bluetooth-Dienstes zu groß")
        except OSError:
            raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) läuft nicht")

    def _config(self):
        try:
            with open(self.config_path) as f:
                d = json.load(f)
            return (d.get("cameras") or {}) if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def fps_for_key(self, key):
        """Eingestellte Bildrate einer DJI-Kamera (Schlüssel dji-xxxxxx), sonst None. Manche DJI-Modelle melden in ihren
        Stream-Metadaten keine Bildrate; dann zeigen wir den Wert, mit dem wir sie starten."""
        if not key.startswith("dji-"):
            return None
        for cfg in self._config().values():
            if isinstance(cfg, dict) and cfg.get("rtmp_key") == key:
                return cfg.get("fps") if cfg.get("fps") in (25, 30) else 30
        return None

    def host_for_key(self, key):
        """(Adresse der Box, Verbindung) für die Adresse dieser DJI-Kamera nach der Verbindung in ihrer Karte, sonst None
        (dann gilt die Hauptverbindung)."""
        if not str(key).startswith("dji-"):
            return None
        for cfg in self._config().values():
            if isinstance(cfg, dict) and cfg.get("rtmp_key") == key:
                w = cfg.get("wifi_ifname")
                if not w:
                    return None
                if w == "manual":
                    return (cfg["ip"], "manual") if cfg.get("ip") else None
                for o in iface_ips():
                    if o["iface"] == w:
                        return (o.get("cam_ip") or o["ip"], w)
                return None
        return None

    EXTRAS_TTL = 3.0

    def camera_extras(self):
        """{Schlüssel: {battery, battery_age, charging}} der DJI-Kameras aus dem Zustand des Dienstes (3 Sekunden zwischengespeichert,
        die Statusseite fragt oft). Ohne Dienst leer."""
        now = time.monotonic()
        hit = getattr(self, "_extras", None)
        if hit and now - hit[0] < self.EXTRAS_TTL:
            return hit[1]
        out = {}
        try:
            for c in self._call({"cmd": "state"}, timeout=2).get("cameras", []):
                if c.get("rtmp_key") and c.get("battery") is not None:
                    out[c["rtmp_key"]] = {"battery": c["battery"], "battery_age": c.get("battery_age"), "charging": c.get("charging")}
        except (RuntimeError, ValueError):
            pass
        self._extras = (now, out)
        return out

    def _ensure_listed(self, cameras):
        """Jede Kamera des Dienstes steht auch in der Kameraliste der Box (damit sie als Bildquelle gewählt werden kann)."""
        if not self.cams:
            return
        have = {c["key"] for c in self.cams.cams}
        for c in cameras:
            key = c.get("rtmp_key")
            if key and key not in have:
                try:
                    self.cams.add(c.get("name") or c.get("model") or "DJI-Kamera", key, self._free_role())
                    have.add(key)
                except ValueError:
                    pass

    def status(self):
        out = {"available": False, "reason": "", "cameras": [], "scan": [], "scanning": False, "scan_error": "",
               "wifi_options": [], "adapters": [], "adapter_problems": [], "driver": {}, "bleak": True}
        try:
            out.update(self._call({"cmd": "state"}))
            out["wifi_options"] = self._call({"cmd": "wifi_options"}).get("wifi_options", [])
            ad = self._call({"cmd": "adapters"})
            out.update({k: ad[k] for k in ("adapters", "adapter_problems", "driver") if k in ad})
            out["available"] = True
            self._ensure_listed(out["cameras"])
        except RuntimeError as e:
            out["reason"] = str(e)
        except ValueError as e:
            out["reason"] = str(e)
        return out

    def command(self, d):
        """Ein Befehl der Oberfläche an den Dienst. Nur die bekannten Befehle und Felder gehen durch."""
        cmd = d.get("cmd")
        if cmd not in DJI_COMMANDS:
            raise ValueError("Unbekannter Befehl")
        req = {"cmd": cmd}
        for k in DJI_FIELDS:
            if k in d:
                req[k] = d[k]
        if cmd == "use_saved" or cmd == "delete_saved":
            req["ssid"] = d.get("ssid", "")
        if "addr" in req and not self.MAC_RE.match(str(req["addr"])):
            raise ValueError("Ungültige Geräteadresse")
        if cmd == "add":
            key = "dji-" + str(req.get("addr", "")).replace(":", "").lower()[-6:]
            role = self.role_for_key(key) or self._free_role()
            req["settings"] = dict(self.PROFILES.get(role, {}))      # erste Einstellung: Ausgangswerte nach Rolle
        if cmd == "update" and "name" in req and self.cams:
            # Der Name gilt auch in der Kameraliste der Box: dort umbenennen (ein doppelter Name wird hier abgelehnt)
            cfg = self._config().get(str(req.get("addr", "")).upper()) or {}
            cam = next((c for c in self.cams.cams if c["key"] == cfg.get("rtmp_key")), None)
            if cam and str(req["name"]).strip() and cam["name"] != str(req["name"]).strip()[:40]:
                self.cams.update(cam["id"], name=str(req["name"]))
        res = self._call(req)
        if cmd == "add" and res.get("key") and self.cams:
            self._ensure_listed([{"rtmp_key": res["key"], "model": req.get("model"), "name": req.get("name")}])
        return {k: v for k, v in res.items() if k not in ("reply_to", "token")}

    # -- Vorschau (--demo): ein nachgestellter Dienst im Speicher, damit die Oberfläche ohne Bluetooth zu sehen ist
    def _fake_call(self, req):
        f, cmd = self.fake, req.get("cmd")
        if not f["cameras"] and not f.get("seeded"):
            f["seeded"] = True
            base = {"wifi_ifname": "eth1", "ssid": "", "ip": "192.168.80.1", "resolution": "1080p", "fps": 30, "bitrate": 8000,
                    "stabilization": "off", "autoconnect": True, "saved": ["KameraNetz"], "in_range": True, "retry_in": 0,
                    "publishing": False, "locked": False, "detail": "", "battery": None}
            f["cameras"]["D0:D0:4B:00:00:01"] = dict(base, addr="D0:D0:4B:00:00:01", name="Kamera vorn", model="Osmo Action 5 Pro",
                                                     kind="action5", rtmp_key="dji-000001", state="streaming", battery=82, charging=True, battery_age=3,
                                                     publishing=True, locked=True, detail="rtmp://192.168.80.1:1935/publish/dji-000001")
            f["cameras"]["F0:4F:E2:00:00:02"] = dict(base, addr="F0:4F:E2:00:00:02", name="Kamera hinten", model="Osmo Pocket 3",
                                                     kind="pocket3", rtmp_key="dji-000002", state="error", autoconnect=False,
                                                     resolution="720p", bitrate=4000, retry_in=0, battery=18, battery_age=95, charging=False,
                                                     detail="Kamera nicht gefunden. Ist sie an, Bluetooth aktiv und nicht mit dem Handy verbunden?")
        if cmd == "state":
            return {"cameras": list(f["cameras"].values()), "scan": f["scan"], "scanning": f["scanning"], "scan_error": "",
                    "bleak": True}
        if cmd == "wifi_options":
            return {"wifi_options": [
                {"ifname": "wlan0", "ssid": "Handy-Hotspot", "ip": "10.1.1.20", "type": "client", "secret_missing": False},
                {"ifname": "eth0", "ssid": "", "ip": "192.168.1.20", "type": "other", "secret_missing": False},
                {"ifname": "eth1", "ssid": "", "ip": "192.168.80.1", "type": "other", "secret_missing": False}]}
        if cmd == "adapters":
            return {"adapters": [{"usb_id": "0b05:190e", "name": "ASUS USB-BT500", "vendor": "Realtek", "address": "A0:AD:9F:00:00:00", "powered": True}],
                    "adapter_problems": [], "driver": {}}
        if cmd == "scan":
            f["scan"] = [{"addr": "AA:BB:CC:00:00:03", "name": "OsmoAction6", "model": "Osmo Action 6", "kind": "action6",
                          "rssi": -61, "paired": False}]
            return {"ok": True}
        addr = str(req.get("addr", "")).upper()
        if cmd == "add":
            f["cameras"][addr] = {"addr": addr, "name": req.get("name") or req.get("model") or addr, "model": req.get("model", ""),
                                  "kind": req.get("kind", ""), "wifi_ifname": "", "ssid": "", "ip": "", "saved": [],
                                  "rtmp_key": "dji-" + addr.replace(":", "").lower()[-6:], "autoconnect": False,
                                  "state": "idle", "detail": "", "battery": None, "in_range": True, "retry_in": 0,
                                  "publishing": False, "locked": False, **{**{"resolution": "1080p", "fps": 30, "bitrate": 6000,
                                                                           "stabilization": "off"}, **(req.get("settings") or {})}}
            return {"ok": True, "key": f["cameras"][addr]["rtmp_key"]}
        cam = f["cameras"].get(addr)
        if cam is None:
            raise ValueError("Unbekannte Kamera")
        if cmd == "update":
            for k in DJI_FIELDS:
                if k in req and k != "addr" and not (k == "password" and not req[k]):
                    cam[k] = req[k]
        elif cmd == "remove":
            del f["cameras"][addr]
        elif cmd in ("connect", "reconnect"):
            cam.update(state="streaming", detail="Die Kamera streamt (Vorschau)", publishing=True, locked=True, battery=64)
        elif cmd == "disconnect":
            cam.update(state="idle", detail="", publishing=False, locked=False, battery=None)
        elif cmd == "delete_saved":
            cam["saved"] = [n for n in cam.get("saved", []) if n != req.get("ssid")]
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    sampler = None
    cams = None
    auth = None
    updates = None
    djisvc = None
    netchoice = None
    names = None
    srtla = None
    pipeline = None
    send = None

    def log_message(self, *a):
        pass

    def host(self):
        return (self.headers.get("Host") or "box").split(":")[0]

    def ip(self):
        """Adresse des Gegenübers. Kommt die Anfrage vom Tailscale-Proxy auf dieser Box (Serve/Funnel), zählt die Adresse, die der
        Proxy als letzte in X-Forwarded-For angehängt hat: So sperren fehlgeschlagene Anmeldungen einzelne Absender und nicht alle."""
        peer = self.client_address[0]
        if peer in ("127.0.0.1", "::1"):
            last = (self.headers.get("X-Forwarded-For", "") or "").split(",")[-1].strip()
            try:
                ipaddress.ip_address(last)
                return last
            except ValueError:
                pass
        return peer

    def token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "pb_session":
                return v
        return ""

    def authed(self):
        return self.auth.valid(self.token())

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            raise ValueError("ungültige Länge")
        return json.loads(self.rfile.read(min(n, 4096)) or b"{}")

    def send_bytes(self, code, body, ctype, cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def reply(self, code, obj, cookie=None):
        self.send_bytes(code, json.dumps(obj).encode(), "application/json", cookie)

    def cookie(self, tok, max_age):
        flags = "HttpOnly; SameSite=Strict; Path=/"
        if self.headers.get("X-Forwarded-Proto") == "https":
            flags += "; Secure"
        return f"pb_session={tok}; Max-Age={max_age}; {flags}"

    def page(self, name):
        body = read(os.path.join(WEB_DIR, name), "")
        self.send_bytes(200, body.encode(), "text/html; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.page("index.html" if self.authed() else "login.html")
        if path == "/api/auth":
            out = {"configured": self.auth.configured, "mode": self.auth.mode, "authed": self.authed()}
            if self.auth.mode == "demo":
                out["demo_password"] = Auth.DEMO_PASSWORD     # nur in der Vorschau: die Anmeldeseite füllt es vor
            return self.reply(200, out)
        if not self.authed():
            return self.reply(401, {"error": "nicht angemeldet"})
        if path == "/api/metrics":
            m = self.sampler.sample()
            m["cameras"] = self.cams.listing(self.host(), self.djisvc.host_for_key)
            extras = self.djisvc.camera_extras()
            for c in m["cameras"]:
                c.update(extras.get(c["key"], {}))             # Akkustand der DJI-Kameras (Status, Kameras)
            m["uplinks"] = uplink_states(((self.srtla.data or {}).get("settings") or {}).get("uplinks") or [])
            pic = self.send.picture()
            for c in m["cameras"]:
                p = pic.get(c["key"]) if pic else None
                c["pic"], c["pic_wait"] = (p[0], p[1]) if p else (None, 0)
            for c in m["cameras"]:
                if c.get("state") == "live" and not c.get("fps"):
                    f = self.djisvc.fps_for_key(c["key"])
                    if f:
                        c["fps"], c["fps_set"] = float(f), True      # eingestellt, nicht gemessen
            return self.reply(200, m)
        if path == "/api/logmode":
            return self.reply(200, self.logmode.status())
        if path == "/api/power":
            return self.reply(200, self.power.status())
        if path == "/api/wifi":
            return self.reply(200, self.wifi.status())
        if path == "/api/remote":
            return self.reply(200, self.remote.status())
        if path == "/api/swupdate":
            return self.reply(200, self.swupdate.status(force="check=1" in (self.path.split("?", 1) + [""])[1]))
        if path == "/api/update":
            try:
                self.updates.auto_check(present=True)       # Wer die Seite öffnet, soll gleich wissen, ob es Updates gibt (einmal je Start, still)
            except Exception as e:
                print("auto_check (Anmeldung):", e)
            return self.reply(200, self.updates.status())
        if path == "/api/dji":
            st = label_bluetooth(self.djisvc.status(), self.names)
            return self.reply(200, st)
        if path == "/api/network":
            return self.reply(200, self.netchoice.status())
        if path == "/api/srtla":
            return self.reply(200, {**self.srtla.public(), "interfaces": iface_ips()})
        if path == "/api/pipeline":
            st = self.pipeline.status(self.cams.listing(""))
            st["live"] = {"main": self.send.delay_live(), "pips": self.send.delay_live_pips()}
            return self.reply(200, st)
        if path == "/api/send":
            out = self.send.status()
            out["autostart"] = self.autostart.status()
            return self.reply(200, out)
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            d = self.read_json()
            if path == "/api/setup":
                self.auth.set_password(d.get("code"), d.get("password"), self.ip())
                rem = d.get("remember") is True
                tok = self.auth.login(d.get("password"), self.ip(), rem)
                return self.reply(200, {"ok": True}, self.cookie(tok, REMEMBER_SECONDS if rem else SESSION_SECONDS))
            if path == "/api/login":
                rem = d.get("remember") is True
                tok = self.auth.login(d.get("password"), self.ip(), rem)
                return self.reply(200, {"ok": True}, self.cookie(tok, REMEMBER_SECONDS if rem else SESSION_SECONDS))
            if path == "/api/logout":
                self.auth.logout(self.token())
                return self.reply(200, {"ok": True}, self.cookie("", 0))
            if not self.authed():
                return self.reply(401, {"error": "nicht angemeldet"})
            if path == "/api/network":
                self.netchoice.select(d.get("iface"))
                return self.reply(200, {"ok": True})
            if path == "/api/send":
                self.send.request(d.get("action"), d.get("confirm") is True)
                if d.get("action") in ("stop", "start"):
                    self.autostart.cancel("Sendung wurde von Hand gestartet oder beendet")
                return self.reply(200, {"ok": True})
            if path == "/api/autostart":
                self.autostart.set_enabled(d.get("enabled"))
                return self.reply(200, self.autostart.status())
            if path == "/api/power":
                self.power.request(d.get("action"), d.get("confirm") is True)
                return self.reply(200, {"ok": True})
            if path == "/api/logmode":
                self.logmode.request(d.get("mode"))
                return self.reply(200, {"ok": True})
            if path == "/api/wifi":
                self.wifi.request(d)
                return self.reply(200, {"ok": True})
            if path == "/api/devname":
                if self.names is None:
                    raise ValueError("Namen sind hier nicht verfügbar")
                self.names.set(d.get("key"), d.get("name", ""))
                return self.reply(200, {"ok": True})
            if path == "/api/remote":
                self.remote.request(d.get("action"), d.get("confirm") is True, d.get("public") is True)
                return self.reply(200, {"ok": True})
            if path == "/api/swupdate":
                self.swupdate.request(d.get("action"), d.get("confirm") is True, d.get("version"), d.get("older") is True)
                return self.reply(200, {"ok": True})
            if path == "/api/pipeline/swap":
                with_key = d.get("with")
                if with_key is not None and (not isinstance(with_key, str) or not KEY_RE.match(with_key)):
                    raise ValueError("Kamera unbekannt")
                self.pipeline.swap_main_pip(with_key)
                if self.send.swap_live():
                    return self.reply(200, {"ok": True, "restarted": False, "note": "Getauscht, ohne Unterbrechung."})
                restarted, note = self.send.restart_if_live()
                return self.reply(200, {"ok": True, "restarted": restarted, "note": note or "Getauscht."})
            if path == "/api/pipeline":
                before = dict(self.pipeline.cfg)
                before["styles"] = clean_styles(before.get("styles"))        # eine unveränderte Einstellung ohne Stile gilt nicht als Änderung
                self.pipeline.set(d, [c["key"] for c in self.cams.cams])
                restarted, note = (False, "")
                if self.pipeline.cfg != before:
                    only_delay = {k: v for k, v in before.items() if k not in DELAY_KEYS} == \
                                 {k: v for k, v in self.pipeline.cfg.items() if k not in DELAY_KEYS}
                    pips_changed = any(before.get(k) != self.pipeline.cfg.get(k) for k in DELAY_KEYS[1:])
                    live_ok = self.send.delay_live_pips() if pips_changed else self.send.delay_live()
                    if only_delay and live_ok:
                        note = "Gespeichert. Die Verzögerung wird live übernommen, ohne Neustart."
                    else:
                        restarted, note = self.send.restart_if_live()
                return self.reply(200, {"ok": True, "restarted": restarted, "note": note})
            if path == "/api/srtla":
                return self.reply(200, {"id": self.srtla.add(d)})
            if path == "/api/srtla/select":
                self.srtla.select(d.get("id"))
                return self.reply(200, {"ok": True})
            if path == "/api/srtla/settings":
                self.srtla.set_settings(d, [o["iface"] for o in iface_ips()] or ["eth0", "eth1"])
                return self.reply(200, {"ok": True})
            m2 = re.match(r"^/api/srtla/([0-9a-f]{8})$", path)
            if m2:
                try:
                    self.srtla.update(m2.group(1), d)
                    return self.reply(200, {"ok": True})
                except KeyError:
                    return self.reply(404, {"error": "nicht gefunden"})
            if path == "/api/dji/cmd":
                return self.reply(200, self.djisvc.command(d))
            if path == "/api/update":
                self.updates.request(d.get("mode"), d.get("confirm") is True)
                return self.reply(200, {"ok": True})
            m = re.match(r"^/api/cameras/([0-9a-f]{8})$", path)
            if m:
                try:
                    if d.get("iface") is not None:
                        cam = next((c for c in self.cams.cams if c["id"] == m.group(1)), None)
                        if cam and cam["key"].startswith("dji-"):
                            raise ValueError("Die Verbindung einer DJI-Kamera wird in ihrer DJI-Karte gewählt")
                    return self.reply(200, self.cams.update(m.group(1), d.get("name"), d.get("role"), d.get("iface")))
                except KeyError:
                    return self.reply(404, {"error": "nicht gefunden"})
            if path == "/api/cameras":
                cam = self.cams.add(d.get("name"), d.get("key"), d.get("role", "extra"))
                return self.reply(200, cam)
        except PermissionError as e:
            return self.reply(429, {"error": str(e)})
        except RuntimeError as e:
            return self.reply(503, {"error": str(e)})
        except (ValueError, TypeError) as e:
            return self.reply(400, {"error": str(e)})
        self.reply(404, {"error": "not found"})

    def do_DELETE(self):
        if not self.authed():
            return self.reply(401, {"error": "nicht angemeldet"})
        m3 = re.match(r"^/api/srtla/([0-9a-f]{8})$", self.path.split("?")[0])
        if m3:
            try:
                self.srtla.remove(m3.group(1))
                return self.reply(200, {"ok": True})
            except KeyError:
                return self.reply(404, {"error": "nicht gefunden"})
        m = re.match(r"^/api/cameras/([0-9a-f]{8})$", self.path.split("?")[0])
        if not m or not self.cams.remove(m.group(1)):
            return self.reply(404, {"error": "nicht gefunden"})
        self.reply(200, {"ok": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--demo", action="store_true", help="Beispielwerte statt /proc")
    ap.add_argument("--state", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "state"),
                    help="Ordner für Passwort-Hash und Kameraliste")
    ap.add_argument("--bela-config", default="", help="belaUI config.json: BELABOX-Passwort mitbenutzen")
    ap.add_argument("--rtmp-port", type=int, default=1935, help="RTMP-Port, den Kameras nutzen")
    ap.add_argument("--rtmp-app", default="publish", help="nginx-rtmp-Applikation")
    ap.add_argument("--rtmp-stat-url", default="", help="nginx-rtmp-Statistik (XML), z. B. http://127.0.0.1/stat")
    args = ap.parse_args()
    Handler.updates = Updates(args.state, args.demo)
    if not args.demo:
        threading.Thread(target=Handler.updates.auto_loop, daemon=True).start()
    Handler.djisvc = None  # unten gesetzt, sobald Kameraliste existiert
    Handler.auth = Auth(args.state, args.bela_config or None,
                        demo=args.demo and args.host in ("127.0.0.1", "::1", "localhost"))   # Demo-Passwort nur auf dem eigenen Rechner
    Handler.cams = CameraStore(os.path.join(args.state, "cameras.json"), args.rtmp_app, args.rtmp_stat_url, args.demo)
    Handler.sampler = Sampler(args.demo)
    Handler.sampler.sample()  # Startwerte für Ratenberechnung
    Handler.srtla = SrtlaStore(os.path.join(args.state, "srtla.json"))
    Handler.pipeline = PipelineStore(os.path.join(args.state, "pipeline.json"))
    Handler.send = SendControl(args.state, Handler.srtla, Handler.pipeline, Handler.cams, args.demo)
    Handler.swupdate = SwUpdate(args.state, args.demo, Handler.send)
    if not args.demo:
        threading.Thread(target=Handler.swupdate.auto_loop, daemon=True).start()
    Handler.remote = Remote(args.state, args.demo)
    Handler.netchoice = NetChoice(os.path.join(args.state, "camera-net.json"))
    Handler.names = DeviceNames(args.state)
    Handler.wifi = Wifi(args.state, args.demo, Handler.netchoice, Handler.names)
    Handler.power = Power(args.state, args.demo, Handler.send)
    Handler.logmode = LogMode(args.state, args.demo)
    Handler.autostart = AutoStart(args.state, Handler.send, args.demo)
    if not args.demo:
        threading.Thread(target=Handler.autostart.run, daemon=True).start()
    Handler.cams.ipfn = Handler.netchoice.ip
    Handler.djisvc = DjiService(args.state, Handler.cams, args.rtmp_app, args.rtmp_port, args.demo)
    Handler.djisvc.pipeline = Handler.pipeline

    def watcher():
        while True:
            try:
                Handler.cams.auto_add()
            except Exception as e:  # nie den Dienst beenden
                print("auto_add:", e)
            time.sleep(3)
    threading.Thread(target=watcher, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"PIPBOX auf http://{args.host}:{args.port} (demo={args.demo})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
