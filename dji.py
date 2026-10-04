"""Bluetooth-Sticks und -Adapter der Box: welche Sticks stecken, welche Adapter BlueZ kennt, welche Hinweise der Nutzer braucht.

Hilfen für den DJI-Dienst (dji_daemon.py) und die Oberfläche. Der Kamerateil selbst (Suche, Kopplung, Stream, Protokoll nach
Moblin, MIT-Lizenz, Copyright (c) 2023 Erik Moqvist) steht in dji_daemon.py. Liest nur /sys und BlueZ (D-Bus), ändert nichts.
"""
import json
import os
import re

# ---------------------------------------------------------------- Bluetooth-Sticks

SYSFS_USB = "/sys/bus/usb/devices"
SYSFS_BT = "/sys/class/bluetooth"
BTDRIVER_STATUS = "/run/pipbox-btdriver/status.json"     # schreibt der Root-Helfer pipbox-btdriver.py (Treiber für Realtek-Sticks)
BARROT_VENDOR = "33fa"      # Barrot Technology (z. B. UGREEN Bluetooth 5.4 und 6.0, Modell CM748)
BARROT_HINT = ("Dieser Stick hat einen BARROT-Chip (zum Beispiel UGREEN Bluetooth 5.4 oder 6.0). Der Kernel dieser BELABOX (5.10) "
               "unterstützt ihn nicht: Der Chip bleibt beim Start hängen, es entsteht kein Bluetooth-Adapter. Ein Update dieses Pakets "
               "kann das nicht beheben (laut Berichten enthalten erst Linux ab 6.18 und die Langzeitzweige ab 6.12.58 und 6.6.117 die Korrektur). "
               "Bitte einen Stick mit Realtek-Chip RTL8761B verwenden, zum Beispiel TP-Link UB500 oder ASUS USB-BT500.")


def _read1(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def usb_bluetooth_devices(root=None):
    """USB-Geräte, die sich als Bluetooth-Stick melden (Schnittstellenklasse e0/01/01) oder von Barrot sind: Liste von
    {"id": "vvvv:pppp", "name": Produktname, "driver": Treiber der Bluetooth-Schnittstelle oder ""}. Liest nur /sys."""
    root = root or SYSFS_USB
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        if ":" in n:
            continue                              # Schnittstellen werden unten über ihr Gerät gelesen
        base = os.path.join(root, n)
        vid, pid = _read1(f"{base}/idVendor").lower(), _read1(f"{base}/idProduct").lower()
        if not vid or not pid:
            continue
        bt, driver = False, ""
        try:
            for i in sorted(os.listdir(base)):
                ib = os.path.join(base, i)
                if ":" in i and (_read1(f"{ib}/bInterfaceClass").lower(), _read1(f"{ib}/bInterfaceSubClass"),
                                 _read1(f"{ib}/bInterfaceProtocol")) == ("e0", "01", "01"):
                    bt = True
                    drv = os.path.realpath(f"{ib}/driver") if os.path.islink(f"{ib}/driver") else ""
                    driver = driver or os.path.basename(drv)
        except OSError:
            pass
        if bt or vid == BARROT_VENDOR:
            out.append({"id": f"{vid}:{pid}", "name": _read1(f"{base}/product") or "Bluetooth-Stick", "driver": driver})
    return out


def usb_id_for_hci(name, root=None):
    """USB-Kennung "vvvv:pppp" des Sticks hinter einem Bluetooth-Adapter (z. B. "hci0"), gelesen aus /sys: vom Adapter aufwärts bis zum
    USB-Gerät. Leer bei einem eingebauten Adapter (kein USB-Gerät darüber). BlueZ selbst liefert dafür nichts Brauchbares: Seine
    Modalias ist meist die Standardkennung "usb:v1D6Bp0246" (Linux Foundation) und nennt nie den Stick."""
    p = os.path.realpath(os.path.join(root or SYSFS_BT, name))
    for _ in range(12):
        vid, pid = _read1(f"{p}/idVendor").lower(), _read1(f"{p}/idProduct").lower()
        if vid and pid:
            return f"{vid}:{pid}"
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    return ""


def usb_id_from_modalias(modalias):
    m = re.search(r"usb:v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})", str(modalias or ""))
    return f"{m.group(1).lower()}:{m.group(2).lower()}" if m else ""


def adapter_problems(adapter_ids, usb_devs):
    """Sticks, die steckten, aus denen der Kernel aber keinen Bluetooth-Adapter gemacht hat: Liste von {"id","name","hint"}.
    adapter_ids: USB-Kennungen der Adapter, die BlueZ kennt. Der eingebaute Adapter anderer Boxen hat keine USB-Kennung und stört nicht."""
    have = set(adapter_ids)
    out = []
    for d in usb_devs:
        if d["id"] in have:
            continue
        if d["id"].startswith(BARROT_VENDOR + ":"):
            hint = BARROT_HINT
        else:
            hint = (f"Der Stick {d['name']} ({d['id']}) wurde erkannt, aber der Kernel hat keinen Bluetooth-Adapter daraus gemacht. "
                    "Möglicherweise fehlt die Firmware oder der Chip wird nicht unterstützt (Systemmeldungen: dmesg).")
        out.append({"id": d["id"], "name": d["name"], "hint": hint})
    return out


BLUEZ = "org.bluez"
OM = "org.freedesktop.DBus.ObjectManager"


def bluez_objects():
    """Alle Objekte von BlueZ ({Pfad: {Schnittstelle: Eigenschaften}}) über D-Bus (python3-dbus), leer, wenn nicht erreichbar."""
    try:
        import dbus
        bus = dbus.SystemBus()
        return dict(dbus.Interface(bus.get_object(BLUEZ, "/"), OM).GetManagedObjects())
    except Exception:
        return {}


def adapter_info(objects=None):
    """Welche Bluetooth-Adapter laufen (USB-Kennung, Adresse, an/aus), welche Sticks stecken, ohne einen Adapter zu ergeben,
    und was der Treiber-Helfer meldet. objects: für Tests; sonst von BlueZ gelesen."""
    objs = bluez_objects() if objects is None else objects
    adapters, ids = [], []
    for path, ifs in sorted(objs.items(), key=lambda kv: str(kv[0])):
        a = ifs.get("org.bluez.Adapter1")
        if not a:
            continue
        mid = usb_id_from_modalias(a.get("Modalias"))
        # Die Kennung des Sticks kommt aus /sys; BlueZ meldet meist nur die Standardkennung (1d6b:0246, Linux Foundation)
        uid = usb_id_for_hci(os.path.basename(str(path))) or ("" if mid.startswith("1d6b:") else mid)
        adapters.append({"usb_id": uid, "address": str(a.get("Address", "")), "powered": bool(a.get("Powered", False))})
        if uid:
            ids.append(uid)
    drv = {}
    try:
        with open(BTDRIVER_STATUS) as f:
            raw = json.load(f)
        drv = {"state": str(raw.get("state", "")), "message": str(raw.get("message", ""))[:300]}
    except (OSError, ValueError, AttributeError):
        pass
    return {"adapters": adapters, "adapter_problems": adapter_problems(ids, usb_bluetooth_devices()), "driver": drv}
