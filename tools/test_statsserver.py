"""Tests für die öffentliche Statistik: Schnappschuss (build_stats_snapshot), Freigabe (StatsShare), WebSocket-Server (StatsServer, Port 8781), die Trennung von der Oberfläche in
Remote und die Seite statistik.html. Gesprochen wird nur mit dem eigenen Rechner (127.0.0.1)."""
import base64
import hashlib
import json
import os
import re
import select
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
if not hasattr(os, "killpg"):                   # Windows (nur Entwicklungsrechner): die Module der Box brauchen Linux-Teile beim Import
    os.killpg = lambda *a, **k: None
    for _m in ("pwd", "grp", "fcntl", "termios"):
        sys.modules.setdefault(_m, mock.MagicMock())
import server  # noqa: E402


def sample_metrics():
    return {"time": 1792150000, "cpu": {"cores": [10.0, 20.0], "avg": 15.0, "max": 20.0, "freq_mhz": [1800, 2400]}, "temp_c": 61.5, "fan_pct": 40,
            "mem": {"used_pct": 22.0, "avail_mb": 6000, "total_mb": 7900},
            "net": {"eth1": {"rx_mbit": 0.1, "tx_mbit": 4.2}, "eth2": {"rx_mbit": 0.0, "tx_mbit": 0.0}, "tailscale0": {"rx_mbit": 0.0, "tx_mbit": 0.0}, "lo": {"rx_mbit": 1, "tx_mbit": 1},
                    "wlan0": {"rx_mbit": 0.5, "tx_mbit": 0.2}},
            "uplinks": {"eth1": {"state": "an", "srtt": 31}, "eth2": {"state": "aus", "srtt": None}},
            "payload_mbit": 4.0,
            "details": {"temps": [{"name": "CPU", "c": 61.5}, {"name": "GPU", "c": 55.0}],
                        "send": {"fps": 29.9, "bitrate_kbps": 8000, "rtt_ms": 31, "send_mbps": 8.1, "snd_buf_ms": 80, "snd_buf_pkts": 55, "retrans_total": 120, "loss_total": 14, "drop_total": 0,
                                 "sent_total": 90000, "loss_pct": 0.0, "retrans_pct": 0.4, "stream_id": "GEHEIM-STREAM-ID", "server": "srt.example.org"},
                        "accel": {"gpu": {"load_pct": 12, "mhz": 300}, "npu": {"load_pct": None, "mhz": 1000}},
                        "disk": {"total_gb": 62.1, "free_gb": 40.2, "used_gb": 21.9, "used_pct": 35.3, "pfad": "/geheim"}},
            "cameras": [{"key": "dji-b8a860", "name": "Haupt", "state": "live", "fps": 30.0, "mbit": 8.0, "battery": 77, "charging": False, "is_main": True, "parked": False,
                         "url": "rtmp://192.168.1.100/publish/dji-b8a860", "ip": "192.168.1.55"},
                        {"key": "cam2", "name": "Handy", "state": "offline", "fps": None, "mbit": None, "is_main": False, "parked": True}],
            "alerts": [{"level": "warn", "text": "Kamera Handy ausgefallen"}], "conn_names": {"eth1": "Vodafone"}, "secret": "NIE-AUSGEBEN"}


ALL = {g: True for g in server.StatsShare.GROUPS}


class Snapshot(unittest.TestCase):
    def snap(self, groups=None):
        return server.build_stats_snapshot(sample_metrics(), {"active": True, "stream_id": "GEHEIM-STREAM-ID"}, {"eth1": "Vodafone"}, groups or ALL, now=1792150001)

    def test_contains_the_status_values(self):
        s = self.snap()
        self.assertEqual((s["v"], s["time"], s["live"]), (1, 1792150001, True))
        by = {n["id"]: n for n in s["net"]}
        self.assertEqual(by["eth1"]["name"], "Vodafone")
        self.assertEqual((by["eth1"]["tx_mbit"], by["eth1"]["state"], by["eth1"]["srtt_ms"], by["eth1"]["uplink"]), (4.2, "an", 31, True))
        self.assertEqual(by["eth2"]["state"], "aus")
        self.assertFalse(by["wlan0"]["uplink"])
        self.assertEqual(s["uplink_tx_mbit"], 4.2)
        self.assertEqual(s["send"]["bitrate_kbps"], 8000)
        self.assertEqual(s["cpu"]["avg"], 15.0)
        self.assertEqual((s["cpu"]["mem_used_pct"], s["cpu"]["mem_avail_mb"], s["cpu"]["mem_total_mb"]), (22.0, 6000, 7900))
        self.assertEqual(s["temp"]["max_c"], 61.5)
        self.assertEqual([z["name"] for z in s["temp"]["zones"]], ["CPU", "GPU"])
        self.assertEqual((s["send"]["buf_pkts"], s["send"]["retrans_total"], s["send"]["loss_total"], s["send"]["drop_total"], s["send"]["sent_total"]), (55, 120, 14, 0, 90000))
        self.assertEqual(s["send"]["shown_mbit"], 4.0)                    # wie neben "Live": nie mehr als auf der Leitung gesendet wird (Summe der Sendewege 4,2, Nutzdaten 4,0)
        self.assertEqual(s["cores"]["load"], [10.0, 20.0])
        self.assertEqual(s["cores"]["freq_mhz"], [1800, 2400])
        self.assertEqual(s["cores"]["gpu"], {"load_pct": 12, "mhz": 300})
        self.assertEqual(s["cores"]["npu"], {"load_pct": None, "mhz": 1000})
        self.assertEqual(s["disk"], {"used_gb": 21.9, "free_gb": 40.2, "total_gb": 62.1, "used_pct": 35.3})
        self.assertEqual([c["name"] for c in s["cameras"]], ["Haupt", "Handy"])
        self.assertEqual(s["cameras"][0]["battery"], 77)
        self.assertTrue(s["cameras"][1]["parked"])
        self.assertEqual(s["alerts"][0]["text"], "Kamera Handy ausgefallen")

    def test_uplinks_come_first_and_internal_cards_are_left_out(self):
        ids = [n["id"] for n in self.snap()["net"]]
        self.assertEqual(ids[:2], ["eth1", "eth2"])
        self.assertNotIn("lo", ids)
        self.assertNotIn("tailscale0", ids)

    def test_nothing_secret_is_in_the_snapshot(self):
        text = json.dumps(self.snap())
        for secret in ("GEHEIM-STREAM-ID", "srt.example.org", "192.168.1.", "rtmp://", "dji-b8a860", "NIE-AUSGEBEN", "stream_id", '"key"', '"url"', '"ip"', "/geheim", "pfad"):
            self.assertNotIn(secret, text, secret)

    def test_groups_switch_parts_off(self):
        s = self.snap({g: False for g in server.StatsShare.GROUPS})
        self.assertEqual(sorted(s), ["live", "time", "v"])
        s = self.snap({**{g: False for g in server.StatsShare.GROUPS}, "cameras": True})
        self.assertEqual(sorted(s), ["cameras", "live", "time", "v"])

    def test_every_block_of_the_status_page_can_be_chosen(self):
        self.assertEqual(sorted(server.StatsShare.GROUPS), ["alerts", "cameras", "cores", "cpu", "disk", "net", "send", "temp"])
        for g in server.StatsShare.GROUPS:                                  # jeder Block lässt sich einzeln übertragen
            only = {x: x == g for x in server.StatsShare.GROUPS}
            keys = set(self.snap(only)) - {"v", "time", "live"}
            self.assertTrue(keys, g)
        self.assertEqual(set(self.snap({**{x: False for x in server.StatsShare.GROUPS}, "disk": True})) - {"v", "time", "live"}, {"disk"})
        self.assertEqual(set(self.snap({**{x: False for x in server.StatsShare.GROUPS}, "cores": True})) - {"v", "time", "live"}, {"cores"})

    def test_older_pages_still_find_what_they_use(self):
        """Schon heruntergeladene statistik.html-Dateien und Seitenadressen lesen diese Felder; sie dürfen sich nie ändern (neue kommen nur dazu)."""
        s = self.snap()
        self.assertEqual(s["v"], 1)
        self.assertTrue({"fps", "bitrate_kbps", "rtt_ms", "send_mbps", "buffer_ms", "loss_pct", "retrans_pct", "payload_mbit"} <= set(s["send"]))
        self.assertTrue({"id", "name", "rx_mbit", "tx_mbit", "uplink", "state", "srtt_ms"} <= set(s["net"][0]))
        self.assertIn("uplink_tx_mbit", s)
        self.assertTrue({"avg", "max", "mem_used_pct", "mem_avail_mb"} <= set(s["cpu"]))
        self.assertTrue({"max_c", "fan_pct", "zones"} <= set(s["temp"]))
        self.assertTrue({"name", "state", "fps", "mbit", "battery", "charging", "main", "parked"} <= set(s["cameras"][0]))
        self.assertTrue({"level", "text"} <= set(s["alerts"][0]))

    def test_groups_missing_in_an_older_settings_file_count_as_on(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "stats-share.json"), "w") as f:
            json.dump({"enabled": True, "token": "x" * 32, "groups": {"send": True, "net": False, "cpu": True, "temp": True, "cameras": True, "alerts": True}}, f)
        g = server.StatsShare(d).groups()
        self.assertEqual((g["cores"], g["disk"], g["net"]), (True, True, False))

    def test_broken_input_does_not_raise(self):
        s = server.build_stats_snapshot(None, None, None, ALL, now=5)
        self.assertEqual((s["live"], s["net"]), (False, []))
        s = server.build_stats_snapshot({"net": {"eth1": "kaputt"}, "cameras": ["x", 3], "cpu": {"cores": "x"}}, {}, {}, ALL)
        self.assertEqual((s["net"], s["cameras"]), ([], []))

    def test_text_values_are_cut(self):
        m = sample_metrics()
        m["cameras"][0]["name"] = "X" * 500
        m["alerts"] = [{"level": "warn", "text": "T" * 900}]
        s = server.build_stats_snapshot(m, {}, {}, ALL)
        self.assertEqual((len(s["cameras"][0]["name"]), len(s["alerts"][0]["text"])), (40, 160))


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.s = server.StatsShare(self.dir)

    def test_off_by_default_without_token(self):
        st = self.s.status()
        self.assertEqual((st["enabled"], st["token"]), (False, ""))
        self.assertEqual(self.s.check("irgendwas", "1.2.3.4"), "bad")

    def test_enabling_creates_a_random_token_and_it_is_checked(self):
        st = self.s.update(enabled=True)
        self.assertTrue(re.match(r"^[A-Za-z0-9_-]{32}$", st["token"]))
        self.assertEqual(self.s.check(st["token"], "1.2.3.4"), "ok")
        for bad in (st["token"] + "x", "", None):
            self.assertEqual(self.s.check(bad, "1.2.3.4"), "bad")

    def test_disabled_share_refuses_a_valid_token_but_keeps_it(self):
        tok = self.s.update(enabled=True)["token"]
        self.s.update(enabled=False)
        self.assertEqual(self.s.check(tok, "1.2.3.4"), "bad")
        self.assertEqual(self.s.status()["token"], tok)

    def test_off_and_on_again_keeps_the_same_address(self):
        tok = self.s.update(enabled=True)["token"]
        for _ in range(3):
            self.s.update(enabled=False)
            self.assertEqual(server.StatsShare(self.dir).update(enabled=True)["token"], tok)

    def test_lost_or_broken_settings_file_keeps_the_address(self):
        tok = self.s.update(enabled=True)["token"]
        os.remove(os.path.join(self.dir, "stats-share.json"))                      # Datei weg
        self.assertEqual(server.StatsShare(self.dir).update(enabled=True)["token"], tok)
        with open(os.path.join(self.dir, "stats-share.json"), "w") as f:
            f.write("{kaputt")                                                      # Datei kaputt
        self.assertEqual(server.StatsShare(self.dir).update(enabled=True)["token"], tok)

    def test_new_token_replaces_the_backup_too(self):
        old = self.s.update(enabled=True)["token"]
        new = self.s.update(new_token=True)["token"]
        os.remove(os.path.join(self.dir, "stats-share.json"))
        self.assertEqual(server.StatsShare(self.dir).update(enabled=True)["token"], new)
        self.assertNotEqual(old, new)

    def test_new_token_invalidates_the_old_one(self):
        old = self.s.update(enabled=True)["token"]
        new = self.s.update(new_token=True)["token"]
        self.assertNotEqual(old, new)
        self.assertEqual((self.s.check(old, "1.2.3.4"), self.s.check(new, "1.2.3.4")), ("bad", "ok"))

    def test_settings_survive_a_restart_and_the_file_is_private(self):
        tok = self.s.update(enabled=True, groups={"cameras": False})["token"]
        st = server.StatsShare(self.dir).status()
        self.assertEqual((st["enabled"], st["token"], st["groups"]["cameras"], st["groups"]["net"]), (True, tok, False, True))
        if os.name != "nt":
            self.assertEqual(oct(os.stat(os.path.join(self.dir, "stats-share.json")).st_mode & 0o777), "0o600")

    def test_garbage_in_the_file_is_ignored(self):
        with open(os.path.join(self.dir, "stats-share.json"), "w") as f:
            json.dump({"enabled": "ja", "token": "kurz", "groups": {"net": "nein", "x": 1}}, f)
        st = server.StatsShare(self.dir).status()
        self.assertEqual((st["enabled"], st["token"], st["groups"]["net"]), (False, "", True))

    def test_input_is_checked(self):
        for bad in ({"enabled": "ja"}, {"groups": {"unbekannt": True}}, {"groups": {"net": "ja"}}, {"groups": [1]}):
            with self.assertRaises(ValueError):
                self.s.update(**bad)

    def test_wrong_tokens_and_many_requests_are_limited_per_sender(self):
        tok = self.s.update(enabled=True)["token"]
        for _ in range(self.s.MAX_FAILS):
            self.assertEqual(self.s.check("falsch", "9.9.9.9"), "bad")
        self.assertEqual(self.s.check("falsch", "9.9.9.9"), "limit")
        self.assertEqual(self.s.check(tok, "9.9.9.9"), "limit")
        self.assertEqual(self.s.check(tok, "8.8.8.8"), "ok")
        for _ in range(self.s.MAX_HITS):
            self.s.check(tok, "7.7.7.7")
        self.assertEqual(self.s.check(tok, "7.7.7.7"), "limit")

    def test_limits_relax_over_time(self):
        tok = self.s.update(enabled=True)["token"]
        t = [1000.0]
        with mock.patch.object(server.time, "monotonic", lambda: t[0]):
            for _ in range(self.s.MAX_HITS):
                self.s.check(tok, "7.7.7.7")
            self.assertEqual(self.s.check(tok, "7.7.7.7"), "limit")
            t[0] += self.s.WINDOW_S + 1
            self.assertEqual(self.s.check(tok, "7.7.7.7"), "ok")

    def test_snapshot_is_cached_for_a_second(self):
        calls, t = [], [50.0]
        with mock.patch.object(server.time, "monotonic", lambda: t[0]):
            for _ in range(5):
                self.s.snapshot(lambda: calls.append(1) or {"n": len(calls)})
            self.assertEqual(len(calls), 1)
            t[0] += 1.1
            self.assertEqual(self.s.snapshot(lambda: calls.append(1) or {"n": len(calls)}), {"n": 2})


class Client:
    """Ein kleiner WebSocket-Client für die Tests (maskierte Rahmen wie ein Browser)."""

    def __init__(self, port, path, headers=None, ws=True):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        h = "GET %s HTTP/1.1\r\nHost: x\r\n" % path
        if ws:
            h += "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: %s\r\n" % key
        for k, v in (headers or {}).items():
            h += "%s: %s\r\n" % (k, v)
        self.sock.sendall((h + "\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = self.sock.recv(4096)
            if not d:
                break
            buf += d
        head, _, self.rest = buf.partition(b"\r\n\r\n")
        self.head = head.decode("latin-1")
        self.status = int(self.head.split()[1])
        self.key = key
        if self.status != 101 and "Content-Length" in self.head:                 # Antwort mit Inhalt: bis zum Ende lesen
            n = int(re.search(r"Content-Length: (\d+)", self.head).group(1))
            while len(self.rest) < n:
                d = self.sock.recv(4096)
                if not d:
                    break
                self.rest += d

    def accept_ok(self):
        want = base64.b64encode(hashlib.sha1(self.key.encode() + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()).decode()
        return ("Sec-WebSocket-Accept: " + want) in self.head

    def recv_frame(self, timeout=5):
        self.sock.settimeout(timeout)
        while True:
            if len(self.rest) >= 2:
                n = self.rest[1] & 0x7f
                pos = 2
                if n == 126:
                    if len(self.rest) < 4:
                        pass
                    else:
                        n, pos = struct.unpack(">H", self.rest[2:4])[0], 4
                if len(self.rest) >= pos + n and not (self.rest[1] & 0x7f == 126 and len(self.rest) < 4):
                    op, data = self.rest[0] & 0x0f, self.rest[pos:pos + n]
                    self.rest = self.rest[pos + n:]
                    return op, data
            d = self.sock.recv(4096)
            if not d:
                return None
            self.rest += d

    def send(self, op, payload=b"", masked=True):
        mask = os.urandom(4)
        body = bytes(c ^ mask[i % 4] for i, c in enumerate(payload)) if masked else payload
        self.sock.sendall(bytes([0x80 | op, (0x80 if masked else 0) | len(payload)]) + (mask if masked else b"") + body)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Serving(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.share = server.StatsShare(self.dir)
        self.token = self.share.update(enabled=True)["token"]
        self.calls = 0

        def snap():
            self.calls += 1
            return {"v": 1, "n": self.calls}
        self.srv = server.StatsServer(self.share, snap, port=0)
        self.assertTrue(self.srv.start())
        self.addCleanup(self.srv.stop)
        self.port = self.srv.port
        self.clients = []

    def open(self, path=None, **kw):
        c = Client(self.port, path or "/ws?t=" + self.token, **kw)
        self.clients.append(c)
        self.addCleanup(c.close)
        return c

    def test_health_page_and_unknown_paths(self):
        c = self.open("/health", ws=False)
        self.assertEqual((c.status, c.rest), (200, b"ok"))
        c = self.open("/", ws=False)
        self.assertEqual(c.status, 200)
        self.assertIn(b"WebSocket", c.rest)
        self.assertNotIn(b"__STATS_URL__", c.rest)                        # die Seite leitet die Adresse selbst ab
        for path in ("/api/metrics", "/api/auth", "/etc/passwd", "/ws2", "/stats2", "//ws"):
            self.assertEqual(self.open(path, ws=False).status, 404, path)

    def test_stats_needs_the_token(self):
        self.assertEqual(self.open("/stats", ws=False).status, 404)
        self.assertEqual(self.open("/stats?t=falsch", ws=False).status, 404)
        c = self.open("/stats?t=" + self.token, ws=False)
        self.assertEqual(c.status, 200)
        self.assertEqual(json.loads(c.rest), {"v": 1, "n": 1})

    def test_websocket_pushes_every_second(self):
        c = self.open()
        self.assertEqual(c.status, 101)
        self.assertTrue(c.accept_ok())
        t0 = time.time()
        got = []
        for _ in range(3):
            op, data = c.recv_frame()
            self.assertEqual(op, 1)
            got.append(json.loads(data)["n"])
        self.assertEqual(got, sorted(got))
        self.assertLess(time.time() - t0, 4.5)
        self.assertGreater(time.time() - t0, 1.5)                         # im Takt von etwa einer Sekunde, nicht schneller

    def test_every_parameter_is_limited_to_1_to_10(self):
        self.assertEqual(self.open("/ws?t=%s&every=0" % self.token).status, 101)
        self.assertEqual(self.open("/ws?t=%s&every=abc" % self.token).status, 101)

    def test_wrong_token_or_missing_upgrade_is_refused(self):
        self.assertEqual(self.open("/ws?t=falsch").status, 404)
        self.assertEqual(self.open("/ws").status, 404)
        self.assertEqual(self.open("/ws?t=" + self.token, ws=False).status, 400)

    def test_at_most_three_viewers(self):
        cs = [self.open() for _ in range(3)]
        self.assertEqual([c.status for c in cs], [101, 101, 101])
        deadline = time.time() + 3
        while self.srv.clients() < 3 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.srv.clients(), 3)
        self.assertEqual(self.open().status, 503)
        cs[0].send(8, b"\x03\xe8")
        for _ in range(4):
            fr = cs[0].recv_frame()
            if fr is None or fr[0] == 8:
                break
        cs[0].close()
        deadline = time.time() + 3
        while self.srv.clients() > 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.open().status, 101)                          # ein Platz ist wieder frei

    def test_ping_is_answered_and_close_ends_the_connection(self):
        c = self.open()
        c.recv_frame()
        c.send(9, b"hallo")
        for _ in range(3):
            op, data = c.recv_frame()
            if op == 10:
                self.assertEqual(data, b"hallo")
                break
        else:
            self.fail("keine Antwort auf den Ping")
        c.send(8, b"\x03\xe8")
        for _ in range(3):
            fr = c.recv_frame()
            if fr is None or fr[0] == 8:
                break
        deadline = time.time() + 3
        while self.srv.clients() > 0 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.srv.clients(), 0)

    def test_unmasked_frames_from_the_client_are_refused(self):
        c = self.open()
        c.recv_frame()
        c.send(1, b"x", masked=False)
        closing = None
        for _ in range(4):
            fr = c.recv_frame()
            if fr is None or fr[0] == 8:
                closing = fr
                break
        self.assertTrue(closing is None or struct.unpack(">H", closing[1][:2])[0] == 1002)

    def test_switching_the_share_off_closes_open_connections(self):
        c = self.open()
        c.recv_frame()
        self.share.update(enabled=False)
        for _ in range(4):
            fr = c.recv_frame(timeout=4)
            if fr is None or fr[0] == 8:
                if fr:
                    self.assertEqual(struct.unpack(">H", fr[1][:2])[0], 1001)
                break
        else:
            self.fail("Verbindung blieb offen")
        self.assertEqual(self.open().status, 404)

    def test_many_wrong_tokens_are_rate_limited(self):
        codes = [self.open("/ws?t=x%d" % i).status for i in range(self.share.MAX_FAILS + 3)]
        self.assertEqual(codes[:self.share.MAX_FAILS], [404] * self.share.MAX_FAILS)
        self.assertEqual(set(codes[self.share.MAX_FAILS:]), {429})
        self.assertEqual(self.open().status, 429)                          # auch der richtige Token zählt für diesen Absender dann nicht mehr

    def test_forwarded_address_is_used_for_the_limits_only_behind_the_proxy(self):
        for i in range(self.share.MAX_FAILS):
            self.open("/ws?t=x%d" % i, headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(self.open("/ws?t=y", headers={"X-Forwarded-For": "203.0.113.9"}).status, 429)
        self.assertEqual(self.open(headers={"X-Forwarded-For": "203.0.113.10"}).status, 101)      # ein anderer Besucher ist nicht betroffen

    def test_frames_are_built_and_parsed_correctly(self):
        f = server.StatsServer.frame
        self.assertEqual(f(1, b"ab"), b"\x81\x02ab")
        self.assertEqual(f(1, b"x" * 300)[:4], b"\x81\x7e\x01\x2c")
        buf = bytearray(b"\x89\x80\x00\x00\x00\x00")
        self.assertEqual(server.StatsServer.read_frame(buf), (9, b""))
        self.assertEqual(len(buf), 0)
        partial = bytearray(b"\x89\x85\x01\x02\x03\x04ab")
        self.assertIsNone(server.StatsServer.read_frame(partial))
        with self.assertRaises(ValueError):
            server.StatsServer.read_frame(bytearray(b"\x81\x01x"))               # nicht maskiert
        with self.assertRaises(ValueError):
            server.StatsServer.read_frame(bytearray(b"\x01\x80\x00\x00\x00\x00"))     # in Teile zerlegt
        with self.assertRaises(ValueError):
            server.StatsServer.read_frame(bytearray(b"\x81\xfe\x10\x00\x00\x00\x00\x00"))    # zu groß


class CheapLiveCheck(unittest.TestCase):
    """Die Statistik fragt jede Sekunde: "läuft die Sendung" darf kein systemctl und keine Prozessliste brauchen."""

    def make(self, content):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        sc = object.__new__(server.SendControl)
        sc.demo = False
        sc.STATUS = os.path.join(d, "status.json")
        if content is not None:
            with open(sc.STATUS, "w") as f:
                json.dump(content, f)
        return sc

    def test_fresh_running_status_is_live(self):
        self.assertTrue(self.make({"time": time.time() - 2, "state": "running"}).live_fast())

    def test_old_missing_or_stopping_status_is_not_live(self):
        self.assertFalse(self.make({"time": time.time() - 60, "state": "running"}).live_fast())
        self.assertFalse(self.make(None).live_fast())
        self.assertFalse(self.make({"time": time.time(), "state": "stopping"}).live_fast())
        self.assertFalse(self.make({"time": "x"}).live_fast())

    def test_snapshot_uses_the_cheap_check_and_the_light_metrics(self):
        body = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read().split("def stats_snapshot_now", 1)[1].split("def stats_status", 1)[0]
        self.assertIn("metrics_data(light=True)", body)
        self.assertIn("live_fast()", body)
        self.assertNotIn("send.status()", body)


class MeasuredFpsInStatus(unittest.TestCase):
    """Kameras ohne Bildrate in den Metadaten (Handys) bekommen die von belacoder gemessene; DJI-Kameras weiter die eingestellte."""

    def make(self, content):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        sc = object.__new__(server.SendControl)
        sc.demo = False
        sc.STATUS = os.path.join(d, "status.json")
        if content is not None:
            with open(sc.STATUS, "w") as f:
                json.dump(content, f)
        return sc

    def test_measured_rates_come_from_the_status_file_of_the_sender(self):
        sc = self.make({"time": time.time() - 1, "failover": {"fps": {"cam2": 29.9, "dji-a": 30.0, "x": 0, "y": "abc"}}})
        self.assertEqual(sc.measured_fps(), {"cam2": 29.9, "dji-a": 30.0})

    def test_old_or_missing_status_gives_nothing(self):
        self.assertEqual(self.make({"time": time.time() - 60, "failover": {"fps": {"cam2": 29.9}}}).measured_fps(), {})
        self.assertEqual(self.make(None).measured_fps(), {})
        self.assertEqual(self.make({"time": time.time(), "failover": None}).measured_fps(), {})
        self.assertEqual(self.make({"time": "x"}).measured_fps(), {})

    def test_wiring(self):
        s = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        body = s.split("def metrics_data", 1)[1].split("return m", 1)[0]
        self.assertIn("measured_fps()", body)
        self.assertIn("fps_measured", body)
        self.assertLess(body.index("measured_fps()"), body.index("fps_for_key"))       # erst gemessen, dann die eingestellte Rate der DJI-Kamera
        send = open(os.path.join(ROOT, "pipbox_send.py"), encoding="utf-8").read()
        self.assertIn("pipbox_live.read_fps()", send)


class ProbedFps(unittest.TestCase):
    """Bildrate von Handys, bevor die Sendung läuft: der Stream wird kurz gelesen und es werden Bilder gezählt."""

    OUT = ("/GstPipeline:pipeline0/GstFPSDisplaySink:fpsdisplaysink0: last-message = rendered: 31, dropped: 0, current: 31.96, average: 31.96" + chr(10) +
           "/GstPipeline:pipeline0/GstFPSDisplaySink:fpsdisplaysink0: last-message = rendered: 62, dropped: 0, current: 28.99, average: 30.48" + chr(10) +
           "/GstPipeline:pipeline0/GstFPSDisplaySink:fpsdisplaysink0: last-message = rendered: 93, dropped: 0, current: 30.99, average: 30.65" + chr(10))

    def wait(self, pr, key, fps):
        for _ in range(100):
            time.sleep(0.02)
            v = pr.get(key)
            if v == fps:
                return v
        return pr.get(key)

    def test_parse_averages_the_seconds_after_the_first_and_rounds_to_a_common_rate(self):
        self.assertEqual(server.FpsProbe.parse(self.OUT), 30.0)                    # (28,99 + 30,99) / 2 = 29,99; die erste Sekunde zählt das Anlaufen mit
        line = "rendered: %d, dropped: 0, current: %s, average: 1.0" + chr(10)
        self.assertEqual(server.FpsProbe.parse(line % (20, "25.0") + line % (80, "59.2") + line % (140, "59.6")), 60.0)
        self.assertEqual(server.FpsProbe.parse(line % (20, "25.0") + line % (44, "24.1") + line % (68, "24.2")), 24.0)
        self.assertEqual(server.FpsProbe.parse(line % (20, "25.0") + line % (35, "15.0") + line % (50, "15.04")), 15.0)
        self.assertEqual(server.FpsProbe.parse(line % (20, "25.0") + line % (41, "20.5") + line % (62, "20.5")), 20.5)

    def test_too_few_frames_or_garbage_give_nothing(self):
        line = "rendered: %d, dropped: 0, current: %s, average: 1.0" + chr(10)
        self.assertIsNone(server.FpsProbe.parse(line % (10, "30.0")))               # nur eine Messung
        self.assertIsNone(server.FpsProbe.parse(line % (10, "30.0") + line % (20, "30.0")))     # zu wenige Bilder
        self.assertIsNone(server.FpsProbe.parse(line % (50, "30.0") + line % (50, "0.0")))      # steht
        self.assertIsNone(server.FpsProbe.parse(""))
        self.assertIsNone(server.FpsProbe.parse(None))
        self.assertIsNone(server.FpsProbe.parse("kein Stream da"))

    def test_get_measures_in_the_background_and_caches(self):
        calls = []
        pr = server.FpsProbe(runner=lambda k: calls.append(k) or self.OUT)
        self.assertIsNone(pr.get("cam2"))                                  # blockiert nie: der erste Aufruf liefert noch nichts
        self.assertEqual(self.wait(pr, "cam2", 30.0), 30.0)
        for _ in range(5):
            self.assertEqual(pr.get("cam2"), 30.0)
        self.assertEqual(calls, ["cam2"])                                  # innerhalb der Gültigkeit keine neue Messung

    def test_only_one_measurement_at_a_time(self):
        started, release = threading.Event(), threading.Event()

        def slow(k):
            started.set()
            release.wait(3)
            return self.OUT
        pr = server.FpsProbe(runner=slow)
        pr.get("cam2")
        self.assertTrue(started.wait(2))
        pr.get("cam3")                                                      # läuft schon eine: diese Kamera wartet auf die nächste Runde
        self.assertEqual(pr.cache.get("cam3"), None)
        release.set()
        self.assertEqual(self.wait(pr, "cam2", 30.0), 30.0)

    def test_a_failed_measurement_keeps_a_young_value_and_is_retried_soon(self):
        outs = [self.OUT, "nichts"]
        pr = server.FpsProbe(runner=lambda k: outs.pop(0))
        pr.get("cam2")
        self.assertEqual(self.wait(pr, "cam2", 30.0), 30.0)
        pr.cache["cam2"] = (time.monotonic() - pr.TTL_S - 1, 30.0)           # abgelaufen: wird neu gemessen
        pr.get("cam2")
        deadline = time.time() + 2
        while pr.busy and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(pr.get("cam2"), 30.0)                              # die Messung scheiterte, der junge Wert bleibt

    def test_keep_forgets_cameras_that_are_not_live(self):
        pr = server.FpsProbe(runner=lambda k: self.OUT)
        pr.cache = {"cam2": (time.monotonic(), 30.0), "cam3": (time.monotonic(), 25.0)}
        pr.keep({"cam2"})
        self.assertEqual(list(pr.cache), ["cam2"])

    def test_unsafe_keys_and_demo_are_never_measured(self):
        calls = []
        pr = server.FpsProbe(runner=lambda k: calls.append(k) or self.OUT)
        for bad in ("../x", "a b", "", None, 5, "cam2; rm -rf /"):
            self.assertIsNone(pr.get(bad))
        self.assertIsNone(server.FpsProbe(demo=True, runner=lambda k: calls.append(k) or self.OUT).get("cam2"))
        time.sleep(0.2)
        self.assertEqual(calls, [])

    def test_the_command_reads_one_stream_without_a_shell_and_counts_frames_only(self):
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read().split("class FpsProbe", 1)[1].split("class LayoutStore", 1)[0]
        self.assertIn('"fpsdisplaysink"', src)
        self.assertIn('"h264parse"', src)
        self.assertNotIn("shell=True", src)
        self.assertNotIn("avdec", src)                                      # kein Dekodieren

    def test_status_uses_it_for_cameras_without_a_rate(self):
        body = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read().split("def metrics_data", 1)[1].split("return m", 1)[0]
        self.assertIn("self.fpsprobe.get(", body)
        self.assertIn("self.fpsprobe.keep(", body)
        self.assertLess(body.index("measured_fps()"), body.index("fps_for_key"))
        self.assertLess(body.index("fps_for_key"), body.index("self.fpsprobe.get("))      # erst nginx, dann belacoder, dann die eingestellte Rate der DJI-Kamera, zuletzt die Messung


class RemoteSplit(unittest.TestCase):
    """Die öffentliche Statistik (Port 8443) und die Oberfläche (Port 443) sind getrennt: jede hat ihren eigenen Zustand."""

    def make(self, serve_cfg):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        r = server.Remote(d, False)
        status = {"BackendState": "Running", "Self": {"DNSName": "irl4you-box.snake-kochab.ts.net.", "Online": True}, "TailscaleIPs": ["100.64.0.1"], "CurrentTailnet": {"Name": "snake-kochab.ts.net"}}
        r._ts = lambda *a: status if a[0] == "status" else serve_cfg
        patch = mock.patch.object(server.shutil, "which", lambda n: "/usr/bin/tailscale")
        patch.start()
        self.addCleanup(patch.stop)
        return r

    UI = {"Web": {"irl4you-box.snake-kochab.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}}}}}
    ST = {"irl4you-box.snake-kochab.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8781"}}}}

    def test_only_statistics_public(self):
        cfg = {"Web": dict(self.UI["Web"], **self.ST), "AllowFunnel": {"irl4you-box.snake-kochab.ts.net:8443": True}}
        st = self.make(cfg).status()
        self.assertFalse(st["funnel"])                                   # die Oberfläche ist NICHT öffentlich
        self.assertTrue(st["stats"])
        self.assertEqual(st["stats_base"], "https://irl4you-box.snake-kochab.ts.net:8443")
        self.assertTrue(st["serve"])                                     # privat im Tailscale-Netz weiter freigegeben

    def test_only_interface_public(self):
        cfg = {"Web": dict(self.UI["Web"], **self.ST), "AllowFunnel": {"irl4you-box.snake-kochab.ts.net:443": True}}
        st = self.make(cfg).status()
        self.assertTrue(st["funnel"])
        self.assertFalse(st["stats"])

    def test_nothing_public(self):
        st = self.make({"Web": self.UI["Web"]}).status()
        self.assertEqual((st["funnel"], st["stats"], st["stats_base"]), (False, False, ""))

    def test_requests_for_the_statistics_are_checked(self):
        r = self.make({"Web": self.UI["Web"]})
        r.demo = False
        with mock.patch.object(r, "status", return_value={"helper_installed": True, "state": "idle", "installed": True, "connected": True, "funnel": False, "stats": False}):
            with self.assertRaises(ValueError):
                r.request("stats_on", True, False)                         # ohne ausdrückliche Bestätigung
            with self.assertRaises(ValueError):
                r.request("stats_off", True)                               # ist gar nicht an
        with mock.patch.object(r, "status", return_value={"helper_installed": True, "state": "idle", "installed": True, "connected": False, "funnel": False, "stats": False}):
            with self.assertRaisesRegex(ValueError, "Tailscale"):
                r.request("stats_on", True, True)
        self.assertIn("stats_on", server.Remote.ACTIONS)
        self.assertIn("stats_off", server.Remote.ACTIONS)


class Wiring(unittest.TestCase):
    def src(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def test_no_public_statistics_route_on_the_interface_port(self):
        s = self.src("server.py")
        self.assertNotIn('path == "/api/stats"', s)                      # die Statistik gibt es nur auf dem eigenen Port 8781
        i, j = s.index('if path == "/api/statsshare":'), s.index("if not self.authed():", s.index('if path == "/api/swprogress":'))
        self.assertGreater(i, j)                                         # Einstellungen und Download nur angemeldet
        self.assertIn('if path == "/api/statsshare":', s.split("def do_POST", 1)[1])

    def test_metrics_endpoint_still_uses_the_same_data(self):
        s = self.src("server.py")
        self.assertIn("return self.reply(200, self.metrics_data())", s)
        self.assertIn("def metrics_data(self, light=False):", s)

    def test_server_listens_on_loopback_only_and_starts_with_the_interface(self):
        s = self.src("server.py")
        self.assertIn('HOST, PORT = "127.0.0.1", 8781', s)
        self.assertIn("Handler.statsserver.start()", s)

    def test_widget_download_uses_the_public_websocket_address(self):
        body = self.src("server.py").split("def stats_widget", 1)[1].split("def do_GET", 1)[0]
        self.assertIn('st["ws_url"]', body)
        self.assertIn("__STATS_URL__", body)
        self.assertIn("attachment", body)

    def test_enabling_asks_the_helper_and_disabling_keeps_working_if_it_fails(self):
        body = self.src("server.py").split('if path == "/api/statsshare":', 2)[2].split("return self.reply(200, self.stats_status())", 1)[0]
        self.assertIn('self.remote.request("stats_on", True, True)', body)
        self.assertLess(body.index("stats_on"), body.index("self.statsshare.update(True)"))     # erst die Freigabe, dann der Schalter: scheitert sie, bleibt alles aus

    def test_the_helper_unit_files_are_unchanged_in_shape(self):
        self.assertIn("pipbox-remote.py heal", self.src("install", "pipbox-remote-heal.service"))


class Page(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "web", "statistik.html"), encoding="utf-8") as f:
            self.html = f.read()

    def test_template_has_the_placeholder_once(self):
        self.assertEqual(self.html.count("__STATS_URL__"), 1)

    def test_page_is_one_self_contained_file(self):
        code = re.sub(r"<!--.*?-->", "", self.html, flags=re.S)                       # die Anleitung im Kommentar nennt Beispieladressen
        self.assertNotRegex(code, r"(?i)<script[^>]+src=|<link[^>]+href=|@import|https?://(?!www\.w3\.org)")

    def test_page_uses_a_websocket_and_reconnects_politely(self):
        for needle in ("new WebSocket(", "document.hidden", "visibilitychange", "Math.pow(2", "disconnect()"):
            self.assertIn(needle, self.html, needle)
        self.assertNotIn("setInterval(() => fetch", self.html)

    def test_page_accepts_its_settings_in_the_address(self):
        for needle in ('Q.get("url")', 'Q.has("every")', 'Q.has("show")', 'Q.has("names")', 'Q.has("bg")', 'Q.has("speed")', "Q.get(\"t\")"):
            self.assertIn(needle, self.html, needle)

    def test_page_never_writes_server_text_as_html(self):
        script = self.html.split("<script>", 1)[1]
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("document.write", script)

    def test_the_interface_has_the_section_inside_the_remote_access_card(self):
        idx = self.src_index()
        card = idx.split('id="rcard"', 1)[1].split('id="swcard"', 1)[0]
        for needle in ('id="statshare"', 'id="ss_on"', 'id="ss_page"', 'id="ss_ws"', 'data-ssg="net"', 'data-ssg="cores"', 'data-ssg="disk"', "/api/statsshare", "/api/statsshare/widget"):
            self.assertIn(needle, idx, needle)
        self.assertIn('id="statshare"', card)

    def test_page_shows_every_block_of_the_status_page(self):
        for needle in ('id="c_net"', 'id="c_send"', 'id="c_cam"', 'id="c_sys"', 'id="c_temps"', 'id="c_cores"', 'id="c_disk"', 'id="c_alerts"', "d.cores", "d.disk", "d.temp.zones", "retrans_total",
                       "buf_pkts", "shown_mbit", "mem_avail_mb"):
            self.assertIn(needle, self.html, needle)
        for flag in ("cores:", "disk:", "zones:", "temp:", "sys:", "cams:", "net:", "send:", "alerts:"):
            self.assertIn(flag, self.html.split("show: {", 1)[1].split("}", 1)[0], flag)

    def test_page_shows_box_offline_when_nothing_arrives(self):
        for needle in ('boxOffline: "Box offline"', "offlineWatch", 'id="offline"', "L.boxOffline", "lastData:", "isOffline("):
            self.assertIn(needle, self.html, needle)
        self.assertIn("setInterval(offlineWatch, 1000)", self.html)

    def run_js(self, calls):
        """Die reine Rechnung der Seite (Frist, Alter) mit Node prüfen; ohne Node übersprungen."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js nicht installiert")
        import subprocess
        src = self.html.split("<script>", 1)[1]
        fns = "".join(re.search(r"^function %s\(.*$" % n, src, re.M).group(0) + chr(10) for n in ("offlineAfterMs", "isOffline", "fmtAge"))
        out = subprocess.run([node, "-e", fns + "console.log(JSON.stringify([" + calls + "]))"], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_offline_rule(self):
        got = self.run_js("offlineAfterMs(1), offlineAfterMs(2), offlineAfterMs(10), "
                          "isOffline(100000, 99000, 0, 1), isOffline(100000, 90000, 0, 1), isOffline(100000, 0, 99000, 1), isOffline(100000, 0, 90000, 1), isOffline(100000, 90000, 99000, 1)")
        self.assertEqual(got[:3], [6500, 8000, 20000])               # die Frist wächst mit dem Takt
        self.assertEqual(got[3:], [False, True, False, True, False])  # frische Daten: online; 10 s ohne Daten: offline; nach dem Wiederaufnehmen zählt ab dort; das Jüngere gilt

    def test_age_text(self):
        self.assertEqual(self.run_js('fmtAge(5), fmtAge(89), fmtAge(120), fmtAge(3600), fmtAge(7200)'), ["5 s", "89 s", "2 min", "60 min", "2 h"])

    @staticmethod
    def src_index():
        with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
            return f.read()


if __name__ == "__main__":
    unittest.main()
