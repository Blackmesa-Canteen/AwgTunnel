#!/usr/bin/env bash
# Shared helpers for the maintainer scripts. Not used by the Flatpak build:
# nothing in here runs on Flathub's builders, which only ever see the manifest.
#
# shellcheck shell=bash

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO_ROOT/io.github.blackmesa_canteen.AwgTunnel.yml"
ENGINE_URL="https://github.com/artem-russkikh/wireproxy-awg.git"
# Read by the scripts that source this file, which shellcheck cannot see.
# shellcheck disable=SC2034
GO_IMAGE="docker.io/library/golang:1.27"

die() {
    echo "FATAL: $*" >&2
    exit 1
}

# The manifest is the single source of truth for what engine we build. Scripts
# read the pin from it rather than keeping their own copy, so the two cannot
# drift apart the way they could when the pin lived in a shell variable.
engine_pin() {
    local field="$1" value
    value="$(awk -v f="$field:" '
        /^  - name: awg-tunnel-engine$/ { in_mod = 1 }
        in_mod && $1 == f { print $2; exit }
    ' "$MANIFEST")"
    [[ -n "$value" ]] || die "no '$field' found in the engine module of $MANIFEST"
    printf '%s\n' "$value"
}

engine_tag() { engine_pin tag; }
engine_commit() { engine_pin commit; }

# podman and docker are both fine; inside a toolbox/distrobox we have to reach
# the host's podman, which is why the flatpak-spawn case exists.
container_runner() {
    if command -v podman >/dev/null 2>&1; then
        echo "podman"
    elif command -v flatpak-spawn >/dev/null 2>&1 &&
        flatpak-spawn --host podman --version >/dev/null 2>&1; then
        echo "flatpak-spawn --host podman"
    elif command -v docker >/dev/null 2>&1; then
        echo "docker"
    else
        die "need podman or docker"
    fi
}

# Clone upstream at the pinned commit and fail loudly if the tag has moved.
clone_engine_at_pin() {
    local dest="$1" tag commit actual
    tag="$(engine_tag)"
    commit="$(engine_commit)"

    rm -rf "$dest"
    mkdir -p "$(dirname "$dest")"
    git clone --quiet --depth 1 --branch "$tag" "$ENGINE_URL" "$dest"

    actual="$(git -C "$dest" rev-parse HEAD)"
    if [[ "$actual" != "$commit" ]]; then
        die "tag $tag now points at $actual but the manifest pins $commit"
    fi
    echo "$tag ($commit)"
}
