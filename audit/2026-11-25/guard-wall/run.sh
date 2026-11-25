#!/bin/zsh
# Guard's combat checks, server 0459 (the wall check) against 0457, on VPS-B: the real SurvivalGames
# (sg2's jar and ArcanaServers main's configs), one room of network bots (sgbot.py --turn-hit: every hit
# is made from a look 150 degrees away, the look toward the target sent right after the attack, as an
# honest client sends it). Guard enforces its combat checks only (the bots fly) and kicks no one.
# sg2 is stopped once (with a notice if anyone is on) and always started again.
#   run.sh ARCANA_MAIN_CHECKOUT TAG:JAR ...
cd /Users/glacier/Documents/Minecraft/developments/sandbox/Athena/.claude/worktrees/world-threads/audit/2026-11-02/world-threads
export VPS_B_HOST="${VPS_B_HOST:?set VPS_B_HOST}"
H=$VPS_B_HOST
CONF_ROOT=$1; shift
restore() {
  echo "== starting sg@sg2 again"
  ssh -o BatchMode=yes $H 'systemctl start sg@sg2; for i in $(seq 1 90); do ss -ltn | grep -q ":25566 " && break; sleep 2; done; systemctl is-active sg@sg2; ss -ltn | grep ":25566 "; free -m | sed -n 2p'
}
trap restore EXIT
n=$(ssh -o BatchMode=yes $H 'ss -tn state established "( sport = :25566 )" | tail -n +2 | wc -l')
if [ "$n" != 0 ]; then
  echo "== $n connected: notice, 30 s"
fi
ssh -o BatchMode=yes $H 'sudo -u mc tmux -L sg2 send-keys -t sg2 "say 30秒後にサーバーを再起動します（試験のため、約10分止まります）" Enter'
sleep 30
ssh -o BatchMode=yes $H 'systemctl stop sg@sg2; systemctl is-active sg@sg2; free -m | sed -n 2p'
for spec in "$@"; do
  TAG=${spec%%:*}; JAR=${spec#*:}
  echo "== $TAG $JAR $(date +%T)"
  python3 sim_vps.py ../../../run/jars/$JAR $TAG --sg ../../../run/sgjar/sg2-v2-3257ec1.jar --conf sg2 --conf-root $CONF_ROOT \
    --map Shady_Hollow,Shady_Hollow --at-once 1 --per-match 12 --rounds 1 --live 150 --deathmatch 90 \
    --heap 256M --gc "-XX:+UseShenandoahGC -XX:+ExplicitGCInvokesConcurrent" \
    --guard-combat --bot-args=--turn-hit > ../../../run/vps-$TAG.log 2>&1
  echo "exit $?"; tail -4 ../../../run/vps-$TAG.log | cut -c1-200
done
