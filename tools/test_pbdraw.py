"""Prüft den Zeichenkern des PiP-Bausteins (gst/gstpbpip.c) ohne GStreamer: Stil-Text, Beschnitt, Deckkraft, Rahmen, Eckenrundung.

Wie test_corner_pos.py: Die Abschnitte PB_POS und PB_DRAW werden aus der C-Datei geschnitten und mit einem kleinen
C-Hauptprogramm (Typen nachgebaut) einzeln übersetzt (cc -O0 -Wall -Werror). Python legt Testmuster an, das Hauptprogramm
rechnet, Python prüft die Bytes. Zusätzlich:
  - eine Übersetzung mit Address-/UB-Sanitizer, falls der Compiler sie kann (sonst nur Wächterbytes),
  - ein Zähler für Lesezugriffe auf das Hauptbild (alle Zugriffe des Abschnitts laufen über memcpy),
  - eine naive Referenz (jeder Bildpunkt einzeln) gegen den schnellen Pfad mit Zufallsfällen,
  - ein davon unabhängiges Fließkomma-Modell nach der Beschreibung (Abdeckung = r - Abstand + 0,5)."""
import math
import os
import random
import re
import subprocess
import tempfile
import unittest
from fractions import Fraction

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gst", "gstpbpip.c")

HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(__has_feature)
#if __has_feature(address_sanitizer)
#include <sanitizer/asan_interface.h>
#define PB_ASAN 1
#endif
#endif
#ifndef PB_ASAN
#define ASAN_POISON_MEMORY_REGION(a, n) ((void) (a), (void) (n))
#define ASAN_UNPOISON_MEMORY_REGION(a, n) ((void) (a), (void) (n))
#endif
typedef unsigned char guint8; typedef unsigned int guint; typedef int gint; typedef long long gint64;
typedef size_t gsize; typedef int gboolean;
#define TRUE 1
#define FALSE 0
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))

/* Lesezugriffe auf das Hauptbild zählen: der Abschnitt liest nur über memcpy */
static const guint8 *g_lo[2], *g_hi[2];
static size_t g_reads;
static void *trace_memcpy(void *d, const void *s, size_t n) {
  const guint8 *p = (const guint8 *) s;
  for (int i = 0; i < 2; i++)
    if (p >= g_lo[i] && p < g_hi[i])
      g_reads += n;
  return memcpy(d, s, n);
}
#undef memcpy
#define memcpy(d, s, n) trace_memcpy((d), (s), (n))

@POS@
@DRAW@

/* Ebene mit Wächterbytes davor und dahinter (unter ASan zusätzlich vergiftet) */
#define GUARD 64
typedef struct { guint8 *base, *p; size_t n; } Plane;
static Plane plane_new(size_t n) {
  Plane pl;
  pl.n = n;
  pl.base = malloc(n + 2 * GUARD);
  memset(pl.base, 0xA5, n + 2 * GUARD);
  pl.p = pl.base + GUARD;
  ASAN_POISON_MEMORY_REGION(pl.base, GUARD);
  ASAN_POISON_MEMORY_REGION(pl.p + n, GUARD);
  return pl;
}
static int plane_guards_ok(Plane *pl) {
  ASAN_UNPOISON_MEMORY_REGION(pl->base, GUARD);
  ASAN_UNPOISON_MEMORY_REGION(pl->p + pl->n, GUARD);
  for (int i = 0; i < GUARD; i++)
    if (pl->base[i] != 0xA5 || pl->p[pl->n + i] != 0xA5)
      return 0;
  return 1;
}
static void plane_free(Plane *pl) {
  ASAN_UNPOISON_MEMORY_REGION(pl->base, GUARD);
  ASAN_UNPOISON_MEMORY_REGION(pl->p + pl->n, GUARD);
  free(pl->base);
}

/* Naive Referenz: jeder Bildpunkt einzeln nach der Definition, das Hauptbild wird immer gelesen */
static void naive(guint8 *dy, gint ys, guint8 *duv, gint uvs, gint mw, const guint8 *src, gint pw, gint ph,
                  const PbPlace *pl, const PbStyle *st) {
  if (st->op <= 0)
    return;
  PbShape s;
  pb_shape_init(&s, st, mw, pl->cw & ~1, pl->ch & ~1);
  gint yb, ub, vb;
  pb_rgb_to_nv12(st->bc, &yb, &ub, &vb);
  const guint8 *suv = src + (size_t) pw * ph;
  for (gint y = 0; y < s.ch; y++)
    for (gint x = 0; x < s.cw; x++) {
      gint wb, a;
      pb_shape_px(&s, x, y, &wb, &a);
      if (a <= 0)
        continue;
      guint8 *d = dy + (size_t) (pl->y + y) * ys + pl->x + x;
      *d = (guint8) pb_mix(*d, src[(size_t) (pl->cy + y) * pw + pl->cx + x], yb, wb, a);
    }
  for (gint m = 0; m < s.ch / 2; m++)
    for (gint k = 0; k < s.cw / 2; k++) {
      gint w4 = 0, a4 = 0;
      for (gint q = 0; q < 4; q++) {
        gint w, a;
        pb_shape_px(&s, 2 * k + (q & 1), 2 * m + (q >> 1), &w, &a);
        w4 += w;
        a4 += a;
      }
      const gint wb = (w4 + 2) >> 2, a = (a4 + 2) >> 2;
      if (a <= 0)
        continue;
      guint8 *d = duv + (size_t) (pl->y / 2 + m) * uvs + pl->x + 2 * k;
      const guint8 *p = suv + (size_t) (pl->cy / 2 + m) * pw + pl->cx + 2 * k;
      d[0] = (guint8) pb_mix(d[0], p[0], ub, wb, a);
      d[1] = (guint8) pb_mix(d[1], p[1], vb, wb, a);
    }
}

static const char *style_arg(const char *a) { return strcmp(a, "@NULL") ? a : NULL; }

/* draw|naive style mw mh ys uvs pw ph cx cy cw ch x y infile outfile */
static int do_draw(int argc, char **argv, int use_naive) {
  if (argc != 17)
    return 2;
  PbStyle st;
  pb_style_parse(style_arg(argv[2]), &st);
  const gint mw = atoi(argv[3]), mh = atoi(argv[4]), ys = atoi(argv[5]), uvs = atoi(argv[6]), pw = atoi(argv[7]), ph = atoi(argv[8]);
  PbPlace pl = { atoi(argv[9]), atoi(argv[10]), atoi(argv[11]), atoi(argv[12]), atoi(argv[13]), atoi(argv[14]) };
  const size_t ny = (size_t) mh * ys, nuv = (size_t) (mh / 2) * uvs, nsrc = (size_t) pw * ph * 3 / 2;
  Plane Y = plane_new(ny), UV = plane_new(nuv);
  guint8 *src = malloc(nsrc);
  FILE *f = fopen(argv[15], "rb");
  if (!f || fread(Y.p, 1, ny, f) != ny || fread(UV.p, 1, nuv, f) != nuv || fread(src, 1, nsrc, f) != nsrc)
    return 3;
  fclose(f);
  g_lo[0] = Y.p; g_hi[0] = Y.p + ny; g_lo[1] = UV.p; g_hi[1] = UV.p + nuv; g_reads = 0;
  if (use_naive)
    naive(Y.p, ys, UV.p, uvs, mw, src, pw, ph, &pl, &st);
  else
    pb_draw_picture(Y.p, ys, UV.p, uvs, mw, src, pw, ph, &pl, &st);
  f = fopen(argv[16], "wb");
  if (!f || fwrite(Y.p, 1, ny, f) != ny || fwrite(UV.p, 1, nuv, f) != nuv)
    return 3;
  fclose(f);
  printf("guards %d reads %lu\n", plane_guards_ok(&Y) && plane_guards_ok(&UV), (unsigned long) g_reads);
  plane_free(&Y);
  plane_free(&UV);
  free(src);
  return 0;
}

static unsigned long long rng_state;
static unsigned rnd(void) {
  rng_state = rng_state * 6364136223846793005ULL + 1442695040888963407ULL;
  return (unsigned) (rng_state >> 33);
}
static int rr(int lo, int hi) { return lo + (int) (rnd() % (unsigned) (hi - lo + 1)); }

/* fuzz seed count: Zufallsfälle, schneller Pfad gegen naive Referenz (Bytes, Wächter, Platz im Hauptbild) */
static int do_fuzz(int argc, char **argv) {
  if (argc != 4)
    return 2;
  rng_state = strtoull(argv[2], NULL, 10);
  const int count = atoi(argv[3]);
  static const int MWS[] = { 64, 96, 480, 1000, 1920, 3840 };
  int ran = 0, skipped = 0, general = 0;
  for (int it = 0; it < count; it++) {
    const gint mw = MWS[rnd() % 6];
    gint pw = (rnd() % 4 == 0) ? 2 * rr(8, 200) : 2 * rr(8, 48);
    const gint ph = 2 * rr(8, 40);
    const gint mh = ph + 2 * rr(0, 20);
    const gint ys = mw + ((rnd() % 2) ? 0 : 2 * rr(0, 20)), uvs = mw + ((rnd() % 2) ? 0 : 2 * rr(0, 20));
    PbStyle st;
    pb_style_default(&st);
    st.op = (rnd() % 3 == 0) ? 100 : (rnd() % 12 == 0 ? 0 : rr(1, 100));
    if (rnd() % 2) { st.cl = rr(0, 900); st.cr = rr(0, 900); }
    if (rnd() % 2) { st.ct = rr(0, 900); st.cb = rr(0, 900); }
    st.bw = (rnd() % 3 == 0) ? 0 : rr(1, 64);
    st.br = (rnd() % 3 == 0) ? 0 : rr(0, 200);
    st.bo = (rnd() % 3 == 0) ? 100 : rr(0, 100);
    st.bc = rnd() & 0xffffff;
    PbPlace pl;
    if (!pb_place(&st, mw, mh, pw, ph, (guint) rr(0, 5), rr(0, 1000), rr(0, 1000), &pl)) {
      skipped++;
      continue;
    }
    if (pl.x < 0 || pl.y < 0 || (pl.x & 1) || (pl.y & 1) || (pl.cx & 1) || (pl.cy & 1) || (pl.cw & 1) || (pl.ch & 1) ||
        pl.x + pl.cw > mw || pl.y + pl.ch > mh || pl.cx < 0 || pl.cy < 0 || pl.cx + pl.cw > pw || pl.cy + pl.ch > ph) {
      printf("FAIL place %d %d %d %d %d %d\n", pl.cx, pl.cy, pl.cw, pl.ch, pl.x, pl.y);
      return 1;
    }
    const size_t ny = (size_t) mh * ys, nuv = (size_t) (mh / 2) * uvs, nsrc = (size_t) pw * ph * 3 / 2;
    Plane Y = plane_new(ny), UV = plane_new(nuv), RY = plane_new(ny), RUV = plane_new(nuv);
    guint8 *src = malloc(nsrc);
    for (size_t i = 0; i < ny; i++) Y.p[i] = RY.p[i] = (guint8) rnd();
    for (size_t i = 0; i < nuv; i++) UV.p[i] = RUV.p[i] = (guint8) rnd();
    for (size_t i = 0; i < nsrc; i++) src[i] = (guint8) rnd();
    pb_draw_picture(Y.p, ys, UV.p, uvs, mw, src, pw, ph, &pl, &st);
    naive(RY.p, ys, RUV.p, uvs, mw, src, pw, ph, &pl, &st);
    const int ok = memcmp(Y.p, RY.p, ny) == 0 && memcmp(UV.p, RUV.p, nuv) == 0 && plane_guards_ok(&Y) && plane_guards_ok(&UV);
    if (!ok) {
      printf("FAIL it=%d mw=%d mh=%d ys=%d uvs=%d pw=%d ph=%d pl=%d,%d,%d,%d,%d,%d op=%d bw=%d br=%d bo=%d bc=%06x\n", it, mw, mh, ys, uvs,
             pw, ph, pl.cx, pl.cy, pl.cw, pl.ch, pl.x, pl.y, st.op, st.bw, st.br, st.bo, st.bc);
      return 1;
    }
    if (st.op < 100 || st.bw > 0)
      general++;
    ran++;
    plane_free(&Y); plane_free(&UV); plane_free(&RY); plane_free(&RUV);
    free(src);
  }
  printf("fuzz ok ran=%d skipped=%d general=%d\n", ran, skipped, general);
  return 0;
}

int main(int argc, char **argv) {
  if (argc < 2)
    return 2;
  const char *mode = argv[1];
  if (!strcmp(mode, "parse") && argc == 3) {
    PbStyle st;
    pb_style_parse(style_arg(argv[2]), &st);
    printf("%d %d %d %d %d %d %06x %d %d\n", st.op, st.cl, st.cr, st.ct, st.cb, st.bw, st.bc, st.bo, st.br);
    return 0;
  }
  if (!strcmp(mode, "crop") && argc == 5) {
    PbStyle st;
    gint cx, cy, cw, ch;
    pb_style_parse(style_arg(argv[2]), &st);
    const gboolean r = pb_style_crop(&st, atoi(argv[3]), atoi(argv[4]), &cx, &cy, &cw, &ch);
    printf("%d %d %d %d %d\n", r, cx, cy, cw, ch);
    return 0;
  }
  if (!strcmp(mode, "place") && argc == 10) {
    PbStyle st;
    PbPlace pl = { 0, 0, 0, 0, 0, 0 };
    pb_style_parse(style_arg(argv[2]), &st);
    const gboolean r = pb_place(&st, atoi(argv[3]), atoi(argv[4]), atoi(argv[5]), atoi(argv[6]), (guint) atoi(argv[7]), atoi(argv[8]), atoi(argv[9]), &pl);
    printf("%d %d %d %d %d %d %d\n", r, pl.cx, pl.cy, pl.cw, pl.ch, pl.x, pl.y);
    return 0;
  }
  if (!strcmp(mode, "rgb")) {
    for (int i = 2; i < argc; i++) {
      gint y, u, v;
      pb_rgb_to_nv12((guint) strtoul(argv[i], NULL, 16), &y, &u, &v);
      printf("%d %d %d\n", y, u, v);
    }
    return 0;
  }
  if (!strcmp(mode, "draw"))
    return do_draw(argc, argv, 0);
  if (!strcmp(mode, "naive"))
    return do_draw(argc, argv, 1);
  if (!strcmp(mode, "fuzz"))
    return do_fuzz(argc, argv);
  return 2;
}
"""

_tmp = None
_exe_plain = None
_exe_san = None


def _section(text, name):
    m = re.search(r"/\* %s_BEGIN.*?\*/(.*?)/\* %s_END \*/" % (name, name), text, re.S)
    assert m is not None, "Markierungen %s_BEGIN/END fehlen" % name
    return m.group(1)


def _build(c_path, out, flags):
    subprocess.run(["cc", "-O0", "-Wall", "-Werror"] + flags + [c_path, "-o", out], check=True, capture_output=True)


def setUpModule():
    global _tmp, _exe_plain, _exe_san
    _tmp = tempfile.TemporaryDirectory()
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    code = HARNESS.replace("@POS@", _section(text, "PB_POS")).replace("@DRAW@", _section(text, "PB_DRAW"))
    c_path = os.path.join(_tmp.name, "t.c")
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(code)
    _exe_plain = os.path.join(_tmp.name, "t_plain")
    _build(c_path, _exe_plain, [])
    # Mit Sanitizern nur, wenn Übersetzen UND ein Probelauf klappen (sonst bleiben nur die Wächterbytes)
    san = os.path.join(_tmp.name, "t_san")
    try:
        _build(c_path, san, ["-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"])
        subprocess.run([san, "parse", "op=5"], check=True, capture_output=True)
        _exe_san = san
    except (subprocess.CalledProcessError, OSError):
        _exe_san = None


def tearDownModule():
    _tmp.cleanup()


def run(args, exe=None):
    p = subprocess.run([exe or _exe_plain] + [str(a) for a in args], capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError("Testprogramm %s endete mit %d:\n%s%s" % (args[:2], p.returncode, p.stdout, p.stderr))
    return p.stdout


# ---------------------------------------------------------------- Hilfen

DEFAULT = dict(op=100, cl=0, cr=0, ct=0, cb=0, bw=0, bc=0xFFFFFF, bo=100, br=0)


def sty(**kw):
    """Stil als (Text, Wörterbuch) für das C-Programm und das Python-Modell."""
    d = dict(DEFAULT)
    d.update(kw)
    parts = []
    for k, v in kw.items():
        parts.append("%s=%06x" % (k, v) if k == "bc" else "%s=%d" % (k, v))
    return ",".join(parts), d


def nv12_of(rgb):
    """BT.709, begrenzter Bereich, exakt gerechnet (Brüche), gerundet."""
    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    F = Fraction
    y = 16 + F(219) * (F(2126, 10000) * r + F(7152, 10000) * g + F(722, 10000) * b) / 255
    u = 128 + F(224) * (F(-1146, 10000) * r + F(-3854, 10000) * g + F(5000, 10000) * b) / 255
    v = 128 + F(224) * (F(5000, 10000) * r + F(-4542, 10000) * g + F(-458, 10000) * b) / 255

    def rnd(q):
        return math.floor(q + F(1, 2))
    return (min(max(rnd(y), 16), 235), min(max(rnd(u), 16), 240), min(max(rnd(v), 16), 240))


class Frame:
    """Hauptbild: Y- und UV-Ebene mit Stride (kann größer als die Breite sein; der Rest ist Füllung, die unverändert bleiben muss)."""

    def __init__(self, mw, mh, ys=None, uvs=None, seed=1, flat=None):
        self.mw, self.mh = mw, mh
        self.ys, self.uvs = ys or mw, uvs or mw
        ny, nuv = mh * self.ys, (mh // 2) * self.uvs
        if flat is None:
            rnd = random.Random(seed)
            self.y = bytearray(rnd.getrandbits(8 * ny).to_bytes(ny, "little"))
            self.uv = bytearray(rnd.getrandbits(8 * nuv).to_bytes(nuv, "little"))
        else:
            y, u, v = flat
            self.y = bytearray([y]) * ny
            self.uv = bytearray([u, v]) * (nuv // 2)


class Pic:
    """Kleines Bild: NV12 dicht gepackt (Y, dann UV, Stride = Breite)."""

    def __init__(self, pw, ph, y, uv):
        self.pw, self.ph, self.y, self.uv = pw, ph, bytes(y), bytes(uv)

    @classmethod
    def pattern(cls, pw, ph, seed=0):
        y = bytearray((30 + (x * 5 + yy * 9 + seed) % 150) for yy in range(ph) for x in range(pw))
        uv = bytearray()
        for r in range(ph // 2):
            for k in range(pw // 2):
                uv.append(60 + (k * 3 + r * 7 + seed) % 100)
                uv.append(90 + (k * 11 + r * 5 + seed) % 110)
        return cls(pw, ph, y, uv)

    @classmethod
    def flat(cls, pw, ph, y, u, v):
        return cls(pw, ph, bytes([y]) * (pw * ph), bytes([u, v]) * (pw * ph // 4))

    def Y(self, x, y):
        return self.y[y * self.pw + x]

    def UV(self, k, m):
        i = m * self.pw + 2 * k
        return self.uv[i], self.uv[i + 1]


def place(text, mw, mh, pw, ph, corner=5, fx=0, fy=0):
    out = run(["place", text if text is not None else "@NULL", mw, mh, pw, ph, corner, fx, fy]).split()
    ok, cx, cy, cw, ch, x, y = map(int, out)
    return (cx, cy, cw, ch, x, y) if ok else None


class Res:
    def __init__(self, frame, pic, pl, style, y, uv, guards, reads):
        self.frame, self.pic, self.pl, self.style = frame, pic, pl, style
        self.y, self.uv, self.guards, self.reads = y, uv, guards, reads

    def Y(self, px, py):
        cx, cy, cw, ch, x, y = self.pl
        return self.y[(y + py) * self.frame.ys + x + px]

    def UV(self, k, m):
        cx, cy, cw, ch, x, y = self.pl
        i = (y // 2 + m) * self.frame.uvs + x + 2 * k
        return self.uv[i], self.uv[i + 1]

    def rect(self):
        """Bytes des beschriebenen Rechtecks (Y-Zeilen, dann UV-Zeilen)."""
        cx, cy, cw, ch, x, y = self.pl
        f = self.frame
        out = bytearray()
        for r in range(ch):
            out += self.y[(y + r) * f.ys + x:(y + r) * f.ys + x + cw]
        for r in range(ch // 2):
            out += self.uv[(y // 2 + r) * f.uvs + x:(y // 2 + r) * f.uvs + x + cw]
        return bytes(out)


def draw(text, frame, pic, pl, exe=None, mode="draw"):
    infile, outfile = os.path.join(_tmp.name, "in.bin"), os.path.join(_tmp.name, "out.bin")
    with open(infile, "wb") as f:
        f.write(frame.y)
        f.write(frame.uv)
        f.write(pic.y)
        f.write(pic.uv)
    out = run([mode, text if text is not None else "@NULL", frame.mw, frame.mh, frame.ys, frame.uvs, pic.pw, pic.ph] +
              list(pl) + [infile, outfile], exe).split()
    assert out[0] == "guards"
    with open(outfile, "rb") as f:
        data = f.read()
    ny = frame.mh * frame.ys
    return data[:ny], data[ny:], int(out[1]), int(out[3])


def render(style, mw=1920, mh=120, pw=64, ph=40, ys=None, uvs=None, pic=None, frame=None, at=None, corner=5, fx=0, fy=0,
           exe=None, mode="draw"):
    """Ausschnitt und Platz kommen von pb_place (wie im Plugin); mit at=(x, y) wird nur der Ort überschrieben."""
    text, d = style
    pic = pic or Pic.pattern(pw, ph)
    frame = frame or Frame(mw, mh, ys, uvs)
    pl = place(text, frame.mw, frame.mh, pic.pw, pic.ph, corner, fx, fy)
    assert pl is not None, "pb_place meldet: nichts zeichnen"
    if at:
        pl = pl[:4] + tuple(at)
    y, uv, guards, reads = draw(text, frame, pic, pl, exe, mode)
    return Res(frame, pic, pl, d, y, uv, guards, reads)


# Fließkomma-Modell nach der Beschreibung, unabhängig vom C-Code
def _shape(style, mw, cw, ch):
    lim = min(cw, ch) // 2
    bw = ro = 0
    if style["bw"] > 0:
        bw = min(max((style["bw"] * mw + 960) // 1920, 1), lim)
    if style["br"] > 0:                      # die Rundung gilt für das Bild selbst, mit oder ohne Rahmen
        ro = min((style["br"] * mw + 960) // 1920, lim)
    return bw, ro, max(ro - bw, 0)


def _corner(fx, fy, r):
    return min(1.0, max(0.0, r - math.hypot(r - fx - 0.5, r - fy - 0.5) + 0.5))


def model_cov(style, mw, cw, ch, x, y):
    """(Rahmenanteil, Deckkraft) des Luma-Bildpunkts (x, y) des Ausschnitts."""
    bw, ro, ri = _shape(style, mw, cw, ch)
    fx, fy = min(x, cw - 1 - x), min(y, ch - 1 - y)
    ao = _corner(fx, fy, ro) if ro > 0 and fx < ro and fy < ro else 1.0
    ai = 1.0
    if bw > 0:
        ix, iy = fx - bw, fy - bw
        if ix < 0 or iy < 0:
            ai = 0.0
        elif ri > 0 and ix < ri and iy < ri:
            ai = _corner(ix, iy, ri)
    return style["bo"] / 100.0 * (1.0 - ai), style["op"] / 100.0 * ao


class ModelMixin:
    def check_model(self, res, tol=1.0):
        """Das Rechteck stimmt mit dem Modell überein (Toleranz tol), alles außerhalb ist byteweise unverändert."""
        st, f, pic = res.style, res.frame, res.pic
        cx, cy, cw, ch, x, y = res.pl
        yb, ub, vb = nv12_of(st["bc"])
        outside_y, outside_uv = bytearray(res.y), bytearray(res.uv)
        for py in range(ch):
            for px in range(cw):
                wb, a = model_cov(st, f.mw, cw, ch, px, py)
                i = (y + py) * f.ys + x + px
                d, p = f.y[i], pic.Y(cx + px, cy + py)
                exp = d * (1 - a) + (p * (1 - wb) + yb * wb) * a
                if a < 1e-9:
                    self.assertEqual(res.y[i], d, "Luma %d,%d außerhalb der Form muss das Hauptbild bleiben" % (px, py))
                else:
                    self.assertLessEqual(abs(res.y[i] - exp), tol, "Luma %d,%d: %d statt %.2f" % (px, py, res.y[i], exp))
                outside_y[i] = d
        for m in range(ch // 2):
            for k in range(cw // 2):
                w4 = a4 = 0.0
                for q in range(4):
                    w, a = model_cov(st, f.mw, cw, ch, 2 * k + (q & 1), 2 * m + (q >> 1))
                    w4 += w
                    a4 += a
                wb, a = w4 / 4, a4 / 4
                i = (y // 2 + m) * f.uvs + x + 2 * k
                pu, pv = pic.UV(cx // 2 + k, cy // 2 + m)
                for j, (p, b) in enumerate(((pu, ub), (pv, vb))):
                    d = f.uv[i + j]
                    exp = d * (1 - a) + (p * (1 - wb) + b * wb) * a
                    if a < 1e-9:
                        self.assertEqual(res.uv[i + j], d)
                    else:
                        self.assertLessEqual(abs(res.uv[i + j] - exp), tol, "Chroma %d,%d,%d: %d statt %.2f" % (k, m, j, res.uv[i + j], exp))
                    outside_uv[i + j] = d
        self.assertEqual(bytes(outside_y), bytes(f.y), "Hauptbild (Y) außerhalb des Bildes verändert")
        self.assertEqual(bytes(outside_uv), bytes(f.uv), "Hauptbild (UV) außerhalb des Bildes verändert")
        self.assertEqual(res.guards, 1, "Wächterbytes verändert")


def old_memcpy_result(frame, pic, x, y):
    """Der bisherige Schnellpfad: Zeilen kopieren, nichts anderes."""
    ey, euv = bytearray(frame.y), bytearray(frame.uv)
    for r in range(pic.ph):
        ey[(y + r) * frame.ys + x:(y + r) * frame.ys + x + pic.pw] = pic.y[r * pic.pw:(r + 1) * pic.pw]
    for r in range(pic.ph // 2):
        euv[(y // 2 + r) * frame.uvs + x:(y // 2 + r) * frame.uvs + x + pic.pw] = pic.uv[r * pic.pw:(r + 1) * pic.pw]
    return bytes(ey), bytes(euv)


# ---------------------------------------------------------------- 1. Stil-Text

class StyleParse(unittest.TestCase):
    def parse(self, text):
        out = run(["parse", text if text is not None else "@NULL"]).split()
        op, cl, cr, ct, cb, bw, bc, bo, br = out
        return dict(op=int(op), cl=int(cl), cr=int(cr), ct=int(ct), cb=int(cb), bw=int(bw), bc=int(bc, 16), bo=int(bo), br=int(br))

    def test_example(self):
        self.assertEqual(self.parse("op=80,cl=400,cr=0,ct=0,cb=0,bw=6,bc=ff8800,bo=70,br=24"),
                         dict(op=80, cl=400, cr=0, ct=0, cb=0, bw=6, bc=0xFF8800, bo=70, br=24))

    def test_empty_and_null_are_default(self):
        self.assertEqual(self.parse(None), DEFAULT)
        self.assertEqual(self.parse(""), DEFAULT)
        self.assertEqual(self.parse(" , ,,"), DEFAULT)

    def test_unknown_keys_and_fragments_are_ignored(self):
        want = dict(DEFAULT, op=50)
        for text in ("foo=1,op=50", "op=50,foo=1", "bar,op=50,=7,baz=", "OP=10,op=50", "op=50,bw", "x=y=z,op=50", "opacity=3,op=50"):
            self.assertEqual(self.parse(text), want, text)

    def test_errors_keep_defaults(self):
        for text in ("op=abc", "op=", "op=5x", "op=5.5", "op= ", "bw=--3", "cl=0x10", "bc=ff88", "bc=gg0000", "bc=ff880000",
                     "bc=#ff8800", "bc=", "bo=1e2"):
            self.assertEqual(self.parse(text), DEFAULT, text)
        self.assertEqual(self.parse("br=1,5"), dict(DEFAULT, br=1))          # "5" ohne Schlüssel wird übergangen

    def test_error_does_not_undo_an_earlier_value(self):
        self.assertEqual(self.parse("op=40,op=abc,bc=112233,bc=zz"), dict(DEFAULT, op=40, bc=0x112233))

    def test_clamping(self):
        self.assertEqual(self.parse("op=150,cl=5000,cr=1901,ct=1900,cb=-3,bw=100,bo=101,br=999"),
                         dict(DEFAULT, op=100, cl=1900, cr=1900, ct=1900, cb=0, bw=64, bo=100, br=200))
        self.assertEqual(self.parse("op=-5,bw=-1,bo=-100,br=-7,cl=-1"), dict(DEFAULT, op=0, bw=0, bo=0, br=0, cl=0))
        self.assertEqual(self.parse("op=99999999999999999999,cl=+7"), dict(DEFAULT, op=100, cl=7))
        self.assertEqual(self.parse("bw=64,br=200,op=0,bo=0,cl=1900"), dict(DEFAULT, bw=64, br=200, op=0, bo=0, cl=1900))

    def test_last_wins_whitespace_and_hex_case(self):
        self.assertEqual(self.parse("op=10,op=20"), dict(DEFAULT, op=20))
        self.assertEqual(self.parse(" op = 70 , bw=3 ,bc=FfAa00"), dict(DEFAULT, op=70, bw=3, bc=0xFFAA00))
        self.assertEqual(self.parse("bc=000000"), dict(DEFAULT, bc=0))


# ---------------------------------------------------------------- 2. Beschnitt und Platz

def ref_crop(st, sw, sh):
    """Beschreibung: round(c * sw / 1920) usw., auf gerade Werte abgerundet, mindestens 16x16 übrig, sonst ganz ignorieren."""
    def rnd(num, den):
        return math.floor(Fraction(num, den) + Fraction(1, 2))
    l, r = rnd(st["cl"] * sw, 1920) & ~1, rnd(st["cr"] * sw, 1920) & ~1
    t, b = rnd(st["ct"] * sh, 1080) & ~1, rnd(st["cb"] * sh, 1080) & ~1
    if (l, r, t, b) == (0, 0, 0, 0):
        return (0, (0, 0, sw, sh))
    w, h = (sw - l - r) & ~1, (sh - t - b) & ~1
    if w < 16 or h < 16:
        return (0, (0, 0, sw, sh))
    return (1, (l, t, w, h))


class Crop(unittest.TestCase, ModelMixin):
    def crop(self, text, sw=480, sh=270):
        ok, cx, cy, cw, ch = map(int, run(["crop", text, sw, sh]).split())
        return ok, (cx, cy, cw, ch)

    def test_conversion_from_1920x1080_to_480x270(self):
        self.assertEqual(self.crop("cl=400"), (1, (100, 0, 380, 270)))
        self.assertEqual(self.crop("cr=400"), (1, (0, 0, 380, 270)))
        self.assertEqual(self.crop("ct=540"), (1, (0, 134, 480, 136)))
        self.assertEqual(self.crop("cb=540"), (1, (0, 0, 480, 136)))
        self.assertEqual(self.crop("cl=400,cr=200,ct=100,cb=300"), (1, (100, 24, 330, 172)))
        self.assertEqual(self.crop("cl=400", 640, 360), (1, (132, 0, 640 - 132, 360)))

    def test_no_crop_is_whole_picture(self):
        self.assertEqual(self.crop(""), (0, (0, 0, 480, 270)))
        self.assertEqual(self.crop("op=50,bw=4"), (0, (0, 0, 480, 270)))
        self.assertEqual(self.crop("cl=3"), (0, (0, 0, 480, 270)))          # 0,75 Bildpunkte -> 1 -> gerade 0

    def test_rounding_then_even(self):
        self.assertEqual(self.crop("cl=410")[1][0], 102)                    # 102,5 -> 103 -> 102
        self.assertEqual(self.crop("cl=405")[1][0], 100)                    # 101,25 -> 101 -> 100
        self.assertEqual(self.crop("cl=401")[1][0], 100)

    def test_at_least_16x16_remain_else_whole_crop_ignored(self):
        self.assertEqual(self.crop("cl=1856"), (1, (464, 0, 16, 270)))
        self.assertEqual(self.crop("cl=1860"), (1, (464, 0, 16, 270)))
        self.assertEqual(self.crop("cl=1864"), (0, (0, 0, 480, 270)))       # nur 14 breit -> ignoriert
        self.assertEqual(self.crop("ct=1000"), (1, (0, 250, 480, 20)))
        self.assertEqual(self.crop("ct=1030"), (0, (0, 0, 480, 270)))       # nur 12 hoch -> ignoriert
        self.assertEqual(self.crop("cl=400,ct=1030"), (0, (0, 0, 480, 270)))  # auch der gültige Teil entfällt
        self.assertEqual(self.crop("cl=1900,cr=1900"), (0, (0, 0, 480, 270)))

    def test_matches_description_for_many_sizes(self):
        rnd = random.Random(7)
        for _ in range(300):
            sw, sh = 2 * rnd.randint(8, 960), 2 * rnd.randint(8, 540)
            st = dict(DEFAULT, **{k: (rnd.randint(0, 1900) if rnd.random() < 0.6 else 0) for k in ("cl", "cr", "ct", "cb")})
            text = ",".join("%s=%d" % (k, st[k]) for k in ("cl", "cr", "ct", "cb"))
            ok, rect = self.crop(text, sw, sh)
            self.assertEqual((ok, rect), ref_crop(st, sw, sh), "%s bei %dx%d" % (text, sw, sh))
            self.assertTrue(all(v % 2 == 0 for v in rect))

    def test_place_uses_size_of_the_crop(self):
        # 480x270 im 1920x1080-Hauptbild, Rand 32
        m = 32
        self.assertEqual([place("", 1920, 1080, 480, 270, c)[4:] for c in range(5)],
                         [(m, m), (1920 - 480 - m, m), (m, 1080 - 270 - m), (1920 - 480 - m, 1080 - 270 - m), ((1920 - 480) // 2, 1080 - 270 - m)])
        self.assertEqual(place("cl=400", 1920, 1080, 480, 270, 3), (100, 0, 380, 270, 1920 - 380 - m, 1080 - 270 - m))
        self.assertEqual(place("cl=400", 1920, 1080, 480, 270, 1)[4:], (1920 - 380 - m, m))
        self.assertEqual(place("cl=400", 1920, 1080, 480, 270, 4)[4:], ((1920 - 380) // 2, 1080 - 270 - m))
        self.assertEqual(place("ct=540", 1920, 1080, 480, 270, 3), (0, 134, 480, 136, 1920 - 480 - m, 1080 - 136 - m))
        self.assertEqual(place("cl=400", 1920, 1080, 480, 270, 5, 1000, 1000)[4:], (1920 - 380, 1080 - 270))

    def test_place_position_is_even(self):
        for fx in (1, 335, 777, 999):
            for fy in (1, 333, 501):
                cx, cy, cw, ch, x, y = place("cl=400", 1920, 1080, 480, 270, 5, fx, fy)
                self.assertEqual((x % 2, y % 2), (0, 0))
                self.assertEqual(x, (((1920 - cw) * fx) // 1000) & ~1)
                self.assertEqual(y, (((1080 - ch) * fy) // 1000) & ~1)

    def test_place_nothing_when_it_does_not_fit_or_opacity_zero(self):
        self.assertIsNone(place("", 400, 300, 480, 270))
        self.assertIsNone(place("", 640, 200, 480, 270))
        self.assertIsNotNone(place("cl=400", 400, 300, 480, 270))           # beschnitten (380 breit) passt
        self.assertIsNone(place("op=0", 1920, 1080, 480, 270))
        self.assertIsNone(place("op=0,cl=400,bw=3", 1920, 1080, 480, 270))
        self.assertIsNotNone(place("op=1", 1920, 1080, 480, 270))
        # unten Mitte mit Rand: passt nicht mehr, wenn der Rand nicht mehr hineinpasst
        self.assertIsNone(place("", 1920, 270 + 20, 480, 270, 4))

    def test_cropped_content_is_byte_exact(self):
        pic = Pic.pattern(480, 270, seed=3)
        frame = Frame(1920, 1080, ys=1920 + 16, uvs=1920 + 8, seed=5)
        for text in ("cl=400", "cr=400", "cl=400,cr=200,ct=100,cb=300", "ct=540", "cl=410,cb=77"):
            res = render((text, dict(DEFAULT)), pic=pic, frame=frame, corner=3)
            cx, cy, cw, ch, x, y = res.pl
            self.assertEqual((cw % 2, ch % 2, cx % 2, cy % 2), (0, 0, 0, 0))
            ey, euv = bytearray(frame.y), bytearray(frame.uv)
            for r in range(ch):
                ey[(y + r) * frame.ys + x:(y + r) * frame.ys + x + cw] = pic.y[(cy + r) * 480 + cx:(cy + r) * 480 + cx + cw]
            for r in range(ch // 2):
                euv[(y // 2 + r) * frame.uvs + x:(y // 2 + r) * frame.uvs + x + cw] = pic.uv[(cy // 2 + r) * 480 + cx:(cy // 2 + r) * 480 + cx + cw]
            self.assertEqual(res.y, bytes(ey), text)
            self.assertEqual(res.uv, bytes(euv), text)
            self.assertEqual((res.guards, res.reads), (1, 0), text)
        # Größe und Ort des Beispiels aus der Beschreibung
        self.assertEqual(render(("cl=400", dict(DEFAULT)), pic=pic, frame=frame, corner=3).pl, (100, 0, 380, 270, 1508, 778))


# ---------------------------------------------------------------- 3. Standardstil = bisheriger Schnellpfad

class PlainPath(unittest.TestCase):
    STYLES = ("", None, "op=100", "foo=bar", "op=100,bw=0,bo=20,bc=ff0000", "bc=00ff00,bo=3",
              "cl=1900,cr=1900", "ct=1000,cb=1000", "cl=1,cr=1")              # die letzten: Beschnitt ungültig / ergibt 0

    def test_default_style_is_bitwise_the_old_memcpy(self):
        for ys, uvs in ((1920, 1920), (1920 + 34, 1920 + 18)):
            for text in self.STYLES:
                for at in ((0, 0), (10, 6), (1920 - 64, 120 - 40)):
                    frame = Frame(1920, 120, ys, uvs, seed=11)
                    pic = Pic.pattern(64, 40)
                    res = render((text, dict(DEFAULT)), pic=pic, frame=frame, at=at)
                    ey, euv = old_memcpy_result(frame, pic, *at)
                    self.assertEqual(res.y, ey, (text, at, ys))
                    self.assertEqual(res.uv, euv, (text, at, ys))
                    self.assertEqual(res.pl[:4], (0, 0, 64, 40))
                    self.assertEqual((res.guards, res.reads), (1, 0), "der Schnellpfad darf das Hauptbild nicht lesen")

    def test_plain_place_equals_old_position(self):
        # wie bisher: pb_pos mit der Größe des ganzen Bildes
        for pw, ph in ((480, 270), (288, 162), (768, 432)):
            for text in ("", "op=100,bw=0"):
                m = (1920 // 60) & ~1
                self.assertEqual([place(text, 1920, 1080, pw, ph, c)[4:] for c in range(5)],
                                 [(m, m), (1920 - pw - m, m), (m, 1080 - ph - m), (1920 - pw - m, 1080 - ph - m), ((1920 - pw) // 2, 1080 - ph - m)])

    def test_default_style_full_size_pictures(self):
        frame = Frame(1920, 1080, seed=2)
        pic = Pic.pattern(480, 270, seed=9)
        res = render(("", dict(DEFAULT)), pic=pic, frame=frame, corner=3)
        ey, euv = old_memcpy_result(frame, pic, 1920 - 480 - 32, 1080 - 270 - 32)
        self.assertEqual((res.y, res.uv, res.reads), (ey, euv, 0))


# ---------------------------------------------------------------- 4. Rahmen ohne Rundung

class Border(unittest.TestCase, ModelMixin):
    def test_even_border_has_exactly_the_outer_pixels_in_the_border_color(self):
        yb, ub, vb = nv12_of(0xFF8800)
        pic = Pic.pattern(64, 40)
        frame = Frame(1920, 120, ys=1920 + 14, uvs=1920 + 6, seed=3)
        res = render(sty(bw=4, bc=0xFF8800), pic=pic, frame=frame, at=(10, 6))
        ey, euv = bytearray(frame.y), bytearray(frame.uv)
        for py in range(40):
            for px in range(64):
                border = min(px, 63 - px, py, 39 - py) < 4
                ey[(6 + py) * frame.ys + 10 + px] = yb if border else pic.Y(px, py)
        for m in range(20):
            for k in range(32):
                border = min(2 * k, 62 - 2 * k, 2 * m, 38 - 2 * m) < 4
                u, v = (ub, vb) if border else pic.UV(k, m)
                i = (3 + m) * frame.uvs + 10 + 2 * k
                euv[i], euv[i + 1] = u, v
        self.assertEqual(res.y, bytes(ey))
        self.assertEqual(res.uv, bytes(euv))
        self.assertEqual(res.guards, 1)

    def test_border_does_not_change_size_or_position(self):
        pl = place("bw=6,bc=ff8800,br=30", 1920, 1080, 480, 270, 3)
        self.assertEqual(pl, place("", 1920, 1080, 480, 270, 3))
        self.assertEqual(pl, (0, 0, 480, 270, 1408, 778))

    def test_main_picture_is_not_read_for_opaque_border(self):
        for text in (sty(bw=4, bc=0xFF8800), sty(bw=5, bc=0x123456, bo=100), sty(bw=3, bc=0x0000FF, bo=40), sty(bw=7, bo=0, op=100)):
            ra = render(text, frame=Frame(1920, 120, seed=1), at=(10, 6))
            rb = render(text, frame=Frame(1920, 120, seed=2), at=(10, 6))
            self.assertEqual(ra.rect(), rb.rect(), "Ergebnis hängt vom Hauptbild ab: %s" % text[0])
            self.assertEqual((ra.reads, rb.reads), (0, 0), text[0])
            self.check_model(ra)

    def test_translucent_border_mixes_color_with_picture_only(self):
        yb, ub, vb = nv12_of(0xFF8800)
        pic = Pic.pattern(64, 40)
        res = render(sty(bw=4, bc=0xFF8800, bo=50), pic=pic, at=(10, 6))
        self.assertEqual(res.reads, 0)
        for py in range(40):
            for px in range(64):
                border = min(px, 63 - px, py, 39 - py) < 4
                want = (pic.Y(px, py) + yb) / 2 if border else pic.Y(px, py)
                self.assertLessEqual(abs(res.Y(px, py) - want), 1, (px, py))
        for m in range(20):
            for k in range(32):
                border = min(2 * k, 62 - 2 * k, 2 * m, 38 - 2 * m) < 4
                pu, pv = pic.UV(k, m)
                want = ((pu + ub) / 2, (pv + vb) / 2) if border else (pu, pv)
                got = res.UV(k, m)
                self.assertLessEqual(max(abs(got[0] - want[0]), abs(got[1] - want[1])), 1, (k, m))
        self.check_model(res)

    def test_odd_border_width(self):
        for bw in (1, 3, 5, 7):
            for bo in (100, 60):
                res = render(sty(bw=bw, bc=0xFF8800, bo=bo), at=(10, 6))
                self.assertEqual(res.reads, 0)
                self.check_model(res)
                # Luma bildpunktgenau: Zeile bw-1 ist Rahmen, Zeile bw ist Bild
                yb = nv12_of(0xFF8800)[0]
                if bo == 100:
                    self.assertEqual(res.Y(30, bw - 1), yb)
                    self.assertEqual(res.Y(30, bw), res.pic.Y(30, bw))
                    self.assertEqual(res.Y(bw - 1, 20), yb)
                    self.assertEqual(res.Y(bw, 20), res.pic.Y(bw, 20))

    def test_odd_border_chroma_is_mean_of_the_four_luma_pixels(self):
        # bw=3: UV-Paar (Zeile 1, Spalte 1) deckt Luma (2..3, 2..3): drei Bildpunkte Rahmen, einer Bild -> 3/4 Rahmenfarbe
        yb, ub, vb = nv12_of(0xFF8800)
        res = render(sty(bw=3, bc=0xFF8800), at=(10, 6))
        pu, pv = res.pic.UV(1, 1)
        got = res.UV(1, 1)
        self.assertLessEqual(abs(got[0] - (0.75 * ub + 0.25 * pu)), 1)
        self.assertLessEqual(abs(got[1] - (0.75 * vb + 0.25 * pv)), 1)
        # Rand-Paar (Zeile 1, mittlere Spalte): Luma-Zeilen 2 (Rahmen) und 3 (Bild) -> halbe Rahmenfarbe
        pu, pv = res.pic.UV(10, 1)
        got = res.UV(10, 1)
        self.assertLessEqual(abs(got[0] - (ub + pu) / 2), 1)
        self.assertLessEqual(abs(got[1] - (vb + pv) / 2), 1)

    def test_border_width_scales_with_main_width(self):
        base = render(sty(bw=4), at=(10, 6)).rect()                         # 1920 breit: 4 Bildpunkte
        self.assertEqual(render(sty(bw=8), mw=960, at=(10, 6)).rect(), base)     # 8 * 960 / 1920 = 4
        self.assertEqual(render(sty(bw=2), mw=3840, at=(10, 6)).rect(), base)    # 2 * 3840 / 1920 = 4
        one = render(sty(bw=1), at=(10, 6)).rect()
        self.assertEqual(render(sty(bw=1), mw=480, at=(10, 6)).rect(), one)      # mindestens ein Bildpunkt
        self.assertNotEqual(base, one)

    def test_border_width_is_limited_to_half_the_smaller_side(self):
        yb, ub, vb = nv12_of(0xFF8800)
        res = render(sty(bw=64, bc=0xFF8800), at=(10, 6))                   # 64x40: höchstens 20 Bildpunkte -> alles Rahmen
        self.assertTrue(all(res.Y(px, py) == yb for px in range(64) for py in range(40)))
        self.assertTrue(all(res.UV(k, m) == (ub, vb) for k in range(32) for m in range(20)))
        self.assertEqual(res.rect(), render(sty(bw=40, bc=0xFF8800), at=(10, 6)).rect())

    def test_border_inside_a_crop(self):
        pic = Pic.pattern(480, 270)
        res = render(sty(cl=400, ct=100, bw=6, bc=0x00AAFF), pic=pic, mw=1920, mh=1080, at=(40, 30))
        self.assertEqual(res.pl[:4], (100, 24, 380, 246))
        self.assertEqual(res.reads, 0)
        yb = nv12_of(0x00AAFF)[0]
        self.assertEqual(res.Y(0, 0), yb)
        self.assertEqual(res.Y(5, 100), yb)
        self.assertEqual(res.Y(6, 100), pic.Y(100 + 6, 24 + 100))
        self.assertEqual(res.Y(379, 245), yb)


# ---------------------------------------------------------------- 5. Deckkraft

class Opacity(unittest.TestCase, ModelMixin):
    def test_half_opacity_mixes_exactly_with_the_main_picture(self):
        pic = Pic.pattern(64, 40)
        frame = Frame(1920, 120, ys=1920 + 10, uvs=1920 + 4, seed=4)
        res = render(sty(op=50), pic=pic, frame=frame, at=(10, 6))
        for py in range(40):
            for px in range(64):
                self.assertLessEqual(abs(res.Y(px, py) - (frame.y[(6 + py) * frame.ys + 10 + px] + pic.Y(px, py)) / 2), 1)
        for m in range(20):
            for k in range(32):
                i = (3 + m) * frame.uvs + 10 + 2 * k
                pu, pv = pic.UV(k, m)
                self.assertLessEqual(abs(res.uv[i] - (frame.uv[i] + pu) / 2), 1)
                self.assertLessEqual(abs(res.uv[i + 1] - (frame.uv[i + 1] + pv) / 2), 1)
        self.check_model(res)

    def test_various_opacities_follow_the_model(self):
        for op in (1, 20, 33, 80, 99):
            res = render(sty(op=op), at=(10, 6))
            self.check_model(res)

    def test_main_picture_is_read_exactly_where_needed(self):
        res = render(sty(op=50), at=(10, 6))
        self.assertEqual(res.reads, 64 * 40 + 64 * 20)                      # nur das Rechteck des kleinen Bildes: Y und UV
        res = render(sty(op=99, bw=4, bo=30, bc=0x336699), at=(10, 6))
        self.assertEqual(res.reads, 64 * 40 + 64 * 20)
        # und das Ergebnis hängt dort wirklich vom Hauptbild ab
        ra, rb = (render(sty(op=50), frame=Frame(1920, 120, seed=s), at=(10, 6)) for s in (1, 2))
        self.assertNotEqual(ra.rect(), rb.rect())

    def test_zero_opacity_changes_nothing(self):
        pic = Pic.pattern(64, 40)
        frame = Frame(1920, 120, ys=1920 + 10, uvs=1920 + 4, seed=4)
        for text in ("op=0", "op=0,bw=5,bc=ff0000,bo=100,br=30", "op=-5"):
            y, uv, guards, reads = draw(text, frame, pic, (0, 0, 64, 40, 10, 6))      # direkt, ohne pb_place
            self.assertEqual((y, uv, guards, reads), (bytes(frame.y), bytes(frame.uv), 1, 0), text)
        self.assertIsNone(place("op=0", 1920, 120, 64, 40))

    def test_translucent_border_is_mixed_with_the_picture_then_with_the_main_picture(self):
        yb, ub, vb = nv12_of(0xFF8800)
        pic = Pic.pattern(64, 40)
        frame = Frame(1920, 120, seed=6)
        res = render(sty(op=60, bw=4, bc=0xFF8800, bo=50), pic=pic, frame=frame, at=(10, 6))
        d = frame.y[(6 + 1) * 1920 + 10 + 20]                               # Rahmenzeile 1, Spalte 20
        s = (pic.Y(20, 1) + yb) / 2
        self.assertLessEqual(abs(res.Y(20, 1) - (d * 0.4 + s * 0.6)), 1)
        d = frame.y[(6 + 20) * 1920 + 10 + 30]                              # Innen
        self.assertLessEqual(abs(res.Y(30, 20) - (d * 0.4 + pic.Y(30, 20) * 0.6)), 1)
        self.check_model(res)

    def test_opacity_in_a_crop(self):
        pic = Pic.pattern(480, 270)
        res = render(sty(op=70, cl=400, cb=100), pic=pic, mw=1920, mh=1080, at=(40, 30))
        self.assertEqual(res.pl[:4], (100, 0, 380, 246))                       # 100 * 270 / 1080 = 25 -> gerade 24
        self.assertEqual(res.reads, 380 * 246 * 3 // 2)
        self.check_model(res)


# ---------------------------------------------------------------- 6. Rundung

class Rounding(unittest.TestCase, ModelMixin):
    R, W, H = 12, 64, 40

    def flat(self, text, main, **kw):
        return render(text, pic=Pic.flat(self.W, self.H, 120, 100, 150), frame=Frame(1920, 120, flat=main), at=(10, 6), **kw)

    def test_corner_pixels_outside_the_shape_keep_the_main_picture(self):
        main = (235, 128, 128)
        res = self.flat(sty(bw=2, bc=0xFF0000, br=self.R), main)
        for px, py in ((0, 0), (63, 0), (0, 39), (63, 39), (1, 1), (0, 2), (2, 0), (62, 1), (1, 38)):
            self.assertEqual(res.Y(px, py), 235, (px, py))
        # Mitten der Kanten und die Mitte sind unberührt von der Rundung
        yb = nv12_of(0xFF0000)[0]
        for px, py in ((32, 0), (31, 0), (32, 39), (0, 20), (63, 20), (self.R, 0), (63 - self.R, 39), (0, self.R), (63, 39 - self.R)):
            self.assertEqual(res.Y(px, py), yb, (px, py))
        self.assertEqual(res.Y(32, 20), 120)
        self.check_model(res)

    def test_four_corners_are_symmetric(self):
        res = self.flat(sty(bw=3, bc=0xFF0000, br=15, bo=70), (235, 128, 128))
        for py in range(self.H):
            for px in range(self.W):
                v = res.Y(px, py)
                self.assertEqual((res.Y(63 - px, py), res.Y(px, 39 - py), res.Y(63 - px, 39 - py)), (v, v, v), (px, py))
        for m in range(20):
            for k in range(32):
                v = res.UV(k, m)
                self.assertEqual((res.UV(31 - k, m), res.UV(k, 19 - m), res.UV(31 - k, 19 - m)), (v, v, v), (k, m))

    def test_radius_is_limited_to_half_the_smaller_side(self):
        a = self.flat(sty(bw=2, br=200), (235, 128, 128)).rect()            # 64x40 -> höchstens 20
        for br in (20, 21, 40, 100):
            self.assertEqual(self.flat(sty(bw=2, br=br), (235, 128, 128)).rect(), a, br)
        self.assertNotEqual(self.flat(sty(bw=2, br=19), (235, 128, 128)).rect(), a)
        self.check_model(self.flat(sty(bw=2, br=200), (235, 128, 128)))
        # Radius und Rahmenbreite skalieren mit der Hauptbildbreite: bw=4/br=24 bei 960 breit = bw=2/br=12 bei 1920
        pic, main = Pic.pattern(64, 40), (100, 128, 128)                    # gleiches (flaches) Hauptbild, damit die Ecken vergleichbar sind
        self.assertEqual(render(sty(bw=4, br=24), mw=960, pic=pic, frame=Frame(960, 120, flat=main), at=(10, 6)).rect(),
                         render(sty(bw=2, br=12), mw=1920, pic=pic, frame=Frame(1920, 120, flat=main), at=(10, 6)).rect())

    def test_radius_without_border_rounds_the_picture_itself(self):
        """Issue #7: Die Rundung gilt für das Bild selbst, auch ohne Rahmen (Ecken weg, sonst unverändert)."""
        main = (235, 128, 128)
        res = self.flat(sty(br=self.R), main)
        for px, py in ((0, 0), (63, 0), (0, 39), (63, 39), (1, 1), (0, 2), (2, 0), (62, 1), (1, 38)):
            self.assertEqual(res.Y(px, py), 235, (px, py))                    # Eckpixel außerhalb der Form: Hauptbild bleibt
        for px, py in ((32, 0), (0, 20), (63, 20), (32, 39), (self.R, 0), (0, self.R), (32, 20)):
            self.assertEqual(res.Y(px, py), 120, (px, py))                    # Kanten, Mitte: das Bild, ohne Rahmenfarbe
        self.check_model(res)
        # ohne Rundung bleibt es beim reinen Kopieren (kein Lesen des Hauptbildes)
        pic = Pic.pattern(64, 40)
        self.assertEqual(render(sty(), pic=pic, at=(10, 6)).reads, 0)
        self.assertGreater(render(sty(br=30), pic=pic, at=(10, 6)).reads, 0)    # die weichen Kanten der Ecken lesen genau dort das Hauptbild
        # mit Deckkraft zusammen und mit Beschnitt
        self.check_model(render(sty(br=30, op=60), pic=pic, at=(10, 6)))
        self.check_model(render(sty(br=30, cl=400, ct=100), pic=pic, at=(10, 6)))

    def test_soft_edges_exist_and_only_the_corners_depend_on_the_main_picture(self):
        a, b = (235, 128, 128), (16, 60, 200)
        ra = self.flat(sty(bw=2, bc=0xFF0000, br=self.R), a)
        rb = self.flat(sty(bw=2, bc=0xFF0000, br=self.R), b)
        partial = [(px, py) for py in range(self.H) for px in range(self.W)
                   if ra.Y(px, py) != a[0] and ra.Y(px, py) != rb.Y(px, py)]
        self.assertGreater(len(partial), 8)                                  # weiche Kante: Übergangsbildpunkte vorhanden
        for px, py in partial:
            self.assertLess(min(px, 63 - px), self.R, (px, py))
            self.assertLess(min(py, 39 - py), self.R, (px, py))
        partial_uv = [(k, m) for m in range(20) for k in range(32)
                      if ra.UV(k, m) != (a[1], a[2]) and ra.UV(k, m) != rb.UV(k, m)]
        self.assertGreater(len(partial_uv), 2)
        for k, m in partial_uv:
            self.assertLess(min(2 * k, 62 - 2 * k), self.R)
            self.assertLess(min(2 * m, 38 - 2 * m), self.R)
        # gelesen werden nur die Eckquadrate: weit weniger als das ganze Bild
        self.assertGreater(ra.reads, 0)
        self.assertLessEqual(ra.reads, 4 * self.R * self.R + 4 * (self.R // 2) * (self.R // 2) * 2)
        self.assertLess(ra.reads, 64 * 40 * 3 // 2 // 3)

    def test_rounded_shapes_follow_the_model(self):
        pic = Pic.pattern(64, 40)
        for text in (sty(bw=2, br=12, bc=0x00FF88), sty(bw=5, br=8, bc=0x7F00FF, bo=60), sty(bw=1, br=30), sty(bw=3, br=3),
                     sty(bw=6, br=200, bo=0), sty(bw=4, br=12, op=70, bo=40, bc=0xFFFF00), sty(bw=9, br=11, op=90)):
            self.check_model(render(text, pic=pic, at=(10, 6)))

    def test_rounded_crop_and_larger_picture(self):
        pic = Pic.pattern(480, 270)
        res = render(sty(cl=300, cr=100, ct=50, bw=8, br=60, bc=0xFFFFFF, op=85), pic=pic, mw=1920, mh=1080, at=(200, 100))
        self.check_model(res, tol=1.0)


# ---------------------------------------------------------------- 7. Grenzen

class Bounds(unittest.TestCase, ModelMixin):
    STYLES = (sty(bw=3, br=7, bc=0xFF8800), sty(bw=5, br=200, bo=40, op=55), sty(bw=1, br=3, op=100), sty(op=33),
              sty(bw=60, br=150, bc=0x102030, bo=80), sty(cl=500, cb=300, bw=7, br=21, op=90), sty())

    def test_no_access_outside_the_picture(self):
        exe = _exe_san                                                       # mit Sanitizern, falls vorhanden
        for text in self.STYLES:
            for corner, fx, fy in ((5, 0, 0), (5, 1000, 1000), (5, 1000, 0), (5, 0, 1000), (5, 333, 777), (3, 0, 0), (0, 0, 0), (4, 0, 0)):
                for ys, uvs in ((96, 96), (110, 102)):
                    frame = Frame(96, 64, ys, uvs, seed=17)
                    pic = Pic.pattern(64, 40, seed=5)
                    res = render(text, pic=pic, frame=frame, corner=corner, fx=fx, fy=fy, exe=exe)
                    self.check_model(res)

    def test_plain_and_cropped_pictures_flush_at_the_edges(self):
        for text in (sty(), sty(cl=500, ct=300), sty(cr=900)):
            for corner, fx, fy in ((5, 0, 0), (5, 1000, 1000), (5, 1000, 0), (5, 0, 1000)):
                frame = Frame(96, 64, 100, 98, seed=3)
                res = render(text, pic=Pic.pattern(64, 40), frame=frame, corner=corner, fx=fx, fy=fy, exe=_exe_san)
                cx, cy, cw, ch, x, y = res.pl
                ey, euv = bytearray(frame.y), bytearray(frame.uv)
                pic = res.pic
                for r in range(ch):
                    ey[(y + r) * 100 + x:(y + r) * 100 + x + cw] = pic.y[(cy + r) * 64 + cx:(cy + r) * 64 + cx + cw]
                for r in range(ch // 2):
                    euv[(y // 2 + r) * 98 + x:(y // 2 + r) * 98 + x + cw] = pic.uv[(cy // 2 + r) * 64 + cx:(cy // 2 + r) * 64 + cx + cw]
                self.assertEqual((res.y, res.uv, res.guards), (bytes(ey), bytes(euv), 1), (text[0], corner, fx, fy))

    def test_odd_free_positions_stay_inside(self):
        # fx/fy ergeben ungerade Werte; pb_place macht sie gerade, das Bild bleibt vollständig im Hauptbild
        for fx in (1, 335, 501, 999, 1000):
            for fy in (1, 333, 777, 1000):
                res = render(sty(bw=3, br=9, op=80, bo=50), pic=Pic.pattern(64, 40), frame=Frame(1000, 70, 1006, 1002, seed=fx + fy),
                             corner=5, fx=fx, fy=fy, exe=_exe_san)
                cx, cy, cw, ch, x, y = res.pl
                self.assertEqual((x % 2, y % 2), (0, 0))
                self.assertLessEqual(x + cw, 1000)
                self.assertLessEqual(y + ch, 70)
                self.check_model(res)

    def test_random_cases_fast_path_equals_naive_reference(self):
        for seed in (1, 2, 3):
            out = run(["fuzz", seed, 700], _exe_san)
            self.assertTrue(out.startswith("fuzz ok"), out)
            ran = int(re.search(r"ran=(\d+)", out).group(1))
            general = int(re.search(r"general=(\d+)", out).group(1))
            self.assertGreater(ran, 300)
            self.assertGreater(general, 200)

    def test_fast_path_equals_naive_reference_on_the_example(self):
        text = "op=80,cl=400,cr=0,ct=0,cb=0,bw=6,bc=ff8800,bo=70,br=24"
        pic = Pic.pattern(480, 270, seed=2)
        frame = Frame(1920, 1080, ys=1920 + 6, uvs=1920 + 2, seed=8)
        pl = place(text, 1920, 1080, 480, 270, 3)
        fast = draw(text, frame, pic, pl, _exe_san)
        ref = draw(text, frame, pic, pl, _exe_san, mode="naive")
        self.assertEqual(fast[:3], ref[:3])
        self.assertEqual(fast[2], 1)


# ---------------------------------------------------------------- 8. Farbe

class Color(unittest.TestCase):
    def test_bt709_limited_range(self):
        colors = [0xFFFFFF, 0x000000, 0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0x00FFFF, 0xFF00FF, 0xFF8800, 0x808080, 0x7F7F7F, 0x010203]
        rnd = random.Random(5)
        colors += [rnd.getrandbits(24) for _ in range(120)]
        got = [tuple(map(int, l.split())) for l in run(["rgb"] + ["%06x" % c for c in colors]).splitlines()]
        self.assertEqual(got, [nv12_of(c) for c in colors])

    def test_known_values(self):
        for rgb, want in ((0xFFFFFF, (235, 128, 128)), (0x000000, (16, 128, 128)), (0xFF0000, (63, 102, 240)),
                          (0x00FF00, (173, 42, 26)), (0x0000FF, (32, 240, 118))):
            self.assertEqual(tuple(map(int, run(["rgb", "%06x" % rgb]).split())), want, "%06x" % rgb)
            self.assertEqual(nv12_of(rgb), want)


if __name__ == "__main__":
    unittest.main(verbosity=2)
