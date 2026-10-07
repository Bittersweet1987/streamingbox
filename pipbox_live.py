"""Modus "alle Kameras immer bereit" mit der Technik von streamingbox: Compositor statt Umschalter, Kameras als Zweige derselben Pipeline.

Die Pipeline wird **einmal** gebaut und ändert sich bis zum Stopp der Sendung nie. Sie enthält immer alle Plätze (4 Kamerazweige sbf0..sbf3,
3 Rahmen, Compositor, Ton-Umschalter). Ob eine Kamera sendet, wer groß ist, wo die kleinen Bilder liegen, Ton und Stumm: alles Eigenschaften, die
dieses Modul per Steuerkanal (`belacoder -C`) im laufenden Betrieb setzt. Der Encoder sieht nur ein Format: `video/x-raw,NV12,1920x1080,30/1` hinter
dem Compositor (Systemspeicher). Ohne Kamera zeigt der Compositor Schwarz (Rahmen-Streifen aus `videotestsrc is-live=true` geben den Takt), der Ton
ist Stille (`audiotestsrc`).

Dieses Modul enthält die reine Rechnung (Geometrie, Pipeline-Text, Befehle) und den Regler `LiveController`, der die Rolle von
`pipbox_always.Controller` + `Feeders` übernimmt: gleiche Schnittstelle (`start`, `tick`, `stop`, `binder`), gleiche Rückmeldedateien
(`view-state`), so bleibt die Oberfläche unverändert. Nur Standardbibliothek.
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

import pipbox_always as pa

CANVAS_W, CANVAS_H, FPS = 1920, 1080, 30
SLOTS = 4
RINGS = 3                                   # je kleines Bild (Stelle 1..3) ein Rahmen aus 4 Streifen
PLACEHOLDER = "pipbox_unused_%d"            # Schlüssel eines Platzes ohne Kamera: sendet nie
RTMP_BASE = "rtmp://127.0.0.1:1935/publish"
RUN = "/run/pipbox-send"
CTL_FIFO = f"{RUN}/belacoder.ctl"
LIVE_STATS = f"{RUN}/belacoder-live.txt"
BIN = "/opt/pipbox/bin/belacoder"
BUFFER_MIN_MS = 600                         # Ausrichtungspuffer (belacoder -A)
FEED_COOLDOWN_S = 3.0
STATS_MAX_AGE_S = 4.0
Z_BIG = 1
COLOR_CAPS = "video/x-raw,format=NV12,colorimetry=bt709"
AUDIO_CAPS = "audio/x-raw,format=S16LE,rate=48000,channels=2"


# ------------------------------------------------------------------------------------------------ Unterstützung

_SUP = {"mtime": None, "ok": False}


def supported():
    """Kann der installierte belacoder die dynamischen Zweige sbf0..sbf7 (Version "sb10" oder neuer)? PIPBOX_LIVE=0/1 erzwingt die Antwort (Tests)."""
    force = os.environ.get("PIPBOX_LIVE")
    if force in ("0", "1"):
        return force == "1"
    try:
        mt = os.stat(BIN).st_mtime
        if _SUP["mtime"] != mt:
            out = subprocess.run([BIN, "-v"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"-sb(\d+)", out)
            _SUP.update(mtime=mt, ok=bool(m and int(m.group(1)) >= 10))
        return _SUP["ok"]
    except (OSError, subprocess.SubprocessError):
        return False


def enabled(cfg):
    """Gilt für diese (geprüfte) Einstellung die Engine "Compositor"? Nur bei Bild-in-Bild mit gesetztem Schalter und passendem belacoder."""
    return bool(cfg.get("type") == "pip" and cfg.get("always_ready") is True and cfg.get("main") and supported())


# ------------------------------------------------------------------------------------------------ Geometrie (wie der Baustein pbpipmix)

def pip_size(pct):
    """Breite/Höhe des kleinen Bildes vor dem Beschnitt (gerade, Breite durch 16 teilbar), 16:9 (wie server.pip_size)."""
    w = max(16, int(round(CANVAS_W * pct / 100.0 / 16.0)) * 16)
    h = int(round(w * 9 / 16.0 / 2.0)) * 2
    return w, h


def crop_px(st, sw, sh):
    """Beschnitt (links, rechts, oben, unten) in Pixeln des kleinen Bildes sw x sh. Die Werte der Einstellung gelten für 1920x1080, gerundet auf gerade
    Werte; bleiben weniger als 16x16 Pixel, gilt kein Beschnitt (wie pb_style_crop)."""
    c = st["crop"]
    cl, cr, ct, cb = c["l"], c["r"], c["t"], c["b"]
    if cl <= 0 and cr <= 0 and ct <= 0 and cb <= 0:
        return 0, 0, 0, 0
    l = ((cl * sw + 960) // 1920) & ~1
    r = ((cr * sw + 960) // 1920) & ~1
    t = ((ct * sh + 540) // 1080) & ~1
    b = ((cb * sh + 540) // 1080) & ~1
    if l == r == t == b == 0:
        return 0, 0, 0, 0
    w, h = (sw - l - r) & ~1, (sh - t - b) & ~1
    if w < 16 or h < 16:
        return 0, 0, 0, 0
    return l, r, t, b


def place(corner, mw, mh, pw, ph, margin, fx, fy):
    """Position des kleinen Bildes (wie pb_pos): 0 oben links, 1 oben rechts, 2 unten links, 3 unten rechts, 4 unten Mitte, 5 frei (Promille)."""
    if corner == 5:
        fx, fy = max(0, min(1000, fx)), max(0, min(1000, fy))
        x, y = (mw - pw) * fx // 1000, (mh - ph) * fy // 1000
        return max(0, x), max(0, y)
    if corner == 4:
        x, y = (mw - pw) // 2, mh - ph - margin
    else:
        x = mw - pw - margin if corner & 1 else margin
        y = mh - ph - margin if corner & 2 else margin
    return max(0, min(x, mw - pw)), max(0, min(y, mh - ph))


def small_geometry(c, k):
    """Alles über das kleine Bild an Stelle k (1..3): Größe nach dem Skalieren (sw, sh), Beschnitt, Ausschnitt (cw, ch) und Platz (x, y) im Ausgabebild,
    Deckkraft in Prozent, Rahmen. c ist eine geprüfte Einstellung (PipelineStore._safe_cfg)."""
    sfx = "" if k == 1 else str(k)
    pw, ph = pip_size(c["size_pct" + sfx])
    st = c["styles"][str(k)]
    # Beschnitt VOR dem Skalieren, in Bildpunkten der Quelle (Bezug 1920x1080; Quellen anderer Größe rechnet commands() um): ein Beschnitt nach dem
    # Skalieren ließ den Ausgang zeitweise sekundenlang stehen (gemessen), davor nie
    l, r, t, b = crop_px(st, 1920, 1080)
    cw, ch = ((1920 - l - r) * pw // 1920) & ~1, ((1080 - t - b) * ph // 1080) & ~1
    margin = (CANVAS_W // 60) & ~1
    x, y = place(c["corner" + sfx], CANVAS_W, CANVAS_H, cw, ch, margin, c["x" + sfx], c["y" + sfx])
    x, y = x & ~1, y & ~1
    br = st["border"]
    bw = 0
    if br["enabled"] and br["width"] > 0:
        bw = max(1, min(int(br["width"]), min(cw, ch) // 2))
    return {"sw": pw, "sh": ph, "crop": (l, r, t, b), "w": cw, "h": ch, "x": x, "y": y,
            "opacity": int(st["opacity"]) if st["visible"] else 0, "bw": bw,
            "bcolor": int(br["color"][1:], 16), "bopacity": int(br["opacity"])}


def portrait_geometry(c, k, g, dims):
    """Hochkantes Bild (Quelle höher als breit) als kleines Bild: der Rahmen behält das Seitenverhältnis der Quelle bei der Höhe des 16:9-Rahmens,
    kein Beschnitt (der gilt für Querformat). Gleiche Ecke bzw. Lage, nur mit der anderen Breite."""
    sfx = "" if k == 1 else str(k)
    h = g["sh"]
    w = (h * dims[0] // dims[1]) & ~1
    margin = (CANVAS_W // 60) & ~1
    x, y = place(c["corner" + sfx], CANVAS_W, CANVAS_H, w, h, margin, c["x" + sfx], c["y" + sfx])
    g2 = dict(g)
    g2.update(crop=(0, 0, 0, 0), w=w, h=h, x=x & ~1, y=y & ~1)
    if g["bw"]:
        g2["bw"] = max(1, min(g["bw"], min(w, h) // 2))
    return g2


def ring_rects(g):
    """Die vier Streifen des Rahmens liegen innen im Ausschnitt (wie im Baustein): oben, unten, links, rechts (x, y, w, h)."""
    x, y, w, h, bw = g["x"], g["y"], g["w"], g["h"], g["bw"]
    return [(x, y, w, bw), (x, y + h - bw, w, bw), (x, y + bw, bw, h - 2 * bw), (x + w - bw, y + bw, bw, h - 2 * bw)]


def fit_big(dims):
    """Das große Bild füllt das Ausgabebild; hat die Quelle ein anderes Seitenverhältnis, bleibt es erhalten (schwarze Balken). dims = (Breite, Höhe) oder None."""
    if not dims or not dims[0] or not dims[1]:
        return 0, 0, CANVAS_W, CANVAS_H
    aspect = dims[1] / float(dims[0])
    cav = CANVAS_H / float(CANVAS_W)
    if abs(aspect - cav) <= 0.01:
        return 0, 0, CANVAS_W, CANVAS_H
    if aspect > cav:
        w = int(CANVAS_H / aspect) & ~1
        return ((CANVAS_W - w) // 2) & ~1, 0, w, CANVAS_H
    h = int(CANVAS_W * aspect) & ~1
    return 0, ((CANVAS_H - h) // 2) & ~1, CANVAS_W, h


def slot_keys(c):
    """Die Schlüssel der vier Plätze beim Aufbau (leer = Platz ohne Kamera): Hauptbild, kleine Bilder 1 bis 3. Ein Schlüssel bleibt auf seinem Platz,
    solange er gewünscht ist (pa.Binder); der Aufbau und der Regler müssen dieselbe Zuordnung benutzen."""
    b = pa.Binder()
    b.update([k for k in (c.get("main", ""), c.get("pip", ""), c.get("pip2", ""), c.get("pip3", "")) if k])
    return list(b.slot_key)


def delays_by_key(c):
    """Verzögerung in ms je Kamera (sie gehört zur Kamera, nicht zum Platz)."""
    out = {}
    for pos, dk in (("main", "main_delay_ms"), ("pip", "pip_delay_ms"), ("pip2", "pip2_delay_ms"), ("pip3", "pip3_delay_ms")):
        if c.get(pos):
            out[c[pos]] = max(0, min(3000, int(c.get(dk, 0) or 0)))
    return out


def buffer_ms(c):
    """Ausrichtungspuffer: mindestens 600 ms und 300 ms mehr als die größte Verzögerung (sonst kommen verzögerte Bilder 'zu spät')."""
    return max(BUFFER_MIN_MS, max(delays_by_key(c).values() or [0]) + 300)


# ------------------------------------------------------------------------------------------------ Pipeline-Text

def build(c, base=RTMP_BASE):
    """Pipeline-Text für belacoder. c ist eine geprüfte Einstellung. Die Schlüssel sind geprüft (a-z, 0-9, -, _).

    Alle Zweige starten gestoppt (`#sb-absent`): die Sendung beginnt sofort mit Schwarz und Stille, jede Kamera legt sich dann darauf, sobald ihr
    Stream da ist (Regler: `feed sbf<N> on`)."""
    q = "queue max-size-time=10000000000 max-size-buffers=1000 max-size-bytes=41943040"
    keys = slot_keys(c)
    out = []
    # Ausgang: Compositor mit FESTEM Format, dann Encoder. Der Encoder sieht nie ein anderes Format.
    pads = []
    for s in range(SLOTS):
        pads.append(f"sink_{s}::alpha=0 sink_{s}::zorder={Z_BIG}")
    n = SLOTS
    for k in range(RINGS):
        for j in range(4):
            pads.append(f"sink_{n}::xpos=0 sink_{n}::ypos=0 sink_{n}::width=2 sink_{n}::height=2 sink_{n}::alpha=0 sink_{n}::zorder={3 + 2 * k}")
            n += 1
    out.append("compositor name=pipcomp background=black " + " ".join(pads) + " !\n"
               f"video/x-raw,format=NV12,width={CANVAS_W},height={CANVAS_H},framerate={FPS}/1,colorimetry=bt709 !\n"
               "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n"
               "mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
               f"h265parse config-interval=-1 ! {q} ! mux.\n")
    # Ton-Umschalter VOR den Zweigen (GStreamer löst asel.sink_N nur auf, wenn asel schon im Text steht)
    out.append("input-selector name=asel ! identity name=a_delay signal-handoffs=TRUE ! volume name=avol ! "
               f"opusenc bitrate=128000 ! opusparse ! {q} ! mux.\n")
    # Ton: Stille ist immer da (Pad 0), die Kameras kommen dazu
    out.append(f"audiotestsrc is-live=true wave=silence ! capsfilter caps=\"{AUDIO_CAPS}\" ! queue ! asel.sink_0\n")
    # Rahmen-Streifen: lebendige Quellen in genau der Streifengröße (der Compositor skaliert nie); sie sind auch der Takt des Compositors
    n = SLOTS
    for k in range(RINGS):
        for j in range(4):
            i = k * 4 + j
            out.append(f"videotestsrc name=sbb{i} is-live=true pattern=solid-color foreground-color=4294967295 !\n"
                       f"capsfilter name=sbb{i}s caps=\"video/x-raw,format=NV12,width=2,height=2,framerate={FPS}/1,colorimetry=bt709\" !\n"
                       f"queue max-size-buffers=2 leaky=downstream ! pipcomp.sink_{n}\n")
            n += 1
    # Kamerazweige: alle Elemente heißen sbf<N>_..., belacoder stoppt und startet sie mit `feed sbf<N> off|on`
    for s in range(SLOTS):
        key = keys[s] or PLACEHOLDER % s
        nm = f"sbf{s}"
        out.append(
            f"rtmpsrc name={nm}_src location={base}/{key} ! flvdemux name={nm}\n"
            f"{nm}.video ! queue name={nm}_vq max-size-time=4000000000 max-size-buffers=400 ! h264parse name={nm}_parse ! "
            f"mppvideodec name={nm}_dec ! "
            f"queue name={nm}_lq max-size-buffers=45 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
            f"videorate name={nm}_vr max-duplication-time=100000000 ! capsfilter name={nm}_c1 caps=\"video/x-raw,framerate={FPS}/1\" ! "
            f"videocrop name={nm}_crop ! "
            # Der Skalierer (nearest-neighbour, billig) bringt das Bild vor dem Compositor auf genau die Größe seines Pads; die Caps (scc) werden bei
            # jedem Tausch live gesetzt. Skalieren im Compositor ist dagegen zu teuer (große Pads: Ausgang fällt auf 5 Bilder/s).
            f"videoscale name={nm}_sc method=nearest-neighbour ! "
            f"capsfilter name={nm}_scc caps=\"video/x-raw,format=NV12,width={CANVAS_W},height={CANVAS_H}\" ! "
            f"capssetter name={nm}_c2 join=true replace=false caps=\"video/x-raw,colorimetry=bt709\" ! "   # nur beschriften, nie umrechnen (eine Umrechnung kostete zeitweise einen ganzen Kern)
            
            f"queue name={nm}_q ! pipcomp.sink_{s}\n"
            f"{nm}.audio ! queue name={nm}_aq max-size-time=2000000000 ! aacparse name={nm}_ap ! avdec_aac name={nm}_ad ! "
            f"audioconvert name={nm}_ac1 ! audioresample name={nm}_ar ! capsfilter name={nm}_ac2 caps=\"{AUDIO_CAPS}\" ! "
            f"queue name={nm}_aq2 ! asel.sink_{s + 1}\n")
    out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
    out.append("\n#sb-absent: " + " ".join(f"sbf{s}" for s in range(SLOTS)) + "\n")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ Layout und Befehle

def scale_caps(w, h):
    return f"video/x-raw,format=NV12,width={w},height={h}"


def strip_caps(w, h):
    return f"video/x-raw,format=NV12,width={w},height={h},framerate={FPS}/1,colorimetry=bt709"


def argb(rgb):
    return (0xff000000 | rgb) & 0xffffffff


class Layout:
    """Rechnet aus Einstellung, Rollen und Zustand die Befehle für den Steuerkanal. Merkt sich, was schon gesendet ist, und gibt nur Änderungen aus."""

    def __init__(self):
        self.sent = {}

    def reset(self):
        self.sent = {}

    def commands(self, c, slot_key, roles, dims, audio_ok, hide, audio_pos, mute, delay_ms):
        """roles: {Platz: Stelle} nur für Kameras, deren Bilder gerade da sind (Stelle 0 groß, 1..3 klein). dims: {Platz: (Breite, Höhe)}.
        audio_ok: Plätze, deren Zweig Ton hat. hide: Bitmaske der ausgeblendeten Stellen (Bit 0 = Stelle 1). audio_pos: -1 Hauptbild, 0..2 kleines Bild.
        Gibt (Zeilen, Zustand für die Statistik) zurück. Reihenfolge: Das Bild, das oben landet, wird zuletzt bewegt (nie ein Bild mit Loch)."""
        want = []                                 # (Schlüssel, Zeile, z)
        geoms = {k: small_geometry(c, k) for k in (1, 2, 3)}
        by_pos = {v: s for s, v in roles.items()}
        for k in (1, 2, 3):                       # hochkantes Bild: eigener Rahmen (Seitenverhältnis bleibt)
            d = dims.get(by_pos.get(k)) if by_pos.get(k) is not None else None
            if d and d[0] and d[1] and d[1] > d[0]:
                geoms[k] = portrait_geometry(c, k, geoms[k], d)

        def add(key, line, z):
            want.append((key, line, z))

        for s in range(SLOTS):
            nm = f"sbf{s}"
            pos = roles.get(s)
            if pos is None:                       # nicht im Bild: ausblenden, Geometrie bleibt stehen
                add(("a", s), f"pad pipcomp sink_{s} alpha 0", -1)
                continue
            if pos == 0:
                x, y, w, h = fit_big(dims.get(s))
                crop, sw, sh, alpha, z = (0, 0, 0, 0), w, h, 1.0, Z_BIG
            else:
                g = geoms[pos]
                x, y, w, h = g["x"], g["y"], g["w"], g["h"]
                sw0, sh0 = dims.get(s) or (1920, 1080)
                sw0, sh0 = sw0 or 1920, sh0 or 1080
                crop = tuple(int(v * k // 1920) & ~1 for v, k in zip(g["crop"], (sw0, sw0, sw0, sw0)))
                crop = (crop[0], crop[1], int(g["crop"][2] * sh0 // 1080) & ~1, int(g["crop"][3] * sh0 // 1080) & ~1)
                sw, sh = g["w"], g["h"]
                op = 0 if (hide >> (pos - 1)) & 1 else g["opacity"]
                alpha, z = op / 100.0, 2 * pos
            for name, v in zip(("left", "right", "top", "bottom"), crop):
                add(("crop", s, name), f"set {nm}_crop {name} {v}", z)
            add(("caps", s), f"set {nm}_scc caps {scale_caps(sw, sh)}", z)
            add(("x", s), f"pad pipcomp sink_{s} xpos {x}", z)
            add(("y", s), f"pad pipcomp sink_{s} ypos {y}", z)
            add(("w", s), f"pad pipcomp sink_{s} width {w}", z)
            add(("h", s), f"pad pipcomp sink_{s} height {h}", z)
            add(("z", s), f"pad pipcomp sink_{s} zorder {z}", z)
            add(("a", s), f"pad pipcomp sink_{s} alpha {alpha:.2f}", z)
        # Rahmen der kleinen Bilder
        for k in (1, 2, 3):
            g = geoms[k]
            s = by_pos.get(k)
            shown = s is not None and g["bw"] > 0 and g["opacity"] > 0 and not (hide >> (k - 1)) & 1
            rects = ring_rects(g) if shown else [(0, 0, 2, 2)] * 4
            alpha = (g["bopacity"] / 100.0) * (g["opacity"] / 100.0) if shown else 0.0
            for j in range(4):
                i = (k - 1) * 4 + j
                n = SLOTS + i
                rx, ry, rw, rh = rects[j]
                rw, rh = max(2, rw), max(2, rh)
                z = 2 * k + 1
                add(("rc", i), f"set sbb{i} foreground-color {argb(g['bcolor'])}", z)
                add(("rcaps", i), f"set sbb{i}s caps {strip_caps(rw, rh)}", z)
                add(("rx", i), f"pad pipcomp sink_{n} xpos {rx}", z)
                add(("ry", i), f"pad pipcomp sink_{n} ypos {ry}", z)
                add(("rw", i), f"pad pipcomp sink_{n} width {rw}", z)
                add(("rh", i), f"pad pipcomp sink_{n} height {rh}", z)
                add(("ra", i), f"pad pipcomp sink_{n} alpha {alpha:.2f}", z)
        # Ton und Stumm
        asel = 0
        src = by_pos.get(0) if audio_pos < 0 else by_pos.get(audio_pos + 1)
        if src is not None and src in audio_ok:
            asel = src + 1
        add(("asel",), f"select asel sink_{asel}", 100)
        add(("mute",), f"set avol mute {'true' if mute else 'false'}", 100)
        # Verzögerung je Kamera (komprimierte Seite des Zweigs)
        for s in range(SLOTS):
            d = delay_ms.get(slot_key[s] or "", 0)
            add(("delay", s), f"set sbf{s}_vq min-threshold-time {(d + 33) * 1000000 if d else 0}", 100)
        # nach z sortiert (ausgeblendete zuerst), die Reihenfolge innerhalb eines Bildes bleibt (stabil)
        want.sort(key=lambda t: t[2])
        lines = []
        for key, line, _ in want:
            if self.sent.get(key) != line:
                lines.append(line)
                self.sent[key] = line
        return lines


# ------------------------------------------------------------------------------------------------ Hilfen für den Regler

def read_stats(path=LIVE_STATS, now=None):
    """Zustand der Zweige aus der Statistik von belacoder: {Platz: 1 gestoppt | 2 läuft, kein Bild | 3 Bilder kommen}. Leer, wenn die Datei fehlt oder alt ist."""
    try:
        if (now if now is not None else time.time()) - os.stat(path).st_mtime > STATS_MAX_AGE_S:
            return {}
        with open(path) as f:
            text = f.readline()
    except OSError:
        return {}
    return {int(m.group(1)): int(m.group(2)) for m in re.finditer(r" feed_sbf(\d)=(\d)", text)}


def parse_rtmp_stats(xml):
    """Aus der nginx-Statistik: {Schlüssel: {"w":…, "h":…, "audio": bool}} der Kameras, die gerade senden."""
    out = {}
    for m in re.finditer(r"<stream>.*?</stream>", xml, re.S):
        s = m.group(0)
        n = re.search(r"<name>([^<]+)</name>", s)
        if not n or "<publishing/>" not in s:
            continue
        w, h = re.search(r"<width>(\d+)</width>", s), re.search(r"<height>(\d+)</height>", s)
        out[n.group(1)] = {"w": int(w.group(1)) if w else 0, "h": int(h.group(1)) if h else 0, "audio": "<audio>" in s}
    return out


def fetch_publishing(url="http://127.0.0.1:1936/"):
    try:
        return parse_rtmp_stats(urllib.request.urlopen(url, timeout=2).read().decode(errors="replace"))
    except (OSError, ValueError):
        return None


def send_control(lines, path=CTL_FIFO):
    """Zeilen an belacoder. O_NONBLOCK: ohne laufenden belacoder scheitert das sofort (ENXIO) statt zu blockieren. Wahr bei Erfolg."""
    if not lines:
        return True
    fd = None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        data = ("\n".join(lines) + "\n").encode()
        for i in range(0, len(data), 3000):          # die FIFO nimmt 4096 Byte je Schreibvorgang an
            os.write(fd, data[i:i + 3000])
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def big_cpus(cpu_dir="/sys/devices/system/cpu"):
    """Nummern der schnellen Kerne (big.LITTLE über cpu_capacity), [] wenn alle gleich schnell sind."""
    cap = {}
    try:
        for d in os.listdir(cpu_dir):
            m = re.fullmatch(r"cpu(\d+)", d)
            if m:
                try:
                    cap[int(m.group(1))] = int(open(f"{cpu_dir}/{d}/cpu_capacity").read().strip())
                except (OSError, ValueError):
                    pass
    except OSError:
        return []
    if not cap or max(cap.values()) == min(cap.values()):
        return []
    top = max(cap.values())
    return sorted(c for c, v in cap.items() if v >= top * 0.9)


class CpuBoost:
    """Governor `performance` für die schnellen Kerne, solange gemischt wird; danach zurück. Der Zustand liegt in einer Datei, damit ein
    Absturz den Governor nicht "hängen lässt" (beim nächsten Start wird zurückgestellt)."""

    def __init__(self, cpu_dir="/sys/devices/system/cpu", state=f"{RUN}/cpugov.json", log=print):
        self.cpu_dir, self.state, self.log = cpu_dir, state, log
        self.saved = {}

    def _policy_dirs(self):
        seen, out = set(), []
        for c in big_cpus(self.cpu_dir):
            p = f"{self.cpu_dir}/cpu{c}/cpufreq"
            try:
                rel = tuple(sorted(int(x) for x in open(p + "/related_cpus").read().split()))
            except (OSError, ValueError):
                rel = (c,)
            if rel not in seen:
                seen.add(rel)
                out.append(p)
        return out

    def on(self):
        for p in self._policy_dirs():
            try:
                cur = open(p + "/scaling_governor").read().strip()
                if cur != "performance" and "performance" in open(p + "/scaling_available_governors").read().split():
                    with open(p + "/scaling_governor", "w") as f:
                        f.write("performance")
                    self.saved[p] = cur
            except OSError as e:
                self.log(f"send: Takt der schnellen Kerne nicht umstellbar ({e.__class__.__name__})")
        if self.saved:
            try:
                os.makedirs(os.path.dirname(self.state), exist_ok=True)
                with open(self.state, "w") as f:
                    json.dump(self.saved, f)
            except OSError:
                pass
            self.log("send: schnelle Kerne auf vollem Takt (performance), solange gemischt wird")

    def off(self):
        saved = self.saved
        if not saved:
            try:
                saved = json.load(open(self.state))
            except (OSError, ValueError):
                saved = {}
        for p, g in saved.items():
            try:
                with open(p + "/scaling_governor", "w") as f:
                    f.write(str(g))
            except OSError:
                pass
        self.saved = {}
        try:
            os.unlink(self.state)
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ Regler

class LiveController:
    """Hält im Betrieb die Zweige und das Bild auf dem Stand der Einstellung und der Kameras. Schnittstelle wie pipbox_always.Controller.

    tick() etwa alle 0,5 s: (1) Einstellung lesen (bei Änderung; Plätze zuordnen, ein Platz mit neuem Schlüssel bekommt ihn ohne Neustart:
    `feed off`, `set sbf<N>_src location …`), (2) nginx fragen, wer sendet, (3) Zweige starten/stoppen (`feed sbf<N> on|off`, 3 s Abkühlzeit),
    (4) Zustand der Zweige aus der Statistik von belacoder lesen (3 = Bilder kommen), (5) Wahl von Hauptbild und kleinen Bildern (pa.Chooser),
    (6) die geänderten Befehle für Bildflächen, Rahmen, Ton, Stumm und Verzögerung senden."""

    def __init__(self, load_json, put_state_file, state_dir, safe_cfg, view_values, log=print, clock=time.monotonic,
                 fetch=fetch_publishing, stats=read_stats, control=send_control, rtmp_base=RTMP_BASE):
        self.load_json, self.put, self.state_dir = load_json, put_state_file, state_dir
        self.safe_cfg, self.view_values = safe_cfg, view_values
        self.log, self.clock, self.fetch, self.stats, self.control, self.base = log, clock, fetch, stats, control, rtmp_base
        self.binder = pa.Binder()
        self.chooser = pa.Chooser(clock)
        self.layout = Layout()
        self.cfg = {}
        self.desired = ["", "", "", ""]
        self.inactive = []
        self.cfg_sig = None
        self.cmd_at = {}
        self.pub = {}
        self.pub_known = False
        self.pub_t = -10.0
        self.view_sig = object()                  # erste Auswertung immer
        self.view = (0, -1, 0)
        self.main_key = None
        self.last_line = None
        self.states = {}
        self.alive = set()
        self.reassign_lines = []
        self.started = False
        self._view_pending = False

    # -- Einstellung
    def reload(self):
        cfg = self.load_json("pipeline.json")
        sig = repr(sorted((k, repr(v)) for k, v in cfg.items()))
        if sig == self.cfg_sig:
            return False
        self.cfg_sig = sig
        c = self.safe_cfg({**{"type": "single", "main": ""}, **cfg})
        self.cfg = c
        self.desired = [c.get(k, "") if isinstance(c.get(k, ""), str) and pa.KEY_RE.match(c.get(k, "") or "") else "" for k in ("main", "pip", "pip2", "pip3")]
        ina = c.get("inactive")
        self.inactive = [k for k in ina if isinstance(k, str)] if isinstance(ina, list) else []
        before = list(self.binder.slot_key)
        self.binder.update([k for k in self.desired if k])
        for s in range(SLOTS):
            if self.started and before[s] != self.binder.slot_key[s]:
                key = self.binder.slot_key[s] or PLACEHOLDER % s
                # anderer Schlüssel an diesem Platz: Zweig stoppen, Adresse setzen (nur im gestoppten Zustand möglich), danach startet der Regler ihn
                self.reassign_lines += [f"feed sbf{s} off", f"set sbf{s}_src location {self.base}/{key}"]
                self.cmd_at[s] = 0.0
                self.log(f"send: Platz {s}: Kamera {before[s] or '-'} -> {self.binder.slot_key[s] or '-'} (ohne Neustart)")
        return True

    def read_view(self):
        """Ansicht im Betrieb: Datei main-view ("ausgeblendet Ton stumm", schreibt die Oberfläche); fehlt sie, gilt die Einstellung."""
        path = os.path.join(self.state_dir, "main-view")
        try:
            mt = os.stat(path).st_mtime_ns
        except OSError:
            mt = None
        if mt != self.view_sig:
            self.view_sig = mt
            try:
                with open(path) as f:
                    hide, audio, mute = (int(x) for x in f.read().split()[:3])
            except (OSError, ValueError):
                hide, audio = self.view_values(self.cfg) if self.cfg else (0, -1)
                mute = 0
            self.view = (hide, audio, 1 if mute else 0)
            return True
        return False

    # -- Ablauf
    def start(self):
        self.reload()
        self.read_view()
        self.started = True
        self.layout.reset()

    def stop(self):
        pass

    def on_restart(self):
        """belacoder wurde neu gestartet (z. B. Stall-Wächter): der neue Prozess kennt nichts von dem, was vorher live gesendet wurde (alle Bilder
        ausgeblendet, Adressen wie beim Aufbau). Alles noch einmal senden, sonst bliebe das Bild schwarz, obwohl die Kameras da sind."""
        self.layout.reset()
        self.cmd_at.clear()
        self.view_sig = object()
        self.states = {}
        self._view_pending = True
        self.reassign_lines = [f"set sbf{s}_src location {self.base}/{self.binder.slot_key[s] or PLACEHOLDER % s}" for s in range(SLOTS)]

    def _cooldown_ok(self, s, now):
        return now - self.cmd_at.get(s, -100.0) > FEED_COOLDOWN_S

    def tick(self):
        now = self.clock()
        self.reload()
        view_changed = self.read_view()
        reassign = self.reassign_lines
        self.reassign_lines = []
        lines = list(reassign)
        if now - self.pub_t >= 1.0:
            got = self.fetch()
            self.pub_t = now
            if got is not None:
                self.pub, self.pub_known = got, True
        self.states = self.stats()
        slot_key = self.binder.slot_key
        for s in range(SLOTS):
            key, st = slot_key[s], self.states.get(s)
            if st is None or not self.pub_known:
                continue
            # eine deaktivierte Kamera (Doppelklick) ist nicht im Stream: ihr Zweig läuft gar nicht (Hauptbild ist nie deaktiviert)
            want_on = bool(key) and key in self.pub and not (key in self.inactive and key != self.desired[0])
            cmd = None
            if want_on and st == 1:
                cmd = "on"
            elif not want_on and st >= 2:
                cmd = "off"
            if cmd and self._cooldown_ok(s, now):
                self.cmd_at[s] = now
                lines.append(f"feed sbf{s} {cmd}")
                self.log(f"send: Kamera {key or '-'} (Platz {s}): {cmd}")
        self.alive = {slot_key[s] for s in range(SLOTS) if slot_key[s] and self.states.get(s) == 3 and slot_key[s] in self.pub
                      and not (slot_key[s] in self.inactive and slot_key[s] != self.desired[0])}
        slot_of = {k: i for i, k in enumerate(slot_key) if k}
        line, main = self.chooser.line(self.desired, slot_of, self.alive, self.inactive)
        if line != self.last_line:
            if main != self.main_key and self.main_key is not None:
                self.log(f"send: Hauptbild jetzt {main} (eingestellt: {self.desired[0]})")
            self.main_key, self.last_line = main, line
        nums = [int(x) for x in line.split()]
        roles = {}
        if main in slot_of and main in self.alive:
            roles[slot_of[main]] = 0
        for k in (1, 2, 3):
            if nums[k] != 15 and slot_key[nums[k]] in self.alive and nums[k] not in roles:
                roles[nums[k]] = k
        dims = {s: (self.pub.get(slot_key[s], {}).get("w", 0), self.pub.get(slot_key[s], {}).get("h", 0)) for s in range(SLOTS) if slot_key[s]}
        audio_ok = {s for s in range(SLOTS) if slot_key[s] and self.pub.get(slot_key[s], {}).get("audio")}
        hide, apos, mute = self.view
        if apos >= 0 and self.desired[apos + 1] in self.inactive:
            apos = -1                                 # deaktivierte Kamera: nie die Tonquelle, es gilt der Ton des Hauptbildes
        if self.cfg:
            lines += self.layout.commands(self.cfg, slot_key, roles, dims, audio_ok, hide, apos, mute, delays_by_key(self.cfg))
        if lines and os.environ.get("PIPBOX_LIVE_DEBUG"):
            self.log("live: " + " | ".join(lines)[:1500])
        ok = self.control(lines)
        if not ok:
            self.layout.reset()                       # belacoder noch nicht bereit: beim nächsten Durchgang alles neu senden
            self.reassign_lines = reassign + self.reassign_lines
            self._view_pending = self._view_pending or view_changed
        else:
            self._confirm_view(view_changed or self._view_pending)
            self._view_pending = False
        # Anzeige (Fußleiste am Handy): auch deaktivierte Kameras, die senden, behalten ihren Knopf; im Stream sind sie nicht (self.alive)
        # (jede Kamera, die an die Box sendet, behält ihren Knopf, auch während ihr Zweig gerade startet oder deaktiviert ist)
        shown = set(self.alive) | {k for k in slot_key if k and k in self.pub}
        return {"slots": list(slot_key), "alive": sorted(shown), "main": self.main_key, "line": self.last_line}

    def _confirm_view(self, force):
        """Rückmeldung wie früher pbctl: /run/pipbox-send/view-state = was gerade gilt. Die Oberfläche wartet auf eine neue Änderungszeit der Datei
        (auch bei gleichem Inhalt), darum wird bei jeder Änderung von main-view neu geschrieben."""
        hide, apos, mute = self.view
        text = f"{hide} {apos} {mute}\n"
        if not force and getattr(self, "_conf_text", None) == text:
            return
        try:
            tmp = f"{RUN}/view-state.tmp"
            with open(tmp, "w") as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, f"{RUN}/view-state")
            self._conf_text = text
        except OSError:
            pass
