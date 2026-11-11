"""The network side of one sim_vps.py run: the event loop's CPU against what went over the wire.

The bots record every packet they receive (count and bytes, by phase and id) and every packet they
send; the box's sampler records each server thread's CPU time (1/100 s) every 3 s. A run admits a
different number of bots each time (a room turns them away once it is full), so the event loop's CPU
is divided by the packets it carried.

    python3 netstats.py run/vps-out/TAG [...]
"""
import glob, json, os, sys


def thread_cpu(samples, prefix):
    """CPU seconds of the threads named prefix* over the run (last sample minus first)."""
    first, last, cur = None, None, {}
    for line in open(samples):
        if line.startswith("S|"):
            if cur:
                first = first or dict(cur)
                last = dict(cur)
            cur = {}
        elif line.startswith("T|"):
            _, name, ticks = line.rstrip("\n").split("|")
            if name.startswith(prefix):
                cur[name] = cur.get(name, 0) + int(ticks)
    if cur:
        last = dict(cur)
    return (sum(last.values()) - sum(first.values())) / 100.0


for out in sys.argv[1:]:
    rx_n = rx_b = tx_n = 0
    bots = 0
    for f in glob.glob(os.path.join(out, "bot*.json")):
        r = json.load(open(f))
        if not r.get("ok"):
            continue
        bots += 1
        for k, v in r.get("rx", {}).items():
            if k.endswith(" b"):
                rx_b += v
            else:
                rx_n += v
        tx_n += sum(r.get("sent", {}).values())
    samples = os.path.join(out, "samples.txt")
    netty = thread_cpu(samples, "Netty")
    print("%-14s bots %3d | to clients %6.2f M packets %6.1f MB, from clients %5.2f M | Netty %6.1f s = %5.2f us/packet, %6.4f s/MB"
          "" % (
              os.path.basename(out), bots, rx_n / 1e6, rx_b / 1e6, tx_n / 1e6, netty,
              1e6 * netty / (rx_n + tx_n), netty / (rx_b / 1e6)))
