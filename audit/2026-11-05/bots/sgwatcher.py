"""Athena - a client that watches a SurvivalGames match for what it is sent: each world it is put in
(Join Game / Respawn), and how many chunks arrive in each. For checking that a player teleported into a
deathmatch venue gets its chunks.

    python3 sgwatcher.py PORT NAME SECONDS > timeline.txt
"""
import socket, struct, sys, time, uuid, zlib

PORT_ = int(sys.argv[1]); NAME = sys.argv[2]; SECONDS = float(sys.argv[3])
# ---- a 1.8 client (protocol 47), offline mode
def varint(n):
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n: return out


def string(s):
    b = s.encode(); return varint(len(b)) + b


class Reader:
    def __init__(self, d): self.d, self.i = d, 0
    def u8(self): v = self.d[self.i]; self.i += 1; return v
    def varint(self):
        n = s = 0
        while True:
            b = self.u8(); n |= (b & 0x7F) << s; s += 7
            if not b & 0x80: return n
    def take(self, n): v = self.d[self.i:self.i + n]; self.i += n; return v
    def string(self): return self.take(self.varint()).decode()
    def unpack(self, f): v = struct.unpack_from(f, self.d, self.i); self.i += struct.calcsize(f); return v


class Client:
    def __init__(self, name):
        self.sock = socket.create_connection(("127.0.0.1", PORT_), 10)
        self.sock.settimeout(10)
        self.buf = b""
        self.threshold = -1
        self.send(0x00, varint(47) + string("127.0.0.1") + struct.pack(">H", PORT_) + varint(2))
        self.send(0x00, string(name))

    def send(self, pid, payload=b""):
        body = varint(pid) + payload
        if self.threshold >= 0:
            body = varint(0) + body if len(body) < self.threshold else varint(len(body)) + zlib.compress(body)
        self.sock.sendall(varint(len(body)) + body)

    def _fill(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk: raise EOFError("closed")
            self.buf += chunk

    def read(self):
        n = s = i = 0
        while True:
            self._fill(i + 1)
            b = self.buf[i]; i += 1
            n |= (b & 0x7F) << s; s += 7
            if not b & 0x80: break
        self._fill(i + n)
        body = self.buf[i:i + n]; self.buf = self.buf[i + n:]
        if self.threshold >= 0:
            r = Reader(body); size = r.varint(); body = body[r.i:]
            if size: body = zlib.decompress(body)
        r = Reader(body)
        return r.varint(), r



c = None
for attempt in range(60):
    try:
        c = Client(NAME); break
    except OSError:
        time.sleep(2)
while True:
    pid, r = c.read()
    if pid == 0x03: c.threshold = r.varint()
    elif pid == 0x02: break
    elif pid == 0x00: sys.exit("refused: " + r.string())
c.sock.settimeout(0.05)
COMPASS = len(sys.argv) > 4 and sys.argv[4] == "compass"; clicked = False; last_use = 0
tab = {}; tab_logged = 0; seen_mobs = set(); watch = None; got = {}; t0 = time.time(); worlds = 0; chunks = 0; bad = 0; empty = 0; last_send = 0; pos = None; nxt = time.time() + 5
def log(s): print("%7.1f %s" % (time.time() - t0, s), flush=True)
while time.time() - t0 < SECONDS:
    now = time.time()
    if pos and now - last_send > 0.05:
        c.send(0x04, struct.pack(">ddd?", pos[0], pos[1], pos[2], True)); last_send = now
    if COMPASS and pos and not clicked and now - last_use > 5:
        last_use = now  # right click with what is in hand: the spectator's compass
        c.send(0x08, struct.pack(">qB", -1, 255) + struct.pack(">h", -1) + bytes(3))
    if watch and now - watch[2] > 3:
        k = (watch[0], watch[1]); log("own chunk %s: %s" % (k, ("mask %x" % got[k]) if k in got else "NOT RECEIVED")); watch = None
    if now > nxt:
        log("chunks so far in this world: %d (bad %d, empty %d)" % (chunks, bad, empty)); nxt = now + 10
    try:
        pid, r = c.read()
    except socket.timeout:
        continue
    except EOFError:
        log("disconnected"); break
    if pid == 0x00: c.send(0x00, varint(r.varint()))
    elif pid == 0x38:   # the tab list: names the client has now
        action = r.varint()
        for _ in range(r.varint()):
            who = r.take(16)
            if action == 0:
                name = r.string()
                for _p in range(r.varint()):
                    r.string(); r.string()
                    if r.u8(): r.string()
                r.varint(); r.varint()
                if r.u8(): r.string()
                tab[who] = name
            elif action == 4:
                tab.pop(who, None)
            else:
                break
        if action in (0, 4) and now - tab_logged > 2:
            tab_logged = now; log("tab list: %d names" % len(tab))
    elif pid in (0x01, 0x07):
        if pid == 0x01: r.unpack(">i"); r.u8(); dim = r.unpack(">b")[0]
        else: dim = r.unpack(">i")[0]
        log("%s dim=%d (chunks in the world before: %d)" % ("JOIN" if pid == 0x01 else "RESPAWN", dim, chunks)); worlds += 1; chunks = 0; empty = 0; got.clear()
    elif pid == 0x21:
        cx, cz = r.unpack(">ii"); full = r.u8(); mask = r.unpack(">H")[0]; size = r.varint()
        n = bin(mask).count("1")
        expect = n * (8192 + 2048 + 2048) + (256 if full else 0)
        expect_nosky = n * (8192 + 2048) + (256 if full else 0)
        if size not in (expect, expect_nosky) or len(r.d) - r.i != size:
            bad += 1
            if bad <= 5: log("BAD chunk %d,%d mask=%x full=%d size=%d expect=%d left=%d" % (cx, cz, mask, full, size, expect, len(r.d) - r.i))
        if mask == 0: empty += 1
        got[(cx, cz)] = mask
        chunks += 1
    elif pid == 0x26:
        sky = r.u8(); count = r.varint()
        metas = [r.unpack(">iiH") for _ in range(count)]
        total = sum(bin(m[2]).count("1") * (8192 + 2048 + (2048 if sky else 0)) + 256 for m in metas)
        if len(r.d) - r.i != total:
            bad += 1
            if bad <= 5: log("BAD bulk count=%d data=%d expect=%d" % (count, len(r.d) - r.i, total))
        empty += sum(1 for m in metas if m[2] == 0)
        for m in metas: got[(m[0], m[1])] = m[2]
        chunks += count
    elif pid == 0x08:
        x, y, z = r.unpack(">ddd"); yaw, pitch = r.unpack(">ff"); flags = r.u8()
        pos = (x, y, z)
        c.send(0x06, struct.pack(">dddff?", x, y, z, yaw, pitch, True))
        log("POSITION %.1f %.1f %.1f flags=%d" % (x, y, z, flags)); watch = (int(x) >> 4, int(z) >> 4, now)
    elif pid == 0x02:
        raw = r.string()
        try:
            import json
            d = json.loads(raw)
            def flat(c): return c if isinstance(c, str) else c.get("text", "") + "".join(flat(x) for x in c.get("extra", []))
            txt = flat(d)
        except ValueError:
            txt = raw
        if any(k in txt for k in ("eathmatch", "fight", "Round", "won", "Teleport", "until the", "Final", "bye", "Only [")): log("CHAT " + txt[:160])
    elif pid == 0x0F:  # spawn mob: the boss bar's boss
        eid = r.varint(); kind = r.u8(); mx, my, mz = r.unpack(">iii")
        far = ((mx / 32.0 - pos[0]) ** 2 + (mz / 32.0 - pos[2]) ** 2) ** 0.5 if pos else -1
        log("SPAWN MOB type=%d %.0f blocks away" % (kind, far))
    elif pid == 0x2D:  # open window: the spectator's menu
        wid = r.u8(); kind = r.string(); title = r.string(); slots = r.u8()
        log("WINDOW %s %s slots=%d" % (kind, title, slots))
        if not clicked:
            clicked = True
            c.send(0x0E, struct.pack(">BhbhB", wid, 0, 0, 1, 0) + struct.pack(">h", -1))  # click the first slot
    elif pid == 0x40:
        log("KICK " + r.string()); break
log("end: chunks in the last world %d" % chunks)
log("tab list at the end: %d names: %s" % (len(tab), sorted(tab.values())[:30]))
