#!/usr/bin/env python3
"""Athena diagnostics kit - report. Reads an archive made by athena-diag.sh and writes one HTML page
with what was measured and what to change, each recommendation backed by the numbers it came from.

    python3 athena_diag_report.py athena-diag-arena1-20261018-120000.tar.gz [-o report.html]

Standard library only. The rules are in RULES below (and in diag/RULES.md for people)."""

import argparse
import html
import io
import math
import re
import statistics
import sys
import tarfile
import time
from collections import defaultdict

# ---------------------------------------------------------------------------------------- reading


def read_archive(path):
    files = {}
    with tarfile.open(path, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            name = m.name.split("/", 1)[1] if "/" in m.name else m.name
            files[name] = tar.extractfile(m).read().decode("utf-8", "replace")
    return files


def parse_meta(text):
    meta = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            meta[k.strip()] = v.strip()
    return meta


def parse_tsv(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    head = lines[0].split("\t")
    rows = []
    for l in lines[1:]:
        cells = l.split("\t")
        row = {}
        for k, v in zip(head, cells):
            try:
                row[k] = float(v)
            except ValueError:
                row[k] = None
        rows.append(row)
    return rows


def parse_csv(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    head = lines[0].split(",")
    rows = []
    for l in lines[1:]:
        cells = l.split(",")
        if len(cells) != len(head):
            continue
        rows.append({k: float(v) for k, v in zip(head, cells)})
    return rows


def parse_threads(text):
    """epoch, thread name, utime, stime -> list of snapshots {epoch: {name: ticks}}"""
    snaps = defaultdict(lambda: defaultdict(float))
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) != 4:
            continue
        try:
            snaps[int(p[0])][p[1]] += float(p[2]) + float(p[3])
        except ValueError:
            continue
    return dict(sorted(snaps.items()))


GC_RE = re.compile(r"^\[([^\]]+)\]\[(\d+)ms\]\[[^\]]*\]\[gc\s*\] GC\((\d+)\) Pause (Young|Full|Remark|Cleanup)(.*?) (\d+)M->(\d+)M\((\d+)M\) ([\d.]+)ms")


def parse_gc(text):
    events = []
    for line in text.splitlines():
        m = GC_RE.match(line)
        if not m:
            continue
        causes = re.findall(r"\(([^)]*)\)", m.group(5))
        events.append({
            "time": m.group(1), "uptime_ms": int(m.group(2)), "kind": m.group(4),
            "cause": " / ".join(causes), "before": int(m.group(6)), "after": int(m.group(7)),
            "capacity": int(m.group(8)), "pause_ms": float(m.group(9)),
            "epoch": parse_time(m.group(1)),
        })
    return events


def parse_time(s):
    # 2026-10-18T20:27:19.438+0900
    try:
        base, frac_tz = s.split(".", 1)
        return _epoch(base, frac_tz[3:]) + int(frac_tz[:3]) / 1000.0
    except (ValueError, IndexError):
        return None


def _epoch(base, tz):
    import calendar
    t = time.strptime(base, "%Y-%m-%dT%H:%M:%S")
    sign = 1 if tz[0] == "+" else -1
    off = sign * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60)
    return calendar.timegm(t) - off


def flag(flags_text, name):
    m = re.search(r"-XX:([+-])" + re.escape(name) + r"\b", flags_text)
    if m:
        return m.group(1) == "+"
    m = re.search(r"-XX:" + re.escape(name) + r"=(\S+)", flags_text)
    return m.group(1) if m else None


def yml(text, key):
    m = re.search(r"^\s*" + re.escape(key) + r"\s*:\s*(\S+)", text or "", re.M)
    return m.group(1).strip("'\"") if m else None


def prop(text, key):
    m = re.search(r"^" + re.escape(key) + r"=(.*)$", text or "", re.M)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------------------- analysis

def thread_group(name):
    n = name.lower()
    if n.startswith("server thread"):
        return "サーバースレッド（ゲームの処理）"
    if n.startswith("netty") or "epoll" in n or "uring" in n:
        return "Netty（送受信・圧縮）"
    if n.startswith("c2 compiler") or n.startswith("c1 compiler"):
        return "JITコンパイラ"
    if n.startswith("gc ") or n.startswith("vm thread") or n.startswith("g1") or n.startswith("gc thread") or n.startswith("parallel"):
        return "GC・VM"
    if "chunk i/o" in n or n.startswith("file io") or "chunkgen" in n or "decorat" in n:
        return "チャンクの読み書き・生成"
    if n.startswith("log4j") or "async" in n and "log" in n:
        return "ログ"
    return "その他（プラグインなど）"


def mib(kb):
    return kb / 1024.0 if kb is not None else None


def ceil16(mb):
    return int(math.ceil(mb / 16.0) * 16)


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    i = min(len(v) - 1, max(0, int(round(q / 100.0 * (len(v) - 1)))))
    return v[i]


def analyse(files):
    a = {}
    meta = parse_meta(files.get("meta.txt", ""))
    a["meta"] = meta
    clk = float(meta.get("clk_tck", "100"))
    samples = parse_tsv(files.get("samples.tsv", ""))
    athena = parse_csv(files.get("athena.csv", ""))
    gc = parse_gc(files.get("gc.log", ""))
    flags = files.get("vm_flags.txt", "") + "\n" + files.get("vm_command_line.txt", "")
    a["flags_text"] = flags
    a["vm_version"] = files.get("vm_version.txt", "")
    cfg = {k[len("config/"):]: v for k, v in files.items() if k.startswith("config/")}
    a["config"] = cfg
    a["plugins"] = [l.split(" ", 1) for l in files.get("plugins.txt", "").splitlines() if l.strip()]
    a["samples"], a["athena"], a["gc"] = samples, athena, gc

    # CPU per sample window
    cpu = []
    for p, q in zip(samples, samples[1:]):
        dt = q["epoch"] - p["epoch"]
        if dt <= 0:
            continue
        cores = ((q["utime"] + q["stime"]) - (p["utime"] + p["stime"])) / clk / dt
        tot_p = sum(p[k] or 0 for k in ("cpu_user", "cpu_nice", "cpu_system", "cpu_idle", "cpu_iowait", "cpu_steal"))
        tot_q = sum(q[k] or 0 for k in ("cpu_user", "cpu_nice", "cpu_system", "cpu_idle", "cpu_iowait", "cpu_steal"))
        steal = ((q["cpu_steal"] or 0) - (p["cpu_steal"] or 0)) / (tot_q - tot_p) if tot_q > tot_p else 0
        cpu.append({"epoch": q["epoch"], "cores": cores, "steal": steal})
    a["cpu"] = cpu

    # players at each moment, from Athena's lines
    def players_at(epoch):
        best = None
        for r in athena:
            if r["epoch_ms"] / 1000.0 <= epoch + 5:
                best = r
            else:
                break
        return best["players"] if best else None

    for c in cpu:
        c["players"] = players_at(c["epoch"])
    active = [c["cores"] for c in cpu if c["players"]]
    idle = [c["cores"] for c in cpu if c["players"] == 0]
    a["cores_all"] = statistics.mean([c["cores"] for c in cpu]) if cpu else None
    a["cores_active"] = statistics.mean(active) if active else None
    a["cores_idle"] = statistics.mean(idle) if idle else None
    a["cores_p95"] = pct([c["cores"] for c in cpu], 95)
    a["steal_mean"] = statistics.mean([c["steal"] for c in cpu]) if cpu else None

    a["rss_max"] = max((mib(s["rss_kb"]) for s in samples if s["rss_kb"]), default=None)
    a["anon_max"] = max((mib(s["anon_kb"]) for s in samples if s["anon_kb"]), default=None)
    a["avail_min"] = min((mib(s["mem_available_kb"]) for s in samples if s["mem_available_kb"]), default=None)
    a["threads_max"] = max((s["threads"] for s in samples if s["threads"]), default=None)

    # threads by group over the whole run
    snaps = parse_threads(files.get("threads.tsv", ""))
    groups = defaultdict(float)
    if len(snaps) >= 2:
        first, last = list(snaps.items())[0], list(snaps.items())[-1]
        span = last[0] - first[0]
        for name, ticks in last[1].items():
            d = ticks - first[1].get(name, 0.0)
            if d > 0:
                groups[thread_group(name)] += d / clk / span
    a["thread_groups"] = sorted(groups.items(), key=lambda kv: -kv[1])

    # GC
    full = [e for e in gc if e["kind"] == "Full"]
    young = [e for e in gc if e["kind"] == "Young"]
    a["gc_full"], a["gc_young"] = full, young
    a["gc_pause_max"] = max((e["pause_ms"] for e in gc), default=None)
    a["gc_pause_p95"] = pct([e["pause_ms"] for e in gc], 95)
    a["heap_capacity"] = max((e["capacity"] for e in gc), default=None)
    a["live_after_full"] = max((e["after"] for e in full), default=None)
    a["after_gc_max"] = max((e["after"] for e in gc), default=None)
    a["codecache_full"] = [e for e in full if "CodeCache" in e["cause"]]
    a["alloc_full"] = [e for e in full if "CodeCache" not in e["cause"] and "System.gc" not in e["cause"] and "Heap Dump" not in e["cause"] and "Diagnostic" not in e["cause"]]
    if gc and len(gc) > 1 and gc[-1]["epoch"] and gc[0]["epoch"]:
        span_min = max((gc[-1]["epoch"] - gc[0]["epoch"]) / 60.0, 1e-9)
        a["young_per_min"] = len(young) / span_min
    else:
        a["young_per_min"] = None

    # Athena's tick lines
    if athena:
        span = (athena[-1]["epoch_ms"] - athena[0]["epoch_ms"]) / 1000.0
        ticks = sum(r["ticks"] for r in athena[1:])
        a["tps"] = ticks / span if span > 0 else None
        win_tps = []
        for p, q in zip(athena, athena[1:]):
            dt = (q["epoch_ms"] - p["epoch_ms"]) / 1000.0
            if dt > 0:
                win_tps.append(q["ticks"] / dt)
        a["tps_min"] = min(win_tps) if win_tps else None
        worst = max(athena, key=lambda r: r["tick_ms_max"])
        a["tick_max"] = worst["tick_ms_max"]
        a["tick_max_at"] = worst["epoch_ms"] / 1000.0
        a["tick_max_ctx"] = "プレイヤー%d人、チャンク%d、ワールド%d" % (worst["players"], worst["chunks"], worst["worlds"])
        a["over50"] = sum(r["ticks_over_50ms"] for r in athena)
        ms = [r["tick_ms_sum"] / r["ticks"] for r in athena if r["ticks"] and r["players"]]
        a["tick_mean_active"] = statistics.mean(ms) if ms else None
        a["players_max"] = max(r["players"] for r in athena)
        a["chunks_max"] = max(r["chunks"] for r in athena)
        a["entities_max"] = max(r["entities"] for r in athena)
        a["active_share"] = sum(1 for r in athena if r["players"]) / len(athena)
    return a


# ---------------------------------------------------------------------------------------- rules
# Each rule returns None (nothing to say) or a dict: level ok|recommend|decide|warn, title, now,
# change, why (the measurement it comes from), effect (what Athena measured for it, on which load).

def rules(a):
    out = []
    f = a["flags_text"]
    cfg = a["config"]
    nproc = int(a["meta"].get("nproc", "0") or 0)
    xmx = flag(f, "MaxHeapSize")
    xmx_mb = int(xmx) // (1024 * 1024) if xmx and xmx.isdigit() else None
    jdk = re.search(r"^JDK (\d+)", a["vm_version"], re.M)
    jdk = int(jdk.group(1)) if jdk else None
    gc_kind = "G1" if flag(f, "UseG1GC") else "Parallel" if flag(f, "UseParallelGC") else "Serial" if flag(f, "UseSerialGC") else "ZGC" if flag(f, "UseZGC") else "不明"
    a["gc_kind"], a["xmx_mb"], a["jdk"] = gc_kind, xmx_mb, jdk

    # 1. heap size
    live = a["live_after_full"]
    upper = a["after_gc_max"]
    target = None
    hours = (float(a["meta"].get("ended_epoch", "0") or 0) - float(a["meta"].get("started_epoch", "0") or 0)) / 3600.0
    if xmx_mb and (live or upper):
        basis = max(live or 0, upper or 0)
        # without a full GC the live heap is only bounded from above by what young GCs left, and the
        # window may have missed the busiest moment: never below what Athena itself needs (144M ran
        # the heaviest SG map, 128M did not)
        target = max(128 if live else 144, ceil16(basis * 1.45))
        if a["alloc_full"] and target > xmx_mb:
            target = max(target, ceil16(basis * 1.6))
        if a["alloc_full"] and (not live or target > xmx_mb):
            out.append(dict(level="warn", title="ヒープが足りていません",
                            now="-Xmx%dM、メモリ不足によるフルGC %d回（最長 %.0f ms）" % (xmx_mb, len(a["alloc_full"]), max(e["pause_ms"] for e in a["alloc_full"])),
                            change="-Xms%dM -Xmx%dM" % (target, target),
                            why="GC後に残ったヒープが最大 %d MB。その約1.6倍を目安にしています。" % basis,
                            effect="Athenaの計測（SG 20人、15分の試合）：生きているヒープ約100MBに対して128Mではフルが3回（120〜165 ms）、144Mでは試合中0回。"))
        elif xmx_mb > target * 1.5:
            short = hours < 1.0 or not live
            out.append(dict(level="decide" if short else "recommend", title="ヒープを減らせます" + ("（長い計測で確かめてから）" if short else ""),
                            now="-Xmx%dM" % xmx_mb,
                            change="-Xms%dM -Xmx%dM" % (target, target),
                            why=("GC後のヒープが最大 %d MB" % basis) + ("" if live else "（フルGCがなかったので、生きているデータはこれ以下）"),
                            effect="1サーバーあたり約 %d MB のメモリが空きます。目安は「生きているヒープの1.45倍」：Athenaの計測で、約100MBに対して144Mなら試合中のフルGCは0回、128Mでは3回でした。%s" % (
                                xmx_mb - target,
                                ("この計測は%d分と短く、フルGCも起きていないので、一番重いマップと一番多い人数を含めて3時間以上測り直し、生きているヒープを確かめてから変えてください。" % round(hours * 60)) if short and hours < 1.0 else
                                ("この計測ではフルGCが起きていないため、生きているヒープは上限しか分かりません。一番重いマップと一番多い人数の時間を含めて確かめてから変えてください。" if short else ""))))
        else:
            out.append(dict(level="ok", title="ヒープの大きさは適切です",
                            now="-Xmx%dM" % xmx_mb, change="",
                            why="GC後のヒープが最大 %d MB%s" % (basis, ("、メモリ不足のフルGCが%d回（最長 %.0f ms）あったが、推奨値以内" % (len(a["alloc_full"]), max(e["pause_ms"] for e in a["alloc_full"]))) if a["alloc_full"] else ""),
                            effect=""))

    elif xmx_mb:
        out.append(dict(level="decide", title="ヒープの大きさを判断できません",
                        now="-Xmx%dM、測った時間にGCが一度も起きなかった" % xmx_mb,
                        change="プレイヤーがいる時間帯を含めて、もっと長く測る",
                        why="ヒープの大きさは、GCの後に残った量から決めます。GCが起きないほど負荷が軽い時間だけでは、必要な量が分かりません。",
                        effect=""))

    # 2. GC
    heap_after = min(xmx_mb, target) if xmx_mb and target else xmx_mb
    if gc_kind == "G1" and heap_after and heap_after <= 1024:
        out.append(dict(level="recommend", title="GCを Parallel に",
                        now="G1GC（JVMが自動で選んだ可能性があります）", change="-XX:+UseParallelGC",
                        why="ヒープが %d MB 程度なら、G1は自分の管理領域にヒープ以外のメモリを多く使います。" % heap_after,
                        effect="Athenaの計測（1GB VPS）：G1はParallel/Serialより約115 MB多く使いました。"))
    elif gc_kind == "Serial" and nproc >= 2:
        out.append(dict(level="recommend", title="GCを Parallel に",
                        now="SerialGC（CPU %d個）" % nproc, change="-XX:+UseParallelGC",
                        why="フルGCの停止中、Serialは1つのCPUしか使いません。",
                        effect="Athenaの計測（2 vCPU）：フルGCの停止がSerialの125〜185 msからParallelで77〜107 msに。メモリとCPUは同じ。"))
    else:
        out.append(dict(level="ok", title="GCの種類", now=gc_kind + "GC", change="", why="", effect=""))

    # 3. code cache full GCs
    if a["codecache_full"]:
        out.append(dict(level="recommend", title="JITのコード領域が原因のフルGCを止める",
                        now="「CodeCache GC Threshold」のフルGC %d回" % len(a["codecache_full"]),
                        change="-XX:ReservedCodeCacheSize=48m -XX:SweeperThreshold=50",
                        why="新しくコンパイルされたコードが基準を超えるたびに、ヒープに関係なくフルGCが起きています。",
                        effect="Athenaの計測：初期値では試合開始から2分で50〜200 msのフルGCが5〜6回。この設定で0回。上限は予約だけで、実際のメモリはほぼ増えません。"))
    elif a["gc"]:
        out.append(dict(level="ok", title='JITのコード領域が原因のフルGC', now="なし", change="", why="", effect=""))

    # 4. object headers
    if jdk and jdk >= 25 and not flag(f, "UseCompactObjectHeaders"):
        out.append(dict(level="recommend", title="オブジェクトのヘッダを小さく",
                        now="なし", change="-XX:+UseCompactObjectHeaders",
                        why="Java 25では、すべてのオブジェクトのヘッダを12バイトから8バイトにできます。",
                        effect="Athenaの計測（エンティティ30,000体の飽和負荷）：TPS +0.57%、ヒープも小さくなる。"))
    elif flag(f, "UseCompactObjectHeaders"):
        out.append(dict(level="ok", title='オブジェクトのヘッダ', now="UseCompactObjectHeaders あり", change="", why="", effect=""))

    # 5. native memory
    env_malloc = a["meta"].get("env_MALLOC_ARENA_MAX")
    if not env_malloc:
        out.append(dict(level="recommend", title="glibc のメモリ領域を減らす",
                        now="MALLOC_ARENA_MAX なし", change="起動前に export MALLOC_ARENA_MAX=2、と -XX:TrimNativeHeapInterval=5000",
                        why="glibcはスレッドごとにメモリ領域を作り、解放したメモリを抱えたままにします。",
                        effect="Athenaの計測：329 MB → 273 MB（約56 MB減）。"))
    else:
        out.append(dict(level="ok", title='glibc のメモリ領域', now="MALLOC_ARENA_MAX=%s" % env_malloc, change="", why="", effect=""))
    if not flag(f, "AOTCache"):
        out.append(dict(level="recommend", title="AOTキャッシュで起動する（start-small.sh）",
                        now="AOTキャッシュなし", change="deploy/start-small.sh で起動（パッチ済みjarを直接起動し、AOTキャッシュを作って使う）",
                        why="クラスの読み込みとリンクの結果を、起動のたびにメモリ上で作り直しています。",
                        effect="Athenaの計測：1サーバーあたりネイティブメモリ約35 MB減、起動直後のJITが軽くなる。同じVPSのサーバー同士で、jarとキャッシュを共有できる。"))
    elif flag(f, "ArchiveRelocationMode") != "0":
        out.append(dict(level="recommend", title="AOTキャッシュを書き換えずに使う",
                        now="ArchiveRelocationMode が 0 ではない", change="-XX:+UnlockDiagnosticVMOptions -XX:ArchiveRelocationMode=0",
                        why="キャッシュを書き換えて使うと、そのページはサーバーごとのメモリになります。",
                        effect="これがないと、AOTキャッシュの節約がほとんど消えました。"))
    else:
        out.append(dict(level="ok", title='AOTキャッシュ', now="あり（ArchiveRelocationMode=0）", change="", why="", effect=""))

    # 6. configs
    nt = yml(cfg.get("spigot.yml"), "netty-threads")
    if nt and nt.isdigit() and int(nt) > 1 and nproc and nproc <= 2:
        out.append(dict(level="recommend", title="Nettyのスレッドを1本に",
                        now="netty-threads: %s（CPU %d個）" % (nt, nproc), change="spigot.yml の settings.netty-threads: 1",
                        why="CPUが少ないVPSでは、送受信のスレッドを増やしても並列に動けません。",
                        effect="Athenaの計測（20人、1 CPU）：Nettyの CPU時間 13.1 s → 11.6 s（1回の計測あたり）。"))
    elif nt:
        out.append(dict(level="ok", title='Nettyのスレッド数', now="netty-threads: %s（CPU %d個）" % (nt, nproc), change="", why="", effect=""))
    margin = yml(cfg.get("pandaspigot.yml"), "chunkUnloadMargin")
    if margin is None or margin == "0":
        out.append(dict(level="decide", title="視界の外のチャンクを少し残す",
                        now="chunkUnloadMargin: %s" % (margin or "なし（0）"), change="pandaspigot.yml の worlds.default.chunkUnloadMargin: 2",
                        why="チャンクの境界を行き来するたびに、チャンクを消して送り直しています。",
                        effect="Athenaの計測（20人、境界を往復するbot）：Netty −22%、通信量 −83%。その分、読み込むチャンクが増えます（1つ約11 KB）。"))
    else:
        out.append(dict(level="ok", title='視界の外のチャンク', now="chunkUnloadMargin: %s" % margin, change="", why="", effect=""))
    # Spigot 1.8 keeps it per world under world-settings; the default section is what most worlds use
    ax = re.search(r"anti-xray:\s*\n\s+enabled:\s*(true|false)", cfg.get("spigot.yml", "") or "")
    if ax and ax.group(1) == "true":
        out.append(dict(level="decide", title="アンチXrayを切るか（ミニゲームなら）",
                        now="anti-xray: 有効", change="spigot.yml の world-settings.default.anti-xray.enabled: false",
                        why="アンチXrayが有効だと、プレイヤーごとにチャンクを加工するため、チャンクのパケットのキャッシュが使えません。",
                        effect="Athenaの計測（20人のミニゲーム）：チャンクのキャッシュでCPU 0.475 → 0.14 コア。固定のマップで、鉱石の位置が勝負に関係しないなら切れます。"))

    elif ax:
        out.append(dict(level="ok", title='アンチXray', now="anti-xray: 無効（チャンクのキャッシュが効く）", change="", why="", effect=""))

    # 7. the machine
    total_mb = float(a["meta"].get("mem_total_kb", "0") or 0) / 1024.0
    if a["avail_min"] is not None and total_mb and a["avail_min"] >= total_mb * 0.1:
        out.append(dict(level="ok", title='マシンの空きメモリ', now="空きの最小 %.0f MB（全体 %.0f MB）" % (a["avail_min"], total_mb), change="", why="", effect=""))
    if a["steal_mean"] is not None and a["steal_mean"] <= 0.05:
        out.append(dict(level="ok", title='VPSのCPUのsteal', now="平均 %.1f%%" % (a["steal_mean"] * 100), change="", why="", effect=""))
    long_hitch = a.get("tick_max") is not None and a["tick_max"] >= 250
    if long_hitch:
        out.append(dict(level="warn", title="一瞬止まる時がある（長いtick）",
                        now="最長 %.0f ms（%s、%s）、50 msを超えたtick %d回" % (a["tick_max"], time.strftime("%H:%M:%S", time.localtime(a["tick_max_at"])), a["tick_max_ctx"], a["over50"]),
                        change="その時刻に何が起きていたか（ワールドの読み込み、試合の開始、プラグインの処理）を確かめ、プロファイル（JFR / perf）で原因を調べる",
                        why="平均のTPSが20でも、1回のtickが250 msを超えると、プレイヤーには一瞬止まったように見えます。5秒の平均には表れません。",
                        effect=""))
    if a.get("tps_min") is not None and not long_hitch and not (a.get("over50") and a["tps_min"] < 19.5):
        out.append(dict(level="ok", title='ラグ（50 msを超えたtick）', now="%d回、5秒間の最低TPS %.1f" % (a.get("over50", 0), a["tps_min"]), change="", why="", effect=""))
    if a["avail_min"] is not None and total_mb and a["avail_min"] < total_mb * 0.1:
        out.append(dict(level="warn", title="マシンの空きメモリが少ない",
                        now="空きの最小 %.0f MB（全体 %.0f MB）" % (a["avail_min"], total_mb), change="ヒープやサーバーの台数を見直す",
                        why="空きが10%を切ると、OSがキャッシュを捨て始め、最悪の場合OOMでサーバーが落ちます。", effect=""))
    if a["steal_mean"] is not None and a["steal_mean"] > 0.05:
        out.append(dict(level="warn", title="VPSのCPUを他の利用者に取られています",
                        now="steal 平均 %.1f%%" % (a["steal_mean"] * 100), change="プランやホストの変更を検討",
                        why="steal は、割り当てられたCPUを仮想化の基盤が他に回した時間です。サーバー側の設定では減らせません。", effect=""))
    if a.get("over50") and a.get("tps_min") is not None and a["tps_min"] < 19.5:
        out.append(dict(level="warn", title="ラグ（50 msを超えたtick）",
                        now="%d回、最長 %.0f ms、5秒間の最低TPS %.1f" % (a["over50"], a["tick_max"], a["tps_min"]), change="プロファイル（perf / JFR）で原因を調べる",
                        why="1 tickが50 msを超えると、プレイヤーにはカクつきとして見えます。", effect=""))
    return out


# ---------------------------------------------------------------------------------------- charts

def nice_step(span):
    if span <= 0:
        return 1.0
    raw = span / 4.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def svg_chart(series, title, unit, height=200, hline=None, hline_label=None, xrange=None):
    """series: list of (label, css var, [(epoch, value)], kind 'line'|'dot')"""
    pts = [p for s in series for p in s[2] if p[1] is not None]
    if not pts:
        return ""
    w, h, l, r, t, b = 760, height, 52, 14, 14, 26
    x0, x1 = xrange if xrange else (min(p[0] for p in pts), max(p[0] for p in pts))
    ys = [p[1] for p in pts] + ([hline] if hline else [])
    y0 = 0
    step = nice_step(max(ys) * 1.05 or 1)
    y1 = step * max(1, math.ceil(max(ys) * 1.05 / step))
    if x1 == x0:
        x1 = x0 + 1
    X = lambda x: l + (x - x0) / (x1 - x0) * (w - l - r)
    Y = lambda y: t + (1 - (y - y0) / (y1 - y0)) * (h - t - b)
    o = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s">' % (w, h, html.escape(title))]
    yv = 0.0
    while yv <= y1 + step / 1000:
        yy = Y(yv)
        o.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (l, w - r, yy, yy))
        o.append('<text x="%d" y="%.1f" class="axis" text-anchor="end">%s</text>' % (l - 6, yy + 4, fmt_axis(yv)))
        yv += step
    for i in range(6):
        xv = x0 + (x1 - x0) * i / 5
        anchor = "start" if i == 0 else "end" if i == 5 else "middle"
        o.append('<text x="%.1f" y="%d" class="axis" text-anchor="%s">%s</text>' % (X(xv), h - 6, anchor, time.strftime("%H:%M", time.localtime(xv))))
    if hline:
        o.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="hline"/>' % (l, w - r, Y(hline), Y(hline)))
        if hline_label:
            o.append('<text x="%d" y="%.1f" class="axis hl" text-anchor="end">%s</text>' % (w - r, Y(hline) - 4, html.escape(hline_label)))
    for label, color, data, kind in series:
        data = [p for p in data if p[1] is not None]
        if not data:
            continue
        if kind == "dot":
            for x, y in data:
                o.append('<circle cx="%.1f" cy="%.1f" r="2.2" fill="var(%s)"/>' % (X(x), Y(y), color))
        else:
            step = max(1, len(data) // 700)
            d = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(x), Y(y)) for i, (x, y) in enumerate(data[::step]))
            o.append('<path d="%s" fill="none" stroke="var(%s)" stroke-width="1.6"/>' % (d, color))
    o.append("</svg>")
    legend = "".join('<span><i style="background:var(%s)"></i>%s</span>' % (c, html.escape(lbl)) for lbl, c, _, _ in series)
    return '<figure><figcaption>%s <small>%s</small></figcaption>%s<div class="legend">%s</div></figure>' % (html.escape(title), html.escape(unit), "".join(o), legend)


def fmt_axis(v):
    if v == 0:
        return "0"
    if abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return ("%.3f" % v).rstrip("0").rstrip(".")


def smooth(points, n):
    out = []
    for i in range(len(points)):
        win = [p[1] for p in points[max(0, i - n + 1):i + 1] if p[1] is not None]
        out.append((points[i][0], sum(win) / len(win) if win else None))
    return out


# ---------------------------------------------------------------------------------------- page

JP = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
LEVELS = {"warn": ("要対応", "lv-warn"), "recommend": ("推奨", "lv-rec"), "decide": ("要判断", "lv-dec"), "ok": ("問題なし", "lv-ok")}


def page(a, recs, source_name):
    e = html.escape
    m = a["meta"]
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(m.get("started_epoch", "0"))))
    ended = time.strftime("%H:%M", time.localtime(float(m.get("ended_epoch", m.get("started_epoch", "0")))))
    hours = (float(m.get("ended_epoch", "0") or 0) - float(m.get("started_epoch", "0") or 0)) / 3600.0
    total_mb = float(m.get("mem_total_kb", "0") or 0) / 1024.0

    def kpi(v, k, c=""):
        return '<div class="kpi"><span class="v">%s</span><span class="k">%s</span><span class="c">%s</span></div>' % (v, e(k), e(c))

    def n(v, f="%.0f", d="—"):
        return f % v if v is not None else d

    kpis = [
        kpi(n(a["cores_active"], "%.3f"), "使ったCPU（プレイ中の平均）", "コア数。待機中 %s、95%%値 %s" % (n(a["cores_idle"], "%.3f"), n(a["cores_p95"], "%.3f"))),
        kpi(n(a["anon_max"]) + " MB", "プロセスのメモリ（anonの最大）", "RSSの最大 %s MB" % n(a["rss_max"])),
        kpi(n(max(a["live_after_full"] or 0, a["after_gc_max"] or 0) or None) + " MB", "GC後のヒープの最大", "上限 -Xmx %s M" % n(a.get("xmx_mb"))),
        kpi("%d 回" % len(a["gc_full"]), "フルGC", "最長の停止 %s ms" % n(max((g["pause_ms"] for g in a["gc_full"]), default=None))),
    ]
    if a["athena"]:
        kpis += [
            kpi(n(a.get("tps"), "%.2f"), "TPS（期間の平均）", "5秒間の最低 %s" % n(a.get("tps_min"), "%.1f")),
            kpi("%d 回" % a.get("over50", 0), "50 msを超えたtick", "最長 %s ms" % n(a.get("tick_max"))),
            kpi(n(a.get("players_max")) + " 人", "最大のプレイヤー数", "プレイ中だった時間 %s%%" % n(a.get("active_share", 0) * 100)),
            kpi(n(a.get("chunks_max")), "読み込んだチャンクの最大", "エンティティ最大 %s" % n(a.get("entities_max"))),
        ]

    rec_rows = []
    order = {"warn": 0, "recommend": 1, "decide": 2, "ok": 3}
    oks = [r for r in recs if r["level"] == "ok"]
    recs = [r for r in recs if r["level"] != "ok"]
    if not recs:
        rec_rows.append('<p class="note good">変えた方がよい項目は見つかりませんでした。</p>')
    for r in sorted(recs, key=lambda r: order[r["level"]]):
        label, cls = LEVELS[r["level"]]
        rec_rows.append(
            '<article class="rec %s"><header><span class="badge">%s</span><h3>%s</h3></header>'
            '<dl><dt>今</dt><dd>%s</dd>%s<dt>根拠</dt><dd>%s</dd>%s</dl></article>' % (
                cls, label, e(r["title"]), e(r["now"]),
                ('<dt>変更</dt><dd>%s</dd>' % (e(r["change"]) if JP.search(r["change"]) else '<code>%s</code>' % e(r["change"]))) if r["change"] else "",
                e(r["why"]) or "—",
                ('<dt>見込み</dt><dd>%s</dd>' % e(r["effect"])) if r["effect"] else ""))

    if oks:
        rec_rows.append('<h3 class="okh">確認して問題がなかった項目</h3><div class="tbl"><table><tbody>%s</tbody></table></div>' % "".join(
            '<tr><th><span class="okdot"></span>%s</th><td>%s%s</td></tr>' % (e(r["title"]), e(r["now"]), (' <span class="muted">（%s）</span>' % e(r["why"])) if r["why"] else "") for r in oks))

    charts = []
    xr = (float(m.get("started_epoch", "0") or 0), float(m.get("ended_epoch", m.get("started_epoch", "0")) or 0))
    xr = xr if xr[1] > xr[0] else None
    cpu = [(c["epoch"], c["cores"]) for c in a["cpu"]]
    charts.append(svg_chart([("CPU（1分平均）", "--c1", smooth(cpu, max(1, int(60 / float(m.get("interval_s", "5") or 5)))), "line")], "使ったCPU", "コア数", xrange=xr))
    if a["athena"]:
        ath = a["athena"]
        charts.append(svg_chart([
            ("プレイヤー", "--c1", [(r["epoch_ms"] / 1000.0, r["players"]) for r in ath], "line"),
        ], "プレイヤー数", "人", xrange=xr))
        charts.append(svg_chart([
            ("チャンク", "--c2", [(r["epoch_ms"] / 1000.0, r["chunks"]) for r in ath], "line"),
            ("エンティティ", "--c3", [(r["epoch_ms"] / 1000.0, r["entities"]) for r in ath], "line"),
        ], "読み込んだチャンクとエンティティ", "個", xrange=xr))
        charts.append(svg_chart([
            ("最長のtick（5秒ごと）", "--c4", [(r["epoch_ms"] / 1000.0, r["tick_ms_max"]) for r in ath], "line"),
            ("平均のtick", "--c1", [(r["epoch_ms"] / 1000.0, r["tick_ms_sum"] / r["ticks"] if r["ticks"] else None) for r in ath], "line"),
        ], "tickにかかった時間", "ms", hline=50, hline_label="50 ms（1 tickの持ち時間）", xrange=xr))
    charts.append(svg_chart([
        ("RSS", "--c1", [(s["epoch"], mib(s["rss_kb"])) for s in a["samples"]], "line"),
        ("anon", "--c2", [(s["epoch"], mib(s["anon_kb"])) for s in a["samples"]], "line"),
        ("マシンの空き", "--c3", [(s["epoch"], mib(s["mem_available_kb"])) for s in a["samples"]], "line"),
    ], "メモリ", "MB", xrange=xr))
    if a["gc"]:
        charts.append(svg_chart([
            ("ヤングGC後", "--c1", [(g["epoch"], g["after"]) for g in a["gc_young"]], "dot"),
            ("フルGC後", "--c4", [(g["epoch"], g["after"]) for g in a["gc_full"]], "dot"),
        ], "GC後のヒープ", "MB", hline=a.get("xmx_mb"), hline_label="-Xmx", xrange=xr))

    tg = a["thread_groups"]
    tg_total = sum(v for _, v in tg) or 1
    tg_rows = "".join('<tr><td>%s</td><td class="r">%.3f</td><td class="r">%.0f%%</td><td><span class="bar" style="width:%.0f%%"></span></td></tr>' % (e(k), v, v / tg_total * 100, v / tg_total * 100) for k, v in tg)

    gc_rows = ""
    if a["gc"]:
        causes = defaultdict(lambda: [0, 0.0])
        for g in a["gc"]:
            key = ("フル" if g["kind"] == "Full" else "ヤング" if g["kind"] == "Young" else g["kind"]) + "：" + g["cause"]
            causes[key][0] += 1
            causes[key][1] = max(causes[key][1], g["pause_ms"])
        gc_rows = "".join('<tr><td>%s</td><td class="r">%d</td><td class="r">%.1f</td></tr>' % (e(k), c, mx) for k, (c, mx) in sorted(causes.items(), key=lambda kv: -kv[1][0]))

    cfg = a["config"]
    sp = cfg.get("server.properties")
    conf_rows = [
        ("Java", re.sub(r"\s+", " ", a["vm_version"].splitlines()[1] if len(a["vm_version"].splitlines()) > 1 else a["vm_version"])[:90]),
        ("GC", a.get("gc_kind", "?")),
        ("ヒープ（-Xmx）", "%s M" % n(a.get("xmx_mb"))),
        ("view-distance", "spigot.yml %s（server.properties %s）" % (yml(cfg.get("spigot.yml"), "view-distance") or "—", prop(sp, "view-distance") or "—")),
        ("max-players", prop(sp, "max-players") or "—"),
        ("netty-threads", yml(cfg.get("spigot.yml"), "netty-threads") or "—"),
        ("chunkUnloadMargin", yml(cfg.get("pandaspigot.yml"), "chunkUnloadMargin") or "—"),
        ("network-compression-threshold", prop(sp, "network-compression-threshold") or "—"),
        ("MALLOC_ARENA_MAX", m.get("env_MALLOC_ARENA_MAX", "なし")),
        ("AOTキャッシュ", "あり" if flag(a["flags_text"], "AOTCache") else "なし"),
    ]
    conf_html = "".join("<tr><th>%s</th><td>%s</td></tr>" % (e(k), e(str(v))) for k, v in conf_rows)
    plugins = ", ".join(e(p[1].rsplit(".", 1)[0]) for p in a["plugins"]) or "—"

    machine = "%s · %s CPU · %.0f MB · kernel %s" % (m.get("cpu_model", "?"), m.get("nproc", "?"), total_mb, m.get("kernel", "?"))
    athena_note = "" if a["athena"] else '<p class="note">このサーバーからは、Athenaのtickの記録（athena-diag.csv）が取れませんでした。Athena 0317より前の版か、Athena以外のサーバーです。TPS、tickの時間、プレイヤー数、チャンク数は、このレポートに含まれていません。</p>'

    return TEMPLATE.format(
        title=e("サーバー診断 — %s" % m.get("server_dir_name", "?")),
        server=e(m.get("server_dir_name", "?")), period=e("%s 〜 %s（%s）" % (started, ended, ("%.1f 時間" % hours) if hours >= 1 else ("%d 分" % round(hours * 60)))),
        machine=e(machine), kpis="".join(kpis), recs="".join(rec_rows), charts="".join(charts),
        tg_rows=tg_rows or '<tr><td colspan="4">—</td></tr>', gc_rows=gc_rows or '<tr><td colspan="3">GCのログが取れませんでした</td></tr>',
        young=n(a.get("young_per_min"), "%.1f"), pause_p95=n(a.get("gc_pause_p95"), "%.1f"),
        conf=conf_html, plugins=plugins, athena_note=athena_note, source=e(source_name),
        interval=e(m.get("interval_s", "5")), generated=e(time.strftime("%Y-%m-%d %H:%M")),
        steal=n((a.get("steal_mean") or 0) * 100, "%.1f"),
        jit_note=("JITコンパイラが %.0f%% と大きく出ています。サーバーを起動してから間もない計測では、Javaがコードを最適化する分がここに入ります。何時間も動かしたあとの計測では小さくなります。" % (dict(tg).get("JITコンパイラ", 0) / tg_total * 100)) if dict(tg).get("JITコンパイラ", 0) / tg_total > 0.3 else "")


TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#f5f6f4; --surface:#fff; --fg:#1c2125; --muted:#5c666e; --line:#dce1e0; --accent:#2b5d7d;
  --c1:#2b5d7d; --c2:#3a8f6a; --c3:#b07d2a; --c4:#c0463a;
  --warn:#b3261e; --warn-soft:#fbe9e7; --rec:#2b5d7d; --rec-soft:#e3edf3; --dec:#9a5b00; --dec-soft:#fbf0df; --ok:#2f7a4f; --ok-soft:#e3f1e8;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#121619; --surface:#192025; --fg:#e2e8ea; --muted:#98a4ab; --line:#2a343a; --accent:#7fb6d8;
  --c1:#7fb6d8; --c2:#6fcf97; --c3:#e0b060; --c4:#f08070; --warn:#f08070; --warn-soft:#2e1a18; --rec:#7fb6d8; --rec-soft:#1b2c37; --dec:#f0b360; --dec-soft:#2d2215; --ok:#6fcf97; --ok-soft:#17291f; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font: 15px/1.7 "Hiragino Sans","Noto Sans JP",system-ui,sans-serif; padding: 32px max(16px, env(safe-area-inset-left)) 64px; }}
main {{ max-width: 880px; margin: 0 auto; display: grid; gap: 36px; }}
h1 {{ font-size: 28px; margin: 0; line-height: 1.3; }} h2 {{ font-size: 20px; margin: 0; }} h3 {{ font-size: 16px; margin: 0; }}
p {{ margin: 0; }} .muted {{ color: var(--muted); }} .small {{ font-size: 13px; }}
.eyebrow {{ font-family: var(--mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }}
header.top {{ display: grid; gap: 8px; }}
section {{ display: grid; gap: 14px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }}
.kpi {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; display: grid; gap: 2px; min-width: 0; }}
.kpi .v {{ font-family: var(--mono); font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.kpi .k {{ font-size: 13px; font-weight: 600; }} .kpi .c {{ font-size: 12px; color: var(--muted); }}
.rec {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--line); border-radius: 8px; padding: 12px 16px; display: grid; gap: 8px; }}
.rec header {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
.badge {{ font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 99px; }}
.rec.lv-warn {{ border-left-color: var(--warn); }} .rec.lv-warn .badge {{ background: var(--warn-soft); color: var(--warn); }}
.rec.lv-rec {{ border-left-color: var(--rec); }} .rec.lv-rec .badge {{ background: var(--rec-soft); color: var(--rec); }}
.rec.lv-dec {{ border-left-color: var(--dec); }} .rec.lv-dec .badge {{ background: var(--dec-soft); color: var(--dec); }}
.rec.lv-ok {{ border-left-color: var(--ok); }} .rec.lv-ok .badge {{ background: var(--ok-soft); color: var(--ok); }}
dl {{ margin: 0; display: grid; grid-template-columns: 64px 1fr; gap: 4px 12px; font-size: 14px; }}
dt {{ color: var(--muted); }} dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
code {{ font-family: var(--mono); font-size: 12.5px; background: var(--rec-soft); border-radius: 4px; padding: 1px 5px; }}
figure {{ margin: 0; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px 8px; }}
figcaption {{ font-weight: 600; font-size: 14px; }} figcaption small {{ color: var(--muted); font-weight: 400; margin-left: 6px; }}
svg {{ width: 100%; height: auto; display: block; }}
svg .grid {{ stroke: var(--line); stroke-width: 1; }} svg .axis {{ fill: var(--muted); font-size: 11px; font-family: var(--mono); }}
svg .hline {{ stroke: var(--c4); stroke-dasharray: 5 4; stroke-width: 1.2; }} svg .hl {{ fill: var(--c4); }}
.legend {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }}
.tbl {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
tr:last-child th, tr:last-child td {{ border-bottom: none; }}
th {{ font-weight: 500; color: var(--muted); white-space: nowrap; }}
td.r {{ text-align: right; font-variant-numeric: tabular-nums; font-family: var(--mono); font-size: 13px; white-space: nowrap; }}
.bar {{ display: inline-block; height: 10px; border-radius: 3px; background: var(--c1); min-width: 2px; }}
.okh {{ margin-top: 6px; font-size: 15px; }} .okdot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--ok); margin-right: 8px; vertical-align: 1px; }}
.note.good {{ border-left-color: var(--ok); background: var(--ok-soft); }}
.note {{ border-left: 3px solid var(--dec); background: var(--dec-soft); padding: 10px 14px; border-radius: 0 8px 8px 0; font-size: 14px; }}
ul {{ margin: 0; padding-left: 1.2em; }}
footer {{ font-size: 12px; color: var(--muted); border-top: 1px solid var(--line); padding-top: 12px; }}
@media (max-width: 520px) {{ dl {{ grid-template-columns: 1fr; }} dt {{ margin-top: 4px; }} }}
@media print {{ body {{ background: #fff; }} figure, .rec, .kpi {{ break-inside: avoid; }} }}
</style></head>
<body><main>
<header class="top">
  <span class="eyebrow">Athena サーバー診断</span>
  <h1>{server}</h1>
  <p class="muted">{period} · {machine}</p>
</header>
{athena_note}
<section>
  <h2>要点</h2>
  <div class="kpis">{kpis}</div>
</section>
<section>
  <h2>提案</h2>
  <p class="muted small">「要対応」は今の設定で問題が出ているもの、「推奨」は計測の結果から変えた方がよいもの、「要判断」はゲームの内容によって決めるものです。「見込み」は、Athenaの開発で同じ変更を計測したときの値です。負荷が違うので、このサーバーで同じ値になるとは限りません。変更したら、もう一度このキットで測って比べてください。</p>
  {recs}
</section>
<section>
  <h2>時間ごとの変化</h2>
  {charts}
</section>
<section>
  <h2>CPUの内訳（スレッドごと）</h2>
  <div class="tbl"><table><thead><tr><th>スレッド</th><th class="r">コア数</th><th class="r">割合</th><th style="width:40%"></th></tr></thead><tbody>{tg_rows}</tbody></table></div>
  <p class="muted small">期間全体の平均です。VPSのCPUのsteal（他の利用者に取られた時間）の平均は {steal}% でした。{jit_note}</p>
</section>
<section>
  <h2>GC</h2>
  <div class="tbl"><table><thead><tr><th>種類と原因</th><th class="r">回数</th><th class="r">最長の停止 ms</th></tr></thead><tbody>{gc_rows}</tbody></table></div>
  <p class="muted small">ヤングGCは1分あたり {young} 回、停止の95%値は {pause_p95} ms。50 msを超える停止は、プレイヤーにはカクつきとして見えます。</p>
</section>
<section>
  <h2>設定</h2>
  <div class="tbl"><table><tbody>{conf}</tbody></table></div>
  <p class="muted small">プラグイン：{plugins}</p>
</section>
<section>
  <h2>測り方</h2>
  <ul class="small muted">
    <li>サーバーを止めずに、{interval} 秒ごとにプロセスのCPU時間とメモリ（/proc）を記録しました。</li>
    <li>GCのログは、測っている間だけJVMに追加し（jcmd VM.log）、終わったら外しました。</li>
    <li>TPS・tickの時間・プレイヤー数・チャンク数は、Athena本体が5秒ごとに書き出した値です。</li>
    <li>プレイヤー名、チャット、プレイヤーのIPアドレス、ワールドのデータは集めていません。設定ファイルのパスワードやトークン、アドレスは消してあります。</li>
  </ul>
</section>
<footer>{source} から作成 · {generated} · Athena diagnostics kit</footer>
</main></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    files = read_archive(args.archive)
    a = analyse(files)
    recs = rules(a)
    out = args.out or re.sub(r"\.tar\.gz$", "", args.archive) + ".html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(page(a, recs, args.archive.rsplit("/", 1)[-1]))
    print(out)


if __name__ == "__main__":
    main()
