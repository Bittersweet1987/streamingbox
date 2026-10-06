"""Tests: Die gewählte Verbindung einer DJI-Kamera (z. B. der mobile Router) bleibt dieselbe, auch wenn die Netzwerknamen nach einem Neustart vertauscht sind.

Vorfall 6. Oktober 2026: Gespeichert war der Name "eth0". Nach einem Neustart meinte "eth0" das Heimnetz (192.168.1.x) statt des Routers (192.168.80.x);
die Kameras bekamen die Heimnetz-Adresse, die aus dem Router-WLAN nicht erreichbar ist, und der Stream fiel aus. Jetzt gilt die Hardware-Adresse (MAC)."""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import dji_daemon as dd  # noqa: E402

ADDR = "D0:D0:4B:00:00:01"
ROUTER = "c0:74:2b:fe:fa:3b"
LAN = "c0:74:2b:fe:fa:3a"


def fake_sysfs(mapping):
    """Ordner wie /sys/class/net: {Name: MAC}."""
    root = tempfile.mkdtemp()
    for name, mac in mapping.items():
        os.makedirs(os.path.join(root, name))
        with open(os.path.join(root, name, "address"), "w") as f:
            f.write(mac + "\n")
    return root


def options(**ips):
    return [{"ifname": n, "ssid": "", "password": "", "ip": ip, "type": "other"} for n, ip in ips.items()]


class Helpers(unittest.TestCase):
    def test_mac_and_lookup(self):
        root = fake_sysfs({"eth0": LAN, "eth1": ROUTER, "lo": "00:00:00:00:00:00"})
        self.assertEqual(dd.iface_mac("eth1", root), ROUTER)
        self.assertEqual(dd.iface_by_mac(ROUTER, root), "eth1")
        self.assertEqual(dd.iface_mac("lo", root), "")                    # nur Nullen: keine brauchbare Adresse
        self.assertIsNone(dd.iface_by_mac("aa:bb:cc:dd:ee:ff", root))
        self.assertIsNone(dd.iface_by_mac("", root))

    def test_bad_names_are_refused(self):
        root = fake_sysfs({"eth0": LAN})
        for bad in ("../etc", "", None, "a" * 40, "eth0/../x"):
            self.assertEqual(dd.iface_mac(bad, root), "")


class Pinning(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()                     # der Dienst legt beim Erzeugen Sperren an, die eine Ereignisschleife brauchen
        asyncio.set_event_loop(self.loop)
        self.addCleanup(self.loop.close)
        self.dm = dd.Daemon(tempfile.mkdtemp())
        self.cfg = {"name": "A", "model": "", "kind": "action4", "wifi_ifname": "eth0", "ssid": "Router-WLAN", "password": "pw", "rtmp_key": "dji-000001",
                    "autoconnect": False, **dd.DEFAULT_SETTINGS}
        self.dm.cameras[ADDR] = self.cam = dd.Camera(self.dm, ADDR, self.cfg)

    def resolve(self, root, opts):
        with mock.patch.object(dd, "SYSFS_NET", root), mock.patch.object(self.dm, "wifi_options", lambda: opts):
            return self.cam.resolve_target()

    def test_swapped_names_after_a_reboot_keep_the_router(self):
        self.cfg.update(wifi_ifname="eth0", wifi_mac=ROUTER)                       # gespeichert, als der Router noch eth0 hieß
        root = fake_sysfs({"eth0": LAN, "eth1": ROUTER})                          # jetzt ist eth0 das Heimnetz
        ssid, pw, url = self.resolve(root, options(eth0="192.168.1.20", eth1="192.168.80.2"))
        self.assertIn("192.168.80.2", url)
        self.assertNotIn("192.168.1", url)
        self.assertEqual(self.cfg["wifi_ifname"], "eth1")                          # der Name zieht mit

    def test_missing_router_is_an_error_not_a_switch_to_the_home_network(self):
        self.cfg.update(wifi_ifname="eth1", wifi_mac=ROUTER)
        root = fake_sysfs({"eth0": LAN})                                           # Router nicht angeschlossen
        with self.assertRaises(dd.CameraError) as cm:
            self.resolve(root, options(eth0="192.168.1.20"))
        self.assertIn("nicht da", str(cm.exception))
        self.assertIn("wechselt nicht ins Heimnetz", str(cm.exception))

    def test_router_without_an_address_is_also_not_replaced(self):
        self.cfg.update(wifi_ifname="eth1", wifi_mac=ROUTER)
        root = fake_sysfs({"eth0": LAN, "eth1": ROUTER})
        with self.assertRaises(dd.CameraError):
            self.resolve(root, options(eth0="192.168.1.20"))                    # eth1 hat (noch) keine IPv4-Adresse

    def test_old_settings_without_mac_work_as_before(self):
        self.cfg.update(wifi_ifname="eth1")
        root = fake_sysfs({"eth0": LAN, "eth1": ROUTER})
        _, _, url = self.resolve(root, options(eth0="192.168.1.20", eth1="192.168.80.2"))
        self.assertIn("192.168.80.2", url)

    def test_manual_connection_is_not_pinned(self):
        self.cfg.update(wifi_ifname="manual", ssid="X", password="y", ip="10.1.1.1")
        with mock.patch.object(dd, "SYSFS_NET", fake_sysfs({"eth0": LAN})):
            self.dm.pin_connection(self.cam)
        self.assertEqual(self.cfg["wifi_mac"], "")

    def test_choosing_a_connection_in_the_ui_pins_its_mac(self):
        root = fake_sysfs({"eth0": LAN, "eth1": ROUTER})
        with mock.patch.object(dd, "SYSFS_NET", root):
            r = self.loop.run_until_complete(self.dm.handle({"cmd": "update", "addr": ADDR, "wifi_ifname": "eth1"}))
        self.assertTrue(r.get("ok"))
        self.assertEqual(self.cfg["wifi_mac"], ROUTER)

    def test_a_client_cannot_set_the_mac_directly(self):
        with mock.patch.object(dd, "SYSFS_NET", fake_sysfs({"eth0": LAN})):
            self.loop.run_until_complete(self.dm.handle({"cmd": "update", "addr": ADDR, "wifi_mac": "de:ad:be:ef:00:01"}))
        self.assertNotEqual(self.cfg.get("wifi_mac"), "de:ad:be:ef:00:01")

    def test_public_shows_the_current_name_and_hides_the_mac(self):
        self.cfg.update(wifi_ifname="eth0", wifi_mac=ROUTER)
        with mock.patch.object(dd, "SYSFS_NET", fake_sysfs({"eth0": LAN, "eth1": ROUTER})):
            pub = self.cam.public()
        self.assertEqual(pub["wifi_ifname"], "eth1")
        self.assertNotIn("wifi_mac", pub)
        self.assertNotIn("password", pub)

    def test_the_mac_survives_saving_and_loading(self):
        self.cfg.update(wifi_mac=ROUTER)
        self.dm.save()
        dm2 = dd.Daemon(self.dm.state_dir)
        self.assertEqual(dm2.cameras[ADDR].cfg["wifi_mac"], ROUTER)


if __name__ == "__main__":
    unittest.main()
