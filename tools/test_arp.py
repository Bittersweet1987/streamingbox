"""Tests für den Adress-Wächter (install/pipbox-arp.py, 1.0.11): Nichts davon fasst das echte System an. `ip` ist eine Attrappe, /proc/sys/net/ipv4/conf und /run sind
nachgebaute Ordner.
  python3 tools/test_arp.py"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("pbarp", os.path.join(ROOT, "install", "pipbox-arp.py"))
arp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arp)

with open(os.path.join(ROOT, "install", "install.sh"), encoding="utf-8") as _f:
    INSTALL = _f.read()


def line(idx, name, cidr, extra=""):
    return f"{idx}: {name}    inet {cidr} brd 0.0.0.0 scope global {extra}{name}\\       valid_lft forever preferred_lft forever"


class Env:
    """Nachgebaute Umgebung: Karten mit Adressen, ihre conf-Ordner und der Merkzettel."""

    def __init__(self, test, cards, values=None):
        self.tmp = tempfile.mkdtemp(prefix="pbarp-")
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.conf, self.run = f"{self.tmp}/conf", f"{self.tmp}/run"
        os.makedirs(self.conf)
        self.cards = dict(cards)                      # Name -> "192.168.80.135/24" (oder Liste mit eigener Zeile)
        self.values = values or {}
        for name in self.cards:
            self.mk(name)
        self.lines = None                             # eigene `ip`-Ausgabe (statt aus cards)
        self.ip_fails = False
        self.patches = [mock.patch.object(arp, "CONF", self.conf), mock.patch.object(arp, "RUN", self.run),
                        mock.patch.object(arp, "STATE", f"{self.run}/state.json"), mock.patch.object(arp, "STATUS", f"{self.run}/status.json"),
                        mock.patch.object(arp, "LOCK", f"{self.tmp}/lock"), mock.patch.object(arp.subprocess, "run", self.fake_ip)]
        for p in self.patches:
            p.start()
            test.addCleanup(p.stop)

    def mk(self, name):
        os.makedirs(f"{self.conf}/{name}", exist_ok=True)
        for key in ("arp_ignore", "arp_announce"):
            with open(f"{self.conf}/{name}/{key}", "w") as f:
                f.write(f"{self.values.get((name, key), 0)}\n")

    def get(self, name, key):
        with open(f"{self.conf}/{name}/{key}") as f:
            return int(f.read())

    def put(self, name, key, v):
        with open(f"{self.conf}/{name}/{key}", "w") as f:
            f.write(f"{v}\n")

    def fake_ip(self, cmd, **kw):
        assert cmd[:2] == ["ip", "-4"], cmd
        if self.ip_fails:
            return subprocess.CompletedProcess(cmd, 1, "", "kaputt")
        if self.lines is not None:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(self.lines), "")
        out = [line(i + 2, n, c) for i, (n, c) in enumerate(self.cards.items())]
        return subprocess.CompletedProcess(cmd, 0, "\n".join(out), "")


class Detect(unittest.TestCase):
    def nets(self, cards):
        return arp.overlapping([(n, __import__("ipaddress").ip_network(c, strict=False)) for n, c in cards.items()])

    def test_two_cards_in_one_network_overlap(self):
        self.assertEqual(self.nets({"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"}), ["eth1", "wlan0"])

    def test_different_networks_do_not(self):
        self.assertEqual(self.nets({"eth0": "192.168.178.118/24", "wlan0": "192.168.80.135/24", "wlan1": "10.53.136.72/24"}), [])

    def test_a_wide_network_that_contains_another_overlaps(self):
        self.assertEqual(self.nets({"eth0": "192.168.0.5/16", "wlan0": "192.168.80.135/24"}), ["eth0", "wlan0"])

    def test_only_the_cards_that_overlap_are_named(self):
        self.assertEqual(self.nets({"eth0": "192.168.178.118/24", "eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"}), ["eth1", "wlan0"])

    def test_one_card_with_two_addresses_in_one_network_is_no_overlap(self):
        self.assertEqual(arp.overlapping([("eth0", __import__("ipaddress").ip_network("192.168.1.0/24")), ("eth0", __import__("ipaddress").ip_network("192.168.1.0/24"))]), [])


class Addresses(unittest.TestCase):
    def test_skips_loopback_tailscale_containers_and_point_to_point(self):
        e = Env(self, {})
        e.lines = [line(1, "lo", "127.0.0.1/8"), line(2, "eth0", "192.168.178.118/24"), line(3, "tailscale0", "100.83.183.25/32"),
                   line(4, "docker0", "172.17.0.1/16"), line(5, "wg0", "10.9.0.2/24"), line(6, "ppp0", "10.64.64.64/32"), line(7, "wlan0", "192.168.80.135/24"),
                   "8: usb0    inet 10.0.0.2 peer 10.0.0.1/32 scope global usb0"]
        self.assertEqual([n for n, _ in arp.addresses()], ["eth0", "wlan0"])

    def test_unreadable_ip_gives_none_and_changes_nothing(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        e.ip_fails = True
        self.assertIsNone(arp.addresses())
        arp.step()
        self.assertEqual(e.get("eth1", "arp_ignore"), 0)
        self.assertEqual(e.get("wlan0", "arp_announce"), 0)


class Apply(unittest.TestCase):
    def test_overlap_sets_the_values_only_on_the_two_cards(self):
        e = Env(self, {"eth0": "192.168.178.118/24", "eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        for n in ("eth1", "wlan0"):
            self.assertEqual((e.get(n, "arp_ignore"), e.get(n, "arp_announce")), (1, 2))
        self.assertEqual((e.get("eth0", "arp_ignore"), e.get("eth0", "arp_announce")), (0, 0))        # die dritte Karte bleibt unberührt
        st = json.load(open(f"{e.run}/state.json"))
        self.assertEqual(sorted(st), ["eth1", "wlan0"])
        self.assertEqual(json.load(open(f"{e.run}/status.json"))["overlap"], ["eth1", "wlan0"])

    def test_without_overlap_nothing_is_written(self):
        e = Env(self, {"eth0": "192.168.178.118/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        self.assertEqual(sorted(os.listdir(e.run)), ["state.json", "status.json"])
        self.assertEqual(json.load(open(f"{e.run}/state.json")), {})
        self.assertEqual(e.get("wlan0", "arp_ignore"), 0)

    def test_values_are_restored_when_the_overlap_ends(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        e.cards["wlan0"] = "10.1.2.3/24"                                  # die WLAN-Karte wechselt das Netz
        arp.step()
        for n in ("eth1", "wlan0"):
            self.assertEqual((e.get(n, "arp_ignore"), e.get(n, "arp_announce")), (0, 0))
        self.assertEqual(json.load(open(f"{e.run}/state.json")), {})

    def test_a_card_that_vanishes_is_forgotten(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        del e.cards["wlan0"]
        shutil.rmtree(f"{e.conf}/wlan0")
        arp.step()                                                          # keine Fehlermeldung, auch wenn der conf-Ordner weg ist
        self.assertEqual(e.get("eth1", "arp_ignore"), 0)
        self.assertEqual(json.load(open(f"{e.run}/state.json")), {})

    def test_a_setting_of_the_user_is_never_lowered_or_taken_back(self):
        # Der Nutzer hat arp_ignore=1 schon selbst gesetzt (zum Beispiel /etc/sysctl.d): wir schreiben nichts und setzen auch nichts zurück
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"}, {("eth1", "arp_ignore"): 1, ("eth1", "arp_announce"): 2,
                                                                                    ("wlan0", "arp_ignore"): 1, ("wlan0", "arp_announce"): 2})
        arp.step()
        self.assertEqual(json.load(open(f"{e.run}/state.json")), {})
        e.cards["wlan0"] = "10.1.2.3/24"
        arp.step()
        for n in ("eth1", "wlan0"):
            self.assertEqual((e.get(n, "arp_ignore"), e.get(n, "arp_announce")), (1, 2))

    def test_a_higher_value_of_the_user_stays(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"}, {("eth1", "arp_ignore"): 2})
        arp.step()
        self.assertEqual(e.get("eth1", "arp_ignore"), 2)                   # nie herabsetzen
        self.assertEqual(e.get("eth1", "arp_announce"), 2)
        e.cards["wlan0"] = "10.1.2.3/24"
        arp.step()
        self.assertEqual(e.get("eth1", "arp_ignore"), 2)                   # bleibt: gehörte nicht uns
        self.assertEqual(e.get("eth1", "arp_announce"), 0)                 # zurück: den Wert hatten wir gesetzt

    def test_a_value_changed_by_someone_else_in_the_meantime_is_not_overwritten(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        e.put("wlan0", "arp_announce", 1)                                  # jemand anderes hat den Wert geändert
        e.cards["wlan0"] = "10.1.2.3/24"
        arp.step()
        self.assertEqual(e.get("wlan0", "arp_announce"), 1)
        self.assertEqual(e.get("wlan0", "arp_ignore"), 0)

    def test_repeated_runs_change_nothing_more(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        first = open(f"{e.run}/state.json").read()
        arp.step()
        arp.step()
        self.assertEqual(open(f"{e.run}/state.json").read(), first)

    def test_a_restart_of_the_service_keeps_the_state(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        arp.step(arp.load_state())                                          # neuer Prozess liest den Merkzettel
        e.cards["wlan0"] = "10.1.2.3/24"
        arp.step()                                                          # und kann noch zurücksetzen
        self.assertEqual(e.get("eth1", "arp_ignore"), 0)

    def test_a_new_card_joining_later_is_handled(self):
        e = Env(self, {"eth1": "192.168.80.197/24"})
        arp.step()
        self.assertEqual(e.get("eth1", "arp_ignore"), 0)
        e.cards["wlan0"] = "192.168.80.135/24"
        e.mk("wlan0")
        arp.step()
        self.assertEqual((e.get("eth1", "arp_ignore"), e.get("wlan0", "arp_ignore")), (1, 1))

    def test_unwritable_value_is_skipped_quietly(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        os.chmod(f"{e.conf}/eth1/arp_ignore", 0o400)
        if os.geteuid() == 0:
            self.skipTest("als Root nicht nachstellbar")
        arp.step()                                                          # darf nicht abstürzen
        self.assertEqual(e.get("eth1", "arp_ignore"), 0)


class Uninstall(unittest.TestCase):
    def test_uninstall_restores_only_our_values_and_removes_the_notes(self):
        e = Env(self, {"eth1": "192.168.80.197/24", "wlan0": "192.168.80.135/24"})
        arp.step()
        arp.uninstall()
        for n in ("eth1", "wlan0"):
            self.assertEqual((e.get(n, "arp_ignore"), e.get(n, "arp_announce")), (0, 0))
        self.assertFalse(os.path.exists(f"{e.run}/state.json"))

    def test_uninstall_without_notes_does_nothing(self):
        e = Env(self, {"eth1": "192.168.80.197/24"})
        arp.uninstall()
        self.assertEqual(e.get("eth1", "arp_ignore"), 0)


class Files(unittest.TestCase):
    def test_install_sh_installs_starts_and_removes_the_helper(self):
        for n in ("install/pipbox-arp.py", "install/pipbox-arp.service"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, n)), n)
        self.assertIn('install -m 755 "$HERE/install/pipbox-arp.py" /opt/pipbox/pipbox-arp.py', INSTALL)
        self.assertIn('install -m 644 "$HERE/install/pipbox-arp.service" /etc/systemd/system/pipbox-arp.service', INSTALL)
        self.assertIn("pipbox-btchip.service pipbox-arp.service", INSTALL)                      # enable --now
        self.assertIn("python3 /opt/pipbox/pipbox-arp.py uninstall", INSTALL)                   # beim Entfernen zurücksetzen
        self.assertLess(INSTALL.index("systemctl disable --now pipbox-arp.service 2>/dev/null"), INSTALL.index("pipbox-arp.py uninstall"))     # erst anhalten, dann zurücksetzen
        self.assertIn("/etc/systemd/system/pipbox-arp.service /opt/pipbox/pipbox-arp.py", INSTALL)    # rm -f
        self.assertNotIn("/etc/sysctl.d", INSTALL.replace("keine Datei", ""))                  # nichts Dauerhaftes in sysctl.d

    def test_service_is_small_and_locked_down(self):
        s = open(os.path.join(ROOT, "install", "pipbox-arp.service"), encoding="utf-8").read()
        self.assertIn("ExecStart=/usr/bin/python3 /opt/pipbox/pipbox-arp.py watch", s)
        self.assertIn("CapabilityBoundingSet=CAP_NET_ADMIN", s)
        self.assertIn("NoNewPrivileges=yes", s)
        self.assertIn("ProtectSystem=strict", s)
        self.assertNotIn("ProtectKernelTunables", s.replace("ohne ProtectKernelTunables", ""))

    def test_helper_does_not_touch_sysctl_d_or_dispatcher_files(self):
        code = open(os.path.join(ROOT, "install", "pipbox-arp.py"), encoding="utf-8").read()
        body = code.split('"""', 2)[2]                                                           # ohne die Beschreibung
        self.assertNotIn("/etc/", body)


if __name__ == "__main__":
    unittest.main(verbosity=1)
