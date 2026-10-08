"""Tests für die Pulsanzeige (Herzschlag in der Kopfleiste am Handy, Grenzen unter SRTLA > Pulsanzeige) und die Untermenüs der SRTLA-Karte."""
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
IFACES = ["eth0", "eth1"]


class Settings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = server.SrtlaStore(os.path.join(self.dir.name, "srtla.json"))

    def tearDown(self):
        self.dir.cleanup()

    def test_defaults_are_green_from_6000_and_yellow_from_1000(self):
        st = self.store.public()["settings"]
        self.assertEqual((st["pulse_green_kbps"], st["pulse_yellow_kbps"]), (6000, 1000))

    def test_values_are_saved_and_survive_other_changes(self):
        self.store.set_settings({"uplinks": IFACES, "pulse_green_kbps": 8000, "pulse_yellow_kbps": 2500}, IFACES)
        st = self.store.public()["settings"]
        self.assertEqual((st["pulse_green_kbps"], st["pulse_yellow_kbps"]), (8000, 2500))
        self.store.set_settings({"uplinks": IFACES, "latency_ms": 3000}, IFACES)                    # ein veralteter Stand der Seite überschreibt sie nicht
        st = self.store.public()["settings"]
        self.assertEqual((st["pulse_green_kbps"], st["pulse_yellow_kbps"], st["latency_ms"]), (8000, 2500, 3000))
        again = server.SrtlaStore(self.store.path)                                                  # auch nach einem Neustart
        self.assertEqual(again.public()["settings"]["pulse_green_kbps"], 8000)

    def test_bad_values_are_refused_and_change_nothing(self):
        for g, y in ((1000, 1000), (1000, 6000), (0, 0), (100001, 1000), (6000, 0), (6000, -5), ("abc", 1000), (6000, None), (True, 1)):
            with self.assertRaises(ValueError, msg=repr((g, y))):
                self.store.set_settings({"uplinks": IFACES, "pulse_green_kbps": g, "pulse_yellow_kbps": y}, IFACES)
        st = self.store.public()["settings"]
        self.assertEqual((st["pulse_green_kbps"], st["pulse_yellow_kbps"]), (6000, 1000))

    def test_old_settings_files_get_the_defaults(self):
        with open(self.store.path, "w") as f:
            json.dump({"servers": [], "selected": None, "settings": {"min_kbps": 400, "max_kbps": 9000, "latency_ms": 2000, "uplinks": ["eth0"], "spread": "all"}}, f)
        st = server.SrtlaStore(self.store.path).public()["settings"]
        self.assertEqual((st["min_kbps"], st["pulse_green_kbps"], st["pulse_yellow_kbps"]), (400, 6000, 1000))

    def test_pulse_does_not_ask_for_a_restart_of_the_stream(self):
        before = server.srtla_signature(self.store.data)
        self.store.set_settings({"uplinks": IFACES, "pulse_green_kbps": 9000, "pulse_yellow_kbps": 3000}, IFACES)
        self.assertEqual(before, server.srtla_signature(self.store.data))


class Backup(unittest.TestCase):
    def test_pulse_is_part_of_the_srtla_settings_in_the_backup(self):
        t = server.SettingsTransfer.__new__(server.SettingsTransfer)
        raw = {"servers": [], "selected": None, "settings": {"min_kbps": 300, "max_kbps": 12000, "latency_ms": 4000, "spread": "best", "uplinks": ["eth0"],
                                                           "pulse_green_kbps": 7000, "pulse_yellow_kbps": 1500}}
        data, _ = t._clean_srtla(raw)
        self.assertEqual((data["settings"]["pulse_green_kbps"], data["settings"]["pulse_yellow_kbps"]), (7000, 1500))
        del raw["settings"]["pulse_green_kbps"], raw["settings"]["pulse_yellow_kbps"]                 # ältere Sicherung ohne die Angaben
        data, _ = t._clean_srtla(raw)
        self.assertEqual((data["settings"]["pulse_green_kbps"], data["settings"]["pulse_yellow_kbps"]), (6000, 1000))
        raw["settings"].update(pulse_green_kbps=500, pulse_yellow_kbps=900)
        with self.assertRaises(ValueError):
            t._clean_srtla(raw)


class Page(unittest.TestCase):
    def test_srtla_card_has_the_four_submenus_in_order(self):
        card = PAGE[PAGE.index('id="srtlacard"'):PAGE.index('id="c_cams"')]
        ids = re.findall(r'<details class="subsec" id="(srt_\w+)"( open)?><summary>([^<]+)</summary>', card)
        self.assertEqual([(i, t) for i, _, t in ids], [("srt_srv", "SRTLA-Server"), ("srt_lines", "Leitungssteuerung"), ("srt_rate", "Bitrate und Latenz"), ("srt_pulse", "Pulsanzeige")])
        lines = card[card.index('id="srt_lines"'):card.index('id="srt_rate"')]
        self.assertIn("Verteilung der Netze", lines)
        self.assertIn('id="b_spread"', lines)                                                       # "Verteilung der Netze" gehört in die Leitungssteuerung
        self.assertIn('id="b_min"', card[card.index('id="srt_rate"'):card.index('id="srt_pulse"')])
        pulse = card[card.index('id="srt_pulse"'):]
        for needle in ('id="b_pgreen"', 'id="b_pyellow"', "Rot gilt immer ab 0 kbit/s"):
            self.assertIn(needle, pulse)
        self.assertTrue(pulse.index('id="b_pred"') < pulse.index('id="b_pyellow"') < pulse.index('id="b_pgreen"'))   # erst Rot, dann Gelb, dann Grün
        self.assertRegex(pulse, r'<input id="b_pred"[^>]*value="0"[^>]*disabled')                   # Rot ist fest (ab 0) und gesperrt
        self.assertNotIn('"b_pred"', PAGE[PAGE.index("async function saveSrtlaSettings("):PAGE.index('$("b_save").addEventListener')])   # und wird nie gespeichert

    def test_every_submenu_saves_with_one_function(self):
        self.assertEqual(PAGE.count("async function saveSrtlaSettings("), 1)
        for bid in ("b_save", "b_save_spread", "b_save_pulse"):
            self.assertIn('$("%s").addEventListener("click"' % bid, PAGE)
        body = PAGE[PAGE.index("async function saveSrtlaSettings("):PAGE.index('$("b_save").addEventListener')]
        for field in ("min_kbps", "max_kbps", "latency_ms", "uplinks", "spread", "pulse_green_kbps", "pulse_yellow_kbps"):
            self.assertIn(field, body)                                                              # nie nur ein Teil: sonst überschreibt ein Speichern die anderen Werte
        self.assertIn("askRestart()", body)

    def test_header_has_the_ecg_and_the_toggle_only_while_streaming_on_the_phone(self):
        head = PAGE[PAGE.index("<header>"):PAGE.index("</header>")]
        self.assertIn('id="hdr_pulse"', head)
        self.assertIn('class="ecg"', head)                                                          # EKG-Linie statt Herz
        self.assertNotIn("favorite", head)
        self.assertTrue(head.index('id="hdr_live"') < head.index('id="hdr_more"'))                  # der Knopf zum Ein- und Ausklappen steht ganz rechts
        self.assertIn("#hdr_pulse,#hdr_more{display:none}", PAGE)                                   # am Rechner und ohne Sendung nie sichtbar
        media = PAGE[PAGE.index("#hdr_live{display:none!important}"):]
        self.assertIn("body.streaming:not(.hdr-open) #hdr_pulse{display:inline-flex}", media)       # zugeklappt: Pulsanzeige
        self.assertIn("body.streaming #hdr_more{display:inline-flex}", media)
        self.assertIn("body.streaming:not(.hdr-open) header .hdr>*:not(#hdr_pulse):not(#hdr_more){display:none!important}", media)
        self.assertNotIn("hdr-open header{flex-wrap", media)                                        # aufgeklappt: die Ansicht wie ohne Pulsanzeige, keine zweite Zeile
        self.assertNotRegex(media, r"hdr-open #hdr_pulse\{display:inline")                         # aufgeklappt ist die Pulsanzeige weg
        self.assertIn('document.body.classList.toggle("streaming",on)', PAGE)
        self.assertIn('document.body.classList.remove("hdr-open")', PAGE)                           # mit jedem Start oder Ende ist der Kopf wieder zugeklappt bzw. ganz da
        self.assertIn("#hdr_more[aria-expanded=true] svg{transform:rotate(180deg)}", PAGE)          # Pfeil nach links zum Aufklappen, nach rechts zum Zuklappen

    def test_ecg_flows_and_glides_between_levels(self):
        self.assertNotIn("@keyframes ecgmove", PAGE)                                                # keine feste CSS-Schleife mehr: sie sprang beim Wechsel der Stufe
        fn = PAGE[PAGE.index("const ECG={"):PAGE.index("function paintPulse")]
        self.assertIn("ECG.speed+=(ECG.target-ECG.speed)*(1-Math.exp(-dt/.7))", fn)                 # die Geschwindigkeit gleitet zum Ziel
        self.assertIn("%32", fn)                                                                    # eine Periode, nahtlos
        self.assertIn('ECG.target=lv==="ok"?14.5:(lv==="warn"?21.3:35.5)', PAGE)                     # je schlechter, desto schneller
        self.assertIn("transition:color .9s ease", PAGE)                                            # die Farbe blendet über
        self.assertIn("el.offsetParent===null||document.hidden", PAGE)                              # unsichtbar: kein Rechnen

    def test_value_has_a_fixed_width_so_the_line_does_not_move(self):
        self.assertIn(".hpulse b{font-size:15px;display:inline-block;min-width:5ch;text-align:right}", PAGE)   # 1-, 2- und 3-stellige Werte

    def test_colour_follows_the_saved_limits(self):
        fn = PAGE[PAGE.index("function pulseLevel(kbps){"):PAGE.index("function paintPulse")]
        self.assertIn("pulse_green_kbps", fn)
        self.assertIn("pulse_yellow_kbps", fn)
        self.assertIn('kbps>=g?"ok":(kbps>=y?"warn":"crit")', fn)                                    # grün ab, gelb ab, darunter rot
        self.assertIn("paintPulse(tot)", PAGE)
        self.assertIn("(+mbit||0)*1000", PAGE)                                                       # Mbit/s der Netze in kbit/s

    def test_reduced_motion_and_accessible_name(self):
        self.assertIn("@media(prefers-reduced-motion:reduce){.hpulse{transition:none}", PAGE)
        self.assertIn('role="img"', PAGE[PAGE.index('id="hdr_pulse"'):PAGE.index('id="hdr_pulse"') + 200])
        self.assertIn('el.setAttribute("aria-label",t)', PAGE)


if __name__ == "__main__":
    unittest.main()
