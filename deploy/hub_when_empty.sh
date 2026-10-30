#!/bin/bash
# runs on the proxy VPS: hub_when_empty.sh <athena jar> <tag> [hub plugin jar]
# waits until the hub (127.0.0.1:25601, reached only through Velocity) has had 0 connections for two
# checks 30 s apart, then backs up and replaces paperclip.jar, starts it, and restarts it once more
# after 25 s (if still empty) so that start-small.sh builds the new jar's AOT cache. Gives up after 8 hours.
JAR=$1; TAG=$2; PLUGIN=$3; D=/opt/mc/hub; P=25601
conns() { ss -tnH state established "( sport = :$P )" | wc -l; }
waitdone() { for i in $(seq 1 150); do grep -q "Done (" $D/console.out 2>/dev/null && return 0; sleep 2; done; return 1; }
end=$(( $(date +%s) + 8*3600 )); empty=0
while [ $(date +%s) -lt $end ]; do
  if [ "$(conns)" -eq 0 ]; then empty=$((empty+1)); else empty=0; fi
  if [ $empty -ge 2 ] && [ "$(conns)" -eq 0 ]; then
    echo "$(date '+%F %T') hub empty - swapping Athena"
    mkdir -p /root/bak-before-$TAG/hub && cp -p $D/paperclip.jar $D/plugins/Athena.jar /root/bak-before-$TAG/hub/
    systemctl stop hub
    install -o mc -g mc -m 644 $JAR $D/paperclip.jar
    [ -n "$PLUGIN" ] && install -o mc -g mc -m 644 $PLUGIN $D/plugins/Athena.jar
    systemctl start hub; waitdone || { echo "hub did not start"; exit 1; }
    sleep 25
    if [ "$(conns)" -eq 0 ]; then
      systemctl stop hub   # writes the AOT record of this jar
      systemctl start hub; waitdone || { echo "hub did not restart"; exit 1; }
    else
      echo "hub has players again - AOT cache left for the next restart"
    fi
    grep -E "start-small" $D/console.out
    echo "$(date '+%F %T') hub $(systemctl is-active hub) athena $(sha256sum $D/paperclip.jar | cut -c1-12) plugin $(sha256sum $D/plugins/Athena.jar | cut -c1-12) errors $(grep -cE 'ERROR|Exception' $D/console.out)"
    exit 0
  fi
  sleep 30
done
echo "$(date '+%F %T') gave up: hub never empty for 8 hours"; exit 3
