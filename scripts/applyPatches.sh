#!/usr/bin/env bash

(
PS1="$"
basedir="$(cd "$1" && pwd -P)"
workdir="$basedir/base"
# git am and fetch kick off auto gc/maintenance in the background; the next
# applyPatch clones the repo just patched, and a repack under that clone
# deletes objects out from under it
gitcmd="git -c commit.gpgsign=false -c gc.auto=0 -c maintenance.auto=false"
applycmd="$gitcmd am --3way --ignore-whitespace"
# Windows detection to workaround ARG_MAX limitation
windows="$([[ "$OSTYPE" == "cygwin" || "$OSTYPE" == "msys" ]] && echo "true" || echo "false")"

function applyPatch {
    what=$1
    what_name=$(basename "$what")
    target=$2
    branch=$3
    patch_folder=$4

    cd "$basedir/$what" || return 1
    $gitcmd fetch
    $gitcmd branch -f upstream "$branch" >/dev/null || return 1

    cd "$basedir"
    if [ ! -d "$basedir/$target" ]; then
        if ! $gitcmd clone "$what" "$target"; then
            echo "  Failed to clone $what into $target"
            rm -rf "$basedir/$target"
            return 1
        fi
    fi
    # without this a failed cd leaves the commands below resetting the parent repo
    cd "$basedir/$target" || return 1

    echo "Resetting $target to $what_name..."
    $gitcmd remote rm origin > /dev/null 2>&1
    $gitcmd remote rm upstream > /dev/null 2>&1
    $gitcmd remote add upstream "$basedir/$what" >/dev/null 2>&1
    $gitcmd checkout master 2>/dev/null || $gitcmd checkout -b master
    $gitcmd fetch upstream >/dev/null 2>&1
    $gitcmd reset --hard upstream/upstream || return 1

    echo "  Applying patches to $target..."

    statusfile=".git/patch-apply-failed"
    rm -f "$statusfile"
    git config commit.gpgsign false
    $gitcmd am --abort >/dev/null 2>&1

    # Special case Windows handling because of ARG_MAX constraint
    if [[ $windows == "true" ]]; then
        echo "  Using workaround for Windows ARG_MAX constraint"
        find "$basedir/$patch_folder/"*.patch -print0 | xargs -0 $applycmd
    else
        $applycmd "$basedir/$patch_folder/"*.patch
    fi

    if [ "$?" != "0" ]; then
        echo 1 > "$statusfile"
        echo "  Something did not apply cleanly to $target."
        echo "  Please review above details and finish the apply then"
        echo "  save the changes with rebuildPatches.sh"

        # On Windows, finishing the patch apply will only fix the latest patch
        # users will need to rebuild from that point and then re-run the patch
        # process to continue
        if [[ $windows == "true" ]]; then
            echo ""
            echo "  Because you're on Windows you'll need to finish the AM,"
            echo "  rebuild all patches, and then re-run the patch apply again."
            echo "  Consider using the scripts with Windows Subsystem for Linux."
        fi

        exit 1
    else
        rm -f "$statusfile"
        echo "  Patches applied cleanly to $target"
    fi
}

if [ "$2" == "--setup" ]; then
    echo "Rebuilding Forked projects.... "

    # Move into Paper dir
    cd "$workdir/Paper"
    basedir=$(pwd)

    # Apply Spigot
    (
        applyPatch Bukkit Spigot-API HEAD Bukkit-Patches &&
        applyPatch CraftBukkit Spigot-Server patched CraftBukkit-Patches
    ) || (
        echo "Failed to apply Spigot Patches"
        exit 1
    ) || exit 1

    # Apply Paper
    (
        applyPatch Spigot-API PaperSpigot-API HEAD Spigot-API-Patches &&
        applyPatch Spigot-Server PaperSpigot-Server HEAD Spigot-Server-Patches
    ) || (
        echo "Failed to apply Paper Patches"
        exit 1
    ) || exit 1

    # Move out of Paper
    basedir="$1"
    cd "$basedir"

    echo "Importing MC Dev"

    ./scripts/importmcdev.sh "$basedir" || exit 1
fi

if [ ! -d "base/Paper/PaperSpigot-Server" ]; then
    echo "Upstream directory does not exist. Did you forget to run 'panda setup'?"
    exit 1
else
    # Apply PandaSpigot
    (
        applyPatch "base/Paper/PaperSpigot-API" PandaSpigot-API HEAD patches/api &&
        applyPatch "base/Paper/PaperSpigot-Server" PandaSpigot-Server HEAD patches/server
        cd "$basedir"
    ) || (
        echo "Failed to apply PandaSpigot Patches"
        exit 1
    ) || exit 1
fi
) || exit 1
