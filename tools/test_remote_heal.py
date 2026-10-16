"""Tests für die Selbstheilung der öffentlichen Tailscale-Freigabe (install/pipbox-remote.py, Aufruf "heal").
Anlass (16. Oktober 2026): Nach einem Stromausfall blieb die Freigabe tot, obwohl Tailscale sie als eingeschaltet anzeigte, bis man sie aus- und wieder einschaltete.
Es wird nie eine Verbindung zu Tailscale oder ins Internet aufgebaut: alles Äußere ist ersetzt."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if not hasattr(os, "killpg"):
    pass
for _m in ("fcntl",):
    if _m not in sys.modules:
        try:
            __import__(_m)
        except ImportError:                                      # Windows (nur Entwicklungsrechner)
            sys.modules[_m] = mock.MagicMock()


def load():
    path = os.path.join(ROOT, "install", "pipbox-remote.py")
    loader = importlib.machinery.SourceFileLoader("pipbox_remote_heal_under_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Heal(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        m = self.m = load()
        m.RUN = os.path.join(self.dir, "run")
        m.HEAL_STATE = os.path.join(m.RUN, "heal.json")
        m.STATUS = os.path.join(m.RUN, "status.json")
        m.LOG = os.path.join(self.dir, "remote.log")
        m.INTENT_DIR = os.path.join(self.dir, "intent")
        m.INTENT = os.path.join(m.INTENT_DIR, "funnel-wanted")
        self.calls = []
        self.state = ("Running", True, "box.tail1234.ts.net")
        self.synced = True
        self.jumped = False
        self.funnel = True
        self.probe_result = True
        self.reapply_ok = True
        m.installed = lambda: True
        m.funnel_active = lambda cfg=None: self.funnel
        m.tailscale_state = lambda: self.state
        m.clock_synced = lambda: self.synced
        m.tailscaled_clock_jumped = lambda: self.jumped
        m.STATS_INTENT = os.path.join(m.INTENT_DIR, "stats-wanted")
        self.stats_funnel = True
        self.stats_probe = True
        m.stats_active = lambda cfg=None: self.stats_funnel
        m.probe = lambda dns, port=443, path=None: (self.calls.append(("probe", dns) if port == 443 else ("probe_stats", dns, port, path)), self.probe_result if port == 443 else self.stats_probe)[1]
        m.reapply_funnel = lambda: (self.calls.append(("reapply",)), self.reapply_ok)[1]
        m.reapply_stats = lambda: (self.calls.append(("reapply_stats",)), self.reapply_ok)[1]
        self.sub = mock.patch.object(m.subprocess, "run", lambda cmd, **kw: (self.calls.append(tuple(cmd)), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
        self.sub.start()
        self.addCleanup(self.sub.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        m.set_intent(True)

    def reapplied(self):
        return sum(1 for c in self.calls if c == ("reapply",))

    def test_nothing_happens_when_the_user_never_wanted_the_funnel(self):
        self.m.set_intent(False)
        self.funnel = False
        self.assertEqual(self.m.heal(1000), "Freigabe nicht gewollt")
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.m.INTENT))

    def test_a_funnel_from_an_older_version_counts_as_wanted(self):
        self.m.set_intent(False)                                  # kein Merkzettel, aber die Freigabe steht in der Konfiguration
        self.probe_result = True
        self.assertEqual(self.m.heal(1000), "ok")
        self.assertTrue(os.path.exists(self.m.INTENT))

    def test_the_intent_follows_funnel_on_and_off(self):
        self.m.set_intent(False)
        self.assertFalse(os.path.exists(self.m.INTENT))
        self.m.set_intent(True)
        self.assertTrue(os.path.exists(self.m.INTENT))
        self.assertEqual(oct(os.stat(self.m.INTENT).st_mode & 0o777)[-3:] if os.name != "nt" else "600", "600")
        self.m.set_intent(False)
        self.m.set_intent(False)                                  # zweimal löschen schadet nicht
        self.assertFalse(os.path.exists(self.m.INTENT))

    def test_waits_while_tailscale_is_not_running_or_offline(self):
        self.state = ("Starting", False, "")
        self.assertEqual(self.m.heal(1000), "Tailscale nicht verbunden")
        self.state = ("Running", False, "box.tail1234.ts.net")
        self.assertEqual(self.m.heal(1060), "Tailscale nicht verbunden")
        self.assertEqual(self.reapplied(), 0)

    def test_waits_for_the_clock(self):
        self.synced = False
        self.assertEqual(self.m.heal(1000), "Uhr noch nicht synchron")
        self.assertEqual(self.calls, [])

    def test_restarts_tailscaled_once_when_the_clock_jumped_after_its_start(self):
        self.jumped = True
        self.assertEqual(self.m.heal(1000), "tailscaled neu gestartet")
        self.assertIn(("systemctl", "restart", "tailscaled"), self.calls)
        self.calls.clear()
        self.assertNotEqual(self.m.heal(1060), "tailscaled neu gestartet")        # nur einmal je Start
        self.assertNotIn(("systemctl", "restart", "tailscaled"), self.calls)

    def test_after_the_start_a_reachable_funnel_is_left_alone(self):
        self.probe_result = True
        self.assertEqual(self.m.heal(1000), "ok")
        self.assertEqual(self.reapplied(), 0)
        self.assertEqual(self.m.heal(1060), "ok")                  # beim zweiten Durchlauf keine neue Prüfung (alle 10 Minuten)
        self.assertEqual(sum(1 for c in self.calls if c[0] == "probe"), 1)

    def test_after_the_start_an_unreachable_or_untestable_funnel_is_applied_again(self):
        self.probe_result = False
        self.assertEqual(self.m.heal(1000), "neu angewendet")
        self.assertEqual(self.reapplied(), 1)
        self.setUp()
        self.probe_result = None                                   # Name löst nur im Tailscale-Netz auf: nicht zu prüfen
        self.assertEqual(self.m.heal(1000), "neu angewendet")
        self.assertEqual(self.reapplied(), 1)

    def test_a_missing_funnel_in_the_configuration_is_set_again(self):
        self.funnel = False
        self.m.set_intent(True)
        self.assertEqual(self.m.heal(1000), "neu angewendet")
        self.assertEqual(self.reapplied(), 1)

    def test_after_a_long_outage_it_is_applied_again(self):
        self.assertEqual(self.m.heal(1000), "ok")                  # Start erledigt
        self.state = ("Running", False, "box.tail1234.ts.net")
        self.assertEqual(self.m.heal(1060), "Tailscale nicht verbunden")
        self.state = ("Running", True, "box.tail1234.ts.net")
        self.assertEqual(self.m.heal(1060 + 6 * 60), "neu angewendet")
        self.assertEqual(self.reapplied(), 1)

    def test_a_short_outage_is_not_enough(self):
        self.assertEqual(self.m.heal(1000), "ok")
        self.state = ("Running", False, "box.tail1234.ts.net")
        self.m.heal(1060)
        self.state = ("Running", True, "box.tail1234.ts.net")
        self.assertEqual(self.m.heal(1120), "ok")
        self.assertEqual(self.reapplied(), 0)

    def test_the_funnel_is_probed_every_ten_minutes_and_repaired(self):
        self.assertEqual(self.m.heal(1000), "ok")
        self.probe_result = False
        self.assertEqual(self.m.heal(1000 + 300), "ok")            # noch keine 10 Minuten
        self.assertEqual(self.m.heal(1000 + 601), "neu angewendet")
        self.assertEqual(self.reapplied(), 1)

    def test_cooldown_and_hourly_limit(self):
        self.probe_result = False
        times = []
        for k in range(0, 40):                                     # alle 100 Sekunden ein Durchlauf, gut eine Stunde lang, dann weiter
            now = 1000 + k * 100
            if self.m.heal(now) == "neu angewendet":
                times.append(now)
        self.assertEqual(times[:2], [1000, 1600])                  # die erste sofort, die nächste nach 10 Minuten (Prüfung und Pause)
        for t0 in times:
            self.assertLessEqual(len([t for t in times if t0 <= t < t0 + 3600]), self.m.HEAL_MAX_PER_HOUR)
        self.m.HEAL_COOLDOWN_S = 0
        self.m.HEAL_PROBE_EVERY_S = 0
        many = [self.m.heal(100000 + k) for k in range(20)]
        self.assertEqual(many.count("neu angewendet"), self.m.HEAL_MAX_PER_HOUR)
        self.assertIn("zu oft in der letzten Stunde", many)

    def test_a_failed_reapply_is_retried_later_and_keeps_the_intent(self):
        self.reapply_ok = False
        self.probe_result = False
        self.assertEqual(self.m.heal(1000), "Neuanwendung fehlgeschlagen")
        self.assertTrue(os.path.exists(self.m.INTENT))
        self.assertEqual(self.m.heal(1060), "zu früh nach der letzten Neuanwendung")
        self.reapply_ok = True
        self.assertEqual(self.m.heal(1000 + 11 * 60), "neu angewendet")

    def test_heal_never_touches_the_status_shown_in_the_interface(self):
        self.m.status(state="idle", message="Die Oberfläche ist öffentlich im Internet erreichbar.")
        self.probe_result = False
        self.m.heal(1000)
        import json
        s = json.load(open(self.m.STATUS))
        self.assertEqual(s["state"], "idle")
        self.assertIn("öffentlich", s["message"])


class HealStats(Heal):
    """Die öffentliche Statistik (Port 8443) hat ihren eigenen Merkzettel und wird unabhängig von der Oberfläche geprüft und neu angewendet."""

    def setUp(self):
        super().setUp()
        self.m.set_intent(False)                                    # nur die Statistik ist gewollt
        self.funnel = False
        self.m.set_intent(True, self.m.STATS_INTENT)

    def test_a_reachable_statistics_funnel_is_left_alone_and_checked_on_its_port(self):
        self.assertEqual(self.m.heal(1000), "ok")
        self.assertIn(("probe_stats", "box.tail1234.ts.net", 8443, "/health"), self.calls)
        self.assertNotIn(("reapply",), self.calls)
        self.assertNotIn(("reapply_stats",), self.calls)

    def test_an_unreachable_statistics_funnel_is_applied_again_without_touching_the_interface(self):
        self.stats_probe = False
        self.assertEqual(self.m.heal(1000), "neu angewendet")
        self.assertEqual(self.calls.count(("reapply_stats",)), 1)
        self.assertNotIn(("reapply",), self.calls)
        self.assertTrue(os.path.exists(self.m.STATS_INTENT))

    def test_a_missing_statistics_funnel_is_set_again(self):
        self.stats_funnel = False
        self.assertEqual(self.m.heal(1000), "neu angewendet")
        self.assertEqual(self.calls.count(("reapply_stats",)), 1)

    def test_both_are_independent(self):
        self.m.set_intent(True)                                     # Oberfläche öffentlich gewollt
        self.funnel = True
        self.probe_result = True
        self.stats_probe = False
        self.m.heal(1000)
        self.assertEqual(self.calls.count(("reapply_stats",)), 1)
        self.assertEqual(self.calls.count(("reapply",)), 0)        # die Oberfläche war erreichbar: unberührt
        st = json.load(open(self.m.HEAL_STATE))
        self.assertTrue(st["boot_done"])                           # Oberfläche: Zustand oben
        self.assertTrue(st["stats"]["fixes"])                       # Statistik: eigener Zustand

    def test_the_interface_funnel_wanted_but_broken_does_not_trigger_the_statistics_fix(self):
        self.m.set_intent(True)
        self.funnel = False                                         # Oberfläche gewollt, aber fehlt
        self.stats_probe = True
        self.m.heal(1000)
        self.assertEqual(self.calls.count(("reapply",)), 1)
        self.assertEqual(self.calls.count(("reapply_stats",)), 0)

    def test_nothing_wanted_nothing_happens(self):
        self.m.set_intent(False, self.m.STATS_INTENT)
        self.assertEqual(self.m.heal(1000), "Freigabe nicht gewollt")
        self.assertEqual(self.calls, [])


for _name in [n for n in dir(Heal) if n.startswith("test_")]:
    setattr(HealStats, _name, None)                                 # die Prüfungen der Oberfläche laufen nur in Heal selbst


class Commands(unittest.TestCase):
    """Die Befehle: Oberfläche und Statistik berühren sich nie (kein "reset", das alle Freigaben löschen würde)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        m = self.m = load()
        m.RUN = os.path.join(self.dir, "run")
        m.STATUS = os.path.join(m.RUN, "status.json")
        m.LOG = os.path.join(self.dir, "remote.log")
        m.INTENT_DIR = os.path.join(self.dir, "intent")
        m.INTENT = os.path.join(m.INTENT_DIR, "funnel-wanted")
        m.STATS_INTENT = os.path.join(m.INTENT_DIR, "stats-wanted")
        self.cmds = []
        m.ts = lambda *a, timeout=60: (self.cmds.append(["tailscale", *a]), subprocess.CompletedProcess(a, 0, "", ""))[1]
        self.stats = False
        self.ui = False
        m.funnel_active = lambda cfg=None: self.ui
        m.stats_active = lambda cfg=None: self.stats
        self.rc, self.out, self.to = 0, "", False

        def cap(cmd, timeout):
            self.cmds.append(cmd)
            return self.rc, self.out, self.to
        m.run_capture = cap
        sub = mock.patch.object(m.subprocess, "run", lambda cmd, **kw: (self.cmds.append(list(cmd)), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
        sub.start()
        self.addCleanup(sub.stop)

    def flat(self):
        return [" ".join(c) for c in self.cmds]

    def test_stats_on_exposes_only_the_statistics_server_on_8443(self):
        self.stats = True
        self.m.do_stats_on()
        cmd = next(c for c in self.flat() if "funnel" in c and "--bg" in c)
        self.assertIn("--https=8443", cmd)
        self.assertIn("http://127.0.0.1:8781", cmd)
        self.assertNotIn("8780", cmd)
        self.assertTrue(os.path.exists(self.m.STATS_INTENT))
        self.assertFalse(os.path.exists(self.m.INTENT))               # die Oberfläche wird nie mit freigegeben

    def test_stats_on_failing_keeps_no_intent_and_explains(self):
        self.rc, self.out = 1, "Funnel is not enabled on your tailnet. Visit https://login.tailscale.com/f/funnel?node=abc to enable"
        self.m.do_stats_on()                                          # needs_funnel: kein Fehler, aber Hinweis
        self.assertFalse(os.path.exists(self.m.STATS_INTENT))
        st = json.load(open(self.m.STATUS))
        self.assertEqual(st["state"], "needs_funnel")
        self.assertIn("login.tailscale.com", st["hint_url"])
        self.rc, self.out = 1, "boom"
        with self.assertRaises(RuntimeError):
            self.m.do_stats_on()

    def test_stats_off_removes_only_port_8443(self):
        self.stats = False
        self.m.set_intent(True, self.m.STATS_INTENT)
        self.m.do_stats_off()
        self.assertIn("tailscale funnel --https=8443 off", self.flat())
        self.assertFalse(any("reset" in c for c in self.flat()))
        self.assertFalse(os.path.exists(self.m.STATS_INTENT))

    def test_interface_funnel_off_and_serve_off_keep_the_statistics(self):
        self.m.set_intent(True)
        self.m.do_funnel_off()
        self.assertIn("tailscale funnel --https=443 off", self.flat())
        self.assertFalse(any("reset" in c or "8443" in c for c in self.flat()))
        self.cmds.clear()
        self.m.do_serve_off()
        self.assertFalse(any("reset" in c or "8443" in c for c in self.flat()))
        self.assertIn("tailscale serve --https=443 off", self.flat())

    def test_logout_forgets_both_intents(self):
        src = open(os.path.join(ROOT, "install", "pipbox-remote.py"), encoding="utf-8").read()
        block = src.split('elif mode == "logout":', 1)[1].split("log(f", 1)[0]
        self.assertIn("set_intent(False)", block)
        self.assertIn("set_intent(False, STATS_INTENT)", block)

    def test_new_words_are_known(self):
        self.assertIn("stats_on", self.m.MODES)
        self.assertIn("stats_off", self.m.MODES)

    def test_active_checks_are_per_target(self):
        m = load()
        cfg = {"Web": {"b.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}}}, "b.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8781"}}}},
               "AllowFunnel": {"b.ts.net:8443": True}}
        self.assertTrue(m.stats_active(cfg))
        self.assertFalse(m.funnel_active(cfg))                        # nur die Statistik ist öffentlich
        cfg["AllowFunnel"] = {"b.ts.net:443": True}
        self.assertFalse(m.stats_active(cfg))
        self.assertTrue(m.funnel_active(cfg))


class ClockJump(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_parses_the_time_format_of_systemctl(self):
        self.assertEqual(self.m.systemd_time("Fri 2026-10-16 10:56:43 UTC"), 1792148203)
        self.assertIsNone(self.m.systemd_time(""))
        self.assertIsNone(self.m.systemd_time("n/a"))

    def fake(self, wall_text, mono_us, uptime, now):
        out = "ActiveEnterTimestamp=%s" % wall_text + chr(10) + "ActiveEnterTimestampMonotonic=%d" % mono_us + chr(10)
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, out, ""))
        opener = mock.mock_open(read_data="%s 1000.0" % uptime + chr(10))
        return mock.patch.object(self.m.subprocess, "run", run), mock.patch("builtins.open", opener), mock.patch.object(self.m.time, "time", lambda: now)

    def test_no_jump_when_both_time_stamps_agree(self):
        now = 1792148203 + 3000
        a, b, c = self.fake("Fri 2026-10-16 10:56:43 UTC", 37_701_876, 3037.7, now)       # Start bei 37,7 s Uptime, jetzt 3037,7 s
        with a, b, c:
            self.assertFalse(self.m.tailscaled_clock_jumped())

    def test_jump_when_the_clock_moved_forward_after_the_start(self):
        now = 1792148203 + 3000 + 3840                                                      # die Uhr sprang um gut eine Stunde nach vorn
        a, b, c = self.fake("Fri 2026-10-16 10:56:43 UTC", 37_701_876, 3037.7, now)
        with a, b, c:
            self.assertTrue(self.m.tailscaled_clock_jumped())

    def test_unreadable_values_mean_no_jump(self):
        a, b, c = self.fake("n/a", 0, 100.0, 1792148203)
        with a, b, c:
            self.assertFalse(self.m.tailscaled_clock_jumped())


class Probe(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def addrs(self, *ips):
        return [(socket.AF_INET if ":" not in ip else socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]

    def test_a_name_that_resolves_into_the_tailnet_cannot_be_probed(self):
        with mock.patch("socket.getaddrinfo", return_value=self.addrs("100.101.102.103")):
            self.assertIsNone(self.m.probe("box.tail1234.ts.net"))
        with mock.patch("socket.getaddrinfo", return_value=self.addrs("fd7a:115c:a1e0::1234")):
            self.assertIsNone(self.m.probe("box.tail1234.ts.net"))

    def test_unresolvable_or_empty_names_cannot_be_probed(self):
        self.assertIsNone(self.m.probe(""))
        with mock.patch("socket.getaddrinfo", side_effect=OSError("kein DNS")):
            self.assertIsNone(self.m.probe("box.tail1234.ts.net"))

    def test_any_answer_of_the_server_counts_as_reachable(self):
        import urllib.error
        with mock.patch("socket.getaddrinfo", return_value=self.addrs("203.0.113.7")):
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 502, "bad gateway", {}, None)):
                self.assertTrue(self.m.probe("box.tail1234.ts.net"))
            ok = mock.MagicMock()
            ok.__enter__ = lambda s: s
            ok.__exit__ = lambda *a: False
            with mock.patch("urllib.request.urlopen", return_value=ok):
                self.assertTrue(self.m.probe("box.tail1234.ts.net"))

    def test_connection_errors_mean_unreachable(self):
        with mock.patch("socket.getaddrinfo", return_value=self.addrs("203.0.113.7")), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("timed out")), mock.patch("time.sleep"):
            self.assertFalse(self.m.probe("box.tail1234.ts.net"))


class Wiring(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def test_timer_and_service_run_the_heal_mode(self):
        self.assertIn("pipbox-remote.py heal", self.read("install", "pipbox-remote-heal.service"))
        self.assertIn("OnUnitActiveSec=", self.read("install", "pipbox-remote-heal.timer"))

    def test_tailscaled_waits_for_network_and_time(self):
        d = self.read("install", "tailscaled-pipbox-order.conf")
        self.assertIn("After=network-online.target time-sync.target", d)
        self.assertIn("Wants=network-online.target", d)
        self.assertNotIn("Requires=", d)                          # nur eine Reihenfolge, keine Voraussetzung

    def test_install_and_uninstall_know_the_new_files(self):
        sh = self.read("install", "install.sh")
        for name in ("pipbox-remote-heal.service", "pipbox-remote-heal.timer", "tailscaled.service.d/pipbox-order.conf"):
            self.assertGreaterEqual(sh.count(name), 2, name)           # installieren und entfernen
        self.assertIn("pipbox-remote-heal.timer", sh.split("enable --now", 1)[1].split("\n", 1)[0])

    def test_funnel_helper_keeps_the_intent_file_in_a_root_only_folder(self):
        src = self.read("install", "pipbox-remote.py")
        self.assertIn('INTENT_DIR = "/var/lib/pipbox-remote"', src)
        self.assertNotIn('INTENT = f"{STATE}', src)                  # nicht im Ordner des Benutzers pipbox


class Diagnosis(unittest.TestCase):
    def test_log_bundle_has_the_new_tailscale_sections(self):
        src = open(os.path.join(ROOT, "install", "pipbox-logs.py"), encoding="utf-8").read()
        for title in ("Tailscale: Start (", "Tailscale: Zustand (status --json", "Uhr, Zeitsynchronisation und Selbstheilung der Freigabe", "Journal pipbox-remote-heal"):
            self.assertIn(title, src)


if __name__ == "__main__":
    unittest.main()
