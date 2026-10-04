"""Tests für die mobile Ansicht (Issue #19): Fußleiste mit "Live"/"Stop" sowie Kamera- und Ton-Knöpfen, einzeilige Kopfleiste, keine Rückfrage
beim Start der Sendung. Die Seitenskripte laufen, wenn die JavaScript-Maschine von macOS (jsc) da ist, in einer Attrappe der Seite."""
import json
import os
import re
import shutil
import subprocess
import tempfile
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
        self.assertIn("body{padding-bottom:var(--mfh,calc(76px + env(safe-area-inset-bottom)))}", PAGE)   # --mfh: gemessene Höhe der Fußleiste
        self.assertIn(".toast{bottom:calc(var(--mfh,76px) + 16px)}", PAGE)
        self.assertIn('setProperty("--mfh"', PAGE)


JSC = next((p for p in (shutil.which("jsc"), "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc") if p and os.path.exists(p)), None)


def run_js(code):
    """Code in jsc ausführen, Ausgabe zurückgeben."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code)
    try:
        r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(f.name)
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout.strip()


def foot_source():
    return PAGE[PAGE.index("const FIC=(()=>{"):PAGE.index("async function footAct(b,long){")]


STUBS = """
var els = {};
function $(id) { return els[id] || (els[id] = {id: id, hidden: false, dataset: {}, offsetHeight: 120}); }
function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function setHtml(el, h) { el.html = h; }
var document = {documentElement: {style: {setProperty: function () {}, removeProperty: function () {}}}};
var window = {addEventListener: function () {}};
"""


@unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
class FooterScripts(unittest.TestCase):
    def test_whole_page_script_compiles(self):
        scripts = "\n".join(re.findall(r"<script>(.*?)</script>", PAGE, re.S))
        self.assertIn("function renderFoot", scripts)
        out = run_js("try { new Function(%s); print('ok'); } catch (e) { print('FEHLER ' + e); }" % json.dumps(scripts))
        self.assertEqual(out, "ok")

    def labels(self, names):
        cams = [{"key": "k%d" % i, "name": n} for i, n in enumerate(names)]
        src = foot_source()
        fn = src[src.index("function camLabels(cams){"):src.index("let footBusy")]
        out = run_js(fn + "\nprint(JSON.stringify(camLabels(%s)));" % json.dumps(cams))
        return [json.loads(out)["k%d" % i] for i in range(len(names))]

    def test_label_is_the_first_word_of_the_name(self):
        self.assertEqual(self.labels(["Osmo Action 4", "iPhone hinten", "Webcam"]), ["Osmo", "iPhone", "Webcam"])
        self.assertEqual(self.labels(["  Osmo   Action 4 ", ""]), ["Osmo", "k1"])

    def test_two_cameras_with_the_same_first_word_get_more_words_until_they_differ(self):
        self.assertEqual(self.labels(["Osmo Action 4", "Osmo Action 5", "iPhone"]), ["Osmo Action 4", "Osmo Action 5", "iPhone"])
        self.assertEqual(self.labels(["Osmo Action 4", "Osmo Pocket 3"]), ["Osmo Action", "Osmo Pocket"])
        self.assertEqual(self.labels(["Webcam", "Webcam"]), ["Webcam", "Webcam"])                   # gleiche Namen bleiben gleich, nichts hängt

    def render(self, footer):
        out = run_js(STUBS + foot_source() + "\nrenderFoot(%s); print(JSON.stringify({hidden: $('mf_tools').hidden, html: $('mf_tools').html || ''}));"
                     % json.dumps({"footer": footer}))
        return json.loads(out)

    CAMS = [{"key": "a", "name": "Osmo Action 4", "state": "live", "slot": 0, "main": True, "hidden": False},
            {"key": "b", "name": "iPhone hinten", "state": "live", "slot": 1, "main": False, "hidden": True},
            {"key": "c", "name": "Action 5 Pro", "state": "offline", "slot": 2, "main": False, "hidden": False}]

    def test_buttons_only_for_picture_in_picture(self):
        self.assertTrue(self.render(None)["hidden"])
        self.assertTrue(self.render({"cams": self.CAMS[:1], "audio": {}})["hidden"])                # eine Kamera: nichts zu schalten
        r = self.render({"cams": self.CAMS, "audio": {"key": "a", "name": "Osmo Action 4", "mute": False, "next": "pip"}})
        self.assertFalse(r["hidden"])

    def test_one_button_per_camera_named_by_the_first_word_plus_the_audio_button(self):
        r = self.render({"cams": self.CAMS, "audio": {"key": "a", "name": "Osmo Action 4", "mute": False, "next": "pip"}})["html"]
        labels = re.findall(r'data-kind="cam".*?<span>(.*?)</span>', r)
        self.assertEqual(labels, ["Osmo", "iPhone", "Action"])
        self.assertIn("<span>Ton: Osmo</span>", r)
        self.assertEqual(r.count('data-kind="aud"'), 1)

    def test_states_main_hidden_sending_and_muted_are_marked(self):
        r = self.render({"cams": self.CAMS, "audio": {"key": "b", "name": "iPhone hinten", "mute": True, "next": "pip2"}})["html"]
        btn = re.findall(r'<button type="button" class="([^"]*)" data-kind="cam" data-key="(\w)".*?data-slot="(\d)" data-main="(\d)" data-hidden="(\d)"', r)
        by = {k: (cls, slot, main, hid) for cls, k, slot, main, hid in btn}
        self.assertIn("ismain", by["a"][0])                                                         # blauer Rahmen: das Hauptbild
        self.assertNotIn("hid", by["a"][0].split())
        self.assertIn("hid", by["b"][0].split())                                                    # blaues Symbol: ausgeblendet
        self.assertEqual(by["b"][1:], ("1", "0", "1"))
        self.assertIn("live", by["b"][0].split())                                                   # grün: sendet
        self.assertNotIn("live", by["c"][0].split())                                                # weiß: sendet nicht
        self.assertIn("mf-aud muted", r)                                                            # rot durchgestrichenes Mikro
        self.assertIn('data-mute="1"', r)
        self.assertIn('data-next="pip2"', r)
        self.assertEqual(r.count("M4 4l16 16"), 2)                                                  # Strich: ausgeblendetes Bild und stummes Mikro
        self.assertEqual(r.count('x="12" y="11"'), 2)                                               # kleine Bilder mit Bild-im-Bild-Symbol (die Hauptkamera hat das Kamera-Symbol)
        self.assertEqual(r.count("<circle"), 1)

    def test_names_cannot_inject_markup(self):
        cams = [dict(self.CAMS[0], name='<img src=x onerror=alert(1)> "x"'), self.CAMS[1]]
        r = self.render({"cams": cams, "audio": {"key": "a", "name": cams[0]["name"], "mute": False, "next": "pip"}})["html"]
        self.assertNotIn("<img", r)


class FooterBehaviour(unittest.TestCase):
    def test_long_press_and_short_press_are_told_apart(self):
        i = PAGE.index("(function(){                                                              // kurzer und langer Druck")
        block = PAGE[i:PAGE.index("})();", i)]
        self.assertIn("setTimeout(()=>{ timer=null; fired=true;", block)
        self.assertIn(",550)", block)                                                               # etwas mehr als eine halbe Sekunde
        self.assertIn("footAct(b,true)", block)
        self.assertIn("footAct(b,false)", block)
        self.assertIn("if(fired){ fired=false; e.preventDefault(); return; }", block)                # dem langen Druck folgt kein kurzer
        self.assertIn('"contextmenu"', block)                                                       # kein Kontextmenü beim langen Druck
        self.assertIn("touch-action:manipulation", PAGE)                                            # kein Zoomen durch Doppeltippen

    def test_requests_of_the_buttons(self):
        i = PAGE.index("async function footAct(b,long){")
        body = PAGE[i:PAGE.index("(function(){", i)]
        self.assertIn('"/api/pipeline/swap",{with:b.dataset.key}', body)                            # lang auf eine Kamera: Hauptbild tauschen
        self.assertIn('"/api/pipeline/view",{visible:{[b.dataset.slot]:b.dataset.hidden==="1"}}', body)   # kurz: aus-/einblenden
        self.assertIn('"/api/pipeline/view",{mute:b.dataset.mute!=="1"}', body)                    # lang auf Ton: stumm und wieder laut
        self.assertIn('"/api/pipeline/view",{audio:b.dataset.next}', body)                          # kurz auf Ton: nächste Tonspur
        self.assertIn("Das Hauptbild lässt sich nicht ausblenden", body)
        self.assertIn("Stumm schalten geht nur während der Sendung.", body)

    def test_question_only_when_a_change_would_interrupt_the_picture(self):
        i = PAGE.index("async function footAct(b,long){")
        body = PAGE[i:PAGE.index("(function(){", i)]
        self.assertIn("d.active&&!seamlessTo(mainKey,b.dataset.key)&&!confirm(", body)             # Tausch ohne Unterbrechung geht ohne Frage
        self.assertIn("d.active&&!d.view_live&&!confirm(", body)                                    # ausblenden live: ohne Frage
        self.assertIn("d.active&&!(d.view_live&&d.audio_live)&&!confirm(", body)                    # Ton live: ohne Frage
        self.assertEqual(body.count("confirm("), 3)                                                 # sonst nie

    def test_footer_follows_the_poll_and_does_not_replace_buttons_while_pressed(self):
        self.assertIn("updHdrLive(d); renderFoot(d);", PAGE)
        i = PAGE.index("function renderFoot(d){")
        self.assertIn("if(footBusy) return;", PAGE[i:i + 200])

    def test_colours(self):
        for rule in (".mf-cam.live{color:var(--ok)}", ".mf-cam.hid{color:var(--blue)}", ".mf-cam.ismain{border-color:var(--blue)}",
                     ".mf-aud.muted{color:var(--crit);border-color:var(--crit)}"):
            self.assertIn(rule, PAGE)
        self.assertIn("--blue:#3b82f6", PAGE)

    def test_markup(self):
        self.assertRegex(PAGE, r'<nav id="mfoot".*?id="mf_msg".*?id="mf_live".*?id="mf_tools".*?</nav>')
        i = PAGE.index("#mfoot{display:flex;position:fixed")
        self.assertNotIn("flex-direction:column", PAGE[i:PAGE.index("}", i)])                         # eine Reihe wie in der BELABOX-Oberfläche: "Live" links, Knöpfe rechts
        self.assertLess(PAGE.index('id="mf_live"'), PAGE.index('id="mf_tools"'))


if __name__ == "__main__":
    unittest.main()
