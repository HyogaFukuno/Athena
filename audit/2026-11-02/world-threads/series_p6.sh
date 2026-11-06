#!/usr/bin/env bash
# phase 6 bench: SG-like matches (MatchSim + sgbot) in a 2-CPU container, the base jar against the branch.
# series_p6.sh <base jar> <branch jar> [tag prefix]
set -u
here="$(cd "$(dirname "$0")" && pwd -P)"
base="$1"; wt="$2"; pre="${3:-p6}"
run() { echo "=== $*"; python3 "$here/sim_local.py" "$@" 2>&1 | grep -v "^\s*$"; echo; }
[ -z "${SKIP_ONE:-}" ] && run "$base" "$pre-base-1"     --at-once 1 --heap 128M
[ -z "${SKIP_ONE:-}" ] && run "$wt"   "$pre-wt-1"       --at-once 1 --heap 128M
run "$wt"   "$pre-wt-2t"      --at-once 2 --heap 256M --threaded --freeze 200:1:5000
run "$wt"   "$pre-wt-3t"      --at-once 3 --heap 384M --threaded --freeze 200:1:5000
run "$wt"   "$pre-wt-3"       --at-once 3 --heap 384M
run "$wt"   "$pre-wt-3t-race" --at-once 3 --heap 384M --threaded --thread-check --rounds 6 --live 120 --deathmatch 60 \
    --freeze 150:1:4000 --freeze 400:lobby:4000 --freeze 700:2:12000 --freeze 1000:3:3000
