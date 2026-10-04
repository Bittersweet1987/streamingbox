"""Tests für den SSH-Schalter in der Karte "Entwickler" (Issue #18): Root-Helfer pipbox-ssh.py, Developer-Klasse und Endpunkte (server.py)."""
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402


def load_helper():
    spec = importlib.util.spec_from_file_location("pipbox_ssh", os.path.join(ROOT, "install", "pipbox-ssh.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = load_helper()


class FakeSystemctl:
    def __init__(self, unit=True, fail=False, stays=None):
        self.calls, self.unit, self.fail, self.stays = [], unit, fail, stays
        self.active = False

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "cat":
            return mock.Mock(returncode=0 if self.unit else 1, stdout="", stderr="")
        if args[0] in ("start", "stop"):
            if self.fail:
                return mock.Mock(returncode=1, stdout="", stderr="Job failed")
            self.active = self.stays if self.stays is not None else args[0] == "start"
            return mock.Mock(returncode=0, stdout="", stderr="")
        if args[0] == "is-active":
            return mock.Mock(returncode=0 if self.active else 3, stdout="active\n" if self.active else "inactive\n", stderr="")
        raise AssertionError(args)


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.req = os.path.join(self.d, "ssh-request")
        self.run = os.path.join(self.d, "run")
        p = mock.patch.multiple(H, REQ=self.req, RUN=self.run, STATUS=self.run + "/status.json")
        p.start()
        self.addCleanup(p.stop)

    def status(self):
        with open(self.run + "/status.json") as f:
            return json.load(f)

    def go(self, word, sc):
        with open(self.req, "w") as f:
            f.write(word)
        with mock.patch.object(H, "systemctl", sc):
            return H.main()

    def test_start_and_stop_run_fixed_commands_only(self):
        sc = FakeSystemctl()
        self.assertEqual(self.go("start\n", sc), 0)
        self.assertEqual(sc.calls, [("cat", "ssh.service"), ("start", "ssh"), ("is-active", "ssh")])
        self.assertEqual((self.status()["state"], self.status()["message"]), ("done", "SSH eingeschaltet"))
        sc = FakeSystemctl()
        sc.active = True
        self.assertEqual(self.go("stop\n", sc), 0)
        self.assertEqual(sc.calls[1], ("stop", "ssh"))
        self.assertEqual(self.status()["message"], "SSH ausgeschaltet")
        self.assertFalse(os.path.exists(self.req))                                  # Anforderung ist gelöscht

    def test_unknown_keywords_do_nothing(self):
        for word in ("", "restart", "enable", "disable", "start; reboot", "STOP", "start ssh", "../x", "checked"):
            sc = FakeSystemctl()
            self.assertEqual(self.go(word, sc), 1, word)
            self.assertEqual(sc.calls, [], word)
            self.assertEqual(self.status()["state"], "error")
            self.assertFalse(os.path.exists(self.req))

    def test_symlinked_request_is_refused(self):
        real = os.path.join(self.d, "real")
        open(real, "w").write("start\n")
        os.symlink(real, self.req)
        sc = FakeSystemctl()
        with mock.patch.object(H, "systemctl", sc):
            self.assertEqual(H.main(), 1)
        self.assertEqual(sc.calls, [])
        self.assertTrue(os.path.exists(real))

    def test_missing_service_failure_and_wrong_end_state(self):
        self.assertEqual(self.go("start", FakeSystemctl(unit=False)), 1)
        self.assertIn("nicht installiert", self.status()["message"])
        self.assertEqual(self.go("start", FakeSystemctl(fail=True)), 1)
        self.assertEqual(self.status()["state"], "error")
        self.assertEqual(self.go("start", FakeSystemctl(stays=False)), 1)             # startete nicht wirklich
        self.assertIn("nicht gestartet", self.status()["message"])

    def test_helper_never_touches_passwords_keys_or_configuration(self):
        src = open(os.path.join(ROOT, "install", "pipbox-ssh.py"), encoding="utf-8").read()
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("#", '"""')))
        for forbidden in ("passwd", "chpasswd", "sshd_config", "authorized_keys", "PermitRootLogin", "enable", "disable", "mask", "usermod"):
            self.assertNotIn(forbidden, code.replace('"""', ""), forbidden)                  # (die Passwort-Zeile wird nur gelesen und verglichen)
        self.assertNotIn('"w"', code.split("def password_state")[1].split("def systemctl")[0])    # die Prüfung schreibt nichts


class DeveloperTests(unittest.TestCase):
    def make(self, setup=None, config=None, helper=True, unit=True, active=False):
        d = tempfile.mkdtemp()
        bela = os.path.join(d, "belaUI")
        os.makedirs(bela)
        if setup is not None:
            json.dump(setup, open(os.path.join(bela, "setup.json"), "w"))
        if config is not None:
            json.dump(config, open(os.path.join(bela, "config.json"), "w"))
        dev = server.Developer(d, False, os.path.join(bela, "config.json"))
        dev.HELPER = os.path.join(d, "helper.path")
        dev.STATUS = os.path.join(d, "status.json")
        if helper:
            open(dev.HELPER, "w").close()
        server._TTL.clear()
        for p in (mock.patch.object(dev, "_has_unit", lambda: unit), mock.patch.object(dev, "_systemctl", lambda *a: (0, "active" if active else "inactive"))):
            p.start()
            self.addCleanup(p.stop)
        return dev, d

    def test_status_reads_user_and_whether_a_password_was_created_without_exposing_it(self):
        dev, d = self.make({"ssh_user": "user"}, {"ssh_pass": "geheimespasswort", "password_hash": "x"})
        st = dev.status()
        self.assertEqual((st["user"], st["password_created"], st["available"], st["helper_installed"]), ("user", True, True, True))
        self.assertNotIn("geheimespasswort", json.dumps(st))
        dev, d = self.make({"ssh_user": "user"}, {"password_hash": "x"})
        self.assertFalse(dev.status()["password_created"])
        dev, d = self.make({"ssh_user": "user"}, {"ssh_pass": ""})
        self.assertFalse(dev.status()["password_created"])

    def test_without_the_original_ui_nothing_is_known(self):
        dev, d = self.make()
        st = dev.status()
        self.assertIsNone(st["password_created"])
        self.assertEqual(st["user"], "")
        dev = server.Developer(tempfile.mkdtemp(), False, None)
        self.assertIsNone(dev.password_created())

    def test_odd_user_names_are_ignored(self):
        for bad in ("root; reboot", "a b", "", "../x", 5, None, "x" * 40, "Großbuchstabe"):
            dev, d = self.make({"ssh_user": bad}, {})
            self.assertEqual(dev.status()["user"], "", str(bad))

    def test_request_writes_keyword_only(self):
        dev, d = self.make({"ssh_user": "user"}, {"ssh_pass": "x"})
        dev.request("start", False)
        self.assertEqual(open(os.path.join(d, "ssh-request")).read(), "start\n")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "ssh-request")).st_mode), 0o600)
        dev.request("stop", False)
        self.assertEqual(open(os.path.join(d, "ssh-request")).read(), "stop\n")

    def test_unknown_actions_are_refused(self):
        dev, d = self.make({"ssh_user": "user"}, {"ssh_pass": "x"})
        for bad in ("restart", "enable", "", None, 5, "start; reboot", ["start"]):
            with self.assertRaises(ValueError, msg=str(bad)):
                dev.request(bad, True)
        self.assertFalse(os.path.exists(os.path.join(d, "ssh-request")))

    def test_start_without_a_created_password_needs_confirmation(self):
        for config in ({"password_hash": "x"}, None):
            dev, d = self.make({"ssh_user": "user"}, config)
            with self.assertRaises(ValueError) as e:
                dev.request("start", False)
            self.assertIn("Bestätigung", str(e.exception))
            self.assertFalse(os.path.exists(os.path.join(d, "ssh-request")))
            dev.request("start", True)
            self.assertTrue(os.path.exists(os.path.join(d, "ssh-request")))
        dev, d = self.make({"ssh_user": "user"}, {"password_hash": "x"})
        dev.request("stop", False)                                                  # Ausschalten braucht nie eine Bestätigung

    def test_helper_missing_or_no_ssh_service(self):
        dev, d = self.make({"ssh_user": "user"}, {}, helper=False)
        with self.assertRaises(ValueError):
            dev.request("stop", True)
        dev, d = self.make({"ssh_user": "user"}, {}, unit=False)
        self.assertFalse(dev.status()["available"])
        with self.assertRaises(ValueError):
            dev.request("stop", True)

    def test_second_request_while_working_is_refused(self):
        import time
        dev, d = self.make({"ssh_user": "user"}, {"ssh_pass": "x"})
        json.dump({"state": "working", "time": int(time.time())}, open(dev.STATUS, "w"))
        with self.assertRaises(ValueError):
            dev.request("stop", True)
        json.dump({"state": "working", "time": int(time.time()) - 120}, open(dev.STATUS, "w"))
        dev.request("stop", True)                                                   # hängt seit Minuten: neu versuchen

    def test_garbage_status_file(self):
        dev, d = self.make({"ssh_user": "user"}, {})
        open(dev.STATUS, "w").write("kein json")
        self.assertEqual(dev.status()["state"], "idle")

    def test_demo(self):
        dev = server.Developer(tempfile.mkdtemp(), True)
        self.assertFalse(dev.status()["active"])
        self.assertEqual(dev.password()["password"], "Demo-Passwort-1234")
        dev.request("start", False)
        self.assertTrue(dev.status()["active"])
        dev.request("stop", False)
        self.assertFalse(dev.status()["active"])


class PasswordTests(unittest.TestCase):
    def dev(self, setup=None, config=None, state=None):
        d = tempfile.mkdtemp()
        bela = os.path.join(d, "belaUI")
        os.makedirs(bela)
        if setup is not None:
            json.dump(setup, open(os.path.join(bela, "setup.json"), "w"))
        if config is not None:
            json.dump(config, open(os.path.join(bela, "config.json"), "w"))
        dev = server.Developer(d, False, os.path.join(bela, "config.json"))
        dev.STATUS = os.path.join(d, "status.json")
        dev.HELPER = os.path.join(d, "h")
        open(dev.HELPER, "w").close()
        if state is not None:
            json.dump({"state": "done", "message": "m", "time": 5, "password_state": state}, open(dev.STATUS, "w"))
        server._TTL.clear()
        for p in (mock.patch.object(dev, "_has_unit", lambda: True), mock.patch.object(dev, "_systemctl", lambda *a: (0, "inactive"))):
            p.start()
            self.addCleanup(p.stop)
        return dev

    def test_password_of_the_original_ui_is_shown_with_user_and_state(self):
        dev = self.dev({"ssh_user": "user"}, {"ssh_pass": "abcDEF123", "ssh_pass_hash": "x"}, "generated")
        self.assertEqual(dev.password(), {"user": "user", "password": "abcDEF123", "state": "generated"})
        dev = self.dev({"ssh_user": "user"}, {"ssh_pass": "abcDEF123"}, "own")
        self.assertEqual(dev.password()["state"], "own")
        dev = self.dev({"ssh_user": "user"}, {"ssh_pass": "abcDEF123"})
        self.assertIsNone(dev.password()["state"])                                   # noch nie geprüft

    def test_no_password_no_display(self):
        for config in ({}, {"ssh_pass": ""}, {"ssh_pass": 5}, None):
            dev = self.dev({"ssh_user": "user"}, config)
            with self.assertRaises(ValueError, msg=str(config)) as e:
                dev.password()
            self.assertIn("Original-Oberfläche", str(e.exception))

    def test_status_never_contains_the_password(self):
        dev = self.dev({"ssh_user": "user"}, {"ssh_pass": "abcDEF123", "ssh_pass_hash": "root:$6$xyz"}, "generated")
        self.assertNotIn("abcDEF123", json.dumps(dev.status()))
        self.assertNotIn("$6$xyz", json.dumps(dev.status()))
        self.assertEqual(dev.status()["password_state"], "generated")

    def test_odd_state_values_are_dropped(self):
        dev = self.dev({"ssh_user": "user"}, {"ssh_pass": "x"}, "böse")
        self.assertIsNone(dev.status()["password_state"])

    def test_check_needs_no_confirmation_and_no_password(self):
        dev = self.dev({"ssh_user": "user"}, {})
        dev.request("check", False)
        self.assertEqual(open(os.path.join(os.path.dirname(dev.STATUS), "ssh-request")).read(), "check\n")


class HelperPasswordCheck(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(self.d + "/bela")
        self.shadow = self.d + "/shadow"
        p = mock.patch.multiple(H, BELA_DIR=self.d + "/bela", SHADOW=self.shadow)
        p.start()
        self.addCleanup(p.stop)

    def write(self, setup, config, shadow):
        if setup is not None:
            json.dump(setup, open(self.d + "/bela/setup.json", "w"))
        if config is not None:
            json.dump(config, open(self.d + "/bela/config.json", "w"))
        if shadow is not None:
            open(self.shadow, "w").write(shadow)

    def test_generated_own_and_unknown(self):
        line = "user:$6$salt$hash:19000:0:99999:7:::"
        shadow = "root:*:19000:0:99999:7:::\n" + line + "\n"
        self.write({"ssh_user": "user"}, {"ssh_pass": "x", "ssh_pass_hash": line + "\n"}, shadow)
        self.assertEqual(H.password_state(), "generated")
        self.write(None, None, "root:*:1:::\nuser:$6$neu$anders:19001:0:99999:7:::\n")
        self.assertEqual(H.password_state(), "own")
        self.write(None, {"ssh_pass": "x"}, None)                                    # nichts zum Vergleichen
        self.assertEqual(H.password_state(), "unknown")
        self.write(None, {"ssh_pass": "x", "ssh_pass_hash": line}, "root:*:1:::\n")   # Benutzer fehlt in shadow
        self.assertEqual(H.password_state(), "unknown")
        os.remove(self.shadow)
        self.assertEqual(H.password_state(), "unknown")

    def test_odd_user_and_missing_password_are_unknown(self):
        self.write({"ssh_user": "root; reboot"}, {"ssh_pass": "x", "ssh_pass_hash": "y"}, "root:*:1:::\n")
        self.assertEqual(H.password_state(), "unknown")
        self.write({"ssh_user": "user"}, {"ssh_pass_hash": "y"}, "user:y\n")
        self.assertEqual(H.password_state(), "unknown")                              # kein erzeugtes Passwort

    def test_check_runs_no_systemctl_and_reports_the_state(self):
        d = self.d
        with mock.patch.multiple(H, REQ=d + "/req", RUN=d + "/run", STATUS=d + "/run/status.json"):
            open(d + "/req", "w").write("check\n")
            sc = FakeSystemctl()
            with mock.patch.object(H, "systemctl", sc), mock.patch.object(H, "password_state", lambda: "own"):
                self.assertEqual(H.main(), 0)
            self.assertEqual(sc.calls, [])
            st = json.load(open(d + "/run/status.json"))
            self.assertEqual((st["state"], st["password_state"]), ("done", "own"))
            self.assertNotIn("user:", json.dumps(st))


class Endpoints(unittest.TestCase):
    def handler(self, path, body=b"{}", authed=True):
        h = server.Handler.__new__(server.Handler)
        h.path, h.sent, h.hdrs = path, [], {}
        h.authed = lambda: authed
        h.send_response = lambda code, *a: h.sent.append(code)
        h.send_header = lambda k, v: h.hdrs.__setitem__(k, v)
        h.end_headers = lambda: None

        class W:
            data = b""

            def write(self, b):
                W.data += b
        h.wfile, h.out = W(), W
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = type("R", (), {"read": lambda self, n: body})()
        h.client_address = ("127.0.0.1", 1)
        return h

    def setUp(self):
        server.Handler.developer = server.Developer(tempfile.mkdtemp(), True)

    def test_login_is_required(self):
        for method, path in (("do_GET", "/api/developer"), ("do_POST", "/api/developer"), ("do_GET", "/api/developer/password")):
            h = self.handler(path, authed=False)
            getattr(h, method)()
            self.assertEqual(h.sent, [401], method)

    def test_get_and_post(self):
        h = self.handler("/api/developer")
        h.do_GET()
        self.assertEqual(h.sent, [200])
        self.assertFalse(json.loads(h.out.data)["active"])
        h = self.handler("/api/developer", json.dumps({"action": "start"}).encode())
        h.do_POST()
        self.assertEqual(h.sent, [200])
        self.assertTrue(json.loads(h.out.data)["active"])
        for bad in ({"action": "restart", "confirm": True}, {}, {"action": "enable"}):
            h = self.handler("/api/developer", json.dumps(bad).encode())
            h.do_POST()
            self.assertEqual(h.sent, [400], str(bad))
        server.Handler.developer = server.Developer(tempfile.mkdtemp(), False, None)
        server.Handler.developer.HELPER = os.path.join(tempfile.mkdtemp(), "x")
        h = self.handler("/api/developer", json.dumps({"action": "start"}).encode())
        h.do_POST()
        self.assertEqual(h.sent, [400])                                               # Helfer fehlt


class FilesAndPage(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def test_units_and_install(self):
        self.assertIn("PathExists=/var/lib/pipbox/ssh-request", self.read("install", "pipbox-ssh.path"))
        self.assertIn("Unit=pipbox-ssh.service", self.read("install", "pipbox-ssh.path"))
        self.assertIn("Type=oneshot", self.read("install", "pipbox-ssh.service"))
        inst = self.read("install", "install.sh")
        for needle in ('"$HERE/install/pipbox-ssh.py"', '"$HERE/install/pipbox-ssh.service"', '"$HERE/install/pipbox-ssh.path"'):
            self.assertIn(needle, inst)
        self.assertGreaterEqual(inst.count("pipbox-ssh.path"), 4)
        for line in inst.splitlines():                                              # install.sh schaltet SSH selbst nie ein oder aus
            if "systemctl" in line:
                self.assertFalse({"ssh", "ssh.service", "sshd", "sshd.service", "ssh.socket"} & set(line.split()), line)

    def test_page(self):
        page = self.read("web", "index.html")
        for needle in ('id="c_dev"', "Entwickler", 'id="dev_on"', 'id="dev_off"', 'id="dev_pw"', "/api/developer/password", "/api/developer", "Original-Oberfläche"):
            self.assertIn(needle, page)
        self.assertLess(page.index('id="c_logs"'), page.index('id="c_dev"'))             # "nach Protokolle"
        self.assertLess(page.index('id="c_dev"'), page.index('id="c_power"'))


if __name__ == "__main__":
    unittest.main()
