"""One map bench on this Mac: a server from the given paperclip.jar loads a match map the way
SurvivalGames does (MapProbe), 20 flybot2 bots join and fly over it, MapProbe measures and writes
a JSON line. Same configs as the live servers (server/*.yml, networkCompressionLevel 1), 144M heap,
start-small.sh's flags.

    python3 mapbench.py --jar base/paperclip.jar --label base --map ../../../server/maps/map_bayside \
        --center 1.5,61,1.5 --out results/map-bayside-base-1.json
"""
import argparse, json, os, re, shutil, subprocess, sys, threading, time

B = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(B, "..", "..", ".."))
JAVA = os.path.join(os.environ["JAVA_HOME"], "bin", "java")  # a Java 25
FLAGS = ["-XX:+UseParallelGC", "-XX:+UseCompactObjectHeaders", "-XX:TrimNativeHeapInterval=5000",
         "-XX:ReservedCodeCacheSize=48m", "-XX:SweeperThreshold=50", "-Dio.netty.allocator.maxOrder=7",
         "--enable-native-access=ALL-UNNAMED", "--sun-misc-unsafe-memory-access=allow"]

p = argparse.ArgumentParser()
p.add_argument("--jar", required=True)
p.add_argument("--label", required=True)
p.add_argument("--map", required=True)
p.add_argument("--center", required=True)
p.add_argument("--bots", type=int, default=20)
p.add_argument("--warmup", type=int, default=20)
p.add_argument("--window", type=int, default=60)
p.add_argument("--heap", default="144M")
p.add_argument("--port", type=int, default=25710)
p.add_argument("--work", default=os.path.expanduser("~/athena-bench-work"))
p.add_argument("--jvm", action="append", default=[])
p.add_argument("--threshold", type=int, default=256)   # network-compression-threshold; -1 sends uncompressed
p.add_argument("--netty-threads", type=int, default=None)  # spigot.yml settings.netty-threads (the live servers have 1)
p.add_argument("--out", required=True)
a = p.parse_args()

run = os.path.join(a.work, "map-" + a.label)
shutil.rmtree(run, ignore_errors=True)
os.makedirs(os.path.join(run, "plugins"))
os.makedirs(os.path.join(run, "cache"))
shutil.copy(os.path.join(ROOT, "cache", "mojang_1.8.8.jar"), os.path.join(run, "cache"))
shutil.copy(os.path.join(B, "mapprobe", "MapProbe.jar"), os.path.join(run, "plugins"))
for f in ("bukkit.yml", "spigot.yml", "paper.yml", "pandaspigot.yml", "athena.yml"):
    shutil.copy(os.path.join(ROOT, "server", f), run)
athena = open(os.path.join(run, "athena.yml")).read()
athena = re.sub(r"(?m)^networkCompressionLevel: .*$", "networkCompressionLevel: 1", athena)
open(os.path.join(run, "athena.yml"), "w").write(athena)
if a.netty_threads is not None:
    spigot = open(os.path.join(run, "spigot.yml")).read()
    spigot = re.sub(r"(?m)^(\s*netty-threads:) .*$", r"\g<1> %d" % a.netty_threads, spigot)
    open(os.path.join(run, "spigot.yml"), "w").write(spigot)
open(os.path.join(run, "eula.txt"), "w").write("eula=true\n")
open(os.path.join(run, "server.properties"), "w").write(
    "online-mode=false\nallow-flight=true\nlevel-type=FLAT\nview-distance=8\nspawn-protection=0\n"
    "spawn-animals=false\nspawn-monsters=false\nallow-nether=false\nserver-port=%d\n"
    "network-compression-threshold=%d\nsnooper-enabled=false\nmax-players=40\n" % (a.port, a.threshold))

out = os.path.abspath(a.out)
props = ["-Dmapprobe.map=" + os.path.abspath(a.map), "-Dmapprobe.center=" + a.center, "-Dmapprobe.bots=%d" % a.bots,
         "-Dmapprobe.warmup=%d" % a.warmup, "-Dmapprobe.window=%d" % a.window, "-Dmapprobe.out=" + out]
# patch first with room, as start-small.sh does, then run the patched jar off the class path as it does
subprocess.run([JAVA, "-Xmx512M", "-Dpaperclip.patchonly=true", "-jar", os.path.abspath(a.jar)], cwd=run, check=True,
               capture_output=True)
cmd = [JAVA, "-Xms" + a.heap, "-Xmx" + a.heap] + FLAGS + a.jvm + props + ["-cp", "cache/patched_1.8.8.jar",
                                                                          "org.bukkit.craftbukkit.Main", "nogui"]
log = open(os.path.join(run, "console.out"), "w")
server = subprocess.Popen(cmd, cwd=run, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
bots = []
rss = []   # (seconds since start, RSS kB), once a second


def sample():
    t0 = time.time()
    while server.poll() is None:
        r = subprocess.run(["ps", "-o", "rss=", "-p", str(server.pid)], capture_output=True, text=True).stdout.strip()
        if r:
            rss.append((time.time() - t0, int(r)))
        time.sleep(1)


threading.Thread(target=sample, daemon=True).start()
try:
    deadline = time.time() + 600
    while "MAPPROBE READY" not in open(os.path.join(run, "console.out")).read():
        if server.poll() is not None or time.time() > deadline:
            sys.exit("server did not get ready:\n" + open(os.path.join(run, "console.out")).read()[-4000:])
        time.sleep(1)
    cx, _, cz = a.center.split(",")
    for i in range(a.bots):
        bots.append(subprocess.Popen([sys.executable, os.path.join(ROOT, "bench", "flybot2.py"), "--port", str(a.port),
                                      "--centre", "%s,%s" % (cx, cz), "--radius", "16", "--phase", str(i / a.bots),
                                      "--await-teleport", "20", "--seconds", str(a.warmup + a.window + 30),
                                      "--name", "Bot%02d" % i, "--out", os.path.join(run, "bot%02d.json" % i)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(0.3)
    server.wait(timeout=a.warmup + a.window + 300)  # MapProbe stops the server when it is done
finally:
    for b in bots:
        b.kill()
    if server.poll() is None:
        server.kill()
res = json.loads(open(out).read())
res["rss_max_mb"] = round(max(r for _, r in rss) / 1024.0, 1) if rss else None
res["rss_end_mb"] = round(rss[-1][1] / 1024.0, 1) if rss else None
res["netty_threads"] = a.netty_threads
open(out, "w").write(json.dumps(res) + "\n")
print(json.dumps(res))
