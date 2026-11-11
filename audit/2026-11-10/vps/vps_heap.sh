#!/bin/zsh
# A/B on VPS-B: sg2 stopped once (with a notice if anyone is on), runs alternate, sg2 always started again.
cd /Users/glacier/Documents/Minecraft/developments/sandbox/Athena/.claude/worktrees/world-threads
S="${JARS:?set JARS to the folder with base.jar, new.jar and sg-ufix.jar}"
export VPS_B_HOST="${VPS_B_HOST:?set VPS_B_HOST}"
H=$VPS_B_HOST
restore() {
  echo "== starting sg@sg2 again"
  ssh -o BatchMode=yes $H 'systemctl start sg@sg2; for i in $(seq 1 90); do ss -ltn | grep -q ":25566 " && break; sleep 2; done; systemctl is-active sg@sg2; ss -ltn | grep ":25566 "; free -m | sed -n 2,3p'
}
trap restore EXIT
n=$(ssh -o BatchMode=yes $H 'ss -tn state established "( sport = :25566 )" | tail -n +2 | wc -l')
if [ "$n" != 0 ]; then
  echo "== $n connected: notice, 30 s"
  ssh -o BatchMode=yes $H 'sudo -u mc tmux -L sg2 send-keys -t sg2 "say Restarting for maintenance in 30 seconds" Enter'
  sleep 30
fi
ssh -o BatchMode=yes $H 'systemctl stop sg@sg2; systemctl is-active sg@sg2; free -m | sed -n 2p'
G='-XX:+UseShenandoahGC -XX:+ExplicitGCInvokesConcurrent'
export SGBOTS_JVM="-Dathena.threadStats=/opt/mc/simbench/thread-stats.csv -Xlog:gc,safepoint:file=gc.log -Dsgbots.gridstats=true"
mkdir -p run/perf
for TAG in h256-base h256-new h224-base h224-new h192-base h192-new; do
  L=${TAG#*-}; HP=${TAG%%-*}; HP=${HP#h}M
  echo "== $TAG $(date +%T)"
  python3 audit/2026-11-05/bots/sgbots.py $S/jars/$L.jar $S/jars/sg-ufix.jar --vps --map Shady_Hollow --team-size 2 --rooms 8 --matches 1 --live 600 --minutes 12 --heap $HP --gc "$G" --tag $TAG > run/perf/$TAG.txt 2>&1
  scp -q $H:/opt/mc/simbench/thread-stats.csv run/sgbots-vps-out/$TAG/; echo "heap $HP"
  tail -3 run/perf/$TAG.txt | cut -c1-120
done
