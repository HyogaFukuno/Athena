#!/usr/bin/env bash
# bench only: while sgbench3's server runs, every 5 s write each thread's CPU ticks (utime stime name)
# and the RSS of the Java heap's mapping to $1; the file holds the last snapshot before the JVM exits.
# $1.series gets one line per sample: epoch, JIT compiler ticks, whole-process ticks (for per-match JIT).
D=/opt/mc/sgbench3; OUT=$1; : > $OUT.series
for i in $(seq 1 300); do
  pid=$(for p in $(pgrep -u mc -f org.bukkit.craftbukkit.Main); do [ "$(readlink /proc/$p/cwd)" = $D ] && echo $p; done)
  [ -n "$pid" ] && break; sleep 1
done
[ -z "$pid" ] && exit 1
# the heap mapping is as large as -Xmx
HEAPKB=$(tr '\0' '\n' < /proc/$pid/cmdline | sed -n 's/^-Xmx\([0-9]*\)M$/\1/p' | tail -1); HEAPKB=$(( ${HEAPKB:-144} * 1024 ))
heapmax=0
while [ -d /proc/$pid ]; do
  heap=$(awk -v want=$HEAPKB '/^[0-9a-f]+-[0-9a-f]+ /{insz=0} /^Size:/{insz=($2==want)} insz && /^Rss:/{print $2; exit}' /proc/$pid/smaps 2>/dev/null)
  [ -n "$heap" ] && [ "$heap" -gt "$heapmax" ] && heapmax=$heap
  {
    echo "heap_rss_kb_max $heapmax"
    grep -E '^(VmRSS|RssAnon|RssFile|Threads):' /proc/$pid/status | tr -d ':'
    for t in /proc/$pid/task/*; do
      s=$(sed 's/.*) //' $t/stat 2>/dev/null | awk '{print $12, $13}')
      [ -n "$s" ] && echo "thread $s $(cat $t/comm 2>/dev/null)"
    done
  } > $OUT.tmp 2>/dev/null && mv $OUT.tmp $OUT
  awk -v t=$(date +%s) '$1=="thread"{all+=$2+$3; if ($4 ~ /^C[12]$/) jit+=$2+$3} END{if (all) print t, jit, all}' $OUT >> $OUT.series
  sleep 5
done
