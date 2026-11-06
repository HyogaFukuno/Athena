#!/bin/zsh
# usage: measure.sh TAG ROOMS JAR SGJAR [extra JVM]   one 8-room run on VPS-B, then fetch the measurements
cd /Users/glacier/Documents/Minecraft/developments/sandbox/Athena/.claude/worktrees/world-threads
TAG=$1; ROOMS=$2; JAR=$3; SG=$4; EXTRA=$5
H="${VPS_B_HOST:?set VPS_B_HOST}"
n=$(ssh -o BatchMode=yes $H 'ss -tn state established "( sport = :25566 )" | tail -n +2 | wc -l')
if [ "$n" != 0 ]; then echo "$TAG: $n connected, skipped"; exit 3; fi
G='-XX:+UseShenandoahGC -XX:+ExplicitGCInvokesConcurrent'
export SGBOTS_JVM="-Dathena.threadStats=/opt/mc/simbench/thread-stats.csv -Xlog:gc,safepoint:file=gc.log $EXTRA"
VPS_B_HOST=$H audit/2026-11-05/bots/sgbots_vps.sh $JAR $SG --map ${MAP:-all} --team-size 2 --rooms $ROOMS --matches 1 --live 600 --minutes 12 --heap 320M --gc "$G" --tag $TAG > run/perf/$TAG.txt 2>&1
scp -q $H:/opt/mc/simbench/thread-stats.csv run/sgbots-vps-out/$TAG/
scp -q $H:/opt/mc/simbench/rec.jfr run/sgbots-vps-out/$TAG/ 2>/dev/null
tail -9 run/perf/$TAG.txt | cut -c1-110
