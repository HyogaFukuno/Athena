#!/usr/bin/env bash
# Athena - start one small minigame server (a ~320 MB share of a box). Run it from the server
# directory, the one holding paperclip.jar:
#
#   cd /opt/mc/arena1 && /path/to/start-small.sh
#
# Measured on a ConoHa 1 GB / 2 vCPU VPS with 20 bots through whole SurvivalGames matches: the live
# heap is ~40 MB plus ~11 KB per loaded chunk. 144M assumes the plugin pins the play area and
# preloads it in pre-game (Athena.pinChunks / preloadChunks): the map then sits in the heap from the
# start (~1,200 chunks, live heap up to ~100 MB) and the live game reads no chunk. That way 144M ran
# 15-minute matches on Par_72 and Lobby_Games with no full GC outside pre-game; 128M had three on
# Par_72 (120-165 ms). Without the pin, chunks unloaded and reloaded fill the old generation and
# 160M ran a full GC every 3-5 minutes. RSS ~305 MB, 0.13 vCPU in the live game. What it does, and why:
#
# - The server runs from the patched jar directly instead of through paperclip. Paperclip adds the
#   jar to the class path at run time, which keeps the server's classes out of the AOT cache.
# - The AOT cache holds the server's classes already loaded and linked, mapped from a file instead
#   of built in native memory: about 35 MB less per server, and less JIT work after a start.
#   ArchiveRelocationMode=0 maps it without rewriting pointers; with the default, the pages it
#   rewrote stayed private memory and the saving mostly vanished.
# - The patched jar and the AOT cache are shared by every server on the box that runs the same
#   paperclip.jar with the same JDK and flags. They live in $ATHENA_CACHE_ROOT/<key>/ (default
#   /opt/mc/shared-cache when it exists, else ./cache), where the key is a hash of those three
#   things, and every server puts the same absolute path on its class path - the AOT cache is
#   only accepted for the class path it was made with. The servers then map the same files, so
#   the page cache holds one copy of them for the whole box instead of one per server.
# - A key's directory is never rewritten in place: the jar and the cache are made under a
#   temporary name and renamed into it, so a server still running from them keeps its mapping.
#   A new jar, JDK or flag set is simply another key. Patching and building the cache take a lock
#   (flock), so two servers starting together do the work once; the second waits and reuses it.
# - The cache belongs to one jar, one JDK and one flag set. The first run under a new key records
#   (AOTMode=record), each server into its own record-<server>.aotconf; the next server to start
#   builds the shared cache from the newest record, and every start after that uses it. Stop the
#   recording run with "stop" so its record is written. A cache that does not fit is ignored by the
#   JVM with a warning, never fatal.
# - Recording and creating are separate steps (AOTMode=record, then AOTMode=create before the
#   next start). The one-step AOTCacheOutput starts a second JVM at shutdown to build the cache,
#   which on a small box is two JVMs' worth of memory at once.
# - MALLOC_ARENA_MAX=2 and TrimNativeHeapInterval keep glibc from holding ~60 MB of freed native
#   memory; maxOrder=7 makes Netty's buffer pool grow in 1 MB chunks instead of 4 MB.
# - SweeperThreshold=50 and a 48 MB code cache cap: new JIT code worth SweeperThreshold% of the cap
#   triggers a full GC ("CodeCache GC Threshold"). At the defaults (15% of 32 MB, ~5 MB) that was
#   5-6 full GCs of 50-200 ms in a match's first two minutes; at 50% of 32 MB one ~190 ms one was
#   left in a 15-minute match; at 50% of 48 MB none. The cap is only reserved: the cache commits
#   what it uses (~21 MB), so it costs no memory.
# - ParallelGC rather than Serial: the same heap and CPU, but a full GC runs on both vCPUs and
#   pauses 77-107 ms where Serial paused 125-185 ms (2-4 ticks, which players see as a hitch).
set -euo pipefail

JAVA="${JAVA:-java}"
HEAP="${HEAP:-144M}"
PAPERCLIP="paperclip.jar"
MAIN="org.bukkit.craftbukkit.Main"

FLAGS=(
    "-Xms$HEAP" "-Xmx$HEAP"
    -XX:+UseParallelGC
    -XX:+UseCompactObjectHeaders
    -XX:TrimNativeHeapInterval=5000
    -XX:ReservedCodeCacheSize=48m
    -XX:SweeperThreshold=50
    -Dio.netty.allocator.maxOrder=7
    -XX:+UnlockDiagnosticVMOptions
    -XX:ArchiveRelocationMode=0
)

export MALLOC_ARENA_MAX=2

[ -f "$PAPERCLIP" ] || { echo "no $PAPERCLIP here - run this from the server directory" >&2; exit 1; }

if [ -z "${ATHENA_CACHE_ROOT:-}" ]; then
    if [ -d /opt/mc/shared-cache ]; then ATHENA_CACHE_ROOT=/opt/mc/shared-cache; else ATHENA_CACHE_ROOT=cache; fi
fi
mkdir -p "$ATHENA_CACHE_ROOT"
ROOT="$(cd "$ATHENA_CACHE_ROOT" && pwd)"
SERVER="$(basename "$PWD")"

sha() { (sha256sum 2>/dev/null || shasum -a 256) | cut -d' ' -f1; }

# What the jar and cache are built for: this paperclip.jar, this JDK and these flags. The version is
# read with the JVM option variables unset: an option there that prints at startup (a JFR recording,
# say) would otherwise look like another JDK.
JDK="$(env -u JAVA_TOOL_OPTIONS -u JDK_JAVA_OPTIONS "$JAVA" -version 2>&1 | head -1)"
KEY="$( { sha < "$PAPERCLIP"; echo "$JDK"; echo "${FLAGS[*]}"; } | sha | cut -c1-16)"
DIR="$ROOT/$KEY"
PATCHED="$DIR/patched_1.8.8.jar"
AOT="$DIR/athena.aot"
mkdir -p "$DIR"

# Patching and building the cache happen under the lock; running does not
exec 9>"$ROOT/.lock"
command -v flock >/dev/null && flock 9

if [ ! -f "$PATCHED" ]; then
    echo "[start-small] new jar, JDK or flags ($KEY): patching"
    SRC="$PWD/$PAPERCLIP"
    WORK="$(mktemp -d "$ROOT/.patch.XXXXXX")"
    mkdir -p "$WORK/cache"
    # the vanilla jar paperclip patches from; kept once per box so it is not downloaded again
    [ -f "$ROOT/mojang_1.8.8.jar" ] && cp "$ROOT/mojang_1.8.8.jar" "$WORK/cache/"
    # patching holds the vanilla jar, the patch and the output at once: give it room
    (cd "$WORK" && "$JAVA" -Xmx512M -Dpaperclip.patchonly=true -jar "$SRC")
    [ -f "$ROOT/mojang_1.8.8.jar" ] || cp "$WORK/cache/mojang_1.8.8.jar" "$ROOT/" 2>/dev/null || true
    mv "$WORK/cache/patched_1.8.8.jar" "$PATCHED"
    rm -rf "$WORK"
fi

if [ ! -f "$AOT" ]; then
    RECORD="$(ls -t "$DIR"/record-*.aotconf 2>/dev/null | head -1 || true)"
    if [ -n "$RECORD" ] && [ -s "$RECORD" ]; then
        echo "[start-small] building the shared AOT cache from $(basename "$RECORD")"
        if "$JAVA" "${FLAGS[@]}" -XX:AOTMode=create -XX:AOTConfiguration="$RECORD" -XX:AOTCache="$AOT.tmp.$$" -cp "$PATCHED"; then
            mv "$AOT.tmp.$$" "$AOT"
        else
            echo "[start-small] building the AOT cache failed; recording again this run" >&2
            rm -f "$AOT.tmp.$$" "$RECORD"
        fi
    fi
fi

# Keep this key and the two most recently used others; a server still running from an older one
# keeps its open files either way
touch "$DIR"
ls -1dt "$ROOT"/*/ 2>/dev/null | tail -n +4 | while read -r old; do rm -rf "$old"; done

command -v flock >/dev/null && flock -u 9
exec 9>&-

if [ -f "$AOT" ]; then
    AOT_FLAGS=(-XX:AOTCache="$AOT")
else
    # this run is a training run: play normally, and stop the server with "stop" so the record is
    # written; the next start (of any server sharing the key) builds the cache from it
    echo "[start-small] no AOT cache yet ($KEY): recording this run"
    AOT_FLAGS=(-XX:AOTMode=record -XX:AOTConfiguration="$DIR/record-$SERVER.aotconf")
fi

exec "$JAVA" "${FLAGS[@]}" "${AOT_FLAGS[@]}" -cp "$PATCHED" "$MAIN" nogui "$@"
