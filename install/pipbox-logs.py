#!/usr/bin/env python3
"""Root-Helfer: Protokolle der Box einsammeln, von persönlichen Angaben bereinigen und zum Herunterladen bereitstellen (läuft über pipbox-logs.path).

Die Oberfläche legt eine Auslösedatei mit dem Stichwort "collect" ab; der Helfer liest sie ohne Verweisen zu folgen, löscht sie, sammelt die Journale der
IRL4YOU-Dienste, das Zustandsprotokoll, den Zustand der Dienste und eine Kurzfassung der Einstellungen und schreibt EINE Textdatei nach
/run/pipbox-logs/bundle.txt (lesbar für den Dienst, der sie ausliefert). Vorher werden Geheimnisse und persönliche Angaben ersetzt: Passwörter, Stream-ID,
Serveradressen, WLAN-Namen, IP- und MAC-Adressen, Kamera-Schlüssel, Tailscale-Namen, E-Mail-Adressen, Zugangsschlüssel.
"""
import json
import os
import re
import stat
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/logs-request"
RUN = "/run/pipbox-logs"
OUT = f"{RUN}/bundle.txt"
STATUS = f"{RUN}/status.json"
MAX_TOTAL = 2_000_000          # höchste Größe der Datei in Bytes (ältestes fällt zuerst weg)
MIN_SECRET = 4                 # kürzere Werte werden nicht als Geheimnis ersetzt (sonst würde zu viel ersetzt)


def read_req(path, limit=64):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def load_json(name, limit=300_000):
    """Einstellungsdatei aus dem Ordner des Benutzers pipbox lesen (ohne Verweisen zu folgen, nur normale Dateien, begrenzte Größe). Fehler: None."""
    try:
        fd = os.open(f"{STATE}/{name}", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return json.loads(os.read(fd, limit).decode("utf-8", "replace"))
    except ValueError:
        return None
    finally:
        os.close(fd)


def write_status(**kw):
    os.makedirs(RUN, exist_ok=True)
    os.chmod(RUN, 0o755)
    kw["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


# ---------------------------------------------------------------- Bereinigen

SECRET_KEYS = r"(?:pass(?:word|wd)?|psk|secret|token|stream[_-]?id|api[_-]?key|authorization|cookie|private[_-]?key)"
RE_KV = re.compile(r"(?i)\b(" + SECRET_KEYS + r")\b([\"']?\s*[=:]\s*)(?:(?:Bearer|Basic)\s+)?(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)")
RE_MAC = re.compile(r"\b([0-9A-Fa-f]{2})\\?[:-]([0-9A-Fa-f]{2})\\?[:-]([0-9A-Fa-f]{2})(?:\\?[:-][0-9A-Fa-f]{2}){3}\b")      # auch mit "\:" (nmcli -t maskiert den Doppelpunkt)
RE_DEV = re.compile(r"\bdev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}\b")
RE_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
RE_IPV6 = re.compile(r"(?i)(?<![0-9a-z:.])(?:(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}"
                     r"|(?:[0-9a-f]{1,4}:){1,6}(?::[0-9a-f]{1,4}){1,6}"
                     r"|(?:[0-9a-f]{1,4}:){1,7}:|::(?:[0-9a-f]{1,4}:){0,5}[0-9a-f]{1,4}"
                     r"|(?:[0-9a-f]{1,4}:){6}[0-9a-f]{1,4})(?![0-9a-z:])")
RE_DJI_KEY = re.compile(r"\bdji-[0-9a-f]{4,12}\b")
RE_EMAIL = re.compile(r"[\w.+-]{1,64}@[A-Za-z][\w-]{0,62}(?:\.[\w-]{1,63}){0,4}\.[A-Za-z]{2,24}\b")          # begrenzt: lange Zeichenfolgen ohne @ dürfen nicht quadratisch lange dauern
RE_TAILNET = re.compile(r"(?i)\b[\w-]{1,63}(?:\.[\w-]{1,63}){0,4}\.ts\.net\b")
RE_URL_TS = re.compile(r"https://(?:login|console)\.tailscale\.com/\S+")
KEEP_IPS = ("127.", "0.0.0.0", "255.")
KEEP_MACS = ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")


class Scrubber:
    """Ersetzt persönliche Angaben und Geheimnisse. Gleiche Werte bekommen gleiche Platzhalter (so bleibt erkennbar, was zusammengehört)."""

    def __init__(self):
        self.literals = {}          # Wert -> Platzhalter
        self.maps = {}              # Art -> {Wert: Nummer}

    def secret(self, value, label):
        v = str(value or "").strip()
        if len(v) >= MIN_SECRET and v not in self.literals:
            n = sum(1 for p in self.literals.values() if p.startswith("<" + label))
            self.literals[v] = "<%s-%d>" % (label, n + 1)

    def secret_key(self, value):
        """Kamera-Schlüssel (Namen der Videoeingänge): gleiche Nummern wie die Schlüssel, die der Text selbst verrät."""
        v = str(value or "").strip()
        if len(v) >= MIN_SECRET and v not in self.literals:
            self.literals[v] = "<Schlüssel-%d>" % self._num("key", v)

    def _num(self, kind, key):
        m = self.maps.setdefault(kind, {})
        return m.setdefault(key, len(m) + 1)

    def scrub(self, text):
        for v in sorted(self.literals, key=len, reverse=True):          # lange Werte zuerst (ein WLAN-Name kann in einem anderen stecken)
            text = text.replace(v, self.literals[v])
        text = RE_KV.sub(lambda m: m.group(1) + m.group(2) + "<entfernt>", text)
        if "tailscale.com" in text:
            text = RE_URL_TS.sub("<Tailscale-Adresse>", text)
        if ".ts.net" in text:
            text = RE_TAILNET.sub("<Tailscale-Name>", text)
        if "@" in text:
            text = RE_EMAIL.sub("<E-Mail>", text)
        text = RE_DEV.sub(lambda m: "dev_<MAC-%d>" % self._num("mac", m.group(0)[4:].replace("_", ":").upper()), text)
        text = RE_MAC.sub(lambda m: m.group(0) if m.group(0).replace("\\", "").replace("-", ":").lower() in KEEP_MACS else
                          "<MAC-%d %s:%s:%s>" % (self._num("mac", m.group(0).replace("\\", "").replace("-", ":").upper()), m.group(1).upper(), m.group(2).upper(), m.group(3).upper()), text)
        text = RE_DJI_KEY.sub(lambda m: "<Schlüssel-%d>" % self._num("key", m.group(0)), text)
        text = RE_IPV4.sub(lambda m: m.group(0) if m.group(0).startswith(KEEP_IPS) else "<IP-%d>" % self._num("ip", m.group(0)), text)
        text = RE_IPV6.sub(lambda m: m.group(0) if m.group(0) == "::1" else "<IPv6-%d>" % self._num("ip6", m.group(0).lower()), text)
        return text


def collect_secrets(sc):
    """Geheimnisse und persönliche Namen aus den Einstellungen und dem Netzwerkprogramm, damit sie überall im Text ersetzt werden."""
    load = load_json

    d = load("srtla.json")
    if isinstance(d, dict):
        for s in (d.get("servers") or []):
            if isinstance(s, dict):
                sc.secret(s.get("host"), "SERVER")
                sc.secret(s.get("streamid"), "STREAM-ID")
    d = load("dji-cameras.json")
    if isinstance(d, dict):
        for cfg in (d.get("cameras") or {}).values():
            if isinstance(cfg, dict):
                sc.secret(cfg.get("ssid"), "WLAN")
                sc.secret(cfg.get("password"), "PASSWORT")
                sc.secret_key(cfg.get("rtmp_key"))
                for n in cfg.get("saved") or []:
                    if isinstance(n, dict):
                        sc.secret(n.get("ssid"), "WLAN")
                        sc.secret(n.get("password"), "PASSWORT")
        for n in (d.get("by_connection") or {}).values():
            if isinstance(n, dict):
                sc.secret(n.get("ssid"), "WLAN")
                sc.secret(n.get("password"), "PASSWORT")
    d = load("hotspot.json")                                    # Name und Passwort des eigenen Hotspots der Box
    for h in (d.values() if isinstance(d, dict) else []):
        if isinstance(h, dict):
            sc.secret(h.get("ssid"), "HOTSPOT")
            sc.secret(h.get("password"), "PASSWORT")
    d = load("cameras.json")
    for c in (d if isinstance(d, list) else []):
        if isinstance(c, dict):
            sc.secret_key(c.get("key"))
    try:
        fd = os.open(f"{STATE}/dji-token", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            sc.secret(os.read(fd, 200).decode("utf-8", "replace").strip(), "TOKEN")
        finally:
            os.close(fd)
    except OSError:
        pass
    out = run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"], 8)           # gespeicherte WLAN-Namen
    for line in out.splitlines():
        p = line.split(":")
        if len(p) >= 2 and p[-1] == "802-11-wireless" and not p[0].startswith("pipbox-hotspot-"):
            sc.secret(":".join(p[:-1]), "WLAN")
    out = run(["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", "--rescan", "no"], 8)      # Netze der Umgebung (stehen oft im Journal)
    for line in out.splitlines():
        sc.secret(line.strip().replace("\\:", ":"), "WLAN")


# ---------------------------------------------------------------- Sammeln

def run(cmd, timeout=15, limit=400_000):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr and not r.stdout else "")
    except FileNotFoundError:
        return "(Programm nicht vorhanden: %s)\n" % cmd[0]
    except subprocess.TimeoutExpired:
        return "(Zeitüberschreitung: %s)\n" % " ".join(cmd[:3])
    except OSError as e:
        return "(Fehler: %s)\n" % type(e).__name__
    return out[-limit:]


def read_small(path, limit=200):
    """Kleine Datei lesen, auch aus /proc (dort geht kein seek)."""
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace").strip()
    except OSError:
        return "(nicht lesbar)"


def tail_file(path, lines=200, limit=300_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return "(nicht vorhanden)\n"
    except OSError as e:
        return "(nicht lesbar: %s)\n" % type(e).__name__
    return "\n".join(data.splitlines()[-lines:]) + "\n"


def journal(unit, lines):
    return run(["journalctl", "-u", unit, "--no-pager", "-o", "short-iso", "-n", str(lines)], 20)


def collapse_repeats(text):
    """Folgen gleicher Meldungen (nur die Uhrzeit vorne unterscheidet sich) auf eine Zeile mit Zähler zusammenfassen."""
    out, prev, count, last_t = [], None, 0, ""

    def flush():
        if count > 1:
            out.append("    (… %d weitere gleiche Zeilen%s)" % (count - 1, ", die letzte um " + last_t if re.match(r"\d{4}-\d\d-\d\dT", last_t) else ""))
    for line in text.splitlines():
        t, _, rest = line.partition(" ")
        if prev is not None and rest and rest == prev:
            count += 1
            last_t = t
            continue
        flush()
        out.append(line)
        prev, count, last_t = rest, 1, t
    flush()
    return "\n".join(out) + "\n"


def drop_noise(text, keep=15):
    """Die Statusnachrichten der Kamera (alle paar Sekunden) fallen bis auf die letzten weg: Sie füllen das Journal und helfen bei Fehlern selten."""
    out, noise = [], []
    for line in text.splitlines():
        (noise if "Statusnachricht" in line else out).append(line)
    if noise:
        out.append("(Von %d Zeilen \"Statusnachricht\" der Kameras sind nur die letzten %d enthalten:)" % (len(noise), min(keep, len(noise))))
        out.extend(noise[-keep:])
    return "\n".join(out) + "\n"


def wifi_cards():
    """Zustand der WLAN-Karten für die Fehlersuche (Issue #8, Netzsuche): Karten, Fähigkeiten, Funk, Treiber, gefundene Funkstationen. Namen und MAC-Adressen
    werden danach bereinigt."""
    out = ["Karten:\n" + run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"], 8)]
    for line in run(["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"], 8).splitlines():
        dev, _, typ = line.partition(":")
        if typ.strip() == "wifi" and re.fullmatch(r"[A-Za-z0-9._-]{1,15}", dev):
            info = [l for l in run(["nmcli", "-t", "-f", "GENERAL,CAPABILITIES,INTERFACE-FLAGS,WIFI-PROPERTIES", "dev", "show", dev], 8).splitlines()
                    if l.split(":", 1)[0] not in ("GENERAL.DBUS-PATH", "GENERAL.UDI", "GENERAL.CON-UUID", "GENERAL.CON-PATH", "GENERAL.PHYS-PORT-ID")]
            drv = ""
            try:
                drv = os.path.basename(os.path.realpath(f"/sys/class/net/{dev}/device/driver"))
            except OSError:
                pass
            out.append("%s (Treiber %s):\n%s" % (dev, drv or "unbekannt", "\n".join(info)))
    out.append("Funk:\n" + run(["nmcli", "-t", "radio", "all"], 8) + run(["rfkill", "list"], 8))
    out.append("NetworkManager-Einstellungen zum WLAN:\n" + "\n".join(dict.fromkeys(
        l.strip() for l in run(["NetworkManager", "--print-config"], 10).splitlines() if re.match(r"\s*wifi\.", l))))
    out.append("Gefundene Funkstationen (ohne neuen Suchlauf):\n" +
               run(["nmcli", "-f", "IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE", "dev", "wifi", "list", "--rescan", "no"], 10))
    return "\n".join(out)


def settings_summary():
    """Kurzfassung der Einstellungen ohne Zugangsdaten (keine Passwörter, Stream-ID, Server, WLAN, Schlüssel)."""
    lines = []
    d = load_json("pipeline.json")
    if isinstance(d, dict):
        lines.append("Bildaufbau: " + json.dumps({k: v for k, v in d.items() if k != "styles"}, ensure_ascii=False, sort_keys=True))
        lines.append("Aussehen der kleinen Bilder: " + json.dumps(d.get("styles", {}), ensure_ascii=False, sort_keys=True))
    else:
        lines.append("Bildaufbau: (nicht lesbar)")
    d = load_json("srtla.json")
    if isinstance(d, dict):
        lines.append("Senden: " + json.dumps(d.get("settings", {}), ensure_ascii=False, sort_keys=True) + " | Server gespeichert: %d" % len(d.get("servers") or []))
    else:
        lines.append("Senden: (nicht lesbar)")
    d = load_json("dji-cameras.json")
    if isinstance(d, dict):
        for addr, c in (d.get("cameras") or {}).items():
            if isinstance(c, dict):
                keep = {k: c[k] for k in ("name", "model", "kind", "wifi_ifname", "resolution", "fps", "bitrate", "stabilization", "autoconnect") if k in c}
                lines.append("DJI-Kamera %s: %s | gespeicherte WLANs: %d" % (addr, json.dumps(keep, ensure_ascii=False, sort_keys=True), len(c.get("saved") or [])))
    else:
        lines.append("DJI-Kameras: (nicht lesbar)")
    d = load_json("cameras.json")
    if isinstance(d, list):
        lines.append("Kameras (RTMP): " + json.dumps([{k: c.get(k) for k in ("id", "name", "role")} for c in d if isinstance(c, dict)], ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + "\n"


def usb_devices():
    """USB-Geräte aus /sys (lsusb gibt es auf der Box nicht): Anschluss, Hersteller-/Produktnummer, Name."""
    base = "/sys/bus/usb/devices"
    rows = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return "(nicht lesbar)\n"
    for n in names:
        def rd(f):
            try:
                with open(f"{base}/{n}/{f}") as fh:
                    return fh.read(80).strip()
            except OSError:
                return ""
        vid = rd("idVendor")
        if vid and not n.startswith("usb"):
            rows.append("%-8s %s:%s %s | %s" % (n, vid, rd("idProduct"), rd("manufacturer"), rd("product")))
    return "\n".join(rows) + "\n"


def sections():
    now = time.time()
    local = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    version = read_small("/opt/pipbox/VERSION")
    head = ("IRL4YOU BOX Protokolle\n"
            "Erstellt: %s UTC (%s Ortszeit der Box). Alle Zeiten in den Protokollen sind UTC; in Deutschland ist es im Sommer UTC+2.\n"
            "Version: %s\n"
            "Bereinigt: Passwörter, Stream-ID, Serveradressen, WLAN-Namen, IP- und MAC-Adressen, Kamera-Schlüssel, Tailscale-Namen, E-Mail-Adressen und "
            "Zugangsschlüssel sind durch Platzhalter ersetzt (gleiche Werte haben gleiche Nummern). Bitte trotzdem vor dem Weitergeben kurz durchsehen.\n"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)), local, version))
    units = ["pipbox", "pipbox-dji", "pipbox-send", "pipbox-health", "bluetooth", "NetworkManager", "tailscaled", "nginx"]
    helpers = ["pipbox-swupdate", "pipbox-update", "pipbox-wifi", "pipbox-btdriver", "pipbox-remote", "pipbox-power", "pipbox-logmode"]
    out = [("Kopf", head),
           ("System", "Kernel: %s\nBetriebszeit: %s\nLast: %s\n%s" % (run(["uname", "-r"], 5).strip(), run(["uptime", "-p"], 5).strip(),
                                                                    read_small("/proc/loadavg"), run(["free", "-m"], 5))),
           ("Dienste", "".join("%-22s %s\n" % (u, run(["systemctl", "is-active", u + ".service"], 5).strip()) for u in units)
            + "Einmalige Helfer (\"inactive\" ist normal, wenn sie nicht gerade arbeiten):\n"
            + "".join("%-22s %s\n" % (u, run(["systemctl", "is-active", u + ".service"], 5).strip()) for u in helpers)
            + "Fehlgeschlagene Dienste:\n" + (run(["systemctl", "--failed", "--no-legend", "--no-pager"], 10).strip() or "keine") + "\n"),
           ("Einstellungen (Kurzfassung)", settings_summary()),
           ("Netzwerkkarten", run(["ip", "-br", "addr"], 8) + run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"], 8)),
           ("USB-Geräte", usb_devices()),
           ("WLAN-Karten (Zustand und gefundene Funkstationen)", wifi_cards()),
           ("Bluetooth", run(["hciconfig"], 8)),
           ("Zustandsprotokoll (alle 10 s, letzte Stunde)", tail_file("/var/log/pipbox-health.log", 360) if os.path.exists("/var/log/pipbox-health.log")
            else tail_file("/run/pipbox-health.log", 360)),
           ("Zustand der Sendekette (nur während des Sendens vorhanden)", tail_file("/run/pipbox-send/status.json", 80)),
           ("Sendewege (srtla_send)", tail_file("/run/pipbox-send/srtla-links.txt", 40)),
           ("Regler (belacoder, letzte Zeilen)", tail_file("/run/pipbox-send/belacoder-stats.txt", 40)),
           ("Zustand der Software-Updates", tail_file("/run/pipbox-swupdate/status.json", 40)),
           ("Protokoll der Software-Updates", collapse_repeats(tail_file("/var/log/pipbox-swupdate.log", 150))),
           ("Protokoll der System-Updates", collapse_repeats(tail_file(f"{STATE}/update.log", 80)))]
    for u, n in (("pipbox-dji", 700), ("pipbox-send", 700), ("pipbox", 400), ("pipbox-wifi", 120), ("pipbox-btdriver", 120), ("pipbox-swupdate", 200),
                 ("pipbox-update", 100), ("pipbox-remote", 100), ("pipbox-power", 40)):
        text = journal(u, n)
        out.append(("Journal %s" % u, collapse_repeats(drop_noise(text) if u == "pipbox-dji" else text)))
    # Tailscale: Zustand der Freigaben (privat und öffentlich) und die Zeilen des Dienstes, die Freigabe, Zertifikat, Anmeldung und Fehler betreffen
    # (der Rest, zum Beispiel Verbindungsaufbau zu den Gegenstellen, ist Rauschen)
    ts = ("serve:\n" + (run(["tailscale", "serve", "status"], 10).strip() or "(leer)") + "\n\nfunnel:\n"
          + (run(["tailscale", "funnel", "status"], 10).strip() or "(leer)") + "\n")
    out.append(("Tailscale (Freigabe)", ts))
    tsj = [l for l in journal("tailscaled", 3000).splitlines()
           if re.search(r"(?i)serve|funnel|cert|acme|login|auth|expire|error|fail|warn|denied|health|hostinfo|netmap.*(changed|self)", l)
           and not re.search(r"(?i)portmapper|magicsock|derp|disco|netcheck", l)]
    out.append(("Journal tailscaled (nur Freigabe, Zertifikat, Anmeldung, Fehler)", "\n".join(tsj[-120:]) + "\n"))
    out.append(("Warnungen und Fehler des Systems (seit dem Start)", run(["journalctl", "-p", "warning", "-b", "--no-pager", "-o", "short-iso", "-n", "200"], 20)))
    out.append(("Kernel (USB, Bluetooth, WLAN)", "\n".join([l for l in run(["dmesg"], 10).splitlines()
                                                              if re.search(r"(?i)usb|bluetooth|btusb|wlan|wifi|cfg80211|rtl|brcm", l)][-150:]) + "\n"))
    return out


def build():
    sc = Scrubber()
    collect_secrets(sc)
    parts = []
    for title, body in sections():
        parts.append("\n===== %s =====\n%s" % (title, body.rstrip("\n") + "\n"))
    text = sc.scrub("".join(parts))
    if len(text.encode("utf-8")) > MAX_TOTAL:                          # zu groß: vom Ende kürzen (die jüngsten Abschnitte bleiben, der Kopf immer)
        head, rest = text.split("\n===== System =====", 1)
        rest = rest.encode("utf-8")[-(MAX_TOTAL - len(head.encode("utf-8")) - 200):].decode("utf-8", "ignore")
        text = head + "\n(Datei zu groß: der Anfang wurde gekürzt)\n===== System (gekürzt) =====" + rest.split("\n", 1)[-1]
    return text


def main():
    try:
        req = read_req(REQ).strip()
    except (OSError, ValueError):
        req = ""
    try:
        os.remove(REQ)
    except OSError:
        pass
    if req != "collect":
        write_status(state="error", message="Ungültige Anfrage")
        return 1
    write_status(state="working", message="Protokolle werden gesammelt …")
    try:
        text = build()
        os.makedirs(RUN, exist_ok=True)
        os.chmod(RUN, 0o755)
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT)
        write_status(state="done", message="Fertig", size=len(text.encode("utf-8")), version=read_small("/opt/pipbox/VERSION"))
    except Exception as e:                                                                   # nie ohne Meldung enden
        write_status(state="error", message="Fehler beim Sammeln: " + type(e).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
