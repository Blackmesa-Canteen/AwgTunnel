#!/usr/bin/env bash
# Fetch the pinned engine source and vendor its Go modules, so that the Flatpak
# build itself needs no network access.
#
# The engine is wireproxy-awg: a userspace WireGuard/AmneziaWG client built on
# amneziawg-go's netstack. Pinned by commit, not just tag, so a moved tag cannot
# silently change what we build.
set -euo pipefail

ENGINE_REPO="https://github.com/artem-russkikh/wireproxy-awg"
ENGINE_TAG="v1.0.18"
ENGINE_COMMIT="84f4795ea76f9c3168a61e478d0fe0e5c3238308"
GO_IMAGE="docker.io/library/golang:1.27"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="$repo_root/vendor/wireproxy-awg"

if [[ -d "$dest/vendor" ]] && [[ "${1:-}" != "--force" ]]; then
    echo "Engine already vendored at $dest (use --force to refetch)."
    exit 0
fi

echo "Fetching $ENGINE_REPO @ $ENGINE_TAG ($ENGINE_COMMIT)"
rm -rf "$dest"
mkdir -p "$(dirname "$dest")"
git clone --quiet "$ENGINE_REPO" "$dest"
git -C "$dest" checkout --quiet "$ENGINE_COMMIT"

actual="$(git -C "$dest" rev-parse HEAD)"
if [[ "$actual" != "$ENGINE_COMMIT" ]]; then
    echo "FATAL: commit mismatch: expected $ENGINE_COMMIT, got $actual" >&2
    exit 1
fi
rm -rf "$dest/.git"

# Vendoring needs Go 1.26+; run it in a container so no host toolchain is required.
echo "Vendoring Go modules with $GO_IMAGE"
runner=""
if command -v podman >/dev/null 2>&1; then
    runner="podman"
elif command -v flatpak-spawn >/dev/null 2>&1 && flatpak-spawn --host podman --version >/dev/null 2>&1; then
    runner="flatpak-spawn --host podman"
elif command -v docker >/dev/null 2>&1; then
    runner="docker"
else
    echo "FATAL: need podman or docker to vendor Go modules" >&2
    exit 1
fi

# shellcheck disable=SC2086
$runner run --rm -v "$dest:/src:Z" -w /src "$GO_IMAGE" \
    sh -c 'go mod vendor && go build -mod=vendor -o /dev/null ./cmd/wireproxy'

echo "OK: vendored engine at $dest ($(du -sh "$dest" | cut -f1))"
