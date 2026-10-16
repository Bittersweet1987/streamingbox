"""Tests für den Helfer des eingebauten Bluetooth-Chips der ROCK 5B Plus (install/pipbox-btchip.py). Nichts davon fasst das echte System an:
/sys, /proc und /lib/firmware sind nachgebaute Ordner, Schreibzugriffe auf sysfs werden von einer Nachbildung ausgeführt.
  python3 tools/test_btchip.py"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("pbbtchip", os.path.join(ROOT, "install", "pipbox-btchip.py"))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Fake:
    """Nachgebaute /sys-Landschaft mit dem Chip 13d3:3572 an btusb; schreibt wie sysfs (unbind, new_id, bind) und lässt nach dem Binden an rtk_btusb einen Adapter erscheinen."""

    def __init__(self, test, board="Radxa ROCK 5B Plus", chip=True, rtk=True, kernel="5.10.160-belabox", firmware=True):
        self.tmp = tempfile.mkdtemp(prefix="pbbtchip-")
        test.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        self.writes, self.runs = [], []
        self.running = False
        self.dmesg = []
        os.makedirs(f"{t}/sys/bus/usb/devices")
        os.makedirs(f"{t}/sys/class/bluetooth")
        os.makedirs(f"{t}/sys/bus/usb/drivers/btusb")
        if rtk:
            os.makedirs(f"{t}/sys/bus/usb/drivers/rtk_btusb")
        os.makedirs(f"{t}/fw/rtl_bt")
        os.makedirs(f"{t}/state/run")
        os.makedirs(f"{t}/state/persist")
        if firmware:
            for n in ("rtl8852bu_fw.bin", "rtl8852bu_config.bin"):
                open(f"{t}/fw/rtl_bt/{n}", "w").write("x")
        open(f"{t}/model", "w").write(board + "\0")
        self.devdir = f"{t}/sys/devices/usb1/1-1.3"
        if chip:
            os.makedirs(self.devdir)
            open(f"{self.devdir}/idVendor", "w").write("13d3\n")
            open(f"{self.devdir}/idProduct", "w").write("3572\n")
            os.symlink(self.devdir, f"{t}/sys/bus/usb/devices/1-1.3")
            for n in ("1-1.3:1.0", "1-1.3:1.1"):
                os.makedirs(f"{self.devdir}/{n}")
                os.symlink(f"{self.devdir}/{n}", f"{t}/sys/bus/usb/devices/{n}")
                os.symlink(f"{t}/sys/bus/usb/drivers/btusb", f"{self.devdir}/{n}/driver")
            self.make_hci("hci1")
        self.patches = []
        for name, value in dict(DT_MODEL=f"{t}/model", SYSFS_USB=f"{t}/sys/bus/usb/devices", SYSFS_DRV=f"{t}/sys/bus/usb/drivers",
                                SYSFS_BT=f"{t}/sys/class/bluetooth", FIRMWARE_DIR=f"{t}/fw", RUN=f"{t}/state/run",
                                STATUS=f"{t}/state/run/status.json", LOCK=f"{t}/state/lock", REQ=f"{t}/state/request",
                                PERSIST=f"{t}/state/persist", WAIT_ADAPTER=0.2).items():
            self.patch(test, bt, name, value)
        self.patch(test, bt, "release", lambda: kernel)
        self.patch(test, bt.os, "uname", lambda: type("U", (), {"release": kernel, "version": "#168 SMP"})())
        self.patch(test, bt, "write_sysfs", self.write_sysfs)
        self.patch(test, bt, "run", self.fake_run)
        self.patch(test, bt, "belacoder_running", lambda: self.running)
        self.patch(test, bt, "dmesg_lines", lambda: list(self.dmesg))

    def patch(self, test, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        test.addCleanup(setattr, obj, name, old)

    def make_hci(self, name):
        """Adapter am Chip: /sys/class/bluetooth/<name>/device -> Schnittstelle 1.0 (wie im echten sysfs)."""
        d = f"{self.tmp}/sys/class/bluetooth/{name}"
        os.makedirs(d, exist_ok=True)
        if not os.path.islink(f"{d}/device"):
            os.symlink(f"{self.devdir}/1-1.3:1.0", f"{d}/device")

    def drop_hci(self):
        shutil.rmtree(f"{self.tmp}/sys/class/bluetooth", ignore_errors=True)
        os.makedirs(f"{self.tmp}/sys/class/bluetooth")

    def set_driver(self, iface, drv):
        link = f"{self.devdir}/{iface}/driver"
        if os.path.islink(link):
            os.remove(link)
        if drv:
            os.symlink(f"{self.tmp}/sys/bus/usb/drivers/{drv}", link)

    def fake_run(self, cmd, timeout=60):
        self.runs.append(list(cmd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def write_sysfs(self, path, text):
        rel = os.path.relpath(path, f"{self.tmp}/sys/bus/usb/drivers")
        self.writes.append((rel, text))
        drv, action = rel.split("/")
        if not os.path.isdir(os.path.dirname(path)):
            raise OSError("kein Treiber")
        if action == "unbind":
            self.set_driver(text, "")
            self.drop_hci()
        elif action == "new_id":
            for i in ("1-1.3:1.0", "1-1.3:1.1"):
                if not os.path.islink(f"{self.devdir}/{i}/driver"):
                    self.set_driver(i, drv)
            if drv == "rtk_btusb":
                self.make_hci("hci0")
        elif action == "bind":
            if os.path.islink(f"{self.devdir}/{text}/driver"):
                raise OSError("schon gebunden")
            self.set_driver(text, drv)
            if drv == "rtk_btusb" and text.endswith(":1.0"):
                self.make_hci("hci0")
            if drv == "btusb" and text.endswith(":1.0"):
                self.make_hci("hci1")

    def status(self):
        return bt.read_json(bt.STATUS)

    def drivers(self):
        return {i: bt.driver_of(i) for i in ("1-1.3:1.0", "1-1.3:1.1")}


class AppliesOnlyToTheRock5BPlus(unittest.TestCase):
    def test_other_board_does_nothing_at_all(self):
        f = Fake(self, board="Orange Pi 5 Plus")
        self.assertEqual(bt.do_auto(force=True), 0)
        self.assertEqual(f.writes, [])
        self.assertEqual(f.runs, [])
        self.assertFalse(os.path.exists(bt.STATUS))
        self.assertEqual(os.listdir(bt.FIRMWARE_DIR), ["rtl_bt"])

    def test_board_name_must_match_exactly(self):
        for name in ("Radxa ROCK 5B", "Radxa ROCK 5B Plus Dev", "radxa rock 5b plus", ""):
            f = Fake(self, board=name)
            bt.do_auto(force=True)
            self.assertEqual(f.writes, [], name)

    def test_rock_without_the_chip_does_nothing(self):
        f = Fake(self, chip=False)
        self.assertEqual(bt.do_auto(force=True), 0)
        self.assertEqual(f.writes, [])
        self.assertFalse(os.path.exists(bt.STATUS))

    def test_other_kernel_is_reported_not_touched(self):
        f = Fake(self, kernel="6.1.0")
        bt.do_auto()
        self.assertEqual(f.status()["state"], "unsupported")
        self.assertEqual(f.writes, [])
        self.assertEqual(f.drivers(), {"1-1.3:1.0": "btusb", "1-1.3:1.1": "btusb"})

    def test_a_different_usb_id_is_not_taken(self):
        f = Fake(self)
        open(f"{f.devdir}/idProduct", "w").write("3571\n")
        bt.do_auto(force=True)
        self.assertEqual(f.writes, [])


class Attach(unittest.TestCase):
    def test_chip_on_the_generic_driver_is_moved_to_the_vendor_driver(self):
        f = Fake(self)
        bt.do_auto()
        self.assertEqual(f.drivers(), {"1-1.3:1.0": "rtk_btusb", "1-1.3:1.1": "rtk_btusb"})
        self.assertEqual(f.status()["state"], "ok")
        self.assertIn("modprobe", f.runs[0][0])
        self.assertEqual([w for w in f.writes if w[0].endswith("unbind")], [("btusb/unbind", "1-1.3:1.0"), ("btusb/unbind", "1-1.3:1.1")])
        self.assertIn(("rtk_btusb/new_id", "13d3 3572"), f.writes)

    def test_firmware_links_are_created_pointing_at_rtl_bt(self):
        f = Fake(self)
        bt.do_auto()
        self.assertEqual(os.readlink(f"{f.tmp}/fw/rtl8852bu_fw"), "rtl_bt/rtl8852bu_fw.bin")
        self.assertEqual(os.readlink(f"{f.tmp}/fw/rtl8852bu_config"), "rtl_bt/rtl8852bu_config.bin")

    def test_already_attached_changes_nothing(self):
        f = Fake(self)
        bt.do_auto()
        f.writes.clear()
        f.runs.clear()
        bt.do_auto()
        self.assertEqual(f.writes, [])
        self.assertEqual(f.runs, [])
        self.assertEqual(f.status()["state"], "ok")

    def test_nothing_is_changed_while_a_transmission_runs(self):
        f = Fake(self)
        f.running = True
        bt.do_auto()
        self.assertEqual(f.status()["state"], "waiting")
        self.assertEqual(f.writes, [])
        self.assertEqual(f.drivers(), {"1-1.3:1.0": "btusb", "1-1.3:1.1": "btusb"})

    def test_an_already_attached_chip_is_left_alone_during_a_transmission(self):
        f = Fake(self)
        bt.do_auto()
        f.running = True
        f.writes.clear()
        bt.do_auto()
        self.assertEqual(f.status()["state"], "ok")
        self.assertEqual(f.writes, [])

    def test_real_file_at_the_link_path_is_not_overwritten(self):
        f = Fake(self)
        open(f"{f.tmp}/fw/rtl8852bu_fw", "w").write("eigene Datei")
        bt.do_auto()
        self.assertEqual(rd(f"{f.tmp}/fw/rtl8852bu_fw"), "eigene Datei")
        self.assertEqual(f.status()["state"], "ok")

    def test_foreign_link_makes_the_run_fail_cleanly_and_return_the_chip(self):
        f = Fake(self)
        os.symlink("woanders.bin", f"{f.tmp}/fw/rtl8852bu_fw")
        bt.do_auto()
        self.assertEqual(f.status()["state"], "failed")
        self.assertEqual(f.drivers(), {"1-1.3:1.0": "btusb", "1-1.3:1.1": "btusb"})


class Failure(unittest.TestCase):
    def test_missing_vendor_driver_returns_the_chip_to_btusb_and_remembers(self):
        f = Fake(self, rtk=False)
        bt.do_auto()
        st = f.status()
        self.assertEqual(st["state"], "failed")
        self.assertIn("Standardtreiber", st["message"])
        self.assertEqual(f.drivers(), {"1-1.3:1.0": "btusb", "1-1.3:1.1": "btusb"})
        self.assertTrue(os.path.exists(f"{f.tmp}/state/persist/failed.json"))

    def test_a_failed_attempt_is_not_repeated_without_the_button(self):
        f = Fake(self, rtk=False)
        bt.do_auto()
        f.writes.clear()
        f.runs.clear()
        bt.do_auto()
        self.assertEqual(f.writes, [])
        self.assertEqual(f.runs, [])

    def test_the_button_tries_again_after_a_failure(self):
        f = Fake(self, rtk=False)
        bt.do_auto()
        os.makedirs(f"{f.tmp}/sys/bus/usb/drivers/rtk_btusb")
        bt.do_auto(force=True)
        self.assertEqual(f.status()["state"], "ok")
        self.assertFalse(os.path.exists(f"{f.tmp}/state/persist/failed.json"))

    def test_missing_firmware_is_reported_and_nothing_is_switched(self):
        f = Fake(self, firmware=False)
        bt.do_auto()
        self.assertEqual(f.status()["state"], "failed")
        self.assertIn("Firmware", f.status()["message"])
        self.assertEqual([w for w in f.writes if "unbind" in w[0] and w[0].startswith("rtk")], [])
        self.assertEqual(f.drivers(), {"1-1.3:1.0": "btusb", "1-1.3:1.1": "btusb"})

    def test_firmware_error_in_the_kernel_log_rolls_back(self):
        f = Fake(self)
        real = bt.attach

        def attach_and_log(dev):
            real(dev)
            f.dmesg.append("rtk_btusb: load firmware failed!")
        f.patch(self, bt, "attach", attach_and_log)
        bt.do_auto()
        self.assertEqual(f.status()["state"], "failed")
        self.assertEqual(f.drivers(), {"1-1.3:1.0": "btusb", "1-1.3:1.1": "btusb"})

    def test_a_new_kernel_build_tries_again(self):
        f = Fake(self, rtk=False)
        bt.do_auto()
        os.makedirs(f"{f.tmp}/sys/bus/usb/drivers/rtk_btusb")
        f.patch(self, bt.os, "uname", lambda: type("U", (), {"release": "5.10.160-belabox", "version": "#169 SMP"})())
        bt.do_auto()
        self.assertEqual(f.status()["state"], "ok")


class Request(unittest.TestCase):
    def test_request_file_is_taken_once(self):
        f = Fake(self)
        open(bt.REQ, "w").write("install\n")
        self.assertTrue(bt.take_request())
        self.assertFalse(os.path.exists(bt.REQ))
        self.assertFalse(bt.take_request())

    def test_button_marks_the_status_as_manual(self):
        f = Fake(self)
        bt.do_auto(force=True)
        self.assertTrue(f.status()["manual"])
        bt.do_auto(force=False)
        self.assertFalse(f.status()["manual"])


class Uninstall(unittest.TestCase):
    def test_removes_only_our_links(self):
        f = Fake(self)
        bt.do_auto()
        os.remove(f"{f.tmp}/fw/rtl8852bu_config")
        open(f"{f.tmp}/fw/rtl8852bu_config", "w").write("eigene Datei")
        bt.do_uninstall()
        self.assertFalse(os.path.lexists(f"{f.tmp}/fw/rtl8852bu_fw"))
        self.assertEqual(rd(f"{f.tmp}/fw/rtl8852bu_config"), "eigene Datei")
        self.assertFalse(os.path.exists(bt.STATUS))


class Installation(unittest.TestCase):
    def test_units_rule_and_install_script(self):
        svc = rd(os.path.join(ROOT, "install", "pipbox-btchip.service"))
        self.assertIn("ExecStart=/usr/bin/python3 /opt/pipbox/pipbox-btchip.py auto", svc)
        self.assertIn("NoNewPrivileges=yes", svc)
        self.assertIn("ReadWritePaths=/run /var/lib/pipbox /var/lib/pipbox-btchip /lib/firmware", svc)
        self.assertFalse([l for l in svc.splitlines() if l.startswith("ProtectKernelTunables")])      # der Helfer schreibt nach /sys (unbind, new_id, bind)
        self.assertIn("WantedBy=multi-user.target", svc)
        path = rd(os.path.join(ROOT, "install", "pipbox-btchip.path"))
        self.assertIn("PathExists=/var/lib/pipbox/btchip-request", path)
        self.assertIn("Unit=pipbox-btchip.service", path)
        rule = rd(os.path.join(ROOT, "install", "81-pipbox-btchip.rules"))
        self.assertIn('ATTR{idVendor}=="13d3", ATTR{idProduct}=="3572"', rule)
        self.assertIn("pipbox-btchip.service", rule)
        inst = rd(os.path.join(ROOT, "install", "install.sh"))
        for part in ("pipbox-btchip.py", "pipbox-btchip.service", "pipbox-btchip.path", "81-pipbox-btchip.rules"):
            self.assertIn(part, inst)

    def test_helper_source_names_the_board_and_the_chip_only(self):
        src = rd(os.path.join(ROOT, "install", "pipbox-btchip.py"))
        self.assertIn('BOARD = "Radxa ROCK 5B Plus"', src)
        self.assertIn('USB_ID = "13d3:3572"', src)


if __name__ == "__main__":
    unittest.main()
