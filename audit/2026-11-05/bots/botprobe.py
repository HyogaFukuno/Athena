"""Athena - runs the BotProbe plugin (./botprobe) on a bare flat server and checks bots from a client's side.

    python3 botprobe.py JAR

1. Starts the server (run/botprobe/, max-players 2) with only BotProbe, which runs its checks and logs
   "BOTPROBE PASS/FAIL ...", then "BOTPROBE DONE", and leaves three bots online (Bot_Show walking
   round the spawn, two more so the server is past max-players).
2. A client logs in (offline mode) while the server holds three bots against max-players 2: bots must not
   take its place. For 8 seconds it records what it is sent: the tab list entries, the bots spawned for it
   as players, and their moves.
3. Stops the server and checks that no player file, statistics or user cache entry was written for a bot.

Writes run/botprobe-out/summary.json and server.log.
"""
import json, os, re, shutil, socket, struct, subprocess, sys, threading, time, uuid, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RUN = os.path.join(ROOT, "run", "botprobe")
OUT = os.path.join(ROOT, "run", "botprobe-out")
PORT = 25691
BOTS = ["Bot_Login", "Bot_Walk", "Bot_Jump", "Bot_Fall", "Bot_A", "Bot_B", "Bot_Eat", "Bot_Bow", "Bot_Chest", "Bot_Die",
        "Bot_Throw", "Bot_Thread", "Bot_Direct", "Bot_Remove", "Bot_Kick", "Bot_Show", "Bot_Fill1", "Bot_Fill2"]

jar = os.path.abspath(sys.argv[1])
shutil.rmtree(RUN, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(RUN + "/plugins"); os.makedirs(OUT)
open(RUN + "/eula.txt", "w").write("eula=true\n")
open(RUN + "/server.properties", "w").write("\n".join([
    "server-port=%d" % PORT, "server-ip=127.0.0.1", "online-mode=false", "level-type=FLAT", "level-name=world",
    "max-players=2", "spawn-protection=0", "difficulty=2", "allow-nether=false", "view-distance=4",
    "generate-structures=false", "spawn-monsters=false", "spawn-animals=false", "spawn-npcs=false", "gamemode=0", ""]))
subprocess.run([os.path.join(HERE, "botprobe", "build.sh"), RUN + "/plugins/botprobe.jar"], check=True)

log = open(OUT + "/server.log", "w")
server = subprocess.Popen(["java", "-Xms512M", "-Xmx512M", "-jar", jar, "nogui"], cwd=RUN,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
lines = []
done = threading.Event()


def pump():
    for line in server.stdout:
        log.write(line); log.flush()
        lines.append(line)
        if "BOTPROBE DONE" in line:
            done.set()


threading.Thread(target=pump, daemon=True).start()
if not done.wait(300):
    server.kill()
    sys.exit("no BOTPROBE DONE in 5 minutes; see %s/server.log" % OUT)
time.sleep(2)


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
        self.sock = socket.create_connection(("127.0.0.1", PORT), 10)
        self.sock.settimeout(10)
        self.buf = b""
        self.threshold = -1
        self.send(0x00, varint(47) + string("127.0.0.1") + struct.pack(">H", PORT) + varint(2))
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


seen = {"login": None, "tab": {}, "spawned": {}, "moves": {}, "kicked": None, "errors": []}
try:
    c = Client("Watcher")
    while True:  # login
        pid, r = c.read()
        if pid == 0x03: c.threshold = r.varint()
        elif pid == 0x02: seen["login"] = "success"; break
        elif pid == 0x00: seen["login"] = "refused: " + r.string(); break
    if seen["login"] == "success":
        c.sock.settimeout(1)
        end = time.time() + 8
        ids = {}
        while time.time() < end:
            try:
                pid, r = c.read()
            except socket.timeout:
                continue
            if pid == 0x00:  # keep alive
                c.send(0x00, varint(r.varint()))
            elif pid == 0x38:  # player list item
                action, count = r.varint(), r.varint()
                for _ in range(count):
                    u = uuid.UUID(bytes=bytes(r.take(16)))
                    if action != 0: break
                    name = r.string()
                    for _ in range(r.varint()):
                        r.string(); r.string()
                        if r.u8(): r.string()
                    r.varint(); r.varint()
                    if r.u8(): r.string()
                    seen["tab"][name] = str(u)
            elif pid == 0x0C:  # spawn player
                eid = r.varint(); u = str(uuid.UUID(bytes=bytes(r.take(16))))
                x, y, z = r.unpack(">iii")
                ids[eid] = u
                seen["spawned"][u] = [x / 32.0, y / 32.0, z / 32.0]
            elif pid in (0x15, 0x17, 0x18):  # entity moves
                eid = r.varint()
                if eid in ids: seen["moves"][ids[eid]] = seen["moves"].get(ids[eid], 0) + 1
            elif pid == 0x40:
                seen["kicked"] = r.string(); break
            elif pid == 0x08:  # position: confirm it as a client would
                x, y, z = r.unpack(">ddd"); yaw, pitch = r.unpack(">ff")
                c.send(0x06, struct.pack(">dddff?", x, y, z, yaw, pitch, True))
        c.sock.close()
except Exception as e:
    seen["errors"].append(repr(e))

server.stdin.write("stop\n"); server.stdin.flush()
try:
    server.wait(60)
except subprocess.TimeoutExpired:
    server.kill()
time.sleep(1)

text = "".join(lines)
probe = re.findall(r"BOTPROBE (PASS|FAIL) ([^:]+): (.*)", text)
show = str(uuid.uuid3(uuid.NAMESPACE_DNS, "x"))  # placeholder, replaced below
# the bots' ids are version 3 UUIDs of "AthenaBot:<name>" (Java's nameUUIDFromBytes)
import hashlib
def java_name_uuid(s):
    h = bytearray(hashlib.md5(s.encode()).digest()); h[6] = (h[6] & 0x0F) | 0x30; h[8] = (h[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(h)))
ids = {n: java_name_uuid("AthenaBot:" + n) for n in BOTS}

client_checks = [
    ("client logs in past max-players while bots are online", seen["login"] == "success", seen["login"]),
    ("bots in the client's tab list", all(n in seen["tab"] for n in ("Bot_Show", "Bot_Fill1", "Bot_Fill2")), seen["tab"]),
    ("Bot_Show spawned for the client as a player", ids["Bot_Show"] in seen["spawned"], seen["spawned"].get(ids["Bot_Show"])),
    ("Bot_Show's moves reach the client", seen["moves"].get(ids["Bot_Show"], 0) > 20, seen["moves"].get(ids["Bot_Show"], 0)),
    ("client not kicked", seen["kicked"] is None and not seen["errors"], [seen["kicked"], seen["errors"]]),
]
files = []
for d in ("world/playerdata", "world/stats"):
    p = os.path.join(RUN, d)
    if os.path.isdir(p):
        files += [os.path.join(d, f) for f in os.listdir(p)]
bot_files = [f for f in files if any(ids[n] in f for n in BOTS)]
cache = open(os.path.join(RUN, "usercache.json")).read() if os.path.exists(os.path.join(RUN, "usercache.json")) else ""
file_checks = [
    ("no player file or statistics for a bot", not bot_files, bot_files),
    ("the client's own player file was written", any("playerdata" in f for f in files), files),
    ("no bot in usercache.json", not any(n in cache for n in BOTS), cache[:300]),
]
errors = [l.strip() for l in lines if re.search(r"ERROR|Exception", l) and "expected" not in l and "ExceptionInInitializerError" not in l]

summary = {"probe": [{"ok": ok == "PASS", "name": n, "detail": d} for ok, n, d in probe],
           "client": [{"ok": bool(ok), "name": n, "detail": d} for n, ok, d in client_checks],
           "files": [{"ok": bool(ok), "name": n, "detail": d} for n, ok, d in file_checks],
           "server_errors": errors[:40]}
json.dump(summary, open(OUT + "/summary.json", "w"), indent=1, default=str)
bad = 0
for group in ("probe", "client", "files"):
    for c in summary[group]:
        bad += not c["ok"]
        print("%s %-55s %s" % ("PASS" if c["ok"] else "FAIL", c["name"], str(c["detail"])[:150]))
print("server errors:", len(errors))
for e in errors[:20]: print("  ", e[:200])
print("ALL PASS" if not bad else "%d FAILED" % bad)
