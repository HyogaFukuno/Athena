"""Athena - SurvivalGames' rooms through the whole network on this Mac: Velocity, the hub and a
rooms SG server, with the database between them, and bots that walk in through the hub's join signs.

Stage 4 of the rooms plan (audit/2026-11-03/SG-ROOMS-PLAN.md) was checked by writing sg_room_joins
rows by hand; this runs the path a player takes: the hub (AthenaHub, feature/rooms) pings the SG server
and writes each room's state on its sign, a right-click writes the room to sg_room_joins and asks
Velocity to send the player over, and SG reads the row before login and puts the player in that room.

    python3 hubtest.py JAR SG_JAR HUB_JAR TAG

Everything is throwaway: a MariaDB container in the colima "athena" VM (removed at the end), and the
servers run natively from run/hubtest-TAG (Velocity and ArcanaRanks are network-dev's), on ports of
their own. The passwords and the forwarding secret are made up for the run and stay in that folder.

The bots (sgbot.py --hub-sign), in this order:
  hop    1 bot: sign "room 1", then back to the hub with /hub from the SG lobby and sign "room 3"
  r2    24 bots: sign "room 2" (fills it)
  r3     8 bots: sign "room 3"
  any    6 bots: the sign with no room (SG puts them in the fullest lobby with room: room 3)
  late   2 bots: sign "room 2" once room 2 is full: SG refuses them as a full server does ("The server is
         full!"), and Velocity keeps them on the hub with that message
Each plays one match. The report lists the room SG put each bot in, what the bots read on the signs,
the rows left in sg_room_joins, and the errors in the three logs.
"""
import argparse, json, os, re, secrets, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MAIN = os.path.abspath(os.path.join(ROOT, "..", "..", ".."))
DEV = "/Users/glacier/Documents/Minecraft/developments"
CONF = DEV + "/ArcanaServers/vps-b/sg1"
HUBSRC = DEV + "/AthenaHub-rooms/src/main/resources/config.yml"
MAPS = MAIN + "/server/maps"

p = argparse.ArgumentParser()
p.add_argument("jar"); p.add_argument("sg"); p.add_argument("hub"); p.add_argument("tag")
p.add_argument("--rooms", type=int, default=3)
p.add_argument("--map", default="Lobby_Games")       # "A,B" or "all": the rooms vote among them
p.add_argument("--team-size", type=int, default=1)
p.add_argument("--login-gap", type=float, default=0.3)
p.add_argument("--timeout", type=float, default=1200)
a = p.parse_args()

PROXY_PORT, HUB_PORT, SG_PORT, DB_PORT = 25677, 25711, 25712, 33320
DOCKER = ["docker", "--context", "colima-athena"]
DBNAME = "athena-hubtest-db"
RUN = os.path.join(ROOT, "run", "hubtest-" + a.tag)
OUT = os.path.join(ROOT, "run", "hubtest-out", a.tag)
shutil.rmtree(RUN, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT + "/bots")
PW = {k: secrets.token_hex(12) for k in ("root", "proxy", "hub", "sg")}
SECRET = secrets.token_hex(16)
SPAWN = (7.5, 4.0, 6.5)
SIGNS = {"room1": ((5, 4, 9), 1), "room2": ((6, 4, 9), 2), "room3": ((7, 4, 9), 3), "any": ((8, 4, 9), 0)}


def edit(path, pairs):
    s = open(path).read()
    for pat, rep in pairs:
        s, n = re.subn(pat, rep, s, flags=re.M)
        if not n: sys.exit("%s: no %r" % (path, pat))
    open(path, "w").write(s)


def server_dir(name, port, level, extra_props):
    d = os.path.join(RUN, name); os.makedirs(d + "/plugins")
    for f in ("bukkit.yml", "spigot.yml", "paper.yml", "pandaspigot.yml", "athena.yml", "commands.yml", "help.yml", "permissions.yml", "server.properties"):
        shutil.copy(os.path.join(CONF, f), d)
    edit(d + "/server.properties", [(r"^server-port=.*", "server-port=%d" % port), (r"^server-ip=.*", "server-ip=127.0.0.1"),
                                    (r"^level-name=.*", "level-name=" + level), (r"^max-players=.*", "max-players=200")] + extra_props)
    edit(d + "/bukkit.yml", [(r"^(\s*)connection-throttle: \d+", r"\1connection-throttle: -1")])
    # the run's own forwarding secret, the only token the server takes from Velocity; the guard off for the flying bots
    edit(d + "/athena.yml", [(r"^bungeeGuardTokens:\n(\s+- .*\n)+", "bungeeGuardTokens:\n  - %s\n" % SECRET),
                             (r"^guard:\n  enabled: true", "guard:\n  enabled: false")])
    open(d + "/eula.txt", "w").write("eula=true\n")
    shutil.copy(os.path.abspath(a.jar), d + "/paperclip.jar")
    os.makedirs(d + "/cache")
    for f in ("mojang_1.8.8.jar",):
        shutil.copy(os.path.join(ROOT, "run", "sg_rooms", "cache", f), d + "/cache")
    return d


# --- the database ------------------------------------------------------------------------------------
subprocess.run(DOCKER + ["rm", "-f", DBNAME], capture_output=True)
subprocess.run(DOCKER + ["run", "-d", "--rm", "--name", DBNAME, "-p", "127.0.0.1:%d:3306" % DB_PORT,
                         "-e", "MARIADB_ROOT_PASSWORD=" + PW["root"], "-e", "MARIADB_DATABASE=arcana",
                         "-e", "MARIADB_USER=proxy", "-e", "MARIADB_PASSWORD=" + PW["proxy"], "mariadb:11.4"],
               check=True, capture_output=True)


def sql(q):
    r = subprocess.run(DOCKER + ["exec", DBNAME, "mariadb", "-uroot", "-p" + PW["root"], "-N", "arcana", "-e", q],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


for _ in range(120):
    if sql("SELECT 1") is not None: break
    time.sleep(1)
else:
    sys.exit("MariaDB did not come up")
for user, pw in (("hubserver", PW["hub"]), ("sgserver", PW["sg"])):
    sql("CREATE USER '%s'@'%%' IDENTIFIED BY '%s'; GRANT ALL ON arcana.* TO '%s'@'%%'" % (user, pw, user))

# --- Velocity (network-dev's, with ArcanaRanks) ------------------------------------------------------
proxy = os.path.join(RUN, "proxy"); os.makedirs(proxy + "/plugins/arcana-ranks")
shutil.copy(DEV + "/network-dev/proxy/velocity.jar", proxy)
shutil.copy(DEV + "/network-dev/proxy/plugins/ArcanaRanks-Velocity.jar", proxy + "/plugins")
shutil.copy(DEV + "/network-dev/proxy/velocity.toml", proxy)
open(proxy + "/forwarding.secret", "w").write(SECRET)
edit(proxy + "/velocity.toml", [(r'^bind = .*', 'bind = "127.0.0.1:%d"' % PROXY_PORT),
                                (r'^\[servers\]\n(.*\n)*?try = \[\n(.*\n)*?\]', '[servers]\nhub = "127.0.0.1:%d"\nsg1 = "127.0.0.1:%d"\ntry = [\n    "hub"\n]' % (HUB_PORT, SG_PORT))])
conf = open(DEV + "/network-dev/proxy/plugins/arcana-ranks/config.properties").read()
conf = re.sub(r"(?m)^database.port=.*", "database.port=%d" % DB_PORT, conf)
conf = re.sub(r"(?m)^database.password=.*", "database.password=" + PW["proxy"], conf)
open(proxy + "/plugins/arcana-ranks/config.properties", "w").write(conf)

# --- the hub -----------------------------------------------------------------------------------------
hub = server_dir("hub", HUB_PORT, "hub", [(r"^level-type=.*", "level-type=FLAT"), (r"^spawn-protection=.*", "spawn-protection=0")])
os.makedirs(hub + "/plugins/Athena")
shutil.copy(os.path.abspath(a.hub), hub + "/plugins/Athena.jar")
hc = open(HUBSRC).read()
hc = re.sub(r"(?m)^  y: 44\.0", "  y: %.1f" % SPAWN[1], hc)
hc = re.sub(r"(?m)^database:\n  enabled: false\n  host: .*\n  port: .*\n  database: .*\n  username: .*\n  password: .*",
            'database:\n  enabled: true\n  host: 127.0.0.1\n  port: %d\n  database: arcana\n  username: hubserver\n  password: "%s"' % (DB_PORT, PW["hub"]), hc)
hc = re.sub(r"(?m)^servers:\n(  .*\n)+", "servers:\n  sg1:\n    address: 127.0.0.1:%d\n    label: SG-1\n" % SG_PORT, hc + "\n")
if 'enabled: true' not in hc or "127.0.0.1:%d" % SG_PORT not in hc: sys.exit("hub config: edits did not apply")
open(hub + "/plugins/Athena/config.yml", "w").write(hc)
with open(hub + "/plugins/Athena/signs.yml", "w") as f:
    f.write("signs:\n")
    for (x, y, z), room in SIGNS.values():
        f.write("- server: sg1\n" + ("  room: %d\n" % room if room else "") + "  x: %d\n  y: %d\n  z: %d\n" % (x, y, z))

# --- the SG server -----------------------------------------------------------------------------------
sg = server_dir("sg1", SG_PORT, "lobby", [(r"^allow-flight=.*", "allow-flight=true")])
shutil.copytree(os.path.join(MAIN, "bench", "run_sg", "lobby"), sg + "/lobby", ignore=shutil.ignore_patterns("playerdata", "session.lock"))
os.makedirs(sg + "/plugins/SurvivalGames/maps")
shutil.copy(os.path.abspath(a.sg), sg + "/plugins/SurvivalGames.jar")
MAPDIR = os.path.join(CONF, "plugins", "SurvivalGames", "maps")
for m in (sorted(f[:-4] for f in os.listdir(MAPDIR) if f.endswith(".yml")) if a.map == "all" else a.map.split(",")):
    shutil.copy(os.path.join(MAPDIR, m + ".yml"), sg + "/plugins/SurvivalGames/maps")
sc = open(os.path.join(CONF, "plugins", "SurvivalGames", "config.yml")).read()
sc = re.sub(r"(?m)^maps-folder: .*", "maps-folder: " + MAPS, sc)
sc, n = re.subn(r"(?m)^database:\n  enabled: true\n  host: .*\n  port: .*\n  database: .*\n  username: .*\n  password: .*",
                'database:\n  enabled: true\n  host: 127.0.0.1\n  port: %d\n  database: arcana\n  username: sgserver\n  password: "%s"' % (DB_PORT, PW["sg"]), sc)
if not n: sys.exit("SG config: no database block")
for k, v in {"lobby": 45, "pre-game": 15, "live-game": 60, "pre-deathmatch": 15, "deathmatch": 60, "end-game": 10}.items():
    sc, n = re.subn(r"(?m)^(  %s: )\d+" % k, r"\g<1>%d" % v, sc)
    if not n: sys.exit("SG config: no durations.%s" % k)
sc = re.sub(r"(?m)^(rooms|server-name|team-size): .*\n", "", sc) + "\nrooms: %d\nserver-name: sg1\nteam-size: %d\n" % (a.rooms, a.team_size)
open(sg + "/plugins/SurvivalGames/config.yml", "w").write(sc)
subprocess.run([os.path.join(HERE, "sgwatch", "build.sh"), sg + "/plugins/SgWatch.jar"], check=True, capture_output=True)

# --- start ------------------------------------------------------------------------------------------
procs = {}


def start(name, d, cmd):
    log = open(os.path.join(OUT, name + ".out"), "w")
    procs[name] = subprocess.Popen(cmd, cwd=d, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)


def wait_for(name, pattern, seconds=180):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if re.search(pattern, open(os.path.join(OUT, name + ".out"), errors="replace").read()): return True
        if procs[name].poll() is not None: return False
        time.sleep(0.5)
    return False


def console(name, line):
    procs[name].stdin.write(line + "\n"); procs[name].stdin.flush()


def stop_all():
    for name, cmd in (("hub", "stop"), ("sg1", "stop"), ("proxy", "end")):
        if name in procs and procs[name].poll() is None:
            try: console(name, cmd)
            except Exception: pass
    for name in procs:
        try: procs[name].wait(60)
        except subprocess.TimeoutExpired: procs[name].kill()
    subprocess.run(DOCKER + ["rm", "-f", DBNAME], capture_output=True)


SERVER = ["java", "-Xms384M", "-Xmx384M", "-XX:+UseParallelGC", "-XX:+UseCompactObjectHeaders", "-jar", "paperclip.jar", "nogui"]
try:
    start("proxy", proxy, ["java", "-Xmx96M", "-jar", "velocity.jar"])
    if not wait_for("proxy", r"Done \("): sys.exit("Velocity did not start")
    start("sg1", sg, SERVER)
    start("hub", hub, SERVER)
    if not wait_for("sg1", r"Done \(") or not wait_for("hub", r"Done \("): sys.exit("a server did not start")
    for (x, y, z), _ in SIGNS.values():
        console("hub", "setblock %d %d %d standing_sign 8" % (x, y, z))
    time.sleep(8)   # two pings of the SG server, so the signs show the rooms before anyone clicks

    groups = [("hop", 1, "room1", "room3"), ("r2", 24, "room2", None), ("r3", 8, "room3", None), ("any", 6, "any", None)]
    bots = []

    def bot(name, sign, hop):
        cmd = [sys.executable, os.path.join(HERE, "sgbot.py"), "--port", str(PROXY_PORT), "--name", name, "--centre", "auto",
               "--matches", "1", "--seconds", str(a.timeout - 60), "--out", os.path.join(OUT, "bots", name + ".json"),
               "--hub-sign", "%d,%d,%d" % SIGNS[sign][0]]
        if hop: cmd += ["--hop-sign", "%d,%d,%d" % SIGNS[hop][0], "--hop-after", "12"]
        bots.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(os.path.join(OUT, "bots", name + ".err"), "w")))

    t0 = time.time()
    for g, n, sign, hop in groups:
        for i in range(n):
            bot("%s%d" % (g, i), sign, hop); time.sleep(a.login_gap)
    # once room 2's 24 are in, two more ask for it
    for _ in range(60):
        if len(re.findall(r"r2\d+ joined room", open(os.path.join(OUT, "sg1.out"), errors="replace").read())) >= 24: break
        time.sleep(0.5)
    rows_mid = sql("SELECT COUNT(*) FROM sg_room_joins")
    for i in range(2):
        bot("late%d" % i, "room2", None); time.sleep(a.login_gap)
    for b in bots:
        try: b.wait(max(1, a.timeout - (time.time() - t0)))
        except subprocess.TimeoutExpired: b.kill()
    rows_end = sql("SELECT uuid, server, room, requested_at FROM sg_room_joins")
finally:
    stop_all()

def sign_text_of(js):
    try:
        o = json.loads(js)
    except ValueError:
        return js
    def walk(o):
        if isinstance(o, str): return o
        if not isinstance(o, dict): return ""
        return o.get("text", "") + "".join(walk(e) for e in o.get("extra", []) or [])
    return walk(o)


# --- report -----------------------------------------------------------------------------------------
sglog = open(os.path.join(OUT, "sg1.out"), errors="replace").read()
joined = {}
for m in re.finditer(r"(\w+) joined room (\d+)(?: \(picked (\d+) in the hub\))?", sglog):
    joined.setdefault(m.group(1), []).append([int(m.group(2)), int(m.group(3)) if m.group(3) else None])
results = {}
for f in sorted(os.listdir(os.path.join(OUT, "bots"))):
    if f.endswith(".json"):
        r = json.load(open(os.path.join(OUT, "bots", f)))
        results[r["name"]] = {"ok": r["ok"], "err": r.get("err"), "matches": r.get("matches"), "switches": r.get("switches"),
                              "signs": r.get("signs"),
                              "refused": [sign_text_of(m) for _, m in r.get("chat", []) if "full" in m.lower()]}


def errors(name):
    lines = open(os.path.join(OUT, name + ".out"), errors="replace").read().splitlines()
    return [l[:300] for l in lines if re.search(r"ERROR|SEVERE|Exception|WARN.*(sg_room_joins|room|sign)", l)][:40]


report = {"tag": a.tag, "rooms": a.rooms, "map": a.map, "joined": joined, "bots": results,
          "rows_after_room2_filled": rows_mid, "rows_left": rows_end,
          "errors": {n: errors(n) for n in ("proxy", "hub", "sg1")}}
json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1)
ok = sum(1 for r in results.values() if r["ok"])
late = [n for n in results if n.startswith("late")]
refused = [n for n in late if not results[n]["ok"] and results[n]["refused"]]
print("bots ok %d/%d (the %d late bots are meant to be refused: %d were, with %s)" % (
    ok, len(results), len(late), len(refused), sorted(set(m for n in refused for m in results[n]["refused"]))))
for g in ("hop", "r2", "r3", "any", "late"):
    names = sorted(n for n in joined if n.startswith(g))
    print(g, {n: joined[n] for n in names} if g in ("hop", "late") else
          "rooms " + str(sorted(set(tuple(map(tuple, joined[n])) for n in names))))
print("rows left in sg_room_joins:", (rows_end or "").strip() or "none")
for n in ("proxy", "hub", "sg1"):
    print(n, "errors:", len(report["errors"][n]))
print("report:", os.path.join(OUT, "report.json"))
