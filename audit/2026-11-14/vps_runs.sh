#!/bin/zsh
# 2026-11-14 fixes on VPS-B (copied from 2026-11-11/c-net): the real SurvivalGames (sg2's jar and configs) with network bots (sgbot.py through an
# ssh tunnel), Shady_Hollow only (named twice so that the bots find its centre themselves). sg2 is stopped once (with a notice if anyone is on) and always started again.
#   vps_runs.sh TAG:JAR[:inline][:jfr] ...   (always with --sun-misc-unsafe-memory-access=allow, as sg2 runs;
#   inline: C1's inlining limits raised, P2-4)
cd /Users/glacier/Documents/Minecraft/developments/sandbox/Athena/.claude/worktrees/world-threads/audit/2026-11-02/world-threads
export VPS_B_HOST="${VPS_B_HOST:?set VPS_B_HOST}"
H=$VPS_B_HOST
ROOMS=${ROOMS:-8}
restore() {
  echo "== starting sg@sg2 again"
  ssh -o BatchMode=yes $H 'systemctl start sg@sg2; for i in $(seq 1 90); do ss -ltn | grep -q ":25566 " && break; sleep 2; done; systemctl is-active sg@sg2; ss -ltn | grep ":25566 "; free -m | sed -n 2p'
}
trap restore EXIT
n=$(ssh -o BatchMode=yes $H 'ss -tn state established "( sport = :25566 )" | tail -n +2 | wc -l')
if [ "$n" != 0 ]; then
  echo "== $n connected: notice, 30 s"
  ssh -o BatchMode=yes $H 'sudo -u mc tmux -L sg2 send-keys -t sg2 "say Restarting for maintenance in 30 seconds" Enter'
  sleep 30
fi
ssh -o BatchMode=yes $H 'systemctl stop sg@sg2; systemctl is-active sg@sg2; free -m | sed -n 2p'
for spec in "$@"; do
  TAG=${spec%%:*}; rest=${spec#*:}; JAR=${rest%%:*}; J=(--jit "-XX:TieredStopAtLevel=1 --sun-misc-unsafe-memory-access=allow")  # as production: Unsafe allowed
  [[ $rest == *:inline* ]] && J=(--jit "-XX:TieredStopAtLevel=1 -XX:C1MaxInlineSize=70 -XX:C1MaxInlineLevel=15 --sun-misc-unsafe-memory-access=allow")  # P2-4
  [[ $rest == *:noatk* ]] && J=(--jit "-XX:TieredStopAtLevel=1 -Dathena.immediateAttacks=false --sun-misc-unsafe-memory-access=allow")
  [[ $rest == *:nodec* ]] && J=(--jit "-XX:TieredStopAtLevel=1 -Dathena.disableDirectDecode=true --sun-misc-unsafe-memory-access=allow")
  [[ $rest == *:jfr* ]] && J+=(--jfr)
  echo "== $TAG $JAR $J $(date +%T)"
  python3 sim_vps.py ../../../run/jars/$JAR $TAG --sg ../../../run/sgjar/sg2-live.jar --conf sg2 --map Shady_Hollow,Shady_Hollow \
    --at-once $ROOMS --rounds 1 --live 240 --heap 256M --gc "-XX:+UseShenandoahGC -XX:+ExplicitGCInvokesConcurrent" $J \
    > ../../../run/vps-$TAG.log 2>&1
  echo "exit $?"; tail -4 ../../../run/vps-$TAG.log | cut -c1-200
done
