#!/usr/bin/env bash
# Replace the placeholder application ID with your own.
#
# Flathub verifies io.github.* IDs against a repository you control, so the ID
# must match your GitHub account or organisation.
#
#   ./scripts/set-app-id.sh <github-username-or-org>
#
# Example: ./scripts/set-app-id.sh alice  ->  io.github.alice.AwgTunnel
set -euo pipefail

OLD_ID="io.github.blackmesa_canteen.AwgTunnel"
OLD_VENDOR="io.github.blackmesa_canteen"

if [[ $# -ne 1 ]]; then
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 2
fi

account="$1"
if [[ ! "$account" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,38}$ ]]; then
    echo "FATAL: '$account' is not a valid GitHub account name" >&2
    exit 1
fi

# Flathub requires the domain portion of an ID to be lowercase, with dashes
# converted to underscores and a leading digit prefixed by an underscore. The
# underscores map back to dashes when resolving the GitHub URL, so
# io.github.example_foo.Bar corresponds to github.com/example-foo/Bar.
vendor_component="${account,,}"
vendor_component="${vendor_component//-/_}"
case "$vendor_component" in
    [0-9]*) vendor_component="_${vendor_component}" ;;
esac

new_vendor="io.github.${vendor_component}"
new_id="${new_vendor}.AwgTunnel"

if [[ "$new_id" == "$OLD_ID" ]]; then
    echo "Nothing to do: the ID is already $new_id"
    exit 0
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "Renaming $OLD_ID -> $new_id"

# Rename the files whose names encode the ID.
for path in \
    "io.github.blackmesa_canteen.AwgTunnel.yml" \
    "data/io.github.blackmesa_canteen.AwgTunnel.desktop" \
    "data/io.github.blackmesa_canteen.AwgTunnel.metainfo.xml" \
    "data/icons/io.github.blackmesa_canteen.AwgTunnel.svg" \
    "data/icons/io.github.blackmesa_canteen.AwgTunnel-symbolic.svg"
do
    [[ -e "$path" ]] || continue
    new_path="${path//$OLD_ID/$new_id}"
    if command -v git >/dev/null && git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
        git mv "$path" "$new_path"
    else
        mv "$path" "$new_path"
    fi
done

# Then rewrite the contents. The vendor prefix is replaced first so the
# developer id in the metainfo is updated too.
mapfile -t files < <(
    grep -rl --binary-files=without-match "$OLD_VENDOR" \
        --exclude-dir=.git \
        --exclude-dir=vendor \
        --exclude-dir=.flatpak-builder \
        --exclude-dir=.work \
        --exclude-dir=repo \
        --exclude-dir=builddir \
        . || true
)

for file in "${files[@]}"; do
    sed -i "s|${OLD_ID}|${new_id}|g; s|${OLD_VENDOR}|${new_vendor}|g" "$file"
done

echo "Done. Remaining references (should be none):"
grep -rn "$OLD_VENDOR" --exclude-dir=.git --exclude-dir=vendor --exclude-dir=repo --exclude-dir=builddir . || echo "  none"
echo
echo "The ID resolves to https://github.com/${account}/AwgTunnel — the repository"
echo "must carry that name, or Flathub's linter reports appid-url-not-reachable."
echo
echo "Next: update the homepage and bugtracker URLs in the metainfo, then run"
echo "  python3 -m pytest tests/ -q"
