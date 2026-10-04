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
        os.makedirs(self.d + "/bela")
        p = mock.patch.multiple(H, REQ=self.req, RUN=self.run, STATUS=self.run + "/status.json", BELA_DIR=self.d + "/bela", PASS_FILE=self.d + "/ssh-pass.json",
                                SHADOW=self.d + "/shadow", STATE=self.d)
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

    def test_helper_never_touches_keys_or_configuration(self):
        src = open(os.path.join(ROOT, "install", "pipbox-ssh.py"), encoding="utf-8").read()
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("#", '"""')))
        for forbidden in ("sshd_config", "authorized_keys", "PermitRootLogin", "enable", "disable", "mask", "usermod", "useradd", "userdel", "sudoers"):
            self.assertNotIn(forbidden, code.replace('"""', ""), forbidden)
        self.assertNotIn('"w"', code.split("def password_state")[1].split("def store_ours")[0])        # die Prüfung schreibt nichts

    def test_password_is_only_given_to_chpasswd_through_stdin(self):
        src = open(os.path.join(ROOT, "install", "pipbox-ssh.py"), encoding="utf-8").read()
        self.assertEqual(src.count('["chpasswd"]'), 1)
        self.assertIn('input=f"{user}:{password}\\n"', src)                                         # über stdin, nicht als Argument
        self.assertNotRegex(src, r'\["chpasswd",')                                                     # kein Argument hinter dem Programm
        for line in src.splitlines():
            if "print(" in line or "write_status(" in line:
                self.assertNotIn("password", line.replace("password_state", ""), line)           # nie in Protokoll oder Statusmeldung


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
        for bad in ("restart", "enable", "", None, 5, "start; reboot", ["start"], "RESET", "reset; reboot"):
            with self.assertRaises(ValueError, msg=str(bad)):
                dev.request(bad, True)
        self.assertFalse(os.path.exists(os.path.join(d, "ssh-request")))

    def test_start_never_asks_for_confirmation_but_reset_does(self):
        for config in ({"password_hash": "x"}, None, {"ssh_pass": "x"}):
            dev, d = self.make({"ssh_user": "user"}, config)
            dev.request("start", False)
            self.assertEqual(open(os.path.join(d, "ssh-request")).read(), "start\n")
            dev.request("stop", False)
        dev, d = self.make({"ssh_user": "user"}, {"password_hash": "x"})
        with self.assertRaises(ValueError) as e:
            dev.request("reset", False)
        self.assertIn("Bestätigung", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(d, "ssh-request")))
        dev.request("reset", True)
        self.assertEqual(open(os.path.join(d, "ssh-request")).read(), "reset\n")

    def test_reset_needs_a_known_ssh_user(self):
        dev, d = self.make(None, None)
        self.assertFalse(dev.status()["can_reset"])
        with self.assertRaises(ValueError):
            dev.request("reset", True)

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
            self.assertIn("zurücksetzen", str(e.exception))

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


class ResetTests(unittest.TestCase):
    """Passwort erzeugen (Knopf "Passwort zurücksetzen" und erstes Einschalten)."""

    LINE = "user:$6$salt$hash:19000:0:99999:7:::"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(self.d + "/bela")
        self.run = self.d + "/run"
        self.req = self.d + "/ssh-request"
        p = mock.patch.multiple(H, REQ=self.req, RUN=self.run, STATUS=self.run + "/status.json", BELA_DIR=self.d + "/bela", PASS_FILE=self.d + "/ssh-pass.json",
                                SHADOW=self.d + "/shadow", STATE=self.d)
        p.start()
        self.addCleanup(p.stop)
        json.dump({"ssh_user": "user"}, open(self.d + "/bela/setup.json", "w"))
        open(self.d + "/shadow", "w").write("root:*:19000:0:99999:7:::\n" + self.LINE + "\n")
        self.calls = []

    def chpasswd(self, rc=0, missing=False, new_line=None):
        def fake(cmd, **kw):
            self.calls.append((cmd, kw.get("input")))
            if missing:
                raise FileNotFoundError("chpasswd")
            if rc == 0 and new_line:                                    # das echte chpasswd schreibt eine neue Zeile in /etc/shadow
                open(self.d + "/shadow", "w").write("root:*:19000:0:99999:7:::\n" + new_line + "\n")
            return mock.Mock(returncode=rc, stdout="", stderr="Fehler mit Geheimnis")
        return mock.patch.object(H.subprocess, "run", fake)

    def go(self, word, sc=None):
        open(self.req, "w").write(word)
        with mock.patch.object(H, "systemctl", sc or FakeSystemctl()):
            return H.main()

    def status(self):
        return json.load(open(self.run + "/status.json"))

    NEW = "user:$6$neu$gesetzt:19001:0:99999:7:::"

    def test_reset_sets_a_random_password_through_stdin_and_remembers_it(self):
        with self.chpasswd(new_line=self.NEW):
            self.assertEqual(self.go("reset"), 0)
        cmd, given = self.calls[0]
        self.assertEqual(cmd, ["chpasswd"])                                                # kein Passwort als Argument
        user, _, pw = given.strip().partition(":")
        self.assertEqual(user, "user")
        self.assertRegex(pw, r"^[A-Za-z0-9]{20}$")
        saved = json.load(open(self.d + "/ssh-pass.json"))
        self.assertEqual((saved["user"], saved["password"], saved["hash"]), ("user", pw, self.NEW))
        self.assertEqual(stat.S_IMODE(os.stat(self.d + "/ssh-pass.json").st_mode), 0o600)
        st = self.status()
        self.assertEqual((st["state"], st["message"], st["password_state"]), ("done", "SSH-Passwort neu erzeugt", "generated"))
        self.assertNotIn(pw, json.dumps(st))                                              # nie in der Statusmeldung
        self.assertFalse(os.path.exists(self.req))

    def test_two_resets_give_different_passwords(self):
        pws = []
        for i in range(2):
            with self.chpasswd(new_line=self.NEW):
                self.go("reset")
            pws.append(json.load(open(self.d + "/ssh-pass.json"))["password"])
        self.assertNotEqual(pws[0], pws[1])

    def test_reset_failures_leave_no_file_and_no_secret_in_the_message(self):
        for kw in ({"rc": 1}, {"missing": True}):
            with self.chpasswd(**kw):
                self.assertEqual(self.go("reset"), 1)
            st = self.status()
            self.assertEqual(st["state"], "error")
            self.assertNotIn("Geheimnis", st["message"])
            self.assertFalse(os.path.exists(self.d + "/ssh-pass.json"))

    def test_reset_without_ssh_user_does_nothing(self):
        os.remove(self.d + "/bela/setup.json")
        with self.chpasswd():
            self.assertEqual(self.go("reset"), 1)
        self.assertEqual(self.calls, [])
        json.dump({"ssh_user": "root; reboot"}, open(self.d + "/bela/setup.json", "w"))
        with self.chpasswd():
            self.assertEqual(self.go("reset"), 1)
        self.assertEqual(self.calls, [])

    def test_first_start_creates_a_password_before_ssh_goes_up(self):
        sc = FakeSystemctl()
        with self.chpasswd(new_line=self.NEW):
            self.assertEqual(self.go("start", sc), 0)
        self.assertEqual(len(self.calls), 1)                                               # genau ein Passwort erzeugt
        self.assertTrue(os.path.exists(self.d + "/ssh-pass.json"))
        self.assertIn(("start", "ssh"), sc.calls)
        self.assertEqual(self.status()["message"], "SSH eingeschaltet")

    def test_start_does_not_touch_an_existing_password(self):
        json.dump({"ssh_pass": "vomOriginal"}, open(self.d + "/bela/config.json", "w"))          # die Original-Oberfläche hat eines erzeugt
        with self.chpasswd():
            self.assertEqual(self.go("start"), 0)
        self.assertEqual(self.calls, [])
        os.remove(self.d + "/bela/config.json")
        H.store_ours("user", "meinPasswort", self.LINE)                                          # oder IRL4YOU BOX
        with self.chpasswd():
            self.assertEqual(self.go("start"), 0)
        self.assertEqual(self.calls, [])

    def test_failed_password_creation_stops_the_start(self):
        sc = FakeSystemctl()
        with self.chpasswd(rc=1):
            self.assertEqual(self.go("start", sc), 1)
        self.assertNotIn(("start", "ssh"), sc.calls)                                        # SSH geht nicht mit dem Auslieferungspasswort auf
        self.assertIn("nicht eingeschaltet", self.status()["message"])

    def test_start_without_a_known_user_just_starts(self):
        os.remove(self.d + "/bela/setup.json")
        sc = FakeSystemctl()
        with self.chpasswd():
            self.assertEqual(self.go("start", sc), 0)
        self.assertEqual(self.calls, [])
        self.assertIn(("start", "ssh"), sc.calls)

    def test_stop_never_creates_a_password(self):
        sc = FakeSystemctl()
        sc.active = True
        with self.chpasswd():
            self.assertEqual(self.go("stop", sc), 0)
        self.assertEqual(self.calls, [])

    def test_state_of_a_password_made_here(self):
        H.store_ours("user", "meinPasswort", self.LINE)
        self.assertEqual(H.password_state(), "generated")
        open(self.d + "/shadow", "w").write("user:$6$ander$es:19002:0:99999:7:::\n")             # jemand hat es mit passwd geändert
        self.assertEqual(H.password_state(), "own")
        os.remove(self.d + "/shadow")
        self.assertEqual(H.password_state(), "unknown")

    def test_store_never_follows_a_planted_link(self):
        victim = self.d + "/wichtig"
        open(victim, "w").write("unberührt")
        os.symlink(victim, self.d + "/ssh-pass.json.tmp")
        H.store_ours("user", "pw", self.LINE)
        self.assertEqual(open(victim).read(), "unberührt")
        os.remove(self.d + "/ssh-pass.json")
        os.symlink(victim, self.d + "/ssh-pass.json")
        H.store_ours("user", "pw2", self.LINE)
        self.assertEqual(open(victim).read(), "unberührt")
        self.assertEqual(json.load(open(self.d + "/ssh-pass.json"))["password"], "pw2")

    def test_password_of_another_user_is_not_used(self):
        H.store_ours("andere", "fremd", self.LINE)
        self.assertIsNone(H.load_ours("user"))
        self.assertFalse(H.generated_password_exists("user"))


class OwnPasswordOnTheServer(unittest.TestCase):
    def make(self, setup, config, ours=None):
        d = tempfile.mkdtemp()
        os.makedirs(d + "/belaUI")
        if setup is not None:
            json.dump(setup, open(d + "/belaUI/setup.json", "w"))
        if config is not None:
            json.dump(config, open(d + "/belaUI/config.json", "w"))
        if ours is not None:
            json.dump(ours, open(d + "/ssh-pass.json", "w"))
        dev = server.Developer(d, False, d + "/belaUI/config.json")
        dev.STATUS, dev.HELPER = d + "/status.json", d + "/h"
        open(dev.HELPER, "w").close()
        server._TTL.clear()
        for p in (mock.patch.object(dev, "_has_unit", lambda: True), mock.patch.object(dev, "_systemctl", lambda *a: (0, "inactive"))):
            p.start()
            self.addCleanup(p.stop)
        return dev

    def test_own_password_comes_first_and_counts_as_created(self):
        dev = self.make({"ssh_user": "user"}, {"ssh_pass": "alt"}, {"user": "user", "password": "NeuVonUns123", "hash": "x"})
        self.assertEqual(dev.password()["password"], "NeuVonUns123")
        self.assertTrue(dev.status()["password_created"])
        self.assertNotIn("NeuVonUns123", json.dumps(dev.status()))
        dev = self.make({"ssh_user": "user"}, {}, {"user": "user", "password": "NurVonUns123", "hash": "x"})
        self.assertTrue(dev.status()["password_created"])                                    # auch ohne Passwort der Original-Oberfläche
        self.assertEqual(dev.password()["user"], "user")

    def test_own_password_of_another_user_or_broken_file_is_ignored(self):
        for ours in ({"user": "andere", "password": "x", "hash": "h"}, {"user": "user", "password": "", "hash": "h"}, {"user": "user"}, ["x"]):
            dev = self.make({"ssh_user": "user"}, {"ssh_pass": "vomOriginal"}, ours)
            self.assertEqual(dev.password()["password"], "vomOriginal", str(ours))

    def test_without_any_password_the_message_points_to_reset(self):
        dev = self.make({"ssh_user": "user"}, {})
        with self.assertRaises(ValueError) as e:
            dev.password()
        self.assertIn("zurücksetzen", str(e.exception))

    def test_demo_reset_changes_the_password(self):
        dev = server.Developer(tempfile.mkdtemp(), True)
        old = dev.password()["password"]
        with self.assertRaises(ValueError):
            dev.request("reset", False)
        dev.request("reset", True)
        self.assertNotEqual(dev.password()["password"], old)


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
        for needle in ('id="c_dev"', "Entwickler", 'id="dev_toggle"', 'id="dev_pw"', 'id="dev_reset"', "/api/developer/password", "/api/developer",
                       "Passwort ausblenden", "Passwort zurücksetzen"):
            self.assertIn(needle, page)
        self.assertIn(".pwshow{", page)                                                  # Passwort groß, Klick kopiert es
        self.assertIn('class="pwshow"', page)
        self.assertIn("function copyText", page)
        self.assertIn('closest(".pwshow")', page)
        self.assertLess(page.index('id="c_logs"'), page.index('id="c_dev"'))             # "nach Protokolle"
        self.assertLess(page.index('id="c_dev"'), page.index('id="c_power"'))

    def test_card_follows_the_feedback_of_the_issue(self):
        page = self.read("web", "index.html")
        html = page[page.index('id="c_dev"'):page.index('id="c_power"')]
        js = page[page.index("// ---- Entwickler"):page.index("// ---- Box herunterfahren")]
        self.assertNotIn("dev_open", page)                                               # kein Knopf "Original öffnen"
        self.assertNotIn("Original-Oberfläche öffnen", page)
        self.assertNotIn("Hier wird nur der Dienst", page)                              # Beschreibungstext entfernt
        self.assertNotIn("ein Klick kopiert es", js + html)                              # Text über dem SSH-Passwort entfernt
        self.assertNotIn("<p", html)
        toggle = js.split('$("dev_toggle").addEventListener')[1].split('$("dev_pw").addEventListener')[0]
        self.assertNotIn("confirm(", toggle)                                             # kein Popup beim Einschalten
        self.assertIn('"SSH "+(d.active?"aktiv":"ist ausgeschaltet")+(d.user?" · Benutzer: „"+d.user+"“":"")', js)
        self.assertIn('d.active?"SSH ausschalten":"SSH einschalten"', js)               # ein Knopf an derselben Stelle
        self.assertEqual(html.count("<button"), 3)                                       # Umschalter, Passwort anzeigen/ausblenden, zurücksetzen
        self.assertIn("devPwShown", js)                                                  # Passwort wieder ausblenden
        self.assertIn("confirm(", js.split('$("dev_reset").addEventListener')[1])        # beim Zurücksetzen bleibt die Rückfrage


if __name__ == "__main__":
    unittest.main()
