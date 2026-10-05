"""Tests zum Schalter "Oberfläche über fremde WLANs sperren" (Issue #25): Erkennung der Gast-WLANs, Sperre beim Verbinden, Schutz vor dem Aussperren."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.modules.setdefault("dbus", types.ModuleType("dbus"))
import server  # noqa: E402

CLIENT = [{"iface": "wlan0", "ip": "10.180.245.72"}]


class Detection(unittest.TestCase):
    def run_nmcli(self, table):
        def fake(args, capture_output=True, text=True, timeout=4):
            key = tuple(args[1:])
            out = table.get(key, "")
            return types.SimpleNamespace(stdout=out)
        return mock.patch.object(server.subprocess, "run", fake)

    def test_client_wifi_is_found_and_own_hotspot_is_not(self):
        table = {
            ("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"): "wlan0:wifi:connected:OnePlus\nwlan1:wifi:connected:pipbox-hotspot-wlan1\nwlan2:wifi:disconnected:\n"
                                                                  "eth0:ethernet:connected:Kabel\ntailscale0:tun:connected (externally):tailscale0\n",
            ("-g", "802-11-wireless.mode", "connection", "show", "OnePlus"): "infrastructure\n",
            ("-g", "802-11-wireless.mode", "connection", "show", "pipbox-hotspot-wlan1"): "ap\n",
            ("-g", "IP4.ADDRESS", "dev", "show", "wlan0"): "10.180.245.72/24\n",
            ("-g", "IP4.ADDRESS", "dev", "show", "wlan1"): "10.42.0.1/24\n",
        }
        with self.run_nmcli(table):
            self.assertEqual(server.client_wifi_list(), CLIENT)

    def test_escaped_colon_in_the_connection_name(self):
        table = {
            ("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"): "wlan0:wifi:connected:Cafe\\:Gast\n",
            ("-g", "802-11-wireless.mode", "connection", "show", "Cafe:Gast"): "infrastructure\n",
            ("-g", "IP4.ADDRESS", "dev", "show", "wlan0"): "192.168.5.9/24\n",
        }
        with self.run_nmcli(table):
            self.assertEqual(server.client_wifi_list(), [{"iface": "wlan0", "ip": "192.168.5.9"}])

    def test_without_nmcli_nothing_is_found(self):
        with mock.patch.object(server.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertEqual(server.client_wifi_list(), [])


class Switch(unittest.TestCase):
    def make(self, clients=CLIENT):
        d = tempfile.mkdtemp()
        calls = []
        def lister():
            calls.append(1)
            return clients
        return server.UiAccess(os.path.join(d, "ui-access.json"), lister=lister), calls

    def test_default_is_off_and_nothing_is_refused(self):
        ua, calls = self.make()
        self.assertFalse(ua.block)
        self.assertFalse(ua.refuses("10.180.245.72"))
        self.assertEqual(calls, [])                                               # aus: nmcli wird nicht einmal gefragt

    def test_on_refuses_only_the_guest_wifi_address(self):
        ua, _ = self.make()
        ua.set(True, "192.168.178.195")
        self.assertTrue(ua.refuses("10.180.245.72"))
        self.assertFalse(ua.refuses("192.168.178.195"))                           # Ethernet bleibt
        self.assertFalse(ua.refuses("127.0.0.1"))                                 # Tailscale-Proxy bleibt
        self.assertFalse(ua.refuses(None))

    def test_cannot_lock_yourself_out(self):
        ua, _ = self.make()
        with self.assertRaises(ValueError) as e:
            ua.set(True, "10.180.245.72")                                         # man ist selbst über das Gast-WLAN da
        self.assertIn("sperrst du dich aus", str(e.exception))
        self.assertFalse(ua.block)
        ua.set(True, "192.168.178.195")
        ua.set(False, "10.180.245.72")                                            # ausschalten geht von überall
        self.assertFalse(ua.block)

    def test_invalid_value(self):
        ua, _ = self.make()
        for bad in ("ja", 1, None):
            with self.assertRaises(ValueError):
                ua.set(bad)

    def test_setting_is_saved_privately_and_loaded(self):
        ua, _ = self.make()
        ua.set(True, None)
        self.assertEqual(oct(os.stat(ua.path).st_mode & 0o777), "0o600")
        again = server.UiAccess(ua.path, lister=lambda: [])
        self.assertTrue(again.block)
        open(ua.path, "w").write("{kaputt")
        self.assertFalse(server.UiAccess(ua.path).block)                          # kaputte Datei: aus

    def test_list_is_cached_and_errors_mean_no_block(self):
        ua, calls = self.make()
        ua.set(True, None)
        for _ in range(5):
            ua.refuses("10.180.245.72")
        self.assertEqual(len(calls), 1)
        bad = server.UiAccess(os.path.join(tempfile.mkdtemp(), "x.json"), lister=mock.Mock(side_effect=RuntimeError("kaputt")))
        bad.block = True
        self.assertFalse(bad.refuses("10.180.245.72"))                           # Fehler: nichts sperren


class Connections(unittest.TestCase):
    def test_server_drops_connections_on_the_guest_wifi_address(self):
        ua = server.UiAccess(os.path.join(tempfile.mkdtemp(), "x.json"), lister=lambda: CLIENT)
        ua.block = True
        srv = server.LimitedHTTPServer(("127.0.0.1", 0), server.Handler)
        srv.ui_access = ua
        try:
            on_guest = types.SimpleNamespace(getsockname=lambda: ("10.180.245.72", 8780))
            on_eth = types.SimpleNamespace(getsockname=lambda: ("192.168.178.195", 8780))
            self.assertFalse(srv.verify_request(on_guest, ("192.168.178.20", 5000)))
            self.assertTrue(srv.verify_request(on_eth, ("192.168.178.20", 5001)))
            ua.block = False
            self.assertTrue(srv.verify_request(on_guest, ("192.168.178.20", 5002)))
        finally:
            srv.server_close()

    def test_api_toggle_and_lockout_protection(self):
        stub = types.SimpleNamespace(valid=lambda tok: True, configured=True, mode="own")
        ua = server.UiAccess(os.path.join(tempfile.mkdtemp(), "x.json"), lister=lambda: [{"iface": "wlan0", "ip": "127.0.0.1"}])   # "Gast-WLAN" = diese Verbindung
        with mock.patch.object(server.Handler, "auth", stub), mock.patch.object(server.Handler, "uiaccess", ua):
            srv = server.LimitedHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
            try:
                def call(method, body=None):
                    s = socket.create_connection(srv.server_address, timeout=5)
                    data = json.dumps(body).encode() if body is not None else b""
                    s.sendall(f"{method} /api/uiaccess HTTP/1.0\r\nContent-Length: {len(data)}\r\nContent-Type: application/json\r\n\r\n".encode() + data)
                    out = b""
                    while True:
                        c = s.recv(2000)
                        if not c:
                            break
                        out += c
                    head, _, payload = out.partition(b"\r\n\r\n")
                    return int(head.split()[1]), json.loads(payload or b"{}")
                code, st = call("GET")
                self.assertEqual((code, st["block_client_wifi"], st["on_client"]), (200, False, True))
                code, st = call("POST", {"block_client_wifi": True})
                self.assertEqual(code, 400)                                       # man sitzt auf dem Gast-WLAN: nicht ausschalten dürfen/aussperren
                self.assertIn("sperrst du dich aus", st["error"])
                ua.lister = lambda: []
                ua._cache = (-1e9, [])
                code, st = call("POST", {"block_client_wifi": True})
                self.assertEqual((code, st["block_client_wifi"]), (200, True))
            finally:
                srv.shutdown()
                srv.server_close()


class Page(unittest.TestCase):
    def test_switch_is_in_the_connections_card(self):
        page = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()
        card = page[page.index('id="netcard"'):page.index('id="rcard"')]
        for needle in ('id="ua_block"', 'id="ua_info"', 'id="ua_err"', "Über fremde WLANs sperren", "/api/uiaccess"):
            self.assertTrue(needle in card or needle in page, needle)
        self.assertEqual(page.count('id="ua_block"'), 1)


if __name__ == "__main__":
    unittest.main()
