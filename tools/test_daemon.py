"""Tests für den Wächter des Kamera-Dienstes (dji_daemon.py) und die Verbindungssperre (dji.py). Ohne Bluetooth."""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import dji  # noqa: E402
import dji_daemon as dd  # noqa: E402

PARAMS = {"model": "osmoAction4", "ssid": "x", "password": "y", "url": "rtmp://10.0.0.1:1935/publish/dji-aaaaaa",
          "res": "1080p", "fps": 30, "kbps": 10000, "codec": "AVC", "stab": "off"}


class FakeDji:
    def __init__(self):
        self.sessions, self.started, self.stopped = {}, [], []

    def status(self):
        return {"sessions": {k: {"state": v} for k, v in self.sessions.items()}, "devices": []}

    def start(self, addr, *a):
        self.started.append(addr)
        self.sessions[addr] = "connecting"

    def stop(self, addr=None):
        self.stopped.append(addr)


class Supervisor(unittest.TestCase):
    def make(self, cams=("AA:AA", "BB:BB"), iface_up=True):
        self.tmp = tempfile.mkdtemp()
        json.dump({"iface": "eth9"}, open(os.path.join(self.tmp, "camera-net.json"), "w"))
        self.fake = FakeDji()
        dm = dd.Daemon(self.tmp, self.fake)
        dm.desired = {c: dict(PARAMS, url=f"rtmp://10.0.0.1:1935/publish/dji-{c[:2].lower()}") for c in cams}
        dm.ipfn = lambda: "10.0.0.1"
        self.up = [iface_up]
        p1 = mock.patch.object(dd, "iface_ip", lambda name: "10.0.0.1" if self.up[0] else None)
        p2 = mock.patch.object(dd, "publishing_keys", lambda: set())
        p3 = mock.patch.object(dd.time, "sleep", lambda s: None)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        for c in cams:
            self.fake.sessions[c] = "failed"
        return dm

    def test_one_new_connection_per_round(self):
        dm = self.make()
        dm.supervise_once(100)
        self.assertEqual(self.fake.started, ["AA:AA"])            # nur eine Kamera
        self.fake.sessions["AA:AA"] = "connecting"
        dm.supervise_once(105)
        self.assertEqual(self.fake.started, ["AA:AA", "BB:BB"])   # die nächste im folgenden Durchlauf

    def test_no_attempts_while_network_is_down(self):
        dm = self.make(iface_up=False)
        for t in range(100, 400, 5):
            dm.supervise_once(t)
        self.assertEqual(self.fake.started, [])
        self.assertEqual(dm.tries, {})                             # Wartezeiten wurden nicht verbraucht

    def test_network_return_resets_backoff_and_waits(self):
        dm = self.make(cams=("AA:AA",))
        dm.tries["AA:AA"] = (5, 10 ** 9)                           # lange Wartezeit aufgelaufen
        self.up[0] = False
        dm.supervise_once(100)                                     # Router weg
        self.up[0] = True
        dm.supervise_once(110)                                     # Router wieder da: zurücksetzen, noch warten
        self.assertEqual(self.fake.started, [])
        dm.supervise_once(110 + dd.NET_SETTLE - 1)
        self.assertEqual(self.fake.started, [])
        dm.supervise_once(110 + dd.NET_SETTLE + 1)
        self.assertEqual(self.fake.started, ["AA:AA"])

    def test_normal_backoff_still_grows(self):
        dm = self.make(cams=("AA:AA",))
        dm.supervise_once(100)
        self.fake.sessions["AA:AA"] = "failed"
        dm.supervise_once(105)                                     # noch in der Wartezeit (10 s)
        self.assertEqual(len(self.fake.started), 1)
        dm.supervise_once(111)
        self.assertEqual(len(self.fake.started), 2)
        self.assertEqual(dm.tries["AA:AA"][0], 2)


class ConnectionLock(unittest.TestCase):
    def make(self):
        d = dji.Dji.__new__(dji.Dji)
        d._conn_lock = threading.Lock()
        d._set = lambda *a, **k: None
        return d

    def test_second_camera_waits_for_the_first(self):
        d = self.make()
        order = []
        stop = threading.Event()
        with mock.patch.object(dji.time, "sleep", lambda s: None):
            d._acquire_conn("A", stop)
            order.append("A holt")
            t = threading.Thread(target=lambda: (d._acquire_conn("B", threading.Event()), order.append("B holt")))
            t.start()
            time.sleep(0.8)
            self.assertEqual(order, ["A holt"])                    # B wartet noch
            d._release_conn()
            t.join(3)
        self.assertEqual(order, ["A holt", "B holt"])

    def test_waiting_camera_can_be_stopped(self):
        d = self.make()
        d._conn_lock.acquire()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(RuntimeError):
            d._acquire_conn("B", stop)


class BluetoothSticks(unittest.TestCase):
    """Erkennung von Bluetooth-Sticks über /sys und verständliche Hinweise (ohne Bluetooth, ohne Hardware)."""

    def usb(self, devs):
        """Baut einen nachgestellten /sys/bus/usb/devices-Baum: devs = [(Ordner, vid, pid, Name, (Klasse, Unterklasse, Protokoll) oder None, Treiber)]."""
        root = tempfile.mkdtemp()
        for name, vid, pid, product, cls, drv in devs:
            d = os.path.join(root, name)
            os.makedirs(d)
            for fn, val in (("idVendor", vid), ("idProduct", pid), ("product", product)):
                with open(os.path.join(d, fn), "w") as f:
                    f.write(val + "\n")
            if cls:
                i = os.path.join(d, name + ":1.0")
                os.makedirs(i)
                for fn, val in zip(("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"), cls):
                    with open(os.path.join(i, fn), "w") as f:
                        f.write(val + "\n")
                if drv:
                    target = os.path.join(root, "drivers", drv)
                    os.makedirs(target, exist_ok=True)
                    os.symlink(target, os.path.join(i, "driver"))
        return root

    def test_finds_bluetooth_class_devices_and_barrot(self):
        root = self.usb([("5-1.4", "0b05", "190E", "ASUS USB-BT500", ("e0", "01", "01"), "btusb"),
                         ("2-1", "0bda", "c811", "802.11ac NIC", ("ff", "ff", "ff"), "rtl8821cu"),
                         ("5-1.2", "33FA", "0010", "BARROT Bluetooth 5.4 Adapter", ("e0", "01", "01"), None),
                         ("5-1.3", "33fa", "0001", "BRTLink", ("08", "06", "50"), None)])
        found = {d["id"]: d for d in dji.usb_bluetooth_devices(root)}
        self.assertEqual(sorted(found), ["0b05:190e", "33fa:0001", "33fa:0010"])      # das WLAN-Gerät fehlt
        self.assertEqual(found["0b05:190e"]["driver"], "btusb")
        self.assertEqual(found["33fa:0010"]["driver"], "")
        self.assertEqual(found["0b05:190e"]["name"], "ASUS USB-BT500")

    def test_missing_sysfs_is_harmless(self):
        self.assertEqual(dji.usb_bluetooth_devices("/nonexistent/usb"), [])

    def test_modalias_to_usb_id(self):
        self.assertEqual(dji.usb_id_from_modalias("usb:v0B05p190Ed0200"), "0b05:190e")
        self.assertEqual(dji.usb_id_from_modalias("pci:xyz"), "")
        self.assertEqual(dji.usb_id_from_modalias(None), "")

    def test_working_stick_is_no_problem_and_barrot_gets_the_explanation(self):
        devs = [{"id": "0b05:190e", "name": "ASUS USB-BT500", "driver": "btusb"},
                {"id": "33fa:0010", "name": "BARROT Bluetooth 5.4 Adapter", "driver": ""}]
        prob = dji.adapter_problems(["0b05:190e"], devs)
        self.assertEqual([p["id"] for p in prob], ["33fa:0010"])
        self.assertIn("BARROT", prob[0]["hint"])
        self.assertIn("TP-Link UB500", prob[0]["hint"])
        self.assertEqual(dji.adapter_problems(["0b05:190e", "33fa:0010"], devs), [])

    def test_unknown_stick_without_adapter_gets_a_generic_hint(self):
        prob = dji.adapter_problems([], [{"id": "1234:abcd", "name": "Mein Stick", "driver": "btusb"}])
        self.assertIn("Mein Stick", prob[0]["hint"])
        self.assertIn("1234:abcd", prob[0]["hint"])
        self.assertNotIn("BARROT", prob[0]["hint"])

    def test_status_carries_adapters_and_problems(self):
        d = dji.Dji.__new__(dji.Dji)
        d.lock = threading.Lock()
        d.scanning, d.scan_error, d.devices, d.sessions = False, "", [], {}
        d.adapter_paths = lambda: ["/org/bluez/hci0"]
        d.objects = lambda: {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Modalias": "usb:v0B05p190Ed0200", "Address": "AA:BB", "Powered": True}}}
        root = self.usb([("5-1.4", "0b05", "190e", "ASUS USB-BT500", ("e0", "01", "01"), "btusb"),
                         ("5-1.2", "33fa", "0010", "BARROT Bluetooth 5.4 Adapter", ("e0", "01", "01"), None)])
        with mock.patch.object(dji, "SYSFS_USB", root):
            st = d.status()
        self.assertEqual(st["adapters"][0]["usb_id"], "0b05:190e")
        self.assertEqual([p["id"] for p in st["adapter_problems"]], ["33fa:0010"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
