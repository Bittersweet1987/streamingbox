"""Tests für den Protokoll-Download: Bereinigung (echte Zeilenformate der Box), Root-Helfer pipbox-logs.py, LogBundle und Endpunkte (server.py)."""
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402


def load_helper():
    spec = importlib.util.spec_from_file_location("pipbox_logs", os.path.join(ROOT, "install", "pipbox-logs.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = load_helper()

# Zeilen in den Formaten, die auf der Box wirklich vorkommen (Journal short-iso, Zustandsprotokoll, NetworkManager, SRT/srtla)
REAL_LINES = {
    "dji": "2026-10-04T17:21:22+0000 belabox python3[841364]: INFO pipbox-dji: AC:DE:48:11:22:33: Statusnachricht (34 Byte, Akku 100 %): 00401100000000",
    "dji_key": "2026-10-04T17:21:25+0000 belabox python3[1]: INFO pipbox-dji: dji-acde49 streamt nach rtmp://192.0.2.131:1935/publish/dji-acde48112233",
    "health": "17:28:26 load=0.77/0.98/1.04 temp=32C mhz=1200/1200/1200/1200/408/408/408/408 memfree=7488M eth1=ja belacoder=nein srtla=nein bt=1",
    "nm": "Oct  4 17:20:01 belabox NetworkManager[812]: <info>  [1759598401.1] device (wlan1): Activation: successful, connected to MeinHandyNetz",
    "srt": "srt://live.example.org:5000?streamid=abc123streamidXYZ&latency=2000",
    "hci": "hci0:\tType: Primary  Bus: USB\n\tBD Address: AC:DE:48:77:88:99  ACL MTU: 1021:8  SCO MTU: 255:12",
    "dev": "bluez: /org/bluez/hci0/dev_AC_DE_48_44_55_66 Connected: yes",
}


def scrubber():
    sc = H.Scrubber()
    sc.secret("MeinHandyNetz", "WLAN")
    sc.secret("live.example.org", "SERVER")
    sc.secret("abc123streamidXYZ", "STREAM-ID")
    sc.secret("geheimespasswort", "PASSWORT")
    return sc


class Scrubbing(unittest.TestCase):
    def test_nothing_personal_survives_in_real_line_formats(self):
        sc = scrubber()
        out = "\n".join(sc.scrub(t) for t in REAL_LINES.values())
        for private in ("AC:DE:48:11:22:33", "11:22:33", "acde49", "acde48112233", "192.0.2.131", "MeinHandyNetz", "live.example.org", "abc123streamidXYZ",
                        "AC:DE:48:77:88:99", "77:88:99", "AC_DE_48_44_55_66", "44_55_66"):
            self.assertNotIn(private, out, private)

    def test_useful_parts_stay_readable(self):
        sc = scrubber()
        self.assertIn("Akku 100 %", sc.scrub(REAL_LINES["dji"]))
        self.assertIn("load=0.77/0.98/1.04 temp=32C", sc.scrub(REAL_LINES["health"]))
        self.assertIn("bt=1", sc.scrub(REAL_LINES["health"]))
        self.assertIn("latency=2000", sc.scrub(REAL_LINES["srt"]))
        self.assertIn("Activation: successful", sc.scrub(REAL_LINES["nm"]))

    def test_same_value_gets_same_number(self):
        sc = scrubber()
        a = sc.scrub("von 192.0.2.131 nach 192.0.2.1 und wieder 192.0.2.131")
        self.assertEqual(a, "von <IP-1> nach <IP-2> und wieder <IP-1>")
        m = sc.scrub("AC:DE:48:11:22:33 und dev_AC_DE_48_11_22_33 und AC:DE:48:44:55:66")
        self.assertEqual(m, "<MAC-1 AC:DE:48> und dev_<MAC-1> und <MAC-2 AC:DE:48>")       # Herstellerteil bleibt für die Fehlersuche

    def test_loopback_and_netmask_stay(self):
        sc = scrubber()
        self.assertEqual(sc.scrub("127.0.0.1:8780 0.0.0.0 255.255.255.0 ::1"), "127.0.0.1:8780 0.0.0.0 255.255.255.0 ::1")

    def test_versions_and_times_are_not_taken_for_addresses(self):
        sc = scrubber()
        for t in ("Kernel 5.10.160-belabox", "Version 0.9.66", "17:28:26", "2026-10-04T17:21:22+0000", "std::bad Class::method", "load 0.77/0.98/1.04"):
            self.assertEqual(sc.scrub(t), t)

    def test_ipv6(self):
        sc = scrubber()
        for a in ("fe80::a00:27ff:fe4e:66a1", "2a02:810d:1234:5678::1", "2001:db8:0:0:0:0:0:1"):
            out = sc.scrub("Adresse " + a + " ok")
            self.assertNotIn(a, out)
            self.assertRegex(out, r"^Adresse <IPv6-\d> ok$")

    def test_secret_key_value_pairs(self):
        sc = scrubber()
        for t, gone in (('password=hunter2', "hunter2"), ('{"password": "geheim 123", "ssid": "x"}', "geheim 123"), ("token: abc.def", "abc.def"),
                        ("Authorization: Bearer eyJabc.def.ghi", "eyJabc"), ("GET /x?api_key=AbCd1234&b=1", "AbCd1234"),
                        ("streamid=SRT-ID-1", "SRT-ID-1"), ("psk=xyzxyzxyz", "xyzxyzxyz")):
            self.assertNotIn(gone, sc.scrub(t), t)
        self.assertIn("&b=1", sc.scrub("GET /x?api_key=AbCd1234&b=1"))          # der Rest der Zeile bleibt

    def test_literals_longest_first_and_short_values_ignored(self):
        sc = H.Scrubber()
        sc.secret("Netz", "WLAN")
        sc.secret("Netz Zuhause 5G", "WLAN")
        sc.secret("ab", "WLAN")                       # zu kurz: würde zu viel ersetzen
        sc.secret("", "WLAN")
        sc.secret(None, "WLAN")
        self.assertEqual(sc.scrub("Netz Zuhause 5G und ab"), "<WLAN-2> und ab")

    def test_tailscale_and_mail(self):
        sc = scrubber()
        out = sc.scrub("box.tail1234.ts.net https://login.tailscale.com/a/abc123 user.name@example.com")
        for gone in ("tail1234", "abc123", "user.name", "example.com"):
            self.assertNotIn(gone, out)

    def test_mac_addresses_with_escaped_colons_from_nmcli_terse_output(self):
        sc = scrubber()
        out = sc.scrub(r"GENERAL.HWADDR:AC\:DE\:48\:11\:22\:33 und AC:DE:48:44:55:66 und 00\:00\:00\:00\:00\:00")
        self.assertNotIn("11", out.replace("<MAC-1 AC:DE:48>", ""))
        self.assertIn("<MAC-1 AC:DE:48>", out)
        self.assertIn("<MAC-2 AC:DE:48>", out)
        self.assertIn(r"00\:00\:00\:00\:00\:00", out)                                            # die Nulladresse bleibt auch so

    def test_wlan_cards_section_is_in_the_bundle_and_scrubbed(self):
        def fake(cmd, timeout=15, limit=0):
            if cmd[:3] == ["nmcli", "-t", "-f"] and "DEVICE,TYPE" in cmd:
                return "wlan1:wifi\neth0:ethernet\n"
            if cmd[:2] == ["nmcli", "-t"] and "show" in cmd:
                return "GENERAL.DEVICE:wlan1\nGENERAL.HWADDR:AC\\:DE\\:48\\:11\\:22\\:33\nWIFI-PROPERTIES.AP:yes\nIP4.ADDRESS[1]:192.0.2.5/24\n"
            if cmd[:5] == ["nmcli", "-t", "-f", "SSID", "dev"]:                                    # so liest der Helfer die Namen, die er überall ersetzt
                return "Nachbar\n"
            if cmd[0] == "nmcli" and "BSSID" in " ".join(cmd):
                return "IN-USE SSID BSSID CHAN\n      Nachbar AC:DE:48:99:88:77 6\n"
            return ""
        with mock.patch.object(H, "run", side_effect=fake), mock.patch.object(H, "STATE", tempfile.mkdtemp()):
            text = H.build()
        self.assertIn("===== WLAN-Karten", text)
        self.assertIn("WIFI-PROPERTIES.AP:yes", text)
        self.assertNotIn("IP4.ADDRESS", text.split("===== WLAN-Karten")[1].split("=====")[0])           # nur Zustand und Fähigkeiten, keine Adressen
        for gone in ("11:22:33", "99:88:77", "Nachbar"):
            self.assertNotIn(gone, text)

    def test_zero_mac_and_device_tree_names_stay(self):
        sc = scrubber()
        t = "phy phy-fd5d0000.syscon:usb2-phy@0.0: Looking up 00:00:00:00:00:00 ff:ff:ff:ff:ff:ff in /syscon@fd5d0000/usb2-phy@0/otg-port"
        self.assertEqual(sc.scrub(t), t)

    def test_camera_keys_are_numbered_alike(self):
        sc = H.Scrubber()
        sc.secret_key("cafe0001")
        sc.secret_key("kurz")
        sc.secret_key("ab")                                  # zu kurz: bleibt
        self.assertEqual(sc.scrub("cafe0001 dji-acde49 cafe0001 dji-acde49 dji-acde48112233 ab"),
                         "<Schlüssel-1> <Schlüssel-3> <Schlüssel-1> <Schlüssel-3> <Schlüssel-4> ab")

    def test_umlauts_and_odd_bytes(self):
        sc = scrubber()
        self.assertEqual(sc.scrub("Grüße aus Köln ✓"), "Grüße aus Köln ✓")


class HelperSettings(unittest.TestCase):
    def state(self, files):
        d = tempfile.mkdtemp()
        for name, data in files.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(data if isinstance(data, str) else json.dumps(data))
        return d

    def test_secrets_come_from_the_settings_files(self):
        d = self.state({
            "srtla.json": {"servers": [{"name": "Eigener", "host": "live.example.org", "port": 5000, "streamid": "abc123streamidXYZ"}], "settings": {}},
            "dji-cameras.json": {"cameras": {"AC:DE:48:11:22:33": {"ssid": "DJI-Kamera-Netz", "password": "kamerapass99", "rtmp_key": "dji-acde48112233",
                                                                    "saved": [{"ssid": "AltesNetz", "password": "altpass1234"}]}},
                                 "by_connection": {"eth2": {"ssid": "HandyHotspot", "password": "hotspotpass"}}},
            "cameras.json": [{"id": "x", "name": "Kamera 1", "key": "meinstreamschluessel", "role": "main"}],
            "dji-token": "tok-1234567890abcdef\n"})
        sc = H.Scrubber()
        with mock.patch.object(H, "STATE", d), mock.patch.object(H, "run", return_value=""):
            H.collect_secrets(sc)
        text = sc.scrub("live.example.org abc123streamidXYZ DJI-Kamera-Netz kamerapass99 dji-acde48112233 AltesNetz altpass1234 HandyHotspot hotspotpass "
                        "meinstreamschluessel tok-1234567890abcdef")
        for gone in ("live.example", "abc123", "DJI-Kamera-Netz", "kamerapass99", "acde48", "AltesNetz", "altpass1234", "HandyHotspot", "hotspotpass",
                     "meinstreamschluessel", "tok-1234567890abcdef"):
            self.assertNotIn(gone, text, gone)

    def test_network_names_from_nmcli_are_included(self):
        def fake(cmd, timeout=15, limit=0):
            if cmd[:2] == ["nmcli", "-t"] and "con" in cmd:
                return "Wired connection 1:802-3-ethernet\nZuhauseWLAN:802-11-wireless\nMit\\:Doppelpunkt:802-11-wireless\n"
            if "wifi" in cmd:
                return "NachbarNetz\nZuhauseWLAN\n"
            return ""
        sc = H.Scrubber()
        with mock.patch.object(H, "STATE", tempfile.mkdtemp()), mock.patch.object(H, "run", side_effect=fake):
            H.collect_secrets(sc)
        out = sc.scrub("Zuhause: ZuhauseWLAN Nachbar: NachbarNetz Wired connection 1")
        self.assertNotIn("ZuhauseWLAN", out)
        self.assertNotIn("NachbarNetz", out)
        self.assertIn("Wired connection 1", out)

    def test_summary_has_no_credentials(self):
        d = self.state({
            "pipeline.json": {"type": "pip", "corner": 4, "styles": {"1": {"bw": 3}}},
            "srtla.json": {"servers": [{"host": "live.example.org", "streamid": "abc123streamidXYZ"}], "settings": {"max_kbps": 8000}},
            "dji-cameras.json": {"cameras": {"AC:DE:48:11:22:33": {"name": "Kamera 1", "ssid": "DJI-Kamera-Netz", "password": "kamerapass99",
                                                                    "rtmp_key": "dji-acde48112233", "fps": 30, "saved": [{"ssid": "A", "password": "b"}]}}},
            "cameras.json": [{"id": "x", "name": "Kamera 1", "key": "meinstreamschluessel", "role": "main"}]})
        with mock.patch.object(H, "STATE", d):
            text = H.settings_summary()
        for gone in ("live.example", "abc123", "DJI-Kamera-Netz", "kamerapass99", "acde48", "meinstreamschluessel"):
            self.assertNotIn(gone, text, gone)
        for kept in ("max_kbps", "Kamera 1", '"fps": 30', "Server gespeichert: 1", "gespeicherte WLANs: 1"):
            self.assertIn(kept, text)

    def test_repeated_lines_are_folded(self):
        t = "\n".join(["2026-10-04T06:45:43+0000 belabox x: IRL4YOU BOX läuft"] * 3 + ["2026-10-04T06:45:50+0000 belabox x: IRL4YOU BOX läuft",
                                                                                      "2026-10-04T06:46:00+0000 belabox x: anderes",
                                                                                      "2026-10-04T06:46:01+0000 belabox x: anderes"])
        out = H.collapse_repeats(t).splitlines()
        self.assertEqual(len(out), 4)
        self.assertIn("3 weitere gleiche Zeilen, die letzte um 2026-10-04T06:45:50+0000", out[1])
        self.assertIn("1 weitere gleiche Zeilen", out[3])
        self.assertEqual(H.collapse_repeats("a\nb\nc"), "a\nb\nc\n")                         # nichts Gleiches: unverändert
        self.assertEqual(H.collapse_repeats("IRL4YOU BOX läuft\n" * 3), "IRL4YOU BOX läuft\n    (… 2 weitere gleiche Zeilen)\n")   # ohne Uhrzeit vorne

    def test_missing_file_is_called_missing(self):
        self.assertEqual(H.tail_file("/gibt/es/nicht"), "(nicht vorhanden)\n")
        d = tempfile.mkdtemp()
        with open(d + "/v", "w") as f:
            f.write("0.9.66\n")
        self.assertEqual(H.read_small(d + "/v"), "0.9.66")
        self.assertEqual(H.read_small(d + "/fehlt"), "(nicht lesbar)")

    def test_json_loader_refuses_symlinks_and_garbage(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "echt.json"), "w") as f:
            f.write('{"geheim": 1}')
        os.symlink(os.path.join(d, "echt.json"), os.path.join(d, "srtla.json"))
        with open(os.path.join(d, "pipeline.json"), "w") as f:
            f.write("kein json")
        with mock.patch.object(H, "STATE", d):
            self.assertIsNone(H.load_json("srtla.json"))              # kein Verweis auf eine andere Datei
            self.assertIsNone(H.load_json("pipeline.json"))
            self.assertIsNone(H.load_json("fehlt.json"))
            self.assertEqual(H.load_json("echt.json"), {"geheim": 1})

    def test_noise_lines_are_dropped_but_the_last_kept(self):
        lines = ["x INFO pipbox-dji: a: Statusnachricht (34 Byte, Akku %d %%): 00" % i for i in range(100)] + ["x ERROR wichtig"]
        out = H.drop_noise("\n".join(lines))
        self.assertIn("ERROR wichtig", out)
        self.assertEqual(out.count("Statusnachricht (34"), 15)
        self.assertIn("Von 100 Zeilen", out)


class HelperRun(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.req = os.path.join(self.d, "logs-request")
        self.run_dir = os.path.join(self.d, "run")
        self.patch = mock.patch.multiple(H, STATE=self.d, REQ=self.req, RUN=self.run_dir, OUT=self.run_dir + "/bundle.txt", STATUS=self.run_dir + "/status.json")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def status(self):
        with open(self.run_dir + "/status.json") as f:
            return json.load(f)

    def test_collect_writes_bundle_and_status(self):
        open(self.req, "w").write("collect\n")
        with mock.patch.object(H, "build", return_value="Protokolltext äöü\n"):
            self.assertEqual(H.main(), 0)
        self.assertEqual(open(self.run_dir + "/bundle.txt", encoding="utf-8").read(), "Protokolltext äöü\n")
        st = self.status()
        self.assertEqual((st["state"], st["size"]), ("done", len("Protokolltext äöü\n".encode())))
        self.assertEqual(stat.S_IMODE(os.stat(self.run_dir + "/bundle.txt").st_mode), 0o644)
        self.assertFalse(os.path.exists(self.req))                     # Anforderung ist gelöscht

    def test_unknown_keyword_does_nothing_but_is_deleted(self):
        open(self.req, "w").write("poweroff\n")
        with mock.patch.object(H, "build") as b:
            self.assertEqual(H.main(), 1)
            b.assert_not_called()
        self.assertEqual(self.status()["state"], "error")
        self.assertFalse(os.path.exists(self.req))

    def test_symlinked_request_is_refused(self):
        real = os.path.join(self.d, "real")
        open(real, "w").write("collect\n")
        os.symlink(real, self.req)
        with mock.patch.object(H, "build") as b:
            self.assertEqual(H.main(), 1)
            b.assert_not_called()
        self.assertTrue(os.path.exists(real))                          # nur der Verweis wird entfernt

    def test_failure_is_reported_not_raised(self):
        open(self.req, "w").write("collect\n")
        with mock.patch.object(H, "build", side_effect=RuntimeError("kaputt")):
            self.assertEqual(H.main(), 1)
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertNotIn("kaputt", st["message"])                      # keine Interna in der Meldung

    def test_old_bundle_stays_until_replaced(self):
        open(self.req, "w").write("collect\n")
        with mock.patch.object(H, "build", return_value="eins\n"):
            H.main()
        open(self.req, "w").write("collect\n")
        with mock.patch.object(H, "build", side_effect=RuntimeError):
            H.main()
        self.assertEqual(open(self.run_dir + "/bundle.txt").read(), "eins\n")

    def test_build_scrubs_everything_and_has_all_sections(self):
        d = self.d
        open(os.path.join(d, "srtla.json"), "w").write(json.dumps({"servers": [{"host": "live.example.org", "streamid": "abc123streamidXYZ"}], "settings": {}}))

        def fake_run(cmd, timeout=15, limit=400_000):
            if cmd[0] == "journalctl":
                return "2026-10-04T17:21:22+0000 belabox x: verbunden mit live.example.org 192.0.2.131 streamid=abc123streamidXYZ\n"
            return ""
        with mock.patch.object(H, "run", side_effect=fake_run):
            text = H.build()
        for title in ("Kopf", "System", "Dienste", "Einstellungen (Kurzfassung)", "Journal pipbox-dji", "Journal pipbox-send", "Journal pipbox-swupdate",
                      "Zustandsprotokoll"):
            self.assertIn("===== " + title, text)
        for gone in ("live.example.org", "192.0.2.131", "abc123streamidXYZ"):
            self.assertNotIn(gone, text)
        self.assertIn("<IP-1>", text)

    def test_size_is_capped_and_head_kept(self):
        big = "x" * 1000 + "\n"
        with mock.patch.object(H, "MAX_TOTAL", 60_000), \
                mock.patch.object(H, "run", side_effect=lambda cmd, *a, **k: big * 200 if cmd[0] == "journalctl" else ""):
            text = H.build()
        self.assertLessEqual(len(text.encode()), 60_000 + 500)
        self.assertIn("IRL4YOU BOX Protokolle", text)
        self.assertIn("gekürzt", text)


class Bundle(unittest.TestCase):
    def make(self):
        d = tempfile.mkdtemp()
        b = server.LogBundle(d, demo=False)
        run = os.path.join(d, "run")
        os.makedirs(run)
        b.STATUS, b.FILE, b.HELPER = run + "/status.json", run + "/bundle.txt", d + "/helper.path"
        open(b.HELPER, "w").close()
        return b, d, run

    def put(self, b, state, when=None, text="Inhalt\n"):
        with open(b.STATUS, "w") as f:
            json.dump({"state": state, "message": "m", "time": int(when or time.time())}, f)
        if text is not None:
            with open(b.FILE, "w") as f:
                f.write(text)

    def test_request_writes_keyword_only(self):
        b, d, run = self.make()
        b.request()
        self.assertEqual(open(os.path.join(d, "logs-request")).read(), "collect\n")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "logs-request")).st_mode), 0o600)

    def test_request_needs_installed_helper(self):
        b, d, run = self.make()
        os.remove(b.HELPER)
        self.assertFalse(b.status()["helper_installed"])
        with self.assertRaises(ValueError):
            b.request()
        self.assertFalse(os.path.exists(os.path.join(d, "logs-request")))

    def test_old_result_does_not_count_after_a_new_request(self):
        b, d, run = self.make()
        self.put(b, "done", when=time.time() - 600)
        self.assertEqual(b.status()["state"], "done")                 # ohne neue Anfrage: das alte Ergebnis ist da
        b.request()
        self.assertEqual(b.status()["state"], "working")              # Helfer hat sich noch nicht gemeldet
        self.assertIsNone(b.content())
        self.put(b, "done", text="neu\n")
        st = b.status()
        self.assertEqual((st["state"], st["size"]), ("done", 4))
        self.assertEqual(b.content(), b"neu\n")

    def test_helper_that_never_answers_ends_in_error(self):
        b, d, run = self.make()
        b.request()
        b.requested -= server.LogBundle.WAIT_SECONDS + 5
        self.assertEqual(b.status()["state"], "error")

    def test_stuck_working_state_ends_in_error(self):
        b, d, run = self.make()
        b.request()
        self.put(b, "working", text=None)
        self.assertEqual(b.status()["state"], "working")
        b.requested -= server.LogBundle.WAIT_SECONDS + 5
        self.put(b, "working", when=time.time() - 400, text=None)
        self.assertEqual(b.status()["state"], "error")

    def test_error_state_and_missing_file(self):
        b, d, run = self.make()
        b.request()
        self.put(b, "error", text=None)
        self.assertEqual(b.status()["state"], "error")
        self.assertIsNone(b.content())
        os.remove(b.FILE) if os.path.exists(b.FILE) else None
        self.put(b, "done", text=None)
        self.assertEqual(b.status()["state"], "error")               # fertig gemeldet, Datei fehlt

    def test_garbage_status_is_an_error_not_a_crash(self):
        b, d, run = self.make()
        b.request()
        open(b.STATUS, "w").write("kein json")
        self.assertEqual(b.status()["state"], "working")
        open(b.STATUS, "w").write(json.dumps({"state": "böse", "time": int(time.time())}))
        self.assertEqual(b.status()["state"], "error")

    def test_second_request_while_working_is_ignored(self):
        b, d, run = self.make()
        b.request()
        os.remove(os.path.join(d, "logs-request"))
        b.request()
        self.assertFalse(os.path.exists(os.path.join(d, "logs-request")))

    def test_demo_flow(self):
        b = server.LogBundle(tempfile.mkdtemp(), demo=True)
        self.assertEqual(b.status()["state"], "idle")
        self.assertIsNone(b.content())
        b.request()
        self.assertEqual(b.status()["state"], "done")
        self.assertIn(b"Protokolle", b.content())


class Endpoints(unittest.TestCase):
    def handler(self, path, body=b"{}", authed=True):
        h = server.Handler.__new__(server.Handler)
        h.path = path
        h.sent = []
        h.hdrs = {}
        h.authed = lambda: authed
        h.send_response = lambda code, *a: h.sent.append(code)
        h.send_header = lambda k, v: h.hdrs.__setitem__(k, v)
        h.end_headers = lambda: None

        class W:
            data = b""

            def write(self, b):
                W.data += b
        h.wfile = W()
        h.out = W
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = type("R", (), {"read": lambda self, n: body})()
        h.client_address = ("127.0.0.1", 1)
        return h

    def setUp(self):
        self.bundle = server.LogBundle(tempfile.mkdtemp(), demo=True)
        server.Handler.logbundle = self.bundle

    def test_file_is_an_attachment_and_needs_login(self):
        self.bundle.request()
        h = self.handler("/api/logs/file")
        h.do_GET()
        self.assertEqual(h.sent, [200])
        self.assertIn("attachment", h.hdrs["Content-Disposition"])
        self.assertTrue(h.hdrs["Content-Type"].startswith("text/plain"))
        self.assertEqual(h.hdrs["X-Content-Type-Options"], "nosniff")
        h = self.handler("/api/logs/file", authed=False)
        h.out.data = b""
        h.do_GET()
        self.assertEqual(h.sent, [401])
        self.assertNotIn(b"Protokolle", h.out.data)

    def test_no_file_yet_is_404(self):
        h = self.handler("/api/logs/file")
        h.do_GET()
        self.assertEqual(h.sent, [404])

    def test_post_only_accepts_collect(self):
        h = self.handler("/api/logs", json.dumps({"action": "collect"}).encode())
        h.do_POST()
        self.assertEqual(h.sent, [200])
        self.assertEqual(self.bundle.status()["state"], "done")
        for bad in ({"action": "delete"}, {}, {"action": ["collect"]}):
            h = self.handler("/api/logs", json.dumps(bad).encode())
            h.do_POST()
            self.assertEqual(h.sent, [400], str(bad))


class FilesAndPage(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def test_units_and_install(self):
        self.assertIn("PathExists=/var/lib/pipbox/logs-request", self.read("install", "pipbox-logs.path"))
        self.assertIn("Unit=pipbox-logs.service", self.read("install", "pipbox-logs.path"))
        svc = self.read("install", "pipbox-logs.service")
        self.assertIn("Type=oneshot", svc)
        self.assertIn("/opt/pipbox/pipbox-logs.py", svc)
        inst = self.read("install", "install.sh")
        for needle in ('install -m 755 "$HERE/install/pipbox-logs.py" /opt/pipbox/pipbox-logs.py',
                       '"$HERE/install/pipbox-logs.service"', '"$HERE/install/pipbox-logs.path"'):
            self.assertIn(needle, inst)
        self.assertGreaterEqual(inst.count("pipbox-logs.path"), 4)       # installieren, aktivieren, abschalten, entfernen

    def test_page_has_button_and_download_code(self):
        page = self.read("web", "index.html")
        self.assertIn('id="lg_dl"', page)
        self.assertIn("Protokolle herunterladen", page)
        self.assertIn('"/api/logs/file"', page)
        self.assertIn('action:"collect"', page)


if __name__ == "__main__":
    unittest.main()
