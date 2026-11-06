#!/usr/bin/env bash
# Athena - bots on VPS-B: stops the live SurvivalGames servers, runs sgbots.py --vps with the
# arguments given, and always starts them again, then checks they are up and listening.
#
#   VPS_B_HOST=root@<VPS-B address> ./sgbots_vps.sh ATHENA_JAR SG_JAR [sgbots.py arguments...]
set -u
H="${VPS_B_HOST:?set VPS_B_HOST to root@<VPS-B address>}"
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
python3 "$HERE/sgbots.py" "$@" --vps || echo "!! sgbots.py failed"
