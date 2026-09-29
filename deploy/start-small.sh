#!/usr/bin/env bash
# Athena - start one small minigame server (a ~256 MB share of a box). Run it from the server
# directory, the one holding paperclip.jar:
#
#   cd /opt/mc/arena1 && /path/to/start-small.sh
#
# Measured for 20 players on the production maps with LuckPerms: 256 MB and -Xmx128M holds with
# ~35 MB to spare, at 0.13-0.14 of an M1 core. What it does, and why:
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
#   memory; maxOrder=7 makes Netty's buffer pool grow in 1 MB chunks instead of 4 MB; the code
#   cache is capped well above the ~29 MB it uses.
set -euo pipefail

JAVA="${JAVA:-java}"
HEAP="${HEAP:-128M}"
PAPERCLIP="paperclip.jar"
PATCHED="cache/patched_1.8.8.jar"
AOT="cache/athena.aot"
AOT_CONF="cache/athena.aotconf"
STAMP="cache/athena.stamp"
MAIN="org.bukkit.craftbukkit.Main"

FLAGS=(
    "-Xms$HEAP" "-Xmx$HEAP"
    -XX:+UseSerialGC
    -XX:+UseCompactObjectHeaders
    -XX:TrimNativeHeapInterval=5000
    -XX:ReservedCodeCacheSize=32m
    -Dio.netty.allocator.maxOrder=7
    -XX:+UnlockDiagnosticVMOptions
    -XX:ArchiveRelocationMode=0
)

export MALLOC_ARENA_MAX=2

[ -f "$PAPERCLIP" ] || { echo "no $PAPERCLIP here - run this from the server directory" >&2; exit 1; }
mkdir -p cache

# What the cache was built for: this paperclip.jar and this JDK.
want="$( (sha256sum "$PAPERCLIP" 2>/dev/null || shasum -a 256 "$PAPERCLIP") | cut -d' ' -f1) $("$JAVA" -version 2>&1 | grep -v 'Picked up' | head -1)"
have="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$want" != "$have" ] || [ ! -f "$PATCHED" ]; then
    echo "[start-small] new jar or JDK: patching, and dropping the old AOT cache"
    # patching holds the vanilla jar, the patch and the output at once: give it room
    "$JAVA" -Xmx512M -Dpaperclip.patchonly=true -jar "$PAPERCLIP"
    rm -f "$AOT" "$AOT_CONF"
    echo "$want" > "$STAMP"
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
