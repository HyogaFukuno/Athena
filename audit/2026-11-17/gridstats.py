"""SG のボットの経路探索の計器（-Dsgbots.gridstats=true の [GridStats] の行）を、サーバー全体の 1 秒ごとにまとめる。

    python3 gridstats.py <console.out> <予算 ns/50 ms>

予算は Navigator.PLAN_NANOS_PER_TICK と同じく、サーバー全体の 50 ms あたり。1 秒の予算はその 20 倍。
max_window_plan_ms はそのワールドの 1 つの 50 ms で探した最長で、全ワールドを足すと（同じ 50 ms とは限らないので）上限の見積もり。
"""
import collections
import re
import sys

LINE = re.compile(r"\[GridStats\] (\d+) (\S+) .*?plans=(\d+) nodes=(\d+) plan_ms=([\d.]+) max_plan_ms=([\d.]+) max_window_plan_ms=([\d.]+)")
budget_ms = int(sys.argv[2]) / 1e6
per_sec = collections.defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0])
for line in open(sys.argv[1], errors="replace"):
    m = LINE.search(line)
    if not m:
        continue
    sec = int(m.group(1)) // 1000
    row = per_sec[sec]
    row[0] += int(m.group(3))
    row[1] += int(m.group(4))
    row[2] += float(m.group(5))
    row[3] = max(row[3], float(m.group(6)))
    row[4] += float(m.group(7))
secs = sorted(per_sec)
busy = [per_sec[s] for s in secs if per_sec[s][0] > 0]
if not busy:
    sys.exit("no [GridStats] lines")
plan_ms = sorted(r[2] for r in busy)
q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]
print("seconds with plans %d, plans %d, nodes %d, plan ms %.0f (%.2f ms per plan, %.1f us per node)" % (
    len(busy), sum(r[0] for r in busy), sum(r[1] for r in busy), sum(plan_ms),
    sum(plan_ms) / max(1, sum(r[0] for r in busy)), 1000 * sum(plan_ms) / max(1, sum(r[1] for r in busy))))
print("plan ms per second: median %.1f, p90 %.1f, p99 %.1f, max %.1f (budget %.0f per second)" % (
    q(plan_ms, .5), q(plan_ms, .9), q(plan_ms, .99), plan_ms[-1], budget_ms * 20))
print("seconds at >= 80%% of the budget: %d" % sum(1 for x in plan_ms if x >= 0.8 * budget_ms * 20))
print("longest single plan %.1f ms" % max(r[3] for r in busy))
