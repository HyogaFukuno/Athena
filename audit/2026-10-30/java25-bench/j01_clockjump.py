"""J01: step the wall clock of a Linux VM back and forward under a running server, and read the tick
starts TickProbe recorded (System.nanoTime, so the jumps do not touch the measurement).

The server runs in a privileged container of the colima "athena" VM; `date -s` there sets the VM's
clock, which is the clock the JVM reads. Each run: start, wait for "Done", 20 s, clock -2 s, 20 s,
clock +3 s, 20 s, stop (SIGTERM: TickProbe writes its CSV on disable).

    python3 j01_clockjump.py --jar paperclip.jar --label before --out results/j01-before.json
"""
import argparse, json, os, shutil, subprocess, sys, time

B = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(B, "..", "..", ".."))
p = argparse.ArgumentParser()
p.add_argument("--jar", required=True)
p.add_argument("--label", required=True)
p.add_argument("--work", required=True)
p.add_argument("--out", required=True)
p.add_argument("--image", default="eclipse-temurin:25-jdk")
a = p.parse_args()

run = os.path.join(a.work, "j01-" + a.label)
shutil.rmtree(run, ignore_errors=True)
os.makedirs(os.path.join(run, "plugins"))
os.makedirs(os.path.join(run, "cache"))
shutil.copy(os.path.join(ROOT, "cache", "mojang_1.8.8.jar"), os.path.join(run, "cache"))
shutil.copy(os.path.join(B, "tickprobe", "TickProbe.jar"), os.path.join(run, "plugins"))
open(os.path.join(run, "eula.txt"), "w").write("eula=true\n")
open(os.path.join(run, "server.properties"), "w").write(
    "online-mode=false\nlevel-type=FLAT\nview-distance=2\nspawn-protection=0\nspawn-animals=false\n"
    "spawn-monsters=false\nallow-nether=false\nserver-port=25700\nsnooper-enabled=false\n")
name = "athena-j01-" + a.label
subprocess.run(["docker", "rm", "-f", name], capture_output=True)
subprocess.run(["docker", "run", "-d", "--privileged", "--name", name, "-v", run + ":/srv", "-w", "/srv",
                "-v", os.path.abspath(a.jar) + ":/jar/paperclip.jar:ro", a.image,
                "java", "-Xms512M", "-Xmx512M", "-XX:+UseParallelGC", "-jar", "/jar/paperclip.jar", "nogui"],
               check=True, capture_output=True)


def logs():
    return subprocess.run(["docker", "logs", name], capture_output=True, text=True).stdout


deadline = time.time() + 300
while "Done (" not in logs():
    if time.time() > deadline:
        sys.exit("server did not start:\n" + logs()[-3000:])
    time.sleep(1)


def shift(seconds):
    # one exec reads the clock and sets it, so the shift is exact to the exec's own runtime
    r = subprocess.run(["docker", "exec", name, "sh", "-c",
                        'before=$(date +%%s.%%N); date -s "@$(echo "$before %+d" | awk \'{printf "%%.6f", $1 + $2}\')" > /dev/null; '
                        'echo $before $(date +%%s.%%N)' % seconds], capture_output=True, text=True, check=True)
    print("clock %+ds: %s" % (seconds, r.stdout.strip()), flush=True)
    return time.monotonic()


time.sleep(20)
t_back = shift(-2)
time.sleep(20)
t_fwd = shift(+3)
time.sleep(20)
subprocess.run(["docker", "stop", "-t", "60", name], check=True, capture_output=True)
console = logs()
subprocess.run(["docker", "rm", "-f", name], capture_output=True)

rows = [l.split(",") for l in open(os.path.join(run, "tickprobe.csv")).read().split()[1:]]
nanos = [int(r[1]) for r in rows]
millis = [int(r[2]) for r in rows]
gaps = [(nanos[i + 1] - nanos[i]) / 1e6 for i in range(len(nanos) - 1)]


def jump_index(sign):
    # the tick where the wall clock moved against the monotonic one by more than 1 s
    for i in range(len(rows) - 1):
        drift = (millis[i + 1] - millis[i]) - (nanos[i + 1] - nanos[i]) / 1e6
        if sign * drift > 1000:
            return i
    return None


def window(i, seconds=6.0):
    """Ticks from the jump on: the longest gap, how many came back to back (< 10 ms), how many ran."""
    if i is None:
        return None
    end = nanos[i] + seconds * 1e9
    w = [g for k, g in enumerate(gaps[i:], i) if nanos[k] < end]
    return {"max_gap_ms": round(max(w), 1), "gaps_under_10ms": sum(1 for g in w if g < 10),
            "ticks_in_6s": len(w), "expected_ticks_in_6s": int(seconds * 20)}


back = jump_index(-1)
steady = sorted(gaps[100:back - 1])  # the steady ticks before the first jump
res = {"label": a.label, "ticks": len(rows),
       "steady_gap_ms": {"p50": round(steady[len(steady) // 2], 3), "p99": round(steady[int(len(steady) * .99)], 3),
                         "min": round(steady[0], 3), "max": round(steady[-1], 3)},
       "clock_back_2s": window(back), "clock_forward_3s": window(jump_index(+1)),
       "warnings": [l for l in console.splitlines() if "Can't keep up" in l]}
json.dump(res, open(a.out, "w"), indent=2)
print(json.dumps(res, indent=2))
