"""Tests für die geglättete Anzeige der Netzraten (Upload/Download je Netzkarte und Summe): gleitender Mittelwert statt Rohwert (Testbox, 13. Oktober 2026: Anzeige sprang zwischen
10 und 20 Mbit/s, im Mittel über 10 s waren es 14,5). Ohne Box."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402


def raw(tx, rx=0.0, n="eth0"):
    return {n: {"rx_mbit": rx, "tx_mbit": tx}}


class Smooth(unittest.TestCase):
    def setUp(self):
        self.s = server.Sampler(False)

    def run_series(self, values, dt=2.0):
        return [self.s.smooth_rates(raw(v), dt)["eth0"]["tx_mbit"] for v in values]

    def test_first_value_is_shown_as_measured(self):
        self.assertEqual(self.s.smooth_rates(raw(12.0), None)["eth0"]["tx_mbit"], 12.0)

    def test_a_steady_rate_stays_exactly_where_it_is(self):
        out = self.run_series([12.0] * 10)
        self.assertEqual(out, [12.0] * 10)

    def test_jumping_values_are_calmed(self):
        """Roh 8 und 16 im Wechsel (Schwankung 8 Mbit/s): die Anzeige schwankt danach um höchstens 2,5 Mbit/s um den Mittelwert 12."""
        vals = [8.0, 16.0] * 15
        out = self.run_series(vals)
        tail = out[10:]
        self.assertLess(max(tail) - min(tail), 3.0)
        self.assertAlmostEqual(sum(tail) / len(tail), 12.0, delta=0.6)

    def test_a_single_outlier_hardly_moves_the_display(self):
        out = self.run_series([12.0] * 8 + [21.0] + [12.0] * 4)
        self.assertLess(max(out) - 12.0, 4.0)                     # 21 statt 12: weniger als die Hälfte des Sprungs

    def test_a_failure_is_visible_within_a_few_seconds(self):
        out = self.run_series([12.0] * 6 + [0.0] * 5)
        self.assertLess(out[6], 8.0)                              # schon nach der ersten Messung ohne Verkehr (2 s) deutlich unten
        self.assertEqual(out[7], 0.0)                             # nach der zweiten (rund 3 s) sofort bei null: ein Ausfall wird nicht ausgeglättet

    def test_a_single_quiet_measurement_is_still_smoothed(self):
        """Eine einzelne Messung ohne Verkehr (Pause zwischen zwei Schüben) ist kein Ausfall."""
        out = self.run_series([12.0] * 5 + [0.0] + [12.0] * 3)
        self.assertGreater(min(out[5:]), 6.0)

    def test_a_partial_drop_decays_smoothly(self):
        out = self.run_series([12.0] * 4 + [3.0] * 6)
        self.assertGreater(out[5], 6.0)                           # nach 4 s erst etwa auf halber Höhe
        self.assertLess(out[9], 4.5)                              # nach 12 s fast unten

    def test_a_rise_is_followed_too(self):
        out = self.run_series([0.0] * 4 + [12.0] * 6)
        self.assertGreater(out[-1], 9.0)                          # nach 12 s fast oben

    def test_time_constant_does_not_depend_on_how_often_the_page_asks(self):
        a, b = server.Sampler(False), server.Sampler(False)
        a.smooth_rates(raw(12.0), None)
        b.smooth_rates(raw(12.0), None)
        for _ in range(4):                                        # 8 s: vier Messungen zu 2 s
            ra = a.smooth_rates(raw(6.0), 2.0)
        for _ in range(8):                                        # 8 s: acht Messungen zu 1 s
            rb = b.smooth_rates(raw(6.0), 1.0)
        self.assertAlmostEqual(ra["eth0"]["tx_mbit"], rb["eth0"]["tx_mbit"], delta=0.05)

    def test_after_a_long_pause_the_display_starts_over_with_the_raw_value(self):
        self.run_series([12.0] * 5)
        self.assertEqual(self.s.smooth_rates(raw(3.0), 60.0)["eth0"]["tx_mbit"], 3.0)

    def test_each_interface_and_rx_are_smoothed_on_their_own(self):
        r = {"eth0": {"rx_mbit": 1.0, "tx_mbit": 10.0}, "wlan0": {"rx_mbit": 20.0, "tx_mbit": 2.0}}
        self.s.smooth_rates(r, None)
        r2 = {"eth0": {"rx_mbit": 1.0, "tx_mbit": 10.0}, "wlan0": {"rx_mbit": 0.0, "tx_mbit": 2.0}}
        out = self.s.smooth_rates(r2, 2.0)
        self.assertEqual(out["eth0"], {"rx_mbit": 1.0, "tx_mbit": 10.0})
        self.assertLess(out["wlan0"]["rx_mbit"], 20.0)
        self.assertGreater(out["wlan0"]["rx_mbit"], 10.0)

    def test_a_new_interface_starts_with_its_raw_value_and_a_gone_one_is_forgotten(self):
        self.s.smooth_rates(raw(12.0, n="eth0"), None)
        out = self.s.smooth_rates({"eth1": {"rx_mbit": 0.0, "tx_mbit": 5.0}}, 2.0)
        self.assertEqual(list(out), ["eth1"])
        self.assertEqual(out["eth1"]["tx_mbit"], 5.0)
        out = self.s.smooth_rates(raw(1.0, n="eth0"), 2.0)
        self.assertEqual(out["eth0"]["tx_mbit"], 1.0)             # war weg, beginnt neu

    def test_the_sampler_reports_the_smoothed_values(self):
        """Durch die ganze Messung: vier Zählerstände, Raten 8, 16, 8, 16 Mbit/s über je 2 s."""
        s = server.Sampler(False)
        t = [1000.0]
        counters = [0]
        steps = iter([8.0, 16.0, 8.0, 16.0])

        def net_bytes():
            return {"eth0": (0, counters[0])}
        shown = []
        with mock.patch.object(s, "net_bytes", net_bytes), mock.patch.object(s, "cpu_times", lambda: [(100, 50)]), mock.patch.object(server.time, "time", lambda: t[0]):
            shown.append(s.sample_real()["net"])                  # erste Messung ohne Rate
            for mbit in steps:
                t[0] += 2.0
                counters[0] += int(mbit * 1e6 / 8 * 2.0)
                shown.append(s.sample_real()["net"])
        rates = [x["eth0"]["tx_mbit"] for x in shown[1:]]
        self.assertEqual(rates[0], 8.0)                           # erster Wert wie gemessen
        self.assertLess(max(rates) - min(rates), 5.0)             # roh wären es 8 Mbit/s Unterschied (nur vier Messungen, die Glättung hat sich noch nicht eingeschwungen)
        self.assertGreater(rates[1], 8.0)
        self.assertLess(rates[1], 16.0)


class Payload(unittest.TestCase):
    """Nutzdatenrate der Sendung (große Zahl neben "Live"): gesendete Pakete ohne Neuübertragungen mal 1316 Byte, über die Zeit der Kennzahlen-Datei, geglättet."""

    def setUp(self):
        self.s = server.Sampler(False)

    def st(self, mtime, sent, retr=0):
        return {"mtime": mtime, "sent_total": sent, "retrans_total": retr}

    def test_the_first_value_has_no_rate_yet(self):
        self.assertIsNone(self.s.payload_rate(self.st(1000.0, 5000)))

    def test_a_steady_stream_shows_the_payload(self):
        """Testbox: 11 950 Pakete in 10 s sind 12,6 Mbit/s; davon 104 Neuübertragungen sind 12,5 Mbit/s."""
        self.s.payload_rate(self.st(1000.0, 0))
        out = None
        for i in range(1, 11):
            out = self.s.payload_rate(self.st(1000.0 + i, 1195 * i, 10.4 * i))
        self.assertAlmostEqual(out, 12.47, delta=0.02)

    def test_retransmissions_are_not_counted_as_payload(self):
        """SRT zählt in sent_total auch die Neuübertragungen: bei 20 % davon ist die Nutzdatenrate ein Fünftel niedriger als die Senderate."""
        self.s.payload_rate(self.st(1000.0, 0, 0))
        out = None
        for i in range(1, 12):
            out = self.s.payload_rate(self.st(1000.0 + i, 1200 * i, 240 * i))
        self.assertAlmostEqual(out, 1200 * 0.8 * 1316 * 8 / 1e6, delta=0.05)

    def test_fractions_of_a_second_in_the_time_base_do_not_cause_outliers(self):
        """belacoder schreibt einmal je Sekunde; die ganze Sekunde in seinem Feld "time" kippt an der Grenze. Die Zeit der Datei (mtime) hat Bruchteile: 1000 Pakete/s ergeben immer 10,53 Mbit/s."""
        self.s.payload_rate(self.st(1000.998, 0))
        vals = []
        for i in range(1, 60):
            vals.append(self.s.payload_rate(self.st(1000.998 + i * 1.0007, 1000 * i * 1.0007)))
        self.assertLess(max(vals) - min(vals), 0.05)
        self.assertAlmostEqual(vals[-1], 10.53, delta=0.03)

    def test_the_value_is_calmed_like_the_network_rates(self):
        self.s.payload_rate(self.st(1000.0, 0))
        sent, t, vals = 0, 1000.0, []
        for i in range(20):
            t += 1
            sent += 600 if i % 2 == 0 else 1800          # 6,3 und 18,9 Mbit/s im Wechsel
            vals.append(self.s.payload_rate(self.st(t, sent)))
        tail = vals[10:]
        self.assertLess(max(tail) - min(tail), 4.5)       # roh 12,6 Unterschied

    def test_the_same_file_time_gives_the_last_value_again(self):
        self.s.payload_rate(self.st(1000.0, 0))
        a = self.s.payload_rate(self.st(1001.0, 1200))
        b = self.s.payload_rate(self.st(1001.0, 1200))
        self.assertEqual(a, b)

    def test_a_restart_of_belacoder_resets_the_counter_without_a_wrong_value(self):
        self.s.payload_rate(self.st(1000.0, 0))
        self.s.payload_rate(self.st(1001.0, 1200))
        self.assertIsNone(self.s.payload_rate(self.st(1002.0, 40)))                        # Zähler kleiner: neu angefangen
        self.assertIsNotNone(self.s.payload_rate(self.st(1003.0, 1240)))

    def test_no_stream_gives_none_and_forgets(self):
        self.s.payload_rate(self.st(1000.0, 0))
        self.s.payload_rate(self.st(1001.0, 1200))
        self.assertIsNone(self.s.payload_rate(None))
        self.assertIsNone(self.s.payload_rate(self.st(1500.0, 99999)))                     # nach der Pause beginnt es neu

    def test_broken_values_never_raise(self):
        for bad in ({"mtime": "x", "sent_total": 5}, {"mtime": 1000.0}, {"mtime": 1000.0, "sent_total": -1}, {"mtime": True, "sent_total": 5}, [], "text", 5):
            self.assertIsNone(self.s.payload_rate(bad), bad)
        self.s.payload_rate(self.st(1000.0, 0))
        self.assertIsNotNone(self.s.payload_rate({"mtime": 1001.0, "sent_total": 1200, "retrans_total": "x"}))   # kaputter Zähler der Neuübertragungen: ohne Abzug

    def test_a_long_gap_or_clock_jump_gives_no_wrong_value(self):
        """Springt die Uhr (NTP) um mehr als 15 s vor, darf nicht über die große Zeit gemittelt werden (fast null, rot)."""
        self.s.payload_rate(self.st(1000.0, 0))
        self.s.payload_rate(self.st(1001.0, 1200))
        self.assertIsNone(self.s.payload_rate(self.st(1101.0, 1200 + 600)))
        self.assertAlmostEqual(self.s.payload_rate(self.st(1102.0, 1800 + 600)), 6.32, delta=0.02)

    def test_an_outage_is_not_smoothed_out(self):
        self.s.payload_rate(self.st(1000.0, 0))
        sent = 0
        for i in range(1, 8):
            sent += 1200
            self.s.payload_rate(self.st(1000.0 + i, sent))
        a = self.s.payload_rate(self.st(1008.0, sent))                                      # erste Sekunde ohne Pakete
        b = self.s.payload_rate(self.st(1009.0, sent))                                      # zweite
        self.assertGreater(a, 3.0)
        self.assertEqual(b, 0.0)

    def test_the_sampler_reports_it_with_the_metrics(self):
        with mock.patch.object(server, "send_stats", lambda: self.st(1000.0, 0)), mock.patch.object(self.s, "net_bytes", lambda: {}), mock.patch.object(self.s, "cpu_times", lambda: [(100, 50)]):
            first = self.s.sample_real()["payload_mbit"]
        with mock.patch.object(server, "send_stats", lambda: self.st(1001.0, 1200)), mock.patch.object(self.s, "net_bytes", lambda: {}), mock.patch.object(self.s, "cpu_times", lambda: [(100, 50)]):
            second = self.s.sample_real()["payload_mbit"]
        self.assertIsNone(first)
        self.assertAlmostEqual(second, 12.64, delta=0.02)

    def test_the_cached_answer_is_a_copy(self):
        """Zwei Abfragen kurz hintereinander (zweiter Tab): ändert der Abrufer die Antwort, bleibt der Zwischenspeicher unberührt."""
        s = server.Sampler(False)
        with mock.patch.object(s, "sample_real", lambda: {"alerts": [], "net": {}}):
            a = s.sample()
            a["alerts"] = ["x"]
            a["neu"] = 1
            b = s.sample()
        self.assertEqual(b["alerts"], [])
        self.assertNotIn("neu", b)

    def test_send_stats_carry_the_file_time_with_fractions(self):
        import tempfile
        f = tempfile.mktemp()
        with open(f, "w") as h:
            h.write('{"time": 1791906585, "fps": 30.0, "sent_total": 5, "retrans_total": 1}')
        try:
            with mock.patch.object(server, "BC_STATS_FILE", f):
                st = server.send_stats()
        finally:
            os.unlink(f)
        self.assertIsInstance(st["mtime"], float)
        self.assertEqual(st["sent_total"], 5)
        self.assertNotIn("time", st)


class Page(unittest.TestCase):
    """Die Seite: große Zahl = Nutzdaten (wenn bekannt, sonst die Leitung), Summe der Zeilen = Leitung."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
            cls.page = f.read()

    def test_the_big_number_uses_the_payload_and_falls_back_to_the_wire(self):
        self.assertIn("return pay==null||isNaN(pay)?wire:Math.min(pay,wire);", self.page)                  # nie mehr als auf der Leitung gesendet wird
        self.assertIn("const tot=upShown(m), el=$(\"liveup\");", self.page)
        self.assertIn("Nutzdaten der Sendung (Video und Ton, ohne Kopfdaten und Neuübertragungen)", self.page)
        self.assertIn('drow("Bitrate",`<b>${f1(upShown(m))}</b>', self.page)                               # "Details" zeigt dieselbe Zahl
        self.assertIn("Math.max(0,upShown(m)*1000)", self.page)                                             # der Verlauf ebenso
        self.assertIn("const r=upTotal(m)/want", self.page)                                                 # der Signalbalken bleibt bei der Leitung

    def test_the_sum_row_says_it_is_the_wire(self):
        self.assertIn("Summe über ${nets.length} Netze, auf der Leitung (mit Kopfdaten und Neuübertragungen)", self.page)


if __name__ == "__main__":
    unittest.main()
