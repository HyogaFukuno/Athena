"""Athena - SurvivalGames played by bots only, on this Mac: does a match run from the lobby to the end?

    python3 sgbots.py ATHENA_JAR SG_JAR [--map Shady_Hollow] [--rooms 1] [--fill 24] [--team-size 1] [--matches 1]

sg2's production configs (ArcanaServers vps-b/sg2) with no database, short durations, bots on with
min-humans 0 (a lobby with nobody in it fills with bots and plays), and the BotWatch plugin (./botwatch),
which logs what the bots do every 5 seconds and stops the server after --matches matches.
Writes run/sgbots-out/: server.log, botwatch.csv, combat-log*.csv.
"""
import argparse, os, re, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = "/Users/glacier/Documents/Minecraft/developments/sandbox/Athena"
CONF = "/Users/glacier/Documents/Minecraft/developments/ArcanaServers/vps-b/sg2"
MAPS = MAIN + "/server/maps"
RUN = os.path.join(ROOT, "run", "sgbots")
OUT = os.path.join(ROOT, "run", "sgbots-out")

p = argparse.ArgumentParser()
p.add_argument("jar"); p.add_argument("sg")
p.add_argument("--map", default="Shady_Hollow")
p.add_argument("--rooms", type=int, default=1)
p.add_argument("--fill", type=int, default=24)
p.add_argument("--team-size", type=int, default=1)
p.add_argument("--matches", type=int, default=1)
p.add_argument("--difficulty", default="normal")
p.add_argument("--live", type=int, default=600)
p.add_argument("--minutes", type=float, default=25)
p.add_argument("--vps", action="store_true")   # on VPS-B ($VPS_B_HOST) through production's start-small.sh; stop sg1/sg2 first
p.add_argument("--heap", default="128M")       # with --vps
p.add_argument("--gc", default=None)           # with --vps: flags that take the place of -XX:+UseParallelGC
p.add_argument("--tag", default="local")
a = p.parse_args()
H = os.environ.get("VPS_B_HOST")
if a.vps and not H: sys.exit("set VPS_B_HOST to root@<VPS-B address>")
if a.vps:
    OUT = os.path.join(ROOT, "run", "sgbots-vps-out", a.tag)

shutil.rmtree(RUN, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(RUN + "/plugins/SurvivalGames/maps"); os.makedirs(OUT)
for f in ("bukkit.yml", "spigot.yml", "paper.yml", "pandaspigot.yml", "athena.yml", "commands.yml", "help.yml", "permissions.yml", "server.properties"):
    shutil.copy(os.path.join(CONF, f), RUN)


def edit(name, pairs):
    path = os.path.join(RUN, name)
    s = open(path).read()
    for pat, rep in pairs:
        s, n = re.subn(pat, rep, s, flags=re.M)
        if not n: sys.exit("%s: no %r" % (name, pat))
    open(path, "w").write(s)


edit("server.properties", [(r"^server-port=.*", "server-port=25692"), (r"^server-ip=.*", "server-ip=127.0.0.1"),
                           (r"^online-mode=.*", "online-mode=false"), (r"^max-players=.*", "max-players=100"),
                           (r"^allow-flight=.*", "allow-flight=true")])  # a watching client (sgwatcher.py) does not fall
edit("spigot.yml", [(r"^(\s*)bungeecord: true", r"\1bungeecord: false")])
edit("athena.yml", [(r"^bungeeGuardTokens:\n(\s+- .*\n)+", "bungeeGuardTokens: []\n")])
open(RUN + "/eula.txt", "w").write("eula=true\n")
shutil.copytree(os.path.join(MAIN, "bench", "run_sg", "lobby"), os.path.join(RUN, "lobby"), ignore=shutil.ignore_patterns("playerdata", "session.lock"))
shutil.copy(os.path.abspath(a.sg), RUN + "/plugins/SurvivalGames.jar")
maps = sorted(f[:-4] for f in os.listdir(CONF + "/plugins/SurvivalGames/maps") if f.endswith(".yml")) if a.map == "all" else a.map.split(",")
for m in maps:
    shutil.copy(os.path.join(CONF, "plugins/SurvivalGames/maps", m + ".yml"), RUN + "/plugins/SurvivalGames/maps")
if os.path.isdir(CONF + "/plugins/SurvivalGames/arenas"):
    shutil.copytree(CONF + "/plugins/SurvivalGames/arenas", RUN + "/plugins/SurvivalGames/arenas")
sg = open(CONF + "/plugins/SurvivalGames/config.yml").read()
sg = re.sub(r"(?m)^(database:\n  enabled: )true", r"\1false", sg)
sg = re.sub(r"(?m)^maps-folder: .*", "maps-folder: " + ("/opt/mc/maps" if a.vps else MAPS), sg)
sg = re.sub(r"(?m)^lobby-template: .*\n", "", sg) + "\nlobby-template: %s\n" % ("/opt/mc/maps/map_sg_lobby" if a.vps else os.path.join(MAIN, "bench", "run_sg", "lobby"))
for k, v in {"lobby": 20, "pre-game": 10, "live-game": a.live, "pre-deathmatch": 10, "deathmatch": 120, "end-game": 5, "clean-up": 5}.items():
    sg, n = re.subn(r"(?m)^(  %s: )\d+" % k, r"\g<1>%d" % v, sg)
    if not n: sys.exit("SG config: no durations.%s" % k)
sg = re.sub(r"(?m)^rooms: .*\n", "", sg) + "\nrooms: %d\n" % a.rooms
sg = re.sub(r"(?m)^team-size: .*\n", "", sg) + "team-size: %d\n" % a.team_size
sg = re.sub(r"(?m)^bots:\n(?:  .*\n)*", "", sg)
sg += "bots:\n  enabled: true\n  fill-to: %d\n  min-humans: 0\n  join-at: 15\n  difficulty: %s\n" % (a.fill, a.difficulty)
open(RUN + "/plugins/SurvivalGames/config.yml", "w").write(sg)
subprocess.run([os.path.join(HERE, "botwatch", "build.sh"), RUN + "/plugins/botwatch.jar"], check=True)

if a.vps:
    import json
    D, CACHE, S = "/opt/mc/simbench", "/opt/mc/simbench-cache", "simbench"
    TMUX = "sudo -u mc tmux -L %s" % S

    def ssh(cmd, check=True, timeout=600):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", H, cmd], capture_output=True, text=True, timeout=timeout)
        if check and r.returncode:
            sys.exit("ssh failed (%d): %s\n%s%s" % (r.returncode, cmd[:300], r.stdout[-2000:], r.stderr[-2000:]))
        return r.stdout

    def up():
        return bool(ssh("%s has-session -t %s 2>/dev/null && echo up; true" % (TMUX, S)).strip())

    if up(): sys.exit("a simbench server is still running on the box")
    shutil.copy(os.path.abspath(a.jar), RUN + "/paperclip.jar")
    ssh("rm -rf %s; mkdir -p %s %s; [ -f %s/mojang_1.8.8.jar ] || cp /opt/mc/shared-cache/mojang_1.8.8.jar %s/" % (D, D, CACHE, CACHE, CACHE))
    subprocess.run(["rsync", "-a", RUN + "/", "%s:%s/" % (H, D)], check=True)
    ssh("chown -R mc:mc %s %s" % (D, CACHE))
    script = "/opt/mc/start-small.sh"
    if a.gc:
        script = D + "/start-variant.sh"
        ssh("sed -e 's/-XX:+UseParallelGC/%s/' -e '/UseMaximumCompactionOnSystemGC/d' /opt/mc/start-small.sh > %s && chmod 755 %s && chown mc:mc %s"
            % (a.gc, script, script, script))
    props = "-Dbotwatch.matches=%d -Dsgbots.debug=%s -Dbotwatch.protect=%s" % (a.matches * a.rooms, os.environ.get("SGBOTS_DEBUG", "false"),
                                                                              os.environ.get("BOTWATCH_PROTECT", ""))
    props += " " + os.environ.get("SGBOTS_JVM", "")   # measuring: JFR, -Dathena.threadStats, GC logs
    start = ("cd %s && : > console.out && ATHENA_CACHE_ROOT=%s HEAP=%s JAVA_TOOL_OPTIONS=%s %s 2>&1 | tee -a console.out"
             % (D, CACHE, a.heap, json.dumps(props), script))

    def run_once():
        ssh("%s new -d -s %s %s" % (TMUX, S, json.dumps(start)))
        pid, t0, cpu0, samples = None, None, None, []
        began, stopped = time.time(), False
        deadline = time.time() + a.minutes * 60
        while time.time() < deadline:
            time.sleep(5)
            if not up(): break
            if time.time() - began > 90 and not stopped and "no AOT cache yet" in ssh("head -c 4000 %s/console.out" % D, check=False):
                print("recording the AOT cache: stopping this run", flush=True)   # a record run measures nothing
                ssh("%s send-keys -t %s stop Enter" % (TMUX, S), check=False)
                stopped = True
            if pid is None:
                pid = (ssh("for p in $(pgrep -u mc -f '[o]rg.bukkit.craftbukkit.Main'); do [ \"$(readlink /proc/$p/cwd)\" = %s ] && echo $p; done; true" % D).split() or [None])[0]
                continue
            out = ssh("ps -o time=,rss= -p %s; free -m | awk '/Swap/{print $3}'" % pid, check=False).split()
            if len(out) < 2: continue
            parts = [float(x) for x in out[0].split("-")[-1].split(":")]
            c = 0.0
            for x in parts: c = c * 60 + x
            if "-" in out[0]: c += float(out[0].split("-")[0]) * 86400
            now = time.time()
            if cpu0 is not None:
                samples.append((now - t0, (c - cpu0) / (now - tl), int(out[1]) / 1024.0, int(out[2])))
            else:
                t0 = now
            cpu0, tl = c, now
        else:
            print("!! not done in %s minutes: stopping" % a.minutes, flush=True)
            ssh("%s send-keys -t %s stop Enter" % (TMUX, S), check=False)
            for _ in range(60):
                if not up(): break
                time.sleep(2)
        return ssh("cat %s/console.out" % D, check=False), samples

    text, samples = run_once()
    if "no AOT cache yet" in text and "BOTWATCH DONE" not in text:
        print("that run recorded the AOT cache; again with it", flush=True)
        text, samples = run_once()
    open(OUT + "/server.log", "w").write(text)
    with open(OUT + "/cpu.csv", "w") as f:
        f.write("seconds,cores,rss_mib,swap_mib\n")
        for t, c, r, w in samples: f.write("%.0f,%.3f,%.0f,%d\n" % (t, c, r, w))
    subprocess.run(["scp", "-q", "%s:%s/botwatch.csv" % (H, D), OUT + "/"])
    subprocess.run(["scp", "-q", "%s:%s/gc.log" % (H, D), OUT + "/"], stderr=subprocess.DEVNULL)
    for line in text.splitlines():
        if "match over" in line or "ERROR" in line or "Exception" in line and "io_uring" not in line:
            print(line[:220])
    if samples:
        cs = [x[1] for x in samples]; rs = [x[2] for x in samples]; ws = [x[3] for x in samples]
        print("cpu cores mean %.2f max %.2f, rss MiB mean %.0f max %.0f, swap MiB max %d" % (sum(cs) / len(cs), max(cs), sum(rs) / len(rs), max(rs), max(ws)))
    print("finished" if "BOTWATCH DONE" in text else "not finished")
    sys.exit(0)

log = open(OUT + "/server.log", "w")
cmd = ["java", "-Xms768M", "-Xmx768M", "-XX:+UseParallelGC", "-Dsgbots.debug=" + os.environ.get("SGBOTS_DEBUG", "false"),
       "-Dbotwatch.protect=" + os.environ.get("BOTWATCH_PROTECT", ""), "-Dbotwatch.matches=%d" % (a.matches * a.rooms), "-jar", os.path.abspath(a.jar), "nogui"]
server = subprocess.Popen(cmd, cwd=RUN, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
done = threading.Event()


def pump():
    for line in server.stdout:
        log.write(line); log.flush()
        if "BOTWATCH" in line or "ERROR" in line or "Exception" in line or "joined room" in line:
            print(line.rstrip()[:240], flush=True)
        if "BOTWATCH DONE" in line:
            done.set()


def cputime():
    # the server's CPU time so far, in seconds (ps: [[dd-]hh:]mm:ss.ss)
    out = subprocess.run(["ps", "-o", "time=", "-p", str(server.pid)], capture_output=True, text=True).stdout.strip()
    if not out: return None
    days, _, rest = out.rpartition("-")
    parts = [float(x) for x in rest.split(":")]
    secs = 0.0
    for x in parts: secs = secs * 60 + x
    return secs + (float(days) * 86400 if days else 0)


def sample():
    # the cores the whole server uses (every thread: the server thread and the matches' world threads)
    cpu = open(OUT + "/cpu.csv", "w")
    cpu.write("seconds,cores\n")
    t0, last, at = time.time(), cputime(), time.time()
    while not done.is_set() and server.poll() is None:
        time.sleep(5)
        now, c = time.time(), cputime()
        if c is None or last is None: break
        cpu.write("%.0f,%.3f\n" % (now - t0, (c - last) / (now - at))); cpu.flush()
        last, at = c, now


threading.Thread(target=pump, daemon=True).start()
threading.Thread(target=sample, daemon=True).start()
finished = done.wait(a.minutes * 60)
if not finished:
    print("!! no BOTWATCH DONE in %s minutes: stopping" % a.minutes)
    server.stdin.write("stop\n"); server.stdin.flush()
try:
    server.wait(90)
except subprocess.TimeoutExpired:
    server.kill()
for f in os.listdir(RUN):
    if f.endswith(".csv"):
        shutil.copy(os.path.join(RUN, f), OUT)
for f in os.listdir(RUN + "/plugins/SurvivalGames"):
    if f.endswith(".csv"):
        shutil.copy(os.path.join(RUN, "plugins/SurvivalGames", f), OUT)
print("finished" if finished else "timed out")
