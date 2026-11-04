#!/usr/bin/env bash
# Athena - the lamp bench on VPS-B: stops the live SurvivalGames servers, runs lampbench.py --vps for each
# setting, and always starts them again, then checks they are up and listening.
#
#   VPS_B_HOST=root@<VPS-B address> ./lampbench_vps.sh JAR
set -u
H="${VPS_B_HOST:?set VPS_B_HOST to root@<VPS-B address>}"
JAR="$1"
HERE="$(cd "$(dirname "$0")" && pwd -P)"

restore() {
    echo "== starting sg@sg1 sg@sg2 again"
    ssh -o BatchMode=yes "$H" 'systemctl start sg@sg1 sg@sg2; for i in $(seq 1 90); do
        if ss -ltn | grep -q ":25565 " && ss -ltn | grep -q ":25566 "; then break; fi; sleep 2; done
        systemctl is-active sg@sg1 sg@sg2; ss -ltn | grep -E ":2556[56] "; ls /opt/mc/shared-cache'
}
trap restore EXIT

echo "== stopping sg@sg1 sg@sg2"
ssh -o BatchMode=yes "$H" 'systemctl stop sg@sg1 sg@sg2; systemctl is-active sg@sg1 sg@sg2; free -m | sed -n 2p' || true

for cfg in "v0:0:false" "v4s:4:true" "v0s:0:true" "v1s:1:true"; do
    IFS=: read -r tag lamps stagger <<< "$cfg"
    echo "== $tag (redstoneLampsPerTick $lamps, staggerDaylightDetectors $stagger)"
    python3 "$HERE/lampbench.py" "$JAR" "$tag" --lamps "$lamps" --stagger "$stagger" --vps --heap 128M || echo "!! $tag failed"
done
