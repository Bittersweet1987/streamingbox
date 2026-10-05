"""Tests zu Issue #26 ("Details" im Status): Quellen der Kennzahlen (Wärmezonen, GPU/NPU, Speicherplatz, Datei von belacoder) und die Anzeige."""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import server  # noqa: E402

PAGE = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()


class Sources(unittest.TestCase):
    def test_devfreq_load_is_parsed(self):
        files = {"/sys/class/devfreq/fb000000.gpu/load": "12@300000000Hz\n", "/sys/class/devfreq/fdab0000.npu/load": "100@1000000000Hz\n",
                 "/sys/class/devfreq/dmc/load": "4@2112000000Hz\n"}
        with mock.patch("os.listdir", lambda p: ["dmc", "fb000000.gpu", "fdab0000.npu"]), mock.patch.object(server, "read", lambda p, d=None: files.get(p, d)):
            self.assertEqual(server.devfreq_loads(), {"gpu": {"load_pct": 12, "mhz": 300}, "npu": {"load_pct": None, "mhz": 1000}})   # NPU: der Regler meldet immer 100 %

    def test_devfreq_garbage_is_ignored(self):
        with mock.patch("os.listdir", lambda p: ["fb000000.gpu"]), mock.patch.object(server, "read", lambda p, d=None: "kaputt"):
            self.assertEqual(server.devfreq_loads(), {})

    def test_disk_usage(self):
        d = server.disk_usage(tempfile.gettempdir())
        self.assertTrue(d["total_gb"] > 0 and 0 <= d["used_pct"] <= 100)
        self.assertAlmostEqual(d["used_gb"] + d["free_gb"], d["total_gb"], delta=d["total_gb"] * 0.2)
        self.assertIsNone(server.disk_usage("/gibt/es/nicht"))

    def test_send_stats_need_a_fresh_file(self):
        f = os.path.join(tempfile.mkdtemp(), "s.json")
        data = {"time": 1, "fps": 29.9, "bitrate_kbps": 9000, "rtt_ms": 41.5, "send_mbps": 8.8, "snd_buf_pkts": 30, "snd_buf_ms": 20,
                "retrans_total": 50, "loss_total": 10, "drop_total": 0, "sent_total": 10000}
        with mock.patch.object(server, "BC_STATS_FILE", f):
            self.assertIsNone(server.send_stats())                                   # keine Datei: Sendung aus
            json.dump(data, open(f, "w"))
            s = server.send_stats()
            self.assertEqual((s["fps"], s["retrans_pct"], s["loss_pct"]), (29.9, 0.5, 0.1))
            old = time.time() - 30
            os.utime(f, (old, old))
            self.assertIsNone(server.send_stats())                                   # alte Datei zählt nicht
            open(f, "w").write("{kaputt")
            self.assertIsNone(server.send_stats())
            json.dump(dict(data, sent_total=0, loss_total=-1), open(f, "w"))
            s = server.send_stats()
            self.assertIsNone(s["retrans_pct"])                                      # kein Teilen durch null
            self.assertIsNone(s["loss_pct"])

    def test_demo_has_all_details(self):
        d = server.Sampler(demo=True).sample()["details"]
        self.assertEqual(sorted(d), ["accel", "disk", "send", "temps"])
        self.assertTrue(all(k in d["send"] for k in ("fps", "bitrate_kbps", "rtt_ms", "snd_buf_ms", "retrans_pct", "loss_pct")))
        self.assertIn("gpu", d["accel"])


class Page(unittest.TestCase):
    def test_details_are_collapsed_and_complete(self):
        i = PAGE.index('<details class="dsec" id="statdet">')
        block = PAGE[i:PAGE.index("</details>", i)]
        self.assertNotIn(" open", block.split(">")[0])                               # zunächst zugeklappt
        self.assertIn("<summary>Details</summary>", block)
        for needle in ('id="det_send"', 'id="det_cpu"', 'id="det_temp"', 'id="det_disk"'):
            self.assertEqual(block.count(needle), 1, needle)

    def test_all_requested_values_are_rendered(self):
        j = PAGE[PAGE.index("function renderDetails(m){"):PAGE.index("$(\"statdet\").addEventListener")]
        for text in ("Bitrate", "Laufzeit (Ping, RTT)", "Sendepuffer", "Neuübertragungen", "Paketverlust", "Encoder-Bilder pro Sekunde", "GPU", "NPU", "Kern ${i}",
                     "Belegt", "Frei", "°C"):
            self.assertIn(text, j, text)
        self.assertIn('if(!$("statdet").open) return;', j)                          # nur zeichnen, wenn aufgeklappt


if __name__ == "__main__":
    unittest.main()
