#!/usr/bin/env python3
"""Sammelt die deutschen Texte der Oberfläche (Issue #24): aus der Seite (Text und Attribute), den Skripten der Seite und den Meldungen von Server und Helfern.
Eingefügte Werte (f-Strings, %s, {}, ${...}) werden zu {1}, {2} ... Die Schlüssel der Übersetzungsdateien sind diese deutschen Texte.
  python3 tools/i18n_extract.py --count          Zahlen je Quelle
  python3 tools/i18n_extract.py                  alle Texte als JSON {"Quelle": [Text, ...]}
  python3 tools/i18n_extract.py --keys           ein sortiertes JSON-Feld aller verschiedenen Texte
  python3 tools/i18n_extract.py --skeleton xx    Gerüst für eine neue Sprache (web/i18n/xx.json) mit allen Schlüsseln und leeren Werten"""
import ast
import html.parser
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_TAGS = {"script", "style", "pre", "code", "svg"}
ATTRS = ("title", "placeholder", "aria-label", "alt")
LETTERS = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
PH = re.compile(r"\{\d+\}")
CODEISH = re.compile(r"""^([a-z0-9_.:/#@*>\[\]=~+,-]+|[a-z][a-z0-9_-]*( -{1,2}[a-z0-9=_.-]+)*|[A-Z_0-9]+)$""")
COMMANDISH = re.compile(r"^(sudo |systemctl |nmcli |journalctl |apt|dpkg |rfkill |bluetoothctl |udevadm |hciconfig |gst-|ffmpeg |curl )")
REGEXISH = re.compile(r"(\\[dwsb.]|\(\?|\^|\$$|\[[^\]]+\][+*?]|\{\d+,?\d*\})")
CSSISH = re.compile(r"^[#.]?[\w-]+\s*\{|^[#.]?[\w-]+:(?![\s]|$)|^\s*(display|color|margin|padding|border|width|height|font)\b|;\s*[\w-]+:")


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def number_placeholders(s, marker="\x00"):
    n = [0]

    def sub(_):
        n[0] += 1
        return "{%d}" % n[0]
    return re.sub(marker, sub, s)


def py_format_to_ph(s):
    """%s, %d, %.1f, %(x)s und {}, {0}, {name} in {1}, {2} ... umschreiben (nur für Quelltexte von Python)."""
    n = [0]

    def sub(_):
        n[0] += 1
        return "{%d}" % n[0]
    s = re.sub(r"%(?:\([A-Za-z_]+\))?[-+ 0#]*\d*(?:\.\d+)?[sdifrx]|\{[A-Za-z_][A-Za-z_0-9.]*(?:![rs])?(?::[^{}]*)?\}|\{\d*(?::[^{}]*)?\}", sub, s)
    return s.replace("%%", "%")


def renumber(key, value=None):
    """Platzhalter eines Schlüssels nach ihrer Reihenfolge neu nummerieren ({3}, {5} -> {1}, {2}); dieselbe Zuordnung gilt für den Wert."""
    order = []
    for m in PH.findall(key):
        if m not in order:
            order.append(m)
    mp = {old: "{%d}" % (i + 1) for i, old in enumerate(order)}
    fix = lambda t: PH.sub(lambda m: mp.get(m.group(0), m.group(0)), t)
    return (fix(key), fix(value)) if value is not None else fix(key)


def ui_like(s):
    """Sieht der Text nach einem Text für Menschen aus (nicht nach Befehl, Muster, Stil, Bezeichner)?"""
    t = norm(PH.sub("", s))
    if not LETTERS.search(t):
        return False
    if CODEISH.match(t) or COMMANDISH.match(t) or REGEXISH.search(t) or CSSISH.search(t):
        return False
    if "/" in t and " " not in t:
        return False
    if re.search(r"(^|\s)!(\s|$)|var\(--|^[#.][\w-]|^\[|^\S*=\S*$|^[A-Z][A-Z0-9_.-]+[:=]?$|\|", t) or (" " not in t and "{" in t):
        return False                                                  # Pipeline-Text, Stil, Selektor, Bezeichner, Muster
    if re.match(r"^send: |.*:= ", t):
        return False                                                  # Protokollzeilen von pipbox_send, Makefile-Zeile
    if re.match(r"^(Get:|Unpacking |Setting up |Preparing to )", t):
        return False                                                  # nachgestellte Ausgabe von apt in der Vorschau
    if re.match(r"^[a-z]+/[a-z0-9+.-]+(;.*)?$", t) or (re.search(r"(^|\s)[a-z][a-z0-9-]*=\S", t) and not re.search(r"[.!?:]$", t) and not t[0].isupper()):
        return False                                                  # MIME-Typen, Pipeline-Teile wie "tee name=vt{1}"
    words = re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß'-]*", t)
    if len(words) == 1 and not (t[0].isupper() and len(t) >= 4 and not t.isupper()):
        return False
    return True


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self.attrs, self.stack, self.scripts, self._script = [], [], [], [], False

    def handle_starttag(self, tag, attrs):
        if tag not in ("br", "meta", "link", "input", "img", "hr"):
            self.stack.append(tag)
        d = dict(attrs)
        skip = d.get("translate") == "no"
        if skip:
            self.stack.append("svg")                     # Teile, die nicht übersetzt werden (Sprachnamen), wie ein ausgelassenes Element behandeln
        for k, v in attrs:
            if k in ATTRS and v and not skip and not (set(self.stack) & SKIP_TAGS - {"svg"} and False):
                self.attrs.append(v)
        if tag == "script":
            self._script = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = False
        while self.stack and self.stack[-1] != tag:
            self.stack.pop()
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self._script:
            self.scripts.append(data)
        elif not (set(self.stack) & SKIP_TAGS):
            self.texts.append(data)


REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^") | {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do"}
ESCAPES = {"n": "\n", "t": "\t", "r": "", "'": "'", '"': '"', "`": "`", "\\": "\\", "/": "/", "$": "$", "{": "{", "}": "}", "b": "", "0": ""}


def js_strings(code):
    """Zeichenketten aus JavaScript mit einem kleinen Zerleger (Kommentare, Regex-Literale, Vorlagen mit verschachtelten ${...}). Eingefügte Werte einer
    Vorlage werden \\x00; die Zeichenketten innerhalb von ${...} werden ebenfalls gesammelt."""
    out = []
    n = len(code)

    def read_string(i, q):
        buf, i = [], i + 1
        while i < n and code[i] != q:
            c = code[i]
            if c == "\\" and i + 1 < n:
                e = code[i + 1]
                if e == "u" and re.match(r"[0-9a-fA-F]{4}", code[i + 2:i + 6]):
                    buf.append(chr(int(code[i + 2:i + 6], 16)))
                    i += 6
                    continue
                if e == "x" and re.match(r"[0-9a-fA-F]{2}", code[i + 2:i + 4]):
                    buf.append(chr(int(code[i + 2:i + 4], 16)))
                    i += 4
                    continue
                buf.append(ESCAPES.get(e, e))
                i += 2
                continue
            buf.append(c)
            i += 1
        return "".join(buf), i + 1

    def read_template(i):
        """Gibt (Text mit \\x00, Position nach der Vorlage) zurück; innere Ausdrücke werden mit lex() durchsucht."""
        buf, i = [], i + 1
        while i < n and code[i] != "`":
            c = code[i]
            if c == "\\" and i + 1 < n:
                buf.append(ESCAPES.get(code[i + 1], code[i + 1]))
                i += 2
            elif c == "$" and code[i + 1:i + 2] == "{":
                depth, j = 1, i + 2
                start = j
                while j < n and depth:
                    ch = code[j]
                    if ch in "\"'":
                        _, j = read_string(j, ch)
                        continue
                    if ch == "`":
                        _, j = read_template(j)
                        continue
                    depth += (ch == "{") - (ch == "}")
                    j += 1
                lex(code[start:j - 1])
                buf.append("\x00")
                i = j
            else:
                buf.append(c)
                i += 1
        out.append("".join(buf))
        return "".join(buf), i + 1

    def lex(src):
        nonlocal code, n
        saved = (code, n)
        code, n = src, len(src)
        i, prev = 0, ""
        while i < n:
            c = code[i]
            if c.isspace():
                i += 1
            elif code.startswith("//", i):
                i = code.find("\n", i)
                i = n if i < 0 else i
            elif code.startswith("/*", i):
                i = code.find("*/", i)
                i = n if i < 0 else i + 2
            elif c in "\"'":
                s, i = read_string(i, c)
                out.append(s)
                prev = "str"
            elif c == "`":
                _, i = read_template(i)
                prev = "str"
            elif c == "/" and (prev in REGEX_PREV or prev == ""):
                i += 1
                in_cls = False
                while i < n and (code[i] != "/" or in_cls) and code[i] != "\n":
                    if code[i] == "\\":
                        i += 1
                    elif code[i] == "[":
                        in_cls = True
                    elif code[i] == "]":
                        in_cls = False
                    i += 1
                i += 1
                while i < n and code[i].isalpha():
                    i += 1
                prev = "re"
            elif c.isalnum() or c in "_$":
                m = re.match(r"[A-Za-z0-9_$]+", code[i:])
                prev = m.group(0) if m.group(0) in REGEX_PREV else "id"
                i += len(m.group(0))
            else:
                prev = c if c not in ")]" else "id"
                i += 1
        code, n = saved

    lex(code)
    return out


def html_texts(fragment):
    p = Page()
    try:
        p.feed("<div>" + fragment + "</div>")
    except Exception:
        return [], []
    return p.texts, p.attrs


def from_page(path):
    src = open(path, encoding="utf-8").read()
    p = Page()
    p.feed(src)
    texts, attrs = list(p.texts), list(p.attrs)
    for code in p.scripts:
        for s in js_strings(code):
            if "<" in s and ">" in s:
                t, a = html_texts(s)
                texts += t
                attrs += a
            else:
                texts.append(s)
            for m in re.finditer(r'(?:title|placeholder|aria-label|alt)="([^"]*)"', s):
                attrs.append(m.group(1))
    out = []
    for s in texts + attrs:
        s = number_placeholders(s)
        for line in s.split("\n"):                                    # mehrzeilige Texte (Rückfragen) zeilenweise
            out.append(line)
    return out


def docstring_ids(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) and isinstance(b[0].value.value, str):
                out.add(id(b[0].value))
    return out


def from_python(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    skip = docstring_ids(tree)
    out, inner = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    inner.add(id(v))
                    parts.append(str(v.value))
                else:
                    parts.append("\x00")
            out.append(number_placeholders("".join(parts)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and id(node) not in inner:
            out.append(py_format_to_ph(node.value))
    res = []
    for s in out:
        res += s.split("\n")
    return res


SOURCES = {"index.html": ("page", "web/index.html"), "login.html": ("page", "web/login.html"),
           "server.py": ("py", "server.py"), "pipbox_send.py": ("py", "pipbox_send.py"), "dji_daemon.py": ("py", "dji_daemon.py"), "dji.py": ("py", "dji.py")}
for _f in sorted(os.listdir(os.path.join(ROOT, "install"))):
    if _f.endswith(".py"):
        SOURCES["install/" + _f] = ("py", "install/" + _f)


def collect():
    res = {}
    for name, (kind, rel) in SOURCES.items():
        path = os.path.join(ROOT, rel)
        raw = from_page(path) if kind == "page" else from_python(path)
        seen, items = set(), []
        for s in raw:
            s = norm(s)
            if s and s not in seen and ui_like(s):
                seen.add(s)
                items.append(s)
        res[name] = items
    return res


def extra_keys():
    """Kurze Bruchstücke, die die Erkennung absichtlich übergeht (einzelne kleingeschriebene Wörter, die zusammengesetzt angezeigt werden)"""
    path = os.path.join(ROOT, "tools", "i18n_extra_keys.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def all_keys():
    keys = set(extra_keys())
    for v in collect().values():
        keys |= set(v)
    return sorted(keys)


if __name__ == "__main__":
    if "--skeleton" in sys.argv:
        code = sys.argv[sys.argv.index("--skeleton") + 1]
        out = {"lang": code, "fallback": "en", "exact": {k: "" for k in all_keys()}, "patterns": []}
        path = os.path.join(ROOT, "web", "i18n", code + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("geschrieben:", path, len(out["exact"]), "Schlüssel")
    elif "--keys" in sys.argv:
        json.dump(all_keys(), sys.stdout, ensure_ascii=False, indent=1)
    else:
        data = collect()
        if "--count" in sys.argv:
            tot = set()
            for k, v in data.items():
                print("%-34s %5d" % (k, len(v)))
                tot |= set(v)
            print("%-34s %5d  (%d Zeichen)" % ("verschiedene insgesamt", len(tot), sum(len(x) for x in tot)))
        else:
            json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
