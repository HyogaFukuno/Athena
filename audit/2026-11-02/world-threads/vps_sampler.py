"""simbench's sampler, run on the box: vps_sampler.py PID OUT. Every 3 s it appends one sample to OUT:
"S|<unix time>", a "T|<thread>|<utime+stime ticks>" line per thread of PID, "P|<ticks>" for the whole of PID, and a "B|<json>" line with
the box's CPU ticks (busy, steal), MemAvailable, swap in use, PID's RssAnon and VmRSS, and sshd's CPU
ticks. Stops when PID exits."""
import glob, json, os, sys, time

pid, out = sys.argv[1], sys.argv[2]


def ticks(stat):
    f = stat[stat.rindex(")") + 2:].split()
    return int(f[11]) + int(f[12])


with open(out, "a") as o:
    while os.path.exists("/proc/%s" % pid):
        lines = ["S|%.3f" % time.time()]
        for t in glob.glob("/proc/%s/task/*" % pid):
            try:
                lines.append("T|%s|%d" % (open(t + "/comm").read().strip(), ticks(open(t + "/stat").read())))
            except OSError:
                pass
        try:
            # the whole process, threads that have ended included (a match's world thread ends with it)
            lines.append("P|%d" % ticks(open("/proc/%s/stat" % pid).read()))
        except OSError:
            pass
        cpu = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        mem = dict((l.split(":")[0], int(l.split()[1])) for l in open("/proc/meminfo"))
        st = dict((l.split(":")[0], l.split()[1]) for l in open("/proc/%s/status" % pid) if l.startswith(("RssAnon", "VmRSS")))
        sshd = 0
        for d in glob.glob("/proc/[0-9]*"):
            try:
                if open(d + "/comm").read().strip() == "sshd": sshd += ticks(open(d + "/stat").read())
            except OSError:
                pass
        box = {"busy": sum(cpu[:3]) + sum(cpu[5:8]), "steal": cpu[7], "idle": cpu[3] + cpu[4],
               "avail_kb": mem["MemAvailable"], "swap_used_kb": mem["SwapTotal"] - mem["SwapFree"],
               "anon_kb": int(st.get("RssAnon", 0)), "rss_kb": int(st.get("VmRSS", 0)), "sshd": sshd}
        lines.append("B|" + json.dumps(box))
        o.write("\n".join(lines) + "\n"); o.flush()
        time.sleep(3)
