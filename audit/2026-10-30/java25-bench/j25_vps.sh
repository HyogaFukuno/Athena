#!/usr/bin/env bash
# Whole SurvivalGames matches on sgbench3 (VPS-B), one per variant: Return_To_Moonbase_9, 20 bots
# spread over the map, 120 s live game, 144M heap, production athena.yml (io_uring, compression 1).
# Each variant is a jar and a start script; sgbench3/start-bench.sh runs the script named in
# start-variant with a cache root of its own (aot-cache), so the live servers' AOT key is untouched.
# Before a variant's first run the server is started twice (AOT record, then build). Tick durations
# come from AthenaDiagnostics (athena-diag.csv), GC from gc.log, the rest from sg_match_load.py.
#
#   j25_vps.sh <out dir> name:jar:script[:MB|LB] ...      e.g. base:base:base all:all:new:LB
# MB is Return_To_Moonbase_9 (the default), LB Lobby_Games, P72 Par_72_Revamped.
# J25_MATCHES=3 plays three matches in one JVM, as the live servers do (they never restart between
# matches). A script whose name holds "train" is not warmed: its first run records the AOT cache.
cd "$(dirname "$0")/../../../bench"
H=${J25_HOST:-root@vps-b}  # VPS-B (rebuilt 2026-10-30; the old box was old-vps-b)
D=/opt/mc/sgbench3
OUT="$1"; shift
mkdir -p "$OUT"
F='^WARNING|terminal features|OFFLINE|authenticate|hackers|online-mode|more ram|moved wrongly'
WARMED=" "
n=${J25_START:-0}
M=${J25_MATCHES:-1}
for v in "$@"; do
  IFS=: read -r name jar script map <<< "$v"
  case "$map" in
    LB) MAP_ARGS="--map Lobby_Games --centre 143,15 --roam 200" ;;
    P72) MAP_ARGS="--map Par_72_Revamped --centre 2063,-405 --roam 200" ;;
    MB|"") MAP_ARGS="--map Return_To_Moonbase_9 --centre 256,-153 --roam 200" ;;
  esac
  n=$((n + 1)); tag="j25_${n}_${name}${map:+_$map}"
  echo "=== $tag ($jar jar, start-small.$script.sh) $(date +%T)"
  ssh -o BatchMode=yes $H "install -o mc -g mc -m 644 /root/j25-$jar.jar $D/paperclip.jar; echo $D/start-small.$script.sh > $D/start-variant; chown mc:mc $D/start-variant"
  case "$WARMED$script" in *" $jar:$script "*|*train*) ;; *)
    ssh -o BatchMode=yes $H /root/j25-warm.sh
    WARMED="$WARMED$jar:$script " ;;
  esac
  ssh -o BatchMode=yes $H "rm -f $D/athena-diag.csv; touch $D/athena-diag.request; chown mc:mc $D/athena-diag.request"
  # per-thread CPU and the heap mapping's RSS, if the sampler is installed (see j25-threads.sh)
  ssh -o BatchMode=yes $H "[ -x /root/j25-threads.sh ] && { rm -f /root/$tag.threads; nohup /root/j25-threads.sh /root/$tag.threads > /dev/null 2>&1 & }; true"
  perl -e 'alarm shift; exec @ARGV' $((1500 * M)) python3 sg_match_load.py $H --dir $D --session sgbench3 --port 25570 --tunnel \
    $MAP_ARGS --spread --heap 144M --matches $M --live-seconds 120 \
    --tag $tag 2>&1 | grep -vE "$F" | grep -vE "^\[[0-9]|^$|^ +[0-9]+: |^ num"
  ssh -o BatchMode=yes $H "rm -f $D/athena-diag.request; sudo -u mc tmux has-session -t sgbench3 2>/dev/null && echo 'still running'; \
    cp $D/athena-diag.csv /root/$tag.diag.csv; cp $D/gc.log /root/$tag.gc.log; cp $D/console.out /root/$tag.console; \
    grep -E 'start-small\]|AOT|Using .* channel type|Preloaded' $D/console.out | head -5"
  scp -q $H:/root/$tag.diag.csv $H:/root/$tag.gc.log $H:/root/$tag.console "$OUT/"
  scp -q $H:/root/$tag.threads $H:/root/$tag.threads.series "$OUT/" 2>/dev/null
  cp out/$tag.json "$OUT/" 2>/dev/null
done
ssh -o BatchMode=yes $H "sudo -u mc tmux ls 2>&1; free -m | sed -n 2p"
echo "SERIES DONE $(date +%T)"
