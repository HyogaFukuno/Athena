#!/bin/zsh
# Runs sim_vps.py --sg on VPS-B with sg2 stopped, for each SPEC (tag:SG-jar in run/sgjar), on server 0456.
#   VPS_B_HOST=root@<VPS-B> LOBBY=<copy of sg2's lobby> SPECS="t1:sg.jar" audit/2026-11-22/run_vps.sh
# Before stopping sg2 and before each run, the box arms a 15-minute timer of its own that stops the bench and
# starts sg2 again, so a lost connection from this machine keeps sg2 down 15 minutes at most.
set -u
W=${0:A:h:h:h}
H=$VPS_B_HOST
cd $W/audit/2026-11-02/world-threads
guard() {
  ssh -o BatchMode=yes $H 'systemctl stop athena-bench-guard.timer athena-bench-guard.service 2>/dev/null; systemctl reset-failed athena-bench-guard.service athena-bench-guard.timer 2>/dev/null; systemd-run --unit=athena-bench-guard --on-active=15min /bin/sh -c "sudo -u mc tmux -L simbench send-keys -t simbench stop Enter; sleep 30; sudo -u mc tmux -L simbench kill-server; systemctl start sg@sg2"'
}
restore() {
  echo "== starting sg@sg2 again"
  ssh -o BatchMode=yes $H 'systemctl stop athena-bench-guard.timer 2>/dev/null; systemctl start sg@sg2; for i in $(seq 1 90); do ss -ltn | grep -q ":25566 " && break; sleep 2; done; systemctl is-active sg@sg2; ss -ltn | grep ":25566 "; free -m | sed -n 2p'
}
trap restore EXIT
n=$(ssh -o BatchMode=yes $H 'ss -tn state established "( sport = :25566 )" | tail -n +2 | wc -l')
if [ "$n" != 0 ]; then
  echo "== $n connected: notice, 30 s"
  ssh -o BatchMode=yes $H 'sudo -u mc tmux -L sg2 send-keys -t sg2 "say Restarting for maintenance in 30 seconds" Enter'
  sleep 30
fi
guard
ssh -o BatchMode=yes $H 'systemctl stop sg@sg2; systemctl is-active sg@sg2; free -m | sed -n 2p'
for spec in ${=SPECS}; do
  TAG=${spec%%:*}; SGJ=${spec#*:}
  echo "== $TAG $SGJ $(date +%T)"
  guard
  python3 sim_vps.py ../../../run/jars/s0456.jar $TAG --sg ../../../run/sgjar/$SGJ --conf sg2 --map Shady_Hollow,Shady_Hollow \
    --at-once 8 --rounds 1 --live 240 --heap 256M --gc "-XX:+UseShenandoahGC -XX:+ExplicitGCInvokesConcurrent" \
    --jit "-XX:TieredStopAtLevel=1 --sun-misc-unsafe-memory-access=allow" \
    --main-world $LOBBY --keep-spawn false > ../../../run/vps-$TAG.log 2>&1
  echo "exit $?"; tail -4 ../../../run/vps-$TAG.log | cut -c1-220
done
