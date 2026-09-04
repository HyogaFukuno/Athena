#!/usr/bin/env bash
# Athena - first-run setup for an Oracle Cloud A1 (Ampere, aarch64) instance running Ubuntu 24.04.
#
# Run once as the default user (`ubuntu`) on a fresh instance:
#   bash setup-oracle-a1.sh
#
# It installs a JDK, opens the Minecraft port on the instance's own firewall, and lays out
# /opt/mc/<name> for one or more servers. It does NOT touch the OCI Security List - that is a
# console setting and has to be done separately. Both layers must allow the port.
set -euo pipefail

JDK_URL="https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.4.1%2B1/OpenJDK25U-jdk_aarch64_linux_hotspot_25.0.4.1_1.tar.gz"
JDK_SHA="69df11a02cfa3ef7d7ca645e03edce6778ec090e100f6ae2b42097865730ac52"
JDK_DIR="/opt/jdk-25"
PORTS="${PORTS:-25565}"

[ "$(uname -m)" = "aarch64" ] || { echo "this script is for the Arm A1 shape; uname -m says $(uname -m)" >&2; exit 1; }

echo "== packages =="
sudo apt-get update -qq
sudo apt-get install -y -qq curl tar screen

echo "== JDK 25 (aarch64) =="
if [ ! -x "$JDK_DIR/bin/java" ]; then
    tmp=$(mktemp -d)
    curl -fsSL "$JDK_URL" -o "$tmp/jdk.tar.gz"
    echo "$JDK_SHA  $tmp/jdk.tar.gz" | sha256sum -c -
    sudo mkdir -p "$JDK_DIR"
    sudo tar -xzf "$tmp/jdk.tar.gz" -C "$JDK_DIR" --strip-components=1
    rm -rf "$tmp"
fi
"$JDK_DIR/bin/java" -version

echo "== instance firewall =="
# OCI's Ubuntu images ship iptables rules that allow only SSH. Opening the port in the console's
# Security List is not enough on its own - this is the layer people forget.
for p in $PORTS; do
    if ! sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null; then
        # insert before the catch-all REJECT that the OCI image installs
        sudo iptables -I INPUT 6 -p tcp --dport "$p" -m state --state NEW,ESTABLISHED -j ACCEPT
    fi
done
sudo apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null
sudo iptables -L INPUT -n --line-numbers | head -12

echo "== layout =="
sudo mkdir -p /opt/mc
sudo chown "$USER:$USER" /opt/mc
echo "servers go in /opt/mc/<name>/ - put paperclip.jar there"

cat <<'NOTE'

Still to do, in this order:
  1. OCI console: VCN -> Security List -> add an ingress rule, TCP, source 0.0.0.0/0, dest port 25565
  2. copy paperclip.jar into /opt/mc/<name>/ and run it once to accept the EULA
  3. install the systemd unit (mc@.service) and `sudo systemctl enable --now mc@<name>`
NOTE
