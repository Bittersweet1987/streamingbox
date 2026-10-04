"""Tests für die Härtung der Sendekette: SIGPIPE-Verhalten von belacoder, Protokollzeilen beim Ende des Encoders und der Wächter
für die RTMP-Leerlaufgrenze (install/pipbox-nginx-guard.sh). Ohne Kameras, ohne Box; braucht nur sh."""
import contextlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pipbox_send as ps  # noqa: E402

CFG = {"type": "pip", "main": "cam-m", "pip": "cam-p", "pip2": "cam-q", "corner": 3, "corner2": 2, "size_pct": 25,
       "audio": "main", "auto_failover": True}
ALL = {"cam-m", "cam-p", "cam-q"}


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def wr(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sender():
    return ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"],
                     {"cfg": CFG, "layout": ("cam-m", "cam-p", "cam-q"), "auto": True})


class ExitText(unittest.TestCase):
    def test_signal_is_named(self):
        self.assertEqual(ps.exit_text(-13), "Code -13, Signal SIGPIPE")
        self.assertEqual(ps.exit_text(-9), "Code -9, Signal SIGKILL")

    def test_plain_codes_and_unknown(self):
        self.assertEqual(ps.exit_text(0), "Code 0")
        self.assertEqual(ps.exit_text(1), "Code 1")
        self.assertEqual(ps.exit_text(-999), "Code -999")
        self.assertEqual(ps.exit_text(None), "Code None")


@unittest.skipUnless(signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN, "Testprozess ignoriert SIGPIPE nicht (wie der Dienst es tut)")
class SigpipeInChildren(unittest.TestCase):
    """Der Dienst ignoriert SIGPIPE (Python-Standard). belacoder soll das erben, srtla_send bleibt beim Standard."""

    def run_child(self, name):
        s = sender()
        p = s.spawn(name, ["sh", "-c", "kill -PIPE $$; exit 0"])
        p.wait(timeout=10)
        p.stdout.close()
        return p.returncode

    def test_belacoder_survives_sigpipe(self):
        self.assertEqual(self.run_child("belacoder"), 0)

    def test_other_programs_keep_default(self):
        self.assertEqual(self.run_child("srtla_send"), -signal.SIGPIPE)

    def test_flag_passed_to_popen(self):
        for name, expect in (("belacoder", False), ("srtla_send", True)):
            with mock.patch.object(ps.subprocess, "Popen") as popen:
                popen.return_value.stdout = io.StringIO("")
                sender().spawn(name, ["x"])
            self.assertEqual(popen.call_args.kwargs["restore_signals"], expect, name)


class EndLogging(unittest.TestCase):
    def test_note_keeps_only_element_name(self):
        s = sender()
        s.note("gstreamer error from rtmpsrc1: Could not read rtmp://127.0.0.1:1935/publish/GEHEIM")
        self.assertEqual((s.last, s.last_src), ("Fehler im Bildaufbau (Kamera oder Encoder)", "rtmpsrc1"))
        self.assertNotIn("GEHEIM", repr(vars(s).get("last")) + s.last_src)
        s.note("Pipeline stall detected")
        self.assertEqual((s.last, s.last_src), ("Das Eingangsbild stockte, der Encoder wird neu gestartet", ""))

    def test_encoder_died_logs_camera_count_without_names(self):
        s = sender()
        s.stop_proc = lambda n: s.procs.__setitem__(n, None)
        s.spawn = lambda n, a, env=None: None
        buf = io.StringIO()
        with mock.patch.object(ps, "write_pipeline", lambda cfg: "pipeline-text"), \
                mock.patch.object(ps, "live_keys", lambda: {"cam-m", "cam-q"}), contextlib.redirect_stdout(buf):
            self.assertTrue(s.encoder_died({}))
        out = buf.getvalue()
        self.assertIn("Kameras laut RTMP-Server: 2 von 3 senden", out)
        self.assertNotIn("cam-m", out.split("\n")[0])

    def test_unreadable_statistics_log_nothing_and_do_not_switch(self):
        s = sender()
        buf = io.StringIO()
        with mock.patch.object(ps, "live_keys", lambda: None), contextlib.redirect_stdout(buf):
            self.assertFalse(s.encoder_died({}))
        self.assertEqual(buf.getvalue(), "")


class NginxGuard(unittest.TestCase):
    """Das Skript wird mit ersetzten Pfaden (Datei, /proc) und Attrappen für nginx und logger ausgeführt."""

    CONF = "application publish {\n  live on;\n  drop_idle_publisher %s;\n  idle_streams off;\n}\n"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.d]))
        self.conf = os.path.join(self.d, "99-belabox-rtmp.conf")
        self.proc = os.path.join(self.d, "proc")
        os.makedirs(os.path.join(self.proc, "1"))
        wr(os.path.join(self.proc, "1", "comm"), "systemd\n")
        self.calls = os.path.join(self.d, "calls")
        bindir = os.path.join(self.d, "bin")
        os.makedirs(bindir)
        self.nginx_t_ok = os.path.join(self.d, "nginx_t_ok")
        wr(self.nginx_t_ok, "")
        self.write_exe(os.path.join(bindir, "nginx"),
                       f'echo "nginx $*" >> "{self.calls}"\n[ "$1" = "-t" ] && [ ! -e "{self.nginx_t_ok}" ] && exit 1\nexit 0\n')
        self.write_exe(os.path.join(bindir, "logger"), "exit 0\n")
        src = rd(os.path.join(ROOT, "install", "pipbox-nginx-guard.sh"))
        src = src.replace("/etc/nginx/modules-available/99-belabox-rtmp.conf", self.conf).replace("/proc/[0-9]*", self.proc + "/[0-9]*")
        self.script = os.path.join(self.d, "guard.sh")
        wr(self.script, src)
        self.bindir = bindir

    @staticmethod
    def write_exe(path, body):
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def run_guard(self):
        env = dict(os.environ, PATH=self.bindir + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["sh", self.script], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)           # darf apt nie stören
        called = rd(self.calls).splitlines() if os.path.exists(self.calls) else []
        return r.stdout, called

    def write_conf(self, value, mode=0o664):
        wr(self.conf, self.CONF % value)
        os.chmod(self.conf, mode)

    def read(self, path):
        return rd(path)

    def test_changes_4s_to_15s_with_backup_and_reload(self):
        self.write_conf("4s")
        out, called = self.run_guard()
        self.assertIn("drop_idle_publisher 15s;", self.read(self.conf))
        self.assertNotIn("4s;", self.read(self.conf))
        self.assertEqual(self.read(self.conf + ".vor-pipbox"), self.CONF % "4s")
        self.assertEqual(called, ["nginx -t", "nginx -s reload"])
        self.assertEqual(stat.S_IMODE(os.stat(self.conf).st_mode), 0o664)
        self.assertFalse(os.path.exists(self.conf + ".pipbox-neu"))

    def test_other_directives_untouched(self):
        self.write_conf("4s")
        self.run_guard()
        self.assertEqual(self.read(self.conf), self.CONF % "15s")

    def test_already_changed_does_nothing(self):
        self.write_conf("15s")
        out, called = self.run_guard()
        self.assertEqual((called, out), ([], ""))
        self.assertFalse(os.path.exists(self.conf + ".vor-pipbox"))

    def test_other_value_is_not_touched(self):
        self.write_conf("30s")
        _, called = self.run_guard()
        self.assertEqual((self.read(self.conf), called), (self.CONF % "30s", []))

    def test_missing_file_is_fine(self):
        _, called = self.run_guard()
        self.assertEqual(called, [])

    def test_no_reload_while_sending(self):
        self.write_conf("4s")
        os.makedirs(os.path.join(self.proc, "4242"))
        wr(os.path.join(self.proc, "4242", "comm"), "belacoder\n")
        out, called = self.run_guard()
        self.assertIn("drop_idle_publisher 15s;", self.read(self.conf))
        self.assertEqual(called, ["nginx -t"])                 # geprüft, aber nicht neu geladen
        self.assertIn("nächsten Start", out)

    def test_nginx_rejects_config_is_rolled_back(self):
        self.write_conf("4s")
        os.unlink(self.nginx_t_ok)
        out, called = self.run_guard()
        self.assertEqual(self.read(self.conf), self.CONF % "4s")
        self.assertEqual(called, ["nginx -t"])
        self.assertIn("WARNUNG", out)

    def test_package_update_refreshes_the_backup(self):
        """Ein Paket-Update hat die Datei überschrieben (4 s, neuer Inhalt): die Sicherung zeigt wieder den Stand des Pakets."""
        self.write_conf("4s")
        wr(self.conf + ".vor-pipbox", "alter Stand\n")
        self.run_guard()
        self.assertEqual(self.read(self.conf + ".vor-pipbox"), self.CONF % "4s")
        self.assertEqual(self.read(self.conf), self.CONF % "15s")

    def test_second_run_is_a_no_op(self):
        self.write_conf("4s")
        self.run_guard()
        os.unlink(self.calls)
        out, called = self.run_guard()
        self.assertEqual((called, out), ([], ""))


class InstallScript(unittest.TestCase):
    def test_install_and_uninstall_wire_the_guard(self):
        s = rd(os.path.join(ROOT, "install", "install.sh"))
        self.assertIn('"$HERE/install/pipbox-nginx-guard.sh" /opt/pipbox/pipbox-nginx-guard.sh', s)
        self.assertIn("/etc/apt/apt.conf.d/99pipbox-nginx", s)
        self.assertEqual(s.count("rm -f /etc/apt/apt.conf.d/99pipbox-nginx"), 1)       # Deinstallation entfernt den Haken
        self.assertNotIn("sed -i '/drop_idle_publisher", s)                           # keine zweite Kopie der Logik

    def test_install_script_is_valid_shell(self):
        r = subprocess.run(["sh", "-n", os.path.join(ROOT, "install", "install.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cleanup_removes_only_the_development_test_cameras(self):
        s = rd(os.path.join(ROOT, "install", "install.sh"))
        code = s.split("python3 - <<'PY' || true\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cameras.json")
            cams = [{"id": "1", "name": "Action 4", "key": "dji-f04fe2", "role": "main"},
                    {"id": "2", "name": "Kamera tst-a", "key": "tst-a", "role": "extra"},
                    {"id": "3", "name": "Kamera tst-d", "key": "tst-d", "role": "extra"},
                    {"id": "4", "name": "Meine Kamera", "key": "tst-e", "role": "extra"},
                    {"id": "5", "name": "Test", "key": "test-x", "role": "extra"}]
            with open(path, "w") as f:
                json.dump(cams, f)
            os.chmod(path, 0o600)
            r = subprocess.run([sys.executable, "-c", code.replace("/var/lib/pipbox/cameras.json", path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("entfernt: 2", r.stdout)
            with open(path) as f:
                self.assertEqual([c["key"] for c in json.load(f)], ["dji-f04fe2", "tst-e", "test-x"])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            r = subprocess.run([sys.executable, "-c", code.replace("/var/lib/pipbox/cameras.json", path)], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""))                      # zweiter Lauf: nichts mehr zu tun
            with open(path, "w") as f:
                f.write("kein json")
            r = subprocess.run([sys.executable, "-c", code.replace("/var/lib/pipbox/cameras.json", path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)                                         # kaputte Datei: Hinweis, kein Abbruch
            self.assertIn("nicht bereinigt", r.stdout)

    def test_funnel_guard_units_are_installed_and_removed(self):
        s = rd(os.path.join(ROOT, "install", "install.sh"))
        self.assertIn('"$HERE/install/pipbox-funnel-guard.timer" /etc/systemd/system/pipbox-funnel-guard.timer', s)
        self.assertIn('"$HERE/install/pipbox-funnel-guard.service" /etc/systemd/system/pipbox-funnel-guard.service', s)
        self.assertIn("pipbox-logmode.path pipbox-funnel-guard.timer", s)                      # eingeschaltet
        self.assertIn("pipbox-funnel-guard.timer pipbox-send.service", s.split("uninstall)")[1])      # bei der Deinstallation ausgeschaltet
        self.assertIn("/etc/systemd/system/pipbox-funnel-guard.timer", s.split("uninstall)")[1])
        timer = rd(os.path.join(ROOT, "install", "pipbox-funnel-guard.timer"))
        self.assertIn("OnBootSec=", timer)
        self.assertIn("OnUnitActiveSec=5min", timer)
        service = rd(os.path.join(ROOT, "install", "pipbox-funnel-guard.service"))
        self.assertIn("pipbox-remote.py guard", service)

    def test_apt_hook_is_harmless_without_the_script(self):
        hook = rd(os.path.join(ROOT, "install", "99pipbox-nginx"))
        self.assertIn("DPkg::Post-Invoke", hook)
        cmd = hook.split('"')[1]
        r = subprocess.run(["sh", "-c", cmd.replace("/opt/pipbox/", "/nonexistent/")], capture_output=True)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
