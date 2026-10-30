"""Group a j25-threads.sh snapshot: CPU seconds per thread group, and the heap mapping's peak RSS."""
import sys, collections, re
TCK = 100.0
def group(name):
    for p, g in [("Server thread", "server"), ("Netty", "netty"), ("C2 Compiler", "jit"), ("C1 Compiler", "jit"),
                 ("Shenandoah", "gc"), ("GC Thread", "gc"), ("ParGC", "gc"), ("VM Thread", "vm"), ("Athena chunk", "compressor"),
                 ("File IO", "fileio"), ("Chunk I/O", "chunkio"), ("Log4j", "log")]:
        if name.startswith(p): return g
    return "other"
for path in sys.argv[1:]:
    g = collections.Counter(); info = {}
    for line in open(path):
        f = line.split()
        if f[0] == "thread":
            g[group(" ".join(f[3:]))] += (int(f[1]) + int(f[2])) / TCK
        else:
            info[f[0]] = f[1]
    tot = sum(g.values())
    print("%-22s total %6.1f s  heap_rss_max %s MB  VmRSS %s MB | %s" % (path.split("/")[-1], tot,
          round(int(info.get("heap_rss_kb_max", 0)) / 1024), round(int(info.get("VmRSS", 0)) / 1024),
          "  ".join("%s %.1f" % (k, v) for k, v in g.most_common())))
