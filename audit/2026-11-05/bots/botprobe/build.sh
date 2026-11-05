#!/usr/bin/env bash
# builds botprobe.jar against this checkout's PandaSpigot-API: build.sh <out jar>
# SurvivalGames' Navigator and Terrain are compiled in too (renamed), for the climbing check.
set -e
here="$(cd "$(dirname "$0")" && pwd -P)"
root="$(cd "$here/../../../.." && pwd -P)"
sg="${SG_DIR:-/Users/glacier/Documents/Minecraft/developments/SurvivalGames-bots}/src/main/java/net/arcana/survivalgames"
api="$(ls "$root"/PandaSpigot-API/build/libs/athena-api-*-SNAPSHOT.jar | grep -v sources | grep -v javadoc | head -1)"
libs="$(find ~/.gradle/caches/modules-2/files-2.1 -name '*.jar' \( -path '*guava*' -o -path '*bungeecord-chat*' -o -path '*adventure-api*' -o -path '*adventure-key*' -o -path '*examination*' -o -path '*jsr305*' -o -path '*fastutil*' \) | tr '\n' ':')"
tmp="$(mktemp -d)"
gen="$(mktemp -d)"
for f in bot/Navigator.java bot/Terrain.java domain/LongKeys.java; do
    sed -e 's/^package .*/package athena.botprobe.nav;/' -e '/^import org.jspecify/d' -e 's/@Nullable //g' \
        -e '/^import net.arcana.survivalgames.domain.LongKeys;/d' -e 's/net\.arcana\.survivalgames\.domain\.LongKeys/LongKeys/g' "$sg/$f" > "$gen/$(basename "$f")"
done
sed -i '' -e 's/^final class LongKeys/public final class LongKeys/' "$gen/LongKeys.java" || true
javac -nowarn --release 25 -cp "$api:$libs" -d "$tmp" "$here"/src/athena/botprobe/*.java "$here"/src/athena/botprobe/nav/*.java "$gen"/*.java
cp "$here/plugin.yml" "$tmp/"
jar cf "$1" -C "$tmp" .
rm -rf "$tmp" "$gen"
