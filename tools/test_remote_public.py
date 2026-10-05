"""Tests für die Selbstprüfung der öffentlichen Adresse (Funnel) in der Karte Fernzugriff: DNS-Antwort lesen, Zustand check/ok/wait, Statusfeld."""
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

HOST = "irl4you-box.tail1234.ts.net"


def labels(host):
    return b"".join(bytes([len(p)]) + p.encode() for p in host.split(".")) + b"\x00"


def reply(qid, host, ips, rcode=0, compress=True):
    head = qid + bytes([0x81, 0x80 | rcode]) + b"\x00\x01" + len(ips).to_bytes(2, "big") + b"\x00\x00\x00\x00"
    q = labels(host) + b"\x00\x01\x00\x01"
    ans = b""
    for ip in ips:
        name = b"\xc0\x0c" if compress else labels(host)
        ans += name + b"\x00\x01\x00\x01" + b"\x00\x00\x00\x3c" + b"\x00\x04" + bytes(int(x) for x in ip.split("."))
    return head + q + ans


class DnsParsing(unittest.TestCase):
    def test_reads_a_records_with_and_without_compression(self):
        for comp in (True, False):
            self.assertEqual(server.Remote.parse_dns_a(b"\x12\x34", reply(b"\x12\x34", HOST, ["203.0.113.7", "203.0.113.8"], compress=comp)),
                             ["203.0.113.7", "203.0.113.8"])

    def test_wrong_id_error_code_garbage_and_no_answer(self):
        self.assertEqual(server.Remote.parse_dns_a(b"\x00\x01", reply(b"\x12\x34", HOST, ["203.0.113.7"])), [])      # falsche Kennung
        self.assertEqual(server.Remote.parse_dns_a(b"\x12\x34", reply(b"\x12\x34", HOST, [], rcode=3)), [])           # kein Eintrag (NXDOMAIN)
        self.assertEqual(server.Remote.parse_dns_a(b"\x12\x34", reply(b"\x12\x34", HOST, [])), [])
        for junk in (b"", b"\x12\x34", b"\x12\x34\x81\x80\x00\x01\x00\x05" + b"\xff" * 5):
            self.assertEqual(server.Remote.parse_dns_a(b"\x12\x34", junk), [])

    def test_dns_a_talks_udp(self):
        sv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sv.bind(("127.0.0.1", 0))
        port = sv.getsockname()[1]

        def serve():
            data, addr = sv.recvfrom(512)
            sv.sendto(reply(data[:2], HOST, ["198.51.100.9"]), addr)
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.assertEqual(server.Remote.dns_a(HOST, "127.0.0.1", timeout=3, port=port), ["198.51.100.9"])
        sv.close()

    def test_dns_a_without_answer_is_empty(self):
        sv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sv.bind(("127.0.0.1", 0))
        self.assertEqual(server.Remote.dns_a(HOST, "127.0.0.1", timeout=0.3, port=sv.getsockname()[1]), [])
        sv.close()


class Probe(unittest.TestCase):
    def remote(self):
        return server.Remote("/nonexistent", False)

    def test_only_ts_net_names_are_probed(self):
        r = self.remote()
        with mock.patch.object(server.Remote, "dns_a", side_effect=AssertionError("darf nicht abgefragt werden")):
            for bad in ("example.com", "evil.ts.net.example.com", "ts.net", "a b.ts.net", "127.0.0.1", ""):
                self.assertFalse(r.probe_public(bad), bad)

    def test_no_dns_answer_means_not_reachable(self):
        with mock.patch.object(server.Remote, "dns_a", return_value=[]):
            self.assertFalse(self.remote().probe_public(HOST))

    def test_state_goes_from_check_to_wait_to_ok(self):
        r = self.remote()
        results = iter([False, True])
        with mock.patch.object(r, "probe_public", lambda host: next(results)):
            url = "https://%s/" % HOST
            self.assertEqual(r.public_state(url), "check")                 # noch keine Antwort der Prüfung
            for _ in range(100):
                time.sleep(0.02)
                if r.public_state(url) != "check":
                    break
            self.assertEqual(r.public_state(url), "wait")
            r._probe[HOST]["t"] -= 100                                     # Wartezeit abgelaufen: neu prüfen
            r.public_state(url)
            for _ in range(100):
                time.sleep(0.02)
                if r.public_state(url) == "ok":
                    break
            self.assertEqual(r.public_state(url), "ok")

    def test_status_reports_public_only_with_funnel(self):
        r = self.remote()
        ts = {("status", "--json"): {"BackendState": "Running", "Self": {"DNSName": HOST + "."}, "TailscaleIPs": ["100.64.0.1"]},
              ("serve", "status", "--json"): {"Web": {HOST + ":443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}}}}, "AllowFunnel": {HOST + ":443": True}}}
        with mock.patch.object(r, "_ts", lambda *a: ts[a]), mock.patch.object(server.shutil, "which", lambda x: "/usr/bin/tailscale"), \
                mock.patch.object(r, "public_state", lambda url: "wait"):
            st = r.status()
        self.assertTrue(st["funnel"])
        self.assertEqual(st["public"], "wait")
        ts[("serve", "status", "--json")]["AllowFunnel"] = {}
        with mock.patch.object(r, "_ts", lambda *a: ts[a]), mock.patch.object(server.shutil, "which", lambda x: "/usr/bin/tailscale"), \
                mock.patch.object(r, "public_state", side_effect=AssertionError("ohne Funnel keine Prüfung")):
            st = r.status()
        self.assertFalse(st["funnel"])
        self.assertEqual(st["public"], "")


if __name__ == "__main__":
    unittest.main()
