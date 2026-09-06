#!/usr/bin/env bash
# Re-download every Go module declared in go.mod.yml and check its sha256.
#
# Vendor mode does not consult go.sum, so these hashes are what actually
# protects the build. This re-checks them against the module proxy from a clean
# slate, which catches a hash that was mistyped, hand-edited, or generated from
# a tampered local module cache.
set -euo pipefail

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SOURCES="$REPO_ROOT/go.mod.yml"
[[ -f "$SOURCES" ]] || die "$SOURCES not found"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

checked=0
failed=0

# Each archive entry is a "sha256:" line and a "url:" line within one block.
while read -r sha url; do
    name="${url#https://proxy.golang.org/}"
    name="${name%%/@v/*}"
    if ! curl -fsS --retry 3 --retry-delay 2 -o "$tmp/mod.zip" "$url"; then
        echo "FAIL  $name  (download failed)"
        failed=$((failed + 1))
        continue
    fi
    actual="$(sha256sum "$tmp/mod.zip" | cut -d' ' -f1)"
    checked=$((checked + 1))
    if [[ "$actual" == "$sha" ]]; then
        echo "ok    $name"
    else
        echo "FAIL  $name"
        echo "        declared $sha"
        echo "        actual   $actual"
        failed=$((failed + 1))
    fi
done < <(awk '
    /^  sha256: / { sha = $2 }
    /^  url: /    { if (sha != "") { print sha, $2; sha = "" } }
' "$SOURCES")

# A go.mod.yml that declared nothing would otherwise "pass" silently.
[[ "$checked" -gt 0 ]] || die "no archive sources found in $SOURCES"

echo
if [[ "$failed" -gt 0 ]]; then
    die "$failed of $((checked + failed)) module(s) failed verification"
fi
echo "OK: $checked module(s) match their declared sha256"
