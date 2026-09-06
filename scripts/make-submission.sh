#!/usr/bin/env bash
# Produce the exact file set to copy into a Flathub submission pull request.
#
#   scripts/make-submission.sh v0.1.0 [outdir]
#
# The only difference from the manifest in this repo is the app module's own
# source: local development builds the working tree with `type: dir`, whereas
# Flathub must fetch a tagged, publicly reachable commit. Deriving the
# submission manifest instead of maintaining a second copy means the two cannot
# drift in their permissions or their engine pin.
#
# This writes files for you to review and commit yourself. It deliberately does
# not touch GitHub: Flathub's requirements prohibit AI tools or agents from
# opening submission pull requests or writing their commit messages, so the PR
# and everything written into it has to be your own work.
set -euo pipefail

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

APP_ID="io.github.blackmesa_canteen.AwgTunnel"
APP_URL="https://github.com/Blackmesa-Canteen/AwgTunnel.git"

ref="${1:-}"
outdir="${2:-$REPO_ROOT/.work/flathub}"
[[ -n "$ref" ]] || die "usage: ${0##*/} <tag-or-commit> [outdir]"

commit="$(git -C "$REPO_ROOT" rev-parse --verify "${ref}^{commit}" 2>/dev/null)" ||
    die "$ref does not resolve to a commit in this repo"

# A release submission pins a tag as well as a commit, which is what lets
# Flathub's update checker notice new versions. CI passes a bare commit to
# prove the manifest builds before any tag exists, and then there is no tag to
# record and no version for the checker to compare against.
tag=""
if git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/tags/$ref" >/dev/null; then
    tag="$ref"
fi

rm -rf "$outdir"
mkdir -p "$outdir"

# Replace the app module's `type: dir` source (and its skip list, which runs to
# the end of the file) with a pinned git source. Asserting the shape first so a
# manifest reshuffle fails here rather than silently producing a manifest that
# builds the wrong thing.
grep -q '^  - name: awg-tunnel$' "$MANIFEST" || die "app module not found in $MANIFEST"
[[ "$(grep -c '^      - type: dir$' "$MANIFEST")" -eq 1 ]] ||
    die "expected exactly one 'type: dir' source in $MANIFEST"

awk '
    /^      - type: dir$/ { exit }
    { print }
' "$MANIFEST" >"$outdir/$APP_ID.yml"

{
    echo "      - type: git"
    echo "        url: $APP_URL"
    [[ -n "$tag" ]] && echo "        tag: $tag"
    echo "        commit: $commit"
    if [[ -n "$tag" ]]; then
        echo "        x-checker-data:"
        echo "          type: git"
        echo '          tag-pattern: ^v([\d.]+)$'
    fi
} >>"$outdir/$APP_ID.yml"

cp "$REPO_ROOT/go.mod.yml" "$REPO_ROOT/modules.txt" "$outdir/"

# Sanity: the derived manifest must keep the permission set and the engine pin
# byte-for-byte. A submission that quietly widened finish-args would be the
# worst possible drift.
for needle in "$(engine_commit)" "--talk-name=org.freedesktop.secrets" "go.mod.yml"; do
    grep -q -- "$needle" "$outdir/$APP_ID.yml" || die "derived manifest lost '$needle'"
done

# The manifest documents which permissions are deliberately absent, so strip
# comments before looking for a granted one — otherwise the documentation
# itself trips the check.
if sed 's/#.*$//' "$outdir/$APP_ID.yml" | grep -q -- '--filesystem'; then
    die "derived manifest gained --filesystem"
fi

echo "Submission files for ${tag:-untagged} ($commit):"
find "$outdir" -maxdepth 1 -type f -printf '  %f\n' | sort
echo
echo "Next: build it once with flatpak-builder before you open anything."
