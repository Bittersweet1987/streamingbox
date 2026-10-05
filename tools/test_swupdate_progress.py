"""Tests für den Fortschritt des Update-Helfers (install/pipbox-swupdate.py): Schrittmarken von install.sh, Prozente, Rückgabecode."""
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pbswupdate_progress", os.path.join(os.path.dirname(HERE), "install", "pipbox-swupdate.py"))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def tree(script):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "install"))
    with open(os.path.join(d, "install", "install.sh"), "w") as f:
        f.write(script)
    return d


class RunInstall(unittest.TestCase):
    def run_it(self, script):
        seen = []
        with mock.patch.object(H, "status", lambda **kw: seen.append(kw)):
            rc, tail = H.run_install(tree(script))
        return rc, tail, seen

    def test_markers_become_progress_and_are_not_in_the_log(self):
        rc, tail, seen = self.run_it('echo hallo\necho "PIPBOX-STEP pakete"\necho "PIPBOX-STEP dateien"\necho "PIPBOX-STEP start"\necho fertig\n')
        self.assertEqual(rc, 0)
        self.assertEqual([s["progress"] for s in seen], [40, 58, 95])
        self.assertEqual(seen[0]["step"], "Prüfe die benötigten Pakete")
        self.assertEqual(tail, ["hallo", "fertig"])

    def test_progress_only_grows_along_the_real_order(self):
        order = ["pakete", "dienst", "dateien", "baustein", "srtla", "belacoder", "start"]
        pcts = [H.INSTALL_STEPS[k][0] for k in order]
        self.assertEqual(pcts, sorted(pcts))
        self.assertGreater(pcts[0], 36)                  # nach "Installiere Version" (36)
        self.assertLess(pcts[-1], 100)                   # 100 gibt es erst, wenn der Dienst wieder läuft

    def test_unknown_marker_is_just_a_line_and_exit_code_is_returned(self):
        rc, tail, seen = self.run_it('echo "PIPBOX-STEP gibtsnicht"\necho Fehler >&2\nexit 3\n')
        self.assertEqual(rc, 3)
        self.assertEqual(seen, [])
        self.assertEqual(tail, ["PIPBOX-STEP gibtsnicht", "Fehler"])

    def test_log_keeps_only_the_last_25_lines(self):
        rc, tail, _ = self.run_it("for i in $(seq 1 60); do echo z$i; done\n")
        self.assertEqual((rc, len(tail), tail[-1]), (0, 25, "z60"))

    def test_too_long_install_is_killed(self):
        with mock.patch.object(H, "INSTALL_TIMEOUT", 0.5):
            rc, tail, _ = self.run_it("sleep 30\n")
        self.assertEqual(rc, -9)


class InstallScript(unittest.TestCase):
    def test_every_marker_in_install_sh_is_known_and_in_order(self):
        src = open(os.path.join(os.path.dirname(HERE), "install", "install.sh"), encoding="utf-8").read()
        marks = re.findall(r'^\s*echo "PIPBOX-STEP (\w+)"', src, re.M)
        self.assertEqual(marks, ["pakete", "dienst", "dateien", "baustein", "srtla", "belacoder", "start"])
        self.assertEqual(set(marks), set(H.INSTALL_STEPS))


class Download(unittest.TestCase):
    def test_download_reports_percent(self):
        class R(io.BytesIO):
            headers = {"Content-Length": "200000"}

            def geturl(self):
                return "https://example.invalid/x"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        got = []
        t = [0.0]

        def clock():
            t[0] += 1.0
            return t[0]
        with mock.patch.object(H.urllib.request, "urlopen", lambda *a, **k: R(b"x" * 200000)), mock.patch.object(H.time, "time", clock), \
                mock.patch.object(H, "TEST_TARBALL", ""):
            data = H.download("https://example.invalid/x", got.append)
        self.assertEqual(len(data), 200000)
        self.assertEqual(got[-1], 100)
        self.assertEqual(got, sorted(got))


class TailscaleSnapshot(unittest.TestCase):
    """install.sh schreibt den Zustand der Tailscale-Freigaben vor und nach der Installation ins Protokoll (Fehlersuche), ohne je zu scheitern."""

    def run_fn(self, with_tailscale):
        src = open(os.path.join(os.path.dirname(HERE), "install", "install.sh"), encoding="utf-8").read()
        fn = re.search(r"ts_snapshot\(\) \{.*?\n\}\n", src, re.S).group(0)
        d = tempfile.mkdtemp()
        if with_tailscale:
            with open(os.path.join(d, "tailscale"), "w") as f:
                f.write('#!/bin/sh\nif [ "$1" = serve ]; then echo "https://box.example.ts.net (tailnet only)"; else echo "No serve config"; fi\n')
            os.chmod(os.path.join(d, "tailscale"), 0o755)
        log = os.path.join(d, "log")
        import subprocess
        r = subprocess.run(["sh", "-c", fn + '\nts_snapshot "vor der Installation"\nts_snapshot "nach der Installation"\n'],
                           env={"PATH": d + ":/usr/bin:/bin", "PIPBOX_SWUPDATE_LOG": log}, capture_output=True, text=True)
        return r, log

    def test_snapshots_before_and_after(self):
        r, log = self.run_fn(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = open(log).read()
        self.assertEqual(len(re.findall(r"Tailscale vor der Installation:", text)), 1)
        self.assertEqual(len(re.findall(r"Tailscale nach der Installation:", text)), 1)
        self.assertEqual(text.count("    serve: https://box.example.ts.net (tailnet only)"), 2)
        self.assertEqual(text.count("    funnel: No serve config"), 2)

    def test_no_tailscale_no_log_no_error(self):
        r, log = self.run_fn(False)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(log))

    def test_called_once_before_and_once_after_the_installation(self):
        src = open(os.path.join(os.path.dirname(HERE), "install", "install.sh"), encoding="utf-8").read()
        self.assertLess(src.index('ts_snapshot "vor der Installation"'), src.index('echo "PIPBOX-STEP pakete"'))
        self.assertGreater(src.index('ts_snapshot "nach der Installation"'), src.index('echo "PIPBOX-STEP start"'))


if __name__ == "__main__":
    unittest.main()
