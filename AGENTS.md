# Working on AWG Tunnel

Instructions for coding agents. Humans should read [README.md](README.md) first;
this file exists to record the constraints that are not obvious from the code
and the ones that are expensive to get wrong.

AWG Tunnel is a GTK4/libadwaita client for WireGuard and AmneziaWG servers. It
terminates the tunnel in userspace and exposes it as a local SOCKS5 proxy, so it
needs no root, no kernel module, no host service and no elevated capabilities.
That property is the whole design, not an implementation detail.

## Hard constraints

Break any of these and the change is wrong, however well it works.

**The sandbox permission set is closed.** `finish-args` is exactly the six
entries in the manifest, asserted by `tests/test_manifest_policy.py`. In
particular there is **no `--filesystem` of any kind** — profile import goes
through the file chooser portal. If a feature seems to need host filesystem
access, a bus name, or `--device=all`, the feature is wrong for this app. Do not
widen permissions to make a test pass.

**No upstream source code is committed to this repository.** The engine is
fetched from a pinned upstream commit and its Go modules are declared in
`go.mod.yml` as sha256-pinned archives. Never commit a `vendor/` tree, a
prebuilt binary, or a `type: dir` source for the engine — Flathub's builders
only ever see the manifest, so a local directory means a build that works
nowhere else. CI enforces all three.

**Obfuscation parameters are passed through verbatim.** `Profile.to_engine_config`
emits the user's sections unchanged and appends only `[Socks5]`. The engine is
the authority on what `Jc`, `S1`–`S4`, `H1`–`H4` and friends mean. Validation in
`profile.py` exists solely to produce a useful error at import time instead of an
unexplained handshake timeout later; it must never rewrite, normalise or reorder
what the user supplied.

**Never report a connection the tunnel has not made.** `engine.py` drives state
from observed handshake and traffic counters, never from optimism. On handshake
timeout it *stops* the engine rather than leaving it running: a live SOCKS proxy
over a dead tunnel silently blackholes traffic, which is worse than refusing to
connect. Preserve that.

**Secrets.** Private keys go to the Secret Service; if no keyring is available
the key is stored `0600` and the UI says so rather than downgrading quietly.
`redact()` in `profile.py` matches key *shape* (43 base64 chars plus `=`), not
key names, so it catches secrets in free-form engine output too. Any new path
that logs, displays or serialises profile text must go through it. There is a
test for this; it once caught a regex that silently never matched a real key.

**Honesty in the UI.** The banner "Only apps pointed at the proxy are tunnelled
— there is no kill switch." is permanent and must stay. This is a proxy, not a
system-wide VPN, and the app must not imply whole-device protection.

## Flathub: what agents must not do

Flathub's requirements are explicit, and this project intends to publish there.

> AI tools or agents must not open or automate Flathub submission pull requests,
> or generate their commit messages, descriptions, review comments, or replies.

So: **do not open a submission PR, do not write its commit message or
description, and do not draft replies to Flathub reviewers.**
`scripts/make-submission.sh` deliberately produces files and stops. Leave the
pull request to the maintainer.

Separately, Flathub requires the submitter to *disclose* AI-generated code,
documentation and packaging. This project qualifies. Do not remove or soften the
disclosure guidance in [docs/SUBMISSION.md](docs/SUBMISSION.md).

## Commands

```bash
# Unit suite: no GTK, no display, no network. Must stay that way.
python3 -m pytest tests/ -q

# End-to-end: a real obfuscated tunnel on loopback, using the engine as its own
# responder. Needs no privileges.
eval "$(./scripts/build-test-engine.sh --export)"
python3 -m pytest tests/ -q -m e2e

ruff check src tests && ruff format --check src tests
shellcheck scripts/*.sh

# Build and install. Nothing needs fetching or vendoring first.
flatpak run --command=flathub-build org.flatpak.Builder --install \
    io.github.blackmesa_canteen.AwgTunnel.yml

flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest \
    io.github.blackmesa_canteen.AwgTunnel.yml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo

# Engine maintenance (maintainer tools; no build runs them).
./scripts/update-engine.sh v1.0.19    # rewrites pin, go.mod.yml, modules.txt
./scripts/verify-engine-sources.sh    # re-checks every hash against the proxy
```

Two lint findings are expected until the GitHub repository is public and
screenshots are added: `appid-url-not-reachable` and
`metainfo-missing-screenshots`. Everything else should be clean.

## Layout

| Path | What it is |
|---|---|
| `src/awgtunnel/profile.py` | `.conf` parsing, validation, redaction, engine config rendering |
| `src/awgtunnel/engine.py` | engine subprocess lifecycle and the connection state machine |
| `src/awgtunnel/storage.py` | keyring-backed profile store |
| `src/awgtunnel/socks.py` | minimal SOCKS5 client, used by the "Test connection" button |
| `src/awgtunnel/window.py` | the UI |
| `io.github.blackmesa_canteen.AwgTunnel.yml` | manifest; `type: dir` app source for local builds |
| `go.mod.yml`, `modules.txt` | GENERATED. Edit via `scripts/update-engine.sh`, never by hand |
| `scripts/lib.sh` | shared helpers; reads the engine pin out of the manifest |
| `tests/test_manifest_policy.py` | the permission and supply-chain guards |

## Conventions

- **Commits** are Conventional Commits (`build:`, `ci:`, `docs:`, `feat(ui):`,
  `fix(scripts):`, `test:`) with a body explaining *why*. One logical change per
  commit; do not bundle unrelated work.
- **Comments** explain reasoning, not mechanics. Say why a thing is done this
  way, especially where the obvious approach is wrong. Match the existing
  density — sparse but load-bearing.
- **British spelling** in prose ("behaviour", "licence" as a noun).
- Commit signing is **not** configured yet. Do not sign on the maintainer's
  behalf; a signature is an attestation that is theirs to make.
- Ruff rules are pinned explicitly in `ruff.toml` so a new release cannot break
  CI by enabling checks. Add rules deliberately, do not switch to defaults.

## Things that look like mistakes but are not

- **The hand-rolled INI parser** in `profile.py`. `configparser` rejects
  repeated `[Peer]` sections, which real profiles have.
- **Validation duplicated** between the app and the engine. Deliberate; see the
  passthrough constraint above.
- **`type: dir path: .`** for the app module in the root manifest. That is the
  local/CI manifest; the Flathub one is derived by `make-submission.sh` with a
  pinned git source. Do not "fix" it, and do not hand-maintain a second copy.
- **`tests/test_manifest_policy.py` strips comments** before checking for
  forbidden permissions. The manifest documents which permissions are
  deliberately absent, and an earlier version of the test tripped over its own
  documentation.
- **The e2e suite skips** when no engine binary is present. That is the intended
  behaviour for a clean checkout, not a broken test.

## Environment notes

- The maintainer runs Fedora Atomic (Aurora) and develops inside a
  distrobox/toolbox container. `flatpak` is **not** on the container's `PATH`;
  reach the host with `flatpak-spawn --host flatpak ...`. The same applies to
  `podman` and `gh`.
- `flatpak-builder` must run on the host, not in the container.
- **Do not use `pkill -f awg-tunnel...`** to clean up. The pattern matches the
  invoking shell's own command line and kills it. Use `kill <pid>` with a pid
  from `pgrep -f`, or a pattern that cannot match your own process.

## Before you finish

- Unit suite, ruff, and shellcheck clean.
- If you touched the manifest, `go.mod.yml`, or anything under `scripts/`: run a
  real build, and prefer a **fresh clone** — it is the only way to catch a
  manifest that depends on untracked local state.
- If you touched `engine.py` or `profile.py`: run the e2e suite.
- Say plainly what you verified and what you did not. An untested workflow is
  untested; do not describe it as working.
