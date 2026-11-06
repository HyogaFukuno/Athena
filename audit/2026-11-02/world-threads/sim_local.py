"""Athena - SG-like matches, several at once in one JVM, against a server in a resource-limited container.

The bench for feature/world-threads (phase 6), with SurvivalGames and every other plugin taken out:
the MatchSim plugin (./matchsim) plays SG's matches - the template map pinned and preloaded, 24
players teleported in, chests, PvP, eliminations into hidden spectators, a deathmatch, the clean-up
and its System.gc() - and sgbot.py plays the players, as in bench/sg_local.py. --at-once matches run
side by side in one JVM; --threaded gives each match world its own tick thread.

The server runs in the colima "athena" VM under a cgroup limit (--cpus, --mem), with production's
JVM flags (ArcanaServers common/start-small.sh, without the AOT cache) and production's configs
(ArcanaServers vps-b/sg1: view distance 8, io_uring...), changed only where the bench needs it: no
proxy, no connection throttle, flight allowed, Athena's guard off (the bots fly). Every few seconds
it reads each thread's CPU and the cgroup's anonymous memory; MatchSim writes each world's tick gaps
(measured on the thread that ticks it) to ticks.csv.

    python3 sim_local.py JAR TAG --at-once 2 --threaded --heap 256M
    python3 sim_local.py JAR TAG --at-once 3 --threaded --thread-check --rounds 6 --live 120 --freeze 400:1:5000

--freeze AT:MATCH:MS (repeatable) freezes match MATCH's tick thread (or "lobby": the server thread) for
MS milliseconds AT seconds after the bots start; the report says how every world ticked meanwhile.
"""
import argparse, collections, csv, hashlib, json, os, re, shutil, statistics, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = os.path.abspath(os.path.join(ROOT, "..", "..", ".."))           # the main checkout, for bench/run_sg's lobby
CONF = "/Users/glacier/Documents/Minecraft/developments/ArcanaServers/vps-b/sg1"
MAPS = "/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/server/maps"

p = argparse.ArgumentParser()
p.add_argument("jar"); p.add_argument("tag")
p.add_argument("--at-once", type=int, default=1)       # matches side by side
p.add_argument("--per-match", type=int, default=24)
p.add_argument("--bots", type=int, default=None)       # default: at-once x per-match
p.add_argument("--rounds", type=int, default=3)        # matches each bot plays before it leaves
p.add_argument("--threaded", action="store_true")
p.add_argument("--thread-check", action="store_true")  # -Dathena.threadCheck=true
p.add_argument("--map", default="Lobby_Games")
p.add_argument("--roam", type=float, default=200)
p.add_argument("--lobby", type=int, default=20)
p.add_argument("--pregame", type=int, default=15)
p.add_argument("--live", type=int, default=300)
p.add_argument("--predeathmatch", type=int, default=30)
p.add_argument("--deathmatch", type=int, default=120)
p.add_argument("--end", type=int, default=15)
p.add_argument("--cpus", default="2")
p.add_argument("--mem", default="3g")
p.add_argument("--heap", default="128M")
p.add_argument("--port", type=int, default=25650)
p.add_argument("--login-gap", type=float, default=0.3)
p.add_argument("--timeout", type=float, default=4800)
p.add_argument("--freeze", action="append", default=[])
p.add_argument("--jvmarg", action="append", default=[])
p.add_argument("--image", default="eclipse-temurin:25-jdk")
a = p.parse_args()
BOTS = a.bots or a.at_once * a.per_match

JAR = os.path.abspath(a.jar)
RUN = os.path.join(ROOT, "run", "sim-" + a.tag)
OUT = os.path.join(ROOT, "run", "sim-out", a.tag)
CNAME = "athena-sim-" + a.tag
shutil.rmtree(RUN, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(RUN); os.makedirs(OUT)
jar_key = hashlib.sha256(open(JAR, "rb").read()).hexdigest()[:12]
CACHE = os.path.join(ROOT, "run", "sim-cache", jar_key)       # paperclip's patched jar, per server jar
os.makedirs(CACHE, exist_ok=True)
if not os.path.exists(os.path.join(CACHE, "mojang_1.8.8.jar")) and os.path.exists(os.path.join(ROOT, "cache", "mojang_1.8.8.jar")):
    shutil.copy(os.path.join(ROOT, "cache", "mojang_1.8.8.jar"), CACHE)

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


edit("server.properties", [(r"^server-port=.*", "server-port=%d" % a.port), (r"^server-ip=.*", "server-ip="),
                           (r"^allow-flight=.*", "allow-flight=true"), (r"^motd=.*", "motd=matchsim"),
                           (r"^max-players=.*", "max-players=%d" % (BOTS + 10))])
edit("spigot.yml", [(r"^(\s*)bungeecord: true", r"\1bungeecord: false")])
edit("bukkit.yml", [(r"^(\s*)connection-throttle: \d+", r"\1connection-throttle: -1")])
edit("athena.yml", [(r"^bungeeGuardTokens:\n(\s+- .*\n)+", "bungeeGuardTokens: []\n"),
                    (r"^guard:\n  enabled: true", "guard:\n  enabled: false")])
open(os.path.join(RUN, "eula.txt"), "w").write("eula=true\n")
shutil.copytree(os.path.join(MAIN, "bench", "run_sg", "lobby"), os.path.join(RUN, "lobby"),
                ignore=shutil.ignore_patterns("playerdata", "session.lock"))
os.makedirs(os.path.join(RUN, "plugins", "MatchSim"))
plugin = os.path.join(RUN, "plugins", "MatchSim.jar")
subprocess.run([os.path.join(HERE, "matchsim", "build.sh"), plugin], check=True)
shutil.copy(os.path.join(HERE, "maps", a.map + ".yml"), os.path.join(RUN, "map.yml"))
open(os.path.join(RUN, "plugins", "MatchSim", "config.yml"), "w").write("""maps-folder: /maps
map-file: /srv/map.yml
players-per-match: %d
max-matches: %d
threaded: %s
gc-after-match: true
deathmatch-at: 3
durations: {lobby: %d, pre-game: %d, live-game: %d, pre-deathmatch: %d, deathmatch: %d, end-game: %d, clean-up: 5}
""" % (a.per_match, a.at_once, "true" if a.threaded else "false", a.lobby, a.pregame, a.live, a.predeathmatch, a.deathmatch, a.end))
centre = [l for l in open(os.path.join(HERE, "maps", a.map + ".yml")) if l.startswith("center:")][0].split(":")[1].split(",")
CENTRE = "%s,%s" % (centre[0].strip(), centre[2].strip())

# --- the JVM: production's flags (start-small.sh) without the AOT cache ------------------------------
flags = ["-Xms" + a.heap, "-Xmx" + a.heap, "-XX:+UseParallelGC", "-XX:TieredStopAtLevel=1",
         "-XX:-UseMaximumCompactionOnSystemGC", "-XX:+UseCompactObjectHeaders", "-XX:TrimNativeHeapInterval=5000",
         "-XX:ReservedCodeCacheSize=48m", "-XX:SweeperThreshold=50", "-Dio.netty.allocator.maxOrder=7",
         "-XX:+UnlockDiagnosticVMOptions", "-Xlog:gc:file=/out/gc.log:uptime"]
if a.thread_check: flags.append("-Dathena.threadCheck=true")
flags += a.jvmarg
subprocess.run(["docker", "rm", "-f", CNAME], capture_output=True)
cmd = ["docker", "run", "-i", "--name", CNAME, "--cpus=" + a.cpus, "--memory=" + a.mem,
       "--security-opt", "seccomp=unconfined", "-e", "MALLOC_ARENA_MAX=2",
       "-p", "127.0.0.1:%d:%d" % (a.port, a.port), "-v", "%s:/srv" % RUN, "-w", "/srv", "-v", "%s:/srv/cache" % CACHE,
       "-v", "%s:/maps:ro" % MAPS, "-v", "%s:/jar/server.jar:ro" % JAR, "-v", "%s:/out" % OUT,
       a.image, "java"] + flags + ["-jar", "/jar/server.jar", "nogui"]
log = open(os.path.join(OUT, "server.log"), "wb")
srv = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
desc = "%s: %d bots, %d at once (%s), %d rounds, --cpus=%s --memory=%s, heap %s%s, jar %s, configs %s" % (
    a.tag, BOTS, a.at_once, "threaded" if a.threaded else "one thread", a.rounds, a.cpus, a.mem, a.heap,
    ", thread check" if a.thread_check else "", jar_key, conf_rev)
print(desc, flush=True)


def logtext():
    try: return open(os.path.join(OUT, "server.log"), "rb").read().decode("utf8", "replace")
    except Exception: return ""


t0 = time.time()
while "Done (" not in logtext():
    if srv.poll() is not None or time.time() - t0 > 400:
        sys.exit("%s: server did not start\n%s" % (a.tag, logtext()[-3000:]))
    time.sleep(1)


def console(s):
    try:
        srv.stdin.write((s + "\n").encode()); srv.stdin.flush()
    except BrokenPipeError:
        pass


# --- samples: each thread's CPU (/proc, 1/100 s) and the cgroup's anonymous memory ------------------
SAMPLES = []
stop = threading.Event()
SCRIPT = ('for t in /proc/1/task/*; do s=$(sed "s/.*) //" $t/stat); set -- $s;'
          ' printf "%s|%s %s\\n" "$(cat $t/comm)" "${12}" "${13}"; done;'
          " echo ANON $(grep '^anon ' /sys/fs/cgroup/memory.stat | cut -d' ' -f2);"
          " echo RSS $(grep '^VmRSS' /proc/1/status | tr -s ' ' | cut -d' ' -f2)")


def sample():
    while not stop.is_set():
        try:
            out = subprocess.check_output(["docker", "exec", CNAME, "sh", "-c", SCRIPT], text=True, timeout=20)
            th = collections.Counter(); anon = rss = None
            for line in out.splitlines():
                if line.startswith("ANON "): anon = int(line.split()[1]); continue
                if line.startswith("RSS "): rss = int(line.split()[1]) * 1024; continue
                name, ticks = line.rsplit("|", 1)
                u, s_ = (ticks.split() + ["0", "0"])[:2]
                key = re.sub(r"[ #-]*\d+$", "", name).strip() or name
                th[key] += (int(u) + int(s_)) / 100.0
            SAMPLES.append((time.time(), dict(th), anon, rss))
        except Exception:
            pass
        stop.wait(3)


sampler = threading.Thread(target=sample, daemon=True); sampler.start()
time.sleep(3)
BOT_START = time.time()

FREEZES = []
for f in a.freeze:
    at_s, match, ms = f.split(":")
    FREEZES.append((float(at_s), match, int(ms)))


def freezer():
    for at_s, match, ms in sorted(FREEZES):
        while time.time() - BOT_START < at_s and not stop.is_set():
            time.sleep(0.2)
        if stop.is_set(): return
        console("sim freeze %s %d" % (match, ms))
        FREEZE_LOG.append((time.time(), match, ms))


FREEZE_LOG = []
threading.Thread(target=freezer, daemon=True).start()

bots = []
for i in range(BOTS):
    out = os.path.join(OUT, "bot%d.json" % i)
    args = [sys.executable, os.path.join(HERE, "sgbot.py"), "--port", str(a.port), "--name", "SBot%d" % i,
            "--centre", CENTRE, "--roam", str(a.roam), "--matches", str(a.rounds), "--seconds", str(a.timeout),
            "--seed", str(i), "--out", out]
    bots.append((out, subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=open(os.path.join(OUT, "bot%d.err" % i), "w"))))
    time.sleep(a.login_gap)
for _, pr in bots:
    try: pr.wait(timeout=a.timeout + 120)
    except subprocess.TimeoutExpired: pr.kill()
time.sleep(4)
console("sim status"); console("sim threadcheck")
time.sleep(2)
stop.set(); sampler.join(timeout=30)
console("stop")
try: srv.wait(timeout=120)
except subprocess.TimeoutExpired: subprocess.run(["docker", "kill", CNAME], capture_output=True)
oom = subprocess.run(["docker", "inspect", "-f", "{{.State.OOMKilled}}", CNAME], capture_output=True, text=True).stdout.strip()
subprocess.run(["docker", "rm", "-f", CNAME], capture_output=True)
shutil.copy(os.path.join(RUN, "plugins", "MatchSim", "ticks.csv"), OUT)

# --- the report ----------------------------------------------------------------------------------
res = []
for out, _ in bots:
    try: res.append(json.load(open(out)))
    except Exception as e: res.append({"ok": False, "err": "no result: %s" % e})
text = logtext()
rows = list(csv.DictReader(open(os.path.join(OUT, "ticks.csv"))))
report = {"desc": desc, "tag": a.tag, "bots": BOTS, "at_once": a.at_once, "threaded": a.threaded, "heap": a.heap,
          "cpus": a.cpus, "oom": oom, "bots_ok": sum(1 for r in res if r.get("ok")),
          "bot_matches": collections.Counter(r.get("matches", 0) for r in res),
          "errors": [r.get("err") for r in res if not r.get("ok")][:10]}
print("bots ok %d/%d, matches per bot %s, OOM-killed %s" % (report["bots_ok"], BOTS, dict(report["bot_matches"]), oom))

# ticks per world: matches are "sg-...-<n>"; the lobby is "lobby"
per = collections.defaultdict(list)
for r in rows: per[r["world"]].append(r)
worlds = {}
for w, rs in per.items():
    live = [r for r in rs if r["state"] in ("LIVE", "PREDEATHMATCH", "DEATHMATCH")] or rs
    ticks = sum(int(r["ticks"]) for r in rs)
    worlds[w] = {"seconds": len(rs), "ticks": ticks, "max_gap_ms": max(int(r["max_gap_ms"]) for r in rs),
                 "gaps_over_60": sum(int(r["gaps_over_60"]) for r in rs), "gaps_over_100": sum(int(r["gaps_over_100"]) for r in rs),
                 "tps_fighting_median": statistics.median(int(r["ticks"]) for r in live) if live else None}
report["worlds"] = worlds
late = sum(v["gaps_over_60"] for k, v in worlds.items() if k != "lobby")
allt = sum(v["ticks"] for k, v in worlds.items() if k != "lobby")
report["match_ticks"] = allt; report["match_gaps_over_60"] = late
report["match_gaps_over_100"] = sum(v["gaps_over_100"] for k, v in worlds.items() if k != "lobby")
print("match worlds: %d, ticks %d, gaps > 60 ms %d (%.2f%%), > 100 ms %d, worst %d ms | lobby: gaps > 60 ms %d, worst %d ms" % (
    len(worlds) - ("lobby" in worlds), allt, late, 100.0 * late / max(allt, 1), report["match_gaps_over_100"],
    max([v["max_gap_ms"] for k, v in worlds.items() if k != "lobby"] or [0]),
    worlds.get("lobby", {}).get("gaps_over_60", 0), worlds.get("lobby", {}).get("max_gap_ms", 0)))

# how many matches were fighting at each moment, from ticks.csv; memory and CPU while all were
fighting = collections.defaultdict(set)
for r in rows:
    if r["state"] in ("LIVE", "PREDEATHMATCH", "DEATHMATCH"): fighting[int(r["unix_ms"]) // 1000].add(r["world"])
full = sorted(t for t, ws in fighting.items() if len(ws) >= a.at_once)
anon_all = [s[2] for s in SAMPLES if s[2]]
rss_all = [s[3] for s in SAMPLES if s[3]]
report["anon_peak_mib"] = max(anon_all) / 2**20 if anon_all else None
report["rss_peak_mib"] = max(rss_all) / 2**20 if rss_all else None
in_full = [s for s in SAMPLES if full and int(s[0]) in set(full)]
if len(in_full) >= 2:
    wall = in_full[-1][0] - in_full[0][0]
    # CPU over the samples inside full windows only (consecutive pairs both inside)
    cores = []; threads = collections.Counter(); secs = 0.0
    for s0, s1 in zip(in_full, in_full[1:]):
        dt = s1[0] - s0[0]
        if dt > 10: continue
        secs += dt
        for k in s1[1]: threads[k] += s1[1].get(k, 0) - s0[1].get(k, 0)
    report["full_seconds"] = secs
    report["full_cores"] = sum(threads.values()) / secs if secs else None
    report["full_threads"] = {k: v / secs for k, v in threads.most_common(12)} if secs else {}
    report["full_anon_median_mib"] = statistics.median(s[2] for s in in_full if s[2]) / 2**20
    report["full_anon_peak_mib"] = max(s[2] for s in in_full if s[2]) / 2**20
w0, w1 = SAMPLES[0], SAMPLES[-1]
report["run_cores"] = sum(w1[1].values()) - sum(w0[1].values())
report["run_cores"] /= max(w1[0] - w0[0], 1)
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
print("anon peak %.0f MiB (rss %.0f) | all %d matches fighting: %.0f s, anon median %.0f / peak %.0f MiB, %.3f cores | whole run %.3f cores" % (
    report["anon_peak_mib"] or 0, report["rss_peak_mib"] or 0, a.at_once, report.get("full_seconds", 0),
    report.get("full_anon_median_mib", 0), report.get("full_anon_peak_mib", 0), report.get("full_cores") or 0, report["run_cores"]))
print("  threads while all fought: %s" % ", ".join("%s %.3f" % kv for kv in list(report.get("full_threads", {}).items())[:8]))
print("  GC %s" % report["gc"])

# freezes: how far each world ticked while one was frozen
fr = []
frozen = re.findall(r"freezing (\S+) for \d+ ms on World thread - (\S+)", text)
for t, match, ms in FREEZE_LOG:
    # a world writes a row every 20 of its ticks: rows inside the freeze x 20 / its length is its tick rate
    lo, hi = t + 0.5, t + ms / 1000.0 - 0.5
    got = {}
    for w, rs in per.items():
        times = [int(r["unix_ms"]) / 1000.0 for r in rs]
        if not times or min(times) > lo or max(times) < hi: continue   # not loaded the whole time
        got[w] = 20.0 * sum(1 for x in times if lo <= x <= hi) / (hi - lo)
    world = dict(frozen).get(match, "lobby" if match == "lobby" else "?")
    fr.append({"match": match, "world": world, "ms": ms, "ticks_per_s_during": got})
    print("  freeze %s (%s) %d ms: ticks/s meanwhile %s" % (match, world, ms, ", ".join("%s %.1f" % (w.rsplit("-", 1)[-1], v) for w, v in sorted(got.items()))))
report["freezes"] = fr

# thread check and server log
m = re.findall(r"Thread check: (\d+) calls from a thread that does not tick the world, at (\d+) places", text)
report["thread_check"] = {"calls": int(m[-1][0]), "places": int(m[-1][1])} if m else None
report["thread_check_lines"] = re.findall(r"Thread check: (\d+x .*)", text)[:40]
QUIET = ("Thread check: ", "moved wrongly", "moved too quickly", "OFFLINE/INSECURE", "make no attempt to authenticate",
         "hackers to connect", "online-mode", "more ram", "Advanced terminal")
errs = [l for l in text.splitlines() if re.search(r"\b(ERROR|WARN)\b", l) and not any(q in l for q in QUIET)]
report["moved_wrongly"] = text.count("moved wrongly") + text.count("moved too quickly")
report["log_warnings"] = len(errs)
report["stalls_reported"] = len(re.findall(r"has not finished a tick for", text)); report["log_warning_samples"] = errs[:30]
report["exceptions"] = len(re.findall(r"^\S*Exception|^\tat ", text, re.M))
print("thread check: %s | stalls reported %d | server WARN/ERROR lines %d | timed out %d" % (
    report["thread_check"], report["stalls_reported"], len(errs), text.count("lost connection: Timed out")))
for l in errs[:12]: print("   ", l[:220])
json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1, default=str)
json.dump(SAMPLES, open(os.path.join(OUT, "samples.json"), "w"))
print("out: %s" % OUT)
