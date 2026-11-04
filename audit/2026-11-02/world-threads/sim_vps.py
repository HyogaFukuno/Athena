"""Athena - SG-like matches, several at once in one JVM, on the real box (ConoHa VPS-B, 2 vCPU / 1 GB).

sim_local.py's bench moved to the box the live SG servers run on: the same MatchSim plugin, configs
(ArcanaServers vps-b/sg1, changed only where the bench needs it), map and bots, but the server runs
on VPS-B through production's own start script (/opt/mc/start-small.sh: its JVM flags and its AOT
cache), in /opt/mc/simbench with a cache root of its own (/opt/mc/simbench-cache), so the live
servers' AOT key in /opt/mc/shared-cache is never touched. The server listens on 127.0.0.1 only; the
bots run here and reach it through an ssh tunnel (sshd's CPU is sampled apart from the server's).

A heap the cache root has not seen yet is warmed first, as j25-warm.sh did: one start that records
the AOT configuration (stopped once it is up), then the measured start builds the cache and uses it.

Every 3 s a sampler on the box reads each of the server's threads' CPU, the server's RssAnon/RSS, the
box's CPU (busy and steal), MemAvailable and swap, and sshd's CPU. The live servers are not touched
here: stop them first (systemctl stop sg@sg1 sg@sg2) and start them again afterwards.

    python3 sim_vps.py JAR TAG --at-once 2 --threaded --heap 192M

With --sg SG_JAR it runs the real SurvivalGames (feature/rooms) instead of MatchSim: --at-once rooms,
production's SG config with the database off and only the --map map, and the SgWatch plugin
(sgwatch/) writing ticks.csv. --freeze ROOM:MS@SECONDS (repeatable, ROOM may be "lobby") blocks that
room's match world's thread (or the server thread) for MS ms, SECONDS after the first bot starts.
--map takes several maps ("A,B,C", or "all" for every map production has): each room votes among them
and the bots find each map's centre themselves (sgbot --centre auto). --team-size 2 plays duos. --conf sg2
takes sg2's configs instead of sg1's: duos, and the tournament deathmatch in the registered arenas.
"""
import argparse, collections, csv, json, os, re, shutil, statistics, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = os.path.abspath(os.path.join(ROOT, "..", "..", ".."))

p = argparse.ArgumentParser()
p.add_argument("jar"); p.add_argument("tag")
p.add_argument("--host", default=os.environ.get("VPS_B_HOST"), required="VPS_B_HOST" not in os.environ)  # root@<VPS-B address>, kept out of the repository
p.add_argument("--at-once", type=int, default=1)
p.add_argument("--per-match", type=int, default=24)
p.add_argument("--bots", type=int, default=None)
p.add_argument("--rounds", type=int, default=2)
p.add_argument("--threaded", action="store_true")
p.add_argument("--map", default="Lobby_Games")
p.add_argument("--roam", type=float, default=200)
p.add_argument("--lobby", type=int, default=20)
p.add_argument("--pregame", type=int, default=15)
p.add_argument("--live", type=int, default=240)
p.add_argument("--predeathmatch", type=int, default=30)
p.add_argument("--deathmatch", type=int, default=120)
p.add_argument("--end", type=int, default=15)
p.add_argument("--heap", default="128M")
p.add_argument("--g1", action="store_true")   # = --gc "-XX:+UseG1GC -XX:+ExplicitGCInvokesConcurrent"
p.add_argument("--gc", default=None)           # flags that take the place of start-small.sh's -XX:+UseParallelGC
p.add_argument("--jit", default=None)          # flags that take the place of -XX:TieredStopAtLevel=1 ("" for the default tiers)
p.add_argument("--no-gc-after-match", action="store_true")   # MatchSim skips its System.gc() after a match
p.add_argument("--port", type=int, default=25650)
p.add_argument("--login-gap", type=float, default=0.3)
p.add_argument("--timeout", type=float, default=3600)
p.add_argument("--sg", default=None)               # the SurvivalGames jar (rooms): the real plugin instead of MatchSim
p.add_argument("--freeze", action="append", default=[])   # ROOM:MS@SECONDS, with --sg
p.add_argument("--team-size", type=int, default=None)    # with --sg: 2 for duos (default: the config's)
p.add_argument("--conf", default="sg1")                    # ArcanaServers vps-b/<conf>: sg2 plays duos and the tournament in arenas
a = p.parse_args()
CONF = "/Users/glacier/Documents/Minecraft/developments/ArcanaServers/vps-b/" + a.conf
BOTS = a.bots or a.at_once * a.per_match
H = a.host
D = "/opt/mc/simbench"
CACHE = "/opt/mc/simbench-cache"
S = "simbench"                     # tmux socket and session, as mc
TMUX = "sudo -u mc tmux -L %s" % S

JAR = os.path.abspath(a.jar)
RUN = os.path.join(ROOT, "run", "vps-" + a.tag)
OUT = os.path.join(ROOT, "run", "vps-out", a.tag)
shutil.rmtree(RUN, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(RUN); os.makedirs(OUT)


def ssh(cmd, check=True, timeout=600):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", H, cmd], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        sys.exit("ssh failed (%d): %s\n%s%s" % (r.returncode, cmd[:300], r.stdout[-2000:], r.stderr[-2000:]))
    return r.stdout


# --- the server directory: production's configs, changed for the bench ------------------------------
for f in ("bukkit.yml", "spigot.yml", "paper.yml", "pandaspigot.yml", "athena.yml", "commands.yml", "help.yml", "permissions.yml", "server.properties"):
    shutil.copy(os.path.join(CONF, f), RUN)
conf_rev = subprocess.run(["git", "-C", CONF, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()


def edit(name, pairs):
    path = os.path.join(RUN, name); s = open(path).read()
    for pat, rep in pairs:
        s, n = re.subn(pat, rep, s, flags=re.M)
        if not n: sys.exit("%s: no %r" % (name, pat))
    open(path, "w").write(s)


edit("server.properties", [(r"^server-port=.*", "server-port=%d" % a.port), (r"^server-ip=.*", "server-ip=127.0.0.1"),
                           (r"^allow-flight=.*", "allow-flight=true"), (r"^motd=.*", "motd=matchsim"),
                           (r"^max-players=.*", "max-players=%d" % (BOTS + 10))])
edit("spigot.yml", [(r"^(\s*)bungeecord: true", r"\1bungeecord: false")])
edit("bukkit.yml", [(r"^(\s*)connection-throttle: \d+", r"\1connection-throttle: -1")])
edit("athena.yml", [(r"^bungeeGuardTokens:\n(\s+- .*\n)+", "bungeeGuardTokens: []\n"),
                    (r"^guard:\n  enabled: true", "guard:\n  enabled: false")])
open(os.path.join(RUN, "eula.txt"), "w").write("eula=true\n")
shutil.copytree(os.path.join(MAIN, "bench", "run_sg", "lobby"), os.path.join(RUN, "lobby"),
                ignore=shutil.ignore_patterns("playerdata", "session.lock"))
if a.sg:
    # the real SurvivalGames: its production config, with the bench's rooms, durations and map, and no database
    os.makedirs(os.path.join(RUN, "plugins", "SurvivalGames", "maps"))
    shutil.copy(os.path.abspath(a.sg), os.path.join(RUN, "plugins", "SurvivalGames.jar"))
    MAPS = sorted(f[:-4] for f in os.listdir(os.path.join(CONF, "plugins", "SurvivalGames", "maps")) if f.endswith(".yml")) \
        if a.map == "all" else a.map.split(",")
    for m in MAPS:
        shutil.copy(os.path.join(CONF, "plugins", "SurvivalGames", "maps", m + ".yml"), os.path.join(RUN, "plugins", "SurvivalGames", "maps"))
    sgconf = open(os.path.join(CONF, "plugins", "SurvivalGames", "config.yml")).read()
    sgconf = re.sub(r"(?m)^(database:\n  enabled: )true", r"\1false", sgconf)
    if "\ndatabase:\n  enabled: false" not in sgconf: sys.exit("SG config: could not turn the database off")
    durations = {"lobby": a.lobby, "pre-game": a.pregame, "live-game": a.live, "pre-deathmatch": a.predeathmatch,
                 "deathmatch": a.deathmatch, "end-game": a.end}
    for k, v in durations.items():
        sgconf, n = re.subn(r"(?m)^(  %s: )\d+" % k, r"\g<1>%d" % v, sgconf)
        if not n: sys.exit("SG config: no durations.%s" % k)
    sgconf = re.sub(r"(?m)^rooms: .*\n", "", sgconf) + "\nrooms: %d\n" % a.at_once
    if a.team_size is not None:
        sgconf = re.sub(r"(?m)^team-size: .*\n", "", sgconf) + "team-size: %d\n" % a.team_size
    arenas = os.path.join(CONF, "plugins", "SurvivalGames", "arenas")
    if os.path.isdir(arenas):   # the deathmatch arenas of the tournament (sg2)
        shutil.copytree(arenas, os.path.join(RUN, "plugins", "SurvivalGames", "arenas"))
    open(os.path.join(RUN, "plugins", "SurvivalGames", "config.yml"), "w").write(sgconf)
    subprocess.run([os.path.join(HERE, "sgwatch", "build.sh"), os.path.join(RUN, "plugins", "SgWatch.jar")], check=True)
else:
    os.makedirs(os.path.join(RUN, "plugins", "MatchSim"))
    subprocess.run([os.path.join(HERE, "matchsim", "build.sh"), os.path.join(RUN, "plugins", "MatchSim.jar")], check=True)
    shutil.copy(os.path.join(HERE, "maps", a.map + ".yml"), os.path.join(RUN, "map.yml"))
    open(os.path.join(RUN, "plugins", "MatchSim", "config.yml"), "w").write("""maps-folder: /opt/mc/maps
map-file: %s/map.yml
players-per-match: %d
max-matches: %d
threaded: %s
gc-after-match: %s
deathmatch-at: 3
durations: {lobby: %d, pre-game: %d, live-game: %d, pre-deathmatch: %d, deathmatch: %d, end-game: %d, clean-up: 5}
""" % (D, a.per_match, a.at_once, "true" if a.threaded else "false", "false" if a.no_gc_after_match else "true", a.lobby, a.pregame, a.live, a.predeathmatch, a.deathmatch, a.end))
shutil.copy(JAR, os.path.join(RUN, "paperclip.jar"))
shutil.copy(os.path.join(HERE, "vps_sampler.py"), os.path.join(RUN, "vps_sampler.py"))
if a.sg and len(MAPS) > 1:
    CENTRE = "auto"
else:
    centre = [l for l in open(os.path.join(HERE, "maps", a.map + ".yml")) if l.startswith("center:")][0].split(":")[1].split(",")
    CENTRE = "%s,%s" % (centre[0].strip(), centre[2].strip())

# --- to the box ------------------------------------------------------------------------------------
if ssh("%s has-session -t %s 2>/dev/null && echo up; true" % (TMUX, S)).strip():
    sys.exit("a simbench server is still running on the box")
ssh("rm -rf %s; mkdir -p %s %s; chown mc:mc %s %s; [ -f %s/mojang_1.8.8.jar ] || cp /opt/mc/shared-cache/mojang_1.8.8.jar %s/" % (
    D, D, CACHE, D, CACHE, CACHE, CACHE))
subprocess.run(["rsync", "-a", RUN + "/", "%s:%s/" % (H, D)], check=True)
ssh("chown -R mc:mc %s %s" % (D, CACHE))
jar_key = ssh("sha256sum %s/paperclip.jar" % D).split()[0][:12]
GC = a.gc or ("-XX:+UseG1GC -XX:+ExplicitGCInvokesConcurrent" if a.g1 else None)
desc = "%s: %d bots, %d at once (%s), %d rounds, live %d s, heap %s%s, jar %s, configs %s, VPS-B" % (
    a.tag, BOTS, a.at_once, ("SurvivalGames rooms, %s configs, maps %s%s" % (a.conf, ",".join(MAPS), ", team-size %d" % a.team_size if a.team_size else ""))
    if a.sg else ("threaded" if a.threaded else "one thread"), a.rounds, a.live, a.heap,
    (", no System.gc() after a match" if a.no_gc_after_match else "") + (", GC " + GC if GC else "") + (", JIT %r" % a.jit if a.jit is not None else ""), jar_key, conf_rev)
print(desc, flush=True)

SCRIPT = "/opt/mc/start-small.sh"
if GC or a.jit is not None:
    # production's script with only these flags changed (the AOT key follows the flags, so each set
    # gets a cache of its own). UseMaximumCompactionOnSystemGC belongs to ParallelGC's full GC
    SCRIPT = D + "/start-variant.sh"
    sed = []
    if GC: sed += ["-e 's/-XX:+UseParallelGC/%s/'" % GC, "-e '/UseMaximumCompactionOnSystemGC/d'"]
    if a.jit is not None: sed += ["-e 's/-XX:TieredStopAtLevel=1/%s/'" % a.jit]
    ssh("sed %s /opt/mc/start-small.sh > %s && chmod 755 %s && chown mc:mc %s" % (" ".join(sed), SCRIPT, SCRIPT, SCRIPT))
    print("  flags: " + ssh("sed -n '/^FLAGS=(/,/^)/p' %s | tr -s ' \\n' ' '" % SCRIPT).strip(), flush=True)
START = ("cd %s && : > console.out && ATHENA_CACHE_ROOT=%s HEAP=%s JAVA_TOOL_OPTIONS=-Xlog:gc:file=gc.log:uptime "
         "%s 2>&1 | tee -a console.out") % (D, CACHE, a.heap, SCRIPT)


def start():
    ssh("%s new -d -s %s %s" % (TMUX, S, json.dumps(START)))
    for _ in range(200):
        out = ssh("cat %s/console.out" % D, check=False)
        if "Done (" in out: return out
        if not ssh("%s has-session -t %s 2>/dev/null && echo up; true" % (TMUX, S)).strip():
            sys.exit("server exited:\n" + out[-3000:])
        time.sleep(2)
    sys.exit("server did not start:\n" + out[-3000:])


def console(s):
    ssh("%s send-keys -t %s %s Enter" % (TMUX, S, json.dumps(s)), check=False)


def stop_server():
    console("stop")
    for _ in range(90):
        if not ssh("%s has-session -t %s 2>/dev/null && echo up; true" % (TMUX, S)).strip(): return True
        time.sleep(2)
    ssh("for p in $(pgrep -u mc -f '[o]rg.bukkit.craftbukkit.Main'); do [ \"$(readlink /proc/$p/cwd)\" = %s ] && kill $p; done; true" % D, check=False)
    return False


boot = start()
if "no AOT cache yet" in boot:
    print("warming: AOT record run for heap %s" % a.heap, flush=True)
    time.sleep(5)
    stop_server()
    boot = start()
aot = [l for l in boot.splitlines() if "start-small]" in l]
print("  " + " | ".join(aot) if aot else "  AOT cache in use", flush=True)
# only the bench's server: the live ones run the same main class from their own directories
pid = ssh("for p in $(pgrep -u mc -f '[o]rg.bukkit.craftbukkit.Main'); do [ \"$(readlink /proc/$p/cwd)\" = %s ] && echo $p; done; true" % D).split()[0]
ssh("rm -f /root/simbench-samples.txt; nohup python3 %s/vps_sampler.py %s /root/simbench-samples.txt > /dev/null 2>&1 & echo $! > /root/simbench-sampler.pid" % (D, pid))

tunnel = subprocess.Popen(["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                           "-L", "%d:127.0.0.1:%d" % (a.port, a.port), H])
time.sleep(4)
BOT_START = time.time()
FREEZES = []   # (what, ms, unix seconds when asked)


def freezer(spec):
    what, rest = spec.split(":", 1)
    ms, at = rest.split("@")
    time.sleep(max(0.0, BOT_START + float(at) - time.time()))
    FREEZES.append((what, int(ms), time.time()))
    console("sgwatch freeze %s %s" % (what, ms))


for spec in a.freeze:
    threading.Thread(target=freezer, args=(spec,), daemon=True).start()
bots = []
for i in range(BOTS):
    out = os.path.join(OUT, "bot%d.json" % i)
    args = [sys.executable, os.path.join(HERE, "sgbot.py"), "--port", str(a.port), "--name", "SBot%d" % i,
            "--centre", CENTRE, "--roam", str(a.roam), "--matches", str(a.rounds), "--seconds", str(a.timeout),
            "--seed", str(i), "--out", out]
    bots.append((out, subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=open(os.path.join(OUT, "bot%d.err" % i), "w"))))
    time.sleep(a.login_gap)
for _, pr in bots:
    try: pr.wait(timeout=max(a.timeout + 120 - (time.time() - BOT_START), 10))
    except subprocess.TimeoutExpired: pr.kill()
time.sleep(4)
if not a.sg:
    console("sim status")
time.sleep(2)
ssh("kill $(cat /root/simbench-sampler.pid) 2>/dev/null; true", check=False)
clean = stop_server()
tunnel.terminate()
for f in ("console.out", "gc.log", "plugins/SgWatch/ticks.csv" if a.sg else "plugins/MatchSim/ticks.csv"):
    subprocess.run(["scp", "-q", "%s:%s/%s" % (H, D, f), OUT])
subprocess.run(["scp", "-q", "%s:/root/simbench-samples.txt" % H, os.path.join(OUT, "samples.txt")])
print("server stopped %s; box now: %s" % ("cleanly" if clean else "by kill", ssh("free -m | sed -n 2p").strip()), flush=True)

# --- the report ----------------------------------------------------------------------------------
res = []
for out, _ in bots:
    try: res.append(json.load(open(out)))
    except Exception as e: res.append({"ok": False, "err": "no result: %s" % e})
text = open(os.path.join(OUT, "console.out"), errors="replace").read()
rows = list(csv.DictReader(open(os.path.join(OUT, "ticks.csv"))))
report = {"desc": desc, "tag": a.tag, "bots": BOTS, "at_once": a.at_once, "threaded": a.threaded, "heap": a.heap,
          "aot": aot, "bots_ok": sum(1 for r in res if r.get("ok")),
          "bot_matches": collections.Counter(r.get("matches", 0) for r in res),
          "errors": [r.get("err") for r in res if not r.get("ok")][:10]}
print("bots ok %d/%d, matches per bot %s" % (report["bots_ok"], BOTS, dict(report["bot_matches"])))

per = collections.defaultdict(list)
for r in rows: per[r["world"]].append(r)
worlds = {}
for w, rs in per.items():
    live = [r for r in rs if r["state"] in ("LIVE", "PREDEATHMATCH", "DEATHMATCH")] or rs
    worlds[w] = {"rows": len(rs), "ticks": sum(int(r["ticks"]) for r in rs), "max_gap_ms": max(int(r["max_gap_ms"]) for r in rs),
                 "gaps_over_60": sum(int(r["gaps_over_60"]) for r in rs), "gaps_over_100": sum(int(r["gaps_over_100"]) for r in rs)}
report["worlds"] = worlds
mw = {k: v for k, v in worlds.items() if k != "lobby"}
allt = sum(v["ticks"] for v in mw.values()); late = sum(v["gaps_over_60"] for v in mw.values())
report["match_ticks"] = allt; report["match_gaps_over_60"] = late
report["match_gaps_over_100"] = sum(v["gaps_over_100"] for v in mw.values())
report["match_worst_ms"] = max([v["max_gap_ms"] for v in mw.values()] or [0])
print("match worlds: %d, ticks %d, gaps > 60 ms %d (%.2f%%), > 100 ms %d, worst %d ms | lobby: gaps > 60 ms %d, worst %d ms" % (
    len(mw), allt, late, 100.0 * late / max(allt, 1), report["match_gaps_over_100"], report["match_worst_ms"],
    worlds.get("lobby", {}).get("gaps_over_60", 0), worlds.get("lobby", {}).get("max_gap_ms", 0)))

# the tick rate each match world kept: a row per 20 of its ticks, so rows per wall second x 20
fighting = collections.defaultdict(set)
for r in rows:
    if r["state"] in ("LIVE", "PREDEATHMATCH", "DEATHMATCH"): fighting[int(r["unix_ms"]) // 1000].add(r["world"])
full = set(t for t, ws in fighting.items() if len(ws) >= a.at_once)
tps = []
for w, rs in per.items():
    if w == "lobby": continue
    # a world name comes back for every match the room plays on that map: one rate per match (rows a few
    # seconds apart), not one over the hours between the first and the last
    ts = sorted(int(r["unix_ms"]) / 1000.0 for r in rs if r["state"] in ("LIVE", "PREDEATHMATCH", "DEATHMATCH"))
    runs, cur = [], []
    for t in ts:
        if cur and t - cur[-1] > 30: runs.append(cur); cur = []
        cur.append(t)
    if cur: runs.append(cur)
    for run in runs:
        if len(run) > 5: tps.append(20.0 * (len(run) - 1) / (run[-1] - run[0]))
report["fighting_tps_min"] = min(tps) if tps else None

# samples: "t|thread name|ticks" lines per thread, and one "BOX" line per sample
SAMPLES = []
cur = None
for line in open(os.path.join(OUT, "samples.txt")):
    f = line.rstrip("\n").split("|")
    if f[0] == "S":
        cur = {"t": float(f[1]), "th": collections.Counter(), "box": {}}; SAMPLES.append(cur)
    elif f[0] == "T" and cur is not None:
        key = re.sub(r"[ #-]*\d+$", "", f[1]).strip() or f[1]
        cur["th"][key] += int(f[2]) / 100.0
    elif f[0] == "P" and cur is not None:
        cur["proc"] = int(f[1]) / 100.0
    elif f[0] == "B" and cur is not None:
        cur["box"] = json.loads(f[1])
anon = [s["box"].get("anon_kb", 0) for s in SAMPLES]
report["anon_peak_mib"] = max(anon) / 1024 if anon else None
report["rss_peak_mib"] = max(s["box"].get("rss_kb", 0) for s in SAMPLES) / 1024 if SAMPLES else None
report["avail_min_mib"] = min(s["box"].get("avail_kb", 0) for s in SAMPLES) / 1024 if SAMPLES else None
report["swap_used_max_mib"] = max(s["box"].get("swap_used_kb", 0) for s in SAMPLES) / 1024 if SAMPLES else None


def window(samples):
    """CPU in cores over consecutive samples: the server (by thread), the whole box, steal, sshd.
    The server's total comes from the process (P lines) when the samples have it: summing threads
    by name loses the CPU of a thread that ended (a finished match's world thread) and goes negative."""
    th = collections.Counter(); secs = 0.0; box = steal = sshd = proc = 0.0
    for s0, s1 in zip(samples, samples[1:]):
        dt = s1["t"] - s0["t"]
        if dt > 10: continue
        secs += dt
        for k in s1["th"]: th[k] += s1["th"][k] - s0["th"].get(k, 0)
        b0, b1 = s0["box"], s1["box"]
        box += (b1["busy"] - b0["busy"]) / 100.0; steal += (b1["steal"] - b0["steal"]) / 100.0
        sshd += (b1["sshd"] - b0["sshd"]) / 100.0
        if "proc" in s0 and "proc" in s1: proc += s1["proc"] - s0["proc"]
    if not secs: return None
    return {"seconds": secs, "server_cores": (proc or sum(th.values())) / secs, "box_cores": box / secs, "steal_cores": steal / secs,
            "sshd_cores": sshd / secs, "threads": {k: v / secs for k, v in th.most_common(10)}}


in_full = [s for s in SAMPLES if int(s["t"]) in full]
report["full"] = window(in_full) if len(in_full) >= 2 else None
report["run"] = window(SAMPLES)
if report["full"]:   # one sample or none: the matches never all fought at once (rooms on maps of different lengths)
    report["full"]["anon_median_mib"] = statistics.median(s["box"]["anon_kb"] for s in in_full) / 1024
try:
    gc = open(os.path.join(OUT, "gc.log")).read().splitlines()
    after = [int(m.group(1)) for m in (re.search(r"->(\d+)M", l) for l in gc) if m]
    pauses = [float(m.group(1)) for m in (re.search(r"([\d.]+)ms$", l) for l in gc) if m]
    report["gc"] = {"count": len(pauses), "full": sum("Pause Full" in l for l in gc),
                    "max_after_mb": max(after) if after else None, "longest_ms": max(pauses) if pauses else None,
                    "young_total_ms": round(sum(float(m.group(1)) for l in gc if "Pause Young" in l
                                                for m in [re.search(r"([\d.]+)ms$", l)] if m), 1)}
except Exception as e:
    report["gc"] = {"error": str(e)}
f = report["full"] or {}
print("anon peak %.0f MiB (rss %.0f) | box MemAvailable min %.0f MiB, swap used max %.0f MiB" % (
    report["anon_peak_mib"] or 0, report["rss_peak_mib"] or 0, report["avail_min_mib"] or 0, report["swap_used_max_mib"] or 0))
print("all %d matches fighting: %.0f s, server %.3f cores, box %.3f (steal %.3f, sshd %.3f), anon median %.0f MiB | slowest match world %.2f ticks/s" % (
    a.at_once, f.get("seconds", 0), f.get("server_cores", 0), f.get("box_cores", 0), f.get("steal_cores", 0),
    f.get("sshd_cores", 0), f.get("anon_median_mib", 0), report["fighting_tps_min"] or 0))
print("  threads while all fought: %s" % ", ".join("%s %.3f" % kv for kv in list(f.get("threads", {}).items())[:8]))
print("  whole run: server %.3f cores, box %.3f" % ((report["run"] or {}).get("server_cores", 0), (report["run"] or {}).get("box_cores", 0)))
print("  GC %s" % report["gc"])
QUIET = ("moved wrongly", "moved too quickly", "OFFLINE/INSECURE", "make no attempt to authenticate",
         "hackers to connect", "online-mode", "more ram", "Advanced terminal")
errs = [l for l in text.splitlines() if re.search(r"\b(ERROR|WARN)\b", l) and not any(q in l for q in QUIET)]
report["log_warnings"] = len(errs); report["log_warning_samples"] = errs[:30]
report["stalls_reported"] = len(re.findall(r"has not finished a tick for", text))
report["cant_keep_up"] = len(re.findall(r"Can't keep up", text))
print("stalls reported %d | can't keep up %d | server WARN/ERROR lines %d | timed out %d" % (
    report["stalls_reported"], report["cant_keep_up"], len(errs), text.count("lost connection: Timed out")))
for l in errs[:10]: print("   ", l[:220])
# each freeze: the tick rate of every world while it lasted (rows of 1 s, a second in from each end)
report["freezes"] = []
for what, ms, at in FREEZES:
    t0, t1 = at + 1.5, at + ms / 1000.0 - 0.5
    rates = collections.defaultdict(list)
    for r in rows:
        if t0 <= int(r["unix_ms"]) / 1000.0 <= t1: rates[r["world"]].append(int(r["ticks"]))
    per_world = {w: round(statistics.mean(v), 1) for w, v in sorted(rates.items())}
    report["freezes"].append({"what": what, "ms": ms, "ticks_per_s": per_world})
    print("freeze %s for %d ms: ticks/s %s" % (what, ms, " ".join("%s=%s" % kv for kv in per_world.items())))
json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1, default=str)
print("out: %s" % OUT)
