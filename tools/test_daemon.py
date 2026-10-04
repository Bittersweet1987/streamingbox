"""Tests für die Bluetooth-Sticks und -Adapter (dji.py): Erkennung über /sys, Hinweise, Zuordnung Stick und Adapter. Ohne Bluetooth."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import dji  # noqa: E402


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

    def bt_tree(self, usb_root, adapters):
        """Baut /sys/class/bluetooth nach: adapters = [(hciN, USB-Ordner unter usb_root oder None für einen eingebauten)].
        Wie auf der echten Box: der Adapter liegt unter <USB-Gerät>/<Ordner>:1.0/bluetooth/hciN, der eingebaute unter einem Plattformgerät."""
        cls = tempfile.mkdtemp()
        for hci, usb_dir in adapters:
            if usb_dir:
                real = os.path.join(usb_root, usb_dir, usb_dir + ":1.0", "bluetooth", hci)
            else:
                real = os.path.join(tempfile.mkdtemp(), "platform", "serial0", "bluetooth", hci)
            os.makedirs(real)
            os.symlink(real, os.path.join(cls, hci))
        return cls

    def test_usb_id_comes_from_sysfs_not_from_bluez(self):
        usb = self.usb([("5-1.4", "0b05", "190E", "ASUS USB-BT500", ("e0", "01", "01"), "btusb")])
        bt = self.bt_tree(usb, [("hci0", "5-1.4"), ("hci1", None)])
        self.assertEqual(dji.usb_id_for_hci("hci0", bt), "0b05:190e")
        self.assertEqual(dji.usb_id_for_hci("hci1", bt), "")                    # eingebaut: kein USB-Gerät darüber
        self.assertEqual(dji.usb_id_for_hci("hci9", bt), "")                    # gibt es nicht

    def test_bluez_device_state_reads_connection_and_path(self):
        objs = {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": "AA"}},
                "/org/bluez/hci0/dev_58_B8_58_F0_4F_E2": {"org.bluez.Device1": {"Address": "58:B8:58:F0:4F:E2", "Connected": True, "ServicesResolved": False, "Name": "OsmoAction4-4FE1"}},
                "/org/bluez/hci0/dev_0C_9A": {"org.bluez.Device1": {"Address": "0C:9A:E6:19:5F:AF", "Connected": False}}}
        st = dji.bluez_device_state("58:b8:58:f0:4f:e2", objs)                     # Groß-/Kleinschreibung egal
        self.assertEqual(st, {"path": "/org/bluez/hci0/dev_58_B8_58_F0_4F_E2", "connected": True, "resolved": False, "name": "OsmoAction4-4FE1"})
        self.assertFalse(dji.bluez_device_state("0C:9A:E6:19:5F:AF", objs)["connected"])
        self.assertIsNone(dji.bluez_device_state("11:22:33:44:55:66", objs))        # BlueZ kennt das Gerät nicht
        self.assertIsNone(dji.bluez_device_state("58:B8:58:F0:4F:E2", {}))          # BlueZ nicht erreichbar

    def test_stick_names_come_from_sysfs_for_bluetooth_and_wlan(self):
        """Issue #6: Der Name des Sticks (Produkt, Hersteller, Kennung) wird angezeigt, so wie die Box ihn aus /sys liest (Werte von der echten Box)."""
        usb = self.usb([("5-1.4", "0b05", "190E", "ASUS USB-BT500", ("e0", "01", "01"), "btusb"),
                        ("2-1", "0bda", "c811", "802.11ac NIC", ("ff", "ff", "ff"), "rtl8821cu")])
        for d, vendor in (("5-1.4", "Realtek"), ("2-1", "Realtek")):
            with open(os.path.join(usb, d, "manufacturer"), "w") as f:
                f.write(vendor + "\n")
        bt = self.bt_tree(usb, [("hci0", "5-1.4"), ("hci1", None)])
        self.assertEqual(dji.usb_info_for_hci("hci0", bt), {"usb_id": "0b05:190e", "name": "ASUS USB-BT500", "vendor": "Realtek"})
        self.assertEqual(dji.usb_info_for_hci("hci1", bt), {})                         # eingebaut: nichts Bekanntes
        net = tempfile.mkdtemp()                                                       # /sys/class/net/wlan0/device -> USB-Schnittstelle
        iface = os.path.join(usb, "2-1", "2-1:1.0")
        os.makedirs(os.path.join(net, "wlan0"))
        os.symlink(iface, os.path.join(net, "wlan0", "device"))
        self.assertEqual(dji.netdev_info("wlan0", net), {"usb_id": "0bda:c811", "name": "802.11ac NIC", "vendor": "Realtek", "driver": "rtl8821cu"})
        builtin = os.path.join(tempfile.mkdtemp(), "platform", "fe2c0000.pcie")      # eingebaute Karte: kein USB-Gerät darüber, nur der Treiber
        os.makedirs(os.path.join(builtin, "drivers", "brcmfmac"))
        os.makedirs(os.path.join(net, "wlan1"))
        os.symlink(builtin, os.path.join(net, "wlan1", "device"))
        os.symlink(os.path.join(builtin, "drivers", "brcmfmac"), os.path.join(builtin, "driver"))
        self.assertEqual(dji.netdev_info("wlan1", net), {"usb_id": "", "name": "", "vendor": "", "driver": "brcmfmac"})
        self.assertEqual(dji.netdev_info("gibtesnicht", net), {"usb_id": "", "name": "", "vendor": "", "driver": ""})
        with mock.patch.object(dji, "SYSFS_USB", usb), mock.patch.object(dji, "SYSFS_BT", bt):
            st = dji.adapter_info(self.bluez())
        self.assertEqual((st["adapters"][0]["name"], st["adapters"][0]["vendor"], st["adapters"][0]["usb_id"]), ("ASUS USB-BT500", "Realtek", "0b05:190e"))

    BLUEZ_DEFAULT = "usb:v1D6Bp0246d0540"       # was BlueZ auf der echten Box meldet: die Standardkennung, nicht die des Sticks

    def bluez(self, modalias=None):
        return {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Modalias": modalias or self.BLUEZ_DEFAULT, "Address": "AA:BB",
                                                           "Powered": True}},
                "/org/bluez/hci0/dev_X": {"org.bluez.Device1": {"Address": "CC:DD"}}}

    def info(self, usb_devs, adapters, objs=None):
        root = self.usb(usb_devs)
        bt = self.bt_tree(root, adapters)
        with mock.patch.object(dji, "SYSFS_USB", root), mock.patch.object(dji, "SYSFS_BT", bt):
            return dji.adapter_info(objs if objs is not None else self.bluez())

    def test_status_carries_adapters_and_problems(self):
        st = self.info([("5-1.4", "0b05", "190e", "ASUS USB-BT500", ("e0", "01", "01"), "btusb"),
                        ("5-1.2", "33fa", "0010", "BARROT Bluetooth 5.4 Adapter", ("e0", "01", "01"), None)], [("hci0", "5-1.4")])
        self.assertEqual(st["adapters"][0]["usb_id"], "0b05:190e")
        self.assertEqual([p["id"] for p in st["adapter_problems"]], ["33fa:0010"])      # der laufende ASUS ist KEIN Problem

    def test_working_stick_gives_no_warning_even_with_the_bluez_default_id(self):
        st = self.info([("5-1.4", "0b05", "190e", "ASUS USB-BT500", ("e0", "01", "01"), "btusb")], [("hci0", "5-1.4")])
        self.assertEqual(st["adapter_problems"], [])

    def test_modalias_default_id_is_never_taken_for_the_stick(self):
        st = self.info([], [("hci0", None)])
        self.assertEqual(st["adapters"][0]["usb_id"], "")                         # eingebaut

    def test_unreadable_bluez_gives_no_adapters_but_still_names_a_missing_adapter(self):
        st = self.info([("5-1.4", "2357", "0604", "TP-Link UB500 Adapter", ("e0", "01", "01"), "btusb")], [], objs={})
        self.assertEqual(st["adapters"], [])
        self.assertIn("2357:0604", st["adapter_problems"][0]["id"])

    def test_driver_status_is_passed_on(self):
        import json
        d = tempfile.mkdtemp()
        f = os.path.join(d, "status.json")
        with open(f, "w") as fh:
            json.dump({"state": "ok", "message": "Treiber geladen"}, fh)
        with mock.patch.object(dji, "BTDRIVER_STATUS", f):
            st = self.info([], [])
        self.assertEqual(st["driver"], {"state": "ok", "message": "Treiber geladen"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
