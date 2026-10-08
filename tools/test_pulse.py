"""Tests für den Kopf am Handy während der Sendung (Pulsanzeige und Kamerapunkte des Chats ziehen in den Kopf) und die Untermenüs der SRTLA-Karte."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
SRC = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


class SrtlaCard(unittest.TestCase):
    def test_srtla_card_has_the_three_submenus_in_order(self):
        card = PAGE[PAGE.index('id="srtlacard"'):PAGE.index('id="c_cams"')]
        ids = re.findall(r'<details class="subsec" id="(srt_\w+)"( open)?><summary>([^<]+)</summary>', card)
        self.assertEqual([(i, t) for i, _, t in ids], [("srt_srv", "SRTLA-Server"), ("srt_lines", "Leitungssteuerung"), ("srt_rate", "Bitrate und Latenz")])
        lines = card[card.index('id="srt_lines"'):card.index('id="srt_rate"')]
        self.assertIn("Verteilung der Netze", lines)
        self.assertIn('id="b_spread"', lines)                                                       # "Verteilung der Netze" gehört in die Leitungssteuerung
        self.assertIn('id="b_min"', card[card.index('id="srt_rate"'):])

    def test_every_submenu_saves_with_one_function(self):
        self.assertEqual(PAGE.count("async function saveSrtlaSettings("), 1)
        for bid in ("b_save", "b_save_spread"):
            self.assertIn('$("%s").addEventListener("click"' % bid, PAGE)
        body = PAGE[PAGE.index("async function saveSrtlaSettings("):PAGE.index('$("b_save").addEventListener')]
        for field in ("min_kbps", "max_kbps", "latency_ms", "uplinks", "spread"):
            self.assertIn(field, body)                                                              # nie nur ein Teil: sonst überschreibt ein Speichern die anderen Werte
        self.assertIn("askRestart()", body)

    def test_our_own_pulse_display_is_gone(self):
        for gone in ("hdr_pulse", "hpulse", "paintPulse", "pulseLevel", "pulse_green_kbps", "pulse_yellow_kbps", "b_pgreen", "srt_pulse"):
            self.assertNotIn(gone, PAGE)
        self.assertNotIn("pulse_green_kbps", SRC)
        self.assertNotIn("check_pulse", SRC)
        self.assertNotIn("pulse_green_kbps", server.SrtlaStore.DEFAULT_SETTINGS)


class Header(unittest.TestCase):
    def test_header_has_a_place_for_the_chat_pulse_and_the_toggle(self):
        head = PAGE[PAGE.index("<header>"):PAGE.index("</header>")]
        self.assertIn('<span id="hdr_sig"></span>', head)
        self.assertTrue(head.index('id="hdr_live"') < head.index('id="hdr_more"'))                  # der Knopf zum Ein- und Ausklappen steht ganz rechts
        self.assertIn("#hdr_sig,#hdr_more{display:none}", PAGE)                                     # am Rechner und ohne Sendung nie sichtbar
        media = PAGE[PAGE.index("#hdr_live{display:none!important}"):]
        self.assertIn("body.streaming:not(.hdr-open) #hdr_sig{display:inline-flex", media)          # zugeklappt: Pulsanzeige und Kamerapunkte
        self.assertIn("body.streaming #hdr_more{display:inline-flex}", media)
        self.assertIn("body.streaming:not(.hdr-open) header .hdr>*:not(#hdr_sig):not(#hdr_more){display:none!important}", media)
        self.assertNotIn("hdr-open header{flex-wrap", media)                                        # aufgeklappt: die Ansicht wie ohne Pulsanzeige
        self.assertIn("#hdr_more[aria-expanded=true] svg{transform:rotate(180deg)}", PAGE)

    def test_the_existing_chat_elements_move_into_the_header_and_back(self):
        fn = PAGE[PAGE.index("function placeSig(){"):PAGE.index('$("hdr_more").addEventListener')]
        self.assertIn('const want=isPhone()&&document.body.classList.contains("streaming")', fn)    # nur am Handy und nur während der Sendung
        self.assertIn('hold.appendChild(sig); hold.appendChild(dots)', fn)                           # dieselben Elemente: die Messwerte laufen unverändert weiter
        self.assertIn('gear.before(sig); gear.before(dots)', fn)                                    # sonst zurück an ihren Platz im Chat (vor dem Zahnrad)
        self.assertIn('matchMedia("(max-width:620px)").addEventListener("change",placeSig)', PAGE)  # auch beim Ändern der Fensterbreite
        self.assertIn("placeSig(); } }", PAGE)                                                      # beim Start und Ende der Sendung
        self.assertIn('id="net_sig" class="sig"', PAGE)                                             # die Chat-Elemente selbst bleiben unangetastet
        self.assertIn('id="cam_dots" class="camdots"', PAGE)

    def test_scale_numbers_stay_visible_in_the_header_even_on_narrow_phones(self):
        self.assertIn(".sig .sgs{display:none}", PAGE)                                              # im Chat blendet die Regel für schmale Handys die Skala aus
        self.assertIn("body.streaming #hdr_sig .sig .sgs{display:flex}", PAGE)                      # im Kopf bleibt sie immer sichtbar
        self.assertIn("body.streaming #hdr_sig .sig{gap:5px;height:28px}", PAGE)                    # höher wie am Rechner: die Zahlen sind lesbar (8 Pixel)
        self.assertIn("body.streaming #hdr_sig .sig .sgs{height:28px;font-size:8px", PAGE)
        self.assertIn("body.streaming #hdr_sig .sig svg{width:44px}", PAGE)                         # bei den schmalsten Handys wird nur die Linie kürzer

    def test_stream_state_resets_the_toggle(self):
        self.assertIn('document.body.classList.toggle("streaming",on)', PAGE)
        self.assertIn('document.body.classList.remove("hdr-open")', PAGE)                           # mit jedem Start oder Ende ist der Kopf wieder zugeklappt bzw. ganz da


if __name__ == "__main__":
    unittest.main()
