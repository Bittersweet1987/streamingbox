"""Tests für die Karte "Einstellungen sichern" in der Oberfläche (Issue #20): Aufbau der Seite und die Seitenskripte (in JavaScriptCore, wenn vorhanden)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
JSC = next((p for p in (shutil.which("jsc"), "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc") if p and os.path.exists(p)), None)


def func(name):
    """Quelltext einer Funktion der Seite ("function name" oder "async function name") bis zu ihrer schließenden Klammer in der ersten Spalte."""
    m = re.search(r"^(async )?function %s\(.*?^\}" % re.escape(name), PAGE, re.S | re.M)
    assert m, name
    return m.group(0)


class Markup(unittest.TestCase):
    def test_card_and_controls_exist_before_the_developer_card(self):
        for i in ("c_bk", "bk_secrets", "bk_pw", "bk_pw2", "bk_export", "bk_msg", "bk_file", "bk_ipw", "bk_open", "bk_plan", "bk_imsg", "bk_restore", "bk_reload"):
            self.assertIn('id="%s"' % i, PAGE, i)
        self.assertLess(PAGE.index('id="c_bk"'), PAGE.index('id="c_dev"'))
        self.assertRegex(PAGE, r'<input type="file" id="bk_file" accept="[^"]*json')

    def test_passwords_are_masked_and_not_autofilled(self):
        for i in ("bk_pw", "bk_pw2"):
            self.assertRegex(PAGE, r'<input type="password" id="%s" autocomplete="new-password"' % i)
        self.assertIn('<input type="password" id="bk_ipw" autocomplete="off"', PAGE)

    def test_secrets_are_on_by_default_and_the_text_is_short(self):
        self.assertRegex(PAGE, r'<input type="checkbox" id="bk_secrets" checked>')
        i = PAGE.index('id="c_bk"')
        card = PAGE[i:PAGE.index("</details>", i)]
        for needle in ("immer mit einem Passwort verschlüsselt", "Nur ohne Sendung"):
            self.assertIn(needle, card)
        self.assertLess(len(re.sub(r"<[^>]+>", "", card)), 1200)                           # kein langer Einleitungstext
        self.assertTrue(card.lstrip().startswith("<div class=\"sech\">Sichern</div>") or '<div class="sech">Sichern</div>' in card[:300])

    def test_import_asks_before_it_replaces_and_uses_the_chosen_parts(self):
        i = PAGE.index('if(e.target.id==="bk_go")')
        block = PAGE[i:PAGE.index("});", i)]
        self.assertIn("confirm(", block)
        self.assertIn('/api/settings/import', block)
        self.assertIn("password:bkPw||undefined", block)
        self.assertIn("sections:secs", block)
        self.assertIn("input[data-sec]:checked", block)

    def test_the_file_password_is_kept_in_memory_only_and_cleared(self):
        self.assertIn('let bkDoc=null, bkInfo=null, bkSending=false, bkPw="";', PAGE)
        self.assertIn('bkDoc=null; bkInfo=null; bkPw="";', PAGE)
        self.assertNotIn("localStorage", func("bkExport"))


@unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
class Scripts(unittest.TestCase):
    STUBS = """
var els = {};
function mk(id) { return {id: id, value: "", textContent: "", hidden: false, disabled: false, checked: true, dataset: {}, innerText: ""}; }
function $(id) { return els[id] || (els[id] = mk(id)); }
function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function setHtml(el, h) { el.html = h; }
var calls = [];
var reply = {document: {format: "x"}, encrypted: true, notes: []};
function srtlaCall(method, url, body) { calls.push([method, url, body]); return Promise.resolve(reply); }
var pad2 = n => String(n).padStart(2, "0");
var downloads = [];
var document = {createElement: () => ({click() { downloads.push(this.download); }, remove() {}}), body: {appendChild() {}}};
var URL = {createObjectURL: () => "blob:x", revokeObjectURL() {}};
function Blob(parts, o) { this.size = parts.join("").length; }
function setTimeout(f) {}
var BK_MIN = 8;
"""

    def run_case(self, setup, calls_expected=None):
        src = self.STUBS + func("bkExport") + "\nbkExport.toString();\n" + setup + "\nbkExport().then(function () { print(JSON.stringify({calls: calls, msg: $('bk_msg').textContent, pw: [$('bk_pw').value, $('bk_pw2').value], downloads: downloads, btn: $('bk_export').disabled})); });\n"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
        try:
            r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(f.name)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_export_needs_a_password_when_passwords_are_included(self):
        o = self.run_case("$('bk_secrets').checked = true;")
        self.assertEqual(o["calls"], [])
        self.assertIn("Passwort", o["msg"])

    def test_the_two_passwords_must_match_and_be_long_enough(self):
        o = self.run_case("$('bk_secrets').checked = true; $('bk_pw').value = 'Geheim-Test-2026'; $('bk_pw2').value = 'anders';")
        self.assertEqual((o["calls"], o["msg"]), ([], "Die beiden Passwörter sind nicht gleich."))
        o = self.run_case("$('bk_secrets').checked = false; $('bk_pw').value = 'kurz'; $('bk_pw2').value = 'kurz';")
        self.assertEqual(o["calls"], [])
        self.assertIn("Mindestens 8", o["msg"])

    def test_encrypted_export_sends_secrets_and_password_and_clears_the_fields(self):
        o = self.run_case("$('bk_secrets').checked = true; $('bk_pw').value = 'Geheim-Test-2026'; $('bk_pw2').value = 'Geheim-Test-2026';")
        self.assertEqual(o["calls"], [["POST", "/api/settings/export", {"secrets": True, "password": "Geheim-Test-2026"}]])
        self.assertEqual(o["pw"], ["", ""])
        self.assertRegex(o["downloads"][0], r"^irl4you-einstellungen-\d{8}-\d{4}\.json$")
        self.assertIn("Verschlüsselt", o["msg"])
        self.assertIn("nirgends gespeichert", o["msg"])
        self.assertFalse(o["btn"])

    def test_plain_export_without_passwords_is_possible(self):
        o = self.run_case("$('bk_secrets').checked = false; reply = {document: {format: 'x'}, encrypted: false, notes: ['Hinweis A']};")
        self.assertEqual(o["calls"], [["POST", "/api/settings/export", {"secrets": False}]])
        self.assertIn("Unverschlüsselt, ohne Passwörter", o["msg"])
        self.assertIn("Hinweis A", o["msg"])

    def test_server_error_is_shown_and_the_button_comes_back(self):
        o = self.run_case("$('bk_secrets').checked = true; $('bk_pw').value = 'Geheim-Test-2026'; $('bk_pw2').value = 'Geheim-Test-2026'; "
                          "srtlaCall = function () { return Promise.reject(new Error('Es läuft schon eine WLAN-Aktion')); };")
        self.assertIn("Es läuft schon eine WLAN-Aktion", o["msg"])
        self.assertFalse(o["btn"])

    def test_results_are_escaped(self):
        src = self.STUBS + func("bkResults") + "\nvar loaded = []; var srtlaLoad = function () { loaded.push('s'); };\n" \
              "bkResults({results: [{ok: true, label: 'Kameras', message: '4 eingespielt'}, {ok: false, label: '<b>x</b>', message: '<img src=x onerror=alert(1)>'}]});\n" \
              "print(JSON.stringify({html: $('bk_imsg').html, reload: $('bk_reload').hidden, loaded: loaded}));\n"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
        try:
            r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(f.name)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertNotIn("<img", o["html"])
        self.assertNotIn("<b>x", o["html"])
        self.assertIn("&lt;img", o["html"])
        self.assertIn("bkok", o["html"])
        self.assertIn("bkbad", o["html"])
        self.assertFalse(o["reload"])
        self.assertEqual(o["loaded"], ["s"])

    def test_whole_page_script_compiles(self):
        scripts = "\n".join(re.findall(r"<script>(.*?)</script>", PAGE, re.S))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write("try { new Function(%s); print('ok'); } catch (e) { print('FEHLER ' + e); }" % json.dumps(scripts))
        try:
            r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(f.name)
        self.assertEqual(r.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
