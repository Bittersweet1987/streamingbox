"""Tests für die automatische Kamera-Umschaltung (pipbox_send.py). Ohne Kameras, ohne Dateien auf der Box, ohne Zeit."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pipbox_send as ps  # noqa: E402
import server  # noqa: E402

CFG = {"type": "pip", "main": "cam-m", "pip": "cam-p", "pip2": "cam-q", "corner": 3, "corner2": 2, "size_pct": 25,
       "audio": "main", "main_delay_ms": 1500, "pip_delay_ms": 120, "pip2_delay_ms": 250, "auto_failover": True}
ALL = {"cam-m", "cam-p", "cam-q"}


def run(cfg, events, layout=None, end=None):
    """events: Liste (Sekunde, Menge sendender Kameras). Gibt die Umschaltungen als [(Sekunde, Anordnung)] zurück.
    Die Zeit läuft sekündlich bis `end` (Standard: 300 s nach dem letzten Ereignis)."""
    fo = ps.Failover(cfg, layout if layout is not None else ps.effective_cfg(cfg, ALL)[1])
    out = []
    end = end if end is not None else events[-1][0] + 300
    for t in range(0, end + 1):
        r = fo.step(t, live_at(events, t))
        if r is not None:
            out.append((t, r))
    return out


def live_at(events, t):
    cur = events[0][1]
    for sec, live in events:
        if sec <= t:
            cur = live
    return cur


class EffectiveCfg(unittest.TestCase):
    def test_all_live_equals_configuration(self):
        eff, used = ps.effective_cfg(CFG, ALL)
        self.assertEqual(used, ("cam-m", "cam-p", "cam-q"))
        for k in ("main", "pip", "pip2", "main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "type", "audio"):
            self.assertEqual(eff[k], CFG[k], k)

    def test_four_cameras_with_third_small_picture(self):
        cfg = dict(CFG, pip3="cam-r", corner3=0, pip3_delay_ms=300)
        eff, used = ps.effective_cfg(cfg, {"cam-m", "cam-p", "cam-q", "cam-r"})
        self.assertEqual(used, ("cam-m", "cam-p", "cam-q", "cam-r"))
        self.assertEqual((eff["pip3"], eff["pip3_delay_ms"]), ("cam-r", 300))
        # Hauptbild fällt aus: die drei übrigen rücken auf, die Verzögerung folgt der Kamera
        eff, used = ps.effective_cfg(cfg, {"cam-p", "cam-q", "cam-r"})
        self.assertEqual((eff["main"], eff["pip"], eff["pip2"], eff["pip3"]), ("cam-p", "cam-q", "cam-r", ""))
        self.assertEqual((eff["pip_delay_ms"], eff["pip2_delay_ms"]), (250, 300))

    def test_main_missing_promotes_first_small(self):
        eff, used = ps.effective_cfg(CFG, {"cam-p", "cam-q"})
        self.assertEqual((eff["main"], eff["pip"], eff["pip2"], eff["type"]), ("cam-p", "cam-q", "", "pip"))
        # Verzögerung folgt der Kamera, nicht dem Platz
        self.assertEqual((eff["main_delay_ms"], eff["pip_delay_ms"], eff["pip2_delay_ms"]), (120, 250, 0))

    def test_only_one_live_is_single(self):
        eff, used = ps.effective_cfg(CFG, {"cam-q"})
        self.assertEqual((eff["type"], eff["main"], eff["pip"], used), ("single", "cam-q", "", ("cam-q",)))

    def test_nothing_live(self):
        self.assertEqual(ps.effective_cfg(CFG, set()), (None, ()))

    def test_audio_follows_camera(self):
        cfg = dict(CFG, audio="pip")                       # Ton kommt vom kleinen Bild (cam-p)
        self.assertEqual(ps.effective_cfg(cfg, ALL)[0]["audio"], "pip")
        self.assertEqual(ps.effective_cfg(cfg, {"cam-p", "cam-q"})[0]["audio"], "main")   # cam-p ist jetzt Hauptbild
        self.assertEqual(ps.effective_cfg(cfg, {"cam-m", "cam-q"})[0]["audio"], "main")   # cam-p fehlt: Ton vom Hauptbild

    def test_corners_stay_on_slots(self):
        eff, _ = ps.effective_cfg(CFG, {"cam-m", "cam-q"})
        self.assertEqual((eff["pip"], eff["corner"]), ("cam-q", 3))

    def test_pipelines_build_for_every_layout(self):
        store = server.PipelineStore(os.devnull)
        for live in (ALL, {"cam-p", "cam-q"}, {"cam-m", "cam-q"}, {"cam-m"}, {"cam-q"}):
            eff, used = ps.effective_cfg(CFG, live)
            text = store.build(eff)
            self.assertTrue(text, live)
            for k in used:
                self.assertIn(f"/publish/{k}", text)
            for k in ALL - set(used):
                self.assertNotIn(f"/publish/{k}", text)


class FailoverSteps(unittest.TestCase):
    def test_short_dropout_changes_nothing(self):
        self.assertEqual(run(CFG, [(0, ALL), (10, ALL - {"cam-m"}), (14, ALL)]), [])

    def test_dropout_over_5s_switches_and_returns_after_60s(self):
        sw = run(CFG, [(0, ALL), (10, ALL - {"cam-m"}), (200, ALL)])
        self.assertEqual(sw[0], (15, ("cam-p", "cam-q")))                    # 5 s nach dem Ausfall
        self.assertEqual(sw[1], (200 + 60, ("cam-m", "cam-p", "cam-q")))     # 60 s nach der Rückkehr
        self.assertEqual(len(sw), 2)

    def test_flapping_camera_does_not_come_back(self):
        ev = [(0, ALL), (10, ALL - {"cam-m"})]
        for t in range(30, 300, 20):                                         # alle 20 s kurz da, dann wieder weg
            ev += [(t, ALL), (t + 10, ALL - {"cam-m"})]
        sw = run(CFG, ev)
        self.assertEqual(len(sw), 1)

    def test_pip_drop_removes_only_that_one(self):
        sw = run(CFG, [(0, ALL), (10, ALL - {"cam-p"})])
        self.assertEqual(sw, [(15, ("cam-m", "cam-q"))])

    def test_all_dead_waits_then_starts_with_first_after_5s(self):
        sw = run(CFG, [(0, ALL), (10, set()), (100, {"cam-q"})])
        self.assertEqual(sw[0], (15, ()))
        self.assertEqual(sw[1], (105, ("cam-q",)))

    def test_encoder_exit_drops_missing_camera_at_once(self):
        fo = ps.Failover(CFG, ps.effective_cfg(CFG, ALL)[1])
        self.assertIsNone(fo.step(0, ALL))
        # Kamera fällt weg, der Encoder endet gleich danach: ohne Wartezeit weiter mit den übrigen
        self.assertEqual(fo.step(2, ALL - {"cam-p"}, gone=True), ("cam-m", "cam-q"))
        # kommt später erst nach 60 s stabilem Signal zurück
        self.assertIsNone(fo.step(3, ALL))
        self.assertIsNone(fo.step(62, ALL))
        self.assertEqual(fo.step(63, ALL), ("cam-m", "cam-p", "cam-q"))

    def test_encoder_exit_with_all_cameras_present_changes_nothing(self):
        fo = ps.Failover(CFG, ps.effective_cfg(CFG, ALL)[1])
        self.assertIsNone(fo.step(0, ALL))
        self.assertIsNone(fo.step(1, ALL, gone=True))

    def test_encoder_exit_with_unknown_statistics_changes_nothing(self):
        fo = ps.Failover(CFG, ps.effective_cfg(CFG, ALL)[1])
        self.assertIsNone(fo.step(1, None, gone=True))

    def test_encoder_exit_without_any_camera_waits(self):
        fo = ps.Failover(CFG, ps.effective_cfg(CFG, ALL)[1])
        self.assertEqual(fo.step(1, set(), gone=True), ())

    def test_unknown_statistics_change_nothing(self):
        fo = ps.Failover(CFG, ps.effective_cfg(CFG, ALL)[1])
        self.assertIsNone(fo.step(0, ALL))
        for t in range(1, 100):
            self.assertIsNone(fo.step(t, None))

    def test_single_camera_config_has_nothing_to_switch(self):
        cfg = dict(CFG, type="single", pip="", pip2="")
        self.assertEqual(ps.configured_keys(cfg), ["cam-m"])

    def test_late_newcomer_joins_after_60s(self):
        sw = run(CFG, [(0, {"cam-m"}), (30, {"cam-m", "cam-p"})], layout=("cam-m",))
        self.assertEqual(sw, [(90, ("cam-m", "cam-p"))])


class PrepareWithFailover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state, self.work = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "work")
        os.makedirs(self.state)
        import json
        json.dump({"servers": [{"id": "abcdef01", "name": "T", "host": "example.org", "port": 5000, "streamid": ""}],
                   "selected": "abcdef01", "settings": {}}, open(os.path.join(self.state, "srtla.json"), "w"))
        self.patches = [mock.patch.object(ps, "STATE", self.state), mock.patch.object(ps, "WORK", self.work),
                        mock.patch.object(ps, "PLUGIN_DIR", self.tmp),
                        mock.patch.object(server, "iface_ips", lambda: [{"iface": "eth0", "ip": "10.0.0.2"}])]
        open(os.path.join(self.tmp, "libgstpbpip.so"), "w").close()
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def write_cfg(self, **kw):
        import json
        json.dump(dict(CFG, **kw), open(os.path.join(self.state, "pipeline.json"), "w"))

    def test_start_with_main_missing_uses_the_others(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: {"cam-p", "cam-q"}):
            *_, plan = ps.prepare()
        self.assertEqual(plan["layout"], ("cam-p", "cam-q"))
        text = open(os.path.join(self.work, "pipeline")).read()
        self.assertIn("/publish/cam-p", text)
        self.assertNotIn("/publish/cam-m", text)
        self.assertEqual(open(os.path.join(self.state, "main-delay-ms")).read().split(), ["120", "250", "0", "0"])

    def test_start_with_nobody_is_refused(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: set()):
            with self.assertRaises(ps.Refuse):
                ps.prepare()

    def test_start_with_failover_off_needs_every_camera(self):
        self.write_cfg(auto_failover=False)
        with mock.patch.object(ps, "stream_live", lambda k: k != "cam-m"):
            with self.assertRaises(ps.Refuse):
                ps.prepare()

    def test_unreadable_statistics_use_configuration(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: None):
            *_, plan = ps.prepare()
        self.assertEqual(plan["layout"], ("cam-m", "cam-p", "cam-q"))


class SeamlessSwapSender(PrepareWithFailover):
    """Tausch ohne Neustart aus Sicht der Sendekette: Anfangszustand, Merkzettel für die Oberfläche, Nachführen der Einstellung."""

    def start(self, live=None, **kw):
        self.write_cfg(swap_cams=2, **kw)
        with mock.patch.object(ps, "live_keys", lambda: live):
            return ps.prepare()

    def read(self, name):
        with open(os.path.join(self.state, name)) as f:
            return f.read()

    def test_start_writes_initial_state_and_remembers_the_cameras(self):
        stale = os.path.join(self.tmp, "swap-state")
        open(stale, "w").write("1 0 2 3\n")
        with mock.patch.object(server, "SWAP_STATE", stale):
            *_, plan = self.start()
        self.assertEqual(self.read(server.SWAP_SELECT).split(), ["0", "1", "2", "15"])
        self.assertEqual(ps.SWAP_BASE, {"cams": ["cam-m", "cam-p", "cam-q"], "group": 2})
        self.assertFalse(os.path.exists(stale))                    # Rückmeldung der letzten Sendekette ist weg
        self.assertIn("pbpipsel", open(os.path.join(self.work, "pipeline")).read())

    def test_degraded_start_uses_the_cameras_that_are_there(self):
        self.start(live={"cam-p", "cam-q"})
        self.assertEqual(ps.SWAP_BASE["cams"], ["cam-p", "cam-q"])
        self.assertEqual(self.read(server.SWAP_SELECT).split(), ["0", "1", "15", "15"])
        self.assertEqual(self.read("main-delay-ms").split(), ["120", "250", "0", "0"])        # Reihenfolge des Aufbaus

    def test_without_swap_mode_nothing_is_written(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: None):
            ps.prepare()
        self.assertIsNone(ps.SWAP_BASE)
        self.assertFalse(os.path.exists(os.path.join(self.state, server.SWAP_SELECT)))

    def test_status_carries_the_cameras(self):
        *_, plan = self.start()
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], plan)
        run_dir = os.path.join(self.tmp, "run")
        with mock.patch.object(ps, "RUN", run_dir), mock.patch.object(ps, "STATUS", os.path.join(run_dir, "status.json")):
            s.write_status()
            import json
            st = json.load(open(os.path.join(run_dir, "status.json")))
        self.assertEqual(st["swap"], {"cams": ["cam-m", "cam-p", "cam-q"], "group": 2})
        self.assertTrue(st["delay_live"] and st["delay_live_pips"])

    def test_sync_keeps_the_swapped_picture_after_a_later_camera_failure(self):
        *_, plan = self.start()
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], plan)
        s.sync_cfg()                                               # erster Abgleich: gleicher Stand
        swapped = dict(CFG, swap_cams=2, main="cam-p", pip="cam-m", main_delay_ms=120, pip_delay_ms=1500)
        import json
        json.dump(swapped, open(os.path.join(self.state, "pipeline.json"), "w"))
        os.utime(os.path.join(self.state, "pipeline.json"), None)
        self.assertTrue(s.sync_cfg())
        self.assertEqual((s.plan["cfg"]["main"], s.fo.keys[:2]), ("cam-p", ["cam-p", "cam-m"]))
        self.assertFalse(s.sync_cfg())                             # unverändert: nichts zu tun
        # cam-q fällt aus und kommt wieder: das Hauptbild bleibt cam-p
        eff, used = ps.effective_cfg(s.fo.cfg, {"cam-p", "cam-m"})
        self.assertEqual((eff["main"], eff["pip"], eff["main_delay_ms"], eff["pip_delay_ms"]), ("cam-p", "cam-m", 120, 1500))

    def test_sync_does_not_make_the_automatic_switch_restart_the_encoder(self):
        """Fehler aus dem ersten Test mit echten Kameras: Nach dem Tausch hielt die Automatik die neue Reihenfolge für eine neue Anordnung
        und startete den Encoder 3 s später neu."""
        *_, plan = self.start()
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], plan)
        s.sync_cfg()
        live = {"cam-m", "cam-p", "cam-q"}
        self.assertIsNone(s.fo.step(1000, live))                   # vor dem Tausch: nichts zu tun
        import json
        json.dump(dict(CFG, swap_cams=2, main="cam-p", pip="cam-m", main_delay_ms=120, pip_delay_ms=1500),
                  open(os.path.join(self.state, "pipeline.json"), "w"))
        os.utime(os.path.join(self.state, "pipeline.json"), (time.time() + 9, time.time() + 9))
        self.assertTrue(s.sync_cfg())
        self.assertEqual(s.layout, ("cam-p", "cam-m", "cam-q"))
        for t in range(1001, 1400, 2):                             # die Schleife läuft alle 2 s weiter: nie eine neue Anordnung
            self.assertIsNone(s.fo.step(t, live), t)
        # ein echter Ausfall danach schaltet weiter um, und das Hauptbild bleibt cam-p
        gone = {"cam-p", "cam-q"}
        out = None
        for t in range(2000, 2100):
            out = s.fo.step(t, gone) or out
        self.assertEqual(out, ("cam-p", "cam-q"))

    def test_sync_ignores_other_cameras(self):
        *_, plan = self.start()
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], plan)
        s.sync_cfg()
        import json
        json.dump(dict(CFG, swap_cams=2, pip2="cam-z"), open(os.path.join(self.state, "pipeline.json"), "w"))
        os.utime(os.path.join(self.state, "pipeline.json"), (time.time() + 5, time.time() + 5))
        self.assertFalse(s.sync_cfg())
        self.assertEqual(s.plan["cfg"]["pip2"], "cam-q")

    def test_no_sync_without_swap_mode(self):
        self.write_cfg()
        with mock.patch.object(ps, "live_keys", lambda: None):
            *_, plan = ps.prepare()
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], plan)
        self.assertFalse(s.sync_cfg())


class SenderSwitch(unittest.TestCase):
    def test_switch_restarts_only_belacoder_and_waits_without_cameras(self):
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"],
                      {"cfg": CFG, "layout": ("cam-m", "cam-p", "cam-q"), "auto": True})
        calls = []
        s.stop_proc = lambda n: calls.append(("stop", n)) or s.procs.__setitem__(n, None)
        s.spawn = lambda n, a, env=None: calls.append(("spawn", n))
        with mock.patch.object(ps, "write_pipeline", lambda cfg: "pipeline-text"):
            s.switch(("cam-p", "cam-q"), {})
            self.assertEqual(calls, [("stop", "belacoder"), ("spawn", "belacoder")])
            self.assertEqual((s.layout, s.waiting), (("cam-p", "cam-q"), False))
            calls.clear()
            s.switch((), {})
            self.assertEqual(calls, [("stop", "belacoder")])
            self.assertTrue(s.waiting)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ps, "RUN", d), \
                mock.patch.object(ps, "STATUS", os.path.join(d, "status.json")):
            s.write_status()
            import json
            st = json.load(open(os.path.join(d, "status.json")))
            self.assertTrue(st["failover"]["waiting"] and st["failover"]["degraded"])

class WaitLeft(unittest.TestCase):
    def test_returning_camera_counts_down_and_present_ones_are_absent(self):
        fo = ps.Failover(CFG, ("cam-m", "cam-q"))
        fo.step(0, {"cam-m", "cam-q"})
        fo.step(100, {"cam-m", "cam-p", "cam-q"})            # cam-p kommt zurueck
        self.assertEqual(fo.wait_left(100), {"cam-p": 60})
        self.assertEqual(fo.wait_left(130), {"cam-p": 30})
        self.assertEqual(fo.wait_left(500), {"cam-p": 0})
        self.assertNotIn("cam-m", fo.wait_left(100))          # im Bild: keine Wartezeit
        fo.step(110, {"cam-m", "cam-q"})                      # wieder weg: keine Wartezeit mehr
        self.assertEqual(fo.wait_left(110), {})

    def test_status_contains_wait(self):
        import json
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"],
                      {"cfg": CFG, "layout": ("cam-m", "cam-q"), "auto": True})
        now = time.time()
        s.fo.step(now - 100, {"cam-m", "cam-q"})
        s.fo.step(now - 10, {"cam-m", "cam-p", "cam-q"})          # cam-p kam vor 10 s zurueck
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ps, "RUN", d), \
                mock.patch.object(ps, "STATUS", os.path.join(d, "status.json")):
            s.write_status()
            with open(os.path.join(d, "status.json")) as f:
                st = json.load(f)
        self.assertIn("cam-p", st["failover"]["wait"])
        self.assertTrue(0 < st["failover"]["wait"]["cam-p"] <= 60)



class SrtlaEnv(unittest.TestCase):
    def test_min_share_only_with_spread_all(self):
        src = open(os.path.join(os.path.dirname(HERE), "pipbox_send.py"), encoding="utf-8").read()
        self.assertIn('SRTLA_MIN_SHARE_PCT="10"', src)
        # nur im Zweig der Verteilung "alle" (gleiche Zeile wie die 300 ms)
        line = next(l for l in src.splitlines() if "SRTLA_MIN_SHARE_PCT" in l)
        self.assertIn("SRTLA_LAT_MARGIN_MS", line)

class SenderNote(unittest.TestCase):
    def test_stall_messages(self):
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"])
        s.note("Pipeline stall detected (output). Will exit now\n")
        self.assertIn("Ausgang", s.last)
        s.note("Pipeline stall detected (pipeline). Will exit now\n")
        self.assertIn("Eingangsbild", s.last)
        s.note("Pipeline stall detected. Will exit now\n")      # Original aus dem BELABOX-Paket, ohne Zusatz
        self.assertIn("Eingangsbild", s.last)

    def test_reason_goes_to_journal_once_per_burst(self):
        import contextlib
        import io
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for _ in range(5):
                s.note("Failed to establish an SRT connection: x. Retrying...\n")     # kommt alle 0,5 s
            s.note("Pipeline stall detected (output). Will exit now\n")
            s.note("irgendeine andere Zeile mit Adresse 10.0.0.9\n")
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("send: Der Ausgang stockte"))
        self.assertNotIn("10.0.0.9", buf.getvalue())


class SenderEncoderDied(unittest.TestCase):
    def make(self):
        s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"],
                      {"cfg": CFG, "layout": ("cam-m", "cam-p", "cam-q"), "auto": True})
        s.calls = []
        s.stop_proc = lambda n: s.calls.append(("stop", n)) or s.procs.__setitem__(n, None)
        s.spawn = lambda n, a, env=None: s.calls.append(("spawn", n))
        return s

    def test_missing_camera_switches_without_waiting(self):
        s = self.make()
        with mock.patch.object(ps, "write_pipeline", lambda cfg: "pipeline-text"), \
                mock.patch.object(ps, "live_keys", lambda: {"cam-m", "cam-q"}):
            self.assertTrue(s.encoder_died({}))
        self.assertEqual(s.calls, [("stop", "belacoder"), ("spawn", "belacoder")])
        self.assertEqual((s.layout, s.state), (("cam-m", "cam-q"), "running"))

    def test_all_cameras_present_leaves_restart_to_the_loop(self):
        s = self.make()
        with mock.patch.object(ps, "live_keys", lambda: ALL):
            self.assertFalse(s.encoder_died({}))
        self.assertEqual(s.calls, [])

    def test_unreadable_statistics_leave_restart_to_the_loop(self):
        s = self.make()
        with mock.patch.object(ps, "live_keys", lambda: None):
            self.assertFalse(s.encoder_died({}))
        self.assertEqual(s.calls, [])

    def test_no_failover_no_switch(self):
        s = self.make()
        s.fo = None
        self.assertFalse(s.encoder_died({}))


class LiveUplinks(unittest.TestCase):
    def setUp(self):
        import json
        self.d = tempfile.mkdtemp()
        self.json = json
        self.ifs = [{"iface": "eth0", "ip": "192.0.2.10"}, {"iface": "eth2", "ip": "192.0.2.20"}, {"iface": "wlan0", "ip": "192.0.2.30"}]
        for name, val in (("STATE", self.d), ("WORK", self.d)):
            p = mock.patch.object(ps, name, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(server, "iface_ips", lambda: self.ifs); p.start(); self.addCleanup(p.stop)
        self.s = ps.Sender({"name": "x"}, 4000, ["192.0.2.10", "192.0.2.20", "192.0.2.30"])
        self.proc = mock.Mock(); self.proc.poll.return_value = None
        self.s.procs["srtla_send"] = self.proc

    def save(self, ups):
        self.json.dump({"settings": {"uplinks": ups}}, open(os.path.join(self.d, "srtla.json"), "w"))

    def test_change_writes_file_and_sends_sighup(self):
        import signal
        self.save(["eth2", "wlan0"])
        self.assertTrue(self.s.refresh_uplinks())
        self.assertEqual(open(os.path.join(self.d, "ips")).read().split(), ["192.0.2.20", "192.0.2.30"])
        self.proc.send_signal.assert_called_once_with(signal.SIGHUP)
        self.assertFalse(self.s.refresh_uplinks())                 # nichts Neues: kein zweites Signal
        self.assertEqual(self.proc.send_signal.call_count, 1)

    def test_no_usable_network_changes_nothing(self):
        self.save(["eth7"])
        self.assertFalse(self.s.refresh_uplinks())
        self.assertFalse(os.path.exists(os.path.join(self.d, "ips")))
        self.proc.send_signal.assert_not_called()

    def test_missing_networks_are_skipped(self):
        self.save(["eth0", "eth1", "eth2"])                        # eth1 gibt es nicht
        self.assertTrue(self.s.refresh_uplinks())
        self.assertEqual(open(os.path.join(self.d, "ips")).read().split(), ["192.0.2.10", "192.0.2.20"])


class CornerGate(unittest.TestCase):
    def test_positions_only_when_plugin_knows_them(self):
        with tempfile.TemporaryDirectory() as d:
            so = os.path.join(d, "p.so")
            with mock.patch.object(server, "PLUGIN_SO", so), mock.patch.dict(server._CENTER, {"mtime": None, "ok": True, "free": True}):
                self.assertEqual(len(server.pip_corners()), 6)                      # kein Baustein: alles anzeigen
                open(so, "wb").write(b"\0alt: 3 unten rechts\0")
                self.assertEqual(len(server.pip_corners()), 4)                      # alter Baustein
                os.utime(so, (1, 2))
                open(so, "wb").write(b"\0mittel: 4 unten Mitte\0")
                os.utime(so, (1, 5))
                self.assertEqual(len(server.pip_corners()), 5)                      # Baustein mit "unten Mitte"
                open(so, "wb").write(b"\0neu: 4 unten Mitte, 5 frei (x/y)\0")
                os.utime(so, (1, 9))
                self.assertEqual(len(server.pip_corners()), 6)                      # Baustein mit freier Position


if __name__ == "__main__":
    unittest.main(verbosity=2)
