#!/usr/bin/env bash
# Athena diagnostics kit - collector. Measures one running Athena server for a while, without a
# restart, and packs what it saw into one archive for diag/athena_diag_report.py.
#
#   sudo ./athena-diag.sh --dir /opt/mc/arena1 --duration 3h
#
# Run it on the machine the server runs on, as root or as the user that runs the server. It reads
# /proc, asks the JVM (jcmd, as the server's user) to add a GC log for the duration and takes it away
# again afterwards, and creates athena-diag.request in the server directory so that Athena writes
# tick, player and chunk counts to athena-diag.csv (Athena 0317 and later; older servers are
# measured without them). Ctrl-C ends early and still packs what was collected.
#
# What leaves the machine: numbers, the JVM's flags and version, the machine's CPU and memory, plugin
# file names, and the server's configs with passwords, tokens, secrets and addresses blanked. No
# player names, no chat, no IP addresses of players, no worlds.
set -uo pipefail

DIR=""
DURATION="3h"
INTERVAL=5
OUT=""

usage() {
    echo "usage: $0 --dir <server directory> [--duration 3h|90m|600s] [--interval 5] [--out <archive.tar.gz>]" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done
[ -n "$DIR" ] || usage
[ -d "$DIR" ] || { echo "no such directory: $DIR" >&2; exit 1; }
DIR="$(cd "$DIR" && pwd -P)"

case "$DURATION" in
    *h) SECONDS_TOTAL=$(( ${DURATION%h} * 3600 )) ;;
    *m) SECONDS_TOTAL=$(( ${DURATION%m} * 60 )) ;;
    *s) SECONDS_TOTAL=${DURATION%s} ;;
    *) SECONDS_TOTAL=$DURATION ;;
esac

log() { echo "[athena-diag] $*" >&2; }

# ------------------------------------------------------------------ the server's JVM
PID=""
for p in $(pgrep -f java 2>/dev/null); do
    [ "$(readlink "/proc/$p/cwd" 2>/dev/null)" = "$DIR" ] || continue
    PID=$p
    break
done
[ -n "$PID" ] || { echo "no java process is running in $DIR" >&2; exit 1; }

JUSER="$(stat -c %U "/proc/$PID")"
JBIN="$(dirname "$(readlink "/proc/$PID/exe")")"
JCMD="$JBIN/jcmd"
[ -x "$JCMD" ] || JCMD="$(command -v jcmd || true)"

as_jvm_user() {
    if [ "$(id -un)" = "$JUSER" ]; then
        "$@"
    elif [ "$(id -u)" = 0 ]; then
        if command -v runuser >/dev/null; then runuser -u "$JUSER" -- "$@"; else sudo -u "$JUSER" "$@"; fi
    else
        echo "run as root or as $JUSER" >&2
        return 1
    fi
}

jcmd_() {
    [ -n "$JCMD" ] || return 1
    as_jvm_user "$JCMD" "$PID" "$@" 2>&1
}

STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d "/tmp/athena-diag.$STAMP.XXXXXX")"
chmod 755 "$WORK"   # the server's user has to reach the GC log's directory inside it
D="$WORK/athena-diag-$STAMP"
mkdir -p "$D"
# the JVM writes the GC log itself, as its own user
chown "$JUSER" "$D" 2>/dev/null || chmod 777 "$D"
[ -n "$OUT" ] || OUT="$PWD/athena-diag-$(basename "$DIR")-$STAMP.tar.gz"
log "server pid $PID (user $JUSER) in $DIR; collecting for ${SECONDS_TOTAL}s every ${INTERVAL}s"

# ------------------------------------------------------------------ what does not change
CLK_TCK="$(getconf CLK_TCK)"
{
    echo "kit_version=1"
    echo "started_epoch=$(date +%s)"
    echo "duration_s=$SECONDS_TOTAL"
    echo "interval_s=$INTERVAL"
    echo "clk_tck=$CLK_TCK"
    echo "server_dir_name=$(basename "$DIR")"
    echo "pid=$PID"
    echo "nproc=$(nproc)"
    echo "cpu_model=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')"
    echo "mem_total_kb=$(awk '/^MemTotal/ {print $2}' /proc/meminfo)"
    echo "kernel=$(uname -r)"
    echo "thp=$(cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null)"
    echo "min_free_kbytes=$(cat /proc/sys/vm/min_free_kbytes 2>/dev/null)"
    echo "swappiness=$(cat /proc/sys/vm/swappiness 2>/dev/null)"
    # a container's or a systemd unit's memory limit, cgroup v2
    CG="$(awk -F: '$1 == "0" {print $3}' "/proc/$PID/cgroup" 2>/dev/null)"
    [ -n "$CG" ] && echo "cgroup_memory_max=$(cat "/sys/fs/cgroup$CG/memory.max" 2>/dev/null)"
    # only the variables that change how the JVM uses memory, never the whole environment
    tr '\0' '\n' < "/proc/$PID/environ" 2>/dev/null | grep -E '^(MALLOC_ARENA_MAX|JAVA_TOOL_OPTIONS|JDK_JAVA_OPTIONS)=' | sed 's/^/env_/'
    echo "other_java_processes=$(pgrep -c java)"
} > "$D/meta.txt"

jcmd_ VM.version > "$D/vm_version.txt"
jcmd_ VM.flags > "$D/vm_flags.txt"
jcmd_ VM.command_line > "$D/vm_command_line.txt"
jcmd_ GC.heap_info > "$D/heap_info_start.txt"
jcmd_ VM.log list > "$D/vm_log_list.txt"
jcmd_ Compiler.codecache > "$D/codecache_start.txt"

# the server's configs, with anything secret blanked: a key that names a password, token, secret,
# key or address keeps its name and loses its value, and so do the list items under it
mkdir -p "$D/config"
scrub() {
    awk '
        function indent(s) { match(s, /^ */); return RLENGTH }
        {
            if (skipping) {
                if ($0 ~ /^ *- / && indent($0) >= skip_indent) { print substr($0, 1, indent($0)) "- <redacted>"; next }
                skipping = 0
            }
            if (tolower($0) ~ /(password|passwd|secret|token|apikey|api-key|server-ip|address|webhook|\.key[ =:])/ && $0 ~ /[:=]/) {
                sep = index($0, ":"); eq = index($0, "=")
                if (eq > 0 && (sep == 0 || eq < sep)) { print substr($0, 1, eq) "<redacted>"; next }
                print substr($0, 1, sep) " <redacted>"
                skipping = 1; skip_indent = indent($0)
                next
            }
            print
        }'
}
for f in server.properties bukkit.yml spigot.yml paper.yml pandaspigot.yml athena.yml; do
    [ -f "$DIR/$f" ] && scrub < "$DIR/$f" > "$D/config/$f"
done
ls -l "$DIR/plugins" 2>/dev/null | awk '/\.jar$/ {print $5, $NF}' > "$D/plugins.txt"

# ------------------------------------------------------------------ start
GC_LOG="$D/gc.log"
if jcmd_ VM.log output="file=$GC_LOG" output_options=filecount=0 what=gc=info,gc+heap=info,gc+cpu=info,safepoint=info decorators=time,uptimemillis,level,tags > "$D/vm_log_on.txt"; then
    log "GC log on: $GC_LOG"
else
    log "could not add a GC log (jcmd): the report will have no GC data"
fi

CSV_BEFORE=0
[ -f "$DIR/athena-diag.csv" ] && CSV_BEFORE=$(wc -l < "$DIR/athena-diag.csv")
touch "$DIR/athena-diag.request" && chmod 666 "$DIR/athena-diag.request" 2>/dev/null

threads() {  # one snapshot of every thread's CPU ticks: epoch name utime stime
    local now; now=$(date +%s)
    for t in /proc/$PID/task/*; do
        [ -r "$t/stat" ] || continue
        local comm; comm=$(cat "$t/comm" 2>/dev/null) || continue
        # fields after the ")" so that a thread name with spaces cannot shift them
        local rest; rest=$(sed 's/^.*) //' "$t/stat" 2>/dev/null) || continue
        set -- $rest
        echo "$now	$comm	${12}	${13}"
    done
}

finish() {
    trap - INT TERM EXIT
    log "finishing"
    rm -f "$DIR/athena-diag.request"
    threads >> "$D/threads.tsv"
    # stop this output only; any GC log the server was started with stays as it is
    jcmd_ VM.log output="file=$GC_LOG" what=all=off > /dev/null
    jcmd_ GC.heap_info > "$D/heap_info_end.txt"
    jcmd_ Compiler.codecache > "$D/codecache_end.txt"
    # give Athena one more window to write its last line and close the file
    sleep $(( INTERVAL + 1 ))
    if [ -f "$DIR/athena-diag.csv" ]; then
        { head -1 "$DIR/athena-diag.csv"; tail -n +$(( CSV_BEFORE + 1 )) "$DIR/athena-diag.csv" | grep -v '^epoch_ms'; } > "$D/athena.csv"
        # the kit made the file: leave the directory as it found it
        [ "$CSV_BEFORE" = 0 ] && rm -f "$DIR/athena-diag.csv"
    fi
    echo "ended_epoch=$(date +%s)" >> "$D/meta.txt"
    tar -C "$WORK" -czf "$OUT" "$(basename "$D")" && rm -rf "$WORK"
    log "done: $OUT"
    exit 0
}
trap finish INT TERM EXIT

# ------------------------------------------------------------------ sample
echo "epoch	utime	stime	rss_kb	anon_kb	threads	mem_available_kb	cpu_user	cpu_nice	cpu_system	cpu_idle	cpu_iowait	cpu_steal" > "$D/samples.tsv"
threads > "$D/threads.tsv"
END=$(( $(date +%s) + SECONDS_TOTAL ))
NEXT_THREADS=$(( $(date +%s) + 60 ))

while [ "$(date +%s)" -lt "$END" ]; do
    kill -0 "$PID" 2>/dev/null || { log "the server stopped"; break; }
    now=$(date +%s)
    rest=$(sed 's/^.*) //' "/proc/$PID/stat" 2>/dev/null)
    set -- $rest
    utime=${12}; stime=${13}
    rss=$(awk '/^VmRSS/ {print $2}' "/proc/$PID/status")
    nthreads=$(awk '/^Threads/ {print $2}' "/proc/$PID/status")
    anon=$(awk '/^Anonymous:/ {print $2}' "/proc/$PID/smaps_rollup" 2>/dev/null)
    avail=$(awk '/^MemAvailable/ {print $2}' /proc/meminfo)
    read -r _ cu cn cs ci cw _ _ cst _ < /proc/stat
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$now" "$utime" "$stime" "$rss" "${anon:-}" "$nthreads" "$avail" "$cu" "$cn" "$cs" "$ci" "$cw" "$cst" >> "$D/samples.tsv"
    if [ "$now" -ge "$NEXT_THREADS" ]; then
        threads >> "$D/threads.tsv"
        NEXT_THREADS=$(( now + 60 ))
    fi
    sleep "$INTERVAL"
done
finish
