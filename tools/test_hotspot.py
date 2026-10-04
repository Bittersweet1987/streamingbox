"""Tests für den WLAN-Hotspot (Issue #16): Root-Helfer pipbox-wifi.py, Wifi-Klasse in server.py, Kamera-Anbindung und Protokoll-Bereinigung."""
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402
import dji_daemon as dd  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(ROOT, "install", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = load("pipbox-wifi")


class FakeNm:
    """nmcli nachgebaut: merkt sich die Aufrufe, einzelne Befehle können scheitern."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], fail

    def __call__(self, *args, stdin=None, timeout=60):
        self.calls.append(args)
        bad = any(args[:2] == f or args[:3] == f for f in self.fail)
        return mock.Mock(returncode=1 if bad else 0, stdout="", stderr="Error: Fehler mit geheim1234" if bad else "")

    def find(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


class HelperHotspot(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.hs = os.path.join(self.d, "hotspot.json")
        p = mock.patch.multiple(H, HOTSPOT_FILE=self.hs, STATE=self.d)
        p.start()
        self.addCleanup(p.stop)
        self.names = []
        for patch in (mock.patch.object(H, "check_iface", lambda i: None),
                      mock.patch.object(H, "wifi_caps", lambda i: {"ap": True, "2ghz": True, "5ghz": True}),
                      mock.patch.object(H, "connection_names", lambda: [[n, "802-11-wireless"] for n in self.names])):
            patch.start()
            self.addCleanup(patch.stop)

    REQ = {"action": "hotspot_start", "iface": "wlan1", "ssid": "Box Netz", "password": "geheim1234", "band": "bg", "channel": 6}

    def start(self, req=None, nm=None):
        nm = nm or FakeNm()
        with mock.patch.object(H, "nm", nm):
            return nm, H.do_hotspot_start(dict(self.REQ, **(req or {})))

    def test_start_builds_an_access_point_profile_and_saves_the_settings(self):
        nm, msg = self.start()
        add = nm.find("con", "add")[0]
        self.assertIn("Box Netz", add)
        self.assertEqual(add[add.index("802-11-wireless.mode") + 1], "ap")
        self.assertEqual(add[add.index("802-11-wireless.band") + 1], "bg")
        self.assertEqual(add[add.index("802-11-wireless.channel") + 1], "6")
        self.assertEqual(add[add.index("ipv4.method") + 1], "shared")
        self.assertEqual(add[add.index("wifi-sec.key-mgmt") + 1], "wpa-psk")
        self.assertEqual(add[add.index("wifi-sec.psk") + 1], "geheim1234")
        self.assertEqual(add[add.index("con-name") + 1], "pipbox-hotspot-wlan1")
        self.assertEqual(nm.find("con", "up")[0][:4], ("con", "up", "id", "pipbox-hotspot-wlan1"))
        saved = json.load(open(self.hs))
        self.assertEqual(saved["wlan1"], {"ssid": "Box Netz", "password": "geheim1234", "band": "bg", "channel": 6})
        self.assertEqual(stat.S_IMODE(os.stat(self.hs).st_mode), 0o600)
        self.assertIn("Box Netz", msg)
        self.assertNotIn("geheim1234", msg)

    def test_automatic_channel_is_not_passed(self):
        nm, _ = self.start({"channel": 0})
        self.assertNotIn("802-11-wireless.channel", nm.find("con", "add")[0])

    def test_existing_profile_is_replaced(self):
        self.names = ["pipbox-hotspot-wlan1", "Heim"]
        nm, _ = self.start()
        self.assertEqual(nm.find("con", "delete")[0], ("con", "delete", "id", "pipbox-hotspot-wlan1"))
        self.assertEqual(len(nm.find("con", "delete")), 1)                       # das Netz "Heim" bleibt unberührt

    def test_empty_password_keeps_the_saved_one(self):
        self.start()
        nm, _ = self.start({"password": "", "ssid": "Neuer Name"})
        add = nm.find("con", "add")[0]
        self.assertEqual(add[add.index("wifi-sec.psk") + 1], "geheim1234")
        self.assertEqual(json.load(open(self.hs))["wlan1"]["ssid"], "Neuer Name")

    def test_empty_password_without_saved_one_is_refused(self):
        with self.assertRaises(ValueError):
            self.start({"password": ""})

    def test_invalid_values_are_refused_before_anything_runs(self):
        bad = [{"ssid": ""}, {"ssid": "x" * 33}, {"ssid": " x"}, {"ssid": "a\nb"}, {"ssid": "pipbox-hotspot-wlan0"}, {"ssid": None},
               {"password": "kurz"}, {"password": "x" * 64}, {"password": "Ümlaut-Passwort"}, {"password": 12345678},
               {"band": "ac"}, {"band": "bg", "channel": 14}, {"band": "bg", "channel": 36}, {"band": "a", "channel": 6}, {"band": "a", "channel": 52},
               {"channel": "6"}, {"channel": True}]
        for b in bad:
            nm = FakeNm()
            with self.assertRaises(ValueError, msg=str(b)):
                self.start(b, nm)
            self.assertEqual(nm.calls, [], str(b))
        self.assertFalse(os.path.exists(self.hs))

    def test_5ghz_channels_without_radar_duty_are_accepted(self):
        for ch in (0, 36, 40, 44, 48):
            self.start({"band": "a", "channel": ch})

    def test_card_without_access_point_mode_or_5ghz(self):
        with mock.patch.object(H, "wifi_caps", lambda i: {"ap": False, "2ghz": True, "5ghz": False}):
            with self.assertRaises(ValueError) as e:
                self.start()
            self.assertIn("Hotspot", str(e.exception))
        with mock.patch.object(H, "wifi_caps", lambda i: {"ap": True, "2ghz": True, "5ghz": False}):
            with self.assertRaises(ValueError):
                self.start({"band": "a", "channel": 0})

    def test_failed_start_removes_the_profile_and_reconnects_without_leaking_the_password(self):
        nm = FakeNm(fail=[("con", "up")])
        with self.assertRaises(RuntimeError) as e:
            self.start(nm=nm)
        self.assertNotIn("geheim1234", str(e.exception))
        self.assertTrue(nm.find("con", "delete"))
        self.assertTrue(nm.find("dev", "connect"))                                # das vorherige Netz der Karte kommt wieder
        self.assertFalse(os.path.exists(self.hs))

    def test_failed_add_leaves_nothing_behind(self):
        nm = FakeNm(fail=[("con", "add")])
        with self.assertRaises(RuntimeError) as e:
            self.start(nm=nm)
        self.assertNotIn("geheim1234", str(e.exception))
        self.assertEqual(nm.find("con", "up"), [])
        self.assertFalse(os.path.exists(self.hs))

    def test_stop_disables_autostart_and_brings_it_down(self):
        self.names = ["pipbox-hotspot-wlan1"]
        nm = FakeNm()
        with mock.patch.object(H, "nm", nm):
            msg = H.do_hotspot_stop({"iface": "wlan1"})
        self.assertEqual(nm.calls[0], ("con", "modify", "id", "pipbox-hotspot-wlan1", "connection.autoconnect", "no"))
        self.assertEqual(nm.calls[1], ("con", "down", "id", "pipbox-hotspot-wlan1"))
        self.assertIn("beendet", msg)

    def test_stop_without_hotspot(self):
        with mock.patch.object(H, "nm", FakeNm()), self.assertRaises(ValueError):
            H.do_hotspot_stop({"iface": "wlan1"})

    def test_camera_card_is_refused(self):
        H2 = load("pipbox-wifi")                                                    # die echte Prüfung, nicht die in setUp ersetzte
        with mock.patch.object(H2, "wifi_devices", lambda: [{"iface": "wlan1", "state": "connected", "connection": "x"}]), \
                mock.patch.object(H2, "camera_iface", lambda: "wlan1"):
            for fn, req in ((H2.do_hotspot_start, dict(self.REQ)), (H2.do_hotspot_stop, {"iface": "wlan1"})):
                with self.assertRaises(ValueError) as e:
                    fn(req)
                self.assertIn("Kameranetz", str(e.exception))

    def test_hotspot_profiles_are_not_saved_networks(self):
        with mock.patch.object(H, "connection_names", lambda: [["Heim", "802-11-wireless"], ["pipbox-hotspot-wlan1", "802-11-wireless"]]):
            self.assertEqual(H.saved_wifi(), ["Heim"])

    def test_client_actions_are_refused_while_a_hotspot_runs(self):
        with mock.patch.object(H, "hotspot_active", lambda i: True):
            for fn, req in ((H.do_scan, "wlan1"), (H.do_disconnect, {"iface": "wlan1"}), (H.do_connect, {"iface": "wlan1", "ssid": "Heim", "password": ""})):
                with self.assertRaises(ValueError, msg=str(fn)):
                    fn(req)

    def test_reserved_names_cannot_be_connected_or_forgotten(self):
        with mock.patch.object(H, "hotspot_active", lambda i: False):
            with self.assertRaises(ValueError):
                H.do_connect({"iface": "wlan1", "ssid": "pipbox-hotspot-wlan1", "password": ""})
        with self.assertRaises(ValueError):
            H.do_forget({"ssid": "pipbox-hotspot-wlan1"})

    def test_hotspot_active_needs_the_matching_profile_and_state(self):
        devs = [{"iface": "wlan1", "state": "connected", "connection": "pipbox-hotspot-wlan1"}, {"iface": "wlan0", "state": "connected", "connection": "Heim"}]
        with mock.patch.object(H, "wifi_devices", lambda: devs):
            self.assertTrue(H.hotspot_active("wlan1"))
            self.assertFalse(H.hotspot_active("wlan0"))
        devs[0]["state"] = "connecting"
        with mock.patch.object(H, "wifi_devices", lambda: devs):
            self.assertFalse(H.hotspot_active("wlan1"))

    def test_store_never_follows_a_planted_link(self):
        victim = os.path.join(self.d, "wichtig")
        open(victim, "w").write("unberührt")
        os.symlink(victim, self.hs + ".tmp")
        H.hs_store({"wlan1": {"ssid": "x"}})
        self.assertEqual(open(victim).read(), "unberührt")
        self.assertEqual(json.load(open(self.hs)), {"wlan1": {"ssid": "x"}})
        os.remove(self.hs)
        os.symlink(victim, self.hs)                                                  # auch die Zieldatei selbst darf ein Verweis sein
        self.assertEqual(H.hs_load(), {})
        H.hs_store({"a": {}})
        self.assertEqual(open(victim).read(), "unberührt")

    def test_caps_are_read_from_networkmanager(self):
        H2 = load("pipbox-wifi")
        with mock.patch.object(H2, "nm", lambda *a, **k: mock.Mock(returncode=0, stdout="yes\nyes\nno\n", stderr="")):
            self.assertEqual(H2.wifi_caps("wlan1"), {"ap": True, "2ghz": True, "5ghz": False})
        with mock.patch.object(H2, "nm", lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr="")):
            self.assertEqual(H2.wifi_caps("wlan1"), {"ap": False, "2ghz": False, "5ghz": False})

    def test_actions_list(self):
        for a in ("hotspot_start", "hotspot_stop", "hotspot_save"):
            self.assertIn(a, H.ACTIONS)

    def save(self, req=None, nm=None, active=False):
        nm = nm or FakeNm()
        with mock.patch.object(H, "nm", nm), mock.patch.object(H, "hotspot_active", lambda i: active):
            return nm, H.do_hotspot_save(dict(self.REQ, action="hotspot_save", **(req or {})))

    def test_save_stores_the_settings_without_touching_networkmanager(self):
        nm, msg = self.save()
        self.assertEqual(nm.calls, [])                                                    # kein Profil, kein Einschalten
        self.assertEqual(json.load(open(self.hs))["wlan1"], {"ssid": "Box Netz", "password": "geheim1234", "band": "bg", "channel": 6})
        self.assertEqual(stat.S_IMODE(os.stat(self.hs).st_mode), 0o600)
        self.assertNotIn("geheim1234", msg)

    def test_save_keeps_the_saved_password_when_the_field_is_empty(self):
        self.save()
        self.save({"password": "", "ssid": "Neuer Name", "band": "a", "channel": 40})
        self.assertEqual(json.load(open(self.hs))["wlan1"], {"ssid": "Neuer Name", "password": "geheim1234", "band": "a", "channel": 40})

    def test_save_is_refused_while_the_hotspot_runs_and_for_bad_values(self):
        with self.assertRaises(ValueError) as e:
            self.save(active=True)
        self.assertIn("läuft", str(e.exception))
        self.assertFalse(os.path.exists(self.hs))
        for bad in ({"ssid": ""}, {"password": "kurz"}, {"band": "ac"}, {"channel": 99}, {"password": ""}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.save(bad)
        self.assertFalse(os.path.exists(self.hs))

    def test_save_checks_what_the_card_can(self):
        with mock.patch.object(H, "wifi_caps", lambda i: {"ap": False, "2ghz": True, "5ghz": False}):
            with self.assertRaises(ValueError):
                self.save()
        with mock.patch.object(H, "wifi_caps", lambda i: {"ap": True, "2ghz": True, "5ghz": False}):
            with self.assertRaises(ValueError):
                self.save({"band": "a", "channel": 0})

    def test_start_after_save_with_empty_password_uses_the_saved_one(self):
        self.save()
        nm, _ = self.start({"password": ""})
        add = nm.find("con", "add")[0]
        self.assertEqual(add[add.index("wifi-sec.psk") + 1], "geheim1234")

    def test_save_never_follows_a_planted_link(self):
        victim = os.path.join(self.d, "wichtig")
        open(victim, "w").write("unberührt")
        os.symlink(victim, self.hs)
        self.save()
        self.assertEqual(open(victim).read(), "unberührt")


class FakeSrtla:
    def __init__(self, uplinks):
        self.data = {"settings": {"uplinks": list(uplinks)}}
        self.set = []

    def set_settings(self, req, valid):
        self.set.append((req, valid))
        self.data["settings"]["uplinks"] = req["uplinks"]


class ServerHotspot(unittest.TestCase):
    CARD = {"iface": "wlan1", "ip": "", "camera_net": False, "up": False, "ssid": "", "signal": None, "ap": True, "band24": True, "band5": True, "hotspot": None}

    def make(self, cards=None, uplinks=("eth0", "wlan1"), valid=("eth0", "wlan1")):
        d = tempfile.mkdtemp()
        w = server.Wifi(d, False, mock.Mock(iface="eth2"), None, FakeSrtla(uplinks))
        st = {"helper_installed": True, "cards": cards if cards is not None else [dict(self.CARD)], "state": "idle", "message": "", "time": 0}
        patches = [mock.patch.object(w, "status", lambda: st), mock.patch.object(server, "iface_ips", lambda: [{"iface": v, "ip": "10.0.0.1"} for v in valid])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return w, d

    REQ = {"action": "hotspot_start", "iface": "wlan1", "ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 0}

    def sent(self, d):
        with open(os.path.join(d, "wifi-request")) as f:
            return json.load(f)

    def test_request_file_has_the_checked_values(self):
        w, d = self.make()
        w.request(dict(self.REQ, channel=11, extra="ignoriert"))
        r = self.sent(d)
        self.assertEqual(r, {"action": "hotspot_start", "iface": "wlan1", "ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 11})
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "wifi-request")).st_mode), 0o600)

    def test_invalid_requests(self):
        w, d = self.make()
        for bad in ({"ssid": ""}, {"ssid": 5}, {"ssid": " x"}, {"ssid": "pipbox-hotspot-x"}, {"password": "kurz"}, {"password": ""}, {"password": "ä" * 9},
                    {"password": 5}, {"band": "ac"}, {"channel": 99}, {"channel": "6"}, {"band": "a", "channel": 6}, {"iface": "wlan9"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                w.request(dict(self.REQ, **bad))
        self.assertFalse(os.path.exists(os.path.join(d, "wifi-request")))

    def test_camera_card_and_card_without_ap_support_are_refused(self):
        w, d = self.make([dict(self.CARD, camera_net=True)])
        with self.assertRaises(ValueError):
            w.request(self.REQ)
        w, d = self.make([dict(self.CARD, ap=False)])
        with self.assertRaises(ValueError):
            w.request(self.REQ)
        w, d = self.make([dict(self.CARD, band5=False)])
        with self.assertRaises(ValueError):
            w.request(dict(self.REQ, band="a", channel=36))
        w.request(self.REQ)                                                         # 2,4 GHz geht

    def test_connected_card_needs_confirmation(self):
        w, d = self.make([dict(self.CARD, ip="10.0.0.5", ssid="Heim")])
        with self.assertRaises(ValueError) as e:
            w.request(self.REQ)
        self.assertIn("Bestätigung", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(d, "wifi-request")))
        w.request(dict(self.REQ, confirm=True))
        self.assertTrue(os.path.exists(os.path.join(d, "wifi-request")))
        w, d = self.make([dict(self.CARD, ip="10.42.0.1", hotspot={"ssid": "Box", "running": True, "band": "bg", "channel": 0})])
        w.request(self.REQ)                                                         # Neustart eines laufenden Hotspots: nichts zu bestätigen

    def test_card_leaves_the_sending_networks(self):
        w, d = self.make()
        w.request(self.REQ)
        self.assertEqual(w.srtla.set[0][0], {"uplinks": ["eth0"]})
        self.assertEqual(w.srtla.data["settings"]["uplinks"], ["eth0"])

    def test_only_sending_network_blocks_the_start(self):
        w, d = self.make(uplinks=("wlan1",))
        with self.assertRaises(ValueError) as e:
            w.request(self.REQ)
        self.assertIn("einzige", str(e.exception))
        self.assertEqual(w.srtla.set, [])
        self.assertFalse(os.path.exists(os.path.join(d, "wifi-request")))
        w, d = self.make(uplinks=("wlan1", "eth9"), valid=("wlan1",))                 # das andere Netz ist gar nicht da: gilt als einziges
        with self.assertRaises(ValueError):
            w.request(self.REQ)

    def test_card_not_among_the_sending_networks_leaves_them_alone(self):
        w, d = self.make(uplinks=("eth0",))
        w.request(self.REQ)
        self.assertEqual(w.srtla.set, [])

    def test_stop_needs_a_hotspot(self):
        w, d = self.make()
        with self.assertRaises(ValueError):
            w.request({"action": "hotspot_stop", "iface": "wlan1"})
        w, d = self.make([dict(self.CARD, hotspot={"ssid": "Box", "running": True, "band": "bg", "channel": 0})])
        w.request({"action": "hotspot_stop", "iface": "wlan1"})
        self.assertEqual(self.sent(d), {"action": "hotspot_stop", "iface": "wlan1"})

    def test_client_actions_are_refused_while_hotspot_runs(self):
        w, d = self.make([dict(self.CARD, hotspot={"ssid": "Box", "running": True, "band": "bg", "channel": 0})])
        for a in ({"action": "scan", "iface": "wlan1"}, {"action": "connect", "iface": "wlan1", "ssid": "Heim", "password": ""}, {"action": "disconnect", "iface": "wlan1"}):
            with self.assertRaises(ValueError, msg=str(a)):
                w.request(a)

    def test_reserved_network_names(self):
        w, d = self.make()
        for a in ({"action": "connect", "iface": "wlan1", "ssid": "pipbox-hotspot-wlan1", "password": ""}, {"action": "forget", "ssid": "pipbox-hotspot-wlan1"}):
            with self.assertRaises(ValueError):
                w.request(a)

    def test_password_is_only_given_on_request_and_only_for_saved_hotspots(self):
        w, d = self.make()
        with open(os.path.join(d, "hotspot.json"), "w") as f:
            json.dump({"wlan1": {"ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 0}, "kaputt": 5}, f)
        self.assertEqual(w.hotspot_secret("wlan1"), {"iface": "wlan1", "ssid": "Box", "password": "geheim1234"})
        for bad in ("wlan0", "", None, "kaputt"):
            with self.assertRaises(ValueError, msg=str(bad)):
                w.hotspot_secret(bad)
        self.assertNotIn("geheim1234", json.dumps(w.hotspots().get("wlan1", {}).get("ssid")))

    def test_empty_password_is_fine_when_one_is_saved(self):
        w, d = self.make()
        with open(os.path.join(d, "hotspot.json"), "w") as f:
            json.dump({"wlan1": {"ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 0}}, f)
        w.request(dict(self.REQ, password=""))
        self.assertEqual(self.sent(d)["password"], "")                             # der Helfer nimmt dann das gespeicherte

    def saved_file(self, d, iface="wlan1", **kw):
        with open(os.path.join(d, "hotspot.json"), "w") as f:
            json.dump({iface: dict({"ssid": "Gespeichert", "password": "gespeichert99", "band": "a", "channel": 40}, **kw)}, f)

    def test_save_request_goes_to_the_helper_without_starting(self):
        w, d = self.make()
        w.request({"action": "hotspot_save", "iface": "wlan1", "ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 6})
        self.assertEqual(self.sent(d), {"action": "hotspot_save", "iface": "wlan1", "ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 6})
        self.assertEqual(w.srtla.set, [])                                                   # Speichern ändert die Sendewege nicht

    def test_save_needs_no_confirmation_even_on_a_connected_card(self):
        w, d = self.make([dict(self.CARD, ip="10.0.0.5", ssid="Heim")])
        w.request({"action": "hotspot_save", "iface": "wlan1", "ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 0})
        self.assertTrue(os.path.exists(os.path.join(d, "wifi-request")))

    def test_save_is_refused_while_the_hotspot_runs(self):
        w, d = self.make([dict(self.CARD, ip="10.42.0.1", hotspot={"ssid": "Box", "running": True, "band": "bg", "channel": 0})])
        with self.assertRaises(ValueError) as e:
            w.request({"action": "hotspot_save", "iface": "wlan1", "ssid": "Box", "password": "geheim1234"})
        self.assertIn("ausschalten", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(d, "wifi-request")))

    def test_save_validates_like_start(self):
        w, d = self.make()
        for bad in ({"ssid": ""}, {"ssid": " x"}, {"password": "kurz"}, {"password": ""}, {"band": "ac"}, {"channel": 99}, {"iface": "wlan9"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                w.request(dict({"action": "hotspot_save", "iface": "wlan1", "ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 0}, **bad))
        self.assertFalse(os.path.exists(os.path.join(d, "wifi-request")))

    def test_start_without_values_uses_the_saved_settings(self):
        w, d = self.make()
        self.saved_file(d)
        w.request({"action": "hotspot_start", "iface": "wlan1"})
        self.assertEqual(self.sent(d), {"action": "hotspot_start", "iface": "wlan1", "ssid": "Gespeichert", "password": "", "band": "a", "channel": 40})
        self.assertNotIn("gespeichert99", open(os.path.join(d, "wifi-request")).read())      # das gespeicherte Passwort holt sich der Helfer selbst

    def test_start_without_saved_settings_asks_to_set_them_up_first(self):
        w, d = self.make()
        with self.assertRaises(ValueError) as e:
            w.request({"action": "hotspot_start", "iface": "wlan1"})
        self.assertIn("Einstellen", str(e.exception))
        self.saved_file(d, password="")                                                     # ohne Passwort gilt es nicht als eingestellt
        with self.assertRaises(ValueError):
            w.request({"action": "hotspot_start", "iface": "wlan1"})
        self.assertFalse(os.path.exists(os.path.join(d, "wifi-request")))

    def test_start_from_saved_settings_still_needs_confirmation_on_a_connected_card(self):
        w, d = self.make([dict(self.CARD, ip="10.0.0.5", ssid="Heim")])
        self.saved_file(d)
        with self.assertRaises(ValueError) as e:
            w.request({"action": "hotspot_start", "iface": "wlan1"})
        self.assertIn("Bestätigung", str(e.exception))
        w.request({"action": "hotspot_start", "iface": "wlan1", "confirm": True})
        self.assertTrue(os.path.exists(os.path.join(d, "wifi-request")))

    def test_start_from_saved_settings_leaves_the_sending_networks_like_before(self):
        w, d = self.make()
        self.saved_file(d)
        w.request({"action": "hotspot_start", "iface": "wlan1"})
        self.assertEqual(w.srtla.set[0][0], {"uplinks": ["eth0"]})

    def test_demo_flow_with_save_then_switch(self):
        w = server.Wifi(tempfile.mkdtemp(), True, mock.Mock(iface="eth2"), None, None)
        with self.assertRaises(ValueError):
            w.request({"action": "hotspot_start", "iface": "wlan1"})                          # nie eingestellt
        w.request({"action": "hotspot_save", "iface": "wlan1", "ssid": "Box", "password": "geheim1234", "band": "bg", "channel": 6})
        c = [c for c in w.status()["cards"] if c["iface"] == "wlan1"][0]
        self.assertEqual((c["hotspot"]["ssid"], c["hotspot"]["running"], c["ip"]), ("Box", False, ""))        # gespeichert, aber aus
        w.request({"action": "hotspot_start", "iface": "wlan1"})
        c = [c for c in w.status()["cards"] if c["iface"] == "wlan1"][0]
        self.assertTrue(c["hotspot"]["running"])
        self.assertEqual(w.hotspot_secret("wlan1")["password"], "geheim1234")

    def test_demo_flow(self):
        w = server.Wifi(tempfile.mkdtemp(), True, mock.Mock(iface="eth2"), None, None)
        st = w.status()
        self.assertEqual([c["hotspot"] for c in st["cards"]], [None, None])
        w.request(dict(self.REQ, iface="wlan1"))
        c = [c for c in w.status()["cards"] if c["iface"] == "wlan1"][0]
        self.assertTrue(c["hotspot"]["running"])
        self.assertEqual(w.hotspot_secret("wlan1")["password"], "geheim1234")
        w.request({"action": "hotspot_stop", "iface": "wlan1"})
        self.assertFalse([c for c in w.status()["cards"] if c["iface"] == "wlan1"][0]["hotspot"]["running"])
        self.assertTrue(w.status()["action"].startswith("hotspot_"))


class CameraSide(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.old = dd.HOTSPOT_FILE
        dd.HOTSPOT_FILE = os.path.join(self.d, "hotspot.json")
        self.addCleanup(lambda: setattr(dd, "HOTSPOT_FILE", self.old))
        with open(dd.HOTSPOT_FILE, "w") as f:
            json.dump({"wlan1": {"ssid": "Box Netz", "password": "geheim1234", "band": "bg", "channel": 0}}, f)

    def nm(self, conn="pipbox-hotspot-wlan1", ssid="Box Netz", psk="", mode="ap"):
        def run(cmd):
            if cmd[:4] == ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION"]:
                return "wlan1:wifi:connected:" + conn
            if "802-11-wireless.ssid" in cmd:
                return ssid
            if "802-11-wireless.mode" in cmd:
                return mode
            if "802-11-wireless-security.psk" in cmd:
                return psk
            if "802-11-wireless-security.key-mgmt" in cmd:
                return "wpa-psk"
            if "IP4.ADDRESS" in cmd:
                return "10.42.0.1/24"
            return ""
        return run

    def test_hotspot_password_comes_from_the_file_when_networkmanager_does_not_give_it(self):
        with mock.patch.object(dd, "run", self.nm()), mock.patch.object(dd, "other_connections", lambda skip: []):
            o = dd.nm_wifi_options()[0]
        self.assertEqual((o["type"], o["ssid"], o["password"], o["secret_missing"]), ("hotspot", "Box Netz", "geheim1234", False))

    def test_networkmanager_password_wins_and_wrong_names_are_ignored(self):
        with mock.patch.object(dd, "run", self.nm(psk="ausnm1234")), mock.patch.object(dd, "other_connections", lambda skip: []):
            self.assertEqual(dd.nm_wifi_options()[0]["password"], "ausnm1234")
        with mock.patch.object(dd, "run", self.nm(ssid="Anderer Name")), mock.patch.object(dd, "other_connections", lambda skip: []):
            o = dd.nm_wifi_options()[0]
        self.assertEqual((o["password"], o["secret_missing"]), ("", True))                # Datei passt nicht zum Netz: von Hand eintragen
        with mock.patch.object(dd, "run", self.nm(conn="Fremdes Netz")), mock.patch.object(dd, "other_connections", lambda skip: []):
            self.assertEqual(dd.nm_wifi_options()[0]["password"], "")                      # nur das Profil dieser Box wird aus der Datei bedient

    def test_broken_file(self):
        open(dd.HOTSPOT_FILE, "w").write("kaputt")
        self.assertEqual(dd.hotspot_password("wlan1", "Box Netz"), "")
        os.remove(dd.HOTSPOT_FILE)
        self.assertEqual(dd.hotspot_password("wlan1", "Box Netz"), "")

    def test_daemon_points_at_its_state_folder(self):
        d = tempfile.mkdtemp()
        dd.Daemon(d)
        self.assertEqual(dd.HOTSPOT_FILE, os.path.join(d, "hotspot.json"))


class LogsAndPage(unittest.TestCase):
    def test_hotspot_name_and_password_are_scrubbed_from_logs(self):
        L = load("pipbox-logs")
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "hotspot.json"), "w") as f:
            json.dump({"wlan1": {"ssid": "MeinBoxNetz", "password": "hotspotpasswort"}}, f)
        sc = L.Scrubber()
        with mock.patch.object(L, "STATE", d), mock.patch.object(L, "run", lambda *a, **k: "pipbox-hotspot-wlan1:802-11-wireless\nHeimnetz:802-11-wireless\n"):
            L.collect_secrets(sc)
        out = sc.scrub("Netz MeinBoxNetz Passwort hotspotpasswort Profil pipbox-hotspot-wlan1 Heimnetz")
        self.assertNotIn("MeinBoxNetz", out)
        self.assertNotIn("hotspotpasswort", out)
        self.assertNotIn("Heimnetz", out)
        self.assertIn("pipbox-hotspot-wlan1", out)

    def test_page(self):
        page = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
        for needle in ("Hotspot-Modus", "hs-toggle", "hs-edit", "hs-save", "hs-show", "Einstellen", "Passwort ausblenden", "/api/wifi/hotspot",
                       "hotspot_start", "hotspot_stop", "hotspot_save", "Verbindungen zum Senden (Upload)", "WLAN-Verbindungen (z. B. Handy-Hotspot"):
            self.assertIn(needle, page)
        for gone in ('id="h_start"', 'id="h_stop"', 'id="h_if"', 'id="h_pw"', 'id="h_pw_out"', "WLAN-Hotspot (Stick als Zugangspunkt der Box)"):
            self.assertNotIn(gone, page)                                                  # alter Abschnitt ist durch den Schalter je Stick ersetzt
        self.assertNotIn("Netze zum Senden", page.replace("Nur die Netze zum Senden anzeigen", ""))

    def test_switch_per_stick_follows_the_issue(self):
        page = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
        js = page[page.index("function hsControls("):page.index("function renderWifiDev(")]
        self.assertIn('${on?"An":"Aus"}', js)                                           # Schalter zeigt An oder Aus
        self.assertIn('class="sec hs-edit"', js)                                          # "Einstellen" nur im Zustand Aus
        self.assertLess(js.index("hs-toggle"), js.index("hs-edit"))
        self.assertIn("c.ap===false", js)                                                 # Sticks ohne Zugangspunkt-Betrieb bekommen keinen Schalter
        toggle = page[page.index("async function hsToggle("):page.index("async function hsSave(")]
        self.assertIn("if(!c.hotspot)", toggle)                                           # noch nie eingestellt: erst "Einstellen", nie ohne Passwort starten
        self.assertIn('{action:"hotspot_start",iface}', toggle)                           # Einschalten schickt nur die Karte: Werte kommen aus den gespeicherten


if __name__ == "__main__":
    unittest.main()
