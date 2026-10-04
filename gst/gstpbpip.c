/* IRL4YOU PIP: Bild-in-Bild-Baustein für GStreamer (NV12).
 *
 * Zwei Elemente, die sich einen kleinen Zwischenspeicher teilen (Eigenschaft slot 0, 1 oder 2,
 * so sind bis zu drei kleine Bilder gleichzeitig möglich; Platz 3 gehört der Hauptkamera beim Tausch):
 *
 *   pbpipsink  nimmt das KLEINE Bild entgegen (schon vom Hardware-Decoder auf z. B. 480x270
 *              verkleinert) und legt es in einem Ringspeicher ab.
 *   pbpipmix   steht im Hauptbild-Pfad und schreibt pro Hauptbild das nächste kleine Bild an
 *              die gewählte Ecke HINEIN. Das große Bild wird nie gelesen.
 *
 * Warum so: Auf dem RK3588 ist das Lesen von Decoder-Bildern durch die CPU extrem langsam
 * (gemessen: 1080p-Umwandlung ca. 9 Bilder/s). Schreiben in diesen Speicher ist schnell.
 * Ein Standard-Mischer (compositor) muss das große Bild lesen und schafft deshalb kein 30 fps.
 *
 * Der Ringspeicher gleicht Schübe des kleinen Bildes aus (Vorlauf von wenigen Bildern, zu alte
 * Bilder werden verworfen). Bleibt das kleine Bild länger als 2 Sekunden aus, wird nichts mehr
 * eingeblendet (statt eines eingefrorenen Bildes).
 *
 * Drittes Element pbctl: liest alle 0,3 s eine kleine Datei (bis zu vier Zahlen in Millisekunden: Hauptbild,
 * kleines Bild 1, 2 und 3) und stellt damit die Wartezeit (min-threshold-time) benannter queue-Elemente um.
 * So lassen sich die Verzögerungen im laufenden Betrieb ändern, ohne die Sendekette neu zu starten.
 *
 * Viertes Element pbpipsel (Tausch ohne Unterbrechung): wählt eines von bis zu vier Eingängen (jede Kamera hat
 * dann zwei Zweige: groß für das Hauptbild, klein für das Bild-in-Bild). Verworfen wird, was nicht gewählt ist;
 * die Zeitstempel laufen beim Umschalten lückenlos weiter (der Encoder sieht keinen Sprung). pbctl schaltet es
 * über eine kleine Datei um, pbpipmix erfährt über das Bild selbst, welche kleinen Bilder jetzt zu zeigen sind.
 *
 * MIT-Lizenz, Copyright (c) 2026 IRL4YOU. Eigene Umsetzung.
 */
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <glib/gstdio.h>
#include <gst/gst.h>
#include <gst/base/gstbasesink.h>
#include <gst/video/video.h>
#include <gst/video/gstvideofilter.h>

GST_DEBUG_CATEGORY_STATIC(pbpip_debug);
#define GST_CAT_DEFAULT pbpip_debug

#define RING_CAP 12              /* höchstens so viele kleine Bilder vorhalten */
#define PREBUFFER 3              /* Vorlauf, bevor das erste Bild gezeigt wird */
#define NSLOTS 4                 /* Plätze 0 bis 2: kleine Bilder 1 bis 3; Platz 3: kleines Bild der Hauptkamera (nur beim Tausch ohne Unterbrechung) */
#define STALE_US (2 * G_USEC_PER_SEC)

/* ------------------------------------------------------------------ gemeinsamer Speicher */

static GMutex ring_lock;
typedef struct {
  guint8 *slot[RING_CAP];
  gsize slot_size;
  guint rd, count;               /* Lesepunkt, Anzahl belegter Plätze */
  gint w, h;                     /* Größe des kleinen Bildes (dicht gepackt: Stride = w) */
  gboolean started;
  gint64 last_push;              /* Zeitpunkt des letzten kleinen Bildes */
  guint8 *cur;                   /* zuletzt gezeigtes Bild */
  gboolean have_cur;
  guint64 pushed, dropped, shown, repeated;
} Ring;
static Ring rings[NSLOTS];

static void ring_reset_locked(Ring *ring) {
  ring->rd = ring->count = 0;
  ring->started = FALSE;
  ring->have_cur = FALSE;
}

static void ring_resize_locked(Ring *ring, gint w, gint h) {
  gsize need = (gsize) w * h * 3 / 2;
  if (need != ring->slot_size) {
    for (guint i = 0; i < RING_CAP; i++) {
      g_free(ring->slot[i]);
      ring->slot[i] = g_malloc(need);
    }
    g_free(ring->cur);
    ring->cur = g_malloc(need);
    ring->slot_size = need;
  }
  ring->w = w;
  ring->h = h;
  ring_reset_locked(ring);
}

/* ------------------------------------------------------------------ pbpipsink */

typedef struct { GstBaseSink parent; GstVideoInfo info; gboolean have_info; guint slot; } PbPipSink;
typedef struct { GstBaseSinkClass parent_class; } PbPipSinkClass;
G_DEFINE_TYPE(PbPipSink, pb_pip_sink, GST_TYPE_BASE_SINK)

static GstStaticPadTemplate sink_tmpl = GST_STATIC_PAD_TEMPLATE(
    "sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static gboolean pb_sink_set_caps(GstBaseSink *bs, GstCaps *caps) {
  PbPipSink *self = (PbPipSink *) bs;
  if (!gst_video_info_from_caps(&self->info, caps))
    return FALSE;
  Ring *ring = &rings[self->slot];
  g_mutex_lock(&ring_lock);
  ring_resize_locked(ring, GST_VIDEO_INFO_WIDTH(&self->info), GST_VIDEO_INFO_HEIGHT(&self->info));
  g_mutex_unlock(&ring_lock);
  self->have_info = TRUE;
  GST_INFO_OBJECT(self, "kleines Bild %dx%d", ring->w, ring->h);
  return TRUE;
}

static GstFlowReturn pb_sink_render(GstBaseSink *bs, GstBuffer *buf) {
  PbPipSink *self = (PbPipSink *) bs;
  GstVideoFrame f;
  if (!self->have_info || !gst_video_frame_map(&f, &self->info, buf, GST_MAP_READ))
    return GST_FLOW_OK;
  Ring *ring = &rings[self->slot];
  const gint w = ring->w, h = ring->h;
  g_mutex_lock(&ring_lock);
  if (ring->count == RING_CAP) {                    /* voll: ältestes verwerfen */
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->dropped++;
  }
  guint8 *dst = ring->slot[(ring->rd + ring->count) % RING_CAP];
  const guint8 *y = GST_VIDEO_FRAME_PLANE_DATA(&f, 0), *uv = GST_VIDEO_FRAME_PLANE_DATA(&f, 1);
  const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(&f, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(&f, 1);
  for (gint r = 0; r < h; r++)
    memcpy(dst + (gsize) r * w, y + (gsize) r * sy, w);
  for (gint r = 0; r < h / 2; r++)
    memcpy(dst + (gsize) w * h + (gsize) r * w, uv + (gsize) r * suv, w);
  ring->count++;
  ring->pushed++;
  ring->last_push = g_get_monotonic_time();
  g_mutex_unlock(&ring_lock);
  gst_video_frame_unmap(&f);
  return GST_FLOW_OK;
}

static gboolean pb_sink_stop(GstBaseSink *bs) {
  PbPipSink *self = (PbPipSink *) bs;
  g_mutex_lock(&ring_lock);
  ring_reset_locked(&rings[self->slot]);
  g_mutex_unlock(&ring_lock);
  return TRUE;
}

static void pb_sink_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPipSink *self = (PbPipSink *) o;
  if (id == 1) self->slot = g_value_get_uint(v) % NSLOTS;
  else G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
}

static void pb_sink_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPipSink *self = (PbPipSink *) o;
  if (id == 1) g_value_set_uint(v, self->slot);
  else G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
}

static void pb_pip_sink_class_init(PbPipSinkClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  oc->set_property = pb_sink_set_property;
  oc->get_property = pb_sink_get_property;
  g_object_class_install_property(oc, 1,
      g_param_spec_uint("slot", "Platz", "welches kleine Bild (0 bis 2)", 0, NSLOTS - 1, 0,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstBaseSinkClass *bc = GST_BASE_SINK_CLASS(klass);
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Eingang", "Sink/Video",
      "Nimmt das kleine Bild für Bild-in-Bild entgegen", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &sink_tmpl);
  bc->set_caps = pb_sink_set_caps;
  bc->render = pb_sink_render;
  bc->stop = pb_sink_stop;
}

static void pb_pip_sink_init(PbPipSink *self) {
  gst_base_sink_set_sync(GST_BASE_SINK(self), FALSE);   /* sofort, ohne Taktabgleich */
  gst_base_sink_set_async_enabled(GST_BASE_SINK(self), FALSE);
  self->have_info = FALSE;
}

/* ------------------------------------------------------------------ pbpipmix */

/* PB_POS_BEGIN  (wird von tools/test_corner_pos.py einzeln übersetzt und geprüft) */
/* Position des kleinen Bildes: 0 oben links, 1 oben rechts, 2 unten links, 3 unten rechts, 4 unten Mitte,
 * 5 frei: fx/fy in Promille des Verschiebewegs (0 = Rand links/oben, 1000 = Rand rechts/unten), das Bild bleibt immer im Bild */
static void pb_pos(guint corner, gint mw, gint mh, gint pw, gint ph, gint margin, gint fx, gint fy, gint *x, gint *y) {
  if (corner == 5u) {
    if (fx < 0) fx = 0;
    if (fx > 1000) fx = 1000;
    if (fy < 0) fy = 0;
    if (fy > 1000) fy = 1000;
    *x = (gint) (((gint64) (mw - pw) * fx) / 1000);
    *y = (gint) (((gint64) (mh - ph) * fy) / 1000);
    if (*x < 0) *x = 0;
    if (*y < 0) *y = 0;
  } else if (corner == 4u) {
    *x = (mw - pw) / 2;
    *y = mh - ph - margin;
  } else {
    *x = (corner & 1u) ? mw - pw - margin : margin;
    *y = (corner & 2u) ? mh - ph - margin : margin;
  }
}
/* PB_POS_END */

typedef struct { GstVideoFilter parent; guint corner; guint slot; gint slot2; guint corner2; gint slot3; guint corner3; guint fx, fy, fx2, fy2, fx3, fy3; gint follow_tag; } PbPipMix;
typedef struct { GstVideoFilterClass parent_class; } PbPipMixClass;
G_DEFINE_TYPE(PbPipMix, pb_pip_mix, GST_TYPE_VIDEO_FILTER)

enum { PROP_0, PROP_CORNER, PROP_WIDTH_PCT, PROP_SLOT, PROP_SLOT2, PROP_CORNER2, PROP_SLOT3, PROP_CORNER3, PROP_X, PROP_Y, PROP_X2, PROP_Y2, PROP_X3, PROP_Y3, PROP_FOLLOW };

static GstStaticPadTemplate mix_sink = GST_STATIC_PAD_TEMPLATE(
    "sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate mix_src = GST_STATIC_PAD_TEMPLATE(
    "src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static void pb_mix_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPipMix *self = (PbPipMix *) o;
  switch (id) {
    case PROP_CORNER: g_atomic_int_set(&self->corner, MIN(g_value_get_uint(v), 5u)); break;
    case PROP_WIDTH_PCT: break;      /* nur zur Dokumentation; die Größe liefert der Decoder */
    case PROP_SLOT: self->slot = g_value_get_uint(v) % NSLOTS; break;
    case PROP_SLOT2: self->slot2 = g_value_get_int(v) < 0 ? -1 : g_value_get_int(v) % NSLOTS; break;
    case PROP_CORNER2: g_atomic_int_set(&self->corner2, MIN(g_value_get_uint(v), 5u)); break;
    case PROP_X: g_atomic_int_set(&self->fx, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_Y: g_atomic_int_set(&self->fy, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_X2: g_atomic_int_set(&self->fx2, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_Y2: g_atomic_int_set(&self->fy2, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_X3: g_atomic_int_set(&self->fx3, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_Y3: g_atomic_int_set(&self->fy3, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_SLOT3: self->slot3 = g_value_get_int(v) < 0 ? -1 : g_value_get_int(v) % NSLOTS; break;
    case PROP_CORNER3: g_atomic_int_set(&self->corner3, MIN(g_value_get_uint(v), 5u)); break;
    case PROP_FOLLOW: g_atomic_int_set(&self->follow_tag, g_value_get_boolean(v) ? 1 : 0); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_mix_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPipMix *self = (PbPipMix *) o;
  switch (id) {
    case PROP_CORNER: g_value_set_uint(v, g_atomic_int_get(&self->corner)); break;
    case PROP_WIDTH_PCT: g_value_set_uint(v, 0); break;
    case PROP_SLOT: g_value_set_uint(v, self->slot); break;
    case PROP_SLOT2: g_value_set_int(v, self->slot2); break;
    case PROP_CORNER2: g_value_set_uint(v, g_atomic_int_get(&self->corner2)); break;
    case PROP_X: g_value_set_uint(v, g_atomic_int_get(&self->fx)); break;
    case PROP_Y: g_value_set_uint(v, g_atomic_int_get(&self->fy)); break;
    case PROP_X2: g_value_set_uint(v, g_atomic_int_get(&self->fx2)); break;
    case PROP_Y2: g_value_set_uint(v, g_atomic_int_get(&self->fy2)); break;
    case PROP_X3: g_value_set_uint(v, g_atomic_int_get(&self->fx3)); break;
    case PROP_Y3: g_value_set_uint(v, g_atomic_int_get(&self->fy3)); break;
    case PROP_SLOT3: g_value_set_int(v, self->slot3); break;
    case PROP_CORNER3: g_value_set_uint(v, g_atomic_int_get(&self->corner3)); break;
    case PROP_FOLLOW: g_value_set_boolean(v, g_atomic_int_get(&self->follow_tag)); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

/* Zeichnet das kleine Bild aus Platz "slot" in das Hauptbild. Aufrufer hält ring_lock. */
static void pb_mix_draw(GstVideoFrame *frame, guint slot, guint corner, gint fx, gint fy) {
  Ring *ring = &rings[slot];
  if (ring->w <= 0 || ring->slot_size == 0)
    return;
  if (!ring->started && ring->count >= PREBUFFER)
    ring->started = TRUE;
  while (ring->count > PREBUFFER + 3) {             /* zu viel Vorrat: aufholen */
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->dropped++;
  }
  if (ring->started && ring->count > 0) {
    memcpy(ring->cur, ring->slot[ring->rd], ring->slot_size);
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->have_cur = TRUE;
    ring->shown++;
  } else if (ring->have_cur) {
    ring->repeated++;
  }
  const gint64 age = g_get_monotonic_time() - ring->last_push;
  const gboolean draw = ring->have_cur && age < STALE_US;
  const gint pw = ring->w, ph = ring->h;
  const gint mw = GST_VIDEO_FRAME_WIDTH(frame), mh = GST_VIDEO_FRAME_HEIGHT(frame);
  if (draw && pw <= mw && ph <= mh) {
    const gint margin = (mw / 60) & ~1;            /* ca. 2 % der Breite, gerade */
    gint x, y;
    pb_pos(corner, mw, mh, pw, ph, margin, fx, fy, &x, &y);
    x &= ~1;
    y &= ~1;
    if (x >= 0 && y >= 0) {
      guint8 *dy = GST_VIDEO_FRAME_PLANE_DATA(frame, 0), *duv = GST_VIDEO_FRAME_PLANE_DATA(frame, 1);
      const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 1);
      for (gint r = 0; r < ph; r++)
        memcpy(dy + (gsize) (y + r) * sy + x, ring->cur + (gsize) r * pw, pw);
      for (gint r = 0; r < ph / 2; r++)
        memcpy(duv + (gsize) (y / 2 + r) * suv + x, ring->cur + (gsize) pw * ph + (gsize) r * pw, pw);
    }
  }
}

/* Ein wartendes kleines Bild (gerade nicht gezeigt) auf den Vorlauf beschneiden: beim Tausch passt es dann sofort und
 * mit demselben Abstand zum Hauptbild wie im Dauerbetrieb. Aufrufer hält ring_lock. */
static void pb_mix_trim(guint slot) {
  Ring *ring = &rings[slot];
  while (ring->count > PREBUFFER) {
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->dropped++;
  }
}

static GstFlowReturn pb_mix_transform_ip(GstVideoFilter *filter, GstVideoFrame *frame) {
  PbPipMix *self = (PbPipMix *) filter;
  /* Tausch ohne Unterbrechung: pbpipsel schreibt in GST_BUFFER_OFFSET, welche Kamera Hauptbild ist und welche Kamera
   * an welcher Stelle (1 bis 3) kleiner gezeigt wird. Der Zustand reist so mit dem Bild und passt immer genau zu ihm. */
  const guint64 tag = GST_BUFFER_OFFSET(frame->buffer);
  const gboolean follow = g_atomic_int_get(&self->follow_tag) && (tag >> 60) == 9;
  g_mutex_lock(&ring_lock);
  if (follow) {
    const guint cn[3] = { g_atomic_int_get(&self->corner), g_atomic_int_get(&self->corner2), g_atomic_int_get(&self->corner3) };
    const gint fx[3] = { g_atomic_int_get(&self->fx), g_atomic_int_get(&self->fx2), g_atomic_int_get(&self->fx3) };
    const gint fy[3] = { g_atomic_int_get(&self->fy), g_atomic_int_get(&self->fy2), g_atomic_int_get(&self->fy3) };
    guint shown = 0;
    for (guint p = 0; p < 3; p++) {
      const guint cam = (guint) ((tag >> (4 * (p + 1))) & 0xF);
      if (cam > 3)
        continue;                                      /* 15: an dieser Stelle gibt es kein kleines Bild */
      const guint ring = (cam + 3) % NSLOTS;           /* Kamera 1 -> Platz 0, 2 -> 1, 3 -> 2, Hauptkamera 0 -> 3 */
      pb_mix_draw(frame, ring, cn[p], fx[p], fy[p]);
      shown |= 1u << ring;
    }
    for (guint r = 0; r < NSLOTS; r++)
      if (!(shown & (1u << r)))
        pb_mix_trim(r);
  } else {
    pb_mix_draw(frame, self->slot, g_atomic_int_get(&self->corner), g_atomic_int_get(&self->fx), g_atomic_int_get(&self->fy));
    if (self->slot2 >= 0)                              /* zweites kleines Bild im selben Durchgang (nur ein Mapping des Hauptbilds) */
      pb_mix_draw(frame, (guint) self->slot2, g_atomic_int_get(&self->corner2), g_atomic_int_get(&self->fx2), g_atomic_int_get(&self->fy2));
    if (self->slot3 >= 0)
      pb_mix_draw(frame, (guint) self->slot3, g_atomic_int_get(&self->corner3), g_atomic_int_get(&self->fx3), g_atomic_int_get(&self->fy3));
  }
  g_mutex_unlock(&ring_lock);
  return GST_FLOW_OK;
}

static void pb_pip_mix_class_init(PbPipMixClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstVideoFilterClass *vc = GST_VIDEO_FILTER_CLASS(klass);
  oc->set_property = pb_mix_set_property;
  oc->get_property = pb_mix_get_property;
  g_object_class_install_property(oc, PROP_CORNER,
      g_param_spec_uint("corner", "Ecke", "0 oben links, 1 oben rechts, 2 unten links, 3 unten rechts, 4 unten Mitte, 5 frei (x/y)",
                        0, 5, 3, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  g_object_class_install_property(oc, PROP_WIDTH_PCT,
      g_param_spec_uint("width-pct", "Breite in Prozent", "nur zur Information (Größe liefert der Decoder)",
                        0, 100, 0, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PROP_SLOT,
      g_param_spec_uint("slot", "Platz", "welches kleine Bild (0 bis 2)", 0, NSLOTS - 1, 0,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, PROP_SLOT2,
      g_param_spec_int("slot2", "Zweiter Platz", "zweites kleines Bild im selben Durchgang (-1 = keins)", -1, NSLOTS - 1, -1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, PROP_CORNER2,
      g_param_spec_uint("corner2", "Ecke 2", "Ecke des zweiten kleinen Bildes", 0, 5, 2,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  {
    static const struct { guint id; const gchar *name; const gchar *nick; } fp[] = {
      {PROP_X, "x", "X"}, {PROP_Y, "y", "Y"}, {PROP_X2, "x2", "X2"}, {PROP_Y2, "y2", "Y2"}, {PROP_X3, "x3", "X3"}, {PROP_Y3, "y3", "Y3"}};
    for (guint i = 0; i < G_N_ELEMENTS(fp); i++)
      g_object_class_install_property(oc, fp[i].id,
          g_param_spec_uint(fp[i].name, fp[i].nick, "freie Position in Promille des Verschiebewegs (nur bei Ecke 5)", 0, 1000, 0,
                            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  }
  g_object_class_install_property(oc, PROP_SLOT3,
      g_param_spec_int("slot3", "Dritter Platz", "drittes kleines Bild im selben Durchgang (-1 = keins)", -1, NSLOTS - 1, -1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, PROP_CORNER3,
      g_param_spec_uint("corner3", "Ecke 3", "Ecke des dritten kleinen Bildes", 0, 5, 0,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  g_object_class_install_property(oc, PROP_FOLLOW,
      g_param_spec_boolean("follow-tag", "Zustand aus dem Bild", "kleine Bilder nach dem Zustand zeigen, den pbpipsel ins Bild schreibt (Tausch ohne Unterbrechung)",
                           FALSE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Einblendung", "Filter/Effect/Video",
      "Schreibt das kleine Bild in das Hauptbild, ohne dieses zu lesen", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &mix_sink);
  gst_element_class_add_static_pad_template(ec, &mix_src);
  vc->transform_frame_ip = pb_mix_transform_ip;
}

static void pb_pip_mix_init(PbPipMix *self) {
  self->corner = 3;
  self->slot2 = -1;
  self->corner2 = 2;
  self->slot3 = -1;
  self->corner3 = 0;
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(self), TRUE);
}

/* ------------------------------------------------------------------ pbpipsel */

#define SEL_PADS 4
#define STATE_MARK G_GUINT64_CONSTANT(0x9000000000000000)

/* Wählt eines von bis zu vier Eingängen (Anfragen-Pads sink_0 bis sink_3, Nummer = Kamera). Alles, was nicht vom gewählten
 * Eingang kommt, wird sofort verworfen. Die Eigenschaft "state" ist eine Zahl: untere 4 Bit = gewählter Eingang, die
 * nächsten drei Viertelbytes = Kamera an Stelle 1 bis 3 des Bild-in-Bilds (15 = keine). Mit tag-offset schreibt das Element
 * den Zustand in GST_BUFFER_OFFSET jedes Bildes (Markierung 9 in den obersten 4 Bit), damit pbpipmix ihn zum Bild bekommt.
 *
 * Zeitstempel: beim Umschalten wird der Aufschlag so gewählt, dass das erste Bild des neuen Eingangs genau dort anschließt,
 * wo das letzte des alten endete. Segment und Datenstrom-Anfang sendet das Element selbst (einmal); die der Eingänge
 * werden verworfen. So sieht alles dahinter (Encoder, Muxer) einen einzigen, ununterbrochenen Strom. */
typedef struct {
  GstElement parent;
  GstPad *src;
  GstPad *sinks[SEL_PADS];
  GstCaps *caps[SEL_PADS];
  GMutex lock;
  guint state;                   /* atomar lesen/schreiben */
  gboolean tag_offset, force_key;
  gint sent_pad;                 /* Eingang, der zuletzt Daten durchgelassen hat (-1: noch keiner) */
  GstCaps *sent_caps;
  gboolean sent_start, sent_seg, resync;
  gint64 offset;                 /* wird auf die Zeitstempel des gewählten Eingangs aufgeschlagen */
  gboolean have_next;
  GstClockTime next_out, prev_pts;
  guint64 switches;
} PbPipSel;
typedef struct { GstElementClass parent_class; } PbPipSelClass;
G_DEFINE_TYPE(PbPipSel, pb_pip_sel, GST_TYPE_ELEMENT)

enum { SEL_0, SEL_STATE, SEL_TAG, SEL_KEY };

static GstStaticPadTemplate sel_sink_tmpl = GST_STATIC_PAD_TEMPLATE("sink_%u", GST_PAD_SINK, GST_PAD_REQUEST, GST_STATIC_CAPS_ANY);
static GstStaticPadTemplate sel_src_tmpl = GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS_ANY);

static void pb_sel_reset(PbPipSel *self) {
  g_mutex_lock(&self->lock);
  self->sent_pad = -1;
  self->sent_start = self->sent_seg = self->resync = self->have_next = FALSE;
  self->offset = 0;
  self->next_out = self->prev_pts = GST_CLOCK_TIME_NONE;
  gst_caps_replace(&self->sent_caps, NULL);
  for (guint i = 0; i < SEL_PADS; i++)
    gst_caps_replace(&self->caps[i], NULL);
  g_mutex_unlock(&self->lock);
}

/* Datenstrom-Anfang, Format und Segment vor dem ersten Puffer (und bei geändertem Format) nach hinten schicken. Aufrufer hält lock. */
static gboolean pb_sel_push_sticky_locked(PbPipSel *self, gint idx) {
  if (!self->sent_start) {
    gchar *sid = g_strdup_printf("pbpipsel/%p", (void *) self);
    GstEvent *ev = gst_event_new_stream_start(sid);
    g_free(sid);
    gst_event_set_group_id(ev, gst_util_group_id_next());
    if (!gst_pad_push_event(self->src, ev))
      return FALSE;
    self->sent_start = TRUE;
  }
  if (self->caps[idx] && (!self->sent_caps || !gst_caps_is_equal(self->sent_caps, self->caps[idx]))) {
    if (!gst_pad_push_event(self->src, gst_event_new_caps(self->caps[idx])))
      return FALSE;
    gst_caps_replace(&self->sent_caps, self->caps[idx]);
  }
  if (!self->sent_seg) {
    GstSegment seg;
    gst_segment_init(&seg, GST_FORMAT_TIME);
    if (!gst_pad_push_event(self->src, gst_event_new_segment(&seg)))
      return FALSE;
    self->sent_seg = TRUE;
  }
  return TRUE;
}

static GstFlowReturn pb_sel_chain(GstPad *pad, GstObject *parent, GstBuffer *buf) {
  PbPipSel *self = (PbPipSel *) parent;
  const gint idx = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(pad), "pbsel-idx"));
  if ((gint) (g_atomic_int_get(&self->state) & 0xF) != idx) {      /* nicht gewählt: sofort verwerfen, ohne zu warten */
    gst_buffer_unref(buf);
    return GST_FLOW_OK;
  }
  g_mutex_lock(&self->lock);
  const guint state = g_atomic_int_get(&self->state);
  if ((gint) (state & 0xF) != idx || !self->caps[idx]) {
    g_mutex_unlock(&self->lock);
    gst_buffer_unref(buf);
    return GST_FLOW_OK;
  }
  if (self->sent_pad != idx) {                                       /* Umschalten (oder erster Puffer) */
    if (!pb_sel_push_sticky_locked(self, idx)) {
      g_mutex_unlock(&self->lock);
      gst_buffer_unref(buf);
      return GST_FLOW_NOT_NEGOTIATED;
    }
    const GstClockTime pts = GST_BUFFER_PTS(buf);
    if (self->sent_pad >= 0) {
      if (self->have_next && GST_CLOCK_TIME_IS_VALID(pts))
        self->offset = (gint64) self->next_out - (gint64) pts;
      self->resync = TRUE;
      if (self->force_key)                                           /* Schnitt: der Encoder soll einen vollständigen Bildanfang setzen */
        gst_pad_push_event(self->src, gst_video_event_new_downstream_force_key_unit(
            GST_CLOCK_TIME_NONE, GST_CLOCK_TIME_NONE, GST_CLOCK_TIME_NONE, TRUE, 0));
      self->switches++;
    }
    self->sent_pad = idx;
    self->prev_pts = GST_CLOCK_TIME_NONE;
  } else if (!self->sent_caps || (self->caps[idx] && !gst_caps_is_equal(self->sent_caps, self->caps[idx]))) {
    pb_sel_push_sticky_locked(self, idx);                            /* Format hat sich beim gewählten Eingang geändert */
  }
  buf = gst_buffer_make_writable(buf);
  const GstClockTime pts = GST_BUFFER_PTS(buf), dts = GST_BUFFER_DTS(buf);
  if (GST_CLOCK_TIME_IS_VALID(pts)) {
    gint64 o = (gint64) pts + self->offset;
    if (o < 0)
      o = 0;
    GST_BUFFER_PTS(buf) = (GstClockTime) o;
    GstClockTime dur = 0;
    if (GST_BUFFER_DURATION_IS_VALID(buf))
      dur = GST_BUFFER_DURATION(buf);
    else if (GST_CLOCK_TIME_IS_VALID(self->prev_pts) && pts > self->prev_pts && pts - self->prev_pts < 200 * GST_MSECOND)
      dur = pts - self->prev_pts;
    self->prev_pts = pts;
    self->next_out = (GstClockTime) o + dur;
    self->have_next = TRUE;
  }
  if (GST_CLOCK_TIME_IS_VALID(dts)) {
    gint64 o = (gint64) dts + self->offset;
    GST_BUFFER_DTS(buf) = o < 0 ? 0 : (GstClockTime) o;
  }
  if (self->resync) {
    GST_BUFFER_FLAG_SET(buf, GST_BUFFER_FLAG_DISCONT);
    self->resync = FALSE;
  }
  if (self->tag_offset)
    GST_BUFFER_OFFSET(buf) = STATE_MARK | (guint64) (state & 0xFFFF);
  const GstFlowReturn ret = gst_pad_push(self->src, buf);
  g_mutex_unlock(&self->lock);
  return ret;
}

static gboolean pb_sel_event(GstPad *pad, GstObject *parent, GstEvent *event) {
  PbPipSel *self = (PbPipSel *) parent;
  const gint idx = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(pad), "pbsel-idx"));
  const gboolean act = (gint) (g_atomic_int_get(&self->state) & 0xF) == idx;
  gboolean ret = TRUE;
  switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_CAPS: {
      GstCaps *c = NULL;
      gst_event_parse_caps(event, &c);
      g_mutex_lock(&self->lock);
      gst_caps_replace(&self->caps[idx], c);
      g_mutex_unlock(&self->lock);                                   /* nach hinten geht es mit dem nächsten Puffer */
      gst_event_unref(event);
      break;
    }
    case GST_EVENT_EOS:
      if (act) {                                                     /* der gewählte Eingang ist zu Ende: Ende weiterreichen */
        g_mutex_lock(&self->lock);
        if (self->sent_pad < 0 && self->caps[idx])
          pb_sel_push_sticky_locked(self, idx);
        ret = gst_pad_push_event(self->src, event);
        g_mutex_unlock(&self->lock);
      } else {
        gst_event_unref(event);                                      /* wartende Kamera weg: stört die Sendung nicht */
      }
      break;
    case GST_EVENT_FLUSH_START:
      if (act) ret = gst_pad_push_event(self->src, event); else gst_event_unref(event);
      break;
    case GST_EVENT_FLUSH_STOP:
      if (act) {
        g_mutex_lock(&self->lock);
        self->sent_seg = FALSE;
        g_mutex_unlock(&self->lock);
        ret = gst_pad_push_event(self->src, event);
      } else {
        gst_event_unref(event);
      }
      break;
    default:                                                         /* Datenstrom-Anfang, Segment, Tags, Lücken usw. der Eingänge: eigene Regie */
      gst_event_unref(event);
      break;
  }
  return ret;
}

static GstPad *pb_sel_request_pad(GstElement *el, GstPadTemplate *templ, const gchar *name, const GstCaps *caps) {
  PbPipSel *self = (PbPipSel *) el;
  guint idx = 0;
  gchar tail = 0;
  if (!name || sscanf(name, "sink_%u%c", &idx, &tail) != 1 || idx >= SEL_PADS || self->sinks[idx])
    return NULL;
  GstPad *pad = gst_pad_new_from_template(templ, name);
  g_object_set_data(G_OBJECT(pad), "pbsel-idx", GINT_TO_POINTER((gint) idx));
  gst_pad_set_chain_function(pad, pb_sel_chain);
  gst_pad_set_event_function(pad, pb_sel_event);
  gst_pad_set_active(pad, TRUE);
  gst_element_add_pad(el, pad);
  self->sinks[idx] = pad;
  return pad;
}

static void pb_sel_release_pad(GstElement *el, GstPad *pad) {
  PbPipSel *self = (PbPipSel *) el;
  const gint idx = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(pad), "pbsel-idx"));
  if (idx >= 0 && idx < SEL_PADS && self->sinks[idx] == pad)
    self->sinks[idx] = NULL;
  gst_pad_set_active(pad, FALSE);
  gst_element_remove_pad(el, pad);
}

static GstStateChangeReturn pb_sel_change_state(GstElement *el, GstStateChange t) {
  if (t == GST_STATE_CHANGE_READY_TO_PAUSED)
    pb_sel_reset((PbPipSel *) el);
  return GST_ELEMENT_CLASS(pb_pip_sel_parent_class)->change_state(el, t);
}

static void pb_sel_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPipSel *self = (PbPipSel *) o;
  switch (id) {
    case SEL_STATE: g_atomic_int_set(&self->state, g_value_get_uint(v)); break;
    case SEL_TAG: self->tag_offset = g_value_get_boolean(v); break;
    case SEL_KEY: self->force_key = g_value_get_boolean(v); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_sel_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPipSel *self = (PbPipSel *) o;
  switch (id) {
    case SEL_STATE: g_value_set_uint(v, g_atomic_int_get(&self->state)); break;
    case SEL_TAG: g_value_set_boolean(v, self->tag_offset); break;
    case SEL_KEY: g_value_set_boolean(v, self->force_key); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_sel_finalize(GObject *o) {
  PbPipSel *self = (PbPipSel *) o;
  gst_caps_replace(&self->sent_caps, NULL);
  for (guint i = 0; i < SEL_PADS; i++)
    gst_caps_replace(&self->caps[i], NULL);
  g_mutex_clear(&self->lock);
  G_OBJECT_CLASS(pb_pip_sel_parent_class)->finalize(o);
}

static void pb_pip_sel_class_init(PbPipSelClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  oc->set_property = pb_sel_set_property;
  oc->get_property = pb_sel_get_property;
  oc->finalize = pb_sel_finalize;
  g_object_class_install_property(oc, SEL_STATE,
      g_param_spec_uint("state", "Zustand", "untere 4 Bit: gewählter Eingang; danach je 4 Bit: Kamera an Stelle 1, 2, 3 (15 = keine)",
                        0, G_MAXUINT, 0, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  g_object_class_install_property(oc, SEL_TAG,
      g_param_spec_boolean("tag-offset", "Zustand ins Bild schreiben", "Zustand in GST_BUFFER_OFFSET jedes Bildes eintragen (für pbpipmix)",
                           FALSE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, SEL_KEY,
      g_param_spec_boolean("force-key", "Bildanfang erzwingen", "beim Umschalten dem Encoder einen vollständigen Bildanfang nahelegen",
                           FALSE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Umschalter", "Generic",
      "Wählt einen von bis zu vier Eingängen, ohne den Datenstrom zu unterbrechen", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &sel_sink_tmpl);
  gst_element_class_add_static_pad_template(ec, &sel_src_tmpl);
  ec->request_new_pad = pb_sel_request_pad;
  ec->release_pad = pb_sel_release_pad;
  ec->change_state = pb_sel_change_state;
}

static void pb_pip_sel_init(PbPipSel *self) {
  g_mutex_init(&self->lock);
  self->sent_pad = -1;
  self->next_out = self->prev_pts = GST_CLOCK_TIME_NONE;
  self->src = gst_pad_new_from_static_template(&sel_src_tmpl, "src");
  gst_element_add_pad(GST_ELEMENT(self), self->src);
}

/* ------------------------------------------------------------------ pbctl */

typedef struct {
  GstElement parent;
  gchar *file, *video_queue, *audio_queue, *pip_queue, *pip2_queue, *pip3_queue;
  gchar *cam[4];                  /* Tausch ohne Unterbrechung: je Kamera eine Liste "queue:art,..." (v = Bild groß, s = Bild klein, a = Ton) */
  gchar *selector, *audio_selector, *select_file, *state_file;
  gint audio_pos;                 /* -1: Ton der Hauptkamera, 0 bis 2: Ton der Kamera an dieser Stelle */
  GThread *thread;
  volatile gint run;
  gint64 last_mtime, sel_mtime;
} PbCtl;
typedef struct { GstElementClass parent_class; } PbCtlClass;
G_DEFINE_TYPE(PbCtl, pb_ctl, GST_TYPE_ELEMENT)

enum { CTL_0, CTL_FILE, CTL_VQ, CTL_AQ, CTL_PQ, CTL_P2Q, CTL_P3Q, CTL_CAM0, CTL_CAM1, CTL_CAM2, CTL_CAM3,
       CTL_SEL, CTL_ASEL, CTL_SELFILE, CTL_STATEFILE, CTL_APOS };

/* Wartezeit einer benannten queue setzen. Hauptbild/Ton: Zeitlimit großzügig. Kleine Bilder: die queue hat
 * leaky=downstream und ein Zeitlimit; bei Verzögerung wird das Limit entsprechend angehoben. */
static void pb_ctl_set_queue(PbCtl *self, GstObject *par, const gchar *name, gint ms, gboolean small, gint extra) {
  if (!name || !*name || ms < 0 || ms > 3000)
    return;
  GstElement *q = gst_bin_get_by_name(GST_BIN(par), name);
  if (!q)
    return;
  /* Die queue gibt erst frei, wenn der Füllstand die Schwelle überschreitet: gemessen fehlt dabei ein Bild.
   * Beim Bild gleichen wir das mit extra (ein Bild = 33 ms) aus, beim Ton nicht (extra = 0). */
  gint th = ms ? ms + extra : 0;
  if (small)
    g_object_set(q, "max-size-time", (guint64) (th + 500) * GST_MSECOND, "max-size-buffers", (guint) (ms ? 0 : 30),
                 "min-threshold-time", (guint64) th * GST_MSECOND, NULL);
  else
    g_object_set(q, "max-size-time", (guint64) (th + 3000) * GST_MSECOND,
                 "min-threshold-time", (guint64) th * GST_MSECOND, NULL);
  gst_object_unref(q);
  GST_INFO_OBJECT(self, "%s: %d ms", name, ms);
}

/* Liste "queue:art,queue:art" einer Kamera abarbeiten */
static void pb_ctl_set_cam(PbCtl *self, GstObject *par, const gchar *spec, gint ms) {
  if (!spec || !*spec)
    return;
  gchar **items = g_strsplit(spec, ",", 0);
  for (gchar **it = items; *it; it++) {
    gchar *colon = strchr(*it, ':');
    if (!colon || !colon[1])
      continue;
    *colon = 0;
    if (colon[1] == 'v') pb_ctl_set_queue(self, par, *it, ms, FALSE, 33);
    else if (colon[1] == 's') pb_ctl_set_queue(self, par, *it, ms, TRUE, 33);
    else if (colon[1] == 'a') pb_ctl_set_queue(self, par, *it, ms, FALSE, 0);
  }
  g_strfreev(items);
}

static void pb_ctl_apply(PbCtl *self, gint main_ms, gint pip_ms, gint pip2_ms, gint pip3_ms) {
  GstObject *par = gst_object_get_parent(GST_OBJECT(self));
  if (!par)
    return;
  pb_ctl_set_queue(self, par, self->video_queue, main_ms, FALSE, 33);
  pb_ctl_set_queue(self, par, self->audio_queue, main_ms, FALSE, 0);
  pb_ctl_set_queue(self, par, self->pip_queue, pip_ms, TRUE, 33);
  pb_ctl_set_queue(self, par, self->pip2_queue, pip2_ms, TRUE, 33);
  pb_ctl_set_queue(self, par, self->pip3_queue, pip3_ms, TRUE, 33);
  const gint ms[4] = { main_ms, pip_ms, pip2_ms, pip3_ms };
  for (guint i = 0; i < 4; i++)
    pb_ctl_set_cam(self, par, self->cam[i], ms[i]);
  gst_object_unref(par);
}

/* Zustand in die Datei für die Oberfläche schreiben: "Hauptkamera Kamera-an-Stelle-1 -2 -3" */
static void pb_ctl_publish(PbCtl *self, const int v[4]) {
  if (!self->state_file || !*self->state_file)
    return;
  gchar *txt = g_strdup_printf("%d %d %d %d\n", v[0], v[1], v[2], v[3]);
  if (g_file_set_contents(self->state_file, txt, -1, NULL))
    g_chmod(self->state_file, 0644);
  g_free(txt);
}

/* Umschalten: Bild-Umschalter (und, falls vorhanden, Ton-Umschalter) auf den neuen Zustand stellen */
static void pb_ctl_apply_select(PbCtl *self, const int v[4]) {
  GstObject *par = gst_object_get_parent(GST_OBJECT(self));
  if (!par)
    return;
  const guint st = (guint) ((v[0] & 0xF) | ((v[1] & 0xF) << 4) | ((v[2] & 0xF) << 8) | ((v[3] & 0xF) << 12));
  GstElement *sel = self->selector ? gst_bin_get_by_name(GST_BIN(par), self->selector) : NULL;
  if (sel) {
    g_object_set(sel, "state", st, NULL);
    gst_object_unref(sel);
  }
  GstElement *asel = self->audio_selector ? gst_bin_get_by_name(GST_BIN(par), self->audio_selector) : NULL;
  if (asel) {
    const gint cam = self->audio_pos < 0 ? v[0] : v[1 + MIN(self->audio_pos, 2)];
    if (cam >= 0 && cam <= 3)
      g_object_set(asel, "state", (guint) cam, NULL);
    gst_object_unref(asel);
  }
  pb_ctl_publish(self, v);
  GST_INFO_OBJECT(self, "Umschalter: %d %d %d %d", v[0], v[1], v[2], v[3]);
  gst_object_unref(par);
}

/* Zustand aus der Datei übernehmen, sobald sie sich ändert; beim ersten Durchlauf ohne Datei den eingebauten Zustand melden */
static void pb_ctl_poll_select(PbCtl *self, gboolean first) {
  if (!self->selector || !*self->selector)
    return;
  struct stat st;
  if (self->select_file && stat(self->select_file, &st) == 0) {
    gint64 mt = (gint64) st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
    if (mt != self->sel_mtime) {
      self->sel_mtime = mt;
      int v[4] = { -1, 15, 15, 15 };
      FILE *f = fopen(self->select_file, "r");
      if (f) {
        if (fscanf(f, "%d %d %d %d", &v[0], &v[1], &v[2], &v[3]) < 1)
          v[0] = -1;
        fclose(f);
      }
      gboolean ok = v[0] >= 0 && v[0] <= 3;
      for (guint i = 1; i < 4; i++)
        ok = ok && v[i] >= 0 && v[i] <= 15;
      if (ok) {
        pb_ctl_apply_select(self, v);
        return;
      }
    }
  }
  if (first) {                                                     /* keine (brauchbare) Datei: Zustand des Umschalters melden */
    GstObject *par = gst_object_get_parent(GST_OBJECT(self));
    GstElement *sel = par ? gst_bin_get_by_name(GST_BIN(par), self->selector) : NULL;
    if (sel) {
      guint s = 0;
      g_object_get(sel, "state", &s, NULL);
      int v[4] = { (int) (s & 0xF), (int) ((s >> 4) & 0xF), (int) ((s >> 8) & 0xF), (int) ((s >> 12) & 0xF) };
      pb_ctl_publish(self, v);
      gst_object_unref(sel);
    }
    if (par)
      gst_object_unref(par);
  }
}

static void pb_ctl_poll_delay(PbCtl *self) {
  struct stat st;
  if (self->file && stat(self->file, &st) == 0) {
    gint64 mt = (gint64) st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
    if (mt != self->last_mtime) {
      self->last_mtime = mt;
      int v[4] = { -1, 0, 0, 0 };         /* Hauptbild, kleine Bilder 1 bis 3 (alte Datei: weniger Werte) */
      FILE *f = fopen(self->file, "r");
      if (f) {
        if (fscanf(f, "%d %d %d %d", &v[0], &v[1], &v[2], &v[3]) < 1)
          v[0] = -1;
        fclose(f);
      }
      if (v[0] >= 0)
        pb_ctl_apply(self, v[0], v[1], v[2], v[3]);
    }
  }
}

static gpointer pb_ctl_thread(gpointer data) {
  PbCtl *self = data;
  guint tick = 0;
  while (g_atomic_int_get(&self->run)) {
    if (tick % 3 == 0)                                             /* Verzögerung: alle 0,3 s */
      pb_ctl_poll_delay(self);
    pb_ctl_poll_select(self, tick == 0);                           /* Umschalten: alle 0,1 s */
    tick++;
    g_usleep(100000);
  }
  return NULL;
}

static GstStateChangeReturn pb_ctl_change_state(GstElement *el, GstStateChange t) {
  PbCtl *self = (PbCtl *) el;
  if (t == GST_STATE_CHANGE_PAUSED_TO_PLAYING && !self->thread) {
    self->last_mtime = 0;
    self->sel_mtime = 0;
    g_atomic_int_set(&self->run, 1);
    self->thread = g_thread_new("pbctl", pb_ctl_thread, self);
  }
  GstStateChangeReturn r = GST_ELEMENT_CLASS(pb_ctl_parent_class)->change_state(el, t);
  if ((t == GST_STATE_CHANGE_PLAYING_TO_PAUSED || t == GST_STATE_CHANGE_READY_TO_NULL) && self->thread) {
    g_atomic_int_set(&self->run, 0);
    g_thread_join(self->thread);
    self->thread = NULL;
  }
  return r;
}

static gchar **pb_ctl_str_slot(PbCtl *self, guint id) {
  switch (id) {
    case CTL_FILE: return &self->file;
    case CTL_VQ: return &self->video_queue;
    case CTL_AQ: return &self->audio_queue;
    case CTL_PQ: return &self->pip_queue;
    case CTL_P2Q: return &self->pip2_queue;
    case CTL_P3Q: return &self->pip3_queue;
    case CTL_CAM0: return &self->cam[0];
    case CTL_CAM1: return &self->cam[1];
    case CTL_CAM2: return &self->cam[2];
    case CTL_CAM3: return &self->cam[3];
    case CTL_SEL: return &self->selector;
    case CTL_ASEL: return &self->audio_selector;
    case CTL_SELFILE: return &self->select_file;
    case CTL_STATEFILE: return &self->state_file;
    default: return NULL;
  }
}

static void pb_ctl_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbCtl *self = (PbCtl *) o;
  if (id == CTL_APOS) {
    g_atomic_int_set(&self->audio_pos, g_value_get_int(v));
    return;
  }
  gchar **dst = pb_ctl_str_slot(self, id);
  if (!dst) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
    return;
  }
  g_free(*dst);
  *dst = g_value_dup_string(v);
}

static void pb_ctl_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbCtl *self = (PbCtl *) o;
  if (id == CTL_APOS) {
    g_value_set_int(v, g_atomic_int_get(&self->audio_pos));
    return;
  }
  gchar **src = pb_ctl_str_slot(self, id);
  if (!src) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
    return;
  }
  g_value_set_string(v, *src);
}

static void pb_ctl_finalize(GObject *o) {
  PbCtl *self = (PbCtl *) o;
  for (guint id = CTL_FILE; id <= CTL_STATEFILE; id++) {
    gchar **p = pb_ctl_str_slot(self, id);
    if (p)
      g_free(*p);
  }
  G_OBJECT_CLASS(pb_ctl_parent_class)->finalize(o);
}

static void pb_ctl_class_init(PbCtlClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  oc->set_property = pb_ctl_set_property;
  oc->get_property = pb_ctl_get_property;
  oc->finalize = pb_ctl_finalize;
  g_object_class_install_property(oc, CTL_FILE,
      g_param_spec_string("file", "Datei", "Datei mit der Verzögerung in Millisekunden", "/var/lib/pipbox/main-delay-ms",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_VQ,
      g_param_spec_string("video-queue", "Bild-Warteschlange", "Name der queue für das Bild", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_AQ,
      g_param_spec_string("audio-queue", "Ton-Warteschlange", "Name der queue für den Ton", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_PQ,
      g_param_spec_string("pip-queue", "Warteschlange kleines Bild 1", "Name der queue des ersten kleinen Bildes", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_P2Q,
      g_param_spec_string("pip2-queue", "Warteschlange kleines Bild 2", "Name der queue des zweiten kleinen Bildes", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_P3Q,
      g_param_spec_string("pip3-queue", "Warteschlange kleines Bild 3", "Name der queue des dritten kleinen Bildes", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  {
    static const struct { guint id; const gchar *name; } cp[] = { {CTL_CAM0, "cam0"}, {CTL_CAM1, "cam1"}, {CTL_CAM2, "cam2"}, {CTL_CAM3, "cam3"} };
    for (guint i = 0; i < G_N_ELEMENTS(cp); i++)
      g_object_class_install_property(oc, cp[i].id,
          g_param_spec_string(cp[i].name, "Warteschlangen der Kamera", "Liste name:art (v Bild groß, s Bild klein, a Ton), die zur Verzögerung dieser Kamera gehören", NULL,
                              G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  }
  g_object_class_install_property(oc, CTL_SEL,
      g_param_spec_string("selector", "Bild-Umschalter", "Name des pbpipsel für das Bild", NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_ASEL,
      g_param_spec_string("audio-selector", "Ton-Umschalter", "Name des pbpipsel für den Ton", NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_SELFILE,
      g_param_spec_string("select-file", "Umschalt-Datei", "Datei mit vier Zahlen: Hauptkamera und Kamera an Stelle 1 bis 3 (15 = keine)", "/var/lib/pipbox/main-select",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_STATEFILE,
      g_param_spec_string("state-file", "Zustandsdatei", "Hier meldet pbctl den tatsächlich eingestellten Zustand zurück", "/run/pipbox-send/swap-state",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_APOS,
      g_param_spec_int("audio-pos", "Ton von Stelle", "-1 Ton der Hauptkamera, 0 bis 2 Ton der Kamera an dieser Stelle", -1, 2, -1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Steuerung", "Generic",
      "Stellt Wartezeiten und den Umschalter im laufenden Betrieb um", "IRL4YOU");
  ec->change_state = pb_ctl_change_state;
}

static void pb_ctl_init(PbCtl *self) {
  self->thread = NULL;
  self->run = 0;
  self->last_mtime = 0;
  self->sel_mtime = 0;
  self->audio_pos = -1;
}

/* ------------------------------------------------------------------ Plugin */

static gboolean plugin_init(GstPlugin *plugin) {
  GST_DEBUG_CATEGORY_INIT(pbpip_debug, "pbpip", 0, "IRL4YOU PiP");
  return gst_element_register(plugin, "pbpipsink", GST_RANK_NONE, pb_pip_sink_get_type()) &&
         gst_element_register(plugin, "pbpipmix", GST_RANK_NONE, pb_pip_mix_get_type()) &&
         gst_element_register(plugin, "pbpipsel", GST_RANK_NONE, pb_pip_sel_get_type()) &&
         gst_element_register(plugin, "pbctl", GST_RANK_NONE, pb_ctl_get_type());
}

#ifndef PACKAGE
#define PACKAGE "irl4you-pip"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, pbpip,
                  "IRL4YOU Bild-in-Bild: schreibt das kleine Bild in das Hauptbild",
                  plugin_init, "0.1", "MIT/X11", "irl4you-pip", "https://github.com/IRL4YOU/irl4you-pip")
