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
# - The cache belongs to one jar and one JDK. When either changes this script re-patches and
#   throws the cache away; the next run records a new one and the run after uses it. A cache that
#   does not fit is ignored by the JVM with a warning, never fatal.
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
# - The AOT cache is also tied to these flags: changing one (HEAP, the GC) drops the cache, and it
#   is recorded and rebuilt as after a new jar.
set -euo pipefail

JAVA="${JAVA:-java}"
HEAP="${HEAP:-144M}"
PAPERCLIP="paperclip.jar"
PATCHED="cache/patched_1.8.8.jar"
AOT="cache/athena.aot"
AOT_CONF="cache/athena.aotconf"
STAMP="cache/athena.stamp"
FLAGS_STAMP="cache/athena.flags"
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
mkdir -p cache

# What the cache was built for: this paperclip.jar and this JDK. The version is read with the
# JVM option variables unset: an option there that prints at startup (a JFR recording, say) would
# otherwise look like another JDK, drop the cache and make the run a recording one.
want="$( (sha256sum "$PAPERCLIP" 2>/dev/null || shasum -a 256 "$PAPERCLIP") | cut -d' ' -f1) $(env -u JAVA_TOOL_OPTIONS -u JDK_JAVA_OPTIONS "$JAVA" -version 2>&1 | head -1)"
have="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$want" != "$have" ] || [ ! -f "$PATCHED" ]; then
    echo "[start-small] new jar or JDK: patching, and dropping the old AOT cache"
    # patching holds the vanilla jar, the patch and the output at once: give it room
    "$JAVA" -Xmx512M -Dpaperclip.patchonly=true -jar "$PAPERCLIP"
    rm -f "$AOT" "$AOT_CONF"
    echo "$want" > "$STAMP"
fi

# What else the cache was built for: these flags (a cache made under another GC or heap size is
# rejected or only partly used)
if [ "${FLAGS[*]}" != "$(cat "$FLAGS_STAMP" 2>/dev/null || true)" ]; then
    if [ -f "$AOT" ] || [ -f "$AOT_CONF" ]; then
        echo "[start-small] JVM flags changed: dropping the old AOT cache"
    fi
    rm -f "$AOT" "$AOT_CONF"
    echo "${FLAGS[*]}" > "$FLAGS_STAMP"
fi

if [ ! -f "$AOT" ] && [ -f "$AOT_CONF" ]; then
    echo "[start-small] building the AOT cache from the last run's record"
    if ! "$JAVA" "${FLAGS[@]}" -XX:AOTMode=create -XX:AOTConfiguration="$AOT_CONF" -XX:AOTCache="$AOT" -cp "$PATCHED"; then
        echo "[start-small] building the AOT cache failed; recording again this run" >&2
        rm -f "$AOT" "$AOT_CONF"
    fi
fi

if [ -f "$AOT" ]; then
    AOT_FLAGS=(-XX:AOTCache="$AOT")
else
    # this run is the training run: play normally, and stop the server with "stop" so the record
    # is written; the next start builds the cache from it
    echo "[start-small] no AOT cache yet: recording this run"
    AOT_FLAGS=(-XX:AOTMode=record -XX:AOTConfiguration="$AOT_CONF")
fi

exec "$JAVA" "${FLAGS[@]}" "${AOT_FLAGS[@]}" -cp "$PATCHED" "$MAIN" nogui "$@"
