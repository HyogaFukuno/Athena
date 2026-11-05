"""Athena - from a server.log with SGBOTS_DEBUG=true: which bots stayed in one place for 15 seconds or more
in the live game, doing what. Also where bots stood together.

    python3 stuck.py run/sgbots-out/server.log
"""
import collections, re, sys
rows = collections.defaultdict(list)
for line in open(sys.argv[1], errors="replace"):
    m = re.search(r"\[(\d+:\d+:\d+) INFO\]: \[SgBot\] (\S+) state=(\S+) playing=(\S+) mode=(\S+).*world=(\S+) at=([-\d.]+),([-\d.]+),([-\d.]+) goal=(\S+)", line)
    if m and m[3] == "LIVE_GAME" and m[4] == "true":
        rows[m[2]].append((m[1], m[5], float(m[7]), float(m[8]), float(m[9]), m[10]))
total = stuck = 0
spots = collections.Counter(); modes = collections.Counter()
for name, r in rows.items():
    total += len(r)
    for i in range(3, len(r)):
        a, b = r[i - 3], r[i]
        if abs(a[2] - b[2]) + abs(a[4] - b[4]) < 2.5:
            stuck += 1
            modes[b[1]] += 1
            spots[(round(b[2] / 4) * 4, round(b[3]), round(b[4] / 4) * 4, b[1])] += 1
print("samples", total, "in one place for 15 s:", stuck, "(%.0f%%)" % (100.0 * stuck / max(total, 1)))
print("by mode", modes.most_common())
print("spots", spots.most_common(12))
