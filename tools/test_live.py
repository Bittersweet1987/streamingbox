"""Tests für die Engine "alle Kameras immer bereit" mit Compositor (pipbox_live.py): Geometrie wie im Baustein pbpipmix, Pipeline-Text (feste Form,
alle Plätze, feste Ausgabe), Befehle für den Steuerkanal (nur Änderungen, oberstes Bild zuletzt), Regler (Zweige starten/stoppen, Wahl von Haupt- und
kleinen Bildern, anderer Schlüssel ohne Neustart, Ansicht) und die Anbindung an server.py. Die echte Kette wurde mit Messskripten auf der Box geprüft (nicht mehr im Repository, siehe Git-Historie bis 0.9.144)."""
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
_env = mock.patch.dict(os.environ, {"PIPBOX_LIVE": "1"})       # auf dem Entwicklungsrechner gibt es den belacoder sb10 nicht; nur für dieses Modul (sonst verändert es die Tests der anderen Dateien)


def setUpModule():
    _env.start()


def tearDownModule():
    _env.stop()
if not hasattr(os, "killpg"):                   # Windows (nur Entwicklungsrechner): die Module der Box brauchen Linux-Teile beim Import
    os.killpg = lambda *a, **k: None
    for _m in ("pwd", "grp", "fcntl", "termios"):
        sys.modules.setdefault(_m, mock.MagicMock())
import pipbox_live as L  # noqa: E402
import server  # noqa: E402

CFG = {"type": "pip", "main": "dji-a", "pip": "cam2", "pip2": "cam3", "pip3": "cam4", "corner": 5, "corner2": 2, "corner3": 0,
       "x": 994, "y": 0, "size_pct": 99, "size_pct2": 40, "size_pct3": 25, "audio": "main", "always_ready": True,
       "main_delay_ms": 450, "pip_delay_ms": 0, "pip2_delay_ms": 0, "pip3_delay_ms": 0,
       "styles": {"1": {"visible": True, "opacity": 100, "crop": {"l": 600, "r": 600, "t": 0, "b": 0}, "radius": 0,
                        "border": {"enabled": True, "width": 6, "color": "#ffffff", "opacity": 100}}}}


def safe(cfg=None):
    return server.PipelineStore._safe_cfg(dict(CFG, **(cfg or {})))


class Geometry(unittest.TestCase):
    def test_size_and_crop_like_the_plugin(self):
        self.assertEqual(L.pip_size(99), server.pip_size(99))
        self.assertEqual(L.pip_size(25), server.pip_size(25))
        g = L.small_geometry(safe(), 1)
        self.assertEqual((g["sw"], g["sh"]), (1904, 1072))
        l, r, t, b = g["crop"]
        self.assertEqual((l, r, t, b), (600, 600, 0, 0))               # Beschnitt in Bildpunkten der Quelle (Bezug 1920x1080), vor dem Skalieren
        self.assertEqual((g["w"], g["h"]), (714, 1072))                  # sichtbar: (1920-1200) * 1904 / 1920

    def test_crop_is_ignored_when_too_little_is_left(self):
        st = {"crop": {"l": 1000, "r": 1000, "t": 0, "b": 0}}
        self.assertEqual(L.crop_px(st, 1904, 1072), (0, 0, 0, 0))

    def test_corners_and_free_position(self):
        m = (1920 // 60) & ~1
        self.assertEqual(L.place(0, 1920, 1080, 480, 270, m, 0, 0), (m, m))
        self.assertEqual(L.place(3, 1920, 1080, 480, 270, m, 0, 0), (1920 - 480 - m, 1080 - 270 - m))
        self.assertEqual(L.place(4, 1920, 1080, 480, 270, m, 0, 0), ((1920 - 480) // 2, 1080 - 270 - m))
        self.assertEqual(L.place(5, 1920, 1080, 480, 270, m, 1000, 1000), (1440, 810))
        self.assertEqual(L.place(5, 1920, 1080, 480, 270, m, -5, 5000), (0, 810))

    def test_big_picture_keeps_its_aspect(self):
        self.assertEqual(L.fit_big((1920, 1080)), (0, 0, 1920, 1080))
        self.assertEqual(L.fit_big(None), (0, 0, 1920, 1080))
        x, y, w, h = L.fit_big((1080, 1920))                           # Hochformat: schwarze Balken links und rechts
        self.assertEqual((y, h), (0, 1080))
        self.assertLess(w, 1920)
        self.assertLessEqual(abs(x * 2 + w - 1920), 2)

    def test_ring_is_inside_the_picture(self):
        g = L.small_geometry(safe(), 1)
        top, bottom, left, right = L.ring_rects(g)
        self.assertEqual(top, (g["x"], g["y"], g["w"], g["bw"]))
        self.assertEqual(bottom[1], g["y"] + g["h"] - g["bw"])
        self.assertEqual(right[0], g["x"] + g["w"] - g["bw"])

    def test_buffer_covers_the_delay(self):
        self.assertEqual(L.buffer_ms(safe()), 1050)                     # Mindestpuffer (größte Verzögerung 450 ms + 300 ms wären nur 750)
        self.assertEqual(L.buffer_ms(safe({"main_delay_ms": 0})), 1050)
        self.assertEqual(L.buffer_ms(safe({"main_delay_ms": 750})), 1050)     # größte erlaubte Verzögerung: gleicher Puffer

    def test_a_copying_engine_gets_the_larger_buffer_and_the_same_lead_limit_for_the_main_picture(self):
        """belacoder ab -sb13 kopiert das Hauptbild aus dem Dekoderspeicher: Es darf wie die kleinen Bilder bis 1000 ms früh ankommen, der Puffer ist 1350 ms."""
        with mock.patch.dict(os.environ, {"PIPBOX_ENGINE": "12"}):
            self.assertEqual(L.engine_level(), 12)
            self.assertEqual(L.buffer_ms(safe()), 1050)
        with mock.patch.dict(os.environ, {"PIPBOX_ENGINE": "13"}):
            self.assertEqual(L.engine_level(), 13)
            self.assertEqual(L.buffer_ms(safe()), 1350)
            self.assertEqual(L.buffer_ms(safe({"main_delay_ms": 750, "pip_delay_ms": 750})), 1350)   # Verzögerungen allein brauchen höchstens 1050
            c = safe()
            hold = L.hold_delays(L.delays_by_key(c), L.buffer_ms(c), "dji-a", L.LEAD_MAX_MS)
            self.assertEqual(hold["dji-a"], 450)                                   # Haltezeit des Hauptbilds bleibt, der Spielraum ist 1350 - 483 = 867 ms
            self.assertEqual(L.hold_delays({"a": 0, "b": 0}, 1350, "a", L.LEAD_MAX_MS), {"a": 350, "b": 350})
            self.assertEqual(L.hold_delays({"a": 0, "b": 0}, 1350, "a"), {"a": 750, "b": 350})   # ohne Angabe gilt die alte Grenze 600 ms
        with mock.patch.dict(os.environ, {"PIPBOX_ENGINE": "x"}):
            self.assertEqual(L.engine_level(), 0)

    def test_the_controller_remembers_the_engine_it_was_started_with(self):
        f = Fakes({})
        for lvl, lead in (("12", L.LEAD_MAX_BIG_MS), ("13", L.LEAD_MAX_MS)):
            with mock.patch.dict(os.environ, {"PIPBOX_ENGINE": lvl}):
                c = f.controller(tempfile.mkdtemp())
            self.assertEqual(c.big_lead, lead, lvl)
            with mock.patch.dict(os.environ, {"PIPBOX_ENGINE": "13" if lvl == "12" else "12"}):
                self.assertEqual(c.big_lead, lead)                                  # eine später ausgetauschte Datei ändert es nicht

    def test_a_chain_started_before_the_minimum_buffer_is_not_reported_as_outdated(self):
        """Lief die Sendekette mit Puffer 750 (vor 1.0.29) und sind die Verzögerungen unverändert, fehlt nichts: kein "noch nicht übernommen"."""
        self.assertEqual(L.buffer_need_ms(safe()), 750)
        self.assertFalse(L.buffer_mismatch(750, L.buffer_need_ms(safe())))
        self.assertFalse(L.buffer_mismatch(600, L.buffer_need_ms(safe({"main_delay_ms": 0}))))
        self.assertTrue(L.buffer_mismatch(750, L.buffer_need_ms(safe({"pip_delay_ms": 750}))))

    def test_the_default_buffer_gives_the_bursty_main_camera_300_ms_more_room_without_a_longer_hold(self):
        """Pocket 3 als Hauptbild kam mit 0,7 s Streuung an; der Puffer 750 ließ nur etwa 65 ms Spielraum (13. Oktober 2026). Mit 1050 bleibt die Haltezeit des Hauptbilds
        bei 450 ms (Untergrenze Puffer - 600), der Spielraum wächst um 300 ms."""
        c = safe()
        buf = L.buffer_ms(c)
        hold = L.hold_delays(L.delays_by_key(c), buf, "dji-a")
        self.assertEqual(hold["dji-a"], 450)
        self.assertLessEqual(buf - hold["dji-a"], L.LEAD_MAX_BIG_MS)
        self.assertGreaterEqual(L.BUFFER_MIN_MS, 1050)


class PipelineText(unittest.TestCase):
    def setUp(self):
        self.text = L.build(safe())

    def test_all_places_always_exist(self):
        for s in range(4):
            for name in ("src", "vq", "dec", "scc", "crop", "q", "aq2"):
                self.assertIn(f"name=sbf{s}_{name} ", self.text + " ")
            self.assertIn(f"pipcomp.sink_{s}\n", self.text)
            self.assertIn(f"asel.sink_{s + 1}\n", self.text)
        for i in range(12):
            self.assertIn(f"name=sbb{i} ", self.text)
            self.assertIn(f"pipcomp.sink_{4 + i}\n", self.text)

    def test_output_has_a_fixed_format_and_no_old_elements(self):
        self.assertIn("video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1", self.text)
        for old in ("pbpipsel", "pbpipmix", "pbpipsink", "pbctl", "udpsrc", "rtph264depay"):
            self.assertNotIn(old, self.text)
        self.assertIn("name=venc_bps", self.text)
        self.assertIn("appsink name=appsink", self.text)
        self.assertIn("audiotestsrc is-live=true wave=silence", self.text)

    def test_all_branches_start_stopped(self):
        self.assertEqual(self.text.strip().splitlines()[-1], "#sb-absent: sbf0 sbf1 sbf2 sbf3")

    def test_slots_follow_the_binder_and_empty_slots_have_a_dummy_key(self):
        t = L.build(safe({"pip3": "", "pip2": ""}))
        self.assertIn("name=sbf0_src location=rtmp://127.0.0.1:1935/publish/dji-a ", t)
        self.assertIn("name=sbf1_src location=rtmp://127.0.0.1:1935/publish/cam2 ", t)
        self.assertIn("publish/pipbox_unused_2 ", t)
        self.assertIn("publish/pipbox_unused_3 ", t)

    def test_text_does_not_depend_on_who_is_big(self):
        swapped = safe({"main": "cam2", "pip": "dji-a"})
        # nach einem Tausch in der Einstellung bleibt die Reihenfolge der Plätze beim ersten Aufbau (Binder), der Text ist sonst gleich gebaut
        self.assertEqual(self.text.count("rtmpsrc"), L.build(swapped).count("rtmpsrc"))


class Commands(unittest.TestCase):
    def cmds(self, lay, roles, **kw):
        c = safe()
        args = dict(c=c, slot_key=["dji-a", "cam2", "cam3", "cam4"], roles=roles, dims={}, audio_ok={0, 1, 2}, hide=0, audio_pos=-1, mute=False,
                    delay_ms={"dji-a": 450})
        args.update(kw)
        return lay.commands(**args)

    def test_first_send_is_complete_then_only_changes(self):
        lay = L.Layout()
        first = self.cmds(lay, {0: 0, 1: 1, 2: 2})
        self.assertIn("pad pipcomp sink_0 alpha 1.00", first)
        self.assertIn("pad pipcomp sink_3 alpha 0", first)             # Platz 3 ohne Kamera: ausgeblendet
        self.assertIn("select asel sink_1", first)
        self.assertIn("set avol mute false", first)
        self.assertEqual(self.cmds(lay, {0: 0, 1: 1, 2: 2}), [])       # nichts geändert: nichts senden

    def test_swap_moves_only_layout_never_the_branch(self):
        lay = L.Layout()
        self.cmds(lay, {0: 0, 1: 1})
        swapped = self.cmds(lay, {1: 0, 0: 1})
        self.assertTrue(swapped)
        self.assertTrue(all(l == L.WAIT_MARK or l.startswith(("set sbf", "pad pipcomp", "select asel", "set sbb")) for l in swapped))
        self.assertFalse(any(l.startswith("feed ") for l in swapped))
        # das Bild, das oben landet (jetzt das kleine auf Platz 0, Reihenfolge 2), wird zuletzt bewegt: erst das große (Platz 1), dann das kleine
        self.assertGreater(swapped.index("pad pipcomp sink_0 zorder 2"), swapped.index("pad pipcomp sink_1 zorder 1"))
        self.assertGreater(swapped.index(next(l for l in swapped if l.startswith("pad pipcomp sink_0 width"))), swapped.index("pad pipcomp sink_1 width 1920"))
        self.assertGreater(swapped.index("pad pipcomp sink_0 zorder 2"), swapped.index(L.WAIT_MARK))             # das schrumpfende Bild erst nach der Pause

    def test_scaling_caps_come_before_pad_size(self):
        lay = L.Layout()
        lines = self.cmds(lay, {0: 0, 1: 1})
        i = lines.index(next(l for l in lines if l.startswith("set sbf1_scc caps")))
        j = lines.index(next(l for l in lines if l.startswith("pad pipcomp sink_1 width")))
        self.assertLess(i, j)

    def test_hidden_position_and_audio_follow_the_view(self):
        lay = L.Layout()
        lines = self.cmds(lay, {0: 0, 1: 1}, hide=1, audio_pos=0)
        self.assertIn("pad pipcomp sink_1 alpha 0.00", lines)           # Stelle 1 ausgeblendet
        self.assertIn("select asel sink_2", lines)                     # Ton des kleinen Bildes an Stelle 1 (Platz 1)
        self.assertTrue(any(l.startswith("pad pipcomp sink_4 alpha 0.00") for l in lines))   # Rahmen mit ausgeblendet

    def test_audio_falls_back_to_silence_when_the_camera_has_no_audio(self):
        lay = L.Layout()
        lines = self.cmds(lay, {0: 0, 1: 1}, audio_ok={1})
        self.assertIn("select asel sink_0", lines)

    def test_delay_goes_to_the_compressed_queue_of_the_camera(self):
        lines = self.cmds(L.Layout(), {0: 0})
        self.assertIn(f"set sbf0_vq min-threshold-time {(450 + 33) * 1000000}", lines)
        self.assertIn("set sbf1_vq min-threshold-time 0", lines)


class AudioQueues(unittest.TestCase):
    """Die Warteschlangen des Tons wachsen mit dem Puffer (Ton wartet bis zum Termin; fest 2 s + 1 s lief bei Puffern über 2,5 s über)."""

    def text(self, **cfg):
        return L.build(safe(cfg))

    def test_normal_buffer_keeps_two_seconds_at_least(self):
        t = self.text()                                                     # Puffer 1050 + 1 s
        self.assertIn("sbf0_aq max-size-time=2050000000 max-size-buffers=0 max-size-bytes=0", t)
        self.assertIn("sbf0_aq2 max-size-time=2050000000 max-size-buffers=0 max-size-bytes=0", t)

    def test_large_buffer_gets_larger_queues_on_every_branch(self):
        t = self.text(main_delay_ms=450, pip_delay_ms=750)                 # größter Puffer der Engine: 750 + 300 = 1050
        for n in range(4):
            self.assertIn(f"sbf{n}_aq max-size-time=2050000000 max-size-buffers=0 max-size-bytes=0", t)
            self.assertIn(f"sbf{n}_aq2 max-size-time=2050000000 max-size-buffers=0 max-size-bytes=0", t)
        t = self.text(main_delay_ms=450, pip_delay_ms=3000)                # gespeicherte 3000 ms werden begrenzt: derselbe Puffer
        self.assertIn("sbf0_aq max-size-time=2050000000", t)


@mock.patch.object(L, "BUFFER_MIN_MS", 600)        # diese Prüfungen betreffen die Rechnung mit dem alten Mindestpuffer (so bleiben die Fälle "passt nicht in den Puffer" erreichbar)
class BufferMismatch(unittest.TestCase):
    """Passt der Ausrichtungspuffer der laufenden Sendekette noch zu den gespeicherten Verzögerungen? (12. Oktober 2026)"""

    def test_table(self):
        mm = L.buffer_mismatch
        self.assertFalse(mm(750, 750))                      # unverändert
        self.assertFalse(mm(750, 600))                      # kleinere Verzögerung, Puffer nur etwas größer als nötig: gut
        self.assertFalse(mm(600, 750))                      # 150 ms knapper als empfohlen: noch in Ordnung
        self.assertTrue(mm(600, 751))                       # mehr als 150 ms zu knapp: verzögerte Bilder kämen zu spät
        self.assertFalse(mm(1400, 600))                     # genau 800 ms Überschuss
        self.assertTrue(mm(1401, 600))                      # mehr als 800 ms Überschuss: Bilder kürzerer Verzögerung kommen zu früh
        self.assertTrue(mm(2860, 600))                      # der Fall vom 12. Oktober: 2560 ms Verzögerung entfernt
        self.assertTrue(mm(600, 2860))                      # und der umgekehrte: neue hohe Verzögerung ohne Neustart

    def test_bad_values_never_raise_and_never_flag(self):
        for a, b in ((None, 600), (600, None), ("x", 600), ({}, []), (None, None)):
            self.assertFalse(L.buffer_mismatch(a, b), (a, b))

    def test_need_comes_from_the_largest_delay_plus_300_with_a_floor_of_600(self):
        self.assertEqual(L.buffer_ms(safe({"main_delay_ms": 0})), 600)
        self.assertEqual(L.buffer_ms(safe({"main_delay_ms": 450})), 750)
        self.assertEqual(L.buffer_ms(safe({"main_delay_ms": 450, "pip_delay_ms": 750})), 1050)

    def test_stored_delays_above_the_limit_are_capped_for_this_engine(self):
        for big in (751, 1500, 2560, 3000):
            c = safe({"main_delay_ms": 450, "pip_delay_ms": big})
            self.assertEqual(L.delays_by_key(c)["cam2"], 750, big)
            self.assertEqual(L.buffer_ms(c), 1050, big)                     # der Puffer wächst nie über 1050 ms
        self.assertEqual(L.delays_by_key(safe({"main_delay_ms": 750}))["dji-a"], 750)     # genau die Grenze bleibt


class HoldDelays(unittest.TestCase):
    """Haltezeit auf der komprimierten Seite: kein Bild darf mehr als LEAD_MAX_MS vor seinem Termin am Mischer ankommen (Testbox, 12. Oktober 2026)."""

    def test_normal_settings_are_untouched(self):
        self.assertEqual(L.hold_delays({"a": 450, "b": 0, "c": 0}, 750), {"a": 450, "b": 0, "c": 0})        # Standard
        self.assertEqual(L.hold_delays({"a": 0, "b": 0}, 600), {"a": 0, "b": 0})
        self.assertEqual(L.hold_delays({"a": 700, "b": 0}, 1000), {"a": 700, "b": 0})                      # Puffer genau LEAD_MAX_MS: nichts
        self.assertEqual(L.hold_delays({}, 2860), {})

    def test_the_case_from_the_test_box(self):
        d = {"hauptbild": 450, "usb": 2560, "pocket": 0}                    # Puffer 2860
        self.assertEqual(L.hold_delays(d, 2860), {"hauptbild": 1860, "usb": 2560, "pocket": 1860})              # ohne Angabe des Hauptbilds: alle 1000 ms
        self.assertEqual(L.hold_delays(d, 2860, "hauptbild"), {"hauptbild": 2260, "usb": 2560, "pocket": 1860})     # das ganzflächige Hauptbild: 600 ms
        self.assertEqual(L.hold_delays(d, 2860, "unbekannt"), {"hauptbild": 1860, "usb": 2560, "pocket": 1860})     # unbekannter Schlüssel ändert nichts

    def test_a_larger_delay_than_the_floor_is_never_lowered(self):
        self.assertEqual(L.hold_delays({"a": 3000, "b": 2000, "c": 100}, 3300), {"a": 3000, "b": 2300, "c": 2300})

    def test_invariants_for_many_combinations(self):
        import itertools
        vals = (0, 100, 300, 450, 700, 1000, 1500, 2000, 2560, 3000)
        for combo in itertools.product(vals, repeat=3):
            d = {"a": combo[0], "b": combo[1], "c": combo[2]}
            buf = max(L.BUFFER_MIN_MS, max(combo) + 300)                     # so rechnet buffer_ms
            out = L.hold_delays(d, buf)
            for k in d:
                self.assertGreaterEqual(out[k], d[k], (combo, k))             # nie weniger als eingestellt
                self.assertLessEqual(buf - out[k], max(L.LEAD_MAX_MS, buf - d[k]), (combo, k))   # kein Bild früher als LEAD_MAX_MS, es sei denn, so war es schon eingestellt
                if d[k] <= buf - L.LEAD_MAX_MS:
                    self.assertLessEqual(buf - out[k], L.LEAD_MAX_MS, (combo, k))
                self.assertLessEqual(out[k], buf - 300, (combo, k))           # nie später als Termin minus 300 ms: es kommt nie zu spät
                self.assertLessEqual(out[k], 3000, (combo, k))                # nie über die größte erlaubte Verzögerung

    def test_the_big_picture_never_gets_more_than_600_ms_lead_and_normal_setups_stay_untouched(self):
        for buf, d, big in ((600, {"a": 0, "b": 0, "c": 0}, "a"),                      # alles 0: der gemessene gesunde Fall der Testbox
                            (750, {"a": 450, "b": 0, "c": 0}, "a"),                    # Standard: Hauptbild mit 450 ms
                            (600, {"a": 0, "b": 0, "c": 0}, None), (750, {"a": 450, "b": 0, "c": 0}, None)):
            self.assertEqual(L.hold_delays(d, buf, big), d, (buf, d, big))             # nichts ändert sich
        # Ist das große Bild eine Kamera mit kleinerer Verzögerung als der Puffer erlaubt, bekommt sie die Untergrenze (Puffer - 600):
        self.assertEqual(L.hold_delays({"a": 0, "b": 450, "c": 0}, 750, "a"), {"a": 150, "b": 450, "c": 0})
        self.assertEqual(L.hold_delays({"a": 0, "b": 0}, 1000, "a"), {"a": 400, "b": 0})  # die kleinen Bilder erst ab Puffer über 1000

    def test_invariants_with_a_big_picture(self):
        import itertools
        vals = (0, 450, 1000, 2560, 3000)
        for combo in itertools.product(vals, repeat=3):
            d = {"a": combo[0], "b": combo[1], "c": combo[2]}
            buf = max(L.BUFFER_MIN_MS, max(combo) + 300)
            for big in ("a", "b", "c", None):
                out = L.hold_delays(d, buf, big)
                for k in d:
                    lim = L.LEAD_MAX_BIG_MS if k == big else L.LEAD_MAX_MS
                    self.assertGreaterEqual(out[k], d[k], (combo, big, k))
                    self.assertLessEqual(out[k], buf - 300, (combo, big, k))
                    if d[k] <= buf - lim:
                        self.assertLessEqual(buf - out[k], lim, (combo, big, k))

    @mock.patch.object(L, "BUFFER_MIN_MS", 600)        # diese Prüfungen betreffen die Rechnung mit dem alten Mindestpuffer (so bleiben die Fälle "passt nicht in den Puffer" erreichbar)
    def test_effective_delays_apply_the_floor_on_top_of_the_freeze(self):
        # Start mit Puffer 600 (alles 0). Danach Verzögerungen gespeichert, die 1050 bräuchten: eingefroren auf die Werte vom Start, davon wird die Haltezeit gebildet.
        tmp = tempfile.mkdtemp()
        f = Fakes({"main_delay_ms": 0, "pip_delay_ms": 0})
        logs = []
        c = L.LiveController(load_json=f.load, put_state_file=lambda *a: None, state_dir=tmp, safe_cfg=server.PipelineStore._safe_cfg,
                             view_values=server.PipelineStore.view_values, log=logs.append, clock=lambda: f.t, fetch=lambda: {}, stats=lambda: {}, control=f.control,
                             start_cfg=dict(CFG, main_delay_ms=0, pip_delay_ms=0))
        c.cfg = safe({"main_delay_ms": 450, "pip_delay_ms": 750})
        self.assertEqual(c.effective_delays(), {"dji-a": 0, "cam2": 0, "cam3": 0, "cam4": 0})        # eingefroren: die Werte vom Start
        self.assertTrue(c.delays_frozen)

    def test_without_a_start_cfg_the_floor_comes_from_the_current_buffer(self):
        tmp = tempfile.mkdtemp()
        f = Fakes({})
        c = f.controller(tmp)
        c.cfg = safe({"main_delay_ms": 450, "pip_delay_ms": 3000})          # 3000 wird auf 750 begrenzt: Puffer 1050, Untergrenze 50
        self.assertEqual(c.effective_delays(), {"dji-a": 450, "cam2": 750, "cam3": 450, "cam4": 450})       # alle mindestens 450: der Tausch des Hauptbilds ändert keine Haltezeit
        self.assertEqual(c.effective_delays("dji-a"), {"dji-a": 450, "cam2": 750, "cam3": 450, "cam4": 450})   # mit oder ohne Angabe des Hauptbilds dasselbe


@mock.patch.object(L, "BUFFER_MIN_MS", 600)        # diese Prüfungen betreffen die Rechnung mit dem alten Mindestpuffer (so bleiben die Fälle "passt nicht in den Puffer" erreichbar)
class FrozenDelays(unittest.TestCase):
    """Die laufende Sendekette übernimmt Verzögerungen nur, solange sie zum Puffer vom Start passen."""

    def make(self, start, later=None):
        self.tmp = tempfile.mkdtemp()
        self.f = Fakes(dict(start))
        self.logs = []
        c = L.LiveController(load_json=self.f.load, put_state_file=lambda *a: None, state_dir=self.tmp, safe_cfg=server.PipelineStore._safe_cfg,
                             view_values=server.PipelineStore.view_values, log=self.logs.append, clock=lambda: self.f.t,
                             fetch=lambda: dict(self.f.pub), stats=lambda: dict(self.f.states), control=self.f.control,
                             start_cfg=dict(CFG, **start))
        self.c = c
        c.start()
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}, "cam3": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 3, 3: 1}
        self.run_dir = mock.patch.object(L, "RUN", self.tmp)
        self.run_dir.start()
        self.addCleanup(self.run_dir.stop)
        return c

    def tick(self):
        self.f.t += 1.1
        self.c.tick()
        return self.f.take()

    @staticmethod
    def delays(lines):
        out = {}
        for l in lines:
            parts = l.split()
            if len(parts) == 4 and parts[0] == "set" and parts[1].endswith("_vq") and parts[2] == "min-threshold-time":
                out[parts[1][:4]] = int(parts[3]) // 1000000
        return out

    def test_matching_changes_are_applied_live(self):
        self.make({"main_delay_ms": 450})                                    # Puffer 750
        self.assertEqual(self.delays(self.tick()), {"sbf0": 483, "sbf1": 183, "sbf2": 183, "sbf3": 183})   # alle mit der Untergrenze des Hauptbilds (750 - 600)
        self.f.cfg.update(main_delay_ms=300, pip_delay_ms=200)               # braucht 600: passt
        self.assertEqual(self.delays(self.tick()), {"sbf0": 333, "sbf1": 233})
        self.assertFalse(self.c.delays_frozen)
        self.assertEqual(self.logs, [])

    def test_the_setup_of_the_test_box_is_now_capped_and_stays_in_the_tested_range(self):
        """Hauptbild 450, kleines Bild 1 mit 2560, die übrigen 0 (so stand es bei der Testbox): Die Engine begrenzt auf 750, der Puffer bleibt bei 1050 ms."""
        self.make({"main_delay_ms": 450, "pip_delay_ms": 2560})
        d = self.delays(self.tick())
        self.assertEqual(d, {"sbf0": 483, "sbf1": 783, "sbf2": 483, "sbf3": 483})   # Hauptbild 450 (Standard), kleines Bild 1 mit der Grenze 750, die übrigen mit derselben Untergrenze wie das Hauptbild (1050 - 600)
        self.assertEqual(self.c.running_buffer, 1050)
        for slot, hold in d.items():
            self.assertLessEqual(1050 - (hold - 33), 1000 if slot != "sbf0" else 600)

    def test_a_change_that_does_not_fit_the_buffer_is_not_applied(self):
        self.make({"main_delay_ms": 0, "pip_delay_ms": 0})                   # Puffer 600
        first = self.delays(self.tick())
        self.assertEqual(first, {"sbf0": 0, "sbf1": 0, "sbf2": 0, "sbf3": 0})
        self.f.cfg.update(main_delay_ms=450, pip_delay_ms=750)               # bräuchte 1050: mehr als 150 ms über dem Puffer
        self.assertEqual(self.delays(self.tick()), {})                       # nichts geändert: die Sendung bleibt im Takt
        self.assertTrue(self.c.delays_frozen)
        self.assertEqual(len([m for m in self.logs if "bleibt bis zum Neustart" in m]), 1)
        self.tick()
        self.tick()
        self.assertEqual(len([m for m in self.logs if "bleibt bis zum Neustart" in m]), 1)     # nur einmal gemeldet

    def test_frozen_delays_follow_again_when_they_fit(self):
        self.make({"main_delay_ms": 0, "pip_delay_ms": 0})
        self.tick()
        self.f.cfg.update(main_delay_ms=450, pip_delay_ms=750)
        self.tick()
        self.assertTrue(self.c.delays_frozen)
        self.f.cfg.update(main_delay_ms=450, pip_delay_ms=300)               # braucht 750: passt zu 600 (150 ms Toleranz)
        d = self.delays(self.tick())
        self.assertEqual(d, {"sbf0": 483, "sbf1": 333})
        self.assertFalse(self.c.delays_frozen)
        self.assertTrue(any("gilt jetzt" in m for m in self.logs))

    def test_a_larger_delay_than_the_buffer_allows_is_not_applied(self):
        self.make({"main_delay_ms": 0})                                       # Puffer 600
        self.tick()
        self.f.cfg.update(pip_delay_ms=2000)                                  # braucht 2300
        self.assertEqual(self.delays(self.tick()), {})
        self.assertTrue(self.c.delays_frozen)

    def test_swapping_main_and_small_picture_keeps_every_delay_with_its_camera(self):
        self.make({"main_delay_ms": 450, "pip_delay_ms": 0})
        self.delays(self.tick())
        self.f.cfg.update(main="cam2", pip="dji-a", main_delay_ms=0, pip_delay_ms=450)     # Tausch: die Verzögerung geht mit
        self.tick()
        self.assertFalse(self.c.delays_frozen)

    def test_camera_that_was_not_there_at_the_start_gets_at_most_what_the_buffer_allows(self):
        self.make({"main_delay_ms": 450, "pip_delay_ms": 0, "pip2": ""})      # Puffer 750, cam3 war nicht dabei
        self.tick()
        self.f.cfg.update(pip2="cam3", pip2_delay_ms=2000, main_delay_ms=450)  # neue Kamera mit 2000: braucht 2300, passt nicht
        d = self.delays(self.tick())
        self.assertTrue(self.c.delays_frozen)
        self.assertLessEqual(max(d.values() or [0]), (750 - 300 + 33))         # höchstens Puffer minus 300 ms (plus ein Bild)

    def test_without_start_cfg_nothing_is_ever_frozen(self):
        tmp = tempfile.mkdtemp()
        f = Fakes({"main_delay_ms": 450, "pip_delay_ms": 2560})
        c = f.controller(tmp)                                                  # wie in den älteren Tests: ohne start_cfg
        self.assertIsNone(c.running_buffer)
        c.start()
        c.cfg = safe({"main_delay_ms": 0, "pip_delay_ms": 0})
        self.assertEqual(c.effective_delays(), L.delays_by_key(c.cfg))
        self.assertFalse(c.delays_frozen)


class Fakes:
    """Alles, was der Regler von außen braucht: Einstellung, nginx, Statistik von belacoder, Steuerkanal."""

    def __init__(self, cfg=None):
        self.cfg = dict(CFG, **(cfg or {}))
        self.pub = {}
        self.states = {}
        self.sent = []
        self.ok = True
        self.t = 100.0
        self.hold = {}                       # Inhalt von feed-hold.json
        self.lags = {}                       # mittlerer Zeitversatz je Platz (in_f<N>_lag), ms
        self.wall = 1_000_000.0              # Unix-Zeit für die Ablaufzeiten darin

    def load(self, name):
        if name == L.HOLD_FILE:
            return dict(self.hold)
        return dict(self.cfg)

    def control(self, lines):
        if not self.ok:
            return False
        self.sent += lines
        return True

    def controller(self, tmp):
        c = L.LiveController(load_json=self.load, put_state_file=lambda *a: None, state_dir=tmp, safe_cfg=server.PipelineStore._safe_cfg,
                             view_values=server.PipelineStore.view_values, log=lambda m: None, clock=lambda: self.t,
                             fetch=lambda: dict(self.pub), stats=lambda: dict(self.states), control=self.control, wall=lambda: self.wall, lags=lambda: dict(self.lags))
        return c

    def take(self):
        out, self.sent = self.sent, []
        return out


class SwapOrder(unittest.TestCase):
    """Beim Tausch von Größen sieht der Mischer nie ein großes Bild in einem kleinen Feld (Testbox, 14. Oktober 2026: Das kostete beim Tausch des Hauptbilds 1 bis 2 s mit 5 Bildern/s)."""

    BIG = "set sbf%d_scc caps video/x-raw,format=NV12,width=1920,height=1080"
    SMALL = "set sbf%d_scc caps video/x-raw,format=NV12,width=480,height=270"

    def test_no_change_of_size_changes_nothing(self):
        lines = ["pad pipcomp sink_2 xpos 5", self.SMALL % 2, "pad pipcomp sink_2 width 480"]
        self.assertEqual(L.order_for_swap(list(lines), {2: self.SMALL % 2}, {}), lines)
        self.assertEqual(L.order_for_swap(list(lines), {2: self.SMALL % 2 + " "}, {}), lines)
        self.assertEqual(L.order_for_swap(list(lines), {}, {}), lines)                              # beim ersten Setzen keine alte Größe

    def test_a_growing_branch_gets_its_field_before_its_size(self):
        lines = [self.BIG % 3, "pad pipcomp sink_3 xpos 0", "pad pipcomp sink_3 width 1920", "pad pipcomp sink_3 zorder 1", "pad pipcomp sink_3 alpha 1.00"]
        out = L.order_for_swap(lines, {3: self.SMALL % 3}, {})
        self.assertEqual(out[-1], self.BIG % 3)
        self.assertEqual(out[:-1], lines[1:])
        self.assertNotIn(L.WAIT_MARK, out)

    def test_a_shrinking_branch_is_hidden_and_gets_its_field_after_the_pause(self):
        lines = [self.SMALL % 0, "pad pipcomp sink_0 xpos 720", "pad pipcomp sink_0 ypos 404", "pad pipcomp sink_0 width 480", "pad pipcomp sink_0 height 270",
                 "pad pipcomp sink_0 zorder 6", "pad pipcomp sink_0 alpha 0.85"]
        out = L.order_for_swap(lines, {0: self.BIG % 0}, {0: "pad pipcomp sink_0 alpha 0.85"})
        i = out.index(L.WAIT_MARK)
        self.assertEqual(out[:i], [self.SMALL % 0, "pad pipcomp sink_0 alpha 0"])                  # erst klein und unsichtbar
        self.assertEqual(out[i + 1:], lines[1:6] + ["pad pipcomp sink_0 alpha 0.85"])              # danach Feld, Lage und die eingestellte Deckkraft

    def test_the_final_alpha_is_always_restored_even_if_it_did_not_change(self):
        lines = [self.SMALL % 0, "pad pipcomp sink_0 width 480"]
        out = L.order_for_swap(lines, {0: self.BIG % 0}, {0: "pad pipcomp sink_0 alpha 1.00"})
        self.assertEqual(out[-1], "pad pipcomp sink_0 alpha 1.00")

    def test_a_real_swap_through_the_layout(self):
        lay = L.Layout()
        args = dict(c=safe(), slot_key=["dji-a", "cam2", "cam3", "cam4"], dims={}, audio_ok={0, 1}, hide=0, audio_pos=-1, mute=False, delay_ms={})
        lay.commands(roles={0: 0, 1: 1}, **args)
        out = lay.commands(roles={1: 0, 0: 1}, **args)
        i = out.index(L.WAIT_MARK)
        before, after = out[:i], out[i + 1:]
        self.assertIn("pad pipcomp sink_1 width 1920", before)
        self.assertLess(before.index("pad pipcomp sink_1 width 1920"), before.index(next(l for l in before if l.startswith("set sbf1_scc caps"))))   # wachsend: Feld vor Größe
        self.assertIn("pad pipcomp sink_0 alpha 0", before)                                          # schrumpfend: ausgeblendet
        self.assertTrue(any(l.startswith("pad pipcomp sink_0 width") for l in after))
        self.assertEqual(after[-1].split()[:4], ["pad", "pipcomp", "sink_0", "alpha"])
        self.assertEqual(lay.commands(roles={1: 0, 0: 1}, **args), [])                               # danach nichts mehr zu senden

    def test_sent_in_two_parts_with_a_pause(self):
        f = Fakes()
        c = f.controller(tempfile.mkdtemp())
        pauses = []
        c.sleep = pauses.append
        self.assertTrue(c.send_lines(["a", L.WAIT_MARK, "b", "c"]))
        self.assertEqual(f.sent, ["a", "b", "c"])                                                      # ohne die Trennzeile selbst, in der Reihenfolge
        self.assertEqual(pauses, [L.SWAP_SETTLE_S])
        pauses.clear()
        self.assertTrue(c.send_lines(["x", "y"]))
        self.assertEqual(pauses, [])                                                                  # ohne Trennzeile keine Pause

    def test_a_failed_part_reports_failure(self):
        f = Fakes()
        f.ok = False
        c = f.controller(tempfile.mkdtemp())
        c.sleep = lambda s: None
        self.assertFalse(c.send_lines(["a", L.WAIT_MARK, "b"]))

    def test_branch_queue_is_small_and_the_hold_is_the_same_for_every_branch(self):
        txt = L.build(safe())
        self.assertIn(f"queue name=sbf0_q max-size-buffers={L.BRANCH_QUEUE_BUFFERS} max-size-bytes=0 max-size-time=0 ! pipcomp.sink_0", txt)
        self.assertEqual(L.hold_delays({"a": 0, "b": 0}, 1050, "a", uniform=True), {"a": 450, "b": 450})
        self.assertEqual(L.hold_delays({"a": 0, "b": 0}, 1050, "a"), {"a": 450, "b": 50})              # ohne uniform wie bisher


class BelacoderLabel(unittest.TestCase):
    """Die Zahl hinter "-sb" in der Kennung des gebauten belacoder steuert die Engine (engine_level): ab COPY_LEVEL gelten Puffer 1350 ms und 1000 ms Vorlauf für das Hauptbild.
    Der Kopier-Patch kopiert aber nur vergrößerte Bilder; mit "-sb13" fielen beim Tausch des Hauptbilds die Bilder auf 18,8/s (Testbox, 15. Oktober 2026)."""

    def label(self):
        sh = open(os.path.join(os.path.dirname(HERE), "belacoder", "build.sh"), encoding="utf-8").read()
        m = re.search(r'-DVERSION=\\"\$\{COMMIT%\$\{COMMIT#\?+\}\}(-[^"\\]+)\\"', sh)
        self.assertTrue(m, "Kennung in build.sh nicht gefunden")
        return "ccce9ca" + m.group(1)

    def test_the_label_of_the_build_stays_below_the_copy_level(self):
        label = self.label()
        self.assertEqual(label, "ccce9ca-jt2-sb12-eos")
        level = int(re.search(r"-sb(\d+)", label).group(1))
        self.assertLess(level, L.COPY_LEVEL)

    def test_install_rebuilds_belacoder_when_only_the_build_script_changes(self):
        """1.0.39 änderte nur build.sh (Kennung): install.sh baute nicht neu, weil es nur die Patch-Dateien verglich, und die Boxen blieben auf "-sb13"."""
        sh = open(os.path.join(os.path.dirname(HERE), "install", "install.sh"), encoding="utf-8").read()
        self.assertIn('cmp -s "$HERE/belacoder/build.sh" /opt/pipbox/belacoder/build.sh || bc_changed=1', sh)
        self.assertIn('install -m 644 "$HERE/belacoder/build.sh" /opt/pipbox/belacoder/build.sh', sh)
        self.assertLess(sh.index('cmp -s "$HERE/belacoder/build.sh"'), sh.index('if [ "$bc_changed" = 1 ]'))

    def test_the_engine_reads_twelve_from_that_label(self):
        label = self.label()
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(L.os, "stat", return_value=mock.Mock(st_mtime=123.0)), \
                mock.patch.object(L.subprocess, "run", return_value=mock.Mock(stdout=label + "\n")), mock.patch.dict(L._ENG, {"mtime": None, "level": 0}):
            os.environ.pop("PIPBOX_ENGINE", None)
            self.assertEqual(L.engine_level(), 12)
            c = safe({"main_delay_ms": 0, "pip_delay_ms": 0, "pip2_delay_ms": 0, "pip3_delay_ms": 0})
            self.assertEqual(L.buffer_ms(c), L.BUFFER_MIN_MS)                                          # 1050 ms, nicht 1350


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = Fakes()
        self.c = self.f.controller(self.tmp)
        self.c.start()
        self.run_dir = mock.patch.object(L, "RUN", self.tmp)
        self.run_dir.start()

    def tearDown(self):
        self.run_dir.stop()

    def tick(self, dt=1.1):
        self.f.t += dt
        return self.c.tick()

    def test_starts_empty_and_lays_cameras_on_when_they_come(self):
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        st = self.tick()
        self.assertEqual(st["alive"], [])
        self.assertFalse(any(l.startswith("feed ") for l in self.f.take()))      # niemand sendet: nichts zu starten
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}}
        self.tick()
        self.assertIn("feed sbf0 on", self.f.take())
        self.f.states[0] = 2                                              # läuft, noch kein Bild: noch nicht sichtbar
        self.tick()
        self.assertFalse(any("sink_0 alpha 1" in l for l in self.f.take()))
        self.f.states[0] = 3
        st = self.tick()
        self.assertEqual(st["alive"], ["dji-a"])
        self.assertIn("pad pipcomp sink_0 alpha 1.00", self.f.take())

    # ---- Größe vor dem Start des Zweigs (Testbox, 14. Oktober 2026): Setzte der Regler die Größe erst, wenn schon Bilder flossen (scc caps im laufenden Betrieb), stand die ganze Kette beim Anlauf
    # 3 bis 9 s still (Encoder 4 bis 5 Bilder je Sekunde, dann Nachholen mit 70 bis 250), die Bitrate stürzte bis auf 300 kbit/s. Mit der Größe vor dem Start: Bildfluss nach 2 s, Bitrate nie unter 9 Mbit/s.

    def three_cameras(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}, "cam3": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}

    def slot_of(self, key):
        return list(self.c.binder.slot_key).index(key)

    def test_the_size_is_set_before_the_branch_is_started(self):
        self.three_cameras()
        self.tick()
        lines = self.f.take()
        first_on = min(i for i, l in enumerate(lines) if l.startswith("feed sbf"))
        for key, pos in (("dji-a", 0), ("cam2", 1), ("cam3", 2)):
            s = self.slot_of(key)
            caps = [i for i, l in enumerate(lines) if l.startswith(f"set sbf{s}_scc caps")]
            self.assertTrue(caps, key)
            self.assertLess(max(caps), first_on, key)                         # vor dem ersten "feed ... on"
            if pos == 0:
                self.assertIn("width=1920,height=1080", lines[caps[-1]])
            else:
                g = L.small_geometry(safe(), pos)
                self.assertIn(f"width={g['w']},height={g['h']}", lines[caps[-1]])
            self.assertTrue(any(l.startswith(f"set sbf{s}_crop left") for l in lines[:first_on]), key)

    def test_the_same_size_is_not_sent_again_when_the_picture_arrives(self):
        self.three_cameras()
        self.tick()
        self.f.take()
        self.f.states = {0: 3, 1: 3, 2: 3, 3: 1}
        self.tick()
        lines = self.f.take()
        self.assertFalse([l for l in lines if "_scc caps" in l or "_crop " in l], lines)      # nichts doppelt: die Größe stimmte schon
        self.assertIn(f"pad pipcomp sink_{self.slot_of('dji-a')} alpha 1.00", lines)          # sichtbar wird es trotzdem erst jetzt

    def test_only_stopped_or_waiting_branches_get_the_early_size(self):
        self.three_cameras()
        self.f.states = {0: 3, 1: 1, 2: 2, 3: 1}
        self.tick()
        lines = self.f.take()
        slots_with_early = {int(l.split("_")[0][len("set sbf"):]) for l in lines if "_scc caps" in l}
        self.assertEqual(slots_with_early, {0, 1, 2})                          # Platz 0 läuft schon (Zustand 3): sein Bild bekommt die Größe wie bisher mit der Rolle
        self.f.take()

    def test_a_camera_that_does_not_send_gets_no_early_size(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        lines = self.f.take()
        self.assertEqual({l.split("_")[0] for l in lines if "_scc caps" in l}, {f"set sbf{self.slot_of('dji-a')}"})

    def test_a_held_camera_gets_no_early_size(self):
        self.three_cameras()
        self.f.hold = {"keys": {"cam2": self.f.wall + 30}}
        self.tick()
        lines = self.f.take()
        self.assertFalse([l for l in lines if l.startswith(f"set sbf{self.slot_of('cam2')}_scc")], lines)

    def test_a_stand_in_for_the_missing_main_gets_the_big_size_when_its_picture_arrives(self):
        """Das eingestellte Hauptbild sendet nicht: eine kleine Kamera springt ein und wird groß (wie bisher, dabei ändert sich die Größe im laufenden Betrieb)."""
        self.f.pub = {"cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        lines = self.f.take()
        s = self.slot_of("cam2")
        g = L.small_geometry(safe(), 1)
        self.assertIn(f"set sbf{s}_scc caps video/x-raw,format=NV12,width={g['w']},height={g['h']}", lines)      # vorab: kleine Größe nach der eingestellten Stelle
        self.f.states[s] = 3
        self.tick()
        self.assertIn(f"set sbf{s}_scc caps video/x-raw,format=NV12,width=1920,height=1080", self.f.take())      # dann als Hauptbild groß

    def test_the_big_picture_keeps_the_aspect_ratio_of_its_source_in_the_early_size(self):
        self.f.pub = {"dji-a": {"w": 1080, "h": 1920, "audio": True}}
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        caps = [l for l in self.f.take() if "_scc caps" in l][-1]
        w = int(caps.split("width=")[1].split(",")[0])
        self.assertLess(w, 1920)                                                 # schmaler als das Ausgabebild, wie fit_big es für ein hochkantes Bild macht

    def test_a_branch_that_stops_and_starts_again_gets_the_size_again_only_if_it_changed(self):
        self.three_cameras()
        self.tick()
        self.f.take()
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}                                 # Zweige wieder gestoppt (zum Beispiel Verzugs-Schutz)
        self.tick(4.0)
        self.assertFalse([l for l in self.f.take() if "_scc caps" in l])         # dieselbe Größe: keine neue Anweisung

    def test_small_picture_appears_without_touching_the_big_one(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.f.take()
        self.f.states[1] = 3
        self.tick()
        lines = self.f.take()
        self.assertFalse(any(l.startswith("pad pipcomp sink_0") for l in lines))   # das große Bild bleibt, wie es ist
        self.assertTrue(any(l.startswith("pad pipcomp sink_1 alpha 1.00") for l in lines))

    def test_portrait_small_picture_keeps_its_aspect_ratio(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1080, "h": 1920, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick()
        lines = self.f.take()
        w = [int(l.split()[-1]) for l in lines if l.startswith("pad pipcomp sink_1 width")][-1]
        h = [int(l.split()[-1]) for l in lines if l.startswith("pad pipcomp sink_1 height")][-1]
        self.assertLess(w, h)                                                   # Rahmen hochkant
        self.assertLessEqual(abs(w * 1920 - h * 1080), 2 * 1920)               # Seitenverhaeltnis der Quelle (bis auf Rundung auf gerade Zahlen)
        caps = [l for l in lines if l.startswith("set sbf1_scc caps")][-1]
        self.assertIn(f"width={w},height={h}", caps)
        self.assertIn("set sbf1_crop left 0", lines)                          # kein Beschnitt

    def test_feed_commands_wait_for_the_cooldown(self):
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.f.pub = {"dji-a": {"w": 0, "h": 0, "audio": False}}
        self.tick()
        self.assertEqual([l for l in self.f.take() if l.startswith("feed")], ["feed sbf0 on"])
        self.tick(0.6)
        self.assertEqual([l for l in self.f.take() if l.startswith("feed")], [])      # noch Abkühlzeit
        self.tick(3.0)
        self.assertEqual([l for l in self.f.take() if l.startswith("feed")], ["feed sbf0 on"])

    def test_camera_gone_stops_the_branch(self):
        self.f.pub = {"dji-a": {"w": 0, "h": 0, "audio": False}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.f.take()
        self.f.pub = {}
        self.tick(4.0)
        lines = self.f.take()
        self.assertIn("feed sbf0 off", lines)
        self.assertIn("pad pipcomp sink_0 alpha 0", lines)

    def test_held_camera_branch_is_stopped_while_it_still_publishes_and_comes_back_afterwards(self):
        """Der Kamera-Dienst bittet vor dem Beenden des Streams (Wechsel des Netzes, Trennen), den Zweig zu stoppen (feed-hold.json)."""
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        st = self.tick()
        self.assertEqual(st["alive"], ["cam2", "dji-a"])
        self.f.take()
        self.f.hold = {"keys": {"cam2": self.f.wall + 45}}                # cam2 sendet noch, soll aber aus der Sendekette
        st = self.tick(4.0)
        lines = self.f.take()
        self.assertIn("feed sbf1 off", lines)
        self.assertNotIn("feed sbf0 off", lines)                         # die anderen Zweige bleiben unberührt
        self.f.states[1] = 1
        st = self.tick(4.0)
        self.assertNotIn("cam2", self.c.alive)                           # nicht mehr im Stream, auch wenn nginx die Kamera noch zeigt
        self.assertIn("cam2", st["alive"])                               # die Anzeige (Knopf in der Fußleiste) behält sie
        self.assertIn("cam2", st["slots"])                               # der Platz bleibt
        self.assertFalse(any(l == "feed sbf1 on" for l in self.f.take()))      # solange der Eintrag gilt, startet er nicht
        self.f.hold = {}                                                 # der Dienst gibt frei (Kamera sendet neu)
        self.tick(4.0)
        self.assertIn("feed sbf1 on", self.f.take())

    def lag_setup(self, two=False):
        pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.pub = dict(pub)
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.f.lags = {0: -100, 1: -100}
        for _ in range(int(L.LAG_GRACE_S) + 3):                           # Zweige laufen schon länger (keine Schonzeit mehr)
            self.tick(1.0)
        self.f.take()

    def test_a_feed_that_stays_too_late_is_taken_out_and_comes_back(self):
        """Verzugs-Schutz (12. Oktober 2026): über 1,5 s zu spät, länger als 6 s -> Zweig aus der Sendekette, nach 15 s wieder dazu."""
        self.lag_setup()
        self.f.lags[1] = 3000
        for _ in range(4):
            self.tick(1.0)
        self.assertFalse(any(l.startswith("feed sbf1") for l in self.f.take()))          # noch nicht lange genug
        for _ in range(4):
            self.tick(1.0)
        lines = self.f.take()
        self.assertIn("feed sbf1 off", lines)
        self.assertNotIn("feed sbf0 off", lines)                                         # die anderen Zweige bleiben unberührt
        self.f.states[1] = 1
        self.f.lags[1] = -100
        for _ in range(10):
            self.tick(1.0)
        self.assertNotIn("feed sbf1 on", self.f.take())                                   # solange er draußen sein soll
        for _ in range(8):
            self.tick(1.0)
        self.assertIn("feed sbf1 on", self.f.take())                                      # danach wieder

    def test_a_feed_that_stays_too_early_is_taken_out_too(self):
        self.lag_setup()
        self.f.lags[1] = -4000                                                            # Ausrichtung auf einen alten ersten Frame
        for _ in range(8):
            self.tick(1.0)
        self.assertIn("feed sbf1 off", self.f.take())

    def test_normal_and_short_deviations_are_left_alone(self):
        self.lag_setup()
        for v in (-742, -100, 108, 1400, -2400):                                          # gesunde Werte aus den Messungen und knapp innerhalb der Grenzen
            self.f.lags[1] = v
            for _ in range(10):
                self.tick(1.0)
            self.assertFalse(any(l.startswith("feed sbf") for l in self.f.take()), v)
        self.f.lags[1] = 5000                                                             # kurzer Ausreißer
        for _ in range(4):
            self.tick(1.0)
        self.f.lags[1] = -100
        for _ in range(10):
            self.tick(1.0)
        self.assertFalse(any(l.startswith("feed sbf") for l in self.f.take()))

    def test_read_lags_takes_the_mean_of_every_branch_from_the_statistics_line(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "live.txt")
        with open(path, "w") as f:
            f.write("t=1 br=1 in_f0=29.8/0/50 in_f0_lag=-122/-13/70 in_f1=3.0/0/9 in_f1_lag=-2198/-1364/-130 dec_f3_lag=-4796/-4769/-4738 in_f3_lag=300/4510/9000 feed_sbf0=3\nwarn x\n")
        self.assertEqual(L.read_lags(path, now=os.stat(path).st_mtime), {0: -13, 1: -1364, 3: 4510})     # nur in_f<N>_lag, nicht dec_ oder dmx_
        self.assertEqual(L.read_lags(os.path.join(d, "fehlt.txt")), {})
        self.assertEqual(L.read_lags(path, now=os.stat(path).st_mtime + 3600), {})                        # alte Datei: leer

    def test_a_branch_that_just_started_gets_a_grace_time(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick(5)
        self.f.states[0] = 3
        self.f.lags = {0: 4000}                                                           # direkt nach dem Start ist der Versatz noch nicht eingeschwungen
        for _ in range(int(L.LAG_GRACE_S) + 2):
            self.tick(1.0)
        self.assertFalse(any(l == "feed sbf0 off" for l in self.f.take()))

    def test_the_guard_gives_up_after_three_tries_in_three_minutes(self):
        self.lag_setup()
        offs = 0
        for _ in range(4):
            self.f.states[1] = 3
            self.f.lags[1] = 3000
            self.c.up_at.clear()
            for _ in range(int(L.LAG_GRACE_S) + int(L.LAG_SUSTAIN_S) + 3):
                self.tick(1.0)
            offs += sum(1 for l in self.f.take() if l == "feed sbf1 off")
            self.f.states[1] = 1
            for _ in range(int(L.LAG_HOLD_S) + 4):
                self.tick(1.0)
            self.f.take()
        self.assertEqual(offs, 3)

    def test_expired_or_broken_hold_entries_are_ignored(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        for bad in ({"keys": {"dji-a": self.f.wall - 1}}, {"keys": {"dji-a": "x"}}, {"keys": {"dji-a": True}}, {"keys": [1]}, {"keys": None}, {"x": 1}, {}):
            self.f.hold = bad
            self.f.states[0] = 1
            self.c.cmd_at.clear()
            self.tick(4.0)
            self.assertIn("feed sbf0 on", self.f.take(), bad)

    def test_held_main_camera_hands_the_big_picture_to_the_next_one(self):
        pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.pub = dict(pub)
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick()
        self.assertEqual(self.c.main_key, "dji-a")
        self.f.hold = {"keys": {"dji-a": self.f.wall + 45}}
        self.tick(4.0)
        self.assertEqual(self.c.main_key, "cam2")                        # wie bei einem Ausfall, ohne Unterbrechung des Ausgangs
        self.f.hold = {}
        self.f.states[0] = 3
        self.tick(1.0)
        self.tick(4.0)
        self.assertEqual(self.tick(1.0)["main"], "dji-a")                  # nach der Wartezeit (BACK_S) wieder die eigentliche Hauptkamera

    def test_main_camera_gone_the_next_one_becomes_big_and_comes_back(self):
        pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.pub = dict(pub)
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        self.f.pub = {"cam2": pub["cam2"]}
        self.f.states[0] = 1
        st = self.tick(4.0)
        self.assertEqual(st["main"], "cam2")
        lines = self.f.take()
        self.assertIn("pad pipcomp sink_1 width 1920", lines)             # cam2 jetzt groß
        self.f.pub = dict(pub)
        self.f.states[0] = 3
        self.tick(1.0)
        self.assertEqual(self.tick(1.0)["main"], "cam2")                  # erst nach BACK_S (3 s) stabil wieder die eigentliche Hauptkamera
        self.tick(3.5)
        self.assertEqual(self.tick(1.0)["main"], "dji-a")

    def test_swap_in_the_settings_changes_the_layout_not_the_slots(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        self.f.cfg.update(main="cam2", pip="dji-a")
        self.tick()
        lines = self.f.take()
        self.assertEqual(self.c.binder.slot_key[:2], ["dji-a", "cam2"])    # Zweige bleiben, wo sie sind
        self.assertFalse(any(l.startswith("feed ") or "location" in l for l in lines))
        self.assertIn("pad pipcomp sink_1 width 1920", lines)

    def test_new_camera_in_a_free_place_needs_no_restart(self):
        self.f.cfg.update(pip3="")
        c = self.f.controller(self.tmp)
        c.start()
        self.f.states = {0: 3, 1: 3, 2: 3, 3: 1}
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam4": {"w": 1920, "h": 1080, "audio": True}}
        self.f.t += 20
        c.tick()
        self.f.take()
        self.f.cfg.update(pip3="cam4")
        self.f.t += 1
        c.tick()
        lines = self.f.take()
        # Zweig stoppen, Adresse setzen, Zweig starten: im selben Durchgang, in dieser Reihenfolge (belacoder arbeitet die Zeilen nacheinander ab)
        self.assertEqual(lines[:2], ["feed sbf3 off", "set sbf3_src location rtmp://127.0.0.1:1935/publish/cam4"])
        on = lines.index("feed sbf3 on")
        self.assertTrue(all(l.startswith("set sbf3_crop ") or l.startswith("set sbf3_scc caps ") for l in lines[2:on]), lines[:on + 1])      # dazwischen nur Größe und Beschnitt, noch vor dem Start des Zweigs

    def test_other_key_in_a_place_is_set_while_the_branch_is_stopped(self):
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.f.take()
        self.f.cfg.update(pip="cam9")
        self.tick()
        lines = self.f.take()
        self.assertEqual(lines[:2], ["feed sbf1 off", "set sbf1_src location rtmp://127.0.0.1:1935/publish/cam9"])

    def test_commands_are_repeated_until_belacoder_listens(self):
        self.f.ok = False
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.assertEqual(self.f.sent, [])
        self.f.ok = True
        self.tick()
        self.assertTrue(any(l.startswith("pad pipcomp") for l in self.f.take()))      # alles kommt nach, nichts ging verloren

    def test_after_a_belacoder_restart_everything_is_sent_again(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        self.tick()
        self.assertEqual(self.f.take(), [])                                   # alles gesendet, nichts mehr zu tun
        self.c.on_restart()                                                    # der neue belacoder beginnt wieder bei null
        self.f.states = {0: 1, 1: 1, 2: 1, 3: 1}
        self.tick()
        lines = self.f.take()
        self.assertIn("set sbf0_src location rtmp://127.0.0.1:1935/publish/dji-a", lines)
        self.assertIn("feed sbf0 on", lines)
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.tick()
        self.assertIn("pad pipcomp sink_0 alpha 1.00", self.f.take())         # das Bild kommt wieder

    def test_deactivated_camera_is_not_in_the_stream(self):
        self.f.cfg.update(inactive=["cam2"])
        c = self.f.controller(self.tmp)
        c.start()
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 1, 2: 1, 3: 1}
        self.f.t += 20
        c.tick()
        lines = self.f.take()
        self.assertNotIn("feed sbf1 on", lines)                              # der Zweig der deaktivierten Kamera wird nicht gestartet
        self.f.states[1] = 3                                                 # lief er trotzdem (eben deaktiviert): nie im Bild
        self.f.t += 20
        c.tick()
        self.assertNotIn("cam2", c.alive)
        st = c.tick()
        self.assertIn("cam2", st["alive"])                                   # der Knopf in der Fußleiste bleibt (Anzeige), im Bild ist sie nicht
        self.assertNotIn("cam2", c.alive)
        self.assertIn("feed sbf1 off", self.f.take())

    def test_view_file_hides_pictures_and_sets_mute_and_confirms(self):
        self.f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        self.f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        self.tick(20)
        self.f.take()
        with open(os.path.join(self.tmp, "main-view"), "w") as f:
            f.write("1 0 1\n")                                              # Stelle 1 ausgeblendet, Ton von Stelle 1, stumm
        self.tick()
        lines = self.f.take()
        self.assertIn("pad pipcomp sink_1 alpha 0.00", lines)
        self.assertIn("select asel sink_2", lines)
        self.assertIn("set avol mute true", lines)
        with open(os.path.join(self.tmp, "view-state")) as f:
            self.assertEqual(f.read().split(), ["1", "0", "1"])


class FacecamAudio(unittest.TestCase):
    """Mit der Tonquelle auf einem kleinen Bild fällt der Ton nicht mehr in Stille, wenn die Kamera dieses Bildes nicht im Bild ist."""

    def test_sound_falls_back_to_the_main_picture_when_the_small_picture_camera_is_missing(self):
        lay = L.Layout()
        c = safe()
        args = dict(c=c, slot_key=["dji-a", "cam2", "cam3", "cam4"], dims={}, audio_ok={0, 1}, hide=0, mute=False, delay_ms={})
        lines = lay.commands(roles={0: 0}, audio_pos=0, **args)                  # Ton auf kleinem Bild 1, aber dort ist keine Kamera (ausgefallen)
        self.assertIn("select asel sink_1", lines)                               # Ton des Hauptbildes (Platz 0) statt Stille

    def test_sound_stays_on_the_small_picture_when_its_camera_is_there(self):
        lay = L.Layout()
        args = dict(c=safe(), slot_key=["dji-a", "cam2", "cam3", "cam4"], dims={}, audio_ok={0, 1}, hide=0, mute=False, delay_ms={})
        lines = lay.commands(roles={0: 0, 1: 1}, audio_pos=0, **args)
        self.assertIn("select asel sink_2", lines)


class InactiveAudio(unittest.TestCase):
    def test_deactivated_camera_is_never_the_audio_source(self):
        tmp = tempfile.mkdtemp()
        f = Fakes({"inactive": ["cam2"]})
        c = f.controller(tmp)
        c.start()
        with open(os.path.join(tmp, "main-view"), "w") as fh:
            fh.write("0 0 0"+chr(10))                                          # Ton von Stelle 1 (cam2), die deaktiviert ist
        f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        f.t += 20
        with mock.patch.object(L, "RUN", tmp):
            c.tick()
        self.assertIn("select asel sink_1", f.take())                    # Ton des Hauptbildes, nicht sink_2 (cam2)

    def test_deactivating_the_audio_camera_while_live_moves_the_sound(self):
        tmp = tempfile.mkdtemp()
        f = Fakes()
        c = f.controller(tmp)
        c.start()
        with open(os.path.join(tmp, "main-view"), "w") as fh:
            fh.write("0 0 0" + chr(10))                                       # Ton von Stelle 1 (cam2)
        f.pub = {"dji-a": {"w": 1920, "h": 1080, "audio": True}, "cam2": {"w": 1920, "h": 1080, "audio": True}}
        f.states = {0: 3, 1: 3, 2: 1, 3: 1}
        f.t += 20
        with mock.patch.object(L, "RUN", tmp):
            c.tick()
            self.assertIn("select asel sink_2", f.take())                     # Ton liegt auf cam2
            f.cfg = dict(f.cfg, inactive=["cam2"])                            # cam2 wird deaktiviert (Fußleiste, lang drücken)
            f.t += 2
            c.tick()
            lines = f.take()
            self.assertIn("select asel sink_1", lines)                        # der Ton wechselt wirklich zum Hauptbild
            self.assertIn("feed sbf1 off", lines)
            f.states[1] = 1
            f.t += 5
            c.tick()
            self.assertEqual([x for x in f.take() if x.startswith("select asel")], [])
            with open(os.path.join(tmp, "view-state")) as fh:
                self.assertEqual(fh.read().split()[1], "-1")                  # die Rückmeldung nennt das Hauptbild als Tonquelle

    def test_server_refuses_it(self):
        st = server.PipelineStore(os.devnull)
        st.cfg = dict(server.PipelineStore.DEFAULT, **CFG, inactive=["cam2"])
        with self.assertRaises(ValueError):
            st.set_view(audio="pip")
        with mock.patch.object(st, "save"):
            st.set_view(audio="pip2")                                     # eine aktive Kamera geht


class ServerIntegration(unittest.TestCase):
    def test_always_plan_is_the_live_engine_and_build_gives_its_text(self):
        plan = server.PipelineStore.always_plan(CFG)
        self.assertTrue(plan and plan["always"] and plan["live"])
        self.assertIn("name=sbf0_src", server.PipelineStore(os.devnull).build(dict(server.PipelineStore.DEFAULT, **CFG)))
        self.assertIsNone(server.PipelineStore.always_blocker(CFG))

    def test_everything_is_live_in_this_engine(self):
        a, b = dict(CFG), dict(CFG, size_pct=50, corner=1, pip="cam9", main_delay_ms=900)
        self.assertTrue(server.always_compatible(a, b))
        self.assertFalse(server.always_compatible(a, dict(b, always_ready=False)))

    def test_without_the_switch_the_old_modes_are_untouched(self):
        cfg = dict(CFG, always_ready=False)
        text = server.PipelineStore(os.devnull).build(dict(server.PipelineStore.DEFAULT, **cfg))
        self.assertNotIn("sbf0_src", text)

    def test_without_the_new_belacoder_the_old_engine_is_used(self):
        with mock.patch.dict(os.environ, {"PIPBOX_LIVE": "0"}):
            self.assertFalse(L.enabled(safe()))


class SendControl(unittest.TestCase):
    def test_stats_and_rtmp_parsing(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("t=1 br=1 feed_sbf0=3 feed_sbf2=1\nwarn x\n")
        self.assertEqual(L.read_stats(f.name), {0: 3, 2: 1})
        os.utime(f.name, (1, 1))
        self.assertEqual(L.read_stats(f.name), {})                       # alte Datei: belacoder läuft nicht
        os.unlink(f.name)
        xml = ("<stream><name>a</name><publishing/><video><width>1920</width><height>1080</height></video><audio><codec>AAC</codec></audio></stream>"
               "<stream><name>b</name></stream>")
        self.assertEqual(L.parse_rtmp_stats(xml), {"a": {"w": 1920, "h": 1080, "audio": True}})

    @unittest.skipUnless(hasattr(os, "O_NONBLOCK"), "nur Linux")
    def test_control_without_belacoder_fails_at_once(self):
        self.assertFalse(L.send_control(["feed sbf0 on"], "/nonexistent/fifo"))
        self.assertTrue(L.send_control([]))


if __name__ == "__main__":
    unittest.main()
