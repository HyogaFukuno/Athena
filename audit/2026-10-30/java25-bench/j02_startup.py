"""J02/J06: what a start prints and holds, in a Linux container of the colima "athena" VM.

Each variant patches its paperclip.jar once (as start-small.sh does) and then starts the patched jar
the way start-small.sh runs it (-cp, the 144M heap and its flags), or with java -jar paperclip.jar.
Read 20 s after "Done": the WARNING lines on the console, the JVM's thread count and VmRSS, and the
"Done (x s)" time. Then stop with SIGTERM.

    python3 j02_startup.py --work ~/athena-bench-work --runs 2 --out results/j02.json \
        base=~/athena-bench-work/base/paperclip.jar:old new=~/athena-bench-work/j02/paperclip.jar:new
"""
import argparse, json, os, re, shutil, subprocess, sys, time

B = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(B, "..", "..", ".."))
FLAGS_OLD = ["-Xms144M", "-Xmx144M", "-XX:+UseParallelGC", "-XX:+UseCompactObjectHeaders",
             "-XX:TrimNativeHeapInterval=5000", "-XX:ReservedCodeCacheSize=48m", "-XX:SweeperThreshold=50",
             "-Dio.netty.allocator.maxOrder=7", "-XX:+UnlockDiagnosticVMOptions", "-XX:ArchiveRelocationMode=0",
             "-XX:+HeapDumpOnOutOfMemoryError", "-XX:HeapDumpPath=oom.hprof"]
FLAGS_NEW = FLAGS_OLD + ["--enable-native-access=ALL-UNNAMED", "--sun-misc-unsafe-memory-access=allow"]

p = argparse.ArgumentParser()
p.add_argument("--work", required=True)
p.add_argument("--runs", type=int, default=2)
p.add_argument("--out", required=True)
p.add_argument("--image", default="eclipse-temurin:25-jdk")
p.add_argument("variants", nargs="+")   # label=jar:old|new  (the start-small.sh flags to use)
a = p.parse_args()


def prepare(run, jar):
    shutil.rmtree(run, ignore_errors=True)
    os.makedirs(os.path.join(run, "cache"))
    shutil.copy(os.path.join(ROOT, "cache", "mojang_1.8.8.jar"), os.path.join(run, "cache"))
    open(os.path.join(run, "eula.txt"), "w").write("eula=true\n")
    open(os.path.join(run, "server.properties"), "w").write(
        "online-mode=false\nlevel-type=FLAT\nview-distance=2\nspawn-protection=0\nspawn-animals=false\n"
        "spawn-monsters=false\nallow-nether=false\nserver-port=25700\nsnooper-enabled=false\n")
    shutil.copy(jar, os.path.join(run, "paperclip.jar"))


def docker(name, run, args):
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "-v", run + ":/srv", "-w", "/srv", a.image] + args,
                   check=True, capture_output=True)


def logs(name):
    r = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return r.stdout + r.stderr


def measure(label, jar, flags, mode, n):
    name = "athena-j02"
    run = os.path.join(a.work, "j02-%s-%s-%d" % (label, mode, n))
    prepare(run, jar)
    if mode == "cp":
        # patch once, outside the measurement, as start-small.sh does under its lock
        subprocess.run(["docker", "run", "--rm", "-v", run + ":/srv", "-w", "/srv", a.image, "java", "-Xmx512M",
                        "-Dpaperclip.patchonly=true", "-jar", "paperclip.jar"], check=True, capture_output=True)
        args = ["java"] + flags + ["-cp", "cache/patched_1.8.8.jar", "org.bukkit.craftbukkit.Main", "nogui"]
    else:
        args = ["java"] + flags[:2] + ["-jar", "paperclip.jar", "nogui"]
    docker(name, run, args)
    deadline = time.time() + 300
    while "Done (" not in logs(name):
        if time.time() > deadline:
            sys.exit("no start:\n" + logs(name)[-3000:])
        time.sleep(1)
    time.sleep(20)
    status = subprocess.run(["docker", "exec", name, "sh", "-c", "grep -E 'VmRSS|Threads' /proc/1/status"],
                            capture_output=True, text=True).stdout
    console = logs(name)
    subprocess.run(["docker", "stop", "-t", "60", name], capture_output=True)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    done = re.search(r"Done \(([0-9.]+)s\)", console)
    return {"label": label, "mode": mode, "run": n,
            "warning_lines": sum(1 for l in console.splitlines() if l.startswith("WARNING:")),
            "threads": int(re.search(r"Threads:\s+(\d+)", status).group(1)),
            "rss_mb": round(int(re.search(r"VmRSS:\s+(\d+)", status).group(1)) / 1024, 1),
            "done_s": float(done.group(1)),
            "warnings": sorted(set(l for l in console.splitlines() if l.startswith("WARNING:")))}


rows = []
for n in range(1, a.runs + 1):
    order = a.variants if n % 2 else list(reversed(a.variants))
    for v in order:
        label, rest = v.split("=", 1)
        jar, which = rest.rsplit(":", 1)
        for mode in ("cp", "jar"):
            r = measure(label, os.path.expanduser(jar), FLAGS_NEW if which == "new" else FLAGS_OLD, mode, n)
            print(json.dumps({k: r[k] for k in r if k != "warnings"}), flush=True)
            rows.append(r)
json.dump(rows, open(a.out, "w"), indent=2)
