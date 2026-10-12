"""Tests für die Systemwarnungen im Twitch-Chat (SysAlerts in server.py, Issue #67): Einstellungen, Prüfung, Warnen, Pause, "wieder normal", Fehlversuche und die Oberfläche.
Nichts davon sendet etwas: Bot und Messwerte sind Attrappen, die Uhr wird von Hand gestellt.
  python3 tools/test_sysalerts.py"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as _f:
    PAGE = _f.read()
with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as _f:
    SRC = _f.read()


class FakeStore:
    def __init__(self, ready=True):
        self.ready = ready

    def notify_settings(self):
        return {"login": "bot", "channel": "kanal", "token": "geheim" * 5} if self.ready else {"login": "", "channel": "", "token": ""}


class FakeNotifier:
    def __init__(self, ready=True, live=True):
        self.store, self.live, self.sent, self.fail = FakeStore(ready), live, [], False

    def _live(self):
        return self.live

    def _deliver(self, cfg, text, wait=False):
        if self.fail:
            return False, "Twitch nicht erreichbar"
        self.sent.append(text)
        return True, "gesendet"


class FakeSampler:
    def __init__(self):
        self.v = {"temp_c": 50.0, "cpu": {"avg": 20.0}, "mem": {"used_pct": 30.0}}

    def sample(self):
        return self.v

    def set(self, temp=None, cpu=None, ram=None):
        if temp is not None:
            self.v["temp_c"] = temp
        if cpu is not None:
            self.v["cpu"]["avg"] = cpu
        if ram is not None:
            self.v["mem"]["used_pct"] = ram


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "sysalerts.json")
        self.now = 1000.0
        self.n = FakeNotifier()
        self.s = FakeSampler()
        self.sa = self.make()

    def make(self):
        return server.SysAlerts(self.path, self.n, self.s, mono=lambda: self.now, wall=lambda: 1.7e9 + self.now)

    def on(self, **kw):
        req = {"enabled": True}
        req.update(kw)
        return self.sa.save(req)

    def ticks(self, k, step=10):
        for _ in range(k):
            self.sa.tick()
            self.now += step


class Settings(Base):
    def test_defaults_are_off_and_sensible(self):
        d = self.sa.status()
        self.assertFalse(d["enabled"])
        self.assertEqual((d["temp"], d["cpu"], d["ram"]), ({"on": True, "limit": 75}, {"on": True, "limit": 90}, {"on": True, "limit": 90}))
        self.assertEqual((d["cooldown"], d["recover"], d["only_live"]), (10, True, True))
        self.assertFalse(os.path.exists(self.path))                                    # ohne Speichern keine Datei

    def test_save_validates_everything_and_keeps_old_values_on_error(self):
        bad = [{"temp": {"limit": 39}}, {"temp": {"limit": 101}}, {"cpu": {"limit": 19}}, {"ram": {"limit": "90"}}, {"ram": {"limit": True}}, {"cooldown": 0}, {"cooldown": 121},
               {"enabled": "ja"}, {"recover": 1}, {"temp": {"on": "x"}}, {"temp": 5}, {"msg_alert": ""}, {"msg_alert": "/ban alle"}, {"msg_ok": ".me x"},
               {"msg_alert": "zwei\nZeilen"}, {"msg_ok": "x" * 301}]
        for req in bad:
            with self.assertRaises(ValueError, msg=repr(req)):
                self.sa.save(req)
        self.assertEqual(self.sa.status()["temp"]["limit"], 75)
        self.assertFalse(os.path.exists(self.path))
        with self.assertRaises(ValueError):
            self.sa.save([1, 2])

    def test_save_persists_and_a_new_instance_reads_it(self):
        self.on(temp={"limit": 80}, cooldown=5, recover=False, msg_alert="Achtung {Name} {Wert}")
        sa2 = self.make()
        d = sa2.status()
        self.assertEqual((d["enabled"], d["temp"]["limit"], d["cooldown"], d["recover"], d["msg_alert"]), (True, 80, 5, False, "Achtung {Name} {Wert}"))
        self.assertEqual(json.load(open(self.path))["temp"], {"on": True, "limit": 80})

    def test_a_hand_edited_file_with_bad_values_falls_back_per_value(self):
        json.dump({"enabled": True, "cooldown": 9999, "temp": {"limit": 5, "on": False}, "cpu": {"limit": 85}, "msg_alert": "/x", "recover": "ja"}, open(self.path, "w"))
        d = self.make().status()
        self.assertEqual((d["enabled"], d["cooldown"], d["temp"], d["cpu"]["limit"], d["msg_alert"], d["recover"]),
                         (True, 10, {"on": False, "limit": 75}, 85, server.SysAlerts.DEFAULT_ALERT, True))
        open(self.path, "w").write("{kaputt")
        self.assertFalse(self.make().status()["enabled"])

    def test_partial_save_keeps_the_rest(self):
        self.on(cpu={"limit": 70})
        self.sa.save({"temp": {"limit": 60}})
        d = self.sa.status()
        self.assertEqual((d["cpu"]["limit"], d["temp"]["limit"], d["enabled"]), (70, 60, True))

    def test_status_has_no_secrets(self):
        s = json.dumps(self.on())
        self.assertNotIn("geheim", s)
        self.assertNotIn("token", s)
        self.assertTrue(self.sa.status()["ready"])
        self.n.store.ready = False
        self.assertFalse(self.sa.status()["ready"])


class Alerting(Base):
    def test_nothing_happens_while_off_or_without_twitch(self):
        self.s.set(temp=99)
        self.ticks(5)
        self.assertEqual(self.n.sent, [])
        self.on()
        self.n.store.ready = False
        self.ticks(5)
        self.assertEqual(self.n.sent, [])

    def test_a_warning_needs_three_measurements_in_a_row(self):
        self.on()
        self.s.set(temp=80)
        self.ticks(2)
        self.assertEqual(self.n.sent, [])
        self.s.set(temp=60)                                                           # kurzer Ausreißer ist keine Warnung
        self.ticks(1)
        self.s.set(temp=80)
        self.ticks(2)
        self.assertEqual(self.n.sent, [])
        self.ticks(1)
        self.assertEqual(self.n.sent, ["Warnung: CPU-Temperatur bei 80 °C (Grenze 75 °C)"])

    def test_each_metric_has_its_own_text_and_unit(self):
        self.on()
        self.s.set(cpu=95, ram=93)
        self.ticks(3)
        self.assertEqual(self.n.sent, ["Warnung: CPU-Auslastung bei 95 % (Grenze 90 %)", "Warnung: RAM-Auslastung bei 93 % (Grenze 90 %)"])

    def test_a_metric_switched_off_is_ignored(self):
        self.on(temp={"on": False})
        self.s.set(temp=99)
        self.ticks(6)
        self.assertEqual(self.n.sent, [])

    def test_reminder_only_after_the_pause_and_never_in_between(self):
        self.on(cooldown=1)                                                           # 60 s
        self.s.set(temp=80)
        self.ticks(3)                                                                  # Warnung bei t=1020
        self.assertEqual(len(self.n.sent), 1)
        self.ticks(5)                                                                  # bis t=1070: noch keine Pause
        self.assertEqual(len(self.n.sent), 1)
        self.ticks(3)                                                                  # t=1100: 80 s nach der Warnung
        self.assertEqual(len(self.n.sent), 2)
        self.ticks(3)
        self.assertEqual(len(self.n.sent), 2)

    def test_recovery_message_once_after_hysteresis(self):
        self.on()
        self.s.set(temp=80)
        self.ticks(3)
        self.s.set(temp=73)                                                           # unter der Grenze, aber nicht 3 Grad darunter: noch nicht normal
        self.ticks(3)
        self.assertEqual(len(self.n.sent), 1)
        self.s.set(temp=71)
        self.ticks(1)
        self.assertEqual(self.n.sent[-1], "CPU-Temperatur wieder normal (71 °C)")
        self.ticks(5)
        self.assertEqual(len(self.n.sent), 2)                                          # nur einmal
        self.assertEqual(self.sa.status()["alerting"], [])

    def test_no_recovery_message_when_switched_off(self):
        self.on(recover=False)
        self.s.set(ram=95)
        self.ticks(3)
        self.s.set(ram=50)
        self.ticks(3)
        self.assertEqual(len(self.n.sent), 1)

    def test_a_new_episode_warns_again_without_waiting_for_the_pause_only_after_it(self):
        self.on(cooldown=1)
        self.s.set(cpu=95)
        self.ticks(3)
        self.s.set(cpu=40)
        self.ticks(1)                                                                  # normal
        self.s.set(cpu=95)
        self.ticks(3)                                                                  # nur 40 s später: Pause (60 s) noch nicht um
        self.assertEqual([t for t in self.n.sent if t.startswith("Warnung")].__len__(), 1)
        self.ticks(3)
        self.assertEqual([t for t in self.n.sent if t.startswith("Warnung")].__len__(), 2)

    def test_only_while_sending(self):
        self.on()
        self.n.live = False
        self.s.set(temp=90)
        self.ticks(6)
        self.assertEqual(self.n.sent, [])
        self.n.live = True
        self.ticks(2)                                                                  # die Zähler begannen neu
        self.assertEqual(self.n.sent, [])
        self.ticks(1)
        self.assertEqual(len(self.n.sent), 1)
        self.sa.save({"only_live": False})
        self.n.live = False
        self.s.set(temp=40)
        self.ticks(1)
        self.assertEqual(self.n.sent[-1], "CPU-Temperatur wieder normal (40 °C)")      # ohne "nur während der Sendung" gilt es immer

    def test_failed_send_is_retried_after_a_minute_and_does_not_mark_the_warning(self):
        self.on()
        self.n.fail = True
        self.s.set(temp=90)
        self.ticks(3)
        self.assertEqual(self.sa.status()["last"]["ok"], False)
        self.assertEqual(self.sa.status()["alerting"], [])
        self.n.fail = False
        self.ticks(5)                                                                  # unter 60 s: kein Versuch
        self.assertEqual(self.n.sent, [])
        self.ticks(3)
        self.assertEqual(len(self.n.sent), 1)
        self.assertTrue(self.sa.status()["last"]["ok"])

    def test_unreadable_values_are_ignored(self):
        self.on()
        self.s.v = {"temp_c": None, "cpu": {"avg": "viel"}, "mem": {"used_pct": float("nan")}}
        self.ticks(5)
        self.assertEqual(self.n.sent, [])
        self.s.sample = lambda: 1 / 0                                                  # Messfehler legen den Dienst nicht lahm
        self.ticks(2)
        self.assertEqual(self.sa.values(), {"temp": None, "cpu": None, "ram": None})

    def test_templates_cannot_inject_more_than_the_three_words(self):
        self.on(msg_alert="{Name}: {Wert} {Name} {Unbekannt} {Grenze}")
        self.s.set(temp=80)
        self.ticks(3)
        self.assertEqual(self.n.sent, ["CPU-Temperatur: 80 °C CPU-Temperatur {Unbekannt} 75 °C"])

    def test_saving_resets_the_counters(self):
        self.on()
        self.s.set(temp=80)
        self.ticks(2)
        self.sa.save({"cooldown": 5})
        self.ticks(2)
        self.assertEqual(self.n.sent, [])
        self.ticks(1)
        self.assertEqual(len(self.n.sent), 1)


class Interface(unittest.TestCase):
    def test_new_line_sits_below_the_battery_warning_in_the_cameras_card(self):
        i_tw, i_sa = PAGE.index('id="tw_sec"'), PAGE.index('id="sa_sec"')
        self.assertLess(i_tw, i_sa)
        self.assertLess(i_sa, PAGE.index('id="c_cams"') + 10 ** 6)
        self.assertIn("Systemwarnungen im Twitch-Chat", PAGE)
        between = PAGE[i_tw:i_sa]
        self.assertEqual(between.count("</details>"), 1)                               # direkt dahinter, nicht in der Akku-Warnung

    def test_form_fields_and_calls(self):
        for el in ("sa_on", "sa_t_on", "sa_t", "sa_c_on", "sa_c", "sa_r_on", "sa_r", "sa_cd", "sa_rec", "sa_live", "sa_msg_a", "sa_msg_o", "sa_save", "sa_test", "sa_status", "sa_err"):
            self.assertIn('id="%s"' % el, PAGE, el)
        self.assertIn('srtlaCall("GET","/api/sysalerts")', PAGE)
        self.assertIn('srtlaCall("POST","/api/sysalerts",saForm())', PAGE)
        self.assertIn('srtlaCall("POST","/api/twitch/test",{})', PAGE)
        self.assertIn('due("sa","c_cams",60000)', PAGE)                                # nur nachladen, wenn die Karte offen ist
        self.assertIn('data-hlp', PAGE[PAGE.index('id="sa_sec"'):PAGE.index('id="sa_status"')])      # Erklärung hinter dem ?-Knopf

    def test_names_and_texts_never_become_html(self):
        js = PAGE[PAGE.index("// ---- Systemwarnungen im Twitch-Chat"):PAGE.index('saLoad(); setInterval')]
        self.assertIn("esc(s.text)", js)
        self.assertNotIn("innerHTML", js)

    def test_server_routes_and_service_are_wired(self):
        self.assertIn('if path == "/api/sysalerts":\n            return self.reply(200, self.sysalerts.status())', SRC)
        self.assertIn('if path == "/api/sysalerts":\n                return self.reply(200, self.sysalerts.save(d))', SRC)
        self.assertIn("threading.Thread(target=Handler.sysalerts.run, daemon=True).start()", SRC)
        self.assertIn("Handler.sysalerts = SysAlerts(", SRC)

    def test_demo_does_not_start_the_service_and_sends_nothing(self):
        start = SRC.index("threading.Thread(target=Handler.sysalerts.run")
        block = SRC[SRC.rfind("if not args.demo:", 0, start):start]
        self.assertIn("if not args.demo:", block)


if __name__ == "__main__":
    unittest.main(verbosity=1)
