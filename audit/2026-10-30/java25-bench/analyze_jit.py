"""Per match of a multi-match run (j25_vps.sh with J25_MATCHES): JIT and process CPU seconds (from
j25-threads.sh's .series), GC (gc.log via the run's JSON) and ticks over 50 ms (athena-diag.csv).
"start" is the server's start up to the first lobby.  analyze_jit.py <results dir> <tag>..."""
import sys, os, json, csv
d = sys.argv[1]
for tag in sys.argv[2:]:
    j = json.load(open(os.path.join(d, tag + ".json")))
    ser = [tuple(map(float, l.split())) for l in open(os.path.join(d, tag + ".threads.series")) if len(l.split()) == 3]
    diag = list(csv.DictReader(open(os.path.join(d, tag + ".diag.csv"))))
    starts = [t for t, ph in j["phases"] if ph == "lobby"]
    end = next((t for t, ph in reversed(j["phases"]) if ph == "cleanup"), ser[-1][0]) + 10
    edges = [ser[0][0]] + starts + [end]
    names = ["start"] + ["match%d" % (i + 1) for i in range(len(starts))]
    def at(t):  # the last sample at or before t
        s = [x for x in ser if x[0] <= t]
        return s[-1] if s else ser[0]
    print("%s  (rss peak %s MB, anon peak %s MB)" % (tag, j.get("rss_peak"), j.get("anon_peak")))
    print("  %-7s %6s %7s %7s %5s | %4s %4s %8s %8s | %s" % ("", "secs", "cpu s", "jit s", "jit%", "gcs", "full", "pause max", "after max", "ticks>50ms"))
    for name, t0, t1 in zip(names, edges, edges[1:]):
        a, b = at(t0), at(t1)
        cpu, jit = (b[2] - a[2]) / 100.0, (b[1] - a[1]) / 100.0
        gs = [g for g in j["gc"] if t0 <= g[0] < t1]
        over = sum(int(x["ticks_over_50ms"]) for x in diag if t0 < int(x["epoch_ms"]) / 1000.0 <= t1)
        print("  %-7s %6.0f %7.1f %7.1f %4.0f%% | %4d %4d %8.1f %8s | %d" % (name, t1 - t0, cpu, jit, 100 * jit / cpu if cpu else 0,
              len(gs), sum(1 for g in gs if g[4] == "Full"), max([g[3] for g in gs] or [0]), max([g[2] for g in gs] or [0]), over))
