#!/usr/bin/env bash
# J06: start-small.sh's key cleanup when four servers start on four keys at once. Server A has chosen
# and touched key A under the lock but not opened its jar yet; B, C and D then touch theirs. An
# eight-day-old key E is also there. Runs the cleanup lines of the given start-small.sh and prints
# which keys survive.  Usage: j06_cleanup.sh <start-small.sh>
set -euo pipefail
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
for k in A B C D E; do mkdir "$ROOT/$k"; : > "$ROOT/$k/patched_1.8.8.jar"; done
now=$(date +%s)
stamp() { touch -t "$(date -r "$1" +%Y%m%d%H%M.%S 2>/dev/null || date -d "@$1" +%Y%m%d%H%M.%S)" "$2"; }
stamp $((now - 8 * 86400)) "$ROOT/E"
stamp $((now - 4)) "$ROOT/A"; stamp $((now - 3)) "$ROOT/B"; stamp $((now - 2)) "$ROOT/C"
DIR="$ROOT/D"
# the cleanup, from "touch $DIR" up to its closing "done"
sed -n '/^touch "\$DIR"$/,/done$/p' "$1" > "$ROOT/cleanup.sh"
( cd "$ROOT" && ROOT="$ROOT" DIR="$DIR" bash -euo pipefail -c "ROOT='$ROOT'; DIR='$DIR'; $(cat "$ROOT/cleanup.sh")" )
echo "kept: $(cd "$ROOT" && ls -d */ | tr -d / | tr '\n' ' ')"
for k in A E; do [ -d "$ROOT/$k" ] && echo "$k kept" || echo "$k deleted"; done
