"""Athena - redstone lamps switched a few per tick (feature/lamp-queue): Shady_Hollow's End, before and after.

SurvivalGames' End sets the match world's time to 400000 (night) for its fireworks. Shady_Hollow's street
lamps hang off daylight detectors, so they all switched on the same tick and that world stalled for 0.5 to
1.7 s (audit/2026-11-03/SG-ROOMS-PLAN.md). This runs the LampBench plugin (./lampbench) on a bare server:
a fresh copy of the map per round, the chunks around its centre loaded, noon, then setTime(400000), and
records the length of each tick and how many lamps switched in each tick.

    python3 lampbench.py JAR TAG --lamps 0 --stagger false     # vanilla
    python3 lampbench.py JAR TAG --lamps 4 --stagger true
    VPS_B_HOST=root@<address> python3 lampbench.py JAR TAG --lamps 4 --stagger true --vps --heap 128M

Production's configs (ArcanaServers vps-b/sg1) with athena.yml's two new keys set; the server runs natively,
or with --vps on VPS-B through production's /opt/mc/start-small.sh (its JVM flags, C1 only), in
/opt/mc/simbench with the AOT cache root /opt/mc/simbench-cache, so the live servers' cache is not touched.
Stop sg@sg1 and sg@sg2 first (lampbench_vps.sh does, and starts them again).
Writes run/lampbench-out/TAG/ (run/lampbench-vps-out/TAG/ with --vps): ticks.csv, summary.json, server.log.
"""
import argparse, csv, json, os, re, shutil, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CONF = "/Users/glacier/Documents/Minecraft/developments/ArcanaServers/vps-b/sg1"
MAP = "/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/server/maps/map_shady_hollow"

p = argparse.ArgumentParser()
p.add_argument("jar"); p.add_argument("tag")
p.add_argument("--lamps", type=int, required=True)            # redstoneLampsPerTick
p.add_argument("--stagger", choices=("true", "false"), required=True)
p.add_argument("--rounds", type=int, default=5)
p.add_argument("--radius", type=int, default=300)           # blocks; Shady_Hollow's radius in SG
p.add_argument("--template", choices=("true", "false"), default="true")
p.add_argument("--ticks", type=int, default=200)
p.add_argument("--heap", default="384M")
p.add_argument("--port", type=int, default=25690)
p.add_argument("--jfr", action="store_true")
p.add_argument("--vps", action="store_true")    # on VPS-B ($VPS_B_HOST) through production's start-small.sh; stop sg1/sg2 first
a = p.parse_args()
H = os.environ.get("VPS_B_HOST")
if a.vps and not H: sys.exit("set VPS_B_HOST to root@<VPS-B address>")
if a.vps:
    MAP = "/opt/mc/maps/map_shady_hollow"
D = "/opt/mc/simbench"                 # as sim_vps.py: its own directory and AOT cache root, never the live ones
CACHE = "/opt/mc/simbench-cache"
S = "simbench"
TMUX = "sudo -u mc tmux -L %s" % S

RUN = os.path.join(ROOT, "run", ("lampbench-vps-" if a.vps else "lampbench-") + a.tag)
OUT = os.path.join(ROOT, "run", "lampbench-vps-out" if a.vps else "lampbench-out", a.tag)
shutil.rmtree(RUN, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(RUN + "/plugins"); os.makedirs(OUT)


def edit(path, pairs):
    s = open(path).read()
    for pat, rep in pairs:
        s, n = re.subn(pat, rep, s, flags=re.M)
        if not n: sys.exit("%s: no %r" % (path, pat))
    open(path, "w").write(s)


for f in ("bukkit.yml", "spigot.yml", "paper.yml", "pandaspigot.yml", "athena.yml", "commands.yml", "help.yml", "permissions.yml", "server.properties"):
    shutil.copy(os.path.join(CONF, f), RUN)
open(RUN + "/eula.txt", "w").write("eula=true\n")
edit(RUN + "/server.properties", [(r"^server-port=.*", "server-port=%d" % a.port), (r"^server-ip=.*", "server-ip=127.0.0.1"),
                                  (r"^level-name=.*", "level-name=benchlobby"), (r"^level-type=.*", "level-type=FLAT"),
                                  (r"^allow-nether=.*", "allow-nether=false")])
edit(RUN + "/spigot.yml", [(r"^(\s*)bungeecord: true", r"\1bungeecord: false")])
edit(RUN + "/athena.yml", [(r"^(    asyncChunkDecoration: .*)$",
                            r"\1\n    redstoneLampsPerTick: %d\n    staggerDaylightDetectors: %s" % (a.lamps, a.stagger))])
subprocess.run([os.path.join(HERE, "lampbench", "build.sh"), RUN + "/plugins/lampbench.jar"], check=True)

PROPS = ["-Dlampbench.source=" + MAP, "-Dlampbench.rounds=%d" % a.rounds, "-Dlampbench.radius=%d" % a.radius,
         "-Dlampbench.template=" + a.template, "-Dlampbench.ticks=%d" % a.ticks]


def ssh(cmd, check=True, timeout=600):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", H, cmd], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        sys.exit("ssh failed (%d): %s\n%s%s" % (r.returncode, cmd[:300], r.stdout[-2000:], r.stderr[-2000:]))
    return r.stdout


def vps_run(props):
    """Starts the bench server on VPS-B and waits for it to stop. Returns its console."""
    start = ("cd %s && : > console.out && ATHENA_CACHE_ROOT=%s HEAP=%s JAVA_TOOL_OPTIONS=%s /opt/mc/start-small.sh 2>&1 | tee -a console.out"
             % (D, CACHE, a.heap, json.dumps(" ".join(props))))
    ssh("%s new -d -s %s %s" % (TMUX, S, json.dumps(start)))
    for _ in range(900):
        if not ssh("%s has-session -t %s 2>/dev/null && echo up; true" % (TMUX, S)).strip():
            return ssh("cat %s/console.out" % D, check=False)
        time.sleep(2)
    ssh("for p in $(pgrep -u mc -f '[o]rg.bukkit.craftbukkit.Main'); do [ \"$(readlink /proc/$p/cwd)\" = %s ] && kill $p; done; true" % D, check=False)
    sys.exit("the bench server did not finish in 30 minutes")


if a.vps:
    if ssh("%s has-session -t %s 2>/dev/null && echo up; true" % (TMUX, S)).strip():
        sys.exit("a simbench server is still running on the box")
    shutil.copy(os.path.abspath(a.jar), RUN + "/paperclip.jar")
    ssh("rm -rf %s; mkdir -p %s %s; chown mc:mc %s %s; [ -f %s/mojang_1.8.8.jar ] || cp /opt/mc/shared-cache/mojang_1.8.8.jar %s/" % (
        D, D, CACHE, D, CACHE, CACHE, CACHE))
    subprocess.run(["rsync", "-a", RUN + "/", "%s:%s/" % (H, D)], check=True)
    ssh("chown -R mc:mc %s %s" % (D, CACHE))
    # the first run of a new jar records the AOT cache (start-small.sh); measure again once it has one
    first = vps_run(PROPS + ["-Dlampbench.out=%s/ticks.csv" % D])
    if "no AOT cache yet" in first:
        print("that run recorded the AOT cache; measuring again with it", flush=True)
        ssh("rm -f %s/ticks.csv" % D)
        first = vps_run(PROPS + ["-Dlampbench.out=%s/ticks.csv" % D])
    open(OUT + "/server.log", "w").write(first)
    subprocess.run(["scp", "-q", "%s:%s/ticks.csv" % (H, D), OUT + "/ticks.csv"])
    rc = 0
else:
    cmd = ["java", "-Xms" + a.heap, "-Xmx" + a.heap, "-XX:+UseParallelGC"] + PROPS + ["-Dlampbench.out=" + OUT + "/ticks.csv"]
    if a.jfr:
        cmd.append("-XX:StartFlightRecording=settings=profile,filename=%s/server.jfr,dumponexit=true" % OUT)
    cmd += ["-jar", os.path.abspath(a.jar), "nogui"]
    with open(OUT + "/server.log", "w") as log:
        rc = subprocess.run(cmd, cwd=RUN, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=1800).returncode
logtext = open(OUT + "/server.log").read()
if not os.path.exists(OUT + "/ticks.csv"):
    sys.exit("no ticks.csv (exit %d); see %s/server.log" % (rc, OUT))

rows = list(csv.DictReader(open(OUT + "/ticks.csv")))
rounds = {}
for r in rows:
    rounds.setdefault(int(r["round"]), []).append(r)
summary = {"tag": a.tag, "redstoneLampsPerTick": a.lamps, "staggerDaylightDetectors": a.stagger == "true",
           "census": (re.findall(r"census: (.*)", logtext) or [""])[0].strip(), "rounds": []}
for n, rs in sorted(rounds.items()):
    gaps = [float(r["tick_ms"]) for r in rs]
    on = [int(r["lamps_on"]) for r in rs]
    lit_ticks = [int(r["tick"]) for r in rs if int(r["lamps_on"]) > 0]
    summary["rounds"].append({
        "round": n, "max_tick_ms": max(gaps), "ticks_over_50ms": sum(g > 50 for g in gaps),
        "median_tick_ms": statistics.median(gaps),
        "lamps_on": sum(on), "most_lamps_in_a_tick": max(on),
        "first_lamp_tick": lit_ticks[0] if lit_ticks else None, "last_lamp_tick": lit_ticks[-1] if lit_ticks else None,
        "top_gaps": sorted(((float(r["tick_ms"]), int(r["tick"]), int(r["lamps_on"])) for r in rs), reverse=True)[:5]})
mx = [r["max_tick_ms"] for r in summary["rounds"]]
summary["max_tick_ms_median"] = statistics.median(mx)
summary["max_tick_ms_max"] = max(mx)
summary["after_night"] = sorted(set(re.findall(r"after night \d+: (.*)", logtext)))
summary["after_day"] = sorted(set(re.findall(r"after day \d+: (.*)", logtext)))
summary["errors"] = len(re.findall(r"ERROR|Exception(?!InInitializerError)", logtext))
json.dump(summary, open(OUT + "/summary.json", "w"), indent=1)
print(json.dumps({k: v for k, v in summary.items() if k != "rounds"}, indent=1))
for r in summary["rounds"]:
    print("round %d: max %.1f ms, median %.2f ms, >50ms %d, lamps on %d (most %d in a tick, ticks %s-%s), top %s"
          % (r["round"], r["max_tick_ms"], r["median_tick_ms"], r["ticks_over_50ms"], r["lamps_on"], r["most_lamps_in_a_tick"],
             r["first_lamp_tick"], r["last_lamp_tick"], r["top_gaps"][:3]))
