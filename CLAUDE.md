# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo also has `AGENTS.md`, written for coding agents generally — it has
the full list of hard constraints, "things that look like mistakes but
aren't", and Flathub-specific rules. Read it before making non-trivial
changes; this file is a shorter, Claude-Code-flavoured entry point that
overlaps with it deliberately, not a replacement.

## What this project is

AWG Tunnel is a GTK4/libadwaita desktop client for WireGuard and AmneziaWG
servers, packaged as a Flatpak. It terminates the tunnel entirely in
userspace and exposes it as a local SOCKS5 proxy — no root, no kernel module,
no host service, no elevated capabilities. **That property is the design,
not an implementation detail** — see "What this is not" in README.md. Do not
propose features (system-wide VPN via NetworkManager, `--filesystem` access,
a privileged helper) that would require walking that back.

## Commands

```bash
# Unit suite: no GTK, no display, no network access. Must stay that way.
python3 -m pytest tests/ -q

# Single test file / single test
python3 -m pytest tests/test_profile.py -q
python3 -m pytest tests/test_profile.py::test_name -q

# End-to-end: a real obfuscated tunnel on loopback, engine as its own responder.
# Skips automatically if no engine binary is present (expected on a clean checkout).
eval "$(./scripts/build-test-engine.sh --export)"
python3 -m pytest tests/ -q -m e2e

ruff check src tests && ruff format --check src tests
shellcheck scripts/*.sh

# Build and install (needs only `flatpak`; nothing to fetch/vendor first)
flatpak run --command=flathub-build org.flatpak.Builder --install \
    io.github.blackmesa_canteen.AwgTunnel.yml
flatpak run io.github.blackmesa_canteen.AwgTunnel

# Manifest / packaging lint
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest \
    io.github.blackmesa_canteen.AwgTunnel.yml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
```

Two lint findings (`appid-url-not-reachable`, `metainfo-missing-screenshots`)
are pre-existing/expected until the repo is public; everything else should be
clean.

### Engine maintenance (maintainer-only; no build runs these automatically)

```bash
./scripts/update-engine.sh v1.0.19   # rewrites the pin, go.mod.yml, modules.txt (needs podman/docker)
./scripts/verify-engine-sources.sh   # re-checks every hash against the Go module proxy
```

## Architecture

```
┌─ Flatpak sandbox ────────────────────────────────────────────┐
│  AWG Tunnel (GTK4 / libadwaita)                              │
│      │  spawns as a child process, reads its /metrics        │
│      ▼                                                        │
│  awg-tunnel-engine  (wireproxy-awg + amneziawg-go)            │
│      ├─ netstack tunnel ──► UDP ──► your server               │
│      └─ SOCKS5 proxy on 127.0.0.1:<port>                      │
└──────────────────────────────────────────────────────────────┘
```

The tunnel terminates inside the process in a userspace TCP/IP stack, so no
`tun` device and no `CAP_NET_ADMIN` are needed. The UI and the engine are
parent and child inside the *same* sandbox, so no IPC crosses a sandbox
boundary.

The Go engine (`wireproxy-awg` + `amneziawg-go`) is **not vendored in this
repo**. It's fetched from a pinned upstream commit in the manifest; its Go
modules are declared in `go.mod.yml` as sha256-pinned archives from the Go
module proxy, rebuilt into `vendor/` at build time. Never commit a `vendor/`
tree, a prebuilt binary, or a `type: dir` source for the engine.

Obfuscation parameters (`Jc`, `Jmin`, `Jmax`, `S1`–`S4`, `H1`–`H4` including
ranges, `I1`–`I5`, `HeaderProtectionKey`, `ContentPaddingAddition`,
`RandomTrailers`, `DisableCookies`, rekey/timeout intervals — AmneziaWG 1.0
through 3.1) are read by the engine and passed through **unchanged**; the app
never rewrites them. `profile.py` duplicates validation only to produce a
useful error at import time instead of an unexplained handshake timeout
later.

### Layout

| Path | What it is |
|---|---|
| `src/awgtunnel/profile.py` | `.conf` parsing, validation, redaction, engine config rendering |
| `src/awgtunnel/engine.py` | engine subprocess lifecycle and the connection state machine |
| `src/awgtunnel/storage.py` | keyring-backed profile store |
| `src/awgtunnel/socks.py` | minimal SOCKS5 client, used by the "Test connection" button |
| `src/awgtunnel/window.py` | the UI |
| `io.github.blackmesa_canteen.AwgTunnel.yml` | manifest; `type: dir` app source for local builds |
| `go.mod.yml`, `modules.txt` | GENERATED — edit via `scripts/update-engine.sh`, never by hand |
| `scripts/lib.sh` | shared helpers; reads the engine pin out of the manifest |
| `tests/test_manifest_policy.py` | the permission and supply-chain guards |

### State and secrets

`engine.py` drives connection state from observed handshake and traffic
counters, never from optimism — on handshake timeout it stops the engine
rather than leaving a live SOCKS proxy over a dead tunnel. Private keys go to
the Secret Service via the system keyring; if none is available the key is
stored `0600` in the app's data directory and the UI says so. While a tunnel
is running, the generated engine config lives in `XDG_RUNTIME_DIR` and is
deleted on disconnect. `redact()` in `profile.py` matches key *shape* (43
base64 chars + `=`), not key names — any new path that logs/displays/
serialises profile text must go through it.

### Sandbox permissions are closed

`finish-args` is exactly six entries (network, ipc/wayland/x11 for display,
dri, secrets), asserted by `tests/test_manifest_policy.py`. No
`--filesystem` (file chooser portal handles profile import), no system bus
access, no `--device=all`. Do not widen this to make a feature work — the
feature is wrong for this app if it needs to.

## Environment notes

- Development happens on Fedora Atomic (Aurora) inside a distrobox/toolbox
  container. `flatpak`, `podman`, and `gh` are **not** on the container's
  `PATH` — reach the host with `flatpak-spawn --host <cmd> ...`.
- `flatpak-builder` must run on the host, not in the container.
- Don't use `pkill -f awg-tunnel...` to clean up a running instance — it can
  match the invoking shell's own command line. Use `kill <pid>` from
  `pgrep -f` instead.

## Flathub submission constraints

This project intends to publish on Flathub, which requires AI-assistance
disclosure (see `docs/SUBMISSION.md`, `CHANGELOG.md`) and forbids AI tools
from opening/automating the submission PR or writing its commit message,
description, or review replies. `scripts/make-submission.sh` produces files
and stops deliberately — leave opening the PR to the maintainer.
