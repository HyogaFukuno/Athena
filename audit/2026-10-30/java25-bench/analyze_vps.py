"""Per-phase numbers for the j25_vps.sh runs: from sg_match_load.py's JSON (CPU cores, RSS, anon,
TPS), athena-diag.csv (tick durations, 100 ticks a line) and gc.log (every pause, any collector).

    python3 analyze_vps.py results/vps
"""
import csv, glob, json, os, re, sys, datetime

d = sys.argv[1]
PHASES = ["lobby", "pregame", "live", "deathmatch"]


def gc_pauses(path):
    out = []
    for line in open(path, errors="replace"):
        m = re.match(r"\[([0-9T:.\-+]+)\]\s*GC\(\d+\) (Pause [A-Za-z ]+?)(?: \([^)]*\))*(?: [0-9]+M->[0-9]+M\([0-9]+M\))? ([0-9.]+)ms", line)
        if m:
            ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
            out.append((ts, m.group(2), float(m.group(3))))
    return out


rows = []
for js in sorted(glob.glob(os.path.join(d, "j25_*.json")), key=lambda p: int(os.path.basename(p).split("_")[1])):
    tag = os.path.basename(js)[:-5]
    r = json.load(open(js))
    phases = r["phases"]
    diag = list(csv.DictReader(open(os.path.join(d, tag + ".diag.csv")))) if os.path.exists(os.path.join(d, tag + ".diag.csv")) else []
    pauses = gc_pauses(os.path.join(d, tag + ".gc.log"))
    for k, (t0, ph) in enumerate(phases):
        t1 = phases[k + 1][0] if k + 1 < len(phases) else t0 + 60
        if ph not in PHASES:
            continue
        # a diag line covers the 100 ticks before it
        ds = [x for x in diag if t0 + 5 <= int(x["epoch_ms"]) / 1000.0 <= t1]
        ticks = sum(int(x["ticks"]) for x in ds)
        ps = [p for p in pauses if t0 <= p[0] <= t1]
        row = next((x for x in r["rows"] if x["phase"] == ph), {})
        rows.append({
            "run": tag, "phase": ph, "s": round(t1 - t0),
            "tps": row.get("tps"), "cores": row.get("cores"), "rss_max": row.get("rss_max"), "anon_max": row.get("anon_max"),
            "tick_mean_ms": round(sum(float(x["tick_ms_sum"]) for x in ds) / ticks, 2) if ticks else None,
            "tick_max_ms": max((float(x["tick_ms_max"]) for x in ds), default=None),
            "ticks_over_50": sum(int(x["ticks_over_50ms"]) for x in ds),
            "gc_pauses": len(ps), "gc_pause_max_ms": round(max((p[2] for p in ps), default=0), 1),
            "gc_pause_sum_ms": round(sum(p[2] for p in ps), 1),
            "full_or_degenerated": sum(1 for p in ps if "Full" in p[1] or "Degenerated" in p[1]),
        })
    rows.append({"run": tag, "phase": "ALL", "rss_max": r.get("rss_peak"), "anon_max": r.get("anon_peak"),
                 "gc_pause_max_ms": max((p[2] for p in pauses), default=0), "gc_pauses": len(pauses),
                 "full_or_degenerated": sum(1 for p in pauses if "Full" in p[1] or "Degenerated" in p[1]),
                 "bots_ok": r.get("bots_ok")})

keys = ["run", "phase", "s", "tps", "cores", "rss_max", "anon_max", "tick_mean_ms", "tick_max_ms", "ticks_over_50",
        "gc_pauses", "gc_pause_max_ms", "gc_pause_sum_ms", "full_or_degenerated", "bots_ok"]
with open(os.path.join(d, "summary.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, keys)
    w.writeheader()
    for x in rows:
        w.writerow({k: x.get(k) for k in keys})
for x in rows:
    print("  ".join("%s=%s" % (k, x.get(k)) for k in keys if x.get(k) is not None))
