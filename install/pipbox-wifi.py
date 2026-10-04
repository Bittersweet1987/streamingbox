#!/usr/bin/env python3
"""Root-Helfer für WLAN-Verbindungen (läuft nur über pipbox-wifi.path).

Liest aus der Auslösedatei eine feste Aktion (scan, connect, forget, disconnect, hotspot_start, hotspot_stop) mit streng geprüften
Werten (WLAN-Karte, Netzname, Passwort) und führt sie mit nmcli aus. Das Passwort eines WLANs, mit dem sich die Box verbindet, wird nie
als Befehlsargument übergeben (nur über stdin an nmcli) und nie ins Protokoll geschrieben; die Auslösedatei wird vor der Ausführung gelöscht.
Die Karte des Kameranetzes (camera-net.json) und Karten ohne WLAN werden abgelehnt.

Hotspot: Ein Stick wird mit einem NetworkManager-Profil "pipbox-hotspot-<Karte>" zum Zugangspunkt (WPA2, Adressvergabe und Weitergabe durch
NetworkManager, "shared"). Das Passwort eines Hotspots ist zum Weitergeben an Kameras und Handys gedacht und liegt deshalb in
hotspot.json (Benutzer pipbox, 0600), damit die Oberfläche es anzeigen und die Kamera-Anbindung es lesen kann.
"""
import json
import os
import pwd
import stat
import re
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/wifi-request"
RUN = "/run/pipbox-wifi"
STATUS = f"{RUN}/status.json"
IFACE_RE = re.compile(r"^[a-z][a-z0-9]{1,14}$")
ACTIONS = ("scan", "connect", "forget", "disconnect", "hotspot_start", "hotspot_stop")
HS_PREFIX = "pipbox-hotspot-"
HOTSPOT_FILE = f"{STATE}/hotspot.json"
HS_BANDS = {"bg": tuple(range(1, 14)), "a": (36, 40, 44, 48)}       # erlaubte Kanäle (0 = automatisch); 5 GHz nur ohne Radarpflicht (DFS)


def nm(*args, stdin=None, timeout=60):
    return subprocess.run(["nmcli", *args], input=stdin, capture_output=True, text=True, timeout=timeout)


def split_terse(line):
    """nmcli -t trennt mit ':' und maskiert ':' und '\\' im Text mit '\\'."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def write_status(**kw):
    os.makedirs(RUN, exist_ok=True)
    try:
        with open(STATUS) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.update(kw)
    s["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def wifi_devices():
    r = nm("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev")
    devs = []
    for line in r.stdout.splitlines():
        p = split_terse(line)
        if len(p) >= 4 and p[1] == "wifi":
            devs.append({"iface": p[0], "state": p[2], "connection": p[3]})
    return devs


def connection_names():
    r = nm("-t", "-f", "NAME,TYPE", "con", "show")
    return [p for p in (split_terse(l) for l in r.stdout.splitlines()) if len(p) >= 2 and p[1] == "802-11-wireless"]


def saved_wifi():
    """Gespeicherte WLAN-Netze zum Verbinden (ohne die Hotspot-Profile dieser Box)."""
    return sorted(p[0] for p in connection_names() if not p[0].startswith(HS_PREFIX))


def hs_name(iface):
    return HS_PREFIX + iface


def hotspot_active(iface):
    """Läuft auf dieser Karte gerade der Hotspot dieser Box?"""
    return any(d["iface"] == iface and d["connection"] == hs_name(iface) and d["state"] == "connected" for d in wifi_devices())


def wifi_caps(iface):
    """Was die Karte kann (NetworkManager): Zugangspunkt, 2,4 und 5 GHz."""
    r = nm("-g", "WIFI-PROPERTIES.AP,WIFI-PROPERTIES.2GHZ,WIFI-PROPERTIES.5GHZ", "dev", "show", iface, timeout=15)
    v = [x.strip().lower() == "yes" for x in r.stdout.splitlines()]
    v += [False] * (3 - len(v))
    return {"ap": v[0], "2ghz": v[1], "5ghz": v[2]}


def camera_iface():
    try:
        with open(f"{STATE}/camera-net.json") as f:
            return str(json.load(f).get("iface") or "")
    except (OSError, ValueError):
        return ""


def check_iface(iface):
    if not isinstance(iface, str) or not IFACE_RE.match(iface):
        raise ValueError("WLAN-Karte ungültig")
    if iface not in [d["iface"] for d in wifi_devices()]:
        raise ValueError("Das ist keine WLAN-Karte")
    if iface == camera_iface():
        raise ValueError("Diese Karte ist das Kameranetz und wird nicht verändert")


def check_client_iface(iface):
    """Wie check_iface, aber für Aktionen als Client (suchen, verbinden, trennen): Läuft auf der Karte ein Hotspot, geht das nicht."""
    check_iface(iface)
    if hotspot_active(iface):
        raise ValueError("Auf dieser Karte läuft ein Hotspot. Bitte zuerst den Hotspot beenden.")


def check_not_hotspot_name(ssid):
    if isinstance(ssid, str) and ssid.startswith(HS_PREFIX):
        raise ValueError("Dieser Netzname ist für den Hotspot der Box reserviert")


def check_not_camera_profile(ssid):
    """Das Profil, mit dem die Kamera-Karte gerade verbunden ist, wird weder ersetzt noch gelöscht."""
    cam = camera_iface()
    if cam and any(d["iface"] == cam and d["connection"] == ssid for d in wifi_devices()):
        raise ValueError("Dieses WLAN gehört zum Kameranetz und wird nicht verändert")


def check_ssid(ssid):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise ValueError("Netzname ungültig (1 bis 32 Zeichen)")


def check_password(pw):
    if pw == "":
        return
    if not isinstance(pw, str) or not (8 <= len(pw) <= 63 and all(32 <= ord(c) < 127 for c in pw)
                                         or re.fullmatch(r"[0-9a-fA-F]{64}", pw or "")):
        raise ValueError("Passwort: 8 bis 63 Zeichen (nur ASCII) oder leer bei offenem Netz")


def read_nets(iface):
    """Die Liste der gefundenen Netze einer Karte, wie NetworkManager sie gerade kennt: {SSID: Netz} (ohne neuen Suchlauf)."""
    r = nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "ifname", iface, "--rescan", "no", timeout=15)
    best = {}
    for line in r.stdout.splitlines():
        p = split_terse(line)
        if len(p) < 4 or not p[1]:
            continue
        sig = int(p[2]) if p[2].isdigit() else 0
        cur = best.get(p[1])
        if cur is None or sig > cur["signal"] or p[0] == "*":
            best[p[1]] = {"ssid": p[1], "signal": sig, "security": p[3] or "offen", "in_use": p[0] == "*" or bool(cur and cur["in_use"])}
    return best


SCAN_WAIT = 12        # so lange wird auf die Ergebnisse eines Suchlaufs gewartet (Sekunden)


def do_scan(iface):
    """Suchlauf auf der gewählten Karte. "--rescan yes" gibt bei manchen Sticks die Liste zurück, bevor der Suchlauf fertig ist (dann stand dort "0 Netze
    gefunden", obwohl die Karte Netze sieht): Darum wird der Suchlauf angestoßen und danach gewartet, bis die Liste nicht mehr leer ist (und noch einen
    Moment länger, damit sie vollständig wird)."""
    check_client_iface(iface)
    nm("radio", "wifi", "on")
    try:
        nm("dev", "wifi", "rescan", "ifname", iface, timeout=20)     # ein Fehler ("Suchlauf gerade nicht erlaubt") ist hier kein Problem: die Liste wird trotzdem gelesen
    except subprocess.TimeoutExpired:
        pass
    deadline = time.time() + SCAN_WAIT
    best = read_nets(iface)
    while not best and time.time() < deadline:
        time.sleep(1.5)
        best = read_nets(iface)
    if best:
        time.sleep(2.0)                                              # der Suchlauf findet nach dem ersten Treffer meist noch weitere Netze
        best.update({k: v for k, v in read_nets(iface).items()})
    nets = sorted(best.values(), key=lambda n: (not n["in_use"], -n["signal"]))
    write_status(scan={"iface": iface, "nets": nets[:40], "scanned": int(time.time())})
    if not nets:
        return ("Keine Netze gefunden. Manche Sticks brauchen einen zweiten Suchlauf: bitte noch einmal „Netze suchen“ drücken. "
                "Bleibt es leer, ist die Karte nicht in Reichweite eines Netzes oder noch nicht bereit.")
    return f"{len(nets)} Netze gefunden"


def connect_saved(iface, ssid):
    """Ein gespeichertes Netz ohne neue Passworteingabe mit seinem gespeicherten Passwort auf der gewählten Karte einschalten. Früher wurde das
    Profil hier gelöscht und ohne Passwort neu versucht: das schlug mit "Password" fehl, und das gespeicherte Netz war danach weg."""
    nm("con", "modify", "id", ssid, "connection.interface-name", iface)      # das Profil gilt auch für eine andere Karte, wenn man es dort wählt
    r = nm("con", "up", "id", ssid, "ifname", iface, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()[:160] or "unbekannter Fehler"
        raise RuntimeError(f"Verbinden mit dem gespeicherten Netz „{ssid}“ fehlgeschlagen: {msg}. Hat sich das Passwort geändert, bitte neu eingeben.")
    nm("con", "modify", "id", ssid, "connection.autoconnect", "yes", "ipv4.route-metric", "600")
    return f"Mit „{ssid}“ verbunden (gespeichertes Netz)"


def do_connect(req):
    iface, ssid, pw = req.get("iface"), req.get("ssid"), req.get("password", "")
    check_client_iface(iface)
    check_ssid(ssid)
    check_not_hotspot_name(ssid)
    check_password(pw)
    hidden = req.get("hidden") is True
    if ssid in saved_wifi():
        check_not_camera_profile(ssid)
        if not pw and not hidden:
            return connect_saved(iface, ssid)
        nm("con", "delete", "id", ssid)             # neues Passwort eingegeben: altes Profil ersetzen
    args = ["--ask", "dev", "wifi", "connect", ssid, "ifname", iface]
    if hidden:
        args += ["hidden", "yes"]
    r = nm(*args, stdin=(pw + "\n") if pw else None, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace(pw, "***") if pw else (r.stderr or r.stdout).strip()
        raise RuntimeError("Verbinden fehlgeschlagen: " + (msg[:160] or "unbekannter Fehler"))
    nm("con", "modify", "id", ssid, "connection.autoconnect", "yes", "ipv4.route-metric", "600")
    return f"Mit „{ssid}“ verbunden"


def do_forget(req):
    ssid = req.get("ssid")
    check_ssid(ssid)
    check_not_hotspot_name(ssid)
    if ssid not in saved_wifi():
        raise ValueError("Dieses WLAN ist nicht gespeichert")
    check_not_camera_profile(ssid)
    nm("con", "delete", "id", ssid)
    return f"„{ssid}“ vergessen"


def do_disconnect(req):
    check_client_iface(req.get("iface"))
    nm("dev", "disconnect", req["iface"])
    return "Getrennt"


# ------------------------------------------------------------------ Hotspot

def hs_load():
    """Gespeicherte Hotspot-Einstellungen {Karte: {ssid, password, band, channel}} (ohne Verweisen zu folgen)."""
    try:
        fd = os.open(HOTSPOT_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return {}
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}
        d = json.loads(os.read(fd, 65536).decode("utf-8", "replace"))
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}
    finally:
        os.close(fd)


def hs_store(data):
    """hotspot.json atomar schreiben, Besitzer pipbox, 0600. Der Ordner gehört dem Benutzer pipbox: nie einem untergeschobenen Verweis folgen
    (Temporärdatei vorher entfernen, mit O_EXCL | O_NOFOLLOW neu anlegen, Besitzer über den Dateideskriptor setzen)."""
    tmp = HOTSPOT_FILE + ".tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            u = pwd.getpwnam("pipbox")
            os.fchown(fd, u.pw_uid, u.pw_gid)
        except (KeyError, PermissionError):
            pass
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, HOTSPOT_FILE)


def check_hotspot_ssid(ssid):
    check_ssid(ssid)
    if ssid != ssid.strip():
        raise ValueError("Netzname: keine Leerzeichen am Anfang oder Ende")
    check_not_hotspot_name(ssid)


def check_hotspot_password(pw):
    if not isinstance(pw, str) or not 8 <= len(pw) <= 63 or any(not 32 <= ord(c) < 127 for c in pw):
        raise ValueError("Passwort: 8 bis 63 Zeichen (nur ASCII)")


def do_hotspot_start(req):
    iface = req.get("iface")
    check_iface(iface)
    ssid = req.get("ssid")
    check_hotspot_ssid(ssid)
    saved = hs_load().get(iface)
    saved = saved if isinstance(saved, dict) else {}
    pw = req.get("password", "")
    if pw == "":
        pw = str(saved.get("password", ""))                      # leer = das gespeicherte Passwort behalten
    check_hotspot_password(pw)
    band = req.get("band", "bg")
    if band not in HS_BANDS:
        raise ValueError("Band ungültig (2,4 GHz oder 5 GHz)")
    ch = req.get("channel", 0)
    if isinstance(ch, bool) or not isinstance(ch, int) or (ch != 0 and ch not in HS_BANDS[band]):
        raise ValueError("Kanal ungültig für dieses Band")
    caps = wifi_caps(iface)
    if not caps["ap"]:
        raise ValueError("Diese WLAN-Karte kann keinen Hotspot aufbauen (kein Zugangspunkt-Betrieb)")
    if band == "a" and not caps["5ghz"]:
        raise ValueError("Diese WLAN-Karte kann kein 5 GHz")
    if band == "bg" and not caps["2ghz"] and caps["5ghz"]:
        raise ValueError("Diese WLAN-Karte kann nur 5 GHz")
    name = hs_name(iface)
    if name in [p[0] for p in connection_names()]:
        nm("con", "delete", "id", name)                          # Profil neu anlegen, damit nichts Altes (Kanal, Passwort) zurückbleibt
    args = ["con", "add", "type", "wifi", "ifname", iface, "con-name", name, "autoconnect", "yes", "ssid", ssid,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", band]
    if ch:
        args += ["802-11-wireless.channel", str(ch)]
    # Das Passwort eines Hotspots ist zum Weitergeben gedacht (Kamera, Handy); der Befehl läuft nur kurz als root.
    args += ["ipv4.method", "shared", "ipv6.method", "ignore", "connection.autoconnect-priority", "100",
             "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp", "wifi-sec.psk", pw]
    r = nm(*args, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("Hotspot konnte nicht angelegt werden: " + ((r.stderr or r.stdout).strip().replace(pw, "***")[:160] or "unbekannter Fehler"))
    r = nm("con", "up", "id", name, "ifname", iface, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace(pw, "***")[:160] or "unbekannter Fehler"
        nm("con", "delete", "id", name)
        try:
            nm("dev", "connect", iface, timeout=30)                  # das vorherige Netz der Karte wieder aufnehmen (soweit gespeichert)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise RuntimeError("Hotspot konnte nicht gestartet werden: " + msg)
    data = hs_load()
    data[iface] = {"ssid": ssid, "password": pw, "band": band, "channel": ch}
    hs_store(data)
    return f"Hotspot „{ssid}“ läuft auf {iface} ({'5' if band == 'a' else '2,4'} GHz, Kanal {ch or 'automatisch'})"


def do_hotspot_stop(req):
    iface = req.get("iface")
    check_iface(iface)
    name = hs_name(iface)
    if name not in [p[0] for p in connection_names()]:
        raise ValueError("Auf dieser Karte ist kein Hotspot eingerichtet")
    nm("con", "modify", "id", name, "connection.autoconnect", "no")      # beendet bleibt beendet, auch nach einem Neustart der Box
    nm("con", "down", "id", name)                                          # ist er gar nicht aktiv, meldet nmcli einen Fehler: egal
    return "Hotspot beendet. Die Karte verbindet sich wieder mit einem gespeicherten Netz, wenn eines in Reichweite ist."


def read_req(path, limit=4096):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def main():
    try:
        req = json.loads(read_req(REQ, 8192))
    except (OSError, ValueError):
        req = None
    try:
        os.remove(REQ)                              # Passwort sofort von der Platte nehmen
    except OSError:
        pass
    if not isinstance(req, dict) or req.get("action") not in ACTIONS:
        write_status(state="error", message="Ungültige Anfrage")
        return 1
    action = req["action"]
    write_status(state="working", message="Wird ausgeführt …", action=action)
    try:
        if action == "scan":
            msg = do_scan(req.get("iface"))
        elif action == "connect":
            msg = do_connect(req)
        elif action == "forget":
            msg = do_forget(req)
        elif action == "hotspot_start":
            msg = do_hotspot_start(req)
        elif action == "hotspot_stop":
            msg = do_hotspot_stop(req)
        else:
            msg = do_disconnect(req)
        write_status(state="done", message=msg, saved=saved_wifi())
    except (ValueError, RuntimeError) as e:
        write_status(state="error", message=str(e), saved=saved_wifi())
    except (OSError, subprocess.TimeoutExpired) as e:
        write_status(state="error", message="Fehler: " + type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
