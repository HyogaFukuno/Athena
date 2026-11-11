"""Compares sim_vps.py runs of the 2026-11-12 fixes (A2, B1, B3) against the build before them.

For each run: the server's CPU while every room fought, the world threads' share of it, the event
loop's CPU per packet (netstats.py's measure), how long each room's map took to preload, memory and
ticks. A run admits a different number of bots, so the CPU is also given per bot.

    python3 compare.py run/vps-out/TAG [...]
"""
import glob, json, os, re, statistics, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "2026-11-11", "c-net"))


def netty_per_packet(out):
    rx = tx = 0
    for f in glob.glob(os.path.join(out, "bot*.json")):
        r = json.load(open(f))
        if not r.get("ok"):
            continue
        rx += sum(v for k, v in r.get("rx", {}).items() if not k.endswith(" b"))
        tx += sum(r.get("sent", {}).values())
    first = last = None
    cur = {}
    for line in open(os.path.join(out, "samples.txt")):
        if line.startswith("S|"):
            if cur:
                first = first or dict(cur)
                last = dict(cur)
            cur = {}
        elif line.startswith("T|"):
            _, name, ticks = line.rstrip("\n").split("|")
            if name.startswith("Netty"):
                cur[name] = cur.get(name, 0) + int(ticks)
    if cur:
        last = dict(cur)
    netty = (sum(last.values()) - sum(first.values())) / 100.0
    return 1e6 * netty / max(1, rx + tx)


for out in sys.argv[1:]:
    r = json.load(open(os.path.join(out, "report.json")))
    full = r["full"]
    world = sum(v for k, v in full["threads"].items() if k.startswith("World thread"))
    preload = [int(m.group(1)) for m in re.finditer(r"Preloaded sg-\S+: \d+ chunks in (\d+) ms", open(os.path.join(out, "console.out"), errors="replace").read())]
    print("%-10s bots %3d | fighting: server %.3f cores (%.2f mcores/bot), world threads %.3f | Netty %.2f us/packet | preload %s ms (mean %.0f) | anon %.0f MiB, tps min %.2f, worst %d ms, errors %d"
          % (os.path.basename(out), r["bots_ok"], full["server_cores"], 1000 * full["server_cores"] / max(1, r["bots_ok"]), world,
             netty_per_packet(out), preload, statistics.mean(preload) if preload else 0, r["anon_peak_mib"], r["fighting_tps_min"],
             r["match_worst_ms"], r["log_warnings"]))
