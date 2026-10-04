#!/usr/bin/env python3
"""Root-Helfer: SSH-Dienst ein- oder ausschalten (läuft nur über pipbox-ssh.path).

Liest aus der Auslösedatei ein Stichwort aus fester Liste (start, stop, check), ohne Verweisen zu folgen, löscht die Datei und führt
`systemctl start ssh` oder `systemctl stop ssh` aus; "check" ändert nichts und stellt nur fest, ob das SSH-Passwort noch das ist, das die
Original-Oberfläche der BELABOX erzeugt hat (Vergleich der Passwort-Zeile des Benutzers in /etc/shadow mit der dort gespeicherten; nichts davon
wird weitergegeben, nur das Ergebnis "generated", "own" oder "unknown"). Der Helfer ändert nichts an Passwörtern, Schlüsseln oder der
Konfiguration von SSH und schaltet auch das Starten beim Hochfahren nicht um: nach einem Neustart gilt wieder die Einstellung des Systems."""
import json
import os
import re
import stat
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/ssh-request"
RUN = "/run/pipbox-ssh"
STATUS = f"{RUN}/status.json"
SERVICE = "ssh"
ACTIONS = ("start", "stop", "check")
BELA_DIR = "/opt/belaUI"
SHADOW = "/etc/shadow"
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


def read_req(path, limit=32):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def write_status(**kw):
    os.makedirs(RUN, exist_ok=True)
    kw["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def read_json(path, limit=65536):
    try:
        with open(path) as f:
            d = json.loads(f.read(limit))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def password_state():
    """"generated": Das Passwort ist (noch) das von der Original-Oberfläche erzeugte, "own": jemand hat es danach selbst geändert, "unknown": nicht
    feststellbar. Verglichen wird, wie in der Original-Oberfläche, die Zeile des Benutzers in /etc/shadow mit der dort gemerkten."""
    user = (read_json(f"{BELA_DIR}/setup.json") or {}).get("ssh_user")
    cfg = read_json(f"{BELA_DIR}/config.json") or {}
    if not isinstance(user, str) or not USER_RE.fullmatch(user) or not cfg.get("ssh_pass") or not cfg.get("ssh_pass_hash"):
        return "unknown"
    try:
        with open(SHADOW) as f:
            line = next((l.rstrip("\n") for l in f if l.startswith(user + ":")), None)
    except OSError:
        return "unknown"
    if line is None:
        return "unknown"
    return "generated" if line == str(cfg["ssh_pass_hash"]).rstrip("\n") else "own"


def systemctl(*args, timeout=30):
    return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=timeout)


def apply(action):
    if action not in ACTIONS:
        raise ValueError("Unbekannte Aktion")
    if action == "check":
        return "Passwort geprüft"
    if systemctl("cat", SERVICE + ".service").returncode != 0:
        raise RuntimeError("Der SSH-Dienst ist auf dieser Box nicht installiert")
    r = systemctl(action, SERVICE)
    if r.returncode != 0:
        raise RuntimeError("systemctl %s %s fehlgeschlagen: %s" % (action, SERVICE, (r.stderr or r.stdout).strip()[:160] or "unbekannter Fehler"))
    active = systemctl("is-active", SERVICE).stdout.strip() == "active"
    if (action == "start") != active:
        raise RuntimeError("Der SSH-Dienst ist danach nicht %s" % ("gestartet" if action == "start" else "beendet"))
    return "SSH eingeschaltet" if active else "SSH ausgeschaltet"


def main():
    try:
        action = read_req(REQ).strip()
    except (OSError, ValueError):
        action = ""
    try:
        os.remove(REQ)
    except OSError:
        pass
    if action not in ACTIONS:
        write_status(state="error", message="Ungültige Anfrage")
        return 1
    write_status(state="working", message="Wird ausgeführt …", action=action)
    try:
        write_status(state="done", message=apply(action), action=action, password_state=password_state())
    except (ValueError, RuntimeError) as e:
        write_status(state="error", message=str(e), action=action, password_state=password_state())
        return 1
    except (OSError, subprocess.TimeoutExpired) as e:
        write_status(state="error", message="Fehler: " + type(e).__name__, action=action)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
