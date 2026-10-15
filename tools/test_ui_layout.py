"""Tests für die Menüeinstellung (Optionen): Ansichten Desktop und Mobil mit eigener Reihenfolge, eigenem Ausblenden und eigenem Design, auf der Box gespeichert."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SRC = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
_A = PAGE.index("// ---- Ansichten: Desktop und Mobil")
BLOCK = PAGE[_A:PAGE.index("\n})();", _A) + 6]                                           # nur dieser Block


class Views(unittest.TestCase):
    """Ansichten Desktop und Mobil: je Ansicht eigene Reihenfolge, Ausblenden und Design, auf der Box gespeichert (gilt für alle Geräte), im Browser nur zwischengemerkt."""

    def test_two_views_with_the_same_shape(self):
        self.assertIn("const views={desktop:blank(),mobile:blank()}", BLOCK)
        self.assertIn('const blank=()=>({order:[],hidden:[],design:""});', BLOCK)
        self.assertIn('(phoneQ&&phoneQ.matches?"mobile":"desktop")', BLOCK)
        self.assertIn('matchMedia("(max-width:620px)")', BLOCK)                              # dieselbe Grenze wie bei isPhone()
        self.assertIn('const isPhone=()=>window.matchMedia("(max-width:620px)").matches;', PAGE)

    def test_saved_on_the_box_and_cached_in_the_browser(self):
        self.assertIn('srtlaCall("POST","/api/layout",{profile:q,order:views[q].order,hidden:views[q].hidden,design:views[q].design})', BLOCK)
        self.assertIn('srtlaCall("GET","/api/layout")', BLOCK)
        self.assertIn('localStorage.setItem(VKEY,JSON.stringify(views))', BLOCK)

    def test_first_start_takes_over_the_old_browser_setting_once(self):
        self.assertIn('localStorage.getItem(KEY)', BLOCK)                                    # bisheriger Stand (pb_layout) und
        self.assertIn('localStorage.getItem("pb_design")', BLOCK)                            # Design als Grundlage beider Ansichten
        self.assertIn('{init:{desktop:views.desktop,mobile:views.mobile}}', BLOCK)           # nur wenn auf der Box noch nichts liegt

    def test_applied_at_start_from_the_cache(self):
        self.assertIn("applyOrder(); applyHidden(); applyDesign(); buildList(); buildCtl();\n  load();\n})();", BLOCK)

    def test_design_cache_for_the_start_page_follows_the_view_in_use(self):
        self.assertIn('localStorage.setItem("pb_design",id)', BLOCK)                          # Kopfzeile der Seite und Anmeldeseite lesen weiter pb_design

    def test_menu_names_do_not_carry_help_buttons(self):
        self.assertIn('c.querySelectorAll("button,.badge,.ibtn").forEach(x=>x.remove())', BLOCK)

    def test_controls_exist(self):
        for i in ('id="view_ctl"', 'id="view_mode"', 'id="vw_desktop"', 'id="vw_mobile"', 'id="vw_copy"', 'id="vw_msg"'):
            self.assertIn(i, PAGE, i)
        self.assertIn('<option value="auto">', PAGE)

    def test_server_stores_the_two_views(self):
        for needle in ("class LayoutStore", '"/api/layout"', "Handler.layout = LayoutStore(args.state)", '"layout.json"'):
            self.assertIn(needle, SRC, needle)
        for old in ("class UiLayout", "ui-layout.json"):
            self.assertNotIn(old, SRC, old)

    def test_backup_does_not_carry_the_menu_setting(self):
        self.assertNotIn('("layout", "Optionen', SRC)
        self.assertNotIn('doc["layout"]', SRC)


class PipelineSwitchOptions(unittest.TestCase):
    def test_the_three_switching_options_are_collapsible_and_closed_by_default(self):
        """Unter "Bildaufbau" stehen das automatische Umschalten, der Tausch ohne Unterbrechung und "Alle Kameras immer bereit" in einem zuklappbaren Bereich, standardmäßig zu."""
        i = PAGE.index('<details class="subsec" id="pipe_switch">')
        head = PAGE[i:i + 80]
        self.assertNotIn(" open", head)
        block = PAGE[i:PAGE.index("</details>", i)]
        for need in ('id="p_auto"', 'id="p_swap"', 'id="p_always"'):
            self.assertIn(need, block, need)
        self.assertIn("<summary>Umschalten und Tausch der Kameras</summary>", block)
        save = PAGE.index('id="p_save"')
        self.assertLess(i, save)                                                             # der Speichern-Knopf bleibt außerhalb, immer sichtbar
        self.assertLess(PAGE.index("</details>", i), save)


class FacecamAndPreviewSwitch(unittest.TestCase):
    def test_facecam_area_is_under_the_picture_setup_with_the_four_choices(self):
        i = PAGE.index('<details class="subsec piponly" id="pipe_facecam">')
        block = PAGE[i:PAGE.index("</details>", i)]
        self.assertIn("<summary>Facecam</summary>", block)
        self.assertIn('id="fc_btns"', block)
        self.assertLess(PAGE.index('id="pipecard"'), i)
        self.assertLess(i, PAGE.index('id="p_save"'))
        for need in ('"Kleines Bild 1"', '"Kleines Bild 2"', '"Kleines Bild 3"', '"Keine Facecam"', "nicht belegt"):
            self.assertIn(need, PAGE, need)

    def test_facecam_choice_shows_the_camera_of_each_small_picture_and_applies_at_once(self):
        self.assertIn('c[s]?nm(c[s]):"nicht belegt"', PAGE)                                   # je Knopf die gespeicherte Kamera
        self.assertIn('srtlaCall("POST","/api/pipeline/facecam",{slot:b.dataset.slot})', PAGE)
        self.assertIn('if path == "/api/pipeline/facecam":', SRC)
        self.assertIn("self.send.change_view(audio=aud)", SRC)                                # Ton live umstellen wie über die Fußleiste

    def test_the_gear_of_the_preview_brings_back_both_hidden_switches(self):
        """"Vorschau auch von außen erlauben" verschwindet mit Haken wie der Haken zur Rechenleistung; das Zahnrad holt beide zurück."""
        self.assertIn('<div id="pa_box">', PAGE)
        self.assertIn('$("pa_box").hidden=ext&&!gearOpen', PAGE)
        self.assertIn("gear.hidden=!(keep.checked||ext)", PAGE)
        self.assertIn("window.pbPrevSync=close=>", PAGE)
        self.assertIn("if(window.pbPrevSync) pbPrevSync(false);", PAGE)                      # nach dem Laden vom Stand der Box
        self.assertIn("pbPrevSync(true)", PAGE)                                              # nach dem Ändern: einklappen


class CameraOrderEverywhere(unittest.TestCase):
    def test_header_dots_overview_and_list_share_one_order(self):
        self.assertIn("function camSorted(list){", PAGE)
        self.assertIn("const l=camSorted(list||[]).filter(c=>!c.parked).slice(0,4);", PAGE)       # Punkte in der Kopfzeile
        self.assertIn("list=camSorted(list).filter(c=>!c.parked);", PAGE)                          # Übersicht "Kameras"
        self.assertIn("  list=camSorted(list);", PAGE)                                              # Kameraliste
        self.assertEqual(PAGE.count("const quiet=c=>"), 1)                                         # die Regel steht nur einmal da


class CompactChat(unittest.TestCase):
    def test_idle_line_and_gear_go_away_while_the_chat_runs(self):
        self.assertIn("html.nohead #chat_msg.quiet{display:none}", PAGE)
        self.assertIn("html.nohead #c_chat.chatok>summary{visibility:hidden;", PAGE)
        self.assertIn("html.nohead #c_chat.chatok.showctl>summary", PAGE)
        self.assertIn("#chat_opt[aria-expanded=true])>summary{visibility:visible", PAGE)       # solange die Chat-Einstellungen offen sind
        self.assertIn('msg.classList.toggle("quiet",d.state==="ok"&&!shown)', PAGE)
        self.assertIn('card.classList.toggle("chatok",d.state==="ok")', PAGE)

    def test_errors_stay_visible(self):
        self.assertIn('msg.classList.remove("quiet"); card.classList.remove("chatok")', PAGE)    # keine Verbindung zur Box: Zeile und ⚙ zurück
        self.assertNotIn("#chat_msg{display:none", PAGE)

    def test_gear_comes_back_on_mouse_move_or_tap(self):
        i = PAGE.index("// ---- Chat in der kompakten Ansicht")
        blk = PAGE[i:i + 600]
        self.assertIn('c.addEventListener("pointermove"', blk)
        self.assertIn('c.addEventListener("click",show)', blk)
        self.assertIn("10000", blk)                                                            # zehn Sekunden sichtbar (7 bis 10 s wünschte der Nutzer: 3 s waren zu wenig)


class PhoneChat(unittest.TestCase):
    def test_phone_rules_are_tight(self):
        i = PAGE.index("@media(max-width:620px){\n  .chatbox{")
        blk = PAGE[i:PAGE.index("\n}", i)]
        self.assertIn("font-size:15px;line-height:1.15", blk)
        self.assertIn(".chatbox .cm{padding:0 2px;", blk)                                   # 0 px statt 9 px zwischen den Nachrichten
        self.assertIn("img.em{height:1.3em;", blk)

    def test_desktop_rules(self):
        """Am Rechner: seit 0.9.219 schmalere Zeilen (Zeilenabstand 1,3 statt 1,45, Innenabstand 3 px oben und 2 px unten statt 7 px)."""
        self.assertIn("line-height:1.3;display:flex;flex-direction:column", PAGE)
        self.assertIn(".chatbox .cm{display:grid;grid-template-columns:auto 1fr auto;column-gap:10px;padding:3px 2px 2px;", PAGE)


class FootHides(unittest.TestCase):
    BLK = PAGE[PAGE.index("// Fußleiste am Handy, Einstellung je Gerät"):PAGE.index("function sizeFoot(){")]

    def test_footer_slides_away_and_back(self):
        self.assertIn("html.mfhide #mfoot{transform:translateY(", PAGE)
        self.assertIn("visibility:hidden", PAGE[PAGE.index("html.mfhide #mfoot"):PAGE.index("html.mfhide #mfoot") + 300])
        self.assertIn("if(ny<24||bottom||run<-8) scrollHid=false; else if(run>8) scrollHid=true;", self.BLK)   # oben, unten, deutlich hoch: da; deutlich runter: weg
        self.assertIn('getComputedStyle(f).display==="none"', self.BLK)                              # nur wo die Leiste steht
        self.assertIn('f.addEventListener("focusin"', self.BLK)
        self.assertIn('window.addEventListener("scroll"', self.BLK)                                  # Fensterscrollen, nicht das im Chatfeld

    def test_only_hides_while_chat_or_preview_is_shown(self):
        self.assertIn('["c_chat","c_prev"].some(id=>{ const e=$(id); return !!e&&e.open&&e.offsetParent!==null; })', self.BLK)
        self.assertIn('h=mode==="off"||(mode!=="always"&&wide&&(mode==="hide"||scrollHid))', self.BLK)   # automatisch und "hide" nur mit Chat oder Vorschau

    def test_three_modes_kept_per_device_only(self):
        self.assertIn('KEY="pb_foot"', self.BLK)
        self.assertIn('localStorage.setItem(KEY,mode)', self.BLK)
        self.assertIn('v==="always"||v==="hide"||v==="off"', self.BLK)                               # Standard: automatisch
        self.assertNotIn("srtlaCall", self.BLK)                                                      # nichts geht an die Box
        self.assertIn('[["auto","Automatisch"],["always","Immer anzeigen"],["hide","Ausblenden bei Chat oder Vorschau"],["off","Immer ausblenden"]]', PAGE)
        self.assertIn('window.pbFootMode(sel.value)', PAGE)

    def test_always_hide_works_without_chat_and_preview(self):
        """Issue #56: Wer Chat und Vorschau nicht nutzt, wählte "Ausblenden" und die Leiste blieb."""
        self.assertIn('h=mode==="off"||(mode!=="always"&&wide&&(mode==="hide"||scrollHid))', self.BLK)
        self.assertIn('root.classList.toggle("mfperm",mode==="off"||(mode==="hide"&&wide))', self.BLK)

    def test_no_space_kept_when_footer_is_gone_for_good(self):
        self.assertIn("html.mfperm body{padding-bottom:calc(16px + env(safe-area-inset-bottom))}", PAGE)


class HeaderLogo(unittest.TestCase):
    """Issue #61: Das IRL4YOU-Logo im Kopf, im Wechsel mit dem Text; während der Sendung nur das Logo."""

    def test_logo_and_text_are_both_in_the_header_and_readable_by_screen_readers(self):
        self.assertIn('<h1 class="hl" aria-label="IRL4YOU BOX"><span class="hlogo"><svg viewBox="0 0 1024 1024"', PAGE)
        self.assertIn('aria-hidden="true" focusable="false"', PAGE[PAGE.index('class="hlogo"'):PAGE.index('class="hlogo"') + 200])
        self.assertIn('<span class="htxt">IRL4YOU BOX</span></h1>', PAGE)

    def test_logo_is_a_small_inline_vector_with_the_brand_colours(self):
        a = PAGE.index('<span class="hlogo">')
        svg = PAGE[a:PAGE.index("</svg>", a) + 6]
        self.assertLess(len(svg), 1200)                                                        # klein, nichts wird nachgeladen
        for colour in ("#12213c", "#1cdaf5", "#f8fafc", "#ffa14f"):                             # Marineblau, Cyan, Weiß, Orange
            self.assertIn(colour, svg)
        self.assertNotIn("<image", svg)
        self.assertNotIn("href=", svg.replace('url(#hl4m)', ""))

    def test_alternates_every_30_seconds_with_a_soft_fade(self):
        self.assertIn(".hl .hlogo{animation:hlA 60s linear infinite}.hl .htxt{animation:hlB 60s linear infinite}", PAGE)
        self.assertIn("@keyframes hlA{0%,46%{opacity:1}50%,96%{opacity:0}100%{opacity:1}}", PAGE)
        self.assertIn("@keyframes hlB{0%,46%{opacity:0}50%,96%{opacity:1}100%{opacity:0}}", PAGE)

    def test_only_the_logo_while_sending(self):
        self.assertIn("body.streaming .hl .htxt{display:none}body.streaming .hl .hlogo{animation:none;opacity:1}", PAGE)

    def test_no_animation_when_the_device_reduces_motion(self):
        i = PAGE.index("@media(prefers-reduced-motion:reduce){.hl .hlogo,.hl .htxt{animation:none;opacity:1}")
        self.assertIn("h1.hl{display:inline-flex;gap:8px}", PAGE[i:i + 250])                  # dann Logo und Text nebeneinander


class DefaultOrder(unittest.TestCase):
    def test_options_sit_near_the_bottom_and_power_is_last(self):
        """Wunsch des Nutzers: Standardreihenfolge unten: Optionen (mit Problem melden darin), Box ausschalten und abmelden (jeder kann sie ändern)."""
        import re
        ids = [m.group(1) for m in re.finditer(r'<details class="card[^>]*?\bid="([A-Za-z_0-9]+)"', PAGE[PAGE.index("<main"):])]
        self.assertEqual(ids[-2:], ["c_layout", "c_power"])                                      # "Problem melden" steht jetzt unter Optionen (Wunsch des Nutzers, 0.9.200)
        self.assertNotIn("c_report", ids)
        self.assertLess(ids.index("c_dev"), ids.index("c_layout"))                              # Entwickler steht über den Optionen
        self.assertEqual(ids[:3], ["c_prev", "c_chat", "c_status"])                              # oben unverändert


class ImportProgress(unittest.TestCase):
    def test_import_and_restore_show_a_progress_bar_like_the_update(self):
        """Wunsch des Nutzers: beim Einspielen eine Fortschrittsanzeige wie beim Software-Update (damit niemand denkt, es passiere nichts)."""
        self.assertIn('<div id="bk_prog" class="prog" hidden>', PAGE)
        self.assertIn('async function bkWithProgress(fn){', PAGE)
        self.assertIn('srtlaCall("GET","/api/settings/progress")', PAGE)
        self.assertIn('await bkWithProgress(()=>srtlaCall("POST","/api/settings/import"', PAGE)
        self.assertIn('await bkWithProgress(()=>srtlaCall("POST","/api/settings/restore"', PAGE)
        self.assertIn('/api/settings/progress', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())

    def test_todo_note_after_import_lists_what_the_backup_does_not_contain(self):
        """Nach dem Einspielen steht, was noch zu tun ist (Wunsch des Nutzers): DJI koppeln, Twitch, Tailscale, Netzwerkkarten prüfen; Haken merkt der Browser."""
        self.assertIn('<div id="bk_todo" class="bktodo" hidden>', PAGE)
        for k in ("dji", "twitch", "tailscale", "net"):
            self.assertIn('data-todo="%s"' % k, PAGE)
        self.assertIn("function bkTodoShow(){", PAGE)
        self.assertIn("function bkResults(r){\n  bkTodoShow();", PAGE)                      # erscheint, sobald Ergebnisse da sind
        self.assertIn('bkTodoSave(all?null:st)', PAGE)                                      # alle gesetzt: weg

    def test_backup_card_lists_what_is_and_is_not_saved(self):
        """Vor dem Herunterladen steht, was die Sicherung enthält und was nicht (Wunsch des Nutzers); die Liste deckt alle Teile des Dokuments ab."""
        i = PAGE.index('<details class="subsec" id="bk_what">')
        self.assertLess(i, PAGE.index('id="bk_export"'))                                     # über dem Knopf zum Herunterladen
        block = PAGE[i:PAGE.index("</details>", i)]
        yes, no = block.split('class="bkno"')
        for t in ("Kameras", "SRTLA-Server", "Sendewege, Kameranetz und Hotspots", "Gespeicherte WLAN-Netze", "DJI-Kameras", "HDMI-Eingang",
                  "Automatischer Start", "Eigene Namen", "Twitch: Kanal"):
            self.assertIn(t, yes)
        for t in ("Bluetooth-Kopplung", "Twitch-Anmeldung und Token", "Tailscale", "Feste Zusatzadresse", "Protokolle und Mitschnitte"):
            self.assertIn(t, no)
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        doc = src[src.index("def make_document"):src.index("def export(self, secrets_on")]
        self.assertNotIn("ExtraAddress", doc)                                               # die feste Adresse steckt tatsächlich nicht in der Sicherung
        self.assertNotIn('"token"', doc)

    def test_chat_command_answer_stays_visible_under_the_input(self):
        """Ein Befehl im Chat-Feld zeigt die Antwort (zum Beispiel "VIP vergeben: Name") einige Sekunden an; Fehler von Twitch stehen wie bisher an derselben Stelle."""
        self.assertIn('if(t[0]==="/"&&r&&r.message){', PAGE)
        self.assertIn('err.textContent=r.message; err.classList.add("okmsg");', PAGE)
        self.assertIn('.err.okmsg{color:var(--ok)}', PAGE)
        i = PAGE.index('$("chat_form").addEventListener("submit"')
        self.assertIn("catch(x){ err.textContent=x.message; }", PAGE[i:i + 1200])

    def test_help_button_sits_next_to_the_heading_and_only_while_open(self):
        """Das "i" steht in der Kopfzeile hinter der Überschrift (keine eigene Zeile darunter) und nur bei aufgeklapptem Bereich (Wunsch des Nutzers)."""
        self.assertIn('(head.querySelector(":scope>.sumh")||head).appendChild(b);', PAGE)
        self.assertNotIn('r.className="ibrow"', PAGE)
        self.assertIn("details:not([open])>summary .ibtn{display:none}", PAGE)

    def test_twitch_roles_per_browser_in_the_page(self):
        """Ohne eigene Anmeldung: nur lesen; Moderator meldet sich selbst an; der Streamer weist sich im neuen Browser aus."""
        self.assertIn('id="tw_acc_owner"', PAGE)
        self.assertIn('twAcc&&twAcc.role==="none"?{action:"start",as:"user"}:{action:"start",all:true}', PAGE)      # Zuschauer: nur Chat
        self.assertIn('id="tw_acc_asmod"', PAGE)
        self.assertIn('{action:"start",as:"mod"}', PAGE)                                                             # Moderator: Moderatorenrechte
        self.assertIn('a.role==="user"?`Angemeldet als ${a.login} · Zuschauer`', PAGE)
        self.assertIn('a.role==="none"&&!!a.streamer', PAGE)
        self.assertIn('hdr["x-pb-owner"]=pbOwnerKey()', PAGE)
        self.assertIn('"x-pb-owner":pbOwnerKey()', PAGE)                                    # auch der Chat-Abruf nennt den Schlüssel

    def test_channel_field_lives_in_the_chat_settings_and_shows_on_top_only_while_missing(self):
        """Das Feld "Kanal (Konto, auf dem gestreamt wird)" steht in den Chat-Einstellungen (Zahnrad); oben im Chat nur, solange der Kanal fehlt (Wunsch des Nutzers). Nie vorbelegt."""
        self.assertIn('<div class="twacc twch" id="tw_chrow" hidden>', PAGE)                  # oben: zunächst versteckt
        self.assertIn('$("tw_chrow").hidden=!a.need_channel;', PAGE)
        i = PAGE.index('id="tw_chrow2"')
        row = PAGE[i:PAGE.index("</div>", i)]
        self.assertIn("Kanal (Konto, auf dem gestreamt wird)", row)
        self.assertIn('id="tw_ch2_in"', row)
        self.assertNotIn(" value=", row)                                                    # kein Name vorbelegt
        self.assertLess(PAGE.index('id="chat_optbox"'), i)                                  # in den Einstellungen (Zahnrad)
        self.assertIn('["tw_ch_in","tw_ch2_in"].forEach(id=>', PAGE)
        self.assertIn('go.hidden=wait||on||!!a.need_channel;', PAGE)
        self.assertIn('srtlaCall("POST","/api/twitch",{channel:v})', PAGE)

    def test_main_category_arrow_has_its_own_colour(self):
        """Der Pfeil vor einer Hauptkategorie (Kartentitel) ist bernsteinfarben, die Unterpunkte behalten ihre Farbe (Wunsch des Nutzers, ohne Platz zu verbrauchen)."""
        self.assertIn("details.card>summary .sumh::before,html[data-design] details.card>summary .sumh::before{color:var(--warn);border-color:var(--warn)}", PAGE)
        self.assertIn("details.card>summary .sumh{font-size:12px;font-weight:600;", PAGE)                   # die Überschrift der Hauptkategorie minimal fetter
        self.assertNotRegex(PAGE, r"\.subsec>summary::before\{[^}]*var\(--warn\)")                 # Unterpunkte nicht in derselben Farbe

    def test_login_window_fades_out_after_a_few_seconds_and_chat_rows_are_slim(self):
        """Nach der Anmeldung blendet Code und Meldung nach einigen Sekunden sanft aus; Chat-Zeilen am Rechner: kein ⋯-Knopf, der die Zeile dehnt, weniger Luft."""
        self.assertIn("const justDone=on&&twWasWait; twWasWait=wait;", PAGE)
        self.assertIn("classList.add(\"twfade\")", PAGE)
        self.assertIn(".twfade{opacity:0;max-height:0!important;", PAGE)
        self.assertIn("transition:opacity .5s ease,max-height .6s ease", PAGE)
        self.assertIn("min-height:20px;height:20px;align-self:center}", PAGE)                  # ⋯ nicht höher als die Textzeile
        self.assertIn("column-gap:10px;padding:3px 2px 2px;", PAGE)
        self.assertIn("font-size:12px;line-height:1.6;", PAGE)

    def test_sign_in_choices_are_three_equal_buttons_under_one_short_line(self):
        """Ohne eigene Anmeldung (Handy und Rechner): eine kurze Zeile "Streamer: Name. Anmelden als:" und drei gleich breite Knöpfe (Zuschauer, Moderator, Streamer)."""
        self.assertIn(".twacc.twwho{display:grid;grid-template-columns:repeat(3,1fr);", PAGE)
        self.assertIn('t.textContent=a.box_login?`Streamer: ${a.box_login}. Anmelden als:`', PAGE)
        self.assertIn('go.textContent=other?"Zuschauer":"Mit Twitch anmelden"; $("tw_acc_asmod").textContent="Moderator"; $("tw_acc_owner").textContent="Streamer";', PAGE)
        self.assertIn('classList.toggle("twwho",other&&!wait&&!on)', PAGE)

    def test_chat_badges_are_small_symbols_not_words(self):
        """Abzeichen im Chat als kleine Symbole im farbigen Quadrat statt Wörtern ("STREAMER" war viel zu breit); der Name steht als Hinweis."""
        self.assertIn('const BADGE_TXT={broadcaster:"\\u25CF",moderator:"\\u2694\\uFE0E",vip:"\\u25C6",subscriber:"\\u2605"', PAGE)
        self.assertNotIn('broadcaster:"STREAMER"', PAGE)
        self.assertIn("min-width:16px;text-align:center;font-size:10px", PAGE)
        self.assertIn("bg.title=BADGE_TIP[bn]||bn", PAGE)

    def test_live_card_shows_state_then_signal_graph_then_megabits_and_phone_rows_are_calmer(self):
        """Karte "Live": erst "Live", dann der Verlauf (Pulsanzeige), dann die Megabit-Zahl, einheitlich; Handy: Wechsel hell/dunkel zwischen den Chat-Zeilen nur leicht."""
        self.assertIn("if(sig.parentNode!==up.parentNode) up.before(sig); if(dots.parentNode!==gear.parentNode) gear.before(dots);", PAGE)
        self.assertIn("setTimeout(placeSig,0);", PAGE)
        self.assertIn("#livebox .row .sig .sgv{display:none}", PAGE)
        i = PAGE.index("@media(max-width:620px){\n  .chatbox{max-height:56vh")
        self.assertIn(".chatbox .cm.alt{background:color-mix(in srgb,var(--text) 3%,transparent)}", PAGE[i:i + 1500])
        self.assertIn(".chatbox .cm.alt{background:color-mix(in srgb,var(--text) 6%,transparent)", PAGE)      # Rechner unverändert

    def test_preview_from_outside_has_its_switch_in_the_preview_card(self):
        """Der Schalter "Vorschau auch von außen erlauben" steht in der Karte "Vorschau" (der Hinweis hinter einem "i"), ist auch über Tailscale sichtbar und änderbar (0.9.231) und die Vorschau erklärt, warum sie von außen aus ist."""
        i = PAGE.index('<details class="card wide" id="c_prev">')
        block = PAGE[i:PAGE.index("</details>", i)]
        self.assertIn('id="pa_ext"', block)
        self.assertIn("Vorschau auch von außen erlauben (verbraucht Upload)", block)
        self.assertIn('id="pa_help_btn"', block)                                             # der Hinweistext ist hinter einem "i" (Erklärung zeigen)
        self.assertRegex(block, r'<div class="ph" id="pa_help" hidden>Jede Vorschau braucht rund 1,7 Mbit/s')
        ua = PAGE[PAGE.index('<details class="subsec" id="net_ua">'):]
        ua = ua[:ua.index("</details>")]
        self.assertNotIn("pa_ext", ua)                                                       # unter "Zugriff auf diese Oberfläche" steht er nicht mehr
        self.assertIn('$("pa_help_btn").addEventListener("click"', PAGE)
        self.assertIn('srtlaCall("POST","/api/previewaccess",{external:e.target.checked})', PAGE)
        self.assertNotIn('$("pa_row").hidden', PAGE)                                         # der Schalter wird nie versteckt, auch über Tailscale nicht
        self.assertNotIn("Ändern lässt sich das nur von dort.", PAGE)
        self.assertIn("Sie lässt sich in dieser Karte („Vorschau“) erlauben.", PAGE)
        self.assertIn('running()&&(st.why==="off"||st.why==="kein-bild")) return waiting();', PAGE)  # "extern" wartet nicht endlos, der Grund steht da
        self.assertIn('extern:"Die Vorschau ist von außen nicht freigegeben.', PAGE)
        self.assertIn('st.origin==="extern"&&!auto', PAGE)                                      # seit 1.0.6: der Hinweis erscheint nur kurz, siehe test_external_preview_hint_disappears_after_a_while
        self.assertIn('const hint="Vorschau von außen: klein', PAGE)

    def test_dji_scan_button_shows_the_reason_in_the_camera_card(self):
        """"Netze suchen" in der DJI-Karte schrieb Fehler nur in die WLAN-Karte weiter oben; dort stand nur "keine Netze gefunden". Jetzt steht der Grund in der Kamera-Karte."""
        self.assertIn('<div class="d-nearmsg" role="status"></div>', PAGE)
        self.assertIn("Keine WLAN-Karte gefunden. Name und Passwort lassen sich auch von Hand eintragen.", PAGE)
        self.assertIn("Keine Netze gefunden. Steckt ein WLAN-Stick, und läuft keine Sendung?", PAGE)
        self.assertIn("const err=await wifiDo({action:\"scan\",iface:$(\"w_if\").value},\"Suche läuft …\");", PAGE)
        self.assertIn('return x.message||"Fehler"; }', PAGE)                                   # wifiDo meldet den Fehler zurück

    def test_live_card_hints_missing_layout(self):
        """Kamera da, Bildaufbau noch nicht gespeichert: Live ist grau; die Live-Karte sagt warum und führt zum Bildaufbau (Wunsch des Nutzers)."""
        self.assertIn('<div id="livehint" class="pending" hidden>', PAGE)
        self.assertIn('id="live_topipe"', PAGE)
        self.assertIn('$("livehint").hidden=!(!d.active&&d.no_layout);', PAGE)
        self.assertIn('$("live_topipe").addEventListener("click"', PAGE)
        self.assertIn('out["no_layout"] = not active and not self.pipeline.cfg.get("main")', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())

    def test_live_card_warns_about_one_address_range_at_two_devices(self):
        """Derselbe Adressbereich bei zwei verschiedenen Geräten (zum Beispiel zwei Modems mit 192.168.8.x): kleine Warnung in der Live-Karte mit Kartennamen und Netz, auch während der Sendung. LAN und WLAN am selben Router warnt nicht (Wunsch des Nutzers, 12. Oktober 2026)."""
        self.assertIn('<div id="nethint" class="pending" hidden>', PAGE)
        self.assertIn("Zwei Verbindungen nutzen denselben Adressbereich bei verschiedenen Geräten (zum Beispiel zwei Modems oder Hotspots mit 192.168.8.x): Das führt zu Adresskonflikten, und Kameras können ausfallen. Bitte eine davon trennen:", PAGE)
        self.assertIn('id="nethint_cards"', PAGE)
        self.assertIn('$("nethint").hidden=cards.length<2; $("nethint_cards").textContent=cards.join(", ")+(nets.length?" ("+nets.join(", ")+")":"");', PAGE)    # textContent: nie als HTML
        self.assertNotIn("nethint_cards\").innerHTML", PAGE)
        self.assertNotIn("net_overlap", PAGE)                                                                                  # nicht mehr an jede Überschneidung gebunden
        self.assertNotIn('$("nethint").hidden=!(!d.active', PAGE)                                                       # nicht an "keine Sendung" gebunden
        self.assertIn('out["net_conflict"] = net_conflict()', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())

    def test_delay_warning_names_the_engine_limit_only_for_always_ready(self):
        """Verzögerungen über 750 ms werden von der Engine "Alle Kameras immer bereit" begrenzt: Hinweis nur dort, nur bei Bild-in-Bild, nur für gewählte Kameras."""
        self.assertIn('<div id="dl_warn" class="ph" role="note" hidden', PAGE)
        self.assertIn("Mit „Alle Kameras immer bereit“ werden Verzögerungen über 750 ms auf 750 ms begrenzt. Größere Werte ließen das Hauptbild ruckeln.", PAGE)
        self.assertIn("const DL_ENGINE_MAX=750;", PAGE)
        self.assertIn('el.hidden=!(pip&&always&&Math.max(...vals)>DL_ENGINE_MAX);', PAGE)
        self.assertIn('always=!!($("p_always")&&$("p_always").checked)', PAGE)
        self.assertNotIn("DL_SPREAD_WARN", PAGE)
        self.assertIn("MAX_DELAY_MS = 750", open(os.path.join(ROOT, "pipbox_live.py"), encoding="utf-8").read())      # dieselbe Zahl wie in der Sendekette

    def test_live_card_hints_missing_srtla_server(self):
        """Noch kein SRTLA-Server eingetragen: Live ist grau; die Live-Karte sagt es oben wie beim fehlenden Bildaufbau und führt zum Server (Wunsch des Nutzers)."""
        self.assertIn('<div id="srvhint" class="pending" hidden>', PAGE)
        self.assertIn("Live geht erst, wenn ein SRTLA-Server eingetragen ist: Server hinzufügen und auswählen.", PAGE)
        self.assertIn('id="live_tosrv"', PAGE)
        self.assertIn('$("srvhint").hidden=!(!d.active&&d.no_server);', PAGE)
        self.assertIn('!(d.no_server&&r==="Kein SRTLA-Server ausgewählt")', PAGE)                   # der Grund steht nicht doppelt in der Zeile darunter
        self.assertIn('$("live_tosrv").addEventListener("click",()=>{ const c=$("srtlacard"); c.open=true;', PAGE)
        self.assertIn('out["no_server"] = not active and not pub["selected"]', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())
        self.assertIn('id="srtlacard"', PAGE)                                                      # Ziel des Knopfs

    def test_external_preview_hint_disappears_after_a_while(self):
        """Der Hinweis über der kleinen Vorschau von außen stand die ganze Zeit da (Wunsch des Nutzers: nach einer Weile ausblenden, nicht bei jedem automatischen Neustart)."""
        self.assertIn("hintTimer=setTimeout(()=>{ if(msg.textContent===hint) msg.textContent=\"\"; },15000);", PAGE)
        self.assertIn('if(st.origin==="extern"&&!auto){', PAGE)                                  # nicht bei automatischem Neustart
        self.assertIn("clearTimeout(hintTimer); mode=\"\"", PAGE)                                     # halt() räumt den Zeitgeber auf
        self.assertEqual(PAGE.count("Vorschau von außen: klein, damit der Upload für die Sendung bleibt."), 1)
        self.assertNotIn('msg.textContent=st.origin==="extern"?', PAGE)                         # nicht mehr dauerhaft gesetzt

    def test_live_row_carries_server_and_box_temperature_on_the_right(self):
        """Desktop: Rechts neben LIVE, Graph und Megabit war die Karte leer, der Server stand eine Zeile tiefer und die Temperatur fehlte (Wunsch des Nutzers)."""
        i = PAGE.index('<div class="row liverow"')
        row = PAGE[i:PAGE.index("</div>", i)]
        for part in ('id="livestate"', 'id="liveup"', 'id="liveserver" class="muted livesrv"', 'id="livetemp"'):
            self.assertIn(part, row)
        self.assertEqual(PAGE.count('id="liveserver"'), 1)                                       # nicht mehr zusätzlich eine Zeile tiefer
        self.assertIn("#livebox .liverow .livesrv{margin:0 0 0 auto}", PAGE)                      # rechts außen
        self.assertIn('lt.textContent=`${fmt(tc,0)} °C`; lt.style.color=tc>=80?"var(--crit)":(tc>=70?"var(--warn)":"");', PAGE)
        self.assertIn('lt.hidden=tc==null;', PAGE)
        self.assertIn('LIMITS = {"temp_warn": 70.0, "temp_crit": 80.0', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())     # gleiche Grenzen wie die Meldung
        self.assertIn("#livecard #livebox>.row,#livecard #swapbar,#livecard #liveserver,#livecard #livehealth{display:none}", PAGE)        # am Handy bleibt alles wie vorher

    def test_live_row_has_a_small_gap_between_graph_and_megabit(self):
        """Zwischen dem Verlauf und der Megabit-Zahl standen 42 Pixel (14 Rand rechts, 16 Rand links, 12 Zeilenabstand): zu viel (Wunsch des Nutzers). Jetzt rund 14."""
        self.assertIn("#livebox .row .sig{margin:0 0 0 16px}", PAGE)
        self.assertIn(".liveup{margin-left:2px;font-size:22px;", PAGE)
        self.assertNotIn("#livebox .row .sig{margin:0 14px 0 16px}", PAGE)
        self.assertNotIn(".liveup{margin-left:16px;", PAGE)

    def test_removed_dji_camera_disappears_from_the_list_at_once(self):
        """Eine entfernte Kamera blieb in der Liste stehen, bis man die Seite neu lud: djiRender baut die Liste nicht neu auf, solange der Fokus in ihr steht, und der Knopf "Entfernen" hatte ihn (Nutzermeldung)."""
        self.assertIn("if(sig!==djiStruct&&!typing){", PAGE)                                                # der Schutz fürs Tippen bleibt (nur noch für Felder)
        i = PAGE.index('if(cl.contains("d-remove")){')
        handler = PAGE[i:PAGE.index('else if(cl.contains("d-saveddel"))', i)]
        self.assertIn('document.activeElement.blur()', handler)                                           # Fokus lösen
        self.assertIn('djiStruct=""; tick(); djiLoad();', handler)                                        # und sofort neu laden
        self.assertLess(handler.index("blur()"), handler.index('djiStruct=""'))                           # erst Fokus lösen, dann neu aufbauen lassen

    def test_a_button_in_the_dji_card_no_longer_blocks_the_refresh(self):
        """Nach "Neu verbinden" erschien das Netz erst unter "Gespeicherte Netze", wenn man die Karte zu- und wieder aufklappte: der Knopf hatte den Fokus, und der Neuaufbau wartete (Nutzermeldung, 12. Oktober 2026). Nur ein Feld, in das getippt wird, hält ihn an."""
        i = PAGE.index("const ae=document.activeElement, typing=")
        line = PAGE[i:PAGE.index("\n", i)]
        for tag in ('"TEXTAREA"', '"SELECT"', '"INPUT"'):
            self.assertIn(tag, line)
        self.assertIn("button|checkbox|radio|submit|reset", line)                                         # Knöpfe und Schalter zählen nicht als Tippen
        self.assertNotIn("BUTTON", line)
        self.assertNotIn("box.contains(document.activeElement)){", PAGE[i:i + 800].split("typing")[0])

    def test_lock_hint_has_a_reconnect_button(self):
        """Die Kamera bleibt nach einer anderen Verbindung, bis sie neu verbindet: der Hinweis dazu hat jetzt einen Knopf (derselbe wie "Neu verbinden")."""
        i = PAGE.index('<div class="ph d-lockhint"')
        hint = PAGE[i:PAGE.index("</div>", i)]
        self.assertIn("Die Kamera ist verbunden. Änderungen an der Verbindung gelten ab der nächsten Verbindung, die laufende bleibt bestehen.", hint)
        self.assertIn('class="sec d-reconnect"', hint)
        self.assertIn("Jetzt neu verbinden", hint)
        self.assertIn('else if(cl.contains("d-reconnect")) await djiCmd({cmd:"reconnect",addr});', PAGE)       # derselbe Befehl wie beim Knopf in der Zeile darunter
        self.assertIn('card.querySelector(".d-lockhint").hidden=!c.locked;', PAGE)                         # nur bei einer verbundenen Kamera sichtbar

    def test_dji_camera_row_has_a_connection_select_and_a_reconnect_button(self):
        """Die Verbindung einer DJI-Kamera ist auch in der Kameraliste wählbar (Wunsch des Nutzers, 12. Oktober 2026): dieselbe Einstellung wie in der DJI-Karte, mit Knopf zum Neu-Verbinden."""
        i = PAGE.index("function camNetHtml(c){")
        fn = PAGE[i:PAGE.index("// QR-Code-Erzeuger", i)]
        self.assertIn('data-dji="${esc(c.key)}"', fn)
        self.assertIn('class="camnetsel"', fn)
        self.assertIn('class="camreconn sec"', fn)
        self.assertNotIn("· DJI-Karte", fn)                                                               # kein reiner Text mehr
        self.assertIn('own&&c.via==="manual"', fn)                                                        # eine von Hand eingetragene Verbindung bleibt sichtbar (nicht wählbar)
        self.assertIn("netOpts.map(o=>", fn)                                                              # dieselben Verbindungen wie in der DJI-Karte
        ci = PAGE.index("const djiAddrOfKey=")
        handler = PAGE[ci:PAGE.index('$("cams").addEventListener("click"', ci)]
        self.assertIn("djiUpdate(addr,{wifi_ifname:e.target.value})", handler)                            # über den Kamera-Dienst, nicht über /api/cameras (der lehnt DJI-Kameras ab)
        self.assertLess(handler.index("djiUpdate(addr"), handler.index('"/api/cameras/"'))                 # DJI-Zweig zuerst
        click = PAGE[PAGE.index('$("cams").addEventListener("click"', ci):PAGE.index("async function renameCam")]
        self.assertIn('djiCmd({cmd:"reconnect",addr})', click)
        self.assertIn("x.rtmp_key===key", PAGE)                                                           # Schlüssel der Kamera -> Adresse im Kamera-Dienst
        # Der Knopf muss sichtbar bleiben: .camnet schneidet Überlauf ab und das Auswahlfeld füllt 100 %, ohne eigene Flex-Zeile verschwand der Knopf (1.0.18, gemessen auf der Box)
        self.assertIn('<span class="camnet withbtn">', fn)
        self.assertIn(".cam .camnet.withbtn{display:flex;", PAGE)
        self.assertIn(".cam .camnet.withbtn select{flex:1 1 0;min-width:0;width:auto}", PAGE)
        self.assertIn(".camreconn{flex:none;", PAGE)
        # Nur WLAN-Schnittstellen zur Wahl (eine DJI-Kamera tritt immer einem WLAN bei); eine schon gewählte Kabel-Schnittstelle bleibt sichtbar; normale Kameras unverändert
        self.assertIn("netOpts.filter(o=>o.wifi!==false||(own&&c.via===o.iface))", fn)
        self.assertIn("nets.map(o=>", fn)
        self.assertEqual(fn.count("netOpts.map(o=>"), 1)                                                  # der Zweig der normalen Kameras behält alle Verbindungen
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        self.assertIn('"wifi": os.path.isdir(f"/sys/class/net/{name}/wireless")', src)                    # der Server meldet, welche Schnittstelle ein WLAN ist
        self.assertIn("Die Verbindung einer DJI-Kamera wird in ihrer DJI-Karte gewählt", open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())      # der Server lehnt den alten Weg weiter ab

    def test_dji_camera_row_shows_a_small_network_change_note(self):
        """Unter der Zeile einer DJI-Kamera steht klein, was beim Netzwechsel passiert (Wunsch des Nutzers, 12. Oktober 2026)."""
        self.assertIn('<div class="netnote" data-key="${esc(c.key)}" role="status" hidden></div>', PAGE)   # nur für DJI-Kameras, zunächst leer
        self.assertIn(".cam .netnote,.cam .netlock{margin:3px 0 0;font-size:11px;", PAGE)                               # klein
        i = PAGE.index("function netNoteOf(key){")
        fn = PAGE[i:PAGE.index("function paintNetNotes(){", i)]
        for text in ("Bitte WLAN-Name und Passwort in der DJI-Karte eintragen.", "Wechsle Netz …", "Netzwechsel erfolgreich (${ch.iface})",
                     "Netzwechsel fehlgeschlagen. Die Meldung steht in der DJI-Karte.", "Netz gewählt. Gilt nach „Neu verbinden“ (↻).",
                     "Netz gewählt. Gilt beim nächsten Verbinden."):
            self.assertIn(text, fn)
        self.assertIn("/von Hand|Passwort/i", fn)                                                         # fehlendes Passwort: Hinweis auf die DJI-Karte
        self.assertIn('d.wifi_ifname===ch.iface', fn)                                                     # Erfolg nur, wenn die Kamera wirklich über die gewählte Verbindung sendet
        self.assertIn("ch.busy", fn)                                                                      # und nur nach einem neuen Verbindungsaufbau, nicht aus dem alten Zustand
        self.assertIn("ch.okAt>15000", fn)                                                                 # der Erfolg verschwindet nach 15 s
        self.assertEqual(fn.count("delete netChg[key]"), 1)                                               # nur der Erfolg löscht den Hinweis; Fehler bleiben stehen
        self.assertIn("netChg[e.target.dataset.dji]={iface:e.target.value,reconn:false,busy:false,okAt:0}", PAGE)   # Auswahl startet die Anzeige
        self.assertIn("{reconn:true,busy:false,okAt:0}", PAGE)                                            # Knopf "Neu verbinden" ebenso
        self.assertIn("setInterval(()=>{ if(!document.hidden) paintNetNotes(); },1000);", PAGE)

    def test_camera_that_stops_sending_stays_on_top_for_two_minutes(self):
        """Nutzerwunsch 12. Oktober 2026: Eine Kamera, die nicht mehr sendet (zum Beispiel beim Wechsel des Netzes), rutscht erst nach zwei Minuten nach unten."""
        self.assertIn("const CAM_HOLD_TOP_MS=120000;", PAGE)
        i = PAGE.index("const quiet=c=>")
        line_ = PAGE[i:PAGE.index("\n", i)]
        self.assertIn('c.state==="live"||c.state==="unknown"||nowMs-(camSeenLive[c.id]||0)<CAM_HOLD_TOP_MS?0:1', line_)
        self.assertIn('for(const c of list) if(c.state==="live"||c.state==="unknown") camSeenLive[c.id]=nowMs;', PAGE)   # merkt, wann sie zuletzt sendete

    def test_switch_lock_is_shown_and_disables_the_controls(self):
        """Nach einem Wechsel sind Auswahl und Knopf der Kamera zwei Minuten gesperrt (der Kamera-Dienst lehnt ihn ebenfalls ab); eine kleine Zeile nennt die Restzeit."""
        self.assertIn("Wechsel für zwei Minuten gesperrt: noch ${d.switch_wait} s.", PAGE)
        self.assertIn('<div class="netlock" data-lockkey="${esc(c.key)}" hidden></div>', PAGE)               # eigene Zeile
        self.assertIn("const show=lock&&!n,", PAGE)                                                       # erst der Hinweis zum Wechsel (grün), danach erst die Sperre
        self.assertIn("b.disabled!==lock) b.disabled=lock;", PAGE)
        self.assertIn("d.switch_wait>0", PAGE)

    def test_installed_version_has_an_info_button_that_shows_only_its_changes(self):
        """Bei der installierten Version steht ein "i" (Wunsch des Nutzers, 12. Oktober 2026): öffnet den Änderungsverlauf und zeigt nur diese Version."""
        i = PAGE.index('<span>Installiert</span>')
        row = PAGE[i:PAGE.index("</div>", i)]
        self.assertIn('id="sw_curinfo"', row)
        self.assertIn('class="swinfo"', row)                                                              # nicht "ibtn": "Hilfe ausblenden" soll ihn nicht verstecken (er öffnet keinen Hilfetext)
        self.assertIn(".swinfo{display:inline-block", PAGE)
        self.assertIn("html[data-design] .swinfo{min-height:0;width:26px;", PAGE)                            # Größe in den Designs wie die anderen "i"-Knöpfe
        self.assertIn('window.swCurVer=d.current||""; $("sw_curinfo").hidden=!d.current;', PAGE)         # ohne bekannte Version kein Knopf
        self.assertIn('if(vre&&!vre.test(sec.head)) continue;', PAGE)                                     # genau die Überschrift dieser Version (nicht jede Erwähnung)
        self.assertIn('q.addEventListener("input",()=>{ only=null;', PAGE)                                # Tippen im Suchfeld zeigt wieder alle Versionen
        self.assertIn('if(open){ only=null;', PAGE)                                                       # "Alle Änderungen" zeigt alle

    def test_remote_card_always_links_to_the_tailscale_https_switch(self):
        """Fernzugriff: Ist die Box verbunden, aber noch nicht freigegeben, steht ein fester Link zu den Tailscale-Einstellungen da, wo HTTPS einmal freigeschaltet wird (ein Nutzer fand den Weg nicht)."""
        self.assertIn('<a id="r_httpslink" class="btnlink" href="https://login.tailscale.com/admin/dns" target="_blank" rel="noopener noreferrer">Tailscale-Einstellungen öffnen</a>', PAGE)
        self.assertIn("#rcard #r_https a.btnlink{background:var(--warn)", PAGE)                    # fett, in der Warnfarbe
        self.assertIn('$("r_https").hidden=!(d.installed&&d.connected&&!d.serve);', PAGE)
        self.assertIn("Für die Freigabe muss HTTPS im Tailscale-Konto einmal freigeschaltet sein.</b>", PAGE)


class LiveAlwaysReachable(unittest.TestCase):
    def test_header_live_button_appears_when_the_footer_is_gone_for_good(self):
        """Am Handy steht Live/Stop in der Fußleiste; war sie per Einstellung ganz weg, fehlte der Knopf zum Live-Gehen (Meldung des Nutzers)."""
        self.assertIn("html:not(.mfperm) #hdr_live{display:none!important}", PAGE)
        self.assertNotIn("  #hdr_live{display:none!important}", PAGE)                            # nicht mehr bedingungslos weg
        self.assertIn('root.classList.toggle("mfperm",mode==="off"||(mode==="hide"&&wide))', PAGE)  # mfperm = Leiste dauerhaft weg (Ausblenden bei Chat/Vorschau, Immer ausblenden)

    def test_live_button_in_the_preview_card_while_not_sending(self):
        """Die Vorschau gibt es nur beim Senden; solange nicht gesendet wird, steht dort ein Knopf zum Live-Gehen."""
        self.assertIn('<button type="button" id="prev_live" class="livebtn" hidden>Live gehen</button>', PAGE)
        self.assertIn("pl.hidden=live||busy; pl.disabled=!d.can_start; pl.title=b.title;", PAGE)
        self.assertIn('$("prev_live").addEventListener("click",liveToggle);', PAGE)

    def test_header_label_is_short_on_phones(self):
        self.assertIn('matchMedia("(max-width:620px)").matches?"Live":"Live gehen"', PAGE)

    def test_phone_header_shows_only_the_logo_and_wide_centred_buttons(self):
        """Wunsch des Nutzers: am Handy dauerhaft das Logo (kein Wechsel mit dem Text), dafür breite, zentrierte Knöpfe."""
        self.assertIn("h1.hl{flex:0 0 auto}h1.hl .htxt{display:none}h1.hl .hlogo{animation:none;opacity:1}", PAGE)
        self.assertIn("display:inline-flex;align-items:center;justify-content:center;min-width:36px;min-height:34px;padding:3px 10px;line-height:1;text-align:center;white-space:nowrap}", PAGE)
        self.assertIn("header .hdr select{min-height:34px;text-align:center;text-align-last:center}", PAGE)
        i = PAGE.index("Knöpfe im Kopf etwas enger, damit auch mit Update-Punkt und Live-Knopf alles passt")   # schmale Handys: enger
        self.assertIn("min-width:30px;padding:3px 6px", PAGE[i:i + 400])


if __name__ == "__main__":
    unittest.main()
