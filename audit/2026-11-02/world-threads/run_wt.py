"""Athena - test run for worlds on their own tick threads (feature/world-threads).

Starts a server from a paperclip jar with the WorldThreadsProbe plugin in a fresh run directory on
this machine, logs in bots (offline mode, protocol 47), and drives one scenario from the console:

  1. create the test worlds (wt-0 ... wt-N-1) and some pigs in each, spread the bots over them
  2. freeze wt-0's tick thread for --freeze-ms
  3. move bots between the worlds every half second (--shuffle-s)
  4. freeze wt-1 while the bots keep moving between worlds
  5. stop the server

and then reports, for each freeze, how far every world's full time advanced while it lasted (a
world on its own thread keeps ticking: 20 a second; a world sharing the frozen thread stops), the
longest gap each bot saw between time updates, bots that were kicked or lost, and server errors.

    python3 run_wt.py --jar ../../../paperclip/build/libs/paperclip-1.8.8-R0.1-SNAPSHOT.jar --tag base

Until worlds have their own threads, every world stops during a freeze: that is the baseline.
"""
import argparse, collections, csv, json, math, os, re, shutil, socket, struct, subprocess, sys, threading, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

p = argparse.ArgumentParser()
p.add_argument("--jar", default=os.path.join(ROOT, "paperclip", "build", "libs", "paperclip-1.8.8-R0.1-SNAPSHOT.jar"))
p.add_argument("--plugin", default=None)   # default: build it from ./plugin
p.add_argument("--tag", default="run")
p.add_argument("--dir", default=os.path.join(ROOT, "run", "wt"))
p.add_argument("--out", default=os.path.join(ROOT, "run", "wt-out"))
p.add_argument("--port", type=int, default=25640)
p.add_argument("--bots", type=int, default=12)
p.add_argument("--worlds", type=int, default=4)
p.add_argument("--pigs", type=int, default=30)
p.add_argument("--freeze-ms", type=int, default=5000)
p.add_argument("--shuffle-s", type=int, default=20)
p.add_argument("--java", default="/Users/glacier/Library/Java/JavaVirtualMachines/graalvm-jdk-25/Contents/Home/bin/java")
p.add_argument("--heap", default="512M")
p.add_argument("--jvmarg", action="append", default=[])
p.add_argument("--threaded", action="store_true")   # the test worlds tick on their own threads; bots stay in "world"
p.add_argument("--moves", action="store_true")   # threaded worlds; bots move between them and the main world, die, respawn, log in
p.add_argument("--players-threaded", action="store_true")   # bots spread over the test worlds, which then get their own threads
a = p.parse_args()

# --- protocol 47 bots ------------------------------------------------------------------------------

def vi(n):
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n: return out

def st(s):
    b = s.encode(); return vi(len(b)) + b

class R:
    def __init__(s, d, i=0): s.d, s.i = d, i
    def vi(s):
        n = sh = 0
        while True:
            b = s.d[s.i]; s.i += 1; n |= (b & 0x7F) << sh; sh += 7
            if not b & 0x80: return n
    def take(s, n):
        v = s.d[s.i:s.i + n]; s.i += n; return v
    def unpack(s, f):
        n = struct.calcsize(f); v = struct.unpack(f, s.d[s.i:s.i + n]); s.i += n; return v
    def string(s):
        return s.take(s.vi()).decode("utf8", "replace")

class Bot:
    def __init__(s, name):
        s.name = name; s.buf = b""; s.threshold = -1; s.play = False
        s.lock = threading.Lock(); s.mv = threading.Lock()
        s.dead = False; s.err = None; s.err_at = None; s.pos = None; s.left = False; s.deaths = 0; s.centre = None
        s.dim = None; s.respawns = 0; s.teleports = 0; s.corrections = 0
        s.time_updates = []          # wall-clock arrival of each Time Update (one a second per world)
        s.dims = []                  # (monotonic, dimension) at each world change
        s.s = socket.create_connection(("127.0.0.1", a.port), 30); s.s.settimeout(60)

    def send(s, pid, payload=b""):
        body = vi(pid) + payload
        if s.threshold >= 0:
            body = (vi(len(body)) + zlib.compress(body)) if len(body) >= s.threshold else (vi(0) + body)
        with s.lock:
            s.s.sendall(vi(len(body)) + body)

    def _fill(s, n):
        while len(s.buf) < n:
            c = s.s.recv(1 << 16)
            if not c: raise EOFError
            s.buf += c

    def read(s):
        n = sh = 0
        while True:
            s._fill(1); b = s.buf[0]; s.buf = s.buf[1:]
            n |= (b & 0x7F) << sh; sh += 7
            if not b & 0x80: break
        s._fill(n); frame, s.buf = s.buf[:n], s.buf[n:]
        r = R(frame)
        if s.threshold >= 0:
            ulen = r.vi(); data = frame[r.i:]
            if ulen: data = zlib.decompress(data)
            r = R(data)
        return r.vi(), r

    def reader(s):
        try:
            while not s.dead:
                pid, r = s.read()
                if not s.play:
                    if pid == 0x03: s.threshold = r.vi()
                    elif pid == 0x02: s.play = True
                    elif pid == 0x00: s.err = "DISCONNECT " + r.string(); s.dead = True
                    continue
                if pid == 0x00: s.send(0x00, vi(r.vi()))
                elif pid == 0x01:
                    r.unpack(">iB"); s.dim, = r.unpack(">b"); s.dims.append((time.monotonic(), s.dim))
                elif pid == 0x03: s.time_updates.append(time.time())
                elif pid == 0x07:
                    s.dim, = r.unpack(">i"); s.respawns += 1; s.dims.append((time.monotonic(), s.dim))
                elif pid == 0x08:
                    x, y, z, yaw, pitch, flags = r.unpack(">dddffb")
                    with s.mv:
                        s.send(0x06, struct.pack(">dddff?", x, y, z, yaw, pitch, True))
                        if s.pos is not None and math.dist(s.pos, (x, y, z)) < 8.0:
                            s.corrections += 1
                        else:
                            s.teleports += 1; s.centre = (x, y, z)
                        s.pos = [x, y, z]
                elif pid == 0x06:
                    health, = r.unpack(">f")
                    if health <= 0:
                        s.deaths += 1; s.send(0x16, vi(0))   # Client Status: perform respawn
                elif pid == 0x40:
                    s.err = "KICKED " + r.string(); s.dead = True
        except Exception as e:
            if not s.dead: s.err = "reader stopped: %s %s" % (type(e).__name__, e)
            s.dead = True
        finally:
            s.err_at = time.time()

    def walker(s):
        """Walks a small circle around the last place the server put us, 20 steps a second."""
        k = 0
        while not s.dead:
            time.sleep(0.05); k += 1
            with s.mv:
                if s.centre is None: continue
                ang = k * 0.08
                x = s.centre[0] + 2.0 * math.cos(ang); z = s.centre[2] + 2.0 * math.sin(ang)
                try:
                    s.send(0x04, struct.pack(">ddd?", x, s.centre[1], z, True))
                except OSError:
                    s.dead = True; return
                s.pos = [x, s.centre[1], z]

    def chat(s, text):
        s.send(0x01, st(text))

    def leave(s):
        """Closes the connection on purpose: not a lost bot."""
        s.left = True; s.dead = True
        try: s.s.close()
        except OSError: pass

    def start(s):
        s.send(0x00, vi(47) + st("127.0.0.1") + struct.pack(">H", a.port) + vi(2))
        s.send(0x00, st(s.name))
        threading.Thread(target=s.reader, daemon=True).start()
        threading.Thread(target=s.walker, daemon=True).start()

    def gaps(s, longer_than=1.5):
        out = []; t = s.time_updates
        for i in range(1, len(t)):
            if t[i] - t[i - 1] > longer_than:
                out.append((t[i - 1], t[i] - t[i - 1]))
        return out

# --- the server -------------------------------------------------------------------------------------

def prepare():
    shutil.rmtree(a.dir, ignore_errors=True)
    os.makedirs(os.path.join(a.dir, "plugins"))
    plugin = a.plugin
    if not plugin:
        plugin = os.path.join(a.dir, "plugins", "wtprobe.jar")
        subprocess.run([os.path.join(HERE, "plugin", "build.sh"), plugin], check=True)
    else:
        shutil.copy(plugin, os.path.join(a.dir, "plugins", "wtprobe.jar"))
    with open(os.path.join(a.dir, "eula.txt"), "w") as f: f.write("eula=true\n")
    with open(os.path.join(a.dir, "server.properties"), "w") as f:
        f.write("\n".join([
            "online-mode=false", "server-port=%d" % a.port, "level-type=FLAT", "generate-structures=false",
            "spawn-animals=false", "spawn-monsters=false", "spawn-npcs=false", "view-distance=4",
            "max-players=200", "spawn-protection=0", "allow-nether=false", "motd=wt", ""]))
    with open(os.path.join(a.dir, "bukkit.yml"), "w") as f:
        f.write("settings:\n  allow-end: false\n")
    # a time update every second (PandaSpigot's default is every 5 s): the bots read a gap in them as
    # their world not ticking
    with open(os.path.join(a.dir, "pandaspigot.yml"), "w") as f:
        f.write("worlds:\n  default:\n    timeUpdateFrequency: 20\n")

class Server:
    def __init__(s):
        s.log = open(os.path.join(a.out, "server.log"), "w")
        s.lines = []; s.cv = threading.Condition()
        cmd = [a.java, "-Xms" + a.heap, "-Xmx" + a.heap, "-XX:+UseParallelGC"] + a.jvmarg + \
              ["-Dcom.mojang.eula.agree=true", "-jar", os.path.abspath(a.jar), "nogui"]
        s.p = subprocess.Popen(cmd, cwd=a.dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=s.pump, daemon=True).start()

    def pump(s):
        for line in s.p.stdout:
            s.log.write(line); s.log.flush()
            with s.cv:
                s.lines.append(line); s.cv.notify_all()

    def wait_for(s, pattern, timeout=120, since=0):
        rx = re.compile(pattern); end = time.time() + timeout
        with s.cv:
            while True:
                for i in range(since, len(s.lines)):
                    if rx.search(s.lines[i]): return i
                left = end - time.time()
                if left <= 0 or s.p.poll() is not None: return -1
                s.cv.wait(left)

    def cmd(s, c):
        mark = len(s.lines)
        s.p.stdin.write(c + "\n"); s.p.stdin.flush()
        return mark

# --- the scenario ------------------------------------------------------------------------------------

def main():
    shutil.rmtree(a.out, ignore_errors=True); os.makedirs(a.out)
    prepare()
    srv = Server()
    if srv.wait_for(r"Done \(", 240) < 0:
        print("server did not start; see", os.path.join(a.out, "server.log")); sys.exit(1)
    srv.wait_for(r"\[WorldThreadsProbe\] ready", 30)

    mark = srv.cmd("wt create %d%s" % (a.worlds, " threaded" if a.threaded else ""))
    if srv.wait_for(r"wt created", 120, mark) < 0:
        print("worlds not created"); srv.cmd("stop"); sys.exit(1)
    for i in range(a.worlds):
        srv.cmd("wt spawn wt-%d %d" % (i, a.pigs))

    bots = []
    for i in range(a.bots):
        b = Bot("wt%02d" % i); b.start(); bots.append(b); time.sleep(0.15)
    t0 = time.time()
    while time.time() - t0 < 60 and any(b.pos is None and not b.dead for b in bots):
        time.sleep(0.1)
    spawned = sum(1 for b in bots if b.pos is not None)

    if not a.threaded:
        srv.cmd("wt distribute")
    time.sleep(5)

    freezes = []
    def freeze(world, ms):
        mark = srv.cmd("wt freeze %s %d" % (world, ms))
        i = srv.wait_for(r"wt freezing %s .* at (\d+)" % world, 30, mark)
        j = srv.wait_for(r"wt froze %s until (\d+)" % world, 30 + ms / 1000, mark)
        if i >= 0 and j >= 0:
            m1 = re.search(r"on (.+?) for .* at (\d+)", srv.lines[i]); m2 = re.search(r"until (\d+)", srv.lines[j])
            freezes.append({"world": world, "thread": m1.group(1), "start_ms": int(m1.group(2)), "end_ms": int(m2.group(1)),
                            "mono_end": time.monotonic()})

    where = {}
    check = None
    if a.moves:
        # every test world on its own thread from the start; the bots begin in the main world and
        # are moved between all of them (Athena.teleportAsync) while one world freezes, some die and
        # respawn, and two more log in aimed straight at a threaded world
        for i in range(a.worlds):
            mark = srv.cmd("wt thread wt-%d on" % i)
            srv.wait_for(r"wt thread wt-%d on" % i, 30, mark)
        srv.cmd("wt joinworld wt-2")
        mark = srv.cmd("wt shuffle 10 %d" % a.shuffle_s)
        time.sleep(a.shuffle_s / 4)
        freeze("wt-0", a.freeze_ms)
        srv.cmd("wt kill 4")
        for k in range(2):
            b = Bot("wtj%d" % k); b.start(); bots.append(b); time.sleep(0.3)
        srv.wait_for(r"wt shuffle done", a.shuffle_s + 60)
        time.sleep(3)
        mark = srv.cmd("wt preload wt-3 160")
        srv.wait_for(r"wt preloaded", 60, mark)
        mark = srv.cmd("wt gather wt-1")
        srv.wait_for(r"wt gathered", 60, mark)
        time.sleep(2)
        mark = srv.cmd("wt check")
        i = srv.wait_for(r"wt check online", 30, mark)
        if i >= 0:
            check = srv.lines[i].strip()
        mark = srv.cmd("wt where"); time.sleep(1.5)
        for l in srv.lines[mark:]:
            m = re.search(r"wt where (\S+) (\S+)", l)
            if m: where[m.group(1)] = m.group(2)
    elif a.players_threaded:
        # the bots are spread over the test worlds, which then move to their own threads with them
        # inside: their packets, connection ticks, commands and quits must follow
        srv.cmd("wt distribute"); time.sleep(5)
        for i in range(a.worlds):
            mark = srv.cmd("wt thread wt-%d on" % i)
            srv.wait_for(r"wt thread wt-%d on" % i, 30, mark)
        time.sleep(3)
        mark = srv.cmd("wt where"); time.sleep(1.5)
        for l in srv.lines[mark:]:
            m = re.search(r"wt where (\S+) (\S+)", l)
            if m: where[m.group(1)] = m.group(2)
        for b in bots: b.chat("/wtping")
        time.sleep(2)
        freeze("wt-0", a.freeze_ms); time.sleep(3)
        freeze("world", a.freeze_ms); time.sleep(3)
        for b in bots[:a.bots // 2]: b.leave()
        time.sleep(3)
    elif a.threaded:
        # players cannot enter threaded worlds yet: freeze one of them, then the server thread, then
        # hand wt-1 back to the server thread and freeze that again
        freeze("wt-0", a.freeze_ms); time.sleep(3)
        freeze("world", a.freeze_ms); time.sleep(3)
        mark = srv.cmd("wt thread wt-1 off")
        srv.wait_for(r"wt thread wt-1 off", 30, mark); time.sleep(2)
        freeze("world", a.freeze_ms); time.sleep(2)
    else:
        freeze("wt-0", a.freeze_ms); time.sleep(3)
        srv.cmd("wt shuffle 10 %d" % a.shuffle_s)
        time.sleep(a.shuffle_s / 2)
        freeze("wt-1", a.freeze_ms)
        srv.wait_for(r"wt shuffle done", a.shuffle_s + 30)
        time.sleep(2)
    mark = srv.cmd("wt status"); time.sleep(1)
    status = [l.strip() for l in srv.lines[mark:] if "wt status" in l]
    srv.cmd("wt dump"); time.sleep(1)
    alive = sum(1 for b in bots if not b.dead)
    stopped_at = time.time()
    srv.cmd("stop")
    try:
        srv.p.wait(60)
    except subprocess.TimeoutExpired:
        srv.p.kill()

    # --- report --------------------------------------------------------------------------------------
    data = os.path.join(a.dir, "plugins", "WorldThreadsProbe")
    shutil.copy(os.path.join(data, "ticks.csv"), a.out)
    shutil.copy(os.path.join(data, "threads.csv"), a.out)
    samples = collections.defaultdict(list)
    for row in csv.DictReader(open(os.path.join(a.out, "ticks.csv"))):
        samples[row["world"]].append((int(row["ms"]), int(row["full_time"])))

    def advance(world, t1, t2):
        s = samples[world]
        before = [v for t, v in s if t <= t1]; after = [v for t, v in s if t >= t2]
        if not before or not after: return None
        return after[0] - before[-1]

    report = {"tag": a.tag, "bots": a.bots, "spawned": spawned, "alive_at_end": alive, "freezes": [], "bots_detail": []}
    for f in freezes:
        secs = (f["end_ms"] - f["start_ms"]) / 1000.0
        # trim 300 ms each side so the sampler's 100 ms grid does not count ticks just outside the freeze
        t1, t2 = f["start_ms"] + 300, f["end_ms"] - 300
        per = {}
        for w in samples:
            adv = advance(w, t1, t2)
            per[w] = None if adv is None else round(adv / ((t2 - t1) / 1000.0), 1)
        report["freezes"].append({"world": f["world"], "thread": f["thread"], "seconds": secs, "ticks_per_s": per})
    # a gap in a bot's time updates that overlaps a freeze is expected while worlds share the frozen
    # thread; one that does not is a stall the freezes do not explain
    windows = [(f["start_ms"] / 1000.0 - 1.5, f["end_ms"] / 1000.0 + 1.5) for f in freezes]
    def in_freeze(t, d): return any(t < w2 and t + d > w1 for w1, w2 in windows)
    for b in bots:
        g = [(t, d) for t, d in b.gaps() if t < stopped_at]
        other = [d for t, d in g if not in_freeze(t, d)]
        lost = b.err if b.err_at is not None and b.err_at < stopped_at and not b.left else None
        report["bots_detail"].append({"name": b.name, "lost": lost, "respawns": b.respawns, "teleports": b.teleports,
                                      "corrections": b.corrections,
                                      "longest_gap_in_freeze_s": round(max([d for t, d in g if in_freeze(t, d)], default=0), 2),
                                      "longest_gap_outside_s": round(max(other, default=0), 2), "gaps_outside": len(other),
                                      "world": where.get(b.name), "left": b.left,
                                      "gapped_in": [f["world"] + "#" + str(k) for k, f in enumerate(freezes)
                                                    if any(t < f["end_ms"] / 1000.0 + 0.5 and t + d > f["start_ms"] / 1000.0 for t, d in g)]})
    log = open(os.path.join(a.out, "server.log")).read()
    log = log[log.find("Done ("):]   # the start's own warnings (offline mode, EULA flag) are not the run's
    # a bot closing its socket on purpose logs "lost connection: Internal Exception: ... reset": not an error
    log = "\n".join(l for l in log.split("\n") if "lost connection" not in l)
    report["server"] = {"errors": len(re.findall(r"\bERROR\]|Exception", log)),
                        "warnings": len(re.findall(r"WARN\]", log)),
                        "moved_wrongly": log.count("moved wrongly"), "status": status}
    report["threads"] = [r for r in csv.DictReader(open(os.path.join(a.out, "threads.csv")))]
    json.dump(report, open(os.path.join(a.out, "report.json"), "w"), indent=1)

    print("== %s: bots %d spawned %d alive %d | server errors %d warnings %d" % (
        a.tag, a.bots, spawned, alive, report["server"]["errors"], report["server"]["warnings"]))
    for f in report["freezes"]:
        print("freeze %s (%.1f s on %s): ticks/s per world %s" % (f["world"], f["seconds"], f["thread"],
              " ".join("%s=%s" % kv for kv in sorted(f["ticks_per_s"].items()))))
    d = report["bots_detail"]; errs = [b for b in d if b["lost"]]
    print("bots: respawns %d, corrections %d, longest time-update gap %.1f s in a freeze, %.1f s outside (%d gaps), lost %d %s" % (
        sum(b["respawns"] for b in d), sum(b["corrections"] for b in d), max(b["longest_gap_in_freeze_s"] for b in d),
        max(b["longest_gap_outside_s"] for b in d), sum(b["gaps_outside"] for b in d), len(errs),
        [(b["name"], b["lost"]) for b in errs][:3]))
    threads = collections.Counter()
    for r in report["threads"]: threads[r["thread"]] += int(r["count"])
    print("event threads:", dict(threads))
    if a.moves:
        log_text = open(os.path.join(a.out, "server.log")).read()
        m = re.search(r"wt shuffle done: (\d+) moves, (\d+) refused", log_text)
        print("moves:", m.group(0) if m else "shuffle did not finish")
        print("deaths %d, world changes seen by the bots %d" % (sum(b.deaths for b in bots), sum(b.respawns for b in bots)))
        joined = [b for b in bots if b.name.startswith("wtj")]
        print("logins aimed at wt-2:", [(b.name, len(b.dims), where.get(b.name)) for b in joined])
        arrivals = re.findall(r"wt arrived (wtj\d) (\S+) -> (\S+) on (.+)", log_text)
        print("first arrival of each login:", {n: (f, t, th) for n, f, t, th in reversed(arrivals)})
        # the probe warns when a player arrives on a thread that does not tick its new world at that moment
        print("arrivals off the new world's thread:", log_text.count("wt arrived off its world's thread"))
        for l in log_text.split("\n"):
            if "wt preloaded" in l or "wt gathered" in l: print(l.split("]: ")[-1])
        print("check:", check)
        print("where:", dict(collections.Counter(where.values())))
    if a.players_threaded:
        for k, f in enumerate(freezes):
            hit = collections.Counter(b["world"] for b in d if f["world"] + "#" + str(k) in b["gapped_in"])
            print("freeze %s: bots with a time-update gap, by their world: %s" % (f["world"], dict(hit) or "none"))
        pings = [l.strip() for l in open(os.path.join(a.out, "server.log")) if "[WorldThreadsProbe] wtping" in l]
        ok = sum(1 for l in pings if re.search(r"in (\S+) on World thread - \1$", l))
        print("commands: %d run, %d on the thread of the player's world" % (len(pings), ok))
        quits = collections.Counter()
        for r in report["threads"]:
            if r["event"] == "quit": quits[(r["world"], r["thread"])] += int(r["count"])
        print("quit events by world and thread:", dict(quits))
    print("report:", os.path.join(a.out, "report.json"))

main()
