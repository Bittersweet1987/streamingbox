"""Tests für die mobile Ansicht (Issue #19): Fußleiste mit "Live"/"Stop", einzeilige Kopfleiste, keine Rückfrage beim Start der Sendung."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()


class MobileFooter(unittest.TestCase):
    def test_footer_exists_and_is_only_shown_on_phones(self):
        self.assertIn('<nav id="mfoot"', PAGE)
        self.assertIn('id="mf_live"', PAGE)
        self.assertRegex(PAGE, r"#mfoot\{display:none\}")                               # Standard: unsichtbar (Desktop)
        phone = re.search(r"@media\(max-width:620px\)\{\s*header\{flex-wrap:nowrap.*?\n\}", PAGE, re.S)
        self.assertIsNotNone(phone)
        css = phone.group(0)
        self.assertIn("#mfoot{display:flex;position:fixed", css)                          # auf dem Handy fest am unteren Rand
        self.assertIn("#hdr_live{display:none!important}", css)                           # "Live" wandert aus der Kopfleiste in die Fußleiste
        self.assertIn("flex-wrap:nowrap", css)                                            # Kopfleiste in einer Zeile
        self.assertIn("safe-area-inset-bottom", css)                                      # nicht unter der Navigationsleiste des Handys

    def test_footer_follows_the_state_of_the_sending(self):
        i = PAGE.index("function updHdrLive(d){")
        body = PAGE[i:PAGE.index("\n}\n", i)]
        self.assertIn('f.textContent=busy?"…":(live?"Stop":"Live")', body)                # Beschriftung "Live" und "Stop"
        self.assertIn("f.disabled=b.disabled", body)                                      # gleiche Bedingungen wie die Kopfleiste
        self.assertIn('f.className="mf-live"+(live&&!busy?" on":"")+(busy?" busy":"")', body)

    def test_both_buttons_use_the_same_toggle(self):
        self.assertIn('$("hdr_live").addEventListener("click",liveToggle)', PAGE)
        self.assertIn('$("mf_live").addEventListener("click",liveToggle)', PAGE)

    def test_no_question_when_going_live_or_when_stopping(self):
        start = PAGE[PAGE.index("async function doLiveStart(){"):PAGE.index('$("live_go").addEventListener')]
        self.assertNotIn("confirm(", start)
        self.assertNotIn("Jetzt LIVE senden?", PAGE)
        toggle = PAGE[PAGE.index("async function liveToggle(){"):PAGE.index('$("hdr_live").addEventListener')]
        self.assertNotIn("confirm(", toggle)                                              # auch beim Beenden keine Rückfrage (Antwort des Melders: Ja)
        self.assertNotIn("Die Sendung jetzt beenden?", PAGE)
        self.assertIn("doLiveStop()", toggle)
        self.assertNotIn("(mit Rückfrage)", PAGE)                                         # Tooltips stimmen wieder

    def test_page_leaves_room_for_the_footer_and_the_toast(self):
        self.assertIn("body{padding-bottom:calc(76px + env(safe-area-inset-bottom))}", PAGE)
        self.assertIn(".toast{bottom:92px}", PAGE)


if __name__ == "__main__":
    unittest.main()
