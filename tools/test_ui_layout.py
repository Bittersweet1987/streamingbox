"""Tests für die gemeinsame Menüeinstellung (Optionen): Reihenfolge und Ausblendung liegen auf der Box und gelten für alle Geräte."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

SRC = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = server.UiLayout(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_without_file_nothing_is_set(self):
        self.assertEqual(self.store.get(), {"set": False, "order": [], "hidden": []})

    def test_roundtrip_and_duplicates(self):
        self.store.set(["c_chat", "netcard", "c_chat"], ["rcard", "statdet", "rcard"])
        self.assertEqual(self.store.get(), {"set": True, "order": ["c_chat", "netcard"], "hidden": ["rcard", "statdet"]})
        self.assertEqual(os.listdir(self.dir.name), ["ui-layout.json"])                       # keine liegengebliebene Zwischendatei

    def test_empty_lists_are_a_valid_state(self):
        self.store.set([], [])
        self.assertEqual(self.store.get(), {"set": True, "order": [], "hidden": []})

    def test_bad_input_is_refused_and_changes_nothing(self):
        self.store.set(["a"], [])
        for order, hidden in ((None, []), ("a", []), ([1], []), (["../x"], []), (["a b"], []), (["x" * 41], []), ([], {"a": 1}),
                              ([str(i) for i in range(server.UiLayout.MAX + 1)], [])):
            with self.assertRaises(ValueError, msg=repr((order, hidden))):
                self.store.set(order, hidden)
        self.assertEqual(self.store.get()["order"], ["a"])

    def test_damaged_file_means_not_set(self):
        for text in ("{", "[]", '{"order": 5}', '{"order": ["../x"]}'):
            with open(self.store.path, "w", encoding="utf-8") as f:
                f.write(text)
            self.assertFalse(self.store.get()["set"], text)

    def test_file_has_no_secrets_and_is_not_world_readable(self):
        self.store.set(["a"], [])
        if os.name == "posix":
            self.assertEqual(os.stat(self.store.path).st_mode & 0o007, 0)
        self.assertEqual(set(json.load(open(self.store.path, encoding="utf-8"))), {"order", "hidden"})


class Wiring(unittest.TestCase):
    def test_routes_and_start(self):
        self.assertIn('if path == "/api/layout":', SRC)
        self.assertEqual(SRC.count('if path == "/api/layout":'), 2)                           # lesen und schreiben (beides hinter der Anmeldung)
        self.assertIn("Handler.layout = UiLayout(args.state)", SRC)
        self.assertIn("    layout = None\n", SRC)                                              # ohne Speicher keine Ausnahme beim Lesen

    def test_page_pushes_and_pulls(self):
        block = PAGE[PAGE.index("// ---- Hauptmenüs: Reihenfolge"):PAGE.index("// ---- Karten zuklappen")]
        self.assertIn('srtlaCall("POST","/api/layout"', block)
        self.assertIn('srtlaCall("GET","/api/layout")', block)
        self.assertIn('visibilitychange', block)                                              # beim Zurückkehren zur Seite neu lesen
        self.assertIn("else if(st.order.length||st.hidden.length) push();", block)            # die Box ist leer: dieses Gerät liefert seinen Stand
        self.assertIn("function save(){", block.replace("const save=()=>{", "function save(){"))


if __name__ == "__main__":
    unittest.main()
