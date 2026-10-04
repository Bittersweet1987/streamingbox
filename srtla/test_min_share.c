/* Prüfprogramm für den Mindestanteil (srtla_send-min-share.patch): bindet srtla_send.c ein und ruft select_conn() mit künstlichen Wegen auf.
   Bauen im gepatchten Checkout von BELABOX/srtla (Commit 37862da, beide Patches angewendet), unter Linux:
     gcc -DVERSION=\"t\" -o test_min_share test_min_share.c common.c && ./test_min_share 10
   Erwartet bei 10: gut 80 %, mittel 10 %, schlecht 10 %, weit (900 ms, ausserhalb des Abstands) 0 %; ohne Mindestanteil (0): gut 100 %. */
#define main srtla_main
#include "srtla_send.c"
#undef main
#include <stdio.h>
static conn_t *mk(const char *name, double rtt, int window, int inflight) {
  conn_t *c = calloc(1, sizeof(conn_t));
  c->window = window; c->in_flight_pkts = inflight; c->srtt_ms = rtt; c->rttvar_ms = 2; c->peak_ms = rtt;
  uint64_t n; get_ms(&n); c->srtt_at_ms = n; c->peak_at_ms = n; c->elig = 1;
  time_t t; get_seconds(&t); c->last_rcvd = t;
  c->next = conns; conns = c; return c;
}
int main(int argc, char **argv) {
  int pct = argc > 1 ? atoi(argv[1]) : 10; min_share_pct = pct;
  lat_margin_ms = 300;
  conn_t *good = mk("gut", 1, 20000, 5), *mid = mk("mittel", 45, 8000, 5), *bad = mk("schlecht", 70, 6000, 5);
  conn_t *far = mk("weit", 900, 20000, 5);   /* ausserhalb des Laufzeitabstands: bekommt nichts */
  for (int i = 0; i < 10000; i++) {
    conn_t *c = select_conn();
    if (c) { c->in_flight_pkts = 5; }
  }
  unsigned long tot = good->sent_total + mid->sent_total + bad->sent_total + far->sent_total;
  printf("min_share=%d%%: gut %.1f%%, mittel %.1f%%, schlecht %.1f%%, weit(900 ms) %.1f%%\n", pct,
    100.0*good->sent_total/tot, 100.0*mid->sent_total/tot, 100.0*bad->sent_total/tot, 100.0*far->sent_total/tot);
  /* Weg ohne freies Fenster bekommt keinen Mindestanteil */
  bad->in_flight_pkts = bad->window + 10; unsigned long b0 = bad->sent_total, t0 = good->sent_total + mid->sent_total + bad->sent_total;
  for (int i = 0; i < 5000; i++) { conn_t *c = select_conn(); if (c && c != bad) c->in_flight_pkts = 5; }
  unsigned long t1 = good->sent_total + mid->sent_total + bad->sent_total;
  printf("voller Weg: %lu von %lu Paketen\n", bad->sent_total - b0, t1 - t0);
  return 0;
}
