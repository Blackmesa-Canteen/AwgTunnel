#!/usr/bin/env bash
# Build the engine binary for the end-to-end test suite.
#
# The e2e suite stands up both ends of a real obfuscated tunnel on loopback, so
# it needs the actual engine rather than a stub. This builds it outside Flatpak
# from the same commit the manifest pins, and prints the binary path.
#
#   eval "$(scripts/build-test-engine.sh --export)"
#   python3 -m pytest tests/ -q -m e2e
set -euo pipefail

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

export_mode=false
[[ "${1:-}" == "--export" ]] && export_mode=true

workdir="$REPO_ROOT/.work/engine-test"
binary="$REPO_ROOT/.work/awg-tunnel-engine"

log() { $export_mode && return 0; echo "$@"; }

if [[ ! -x "$binary" ]]; then
    log "==> Cloning upstream at the pinned commit"
    clone_engine_at_pin "$workdir" >/dev/null

    # Unlike the Flatpak build this may fetch modules from the network: it is a
    # test fixture, not a release artifact. It still builds the pinned commit,
    # whose go.sum verifies every module it downloads.
    if command -v go >/dev/null 2>&1; then
        log "==> Building with the host Go toolchain"
        (cd "$workdir" && go build -o "$binary" ./cmd/wireproxy)
    else
        log "==> Building in a container ($GO_IMAGE)"
        runner="$(container_runner)"
        # shellcheck disable=SC2086
        $runner run --rm -v "$workdir:/src:Z" -w /src "$GO_IMAGE" \
            go build -o /src/awg-tunnel-engine ./cmd/wireproxy
        mv "$workdir/awg-tunnel-engine" "$binary"
    fi
fi

if $export_mode; then
    echo "export AWG_TUNNEL_ENGINE=$binary"
else
    echo "$binary"
fi
