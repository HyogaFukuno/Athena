#!/bin/bash
# runs on the game VPS: athena_sg_when_empty.sh <server> <port> <athena jar> <plugin jar> <tag>
# waits until the server has had 0 connections for two checks 30 s apart, then backs up and replaces
# paperclip.jar and plugins/SurvivalGames.jar together, starts it, and restarts it once more after
# 25 s (if still empty) so that start-small.sh builds the new jar's AOT cache. Gives up after 8 hours.
S=$1; P=$2; JAR=$3; PLUGIN=$4; TAG=$5; D=/opt/mc/$S
conns() { ss -tnH state established "( sport = :$P )" | wc -l; }
waitdone() { for i in $(seq 1 150); do grep -q "Done (" $D/console.out 2>/dev/null && return 0; sleep 2; done; return 1; }
end=$(( $(date +%s) + 8*3600 )); empty=0
while [ $(date +%s) -lt $end ]; do
  if [ "$(conns)" -eq 0 ]; then empty=$((empty+1)); else empty=0; fi
  if [ $empty -ge 2 ] && [ "$(conns)" -eq 0 ]; then
    echo "$(date '+%F %T') $S empty - swapping Athena and the plugin"
    mkdir -p /root/bak-before-$TAG/$S && cp -p $D/paperclip.jar $D/plugins/SurvivalGames.jar /root/bak-before-$TAG/$S/
    systemctl stop sg@$S
    install -o mc -g mc -m 644 $JAR $D/paperclip.jar
    install -o mc -g mc -m 644 $PLUGIN $D/plugins/SurvivalGames.jar
    systemctl start sg@$S; waitdone || { echo "$S did not start"; exit 1; }
    sleep 25
    if [ "$(conns)" -eq 0 ]; then
      systemctl stop sg@$S   # writes the AOT record of this jar
      systemctl start sg@$S; waitdone || { echo "$S did not restart"; exit 1; }
    else
      echo "$S has players again - AOT cache left for the next restart"
    fi
    grep -E "start-small" $D/console.out
    echo "$(date '+%F %T') $S $(systemctl is-active sg@$S) athena $(sha256sum $D/paperclip.jar | cut -c1-12) plugin $(sha256sum $D/plugins/SurvivalGames.jar | cut -c1-12) errors $(grep -cE 'ERROR|Exception' $D/console.out)"
    grep -hE "SurvivalGames\] Loaded" $D/console.out | head -1
    exit 0
  fi
  sleep 30
done
echo "$(date '+%F %T') gave up: $S never empty for 8 hours"; exit 3
