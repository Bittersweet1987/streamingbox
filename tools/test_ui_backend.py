"""Tests für vier Kameras (drittes kleines Bild), Tonauswahl, Sendeweg-Verteilung und WLAN-Anfragen (server.py)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

KEYS = ["cam-a", "cam-b", "cam-c", "cam-d"]
BASE = {"type": "pip", "main": "cam-a", "pip": "cam-b", "corner": 4, "size_pct": 25, "audio": "main",
        "pip2": "cam-c", "corner2": 2, "pip3": "cam-d", "corner3": 3}


def store():
    return server.PipelineStore(os.path.join(tempfile.mkdtemp(), "pipeline.json"))


class FourCameras(unittest.TestCase):
    def test_four_cameras_are_saved(self):
        s = store()
        s.set(dict(BASE, pip3_delay_ms=200), KEYS)
        self.assertEqual((s.cfg["pip3"], s.cfg["corner3"], s.cfg["pip3_delay_ms"]), ("cam-d", 3, 200))

    def test_third_picture_needs_second(self):
        s = store()
        s.set(dict(BASE, pip2=""), KEYS)
        self.assertEqual(s.cfg["pip3"], "")

    def test_duplicates_are_refused(self):
        for bad in (dict(BASE, pip3="cam-a"), dict(BASE, pip3="cam-c"), dict(BASE, pip3="nope"),
                    dict(BASE, corner3=4), dict(BASE, corner3=2)):
            with self.assertRaises(ValueError, msg=str(bad)):
                store().set(bad, KEYS)

    def test_build_has_one_mixer_and_three_pip_inputs(self):
        s = store()
        s.set(BASE, KEYS)
        t = s.build()
        self.assertEqual(t.count("pbpipmix"), 1)
        self.assertIn("slot2=1 corner2=2 slot3=2 corner3=3", t)
        self.assertEqual(t.count("pbpipsink"), 3)
        self.assertIn("pip3-queue=pip3q_v", t)

    def test_audio_from_each_camera_has_exactly_one_audio_path(self):
        for sel in ("main", "pip", "pip2", "pip3"):
            s = store()
            s.set(dict(BASE, audio=sel), KEYS)
            t = s.build()
            self.assertEqual(t.count("opusenc"), 1, sel)
            self.assertEqual(t.count("fakesink"), 3, sel)

    def test_audio_of_missing_picture_is_refused_or_falls_back(self):
        with self.assertRaises(ValueError):
            store().set(dict(BASE, pip3="", audio="pip3"), KEYS)
        t = store().build(dict(BASE, pip3="", audio="pip3"))   # kaputte Datei: Ton vom Hauptbild
        self.assertEqual(t.count("opusenc"), 1)


class FreePosition(unittest.TestCase):
    def test_free_positions_are_saved_and_built(self):
        s = store()
        s.set(dict(BASE, corner=5, x=100, y=900, corner2=5, x2=500, y2=0, corner3=3), KEYS)     # zwei freie Bilder dürfen sich überlappen
        self.assertEqual((s.cfg["corner"], s.cfg["x"], s.cfg["y"]), (5, 100, 900))
        t = s.build()
        self.assertIn("corner=5 x=100 y=900", t)
        self.assertIn("slot2=1 corner2=5 x2=500 y2=0", t)
        self.assertIn("slot3=2 corner3=3", t)
        self.assertNotIn("x3=", t)                          # Voreinstellung: keine freien Werte mitschicken

    def test_values_are_limited(self):
        s = store()
        s.set(dict(BASE, corner=5, x=-50, y=5000), KEYS)
        self.assertEqual((s.cfg["x"], s.cfg["y"]), (0, 1000))
        with self.assertRaises(ValueError):
            store().set(dict(BASE, corner=5, x="abc"), KEYS)

    def test_presets_still_must_differ(self):
        with self.assertRaises(ValueError):
            store().set(dict(BASE, corner=3, corner2=3), KEYS)

    def test_failover_keeps_positions_with_the_place(self):
        import pipbox_send as ps
        cfg = dict(BASE, corner=5, x=100, y=200, audio="main", main_delay_ms=0, pip_delay_ms=0, pip2_delay_ms=0, pip3_delay_ms=0)
        eff, used = ps.effective_cfg(cfg, {"cam-b", "cam-c", "cam-d"})      # Hauptbild fällt aus: Plätze bleiben
        self.assertEqual((eff["corner"], eff["x"], eff["y"]), (5, 100, 200))


class PowerRequests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = server.Power(self.dir, False, mock.Mock(_active=lambda: False))
        pa = mock.patch.object(self.p, "status", lambda: {"helper_installed": True, "sending": False})
        pa.start()
        self.addCleanup(pa.stop)

    def test_poweroff_and_reboot_write_the_keyword_only(self):
        for act in ("poweroff", "reboot"):
            self.p.request(act, True)
            f = os.path.join(self.dir, "power-request")
            self.assertEqual(open(f).read(), act + "\n")
            self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)
            os.remove(f)

    def test_refusals(self):
        for act, conf in (("rm -rf /", True), ("poweroff", False), ("poweroff", None), (None, True), ("shutdown", True)):
            with self.assertRaises(ValueError, msg=str((act, conf))):
                self.p.request(act, conf)
            self.assertFalse(os.path.exists(os.path.join(self.dir, "power-request")))


class OldPlugin(unittest.TestCase):
    def test_old_plugin_gets_two_mixers_and_no_third_picture(self):
        s = store()
        s.set(BASE, KEYS)
        with mock.patch.object(server, "plugin_multi", lambda: False):
            t = s.build()
        self.assertEqual(t.count("pbpipmix"), 2)
        self.assertIn("pbpipmix name=pipmix2 slot=1 corner=2", t)
        self.assertNotIn("slot3", t)
        self.assertEqual(t.count("pbpipsink"), 2)
        self.assertEqual(t.count("opusenc"), 1)


class Spread(unittest.TestCase):
    def setUp(self):
        self.s = server.SrtlaStore(os.path.join(tempfile.mkdtemp(), "srtla.json"))
        self.req = {"min_kbps": 300, "max_kbps": 12000, "latency_ms": 4000, "uplinks": ["eth0", "wlan0"]}

    def test_default_is_best(self):
        self.s.set_settings(self.req, ["eth0", "wlan0"])
        self.assertEqual(self.s.data["settings"]["spread"], "best")

    def test_all_and_invalid(self):
        self.s.set_settings(dict(self.req, spread="all"), ["eth0", "wlan0"])
        self.assertEqual(self.s.data["settings"]["spread"], "all")
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, spread="alles"), ["eth0", "wlan0"])


class StaleUplinks(unittest.TestCase):
    def setUp(self):
        self.s = server.SrtlaStore(os.path.join(tempfile.mkdtemp(), "srtla.json"))      # Standard: eth0 + eth1
        self.req = {"min_kbps": 300, "max_kbps": 12000, "latency_ms": 4000}

    def test_can_change_while_an_old_network_is_missing(self):
        # eth1 steht gespeichert, die Karte gibt es nicht mehr (anderes Gerät); eth2 und wlan0 sollen dazu
        self.s.set_settings(dict(self.req, uplinks=["eth0", "eth1", "eth2", "wlan0"]), ["eth0", "eth2", "wlan0"])
        self.assertEqual(self.s.data["settings"]["uplinks"], ["eth0", "eth1", "eth2", "wlan0"])

    def test_stale_network_can_be_removed(self):
        self.s.set_settings(dict(self.req, uplinks=["eth0", "eth2"]), ["eth0", "eth2"])
        self.assertEqual(self.s.data["settings"]["uplinks"], ["eth0", "eth2"])

    def test_new_unknown_network_is_refused(self):
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, uplinks=["eth0", "eth7"]), ["eth0"])

    def test_at_least_one_existing_network(self):
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, uplinks=["eth1"]), ["eth0"])          # nur ein fehlendes Netz
        with self.assertRaises(ValueError):
            self.s.set_settings(dict(self.req, uplinks=[]), ["eth0"])

    def test_only_uplinks_sent_keeps_other_settings(self):
        # Ein veralteter Stand der Seite darf Mindestbitrate usw. nicht zurücksetzen: beim Anhaken wird nur die Netzliste geschickt
        self.s.set_settings(dict(self.req, min_kbps=4000, uplinks=["eth0", "eth2"], spread="all"), ["eth0", "eth2", "wlan0"])
        self.s.set_settings({"uplinks": ["eth2", "wlan0"]}, ["eth0", "eth2", "wlan0"])
        st = self.s.data["settings"]
        self.assertEqual((st["min_kbps"], st["max_kbps"], st["latency_ms"], st["spread"]), (4000, 12000, 4000, "all"))
        self.assertEqual(st["uplinks"], ["eth2", "wlan0"])

    def test_names_are_checked(self):
        for bad in ("eth0; rm -rf /", "", "x" * 16, "ü"):
            with self.assertRaises(ValueError, msg=bad):
                self.s.set_settings(dict(self.req, uplinks=["eth0", bad]), ["eth0", bad])


class WifiRequests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.net = mock.Mock(iface="eth1")
        self.w = server.Wifi(self.dir, False, self.net)
        cards = [{"iface": "wlan0", "ip": "", "camera_net": False, "up": False, "ssid": "", "signal": None},
                 {"iface": "eth1", "ip": "", "camera_net": True, "up": True, "ssid": "", "signal": None}]
        self.st = {"helper_installed": True, "cards": cards, "state": "idle", "message": "", "scan": {}, "saved": []}
        p = mock.patch.object(self.w, "status", lambda: dict(self.st))
        p.start()
        self.addCleanup(p.stop)

    def req(self):
        return os.path.join(self.dir, "wifi-request")

    def test_connect_writes_private_request_file(self):
        self.w.request({"action": "connect", "iface": "wlan0", "ssid": "Hotspot", "password": "geheim1234"})
        self.assertEqual(stat.S_IMODE(os.stat(self.req()).st_mode), 0o600)
        d = json.load(open(self.req()))
        self.assertEqual((d["action"], d["iface"], d["ssid"]), ("connect", "wlan0", "Hotspot"))

    def test_refusals(self):
        for bad in ({"action": "rm -rf"}, {"action": "scan", "iface": "wlan9"}, {"action": "scan", "iface": "eth1"},
                    {"action": "connect", "iface": "wlan0", "ssid": ""}, {"action": "connect", "iface": "wlan0", "ssid": "x" * 33},
                    {"action": "connect", "iface": "wlan0", "ssid": "x", "password": "p" * 65},
                    {"action": "forget"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.w.request(bad)
            self.assertFalse(os.path.exists(self.req()))

    def test_no_helper_and_busy(self):
        self.st["helper_installed"] = False
        with self.assertRaises(ValueError):
            self.w.request({"action": "scan", "iface": "wlan0"})
        self.st.update(helper_installed=True, state="working", time=server.time.time())
        with self.assertRaises(ValueError):
            self.w.request({"action": "scan", "iface": "wlan0"})


class HelperChecks(unittest.TestCase):
    """Die Prüfungen des Root-Helfers (ohne nmcli)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbwifi", os.path.join(os.path.dirname(HERE), "install", "pipbox-wifi.py"))
        cls.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.h)

    def test_password_rules(self):
        self.h.check_password("")                       # offenes Netz
        self.h.check_password("abcdefgh")
        self.h.check_password("a" * 63)
        self.h.check_password("0123456789abcdef" * 4)   # 64 Hex-Zeichen (PSK)
        for bad in ("short", "g" * 64, "ümlaut-passwort", "tab\there1234"):
            with self.assertRaises(ValueError, msg=bad):
                self.h.check_password(bad)

    def test_ssid_rules(self):
        self.h.check_ssid("Mein Handy")
        self.h.check_ssid("ä" * 16)                     # 32 Byte
        for bad in ("", "x" * 33, "ä" * 17, "a\nb", None):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.h.check_ssid(bad)

    def test_terse_split_unescapes(self):
        self.assertEqual(self.h.split_terse(r"*:Mein\:WLAN:80:WPA2"), ["*", "Mein:WLAN", "80", "WPA2"])


class RootHelperHardening(unittest.TestCase):
    """Root-Helfer und Pipeline-Erzeugung: Eingaben aus dem Ordner des Benutzers pipbox dürfen nichts einschleusen."""

    def helper(self, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(os.path.dirname(HERE), "install", name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_read_req_refuses_symlinks_and_limits_size(self):
        d = tempfile.mkdtemp()
        real, link = os.path.join(d, "real"), os.path.join(d, "req")
        with open(real, "w") as f:
            f.write("check\n" + "x" * 10000)
        os.symlink(real, link)
        for name in ("pipbox-update", "pipbox-remote", "pipbox-swupdate", "pipbox-wifi", "pipbox-power"):
            m = self.helper(name)
            with self.assertRaises(OSError, msg=name):
                m.read_req(link)
            self.assertEqual(len(m.read_req(real, 100)), 100, name)
            self.assertTrue(m.read_req(real).startswith("check"), name)

    def test_read_req_refuses_non_regular_files(self):
        m = self.helper("pipbox-power")
        with self.assertRaises(OSError):
            m.read_req("/dev/null")

    def test_pipeline_numbers_cannot_inject_text(self):
        cfg = dict(BASE, corner="3 ! filesink location=/etc/x", size_pct="25 ! fakesink", x="1 ! y", main_delay_ms="9;x")
        text = server.PipelineStore(os.devnull).build({**server.PipelineStore.DEFAULT, **cfg})
        self.assertTrue(text)
        for bad in ("filesink", "/etc/x", ";", "25 !", "1 ! y"):
            self.assertNotIn(bad, text)
        self.assertIn("width-pct=25 ", text)                 # ungültige Zahl fällt auf den erlaubten Bereich zurück

    def test_safe_cfg_clamps(self):
        c = server.PipelineStore._safe_cfg({"type": "pip", "corner": 99, "size_pct": 1000, "x": -5, "pip_delay_ms": 99999})
        self.assertEqual((c["corner"], c["size_pct"], c["x"], c["pip_delay_ms"]), (len(server.PIP_CORNERS) - 1, 40, 0, 3000))

    def test_work_dir_replaced_when_not_ours(self):
        sys.path.insert(0, os.path.dirname(HERE))
        import pipbox_send as ps
        d = tempfile.mkdtemp()
        target = os.path.join(d, "target")
        os.mkdir(target)
        work = os.path.join(d, "work")
        os.symlink(target, work)
        with mock.patch.object(ps, "WORK", work):
            ps.ensure_work()
            self.assertFalse(os.path.islink(work))
            self.assertEqual(stat.S_IMODE(os.stat(work).st_mode), 0o700)
        self.assertTrue(os.path.isdir(target))               # das Ziel des Verweises bleibt unangetastet


class LogModeSwitch(unittest.TestCase):
    """Protokoll-Modus: Oberfläche legt nur ein festes Stichwort ab, der Root-Helfer stellt um."""

    def helper(self, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(os.path.dirname(HERE), "install", name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_request_writes_keyword_only(self):
        d = tempfile.mkdtemp()
        lm = server.LogMode(d, demo=False)
        with mock.patch.object(lm, "status", return_value={"mode": "ausfuehrlich", "helper_installed": True}):
            lm.request("sparsam")
            self.assertEqual(open(os.path.join(d, "logmode-request")).read(), "sparsam\n")
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "logmode-request")).st_mode), 0o600)
            for bad in ("", "aus", "sparsam; reboot", None, 5, "../x"):
                with self.assertRaises(ValueError, msg=str(bad)):
                    lm.request(bad)

    def test_request_needs_installed_helper(self):
        lm = server.LogMode(tempfile.mkdtemp(), demo=False)
        with mock.patch.object(lm, "status", return_value={"mode": "ausfuehrlich", "helper_installed": False}):
            with self.assertRaises(ValueError):
                lm.request("sparsam")

    def test_status_defaults_to_verbose_without_file(self):
        lm = server.LogMode(tempfile.mkdtemp(), demo=False)
        with mock.patch.object(server, "read", return_value=None):
            self.assertEqual(lm.status()["mode"], "ausfuehrlich")        # ältere Installation schreibt wie bisher dauerhaft
        with mock.patch.object(server, "read", return_value="sparsam\n"):
            self.assertEqual(lm.status()["mode"], "sparsam")
        with mock.patch.object(server, "read", return_value="irgendwas"):
            self.assertEqual(lm.status()["mode"], "ausfuehrlich")

    def test_demo_switches(self):
        lm = server.LogMode(tempfile.mkdtemp(), demo=True)
        lm.request("sparsam")
        self.assertEqual(lm.status()["mode"], "sparsam")

    def test_helper_apply_writes_config_and_restarts_journald(self):
        m = self.helper("pipbox-logmode")
        d = tempfile.mkdtemp()
        calls = []
        with mock.patch.multiple(m, CONF_DIR=d + "/etc", MODE_FILE=d + "/etc/logmode", JOURNAL_DIR=d + "/j",
                                 JOURNAL_CONF=d + "/j/pipbox-journal.conf", OLD_JOURNAL_CONF=d + "/j/pipbox-persistent.conf",
                                 RUN=d + "/run", JOURNAL_LOG_DIR=d + "/varlog"), \
                mock.patch.object(m.subprocess, "run", side_effect=lambda *a, **k: calls.append(a[0])):
            os.makedirs(d + "/j")
            open(d + "/j/pipbox-persistent.conf", "w").write("alt")
            m.apply("sparsam")
            self.assertEqual(open(d + "/etc/logmode").read(), "sparsam\n")
            self.assertIn("Storage=volatile", open(d + "/j/pipbox-journal.conf").read())
            self.assertFalse(os.path.exists(d + "/j/pipbox-persistent.conf"))
            self.assertIn(["systemctl", "restart", "systemd-journald"], calls)
            m.JOURNAL_LOG_DIR = d + "/varlog"
            m.apply("ausfuehrlich")
            self.assertEqual(open(d + "/etc/logmode").read(), "ausfuehrlich\n")
            self.assertIn("Storage=persistent", open(d + "/j/pipbox-journal.conf").read())
            self.assertIn("SystemMaxUse=30M", open(d + "/j/pipbox-journal.conf").read())
            self.assertTrue(os.path.isdir(d + "/varlog"))
            self.assertIn(["journalctl", "--flush"], calls)
            with self.assertRaises(ValueError):
                m.apply("alles")

    def test_helper_ignores_unknown_keyword(self):
        m = self.helper("pipbox-logmode")
        d = tempfile.mkdtemp()
        req = os.path.join(d, "logmode-request")
        open(req, "w").write("poweroff\n")
        with mock.patch.multiple(m, REQ=req, LOCK=d + "/lock"), mock.patch.object(m, "apply") as ap:
            self.assertEqual(m.main(["x"]), 1)
            ap.assert_not_called()
        self.assertFalse(os.path.exists(req))                 # Anforderung wird trotzdem gelöscht

    def test_health_log_follows_mode(self):
        h = self.helper("pipbox_health")
        with mock.patch.object(h, "rd", return_value="sparsam"):
            self.assertEqual(h.mode(), "sparsam")
        with mock.patch.object(h, "rd", return_value=""):
            self.assertEqual(h.mode(), "ausfuehrlich")         # ohne Datei wie bisher auf der Karte


class AutoStartTests(unittest.TestCase):
    """Automatisch live gehen nach dem Start der Box: einmal pro Start, nur mit sendender Kamera, abbrechbar."""

    class FakeSend:
        def __init__(self, can_start=True, reasons=None):
            self.can_start, self.reasons, self.active, self.calls = can_start, reasons or [], False, []

        def status(self):
            return {"active": self.active, "can_start": self.can_start and not self.active, "reasons": [] if self.can_start else self.reasons}

        def request(self, action, confirm):
            self.calls.append((action, confirm))
            self.active = True

    def make(self, send, enabled=True, boot_done=None, wait_s=1.0):
        d = tempfile.mkdtemp()
        if enabled or boot_done:
            with open(os.path.join(d, "autostart.json"), "w") as f:
                json.dump({"enabled": enabled, "boot": boot_done or ""}, f)
        a = server.AutoStart(d, send, wait_s=wait_s, poll_s=0.02)
        return a, d

    def run_it(self, a):
        with mock.patch.object(server.AutoStart, "boot_id", staticmethod(lambda: "boot1")):
            a.run()

    def test_starts_once_when_camera_is_there(self):
        s = self.FakeSend()
        a, d = self.make(s)
        self.run_it(a)
        self.assertEqual(s.calls, [("start", True)])
        self.assertEqual(a.status()["phase"], "gestartet")
        self.assertEqual(json.load(open(os.path.join(d, "autostart.json")))["boot"], "boot1")

    def test_waits_for_camera_then_starts(self):
        s = self.FakeSend(can_start=False, reasons=["Keine Kamera sendet gerade"])
        a, _ = self.make(s)
        threading.Timer(0.15, lambda: setattr(s, "can_start", True)).start()
        self.run_it(a)
        self.assertEqual(len(s.calls), 1)

    def test_gives_up_and_names_reason(self):
        s = self.FakeSend(can_start=False, reasons=["Keine Kamera sendet gerade"])
        a, _ = self.make(s, wait_s=0.2)
        self.run_it(a)
        self.assertEqual(s.calls, [])
        self.assertEqual(a.status()["phase"], "aufgegeben")
        self.assertIn("Keine Kamera", a.status()["message"])

    def test_not_twice_in_same_boot(self):
        s = self.FakeSend()
        a, _ = self.make(s, boot_done="boot1")
        self.run_it(a)
        self.assertEqual(s.calls, [])

    def test_new_boot_starts_again(self):
        s = self.FakeSend()
        a, _ = self.make(s, boot_done="boot0")
        self.run_it(a)
        self.assertEqual(len(s.calls), 1)

    def test_off_by_default_and_when_disabled(self):
        s = self.FakeSend()
        a, _ = self.make(s, enabled=False)
        self.run_it(a)
        self.assertEqual(s.calls, [])
        self.assertFalse(a.enabled())

    def test_manual_stop_cancels_waiting(self):
        s = self.FakeSend(can_start=False, reasons=["x"])
        a, d = self.make(s, wait_s=2.0)
        th = threading.Thread(target=self.run_it, args=(a,))
        th.start()
        time.sleep(0.15)
        with mock.patch.object(server.AutoStart, "boot_id", staticmethod(lambda: "boot1")):
            a.cancel("von Hand beendet")
        th.join(3)
        self.assertFalse(th.is_alive())
        self.assertEqual(s.calls, [])
        self.assertEqual(a.status()["phase"], "abgebrochen")
        self.assertEqual(json.load(open(os.path.join(d, "autostart.json")))["boot"], "boot1")

    def test_cancel_when_not_waiting_does_nothing(self):
        a, d = self.make(self.FakeSend(), enabled=False)
        a.cancel("egal")
        self.assertEqual(a.status()["phase"], "aus")
        self.assertFalse(os.path.exists(os.path.join(d, "autostart.json")))

    def test_set_enabled_validates_and_persists(self):
        a, d = self.make(self.FakeSend(), enabled=False)
        for bad in ("true", 1, None, "ja"):
            with self.assertRaises(ValueError, msg=str(bad)):
                a.set_enabled(bad)
        a.set_enabled(True)
        self.assertTrue(json.load(open(os.path.join(d, "autostart.json")))["enabled"])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "autostart.json")).st_mode), 0o600)
        self.assertTrue(server.AutoStart(d, self.FakeSend()).enabled())


class PendingSettings(unittest.TestCase):
    DATA = {"servers": [{"id": "a1", "name": "X", "host": "h.example", "port": 5000, "streamid": "geheim"}], "selected": "a1",
            "settings": {"min_kbps": 4000, "max_kbps": 12000, "latency_ms": 4000, "spread": "all", "uplinks": ["eth2"]}}

    def sig(self, **chg):
        d = json.loads(json.dumps(self.DATA))
        d["settings"].update(chg.get("settings", {}))
        if "server" in chg:
            d["servers"][0].update(chg["server"])
        return server.srtla_signature(d)

    def test_same_data_same_signature(self):
        self.assertEqual(self.sig(), self.sig())

    def test_uplinks_do_not_need_restart(self):
        self.assertEqual(self.sig(), self.sig(settings={"uplinks": ["eth2", "wlan0"]}))

    def test_each_restart_setting_changes_its_group_only(self):
        base = self.sig()
        for chg, grp in ((dict(settings={"min_kbps": 300}), "bitrate"), (dict(settings={"max_kbps": 9000}), "bitrate"),
                         (dict(settings={"latency_ms": 2000}), "latency"), (dict(settings={"spread": "best"}), "spread"),
                         (dict(server={"host": "x.example"}), "server"), (dict(server={"streamid": "anders"}), "server")):
            new = self.sig(**chg)
            self.assertEqual([k for k in base if base[k] != new[k]], [grp], str(chg))

    def test_signature_hides_streamid(self):
        self.assertNotIn("geheim", json.dumps(self.sig()))

    def test_status_reports_pending(self):
        d = tempfile.mkdtemp()
        srt = server.SrtlaStore(os.path.join(d, "srtla.json"))
        srt.data.update(json.loads(json.dumps(self.DATA)))
        sc = server.SendControl(d, srt, store(), mock.Mock(), demo=False)
        applied = self.sig()
        with mock.patch.object(sc, "_active", return_value=True), \
                mock.patch.object(sc, "_detail", return_value={"state": "running", "applied": applied}), \
                mock.patch.object(sc, "reasons", return_value=[]), mock.patch.object(server, "belacoder_running", return_value=False):
            self.assertEqual(sc.status()["pending"], [])
            srt.data["settings"]["min_kbps"] = 300
            srt.data["settings"]["spread"] = "best"
            self.assertEqual(sc.status()["pending"], ["Bitrate", "Verteilung"])
        with mock.patch.object(sc, "_active", return_value=True), \
                mock.patch.object(sc, "_detail", return_value={"state": "running"}), \
                mock.patch.object(sc, "reasons", return_value=[]), mock.patch.object(server, "belacoder_running", return_value=False):
            self.assertEqual(sc.status()["pending"], [])         # ältere Sendekette ohne Merkwerte: keine Aussage

    def test_picture_state_per_camera(self):
        d = tempfile.mkdtemp()
        srt = server.SrtlaStore(os.path.join(d, "srtla.json"))
        sc = server.SendControl(d, srt, store(), mock.Mock(), demo=False)
        fo = {"layout": ["a", "c"], "configured": ["a", "b", "c", "d"], "wait": {"b": 40}}
        with mock.patch.object(sc, "_active", return_value=True), mock.patch.object(sc, "_detail", return_value={"failover": fo}):
            self.assertEqual(sc.picture(), {"a": ("an", 0), "b": ("wartet", 40), "c": ("an", 0), "d": ("aus", 0)})
        with mock.patch.object(sc, "_active", return_value=False):
            self.assertIsNone(sc.picture())
        with mock.patch.object(sc, "_active", return_value=True), mock.patch.object(sc, "_detail", return_value={"state": "running"}):
            self.assertIsNone(sc.picture())              # keine Automatik: keine Aussage


class UplinkLights(unittest.TestCase):
    LINES = ("10:00:01 links: 10.0.0.2 srtt=45ms var=3 peak=60 guete=70 genutzt in_flight=4 pkts_5s=800\n"
             "10:00:01 links: 10.0.1.2 srtt=250ms var=200 peak=800 guete=1000 reserve in_flight=0 pkts_5s=0\n"
             "10:00:06 links: 10.0.0.2 srtt=40ms var=3 peak=60 guete=70 genutzt in_flight=4 pkts_5s=900\n"
             "10:00:06 links: 10.0.1.2 srtt=-1ms var=200 peak=800 guete=-1 reserve in_flight=0 pkts_5s=0\n")

    def run_states(self, selected, age=0, lines=None):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "srtla-links.txt")
        with open(path, "w") as f:
            f.write(self.LINES if lines is None else lines)
        t = time.time() - age
        os.utime(path, (t, t))
        ifs = [{"iface": "eth2", "ip": "10.0.0.2"}, {"iface": "wlan0", "ip": "10.0.1.2"}]
        with mock.patch.object(server, "LINKS_FILE", path), mock.patch.object(server, "iface_ips", lambda: ifs):
            return server.uplink_states(selected)

    def test_used_reserve_and_missing(self):
        r = self.run_states(["eth2", "wlan0", "eth0"])
        self.assertEqual(r["eth2"], {"state": "an", "srtt": 40})
        self.assertEqual(r["wlan0"], {"state": "reserve", "srtt": None})      # -1 ms = keine frische Messung
        self.assertEqual(r["eth0"], {"state": "aus", "srtt": None})          # ausgewählt, aber nicht verbunden

    def test_stale_file_means_no_lights(self):
        self.assertEqual(self.run_states(["eth2"], age=60), {})

    def test_missing_file_means_no_lights(self):
        with mock.patch.object(server, "LINKS_FILE", "/nonexistent/links.txt"):
            self.assertEqual(server.uplink_states(["eth2"]), {})


class SwapMainPip(unittest.TestCase):
    def store(self, cfg):
        d = tempfile.mkdtemp()
        st = server.PipelineStore(os.path.join(d, "pipeline.json"))
        st.cfg.update(cfg)
        return st

    def test_swaps_cameras_and_their_delays_but_not_the_places(self):
        st = self.store({"type": "pip", "main": "cam-a", "pip": "cam-b", "pip2": "cam-c", "corner": 3, "size_pct": 25,
                         "audio": "main", "main_delay_ms": 1500, "pip_delay_ms": 120, "pip2_delay_ms": 250})
        st.swap_main_pip()
        c = st.cfg
        self.assertEqual((c["main"], c["pip"], c["pip2"]), ("cam-b", "cam-a", "cam-c"))
        self.assertEqual((c["main_delay_ms"], c["pip_delay_ms"], c["pip2_delay_ms"]), (120, 1500, 250))
        self.assertEqual((c["corner"], c["size_pct"], c["audio"]), (3, 25, "main"))
        with open(st.path) as f:                     # gespeichert
            self.assertEqual(json.load(f)["main"], "cam-b")
        st.swap_main_pip()                           # zweimal tauschen = wie vorher
        self.assertEqual((st.cfg["main"], st.cfg["pip"], st.cfg["main_delay_ms"]), ("cam-a", "cam-b", 1500))

    def test_swap_with_a_chosen_small_picture(self):
        st = self.store({"type": "pip", "main": "cam-a", "pip": "cam-b", "pip2": "cam-c", "pip3": "cam-d", "corner2": 2,
                         "main_delay_ms": 1500, "pip_delay_ms": 120, "pip2_delay_ms": 250, "pip3_delay_ms": 300})
        st.swap_main_pip("cam-c")
        c = st.cfg
        self.assertEqual((c["main"], c["pip"], c["pip2"], c["pip3"]), ("cam-c", "cam-b", "cam-a", "cam-d"))
        self.assertEqual((c["main_delay_ms"], c["pip_delay_ms"], c["pip2_delay_ms"], c["pip3_delay_ms"]), (250, 120, 1500, 300))
        self.assertEqual(c["corner2"], 2)                                 # Ecke bleibt am Platz
        st.swap_main_pip("cam-d")
        self.assertEqual((st.cfg["main"], st.cfg["pip3"]), ("cam-d", "cam-c"))
        with self.assertRaises(ValueError):                               # Kamera, die nicht als kleines Bild im Bild ist
            st.swap_main_pip("cam-x")
        with self.assertRaises(ValueError):                               # die Hauptkamera selbst ist kein kleines Bild
            st.swap_main_pip(st.cfg["main"])

    def test_refuses_without_small_picture(self):
        for cfg in ({"type": "single", "main": "cam-a", "pip": ""}, {"type": "pip", "main": "cam-a", "pip": ""}):
            st = self.store(cfg)
            with self.assertRaises(ValueError):
                st.swap_main_pip()
            self.assertEqual(st.cfg["main"], "cam-a")


class SeamlessSwap(unittest.TestCase):
    """Tausch ohne Neustart: Pipeline-Text, Umschaltzeile, Verzögerungsreihenfolge und Übergabe an die laufende Sendekette."""
    CFG = dict(server.PipelineStore.DEFAULT, type="pip", main="cam-a", pip="cam-b", pip2="cam-c", pip3="cam-d", corner=4, corner2=2, corner3=3,
               main_delay_ms=100, pip_delay_ms=300, pip2_delay_ms=50, pip3_delay_ms=0, swap_cams=2)

    def build(self, **kw):
        return server.PipelineStore(os.devnull).build(dict(self.CFG, **kw))

    def test_off_by_default_and_with_old_plugin(self):
        self.assertNotIn("pbpipsel", self.build(swap_cams=0))
        with mock.patch.object(server, "plugin_swap", lambda: False):
            self.assertNotIn("pbpipsel", self.build())
        self.assertNotIn("pbpipsel", self.build(type="single"))

    def test_two_swappable_cameras_decode_six_times(self):
        t = self.build()
        self.assertEqual(t.count("mppvideodec"), 6)          # 2 groß + 4 klein
        self.assertEqual(t.count("tee name="), 2)
        self.assertEqual(t.count("pbpipsel"), 2)             # Bild und Ton
        self.assertEqual(t.count("opusenc"), 1)
        self.assertEqual(t.count("pbpipsink"), 4)
        for ring in range(4):
            self.assertEqual(t.count(f"pbpipsink slot={ring}"), 1)
        self.assertIn("vsel.sink_0", t)
        self.assertIn("vsel.sink_1", t)
        self.assertNotIn("vsel.sink_2", t)
        self.assertIn("follow-tag=true", t)
        self.assertIn("pbctl name=pbctl selector=vsel audio-selector=asel audio-pos=-1 cam0=vfq0:v,vsq0:s,aq0:a cam1=vfq1:v,vsq1:s,aq1:a cam2=vsq2:s cam3=vsq3:s", t)

    def test_four_swappable_cameras_decode_eight_times(self):
        t = self.build(swap_cams=4)
        self.assertEqual(t.count("mppvideodec"), 8)
        for i in range(4):
            self.assertIn(f"vsel.sink_{i}", t)
            self.assertIn(f"asel.sink_{i}", t)

    def test_group_is_limited_by_the_cameras_present(self):
        t = self.build(swap_cams=4, pip3="")
        self.assertEqual(t.count("tee name="), 3)
        self.assertEqual(self.build(swap_cams=4, pip2="", pip3="").count("tee name="), 2)

    def test_every_queue_named_for_the_control_exists(self):
        t = self.build(swap_cams=4)
        ctl = next(l for l in t.splitlines() if l.startswith("pbctl"))
        for part in ctl.split():
            if part.startswith("cam"):
                for item in part.split("=", 1)[1].split(","):
                    self.assertIn(f"name={item.split(':')[0]} ", t + " ")
        self.assertEqual(t.count("name=a_delay"), 1)
        self.assertEqual(t.count("name=v_delay"), 1)

    def test_delay_belongs_to_the_camera_in_all_its_queues(self):
        t = self.build()
        self.assertIn("name=vfq0 min-threshold-time=133000000", t)     # 100 ms + ein Bild
        self.assertIn("name=aq0 min-threshold-time=100000000", t)
        self.assertIn("name=vfq1 min-threshold-time=333000000", t)
        self.assertIn("queue name=vsq1 max-size-time=833000000 max-size-buffers=0 leaky=downstream min-threshold-time=333000000", t)

    def test_audio_follows_the_picture_only_for_swappable_cameras(self):
        t = self.build(audio="pip")                         # Ton vom ersten kleinen Bild: läuft über den Umschalter
        self.assertIn("audio-pos=0", t)
        self.assertIn("pbpipsel name=asel state=1", t)
        t = self.build(audio="pip2")                        # Kamera 3 ist nicht in der Gruppe: fester Ton, kein Umschalter
        self.assertNotIn("asel", t)
        self.assertEqual(t.count("opusenc"), 1)
        self.assertEqual(t.count("fakesink"), 3)
        t = self.build(audio="pip2", swap_cams=4)
        self.assertIn("audio-pos=1", t)
        self.assertIn("pbpipsel name=asel state=2", t)

    def test_plan_and_initial_state(self):
        plan = server.PipelineStore.swap_plan(self.CFG)
        self.assertEqual((plan["cams"], plan["group"], plan["line"]), (KEYS, 2, "0 1 2 3"))
        self.assertEqual(plan["state"], 0 | 1 << 4 | 2 << 8 | 3 << 12)
        plan = server.PipelineStore.swap_plan(dict(self.CFG, pip3="", swap_cams=4))
        self.assertEqual((plan["group"], plan["line"]), (3, "0 1 2 15"))
        self.assertIsNone(server.PipelineStore.swap_plan(dict(self.CFG, swap_cams=0)))
        self.assertIsNone(server.PipelineStore.swap_plan(dict(self.CFG, pip="")))

    def test_swap_line(self):
        line = server.PipelineStore.swap_line
        self.assertEqual(line(["cam-a", "cam-b", "cam-c", "cam-d"], KEYS, 2), "0 1 2 3")
        self.assertEqual(line(["cam-b", "cam-a", "cam-c", "cam-d"], KEYS, 2), "1 0 2 3")
        self.assertIsNone(line(["cam-c", "cam-b", "cam-a", "cam-d"], KEYS, 2))          # Kamera 3 ist nicht in der Gruppe
        self.assertEqual(line(["cam-c", "cam-b", "cam-a", "cam-d"], KEYS, 4), "2 1 0 3")
        self.assertEqual(line(["cam-b", "cam-a", "cam-c", ""], KEYS[:3], 2), "1 0 2 15")
        self.assertIsNone(line(["cam-b", "cam-a", "cam-c", "cam-d"], KEYS[:3], 2))      # andere Kameras als beim Aufbau
        self.assertIsNone(line(["cam-x", "cam-b", "cam-c", "cam-d"], KEYS, 2))
        self.assertIsNone(line(["cam-a", "cam-b"], ["cam-a"], 1))

    def test_delay_file_order_stays_with_the_cameras(self):
        cfg = dict(self.CFG)
        self.assertEqual(server.PipelineStore.delay_values(cfg), [100, 300, 50, 0])
        st = server.PipelineStore(os.path.join(tempfile.mkdtemp(), "p.json"))
        st.cfg.update(cfg)
        st.swap_main_pip()                                   # cfg: b ist Hauptbild und trägt seine 300 ms mit
        self.assertEqual((st.cfg["main"], st.cfg["main_delay_ms"], st.cfg["pip_delay_ms"]), ("cam-b", 300, 100))
        self.assertEqual(server.PipelineStore.delay_values(st.cfg, KEYS), [100, 300, 50, 0])   # Reihenfolge des Aufbaus
        self.assertEqual(server.PipelineStore.delay_values(st.cfg), [300, 100, 50, 0])         # ohne Aufbau: Reihenfolge der Einstellung
        self.assertEqual(server.PipelineStore.delay_values(dict(cfg, type="single")), [0, 0, 0, 0])

    def test_setting_is_validated(self):
        s = store()
        s.set(dict(BASE, swap_cams=4), KEYS)
        self.assertEqual(s.cfg["swap_cams"], 4)
        for bad in (3, 1, "x", -2):
            with self.assertRaises(ValueError):
                store().set(dict(BASE, swap_cams=bad), KEYS)
        s.set(dict(BASE), KEYS)
        self.assertEqual(s.cfg["swap_cams"], 0)
        self.assertEqual(server.PipelineStore._safe_cfg(dict(BASE, swap_cams=3))["swap_cams"], 0)

    def control(self, cams_state=None, swap=None):
        d = tempfile.mkdtemp()
        st = server.PipelineStore(os.path.join(d, "pipeline.json"))
        st.cfg.update(self.CFG)
        cams = mock.Mock()
        cams.listing.return_value = [{"key": k, "state": (cams_state or {}).get(k, "live")} for k in KEYS]
        sc = server.SendControl(d, mock.Mock(), st, cams)
        sc.SWAP_WAIT = 1.0
        sc._active = lambda: True
        sc._detail = lambda: {"swap": swap if swap is not None else {"cams": KEYS, "group": 2}, "failover": {"degraded": False}}
        return d, st, sc

    def answer(self, d, state_path, delay=0.2):
        """Spielt den Baustein: liest die Umschaltdatei und meldet den Zustand zurück."""
        def run():
            time.sleep(delay)
            with open(os.path.join(d, server.SWAP_SELECT)) as f:
                txt = f.read()
            with open(state_path, "w") as f:
                f.write(txt)
        th = threading.Thread(target=run)
        th.start()
        return th

    def test_live_swap_writes_the_line_and_waits_for_the_report(self):
        d, st, sc = self.control()
        state = os.path.join(d, "swap-state")
        with mock.patch.object(server, "SWAP_STATE", state):
            st.swap_main_pip()
            th = self.answer(d, state)
            self.assertTrue(sc.swap_live())
            th.join()
        with open(os.path.join(d, server.SWAP_SELECT)) as f:
            self.assertEqual(f.read().strip(), "1 0 2 3")

    def test_live_swap_without_report_falls_back_to_restart(self):
        d, st, sc = self.control()
        with mock.patch.object(server, "SWAP_STATE", os.path.join(d, "never")):
            st.swap_main_pip()
            self.assertFalse(sc.swap_live())

    def test_stale_report_is_not_taken_for_an_answer(self):
        d, st, sc = self.control()
        state = os.path.join(d, "swap-state")
        with open(state, "w") as f:
            f.write("1 0 2 3\n")
        os.utime(state, (time.time() - 60, time.time() - 60))
        with mock.patch.object(server, "SWAP_STATE", state):
            st.swap_main_pip()
            self.assertFalse(sc.swap_live())

    def test_live_swap_refused_when_it_cannot_work(self):
        for kw in ({"swap": {"cams": KEYS, "group": 2}, "cams_state": {"cam-b": "off"}},   # neue Hauptkamera sendet nicht
                   {"swap": {"cams": KEYS[:3], "group": 2}},                                # Anordnung weicht vom Aufbau ab
                   {"swap": None}):
            d, st, sc = self.control(**kw)
            if kw.get("swap") is None:
                sc._detail = lambda: {}
            st.swap_main_pip()
            self.assertFalse(sc.swap_live(), kw)
            self.assertFalse(os.path.exists(os.path.join(d, server.SWAP_SELECT)), kw)
        d, st, sc = self.control()
        st.swap_main_pip("cam-c")                                                            # Kamera 3 gehört nicht zur Gruppe
        self.assertFalse(sc.swap_live())


class AuthModes(unittest.TestCase):
    """Anmeldung: auf einer BELABOX gilt deren Passwort; ohne Passwort dort wartet die Oberfläche (kein Setup-Code)."""

    def bela(self, config=None):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "belaUI"))
        path = os.path.join(d, "belaUI", "config.json")
        if config is not None:
            with open(path, "w") as f:
                json.dump(config, f)
        return d, path

    def test_fresh_belabox_waits_for_its_password_and_has_no_setup_code(self):
        d, cfg = self.bela({"asrc": "x"})                               # belaUI ist da, aber ohne Passwort
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.configured, a.setup_code), ("belabox-wartet", False, None))
        self.assertFalse(os.path.exists(os.path.join(d, "state", "setup-code")))
        with self.assertRaises(ValueError) as e:
            a.login("irgendwas", "127.0.0.1")
        self.assertIn("BELABOX", str(e.exception))
        with self.assertRaises(ValueError) as e:
            a.set_password("abc", "ein-langes-passwort", "127.0.0.1")
        self.assertIn("BELABOX", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(d, "state", "auth.json")))     # nichts Eigenes angelegt

    def test_missing_config_file_of_an_installed_belaui_also_waits(self):
        d, cfg = self.bela(None)
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.setup_code), ("belabox-wartet", None))

    def test_page_switches_when_the_belabox_password_appears(self):
        d, cfg = self.bela({"asrc": "x"})
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual(a.mode, "belabox-wartet")
        with open(cfg, "w") as f:
            json.dump({"asrc": "x", "password_hash": "$2b$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz01234"}, f)
        self.assertEqual((a.mode, a.configured), ("belabox", True))

    def test_belabox_password_is_used_when_present(self):
        d, cfg = self.bela({"password_hash": "$2b$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz01234"})
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.configured, a.setup_code), ("belabox", True, None))
        with mock.patch.object(a, "bela_ok", lambda pw, h: pw == "richtig"):
            self.assertTrue(a.login("richtig", "127.0.0.1"))
            with self.assertRaises(ValueError):
                a.login("falsch", "127.0.0.1")

    def test_without_belaui_the_own_password_with_setup_code_still_works(self):
        d = tempfile.mkdtemp()
        a = server.Auth(os.path.join(d, "state"), os.path.join(d, "keine-belaui", "config.json"))
        self.assertEqual(a.mode, "own")
        self.assertTrue(a.setup_code)
        code = a.setup_code
        with self.assertRaises(ValueError):
            a.set_password("falsch", "ein-langes-passwort", "127.0.0.1")
        a.set_password(code, "ein-langes-passwort", "127.0.0.1")
        self.assertTrue(a.configured)
        self.assertFalse(os.path.exists(os.path.join(d, "state", "setup-code")))
        self.assertTrue(a.login("ein-langes-passwort", "127.0.0.1"))

    def test_own_password_from_an_earlier_version_stays_valid_on_a_belabox_without_password(self):
        d = tempfile.mkdtemp()
        a0 = server.Auth(os.path.join(d, "state"), None)                 # früher: eigenes Passwort gesetzt
        a0.set_password(a0.setup_code, "ein-langes-passwort", "127.0.0.1")
        bd, cfg = self.bela({"asrc": "x"})
        a = server.Auth(os.path.join(d, "state"), cfg)
        self.assertEqual((a.mode, a.configured), ("own", True))
        self.assertTrue(a.login("ein-langes-passwort", "127.0.0.1"))

    def test_stale_setup_code_file_is_removed_on_a_belabox(self):
        d, cfg = self.bela({"asrc": "x"})
        os.makedirs(os.path.join(d, "state"))
        with open(os.path.join(d, "state", "setup-code"), "w") as f:
            f.write("alt\n")
        server.Auth(os.path.join(d, "state"), cfg)
        self.assertFalse(os.path.exists(os.path.join(d, "state", "setup-code")))


class UpdateHelperRepair(unittest.TestCase):
    """System-Updates: ein unterbrochener Paketlauf (dpkg was interrupted) wird erkannt und vor dem Update abgeschlossen."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pbupdate_repair", os.path.join(os.path.dirname(HERE), "install", "pipbox-update.py"))
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.upd = os.path.join(self.d, "updates")
        os.makedirs(self.upd)
        self.status = {}
        patches = [mock.patch.object(self.m, "DPKG_UPDATES", self.upd), mock.patch.object(self.m, "log", lambda line: None),
                   mock.patch.object(self.m, "save_status", lambda **kw: self.status.update(kw)),
                   mock.patch.object(self.m, "belacoder_running", lambda: False),
                   mock.patch.object(self.m, "DPKG_LOCKS", (os.path.join(self.d, "lock"),))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def audit(self, out="", rc=0):
        return mock.patch.object(self.m.subprocess, "run", lambda *a, **k: mock.Mock(returncode=rc, stdout=out, stderr=""))

    def test_leftovers_in_updates_mean_interrupted(self):
        with self.audit(""):
            self.assertFalse(self.m.dpkg_interrupted())
            open(os.path.join(self.upd, "0001"), "w").close()
            self.assertTrue(self.m.dpkg_interrupted())

    def test_half_configured_packages_mean_interrupted(self):
        with self.audit("The following packages are only half configured, probably due to problems\n tailscale"):
            self.assertTrue(self.m.dpkg_interrupted())

    def test_busy_lock_is_detected(self):
        import fcntl
        self.assertFalse(self.m.dpkg_busy())
        fd = os.open(self.m.DPKG_LOCKS[0], os.O_RDWR | os.O_CREAT, 0o640)
        try:
            # eine Sperre eines anderen Prozesses: im selben Prozess würde lockf sie nur erneuern, darum in einem Kindprozess prüfen
            code = ("import fcntl,os,sys\nfd=os.open(sys.argv[1],os.O_RDWR)\n"
                    "try:\n fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n print('frei')\nexcept OSError:\n print('belegt')\n")
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = subprocess.run([sys.executable, "-c", code, self.m.DPKG_LOCKS[0]], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "belegt")
        finally:
            os.close(fd)

    def test_repair_runs_configure_then_fix_install(self):
        calls = []
        def fake_run(args, **kw):
            calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(self.m.subprocess, "run", fake_run), mock.patch.object(self.m, "run_apt", lambda a, p=None: (calls.append(["apt-get"] + a) or (0, ""))):
            self.assertTrue(self.m.repair_dpkg())
        self.assertEqual(calls[0][:3], ["dpkg", "--configure", "-a"])
        self.assertEqual(calls[1][:3], ["apt-get", "-f", "install"])

    def test_repair_fails_cleanly(self):
        with mock.patch.object(self.m.subprocess, "run", lambda *a, **k: mock.Mock(returncode=1, stdout="Fehler", stderr="")):
            self.assertFalse(self.m.repair_dpkg())
        with mock.patch.object(self.m, "dpkg_busy", lambda: True):
            self.assertFalse(self.m.repair_dpkg())                     # anderer Paketvorgang: nichts anfassen

    def test_friendly_messages(self):
        f = self.m.friendly_error
        self.assertIn("sudo dpkg --configure -a", f("E: dpkg was interrupted, you must manually run 'dpkg --configure -a'"))
        self.assertIn("anderer Paketvorgang", f("E: Could not get lock /var/lib/dpkg/lock-frontend"))
        self.assertEqual(f("E: irgendwas anderes"), "Update fehlgeschlagen: E: irgendwas anderes")

    def test_run_repairs_before_updating_and_reports_failure(self):
        open(os.path.join(self.upd, "0001"), "w").close()
        order = []
        with mock.patch.object(self.m.shutil, "disk_usage", lambda p: mock.Mock(free=10 * 2**30)), \
                mock.patch.object(self.m, "dpkg_interrupted", lambda: True), mock.patch.object(self.m, "dpkg_busy", lambda: False), \
                mock.patch.object(self.m, "repair_dpkg", lambda: order.append("repair") or False), \
                mock.patch.object(self.m, "run_apt", lambda a, p=None: order.append("apt") or (0, "")):
            self.m.do_run()
        self.assertEqual(order, ["repair"])                             # bei Misserfolg kein apt
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("sudo dpkg --configure -a", self.status["message"])

    def test_run_does_not_touch_a_running_package_process(self):
        with mock.patch.object(self.m.shutil, "disk_usage", lambda p: mock.Mock(free=10 * 2**30)), \
                mock.patch.object(self.m, "dpkg_interrupted", lambda: True), mock.patch.object(self.m, "dpkg_busy", lambda: True), \
                mock.patch.object(self.m, "repair_dpkg", lambda: self.fail("darf nicht reparieren")):
            self.m.do_run()
        self.assertEqual(self.status["state"], "failed")
        self.assertIn("anderer Paketvorgang", self.status["message"])


if __name__ == "__main__":
    unittest.main()
