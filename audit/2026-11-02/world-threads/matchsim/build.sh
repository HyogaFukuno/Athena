#!/usr/bin/env bash
# builds matchsim.jar against this checkout's PandaSpigot-API: build.sh <out jar>
set -e
here="$(cd "$(dirname "$0")" && pwd -P)"
root="$(cd "$here/../../../.." && pwd -P)"
api="$(ls "$root"/PandaSpigot-API/build/libs/athena-api-*-SNAPSHOT.jar | grep -v sources | grep -v javadoc | head -1)"
libs="$(find ~/.gradle/caches/modules-2/files-2.1 -name '*.jar' \( -path '*guava*' -o -path '*bungeecord-chat*' -o -path '*adventure-api*' -o -path '*adventure-key*' -o -path '*examination*' -o -path '*jsr305*' \) | tr '\n' ':')"
tmp="$(mktemp -d)"
javac -nowarn --release 21 -cp "$api:$libs" -d "$tmp" "$here"/src/athena/matchsim/*.java
cp "$here/plugin.yml" "$tmp/"
jar cf "$1" -C "$tmp" .
rm -rf "$tmp"
