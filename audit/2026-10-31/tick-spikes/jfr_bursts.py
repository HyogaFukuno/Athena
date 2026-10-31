"""Athena bench - the long stretches of the server thread in a JFR recording made with a 1 ms
execution sample period (sg_local.py --jfr --jfr-period 1ms), and what ran in each.

    jfr_bursts.py <recording.jfr> [min ms, default 30]

A burst is a run of server-thread samples with no gap over 4 ms; one lasting at least <min ms> is a
tick (or a stretch of one) a player can feel. For each: its start (seconds of uptime, as gc.log),
length, the frames that most samples sat in (self) and the deepest frames shared by most samples
from org.bukkit / net.minecraft / net.arcana / athena code (where in the game it was). GC pauses
of 10 ms or more are listed too: during one the server thread is stopped and not sampled.
"""
import subprocess, sys, re, collections, datetime, json

JFR = "/Users/glacier/Library/Java/JavaVirtualMachines/graalvm-jdk-25/Contents/Home/bin/jfr"
path = sys.argv[1]; mn = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
out = subprocess.run([JFR, "print", "--json", "--stack-depth", "48", "--events", "jdk.ExecutionSample,jdk.GarbageCollection,jdk.JVMInformation", path],
                     capture_output=True, text=True).stdout
ev = json.loads(out)["recording"]["events"]
def ts(s):  # "2026-10-31T09:33:49.123456789Z" or "...49.123+09:00" -> unix seconds
    m = re.match(r"([^.]+)\.(\d+)(Z|[+-]\d\d:\d\d)?$", s)
    tz = (m.group(3) or "Z").replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(m.group(1) + tz).timestamp() + float("0." + m.group(2))
start = None
for e in ev:
    if e["type"] == "jdk.JVMInformation":
        start = ts(e["values"]["jvmStartTime"]); break
samples = []
gcs = []
for e in ev:
    v = e["values"]
    if e["type"] == "jdk.ExecutionSample":
        th = (v.get("sampledThread") or {}).get("javaName")
        if th != "Server thread": continue
        frames = ["%s.%s" % (f["method"]["type"]["name"].replace("/", "."), f["method"]["name"]) for f in (v["stackTrace"] or {}).get("frames", [])]
        samples.append((ts(v["startTime"]), frames))
    elif e["type"] == "jdk.GarbageCollection":
        d = float(v["longestPause"][:-2]) if v["longestPause"].endswith("ms") else 0
        if isinstance(v["longestPause"], str) and v["longestPause"].endswith(" s"): d = float(v["longestPause"][:-2]) * 1000
        gcs.append((ts(v["startTime"]), v["name"], v["cause"], v["longestPause"]))
samples.sort()
start = start or samples[0][0]
bursts, cur = [], []
for s in samples:
    if cur and s[0] - cur[-1][0] > 0.004:
        bursts.append(cur); cur = []
    cur.append(s)
if cur: bursts.append(cur)
GAME = re.compile(r"^(org\.bukkit|net\.minecraft|net\.arcana|athena|com\.hpfxd|org\.spigotmc|co\.aikar)")
for b in bursts:
    ms = (b[-1][0] - b[0][0]) * 1000 + 1
    if ms < mn: continue
    selfc = collections.Counter(f[0] for _, f in b if f)
    game = collections.Counter()
    for _, f in b:
        for fr in set(x for x in f if GAME.match(x)): game[fr] += 1
    print("burst at %.2f s (%s), %.0f ms, %d samples" % (b[0][0] - start, datetime.datetime.fromtimestamp(b[0][0]).strftime("%H:%M:%S.%f")[:-3], ms, len(b)))
    print("   self: " + "; ".join("%s %d" % kv for kv in selfc.most_common(6)))
    # frames present in at least 25% of the samples, deepest (least common) first
    common = [(fr, c) for fr, c in game.items() if c >= 0.25 * len(b)]
    common.sort(key=lambda kv: kv[1])
    print("   in:   " + "; ".join("%s %d" % kv for kv in common[:10]))
for t, name, cause, p in gcs:
    print("gc at %.2f s: %s (%s) longest pause %s" % (t - start, name, cause, p)) if (str(p).endswith("ms") and float(str(p)[:-3]) >= 10) or str(p).endswith(" s") else None
