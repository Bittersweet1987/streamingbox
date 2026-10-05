"""Tests zu Issue #25: Anmeldesperre bei gleichzeitigen Anmeldungen, Grenzen und Zeitlimits des HTTP-Servers, Quelladressen."""
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

PW = "ein-langes-passwort"


def make_auth():
    d = tempfile.mkdtemp()
    a = server.Auth(os.path.join(d, "state"))
    a.set_password(a.setup_code, PW, "10.0.0.1")
    return a


class LoginThrottle(unittest.TestCase):
    def test_parallel_wrong_logins_run_few_checks(self):
        """Vorher: 60 gleichzeitige Anmeldungen -> 53 Prüfungen statt 5 (die Sperre zählte erst nach der Prüfung)."""
        a = make_auth()
        calls = []
        real = a._hash

        def slow(pw, salt):
            calls.append(pw)
            time.sleep(0.15)
            return real(pw, salt)
        results = []
        start = threading.Event()

        def attempt(i):
            start.wait()
            try:
                a.login("falsch%d" % i, "203.0.113.9")
                results.append("ok")
            except PermissionError:
                results.append("gesperrt")
            except ValueError:
                results.append("falsch")
        with mock.patch.object(a, "_hash", slow):
            ts = [threading.Thread(target=attempt, args=(i,)) for i in range(60)]
            for t in ts:
                t.start()
            start.set()
            for t in ts:
                t.join()
        self.assertEqual(len(results), 60)
        self.assertLessEqual(len(calls), server.MAX_FAILS)                      # nie mehr Prüfungen als erlaubte Versuche
        self.assertLessEqual(results.count("falsch"), server.MAX_FAILS)
        self.assertEqual(results.count("ok"), 0)
        self.assertGreaterEqual(results.count("gesperrt"), 60 - server.MAX_FAILS)

    def test_at_most_max_checks_run_at_the_same_time(self):
        a = make_auth()
        running, peak, lock = [0], [0], threading.Lock()
        real = a._hash

        def slow(pw, salt):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.2)
            with lock:
                running[0] -= 1
            return real(pw, salt)
        outcomes = []

        def attempt(i):
            try:
                a.login(PW, "198.51.100.%d" % (i + 1))                          # viele Absender, alle mit richtigem Passwort
                outcomes.append("ok")
            except PermissionError:
                outcomes.append("beschäftigt")
        with mock.patch.object(a, "_hash", slow):
            ts = [threading.Thread(target=attempt, args=(i,)) for i in range(12)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertLessEqual(peak[0], server.MAX_CHECKS)
        self.assertGreaterEqual(outcomes.count("ok"), 1)
        self.assertGreaterEqual(outcomes.count("beschäftigt"), 1)

    def test_five_wrong_then_locked_without_a_check(self):
        a = make_auth()
        for _ in range(server.MAX_FAILS):
            with self.assertRaises(ValueError):
                a.login("falsch", "192.0.2.1")
        with mock.patch.object(a, "_hash", side_effect=AssertionError("es darf nicht mehr geprüft werden")):
            with self.assertRaises(PermissionError):
                a.login(PW, "192.0.2.1")                                         # auch das richtige Passwort: gesperrt
        self.assertTrue(a.login(PW, "192.0.2.2"))                                # ein anderer Absender ist nicht betroffen

    def test_success_and_busy_are_not_counted(self):
        a = make_auth()
        for _ in range(server.MAX_FAILS * 2):
            self.assertTrue(a.login(PW, "192.0.2.5"))
        self.assertEqual(a.fails.get("192.0.2.5"), [])
        with mock.patch.object(a, "bela_hash", return_value="$2b$x"), mock.patch.object(a, "bela_ok", side_effect=RuntimeError("nicht möglich")):
            for _ in range(server.MAX_FAILS + 2):
                with self.assertRaises(RuntimeError):
                    a.login(PW, "192.0.2.6")                                     # Prüfung selbst kaputt: kein Fehlversuch
        self.assertEqual(a.fails.get("192.0.2.6"), [])

    def test_wrong_setup_code_counts_and_locks(self):
        d = tempfile.mkdtemp()
        a = server.Auth(os.path.join(d, "state"))
        for _ in range(server.MAX_FAILS):
            with self.assertRaises(ValueError):
                a.set_password("falscher-code", PW, "192.0.2.7")
        with self.assertRaises(PermissionError):
            a.set_password(a.setup_code, PW, "192.0.2.7")


class SourceFilter(unittest.TestCase):
    def test_private_and_local_sources_are_allowed(self):
        for ip in ("127.0.0.1", "::1", "10.1.2.3", "192.168.178.20", "172.16.0.5", "169.254.1.1", "100.112.23.100", "fd7a:115c:a1e0::1", "fe80::1%eth0",
                   "::ffff:192.168.1.5"):
            self.assertTrue(server.source_allowed(ip), ip)

    def test_public_sources_are_refused(self):
        for ip in ("8.8.8.8", "217.244.184.147", "2003:c3:e704::1", "::ffff:8.8.8.8", "100.128.0.1", "kein-ip", ""):
            self.assertFalse(server.source_allowed(ip), ip)


def closed(sock):
    """Hat der Server die Verbindung ohne Antwort geschlossen (leeres Lesen oder zurückgesetzt)?"""
    try:
        return sock.recv(100) == b""
    except ConnectionResetError:
        return True


def start(handler, **limits):
    srv = server.LimitedHTTPServer(("127.0.0.1", 0), handler)
    for k, v in limits.items():
        setattr(srv, k, v)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return srv


class HttpLimits(unittest.TestCase):
    def tearDown(self):
        getattr(self, "srv", None) and (self.srv.shutdown(), self.srv.server_close())

    def conn(self):
        s = socket.create_connection(self.srv.server_address, timeout=3)
        return s

    def test_connections_per_source_are_limited_and_freed_again(self):
        from http.server import BaseHTTPRequestHandler

        class H(BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
        self.srv = start(H, PER_IP=2, MAX_CONN=10)
        a, b = self.conn(), self.conn()                                          # halb offen: Anfrage kommt nie
        time.sleep(0.2)
        c = self.conn()
        c.sendall(b"GET / HTTP/1.0\r\n\r\n")
        c.settimeout(2)
        self.assertTrue(closed(c))                                               # dritte Verbindung vom selben Absender: sofort geschlossen
        a.close()
        time.sleep(0.4)
        d = self.conn()
        d.sendall(b"GET / HTTP/1.0\r\n\r\n")
        d.settimeout(2)
        self.assertIn(b"200 OK", d.recv(200))                                    # Platz ist wieder frei
        b.close()
        d.close()
        c.close()

    def test_total_connections_are_limited(self):
        from http.server import BaseHTTPRequestHandler

        class H(BaseHTTPRequestHandler):
            timeout = 5

        self.srv = start(H, PER_IP=50, MAX_CONN=3)
        held = [self.conn() for _ in range(3)]
        time.sleep(0.2)
        extra = self.conn()
        extra.settimeout(2)
        extra.sendall(b"GET / HTTP/1.0\r\n\r\n")
        self.assertTrue(closed(extra))
        for s in held + [extra]:
            s.close()

    def test_public_source_gets_no_answer(self):
        from http.server import BaseHTTPRequestHandler
        self.srv = start(BaseHTTPRequestHandler)
        self.assertFalse(self.srv.verify_request(None, ("8.8.8.8", 1234)))
        self.assertTrue(self.srv.verify_request(None, ("192.168.1.5", 1234)))
        self.srv.allow_public = True
        self.assertTrue(self.srv.verify_request(None, ("8.8.8.8", 1234)))

    def test_idle_connection_is_closed_by_the_timeout(self):
        with mock.patch.object(server.Handler, "timeout", 1):
            self.srv = start(server.Handler)
            s = self.conn()
            s.settimeout(4)
            t0 = time.time()
            self.assertEqual(s.recv(10), b"")
            self.assertLess(time.time() - t0, 3)
            s.close()

    def test_slow_body_is_cut_off(self):
        """Issue #25: Content-Length 4000, aber nur 1 Byte Inhalt: blieb unbegrenzt offen."""
        with mock.patch.object(server.Handler, "BODY_SECONDS", 1.0), mock.patch.object(server.Handler, "timeout", 5):
            self.srv = start(server.Handler)
            s = self.conn()
            s.settimeout(5)
            s.sendall(b"POST /api/login HTTP/1.0\r\nContent-Length: 4000\r\nContent-Type: application/json\r\n\r\n{")
            t0 = time.time()
            data = b""
            while True:
                chunk = s.recv(1000)
                if not chunk:
                    break
                data += chunk
            self.assertLess(time.time() - t0, 3.5)
            self.assertIn(b"400", data.split(b"\r\n")[0])
            self.assertIn("zu langsam".encode(), data)
            s.close()


if __name__ == "__main__":
    unittest.main()
