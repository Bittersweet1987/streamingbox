"""Tests für den weichen Start der Sendekette: belacoder startet mit fester Startbitrate, darf steigen, sobald der Encoder stabil 25 bis 36 Bilder je Sekunde liefert, und danach ist die Startbitrate noch
SOFT_FLOOR_S lang die Untergrenze (Testbox, 13. Oktober 2026: beim Anlauf 4 bis 5 Bilder je Sekunde, der Regler hob die Bitrate an, dann Nachholen mit 70 bis 90 Bildern: Spitze und "Überlast"). Ohne Kameras, ohne Box."""
import io
import json
import os
import signal
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pipbox_send as ps  # noqa: E402

CFG = {"type": "pip", "main": "cam-m", "pip": "cam-p", "corner": 3, "size_pct": 25, "audio": "main"}


class FakeProc:
    """Ein Kindprozess, der lebt, bis stop() gerufen wird, und die gesendeten Signale merkt."""

    def __init__(self):
        self.alive, self.sigs, self.stdout = True, [], []

    def poll(self):
        return None if self.alive else 0

    def send_signal(self, sig):
        self.sigs.append(sig)

    def stop(self):
        self.alive = False


class FakeThread:
    """Ersatz für threading.Thread: merkt sich Ziel und Argumente, läuft nur, wenn der Test es sagt."""
    made = []

    def __init__(self, target=None, args=(), daemon=None, **kw):
        self.target, self.args = target, args
        FakeThread.made.append(self)

    def start(self):
        pass


def read_br(path):
    with open(path) as f:
        return [int(x) for x in f.read().split()]


class Default(unittest.TestCase):
    def test_the_soft_start_is_off(self):
        """Entscheidung des Nutzers (13. Oktober 2026): kein weicher Start, belacoder startet wie bisher mit der größten Bitrate (der weiche Start verlängerte den Start der Sendung deutlich)."""
        self.assertEqual(ps.SOFT_START_KBPS, 0)
        self.assertIsNone(ps.Sender.soft_rate(300000, 12000000))

    def test_nothing_is_changed_when_it_is_off(self):
        """Ohne weichen Start: keine Änderung der Bitrate-Datei, kein Zeitplan, kein Signal an belacoder."""
        d = tempfile.mkdtemp()
        with open(d + "/bitrate", "w") as f:
            f.write("300000\n12000000")
        FakeThread.made = []
        with mock.patch.object(ps, "WORK", d), mock.patch.object(ps.threading, "Thread", FakeThread), mock.patch.object(ps.Sender, "pump", lambda *a: None), \
                mock.patch.object(ps.subprocess, "Popen", lambda *a, **k: FakeProc()):
            s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], {"cfg": CFG, "layout": ("cam-m",), "auto": False})
            p = s.spawn("belacoder", ["belacoder"])
        self.assertEqual(read_br(d + "/bitrate"), [300000, 12000000])
        self.assertEqual([t for t in FakeThread.made if getattr(t.target, "__name__", "") == "soft_start_run"], [])
        self.assertEqual(p.sigs, [])


@mock.patch.object(ps, "SOFT_START_KBPS", 2000)          # die übrigen Prüfungen betreffen den eingeschalteten weichen Start
class Rates(unittest.TestCase):
    def test_default_settings_start_at_2000(self):
        self.assertEqual(ps.Sender.soft_rate(300000, 12000000), 2000000)

    def test_never_below_the_minimum(self):
        self.assertEqual(ps.Sender.soft_rate(5000000, 12000000), 5000000)

    def test_no_soft_start_when_the_maximum_is_not_above_the_start(self):
        self.assertIsNone(ps.Sender.soft_rate(300000, 2000000))
        self.assertIsNone(ps.Sender.soft_rate(300000, 1500000))
        self.assertIsNone(ps.Sender.soft_rate(12000000, 12000000))
        self.assertEqual(ps.Sender.soft_rate(300000, 2100000), 2000000)

    def test_a_minimum_belacoder_would_reject_keeps_the_old_behaviour(self):
        """belacoder nimmt nichts unter 300 kbit/s an (Fehler beim Start); der weiche Start darf das nicht verdecken."""
        self.assertIsNone(ps.Sender.soft_rate(100000, 12000000))
        self.assertIsNone(ps.Sender.soft_rate(299999, 12000000))
        self.assertEqual(ps.Sender.soft_rate(300000, 12000000), 2000000)


@mock.patch.object(ps, "SOFT_START_KBPS", 2000)
class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.br = self.d + "/bitrate"
        with open(self.br, "w") as f:
            f.write("300000\n12000000")
        FakeThread.made = []
        self.stats = self.d + "/stats.json"
        for p in (mock.patch.object(ps, "WORK", self.d), mock.patch.object(ps, "BC_STATS", self.stats),
                  mock.patch.object(ps.threading, "Thread", FakeThread), mock.patch.object(ps.Sender, "pump", lambda *a: None)):
            p.start()
            self.addCleanup(p.stop)
        self.s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], {"cfg": CFG, "layout": ("cam-m",), "auto": False})
        self.waits, self.files = [], []

        def wait(t):
            self.waits.append(t)
            try:
                self.files.append(read_br(self.br))
            except (OSError, ValueError):
                self.files.append(None)
            return False
        self.s.stop_ev.wait = wait
        self.tick = 0

        def stable(*a):                                                                   # jede Abfrage eine neue Sekunde mit 30 Bildern
            self.tick += 1
            return {"time": 1000 + self.tick, "fps": 30.0, "sent_total": 100 * self.tick}
        self.stats_patch = mock.patch.object(ps.Sender, "bc_stats", stable)
        self.stats_patch.start()
        self.addCleanup(self.stats_patch.stop)

        def popen(args, **kw):
            return FakeProc()
        pp = mock.patch.object(ps.subprocess, "Popen", popen)
        pp.start()
        self.addCleanup(pp.stop)

    def soft_threads(self):
        return [t for t in FakeThread.made if getattr(t.target, "__name__", "") == "soft_start_run"]

    def run_soft(self, i=-1):
        t = self.soft_threads()[i]
        t.target(*t.args)

    def test_belacoder_starts_with_a_fixed_start_bitrate(self):
        self.s.spawn("belacoder", ["belacoder"])
        self.assertEqual(read_br(self.br), [2000000, 2000000])
        self.assertEqual(len(self.soft_threads()), 1)
        self.assertEqual(self.soft_threads()[0].args[1], 2000000)

    def feed(self, seq):
        """bc_stats liefert nacheinander diese Antworten (Zeit, Bilder je Sekunde) oder None; danach immer die letzte."""
        it = iter(seq)
        last = [None]

        def f(*a):
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            v = last[0]
            return None if v is None else {"time": v[0], "fps": v[1], "sent_total": 1}
        return mock.patch.object(ps.Sender, "bc_stats", f)

    def test_the_schedule_waits_for_a_steady_frame_rate(self):
        p = self.s.spawn("belacoder", ["belacoder"])
        seq = [(1, 0.0), (2, 5.0), (3, 5.0), (4, 71.0), (5, 30.0), (6, 30.0), (7, 30.0)]    # Anlauf mit 5 Bildern, Nachholen mit 71, dann 3 stabile Sekunden
        with self.feed(seq):
            self.run_soft()
        self.assertEqual(self.waits[:6], [0.5] * 6)                                       # sechs Abfragen ohne Ergebnis
        self.assertEqual(self.files[0], [2000000, 2000000])                               # bis dahin fest
        self.assertEqual(self.waits[6], ps.SOFT_FLOOR_S)                                  # nach der Freigabe die Untergrenze noch 20 s
        self.assertEqual(self.files[6], [2000000, 12000000])                              # Phase 1: die Untergrenze bleibt
        self.assertEqual(read_br(self.br), [300000, 12000000])                            # Phase 2: echte Werte
        self.assertEqual(p.sigs, [signal.SIGHUP, signal.SIGHUP])

    def test_the_catch_up_burst_does_not_count_as_a_steady_rate(self):
        p = self.s.spawn("belacoder", ["belacoder"])
        seq = [(1, 30.0), (2, 30.0), (3, 80.0), (4, 30.0), (5, 30.0), (6, 30.0)]          # zwei gute Sekunden, dann Schub: von vorn
        with self.feed(seq):
            self.run_soft()
        self.assertEqual(self.waits[:5], [0.5] * 5)                                       # erst nach Sekunde 6 steht die Serie
        self.assertEqual(self.waits[5], ps.SOFT_FLOOR_S)

    def test_the_same_second_is_counted_once(self):
        """belacoder schreibt die Datei einmal je Sekunde; Abfragen alle 0,5 s sehen oft dieselbe Sekunde."""
        self.s.spawn("belacoder", ["belacoder"])
        seq = [(1, 30.0), (1, 30.0), (2, 30.0), (2, 30.0), (3, 30.0)]
        with self.feed(seq):
            self.run_soft()
        self.assertEqual(self.waits[:4], [0.5] * 4)                                       # erst die dritte neue Sekunde reicht (5. Abfrage)
        self.assertEqual(self.waits[4], ps.SOFT_FLOOR_S)

    def steady_after(self, seq, extra=None):
        """Wie viele Wartezeiten von 0,5 s liegen vor der Freigabe (Phase 1)?"""
        p = self.s.spawn("belacoder", ["belacoder"])
        with self.feed(seq):
            self.run_soft()
        n = 0
        while n < len(self.waits) and self.waits[n] == 0.5:
            n += 1
        return n, p

    def test_25_and_60_fps_sources_count_as_steady(self):
        self.assertEqual(self.steady_after([(1, 24.9), (2, 25.0), (3, 24.9)])[0], 2)
        self.setUp()
        self.assertEqual(self.steady_after([(1, 60.0), (2, 59.8), (3, 60.2)])[0], 2)

    def test_uneven_or_starved_rates_are_not_steady(self):
        n, p = self.steady_after([(1, 15.0), (2, 30.0), (3, 15.0), (4, 30.0), (5, 15.0), (6, 30.0), (7, 15.0)] + [(8 + i, 30.0) for i in range(3)])
        self.assertEqual(n, 9)                                                            # erst die drei gleichmäßigen Sekunden am Ende

    def test_a_gap_in_the_seconds_starts_the_count_again(self):
        n, p = self.steady_after([(1, 30.0), (2, 30.0), (10, 30.0), (11, 30.0), (12, 30.0)])    # Lücke von 8 s (belacoder blockiert)
        self.assertEqual(n, 4)

    def test_the_deadline_counts_from_the_first_stats_file_not_from_the_start(self):
        """Nach einem Netzausfall wartet belacoder lange auf die Verbindung (keine Datei): die 60 s dürfen dann noch nicht laufen."""
        seq = [None] * 130 + [(1000 + i, 30.0) for i in range(5)]
        n, p = self.steady_after(seq)
        self.assertEqual(n, 130 + 2)                                                      # 130 Abfragen ohne Datei, dann zwei Sekunden bis zur Serie
        self.assertEqual(p.sigs, [signal.SIGHUP, signal.SIGHUP])

    def test_without_a_steady_frame_rate_the_phases_still_run_60_seconds_after_the_first_stats(self):
        n, p = self.steady_after([(i, 5.0) for i in range(1, 400)])
        self.assertEqual(n, 119)
        self.assertEqual(read_br(self.br), [300000, 12000000])
        self.assertEqual(p.sigs, [signal.SIGHUP, signal.SIGHUP])

    def test_without_any_stats_file_the_phases_still_run_after_90_seconds(self):
        """belacoder ohne Kennzahlen-Datei (Original): die Bitrate bleibt nicht dauerhaft gedeckelt."""
        n, p = self.steady_after([None])
        self.assertEqual(n, 179)
        self.assertEqual(read_br(self.br), [300000, 12000000])
        self.assertEqual(p.sigs, [signal.SIGHUP, signal.SIGHUP])

    def test_an_unexpected_error_releases_the_bitrate(self):
        p = self.s.spawn("belacoder", ["belacoder"])

        def boom(*a):
            raise RuntimeError("kaputt")
        with mock.patch.object(ps.Sender, "bc_stats", boom):
            self.run_soft()
        self.assertEqual(read_br(self.br), [300000, 12000000])
        self.assertEqual(p.sigs, [signal.SIGHUP, signal.SIGHUP])

    def test_odd_fps_values_never_raise(self):
        n, p = self.steady_after([(1, None), (2, "x"), (3, -5.0), (4, float("inf")), (5, 30.0), (6, 30.0), (7, 30.0)])
        self.assertEqual(n, 6)

    def test_a_gone_belacoder_gets_no_signal_and_the_file_stays_alone(self):
        p = self.s.spawn("belacoder", ["belacoder"])
        p.stop()
        self.run_soft()
        self.assertEqual(p.sigs, [])
        self.assertEqual(read_br(self.br), [2000000, 2000000])

    def test_an_older_thread_does_not_touch_the_file_or_signal_a_newer_belacoder(self):
        old = self.s.spawn("belacoder", ["belacoder"])
        old.stop()
        new = self.s.spawn("belacoder", ["belacoder"])                                    # Neustart (Wächter, Umschalten)
        self.assertEqual(read_br(self.br), [2000000, 2000000])                            # wieder weich, mit den gemerkten echten Werten
        self.run_soft(0)                                                                  # der Ablauf des alten
        self.assertEqual(old.sigs, [])
        self.assertEqual(new.sigs, [])
        self.assertEqual(read_br(self.br), [2000000, 2000000])
        self.run_soft(1)                                                                  # der des neuen
        self.assertEqual(new.sigs, [signal.SIGHUP, signal.SIGHUP])
        self.assertEqual(read_br(self.br), [300000, 12000000])

    def test_restart_remembers_the_real_values_not_the_soft_ones(self):
        self.s.spawn("belacoder", ["belacoder"])
        self.s.spawn("belacoder", ["belacoder"])                                          # die Datei hatte schon den weichen Wert
        self.run_soft(1)
        self.assertEqual(read_br(self.br), [300000, 12000000])

    def test_the_signal_waits_until_belacoder_knows_it(self):
        """SIGHUP beendet einen Prozess, der noch keine Funktion dafür hat (belacoder baut erst die Pipeline auf)."""
        p = self.s.spawn("belacoder", ["belacoder"])
        answers = iter([False, False, True, True, True, True])
        with mock.patch.object(ps.Sender, "hup_ready", staticmethod(lambda q: next(answers))):
            self.run_soft()
        self.assertEqual(self.waits[:5], [0.5, 0.5, 0.5, 0.5, ps.SOFT_FLOOR_S])          # zwei Abfragen bis zum stabilen Bild, zwei Wartezeiten auf das Signal, dann die Untergrenze
        self.assertEqual(p.sigs, [signal.SIGHUP, signal.SIGHUP])

    def test_no_signal_when_it_never_knows_it_after_20_seconds(self):
        p = self.s.spawn("belacoder", ["belacoder"])
        with mock.patch.object(ps.Sender, "hup_ready", staticmethod(lambda q: False)):
            self.run_soft()
        self.assertEqual(p.sigs, [])                                                      # nie ein Signal an einen Prozess ohne Funktion
        self.assertEqual(read_br(self.br), [300000, 12000000])                            # die Datei stimmt für den nächsten Start

    def test_hup_ready_reads_the_signal_mask_of_the_process(self):
        for mask, want in (("0000000000000001", True), ("0000000000014a03", True), ("0000000000004002", False), ("0000000000000000", False)):
            text = "Name:\tx\nSigCgt:\t%s\nCapInh:\t0\n" % mask
            with mock.patch("builtins.open", lambda *a, **k: io.StringIO(text)):
                self.assertEqual(ps.Sender.hup_ready(mock.Mock(pid=4242)), want, mask)
        self.assertTrue(ps.Sender.hup_ready(FakeProc()))                                  # kein pid: gilt als bereit (Prüfung nicht möglich)
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertTrue(ps.Sender.hup_ready(mock.Mock(pid=4242)))                    # nicht lesbar: bereit

    def test_stop_event_ends_the_schedule_without_touching_the_file(self):
        p = self.s.spawn("belacoder", ["belacoder"])
        self.s.stop_ev.set()
        self.s.stop_ev.wait = lambda t: True
        self.run_soft()
        self.assertEqual(p.sigs, [])
        self.assertEqual(read_br(self.br), [2000000, 2000000])

    def test_srtla_send_is_untouched(self):
        self.s.spawn("srtla_send", ["srtla_send"])
        self.assertEqual(read_br(self.br), [300000, 12000000])
        self.assertEqual(self.soft_threads(), [])

    def test_maximum_at_or_below_the_start_value_needs_no_soft_start(self):
        with open(self.br, "w") as f:
            f.write("300000\n1500000")
        self.s.spawn("belacoder", ["belacoder"])
        self.assertEqual(read_br(self.br), [300000, 1500000])
        self.assertEqual(self.soft_threads(), [])

    def test_an_invalid_minimum_is_left_for_belacoder_to_report(self):
        with open(self.br, "w") as f:
            f.write("100000\n12000000")
        self.s.spawn("belacoder", ["belacoder"])
        self.assertEqual(read_br(self.br), [100000, 12000000])
        self.assertEqual(self.soft_threads(), [])

    def test_missing_or_broken_file_never_stops_the_start(self):
        os.unlink(self.br)
        self.s.spawn("belacoder", ["belacoder"])
        self.assertEqual(self.soft_threads(), [])
        with open(self.br, "w") as f:
            f.write("kaputt")
        self.s.spawn("belacoder", ["belacoder"])
        self.assertEqual(self.soft_threads(), [])

    def test_the_file_is_written_atomically(self):
        ps.Sender.write_bitrate(300000, 12000000)
        self.assertEqual(read_br(self.br), [300000, 12000000])
        self.assertFalse(os.path.exists(self.br + ".tmp"))


class Stats(unittest.TestCase):
    """bc_stats liest die Kennzahlen-Datei von belacoder (einmal je Sekunde)."""

    def setUp(self):
        self.f = tempfile.mktemp()
        p = mock.patch.object(ps, "BC_STATS", self.f)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: os.path.exists(self.f) and os.unlink(self.f))
        self.s = ps.Sender({"name": "T", "host": "h", "port": 1, "streamid": ""}, 2000, ["10.0.0.2"], {"cfg": CFG, "layout": ("cam-m",), "auto": False})

    def write(self, text):
        with open(self.f, "w") as f:
            f.write(text)

    def test_a_file_of_this_run_is_returned(self):
        self.write(json.dumps({"time": 1000, "fps": 29.9, "sent_total": 5}))
        self.assertEqual(self.s.bc_stats(999.4)["fps"], 29.9)
        self.assertEqual(self.s.bc_stats(1000.0)["time"], 1000)

    def test_a_file_of_the_previous_run_does_not_count(self):
        self.write(json.dumps({"time": 900, "fps": 30.0, "sent_total": 99999}))
        self.assertIsNone(self.s.bc_stats(1000.0))

    def test_missing_or_broken_file(self):
        self.assertIsNone(self.s.bc_stats(0))
        self.write("{kaputt")
        self.assertIsNone(self.s.bc_stats(0))
        self.write("[]")
        self.assertIsNone(self.s.bc_stats(0))
        self.write(json.dumps({"time": None, "fps": None}))
        self.assertIsNone(self.s.bc_stats(1000))                                           # keine Zeit: zählt nicht
        self.write('{"time": 1e999}')
        self.assertIsNone(self.s.bc_stats(1000))                                           # unendlich: kein Absturz


if __name__ == "__main__":
    unittest.main()
