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
            "details": {"temps": [{"name": "CPU", "c": 61.5}, {"name": "GPU", "c": 55.0}], "send": {"fps": 29.9, "bitrate_kbps": 8000, "rtt_ms": 31, "send_mbps": 8.1, "snd_buf_ms": 80,
                                                                                                   "loss_pct": 0.0, "retrans_pct": 0.4, "stream_id": "GEHEIM-STREAM-ID", "server": "srt.example.org"}},
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
        self.assertEqual(s["temp"]["max_c"], 61.5)
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
        for secret in ("GEHEIM-STREAM-ID", "srt.example.org", "192.168.1.", "rtmp://", "dji-b8a860", "NIE-AUSGEBEN", "stream_id", '"key"', '"url"', '"ip"'):
            self.assertNotIn(secret, text, secret)

    def test_groups_switch_parts_off(self):
        s = self.snap({g: False for g in server.StatsShare.GROUPS})
        self.assertEqual(sorted(s), ["live", "time", "v"])
        s = self.snap({**{g: False for g in server.StatsShare.GROUPS}, "cameras": True})
        self.assertEqual(sorted(s), ["cameras", "live", "time", "v"])

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
        for needle in ('id="statshare"', 'id="ss_on"', 'id="ss_page"', 'id="ss_ws"', 'data-ssg="net"', "/api/statsshare", "/api/statsshare/widget"):
            self.assertIn(needle, idx, needle)
        self.assertIn('id="statshare"', card)

    @staticmethod
    def src_index():
        with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
            return f.read()


if __name__ == "__main__":
    unittest.main()
