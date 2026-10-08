"""Tests für die Designs (Optionen > Design): Auswahl, gemerkter Wert, Stilregeln je Design in Oberfläche und Anmeldeseite, Lesbarkeit der Farben."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
LOGIN = open(os.path.join(ROOT, "web", "login.html"), encoding="utf-8").read()
DESIGNS = ("klar", "kompakt")


def palette(selector_start):
    """Die Farbvariablen der Regel, deren Auswahl mit selector_start beginnt."""
    m = re.search(re.escape(selector_start) + r"[^{]*\{([^}]*)\}", PAGE)
    assert m, selector_start
    return dict(re.findall(r"--([a-z]+):(#[0-9a-fA-F]{6})", m.group(1)))


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class Designs(unittest.TestCase):
    def test_choice_is_in_options(self):
        self.assertLess(PAGE.index('id="c_layout"'), PAGE.index('id="design_list"'))
        self.assertLess(PAGE.index('id="design_list"'), PAGE.index('id="opt_menus"'))              # "Design" steht vor "Menüpunkte anzeigen"
        self.assertRegex(PAGE, r'<details class="subsec" id="opt_design"><summary>Design</summary>')

    def test_every_design_is_offered_remembered_and_styled(self):
        script = PAGE[PAGE.index("// ---- Designs (Optionen)"):PAGE.index("// Hilfstexte:")]
        for d in DESIGNS:
            self.assertIn('{id:"%s"' % d, script)
            self.assertIn('html[data-design="%s"]' % d, PAGE)                                      # Stilregeln vorhanden
            self.assertIn('html[data-design="%s"][data-theme=' % d, PAGE)                          # hell und dunkel
            self.assertIn(':root[data-design="%s"]' % d, LOGIN)                                    # auch die Anmeldeseite
        self.assertIn('{id:"",name:"Standard"', script)                                            # Standard = bisheriges Aussehen, kein Attribut
        self.assertIn('localStorage.getItem(KEY)', script)
        self.assertNotIn("kontrast", PAGE.lower())                                                 # das Design "Kontrast" gibt es nicht mehr
        self.assertNotIn("kontrast", LOGIN.lower())

    def test_saved_design_is_applied_before_first_paint(self):
        for page in (PAGE, LOGIN):
            head = page[:page.index("</head>")]
            m = re.search(r'<script>try\{var t=localStorage.getItem\("pb_theme"\),d=localStorage.getItem\("pb_design"\).*?</script>', head)
            self.assertIsNotNone(m)
            for d in DESIGNS:
                self.assertIn('d==="%s"' % d, m.group(0))

    def test_default_look_is_untouched(self):
        # Alle Zusatzregeln hängen am Attribut data-design: ohne Auswahl ändert sich nichts am bisherigen Aussehen
        extra = PAGE[PAGE.index("/* ---- Designs (Optionen > Design)"):PAGE.index("</style>\n</head>")]
        for line in extra.splitlines():
            line = line.strip()
            if not line or line.startswith("/*") or line.startswith("@media") or line.startswith("Die Regeln") or line.startswith("html[data-design]{"):
                continue
            if "{" in line:
                self.assertRegex(line, r"^(html\[data-design|:where\(html\[data-design)", line[:80])

    def test_element_rules_do_not_override_states(self):
        # Allgemeine Regeln für button/input/select stehen in :where() (Gewichtung wie die Ursprungsregel), sonst würden sie Zustände übermalen
        # (ausgewählt, Hauptbild, stumm, Fehler: die Knöpfe in der Fußleiste und die Kamera-Auswahl waren nicht mehr zu erkennen)
        extra = PAGE[PAGE.index("/* ---- Designs (Optionen > Design)"):PAGE.index("</style>\n</head>")]
        for d in DESIGNS:
            self.assertNotRegex(extra, r'(^|\n)html\[data-design="%s"\] (button|input|select)\b' % d)
        self.assertIn(':where(html[data-design="klar"]) button{', extra)
        self.assertIn(':where(html[data-design="kompakt"]) button{', extra)

    def test_colours_are_readable_in_every_mode(self):
        starts = {("klar", "light"): 'html[data-design="klar"][data-theme="light"]', ("klar", "dark"): 'html[data-design="klar"][data-theme="dark"]',
                  ("kompakt", "light"): 'html[data-design="kompakt"][data-theme="light"]', ("kompakt", "dark"): 'html[data-design="kompakt"][data-theme="dark"]'}
        for key, start in starts.items():
            p = palette(start)
            for need in ("bg", "card", "text", "muted", "line", "ctl", "accent", "bar", "ok", "warn", "crit", "onbar", "onwarn", "onok", "oncrit"):
                self.assertIn(need, p, (key, need))
            self.assertGreaterEqual(ratio(p["text"], p["card"]), 7, (key, "Text"))
            self.assertGreaterEqual(ratio(p["muted"], p["card"]), 4.5, (key, "gedämpfter Text"))
            self.assertGreaterEqual(ratio(p["ctl"], p["card"]), 3, (key, "Rand von Knöpfen und Feldern auf der Karte"))
            self.assertGreaterEqual(ratio(p["ctl"], p["bg"]), 3, (key, "Rand von Knöpfen und Feldern auf dem Hintergrund"))
            self.assertGreaterEqual(ratio(p["accent"], p["card"]), 4.5, (key, "Akzentfarbe als Schrift"))
            self.assertGreaterEqual(ratio(p["onbar"], p["bar"]), 4.5, (key, "Schrift auf dem Hauptknopf"))
            self.assertGreaterEqual(ratio(p["onwarn"], p["warn"]), 4.5, (key, "Schrift auf Gelb"))
            self.assertGreaterEqual(ratio(p["onok"], p["ok"]), 4.5, (key, "Schrift auf Grün"))
            self.assertGreaterEqual(ratio(p["oncrit"], p["crit"]), 4.5, (key, "Schrift auf Rot"))

    def test_language_picker_keeps_its_own_colors(self):
        self.assertIn(":is(.pbpick,.langbtns) button{background:var(--bg);color:var(--text)", PAGE)
        self.assertIn(":is(.pbpick,.langbtns) button{background:var(--bg);color:var(--text)", LOGIN)

    def test_round_help_buttons_keep_their_shape(self):
        self.assertIn("html[data-design] :is(.ibtn,.helpbtn){min-height:0", PAGE)

    def test_stream_mode_can_be_hidden(self):
        self.assertIn('row(g,"sm_btn","Streammodus",false)', PAGE)
        self.assertIn('id="sm_btn"', PAGE)


    def test_design_and_order_can_be_hidden_but_not_the_list_itself(self):
        self.assertIn('["opt_design","opt_order"].forEach(id=>{ const sub=$(id); if(sub) row(g,id,label(sub),true); })', PAGE)
        self.assertNotIn('"opt_menus"]', PAGE)                                                      # "Menüpunkte anzeigen" bleibt immer sichtbar
        self.assertIn('c.checked=true; c.disabled=true;', PAGE)                                     # "Optionen" selbst: gesperrt

    def test_help_buttons_can_be_hidden_in_one_go(self):
        self.assertIn('row(g,"hlp_btn","Hilfe-Knöpfe (i)",false)', PAGE)
        self.assertIn('document.documentElement.classList.toggle("nohelp",st.hidden.includes("hlp_btn"))', PAGE)
        self.assertIn("html.nohelp :is(.ibtn,.ibrow,.helpbtn,.hlp){display:none!important}", PAGE)


if __name__ == "__main__":
    unittest.main()
