"""Athena - one player through a whole SurvivalGames (MCSG) match, for the plugin's load test.

flybot2 flies a fixed circle; a match is more than that. This bot follows the match by its chat
messages and plays each phase the way a player loads the server:

  lobby / pre-game / pre-deathmatch / end   stand still (one flying packet per tick, as a client)
  live game     tour the loot chests it has seen in its chunks: fly over, drop onto the lid, open
                it, shift-click everything out, close it; chase and punch any player that comes
                within --chase blocks
  deathmatch    chase and punch the nearest player
  spectator     (after an elimination) roam the map at cruising height

It flies (the bench server has allow-flight=true; the plugin turns flight off for players) at about
sprint-jump speed, and keeps moving from wherever the server last put it, so a correction (a roof,
a tree) costs it a few ticks instead of the whole route.

    python3 sgbot.py --port 25566 --name Bot0 --centre 143,15 --roam 200 --matches 1 --out bot0.json

Through Velocity and the hub (AthenaHub), --hub-sign X,Y,Z right-clicks that join sign when the bot
lands on the hub, and plays from the server the sign sends it to. --hop-sign X,Y,Z --hop-after S goes
back to the hub (/hub) S seconds into its first SG lobby and right-clicks the other sign. --centre auto
takes the map's centre from where the pre-game puts the bot (for rooms on different maps).
"""
import socket, struct, zlib, sys, time, math, threading, collections, json, argparse, os, random, re

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, required=True)
p.add_argument("--name", required=True)
p.add_argument("--out", required=True)
p.add_argument("--centre", required=True)               # the map's centre "x,z"
p.add_argument("--roam", type=float, default=200.0)     # chests / waypoints within this of the centre
p.add_argument("--chase", type=float, default=10.0)     # live game: chase players this close
p.add_argument("--chase-chance", type=float, default=0.35)
p.add_argument("--matches", type=int, default=1)        # leave after this many matches
p.add_argument("--seconds", type=float, default=3600.0) # or after this long
p.add_argument("--seed", type=int, default=None)
# spread: in the live game, roam this bot's own sector of the map (sector k of n) and nothing else,
# so twenty bots hold as many distinct chunks as twenty players can: the memory upper bound
p.add_argument("--spread", default=None)                # "k/n"
p.add_argument("--hub-sign", default=None)              # "x,y,z": the hub's join sign to right-click first
p.add_argument("--hop-sign", default=None)              # "x,y,z": back to the hub from the SG lobby, then this sign
p.add_argument("--hop-after", type=float, default=15.0)
a = p.parse_args()
rnd = random.Random(a.seed if a.seed is not None else a.name)

HOST = os.environ.get("BOT_HOST", "127.0.0.1")
DT = 0.05
SPEED = 0.35        # blocks per tick horizontally (~7 b/s, sprint-jumping)
VSPEED = 0.5
REACH = 3.2         # punch from this close
AUTO_CENTRE = a.centre == "auto"
CX, CZ = (0.0, 0.0) if AUTO_CENTRE else [float(v) for v in a.centre.split(",")]


def vi(n):
    n &= 0xFFFFFFFF   # a VarInt is 32 bits: a negative one (Velocity's keep-alive ids) is written as its two's complement
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n: return out


def st(s):
    b = s.encode(); return vi(len(b)) + b


class R:
    def __init__(s, d, i=0): s.d, s.i = d, i
    def u8(s):
        v = s.d[s.i]; s.i += 1; return v
    def vi(s):
        n = sh = 0
        while True:
            b = s.u8(); n |= (b & 0x7F) << sh; sh += 7
            if not b & 0x80: break
        return n - (1 << 32) if n >= 1 << 31 else n
    def take(s, n):
        v = s.d[s.i:s.i + n]; s.i += n; return v
    def unpack(s, f):
        v = struct.unpack_from(f, s.d, s.i); s.i += struct.calcsize(f); return v
    def string(s):
        return s.take(s.vi()).decode("utf8", "replace")


def skip_nbt(r, t):
    if t in (1,): r.i += 1
    elif t == 2: r.i += 2
    elif t in (3, 5): r.i += 4
    elif t in (4, 6): r.i += 8
    elif t == 7: n, = r.unpack(">i"); r.i += n
    elif t == 8: n, = r.unpack(">H"); r.i += n
    elif t == 9:
        et = r.u8(); n, = r.unpack(">i")
        for _ in range(n): skip_nbt(r, et)
    elif t == 10:
        while True:
            et = r.u8()
            if et == 0: return
            n, = r.unpack(">H"); r.i += n
            skip_nbt(r, et)
    elif t == 11: n, = r.unpack(">i"); r.i += 4 * n
    else: raise ValueError("nbt %d" % t)


def read_slot(r):
    """The slot's raw bytes (to echo back in a click) and whether it holds anything."""
    s = r.i
    item, = r.unpack(">h")
    if item == -1:
        return r.d[s:r.i], False
    r.i += 3            # count, damage
    t = r.u8()
    if t:
        n, = r.unpack(">H"); r.i += n
        skip_nbt(r, t)
    return r.d[s:r.i], True


def packpos(x, y, z):
    return struct.pack(">Q", ((x & 0x3FFFFFF) << 38) | ((y & 0xFFF) << 26) | (z & 0x3FFFFFF))


def unpackpos(v):
    x, y, z = v >> 38, (v >> 26) & 0xFFF, v & 0x3FFFFFF
    if x >= 1 << 25: x -= 1 << 26
    if z >= 1 << 25: z -= 1 << 26
    return x, y, z


def sign_text(js):
    """A sign line's JSON chat component, as plain text."""
    try:
        o = json.loads(js)
    except ValueError:
        return js
    if isinstance(o, str): return o
    if not isinstance(o, dict): return "" if o is None else str(o)
    out = o.get("text", "")
    for e in o.get("extra", []) or []:
        out += e if isinstance(e, str) else sign_text(json.dumps(e))
    return out


# chest (54) and ender chest (130) block states, little-endian (id << 4 | meta) in a 1.8 section
CHEST_RE = re.compile(rb"[\x60-\x6f]\x03|[\x20-\x2f]\x08")


class Bot:
    def __init__(s):
        s.s = socket.create_connection((HOST, a.port), 30); s.s.settimeout(90)
        s.buf = b""; s.threshold = -1; s.lock = threading.Lock(); s.play = False
        s.dead = False; s.err = None
        s.corr_times = collections.deque(maxlen=16)
        s.mv = threading.Lock(); s.corr_log = []
        s.mode = "idle"; s.chatlog = collections.deque(maxlen=40)
        s.pos = None; s.teleports = 0; s.corrections = 0; s.last_tp = 0.0; s.tp_log = []; s.chunk_log = []
        s.eid = None; s.spectator = False
        s.players = {}                       # eid -> [x, y, z]
        s.chests = {}                        # (cx, cz) -> [(x, y, z)]
        s.window = None; s.window_slots = {}; s.window_size = 0
        s.phase = "connect"; s.phases = []   # (unix time, phase)
        s.matches_done = 0
        s.stats = collections.Counter()
        s.sent = collections.Counter()   # Athena - packets sent per phase, to check the bot keeps up
        s.have = set(); s.dropped = set()   # Athena - chunk columns this client holds / was told to drop
        s.rx = collections.Counter()     # Athena - packets received per "phase pid", and their bytes per "phase pid b"
        s.ticks = []
        s.joins = 0                      # Join Game packets: one at login, one more for every server switch (Velocity)
        s.switches = []                  # (unix time, what, ok)
        s.signs = {}                     # "x,y,z" -> the sign's lines, as the server last sent them
        s.sign_log = []                  # (unix time, "x,y,z", lines) every time a sign changed
        s.last_rx = collections.deque(maxlen=12)   # (unix time, packet id): what came last, for a bot that stops hearing

    def send(s, pid, payload=b""):
        body = vi(pid) + payload
        if s.threshold >= 0:
            body = (vi(len(body)) + zlib.compress(body)) if len(body) >= s.threshold else (vi(0) + body)
        with s.lock:
            s.s.sendall(vi(len(body)) + body)
            s.sent["m%d %s" % (s.matches_done + 1, s.phase)] += 1

    def _fill(s, n):
        s.want = n
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

    def set_phase(s, ph):
        if ph != s.phase:
            s.phase = ph; s.phases.append((time.time(), ph))

    # --- what the server says ------------------------------------------------------------------
    def chat(s, text):
        s.chatlog.append((round(time.time(), 1), text[:300]))
        if "until lobby ends" in text or "Waiting in the lobby" in text: s.set_phase("lobby")
        elif "Map:" in text and "MCSG" in text: s.set_phase("starting")
        elif "before the games begin" in text or "until the games begin" in text: s.set_phase("pregame")
        elif "The games have begun" in text: s.set_phase("live")
        elif "seconds for all players to load" in text or ("until deathmatch!" in text and s.phase != "live"):
            s.set_phase("predeathmatch")
        elif "Fight to the death" in text: s.set_phase("deathmatch")
        elif "The games have ended" in text: s.set_phase("end")
        elif "cleaning up" in text:
            s.set_phase("cleanup"); s.matches_done += 1

    def chunk(s, cx, cz, mask, data, at, sky):
        """Records the chests in one chunk column's block arrays, which start at data[at]."""
        key = (cx, cz)
        s.chests.pop(key, None)
        if not mask:
            return at
        found = []
        n = bin(mask).count("1")
        sec = 0
        for y in range(16):
            if not mask >> y & 1: continue
            base = at + sec * 8192
            blocks = data[base:base + 8192]
            for m in CHEST_RE.finditer(blocks):
                i = m.start()
                if i & 1: continue
                i >>= 1
                found.append((cx * 16 + (i & 15), y * 16 + (i >> 8), cz * 16 + (i >> 4 & 15)))
            sec += 1
        if found:
            s.chests[key] = found
        return at + n * 8192 + n * 2048 + (n * 2048 if sky else 0)

    def reader(s):
        try:
            while not s.dead:
                pid, r = s.read()
                s.last_rx.append((round(time.time(), 2), pid if s.play else "login %d" % pid))
                if s.play:
                    ph = "m%d %s" % (s.matches_done + 1, s.phase)   # Athena - per match, so a second match shows the JVM warm
                    s.rx["%s %02x" % (ph, pid)] += 1; s.rx["%s %02x b" % (ph, pid)] += len(r.d) - r.i
                    if pid == 0x3D:   # Athena - Display Scoreboard: position (0 list, 1 sidebar, 2 below name) and objective
                        try:
                            rr = R(r.d, r.i); pos = rr.u8()
                            s.rx["%s 3d pos%d %s" % (ph, pos, rr.string() or "(none)")] += 1
                        except Exception:
                            pass
                    if pid in (0x29, 0x2B):   # Athena - which sound / which game state change
                        try:
                            rr = R(r.d, r.i)
                            what = rr.string() if pid == 0x29 else "reason %d" % r.d[r.i]
                            s.rx["%s %02x %s" % (ph, pid, what)] += 1
                            if what == "random.click":
                                x, y, z = rr.unpack(">iii")
                                s.rx["clickpos %d,%d,%d" % (x // 8, y // 8, z // 8)] += 1
                        except Exception:
                            pass
                if not s.play:
                    if pid == 0x03: s.threshold = r.vi(); continue
                    if pid == 0x02: s.play = True; continue
                    if pid == 0x00: s.err = "DISCONNECT " + r.string(); s.dead = True; return
                    continue
                if pid == 0x00: s.send(0x00, vi(r.vi()))
                elif pid == 0x01:
                    # a server switch through Velocity starts with a Join Game: wait for the new server's position
                    s.eid, = r.unpack(">i"); s.joins += 1; s.pos = None
                    s.players.clear(); s.chests.clear(); s.window = None; s.have.clear(); s.dropped.clear()
                elif pid == 0x33:
                    x, y, z = unpackpos(r.unpack(">q")[0])
                    lines = [sign_text(r.string()) for _ in range(4)]
                    key = "%d,%d,%d" % (x, y, z)
                    if s.signs.get(key) != lines:
                        s.signs[key] = lines
                        if len(s.sign_log) < 400: s.sign_log.append([round(time.time(), 1), key, lines])
                elif pid == 0x02: s.chat(r.string())
                elif pid == 0x03: s.ticks.append((time.monotonic(), r.unpack(">q")[0]))
                elif pid == 0x07:                      # respawn: a world change
                    dim, = r.unpack(">i")
                    s.tp_log.append([round(time.time(), 2), "respawn", dim])
                    s.players.clear(); s.chests.clear(); s.window = None
                    s.have.clear(); s.dropped.clear()
                elif pid == 0x08:
                    x, y, z, yaw, pitch, flags = r.unpack(">dddffb")
                    with s.mv:
                        # under the move lock: the main loop must not send a step computed from the old
                        # position between the server's correction and our acknowledgement of it
                        s.send(0x06, struct.pack(">dddff?", x, y, z, yaw, pitch, False))
                        if s.pos is not None and math.dist(s.pos, (x, y, z)) < 12.0:
                            s.corrections += 1; s.stats["corrections"] += 1; s.corr_times.append(time.time())
                            s.stats["corr_" + s.mode] += 1
                            if len(s.corr_log) < 60:
                                s.corr_log.append([round(time.time(), 2), s.mode, [round(v, 2) for v in s.pos], [round(x, 2), round(y, 2), round(z, 2)], flags])
                        else:
                            s.teleports += 1; s.last_tp = time.time()
                            s.tp_log.append([round(time.time(), 2), round(x, 1), round(y, 1), round(z, 1)])
                        s.pos = [x, y, z]
                elif pid == 0x0C:
                    e = r.vi(); r.take(16); x, y, z = r.unpack(">iii")
                    s.players[e] = [x / 32.0, y / 32.0, z / 32.0]
                elif pid == 0x13:
                    for _ in range(r.vi()): s.players.pop(r.vi(), None)
                elif pid in (0x15, 0x17):
                    e = r.vi(); dx, dy, dz = r.unpack(">bbb")
                    q = s.players.get(e)
                    if q: q[0] += dx / 32.0; q[1] += dy / 32.0; q[2] += dz / 32.0
                elif pid == 0x18:
                    e = r.vi(); x, y, z = r.unpack(">iii")
                    if e in s.players: s.players[e] = [x / 32.0, y / 32.0, z / 32.0]
                elif pid == 0x21:
                    cx, cz = r.unpack(">ii"); r.u8(); mask, = r.unpack(">H"); r.vi()
                    # Athena - full sends vs unloads, and sends of a chunk this client already has (dup) or
                    # had and was told to drop (resend); reset by a world change (respawn)
                    if cx < -40 and len(s.chunk_log) < 4000:   # the deathmatch arena's chunks (bench: arena tests)
                        s.chunk_log.append([round(time.time(), 2), cx, cz, mask])
                    if mask:
                        s.rx[ph + " chunk sent"] += 1
                        if (cx, cz) in s.have: s.rx[ph + " chunk dup"] += 1
                        elif (cx, cz) in s.dropped: s.rx[ph + " chunk resend"] += 1
                        s.have.add((cx, cz)); s.dropped.discard((cx, cz))
                    else:
                        s.rx[ph + " chunk unload"] += 1
                        s.have.discard((cx, cz)); s.dropped.add((cx, cz))
                    s.chunk(cx, cz, mask, r.d, r.i, True)
                elif pid == 0x26:
                    sky = r.u8(); n = r.vi()
                    metas = [r.unpack(">iiH") for _ in range(n)]
                    at = r.i
                    for cx, cz, mask in metas:
                        if cx < -40 and len(s.chunk_log) < 4000:
                            s.chunk_log.append([round(time.time(), 2), cx, cz, mask])
                        at = s.chunk(cx, cz, mask, r.d, at, sky) + 256
                elif pid == 0x2B:
                    reason = r.u8(); val, = r.unpack(">f")
                    if reason == 3: s.spectator = int(val) == 3
                elif pid == 0x2D:
                    wid = r.u8(); r.string(); r.string(); size = r.u8()
                    if wid: s.window = wid; s.window_size = size; s.window_slots = {}; s.stats["chests_opened"] += 1
                elif pid == 0x2E:
                    s.window = None
                elif pid == 0x30:
                    wid = r.u8(); n, = r.unpack(">h")
                    if wid == s.window:
                        s.window_slots = {}
                        for k in range(n):
                            raw, full = read_slot(r)
                            if full and k < s.window_size: s.window_slots[k] = raw
                elif pid == 0x2F:
                    wid, = r.unpack(">b"); slot, = r.unpack(">h")
                    if wid == s.window and 0 <= slot < s.window_size:
                        raw, full = read_slot(r)
                        if full: s.window_slots[slot] = raw
                        else: s.window_slots.pop(slot, None)
                elif pid == 0x32:
                    wid, = r.unpack(">b"); act, = r.unpack(">h"); ok = r.u8()
                    if not ok: s.send(0x0F, struct.pack(">bh?", wid, act, True))
                elif pid == 0x06:
                    s.health, = r.unpack(">f")
                    s.stats["health_min_x10"] = min(s.stats.get("health_min_x10", 200), int(s.health * 10))
                elif pid == 0x40:
                    s.err = "KICKED " + r.string(); s.dead = True; return
        except Exception as e:
            if not s.dead: s.err = "reader stopped: %s %s" % (type(e).__name__, e)
            s.dead = True


b = Bot()
b.send(0x00, vi(47) + st(HOST) + struct.pack(">H", a.port) + vi(2))
b.send(0x00, st(a.name))
reader = threading.Thread(target=b.reader, daemon=True); reader.start()


def wait_spawn(seconds):
    t0 = time.time()
    while b.pos is None and not b.dead and time.time() - t0 < seconds:
        time.sleep(0.05)
    return b.pos is not None


def still(seconds):
    """Wait as a client does: one flying packet a tick (a client never goes quiet while it waits for a server)."""
    t0 = time.time()
    while not b.dead and time.time() - t0 < seconds:
        b.send(0x03, b"\x01"); time.sleep(DT)


def switch(what, act):
    """act() asks for a server switch (a sign, /hub); waits for the next server's Join Game and position."""
    joins = b.joins
    act()
    t0 = time.time()
    while b.joins == joins and not b.dead and time.time() - t0 < 30:
        still(DT)
    ok = b.joins != joins and wait_spawn(30)
    b.switches.append([round(time.time(), 2), what, ok])
    return ok


def click_sign(where):
    x, y, z = [int(v) for v in where.split(",")]
    return lambda: b.send(0x08, packpos(x, y, z) + b"\x01" + struct.pack(">h", -1) + b"\x08\x08\x08")


def fail(err):
    b.err = b.err or err
    import faulthandler
    faulthandler.dump_traceback(sys.stderr, all_threads=True)   # where the reader waits, when the bot stops hearing
    json.dump({"name": a.name, "ok": False, "err": b.err, "switches": b.switches, "signs": b.signs, "joins": b.joins,
               "last_rx": list(b.last_rx), "reader_alive": reader.is_alive(), "at": round(time.time(), 2),
               "buffered": len(b.buf), "wanted": getattr(b, "want", None), "threshold": b.threshold,
               "sign_log": b.sign_log, "chat": list(b.chatlog)}, open(a.out, "w"))
    sys.exit(1)


if not wait_spawn(60):
    fail("never spawned")
if a.hub_sign:
    still(1.0 + rnd.random())   # the sign's lines arrive with its chunk; a player reads before clicking
    if not switch("sign " + a.hub_sign, click_sign(a.hub_sign)):
        fail("the hub sign %s did not send us anywhere" % a.hub_sign)
hop_due = time.time() + a.hop_after if a.hop_sign else None


# --- moving -----------------------------------------------------------------------------------
def step_toward(tx, ty, tz, speed=SPEED):
    """One tick's move toward (tx, ty, tz) from where the server has us. True when there."""
    with b.mv:
        return _step(tx, ty, tz, speed)


def _step(tx, ty, tz, speed):
    x, y, z = b.pos
    dx, dz = tx - x, tz - z
    h = math.hypot(dx, dz)
    if h > speed: dx, dz = dx / h * speed, dz / h * speed
    dy = max(-VSPEED, min(VSPEED, ty - y))
    b.pos = [x + dx, y + dy, z + dz]
    b.send(0x04, struct.pack(">ddd?", b.pos[0], b.pos[1], b.pos[2], False))
    return h <= speed and abs(ty - y) <= VSPEED


def idle():
    b.send(0x03, b"\x00")


cruise = None        # cruising height for this map, found from the spawn
chest_done = set()
plan = None          # current goal: ("chest", (x,y,z), stage, ticks) or ("roam", (x,z))
chasing = None       # eid
decided = {}         # eid -> when this bot chose whether to chase it
live_since = 0.0
swing = 0


def nearest_player(limit):
    best, bd = None, limit
    for e, q in list(b.players.items()):
        d = math.dist(b.pos, q)
        if d < bd: best, bd = e, d
    return best, bd


def known_chests():
    out = []
    for lst in list(b.chests.values()):
        for c in lst:
            if c not in chest_done and math.hypot(c[0] - CX, c[2] - CZ) <= a.roam:
                out.append(c)
    return out


def punch(e):
    global swing
    swing += 1
    if swing % 3 == 0:            # ~6-7 swings a second
        b.send(0x0A)
        b.send(0x02, vi(e) + vi(1))
        b.stats["attacks"] += 1


def live_tick(fight_all):
    global plan, chasing, decided
    if b.window is not None:
        return loot_tick()
    # fights
    if chasing is not None and chasing not in b.players:
        chasing = None
    if chasing is None:
        e, d = nearest_player(40.0 if fight_all else a.chase)
        if e is not None:
            if fight_all:
                chasing = e
            elif time.time() - live_since > 20 and e not in decided:
                # one decision per opponent met, as a player decides to fight or run
                decided[e] = time.time()
                if rnd.random() < a.chase_chance: chasing = e
            for k in [k for k, t in decided.items() if time.time() - t > 30]: del decided[k]
    if chasing is not None:
        q = b.players[chasing]
        d = math.dist(b.pos, q)
        h = math.hypot(q[0] - b.pos[0], q[2] - b.pos[2])
        if not fight_all and d > a.chase * 2.5:
            chasing = None
        else:
            # from above, as the chest approach: across at cruising height, then straight down
            # onto the target, so the route never runs through the terrain between
            if h > 2.5:
                b.mode = "chase_fly"
                if b.pos[1] < cruise - 0.5 and h > 6: step_toward(b.pos[0], cruise, b.pos[2])
                else: step_toward(q[0], max(b.pos[1], q[1] + 1.0) if h > 6 else b.pos[1], q[2])
            elif d > REACH * 0.8:
                b.mode = "chase_drop"; step_toward(q[0], q[1] + 0.5, q[2])
            else:
                b.mode = "fight"; idle()
            if d <= REACH: punch(chasing)
            return
    if fight_all:
        return idle()
    chest_tick()


def chest_tick():
    global plan
    if plan is None or (plan[0] == "roam" and known_chests()):
        cs = known_chests()
        if cs:
            cs.sort(key=lambda c: math.dist(b.pos, c))
            plan = ["chest", rnd.choice(cs[:3]), "fly", 0]
        else:
            ang = rnd.random() * 2 * math.pi; r = a.roam * math.sqrt(rnd.random())
            plan = ["roam", (CX + r * math.cos(ang), CZ + r * math.sin(ang)), "fly", 0]
    b.stats["chests_known_max"] = max(b.stats["chests_known_max"], len(known_chests()))
    kind, tgt, stage, n = plan
    b.mode = kind + "_" + stage
    plan[3] += 1
    if kind == "roam":
        if step_toward(tgt[0], cruise, tgt[1]) or plan[3] > 1200: plan = None
        return
    x, y, z = tgt
    if stage == "fly":
        h = math.hypot(x + 0.5 - b.pos[0], z + 0.5 - b.pos[2])
        if h > 1.0 and b.pos[1] < cruise - 0.5:
            step_toward(b.pos[0], cruise, b.pos[2])       # climb before going anywhere
        elif step_toward(x + 0.5, max(cruise, b.pos[1]) if h > 1.0 else b.pos[1], z + 0.5):
            plan[2] = "drop"; plan[3] = 0
        if plan[3] > 1200: chest_done.add(tgt); plan = None
        return
    if stage == "drop":
        before = b.corrections
        there = step_toward(x + 0.5, y + 1.0, z + 0.5)
        if there or plan[3] > 60:
            eye = (b.pos[0], b.pos[1] + 1.62, b.pos[2])
            if math.dist(eye, (x + 0.5, y + 0.5, z + 0.5)) < 5.5:
                b.send(0x08, packpos(x, y, z) + b"\x01" + struct.pack(">h", -1) + b"\x08\x0f\x08")
                b.stats["chest_clicks"] += 1
                plan[2] = "wait"; plan[3] = 0
            else:
                b.stats["chest_unreachable"] += 1
                chest_done.add(tgt); plan = None
        return
    if stage == "wait":
        idle()
        if plan[3] > 30: chest_done.add(tgt); plan = None   # did not open (cancelled, or not a chest any more)
        return


loot_state = {"n": 0}


def loot_tick():
    global plan
    loot_state["n"] += 1
    b.mode = "loot"; idle()
    n = loot_state["n"]
    if n < 8: return                       # look at it first
    if n % 3 == 0 and b.window_slots:
        slot = min(b.window_slots)
        raw = b.window_slots.pop(slot)
        b.send(0x0E, struct.pack(">bhbhb", b.window, slot, 0, rnd.randrange(1, 30000), 1) + raw)
        b.stats["items_taken"] += 1
        return
    if not b.window_slots and n > 14 or n > 120:
        b.send(0x0D, struct.pack(">b", b.window))
        b.window = None; loot_state["n"] = 0
        if plan and plan[0] == "chest": chest_done.add(plan[1])
        plan = None


def spread_tick():
    global plan
    b.mode = "spread"
    k, n = [int(v) for v in a.spread.split("/")]
    if plan is None or plan[0] != "roam":
        ang = (k + rnd.random()) / n * 2 * math.pi; r = a.roam * math.sqrt(0.15 + 0.85 * rnd.random())
        plan = ["roam", (CX + r * math.cos(ang), CZ + r * math.sin(ang)), "fly", 0]
    plan[3] += 1
    if b.pos[1] < cruise - 0.5 and math.hypot(plan[1][0] - b.pos[0], plan[1][1] - b.pos[2]) > 6:
        step_toward(b.pos[0], cruise, b.pos[2])
    elif step_toward(plan[1][0], cruise, plan[1][1]) or plan[3] > 1200:
        plan = None


def spectator_tick():
    global plan
    b.mode = "spectator"
    if plan is None or plan[0] != "roam":
        ang = rnd.random() * 2 * math.pi; r = a.roam * math.sqrt(rnd.random())
        plan = ["roam", (CX + r * math.cos(ang), CZ + r * math.sin(ang)), "fly", 0]
    plan[3] += 1
    if step_toward(plan[1][0], cruise, plan[1][1]) or plan[3] > 1200: plan = None


start = time.time()
next_t = time.monotonic()
escape = 0; last_escape = 0.0; cruise_base = 0.0; esc_dir = (1.0, 0.0)
seen_tp = 0
while not b.dead and time.time() - start < a.seconds and b.matches_done < a.matches:
    ph = b.phase
    now = time.time()
    if hop_due is not None and now >= hop_due and ph == "lobby":
        # back to the hub from the SG lobby, and through the other sign
        hop_due = None
        b.set_phase("hub")
        if not switch("/hub", lambda: b.send(0x01, st("/hub"))):
            fail("/hub did not send us to the hub")
        still(1.0)
        if not switch("sign " + a.hop_sign, click_sign(a.hop_sign)):
            fail("the hub sign %s did not send us anywhere" % a.hop_sign)
        b.set_phase("connect")
        next_t = time.monotonic()
        continue
    if escape > 0:
        # stuck against a block: go straight up for a while, then carry on (higher) or give up
        escape -= 1
        b.mode = "escape"
        step_toward(b.pos[0] + esc_dir[0] * 4, b.pos[1] + 0.5, b.pos[2] + esc_dir[1] * 4, SPEED)
        if escape == 0 and sum(1 for t in b.corr_times if now - t < 0.6) >= 2:
            b.stats["gave_up"] += 1
            if plan and plan[0] == "chest": chest_done.add(plan[1])
            plan = None; chasing = None
        next_t += DT
        d = next_t - time.monotonic()
        if d > 0: time.sleep(d)
        else: next_t = time.monotonic()
        continue
    if ph in ("live", "deathmatch") and not b.spectator and sum(1 for t in list(b.corr_times) if now - t < 1.0) >= 3 and now - last_escape > 2.0:
        b.stats["escapes"] += 1; escape = 12; last_escape = now
        ang = rnd.random() * 2 * math.pi; esc_dir = (math.cos(ang), math.sin(ang))
        if cruise is not None and b.mode in ("chest_fly", "roam_fly", "chase_fly"): cruise = min(cruise + 4.0, cruise_base + 12.0)
        if plan and plan[0] == "chest" and plan[2] in ("drop", "wait"):
            chest_done.add(plan[1]); plan = None; b.stats["chest_unreachable"] += 1
        continue
    if b.teleports != seen_tp:
        seen_tp = b.teleports
        plan = None; chasing = None
        if ph in ("starting", "pregame"):
            cruise = cruise_base = b.pos[1] + 40.0          # above the map's buildings and trees
            if AUTO_CENTRE: CX, CZ = b.pos[0], b.pos[2]      # the pedestals ring the map's centre
            chest_done.clear(); decided.clear()
    if ph == "live" and not live_since: live_since = time.time()
    if ph != "live" and ph != "deathmatch": live_since = 0.0
    if ph == "live" and cruise is not None:
        if b.spectator: spectator_tick()
        elif a.spread: spread_tick()
        else: live_tick(False)
    elif ph == "deathmatch" and cruise is not None:
        if b.spectator: idle()
        else: live_tick(True)
    else:
        b.mode = "idle"; idle()
    next_t += DT
    d = next_t - time.monotonic()
    if d > 0: time.sleep(d)
    else: next_t = time.monotonic()

ok = b.err is None
b.dead = True
try: b.s.close()
except Exception: pass
json.dump({"name": a.name, "ok": ok, "err": b.err, "phases": b.phases, "matches": b.matches_done,
           "spectator": b.spectator, "stats": dict(b.stats), "sent": dict(b.sent), "rx": dict(b.rx), "teleports": b.teleports, "tp_log": b.tp_log, "chunk_log": b.chunk_log,
           "chat": list(b.chatlog), "corr_log": b.corr_log,
           "switches": b.switches, "signs": b.signs, "sign_log": b.sign_log, "ticks": b.ticks, "mono_to_unix": time.time() - time.monotonic()}, open(a.out, "w"))
