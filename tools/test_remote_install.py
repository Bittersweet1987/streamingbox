"""Tests für die Installation von Tailscale (install/pipbox-remote.py): Paketliste laden mit Wiederholung, klare Fehlermeldung."""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pipbox_remote", os.path.join(os.path.dirname(HERE), "install", "pipbox-remote.py"))
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


class Result:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


class Install(unittest.TestCase):
    def run_install(self, known_after, updates_ok_from=0):
        """known_after: ab dem wievielten apt-get update das Paket bekannt ist (None: nie)."""
        calls = []
        tmp = tempfile.mkdtemp()
        state = {"installed": False}

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["apt-get", "update"]:
                return Result("E: Could not get lock /var/lib/apt/lists/lock" if known_after is None or len([c for c in calls if c[:2] == ["apt-get", "update"]]) < known_after else "ok", 100)
            if cmd[0] == "apt-cache":
                n = len([c for c in calls if c[:2] == ["apt-get", "update"]])
                known = known_after is not None and n >= known_after
                return Result("tailscale:\n  Installed: (none)\n  Candidate: " + ("1.2.3" if known else "(none)") + "\n")
            if cmd[:2] == ["apt-get", "install"]:
                state["installed"] = True
                return Result("")
            return Result("")

        with mock.patch.object(remote, "get", lambda url, limit=0: b"deb https://pkgs.tailscale.com/stable/ubuntu jammy main" if "list" in url else b"key"), \
                mock.patch.object(remote, "codename", lambda: "jammy"), \
                mock.patch.object(remote, "KEYRING", os.path.join(tmp, "k.gpg")), mock.patch.object(remote, "SOURCE", os.path.join(tmp, "t.list")), \
                mock.patch.object(remote, "installed", lambda: state["installed"]), mock.patch.object(remote, "status", lambda **kw: None), \
                mock.patch.object(remote, "log", lambda m: None), mock.patch.object(remote.time, "sleep", lambda s: None), \
                mock.patch.object(remote.subprocess, "run", fake_run):
            try:
                remote.do_install()
                err = None
            except RuntimeError as e:
                err = str(e)
        return calls, err

    def test_works_at_first_try(self):
        calls, err = self.run_install(1)
        self.assertIsNone(err)
        self.assertEqual(len([c for c in calls if c[:2] == ["apt-get", "update"]]), 1)

    def test_retries_while_apt_is_locked(self):
        calls, err = self.run_install(3)
        self.assertIsNone(err)
        self.assertEqual(len([c for c in calls if c[:2] == ["apt-get", "update"]]), 3)

    def test_falls_back_to_full_update(self):
        calls, err = self.run_install(5)
        self.assertIsNone(err)
        last = [c for c in calls if c[:2] == ["apt-get", "update"]][-1]
        self.assertEqual(last, ["apt-get", "update"])

    def test_gives_clear_error_and_does_not_try_to_install(self):
        calls, err = self.run_install(None)
        self.assertIn("Tailscale ist fehlgeschlagen", err)
        self.assertIn("lock", err)
        self.assertFalse([c for c in calls if c[:2] == ["apt-get", "install"]])


class Serve(unittest.TestCase):
    """tailscale serve wartet auf die Freischaltung im Konto und blockiert: der Link muss trotzdem in der Oberfläche ankommen."""

    def run_serve(self, fake_run):
        seen = {}
        with mock.patch.object(remote, "status", lambda **kw: seen.update(kw)), mock.patch.object(remote.subprocess, "run", fake_run):
            try:
                remote.do_serve_on()
                err = None
            except RuntimeError as e:
                err = str(e)
        return seen, err

    def test_timeout_with_link_asks_for_enabling(self):
        def fake_run(cmd, **kw):
            raise remote.subprocess.TimeoutExpired(cmd, 40, output=b"\nServe is not enabled on your tailnet.\nTo enable, visit:\n\n\t https://login.tailscale.com/f/serve?node=n123abc\n")
        seen, err = self.run_serve(fake_run)
        self.assertIsNone(err)
        self.assertEqual(seen["state"], "needs_serve")
        self.assertEqual(seen["hint_url"], "https://login.tailscale.com/f/serve?node=n123abc")

    def test_timeout_without_output_gives_a_message_not_a_traceback(self):
        def fake_run(cmd, **kw):
            raise remote.subprocess.TimeoutExpired(cmd, 40)
        seen, err = self.run_serve(fake_run)
        self.assertIn("Freigabe fehlgeschlagen", err)

    def test_funnel_timeout_with_link_asks_for_enabling(self):
        def fake_run(cmd, **kw):
            raise remote.subprocess.TimeoutExpired(cmd, 40, output=b"Funnel is not enabled on your tailnet.\nTo enable, visit:\n https://login.tailscale.com/f/funnel?node=n123abc\n")
        seen = {}
        with mock.patch.object(remote, "status", lambda **kw: seen.update(kw)), mock.patch.object(remote.subprocess, "run", fake_run), \
                mock.patch.object(remote, "funnel_active", lambda cfg=None: False):
            remote.do_funnel_on()
        self.assertEqual(seen["state"], "needs_funnel")
        self.assertEqual(seen["hint_url"], "https://login.tailscale.com/f/funnel?node=n123abc")

    def test_success(self):
        seen, err = self.run_serve(lambda cmd, **kw: Result("Available within your tailnet", 0))
        self.assertIsNone(err)
        self.assertEqual(seen["state"], "idle")


if __name__ == "__main__":
    unittest.main()
