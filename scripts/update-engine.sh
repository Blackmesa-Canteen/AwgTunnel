#!/usr/bin/env bash
# Bump the pinned engine and regenerate its declared Go module sources.
#
#   scripts/update-engine.sh            # regenerate at the currently pinned tag
#   scripts/update-engine.sh v1.0.19    # move to a new tag
#
# This is a maintainer tool. It is never run by a build: it produces committed,
# reviewable metadata (go.mod.yml, modules.txt and the pin in the manifest),
# and the Flatpak build consumes only that. Review the resulting hash diff
# before committing — an unexplained hash change is the signal you want to see.
set -euo pipefail

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

target_tag="${1:-$(engine_tag)}"
workdir="$REPO_ROOT/.work/engine-update"

echo "==> Resolving $target_tag"
# Dereference an annotated tag to the commit it points at, otherwise the tag
# object's own hash would be recorded and flatpak-builder would reject it.
resolved="$(git ls-remote "$ENGINE_URL" "refs/tags/${target_tag}^{}" | cut -f1)"
[[ -n "$resolved" ]] || resolved="$(git ls-remote "$ENGINE_URL" "refs/tags/${target_tag}" | cut -f1)"
[[ -n "$resolved" ]] || die "tag $target_tag not found at $ENGINE_URL"
echo "    $target_tag -> $resolved"

echo "==> Writing the pin into the manifest"
[[ "$(grep -c '^        tag: ' "$MANIFEST")" -eq 1 ]] || die "expected exactly one tag: line"
[[ "$(grep -c '^        commit: ' "$MANIFEST")" -eq 1 ]] || die "expected exactly one commit: line"
sed -i -E "s|^( *tag: ).*|\1${target_tag}|; s|^( *commit: ).*|\1${resolved}|" "$MANIFEST"

# Keep the version baked into the binary in step with the pin, so that
# `awg-tunnel-engine --version` never lies about which build it is.
sed -i -E "s|(-X main\.version=)[^\"]*|\1${target_tag}|" "$MANIFEST"

echo "==> Cloning upstream at the pin"
clone_engine_at_pin "$workdir" >/dev/null

echo "==> Generating module sources with flatpak-go-mod"
runner="$(container_runner)"
outdir="$workdir/.gen"
mkdir -p "$outdir"
# The generator resolves the module graph and hashes the zips that `go mod
# download` has already verified against upstream's go.sum. Run in a container
# so no host Go toolchain is required and nothing touches a shared module cache.
# shellcheck disable=SC2086
$runner run --rm \
    -v "$workdir:/src:Z" \
    -w /src "$GO_IMAGE" \
    sh -euc '
        go run github.com/dennwc/flatpak-go-mod@latest .
        cp go.mod.yml modules.txt .gen/
    '

cat >"$REPO_ROOT/go.mod.yml" <<'HEADER'
# Go module sources for the awg-tunnel-engine module. GENERATED — do not edit.
#
# Regenerate with scripts/update-engine.sh, which runs flatpak-go-mod inside a
# container. Each entry reconstructs one module into the vendor/ tree from the
# immutable Go module proxy, pinned by sha256.
#
# The integrity chain: `go mod download` verifies each zip against upstream's
# go.sum, the generator hashes those verified zips, and flatpak-builder checks
# the hash again at download time. Vendor mode does not consult go.sum, so
# these sha256 values are the build's actual integrity guarantee.
HEADER
tail -n +2 "$outdir/go.mod.yml" >>"$REPO_ROOT/go.mod.yml"
cp "$outdir/modules.txt" "$REPO_ROOT/modules.txt"

echo "==> Verifying the generated hashes against the proxy"
"$REPO_ROOT/scripts/verify-engine-sources.sh"

echo
echo "Engine pinned at $(engine_tag) ($(engine_commit))"
echo "Changed: $(cd "$REPO_ROOT" && git diff --name-only | tr '\n' ' ')"
echo "Review the hash diff, then run a full build before committing."
