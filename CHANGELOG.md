# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project uses [Semantic Versioning](https://semver.org/).

Most of this codebase was written with AI assistance, directed and reviewed by
a human maintainer — see [README.md](README.md#ai-assisted-development). That
review, not the tag date below, is what "released" means here.

## [0.1.0] - 2026-09-07

First tagged build.

### Added

- GTK4/libadwaita client for WireGuard and AmneziaWG servers, running entirely
  in userspace via a bundled `wireproxy-awg` engine — no root, no kernel
  module, no host service, no elevated Flatpak permissions.
- Tunnel exposed as a local SOCKS5 proxy; a permanent banner states plainly
  that this is a proxy, not a system-wide VPN, and that there is no kill
  switch.
- Profile import for standard `wg-quick`/`awg-quick` configuration files,
  including AmneziaWG obfuscation parameters (`Jc`/`Jmin`/`Jmax`, `S1`–`S4`,
  `H1`–`H4` as single values or ranges, `I1`–`I5`, header protection,
  content padding, rekey/timeout intervals). Parameters are validated only to
  give a useful error at import time; they are passed to the engine
  unchanged.
- Connection state driven entirely from the engine's own handshake and traffic
  counters, never assumed. A stalled handshake stops the engine rather than
  leaving a dead tunnel behind a live proxy.
- Private keys stored in the system keyring via the Secret Service; a `0600`
  file fallback if no keyring is available, with the app saying so rather than
  downgrading silently.
- "Test connection" button that fetches a page through the tunnel and reports
  the exit IP, so a working tunnel is verified, not assumed.
- Flatpak manifest with a permission set enforced by test:
  `--share=ipc`, `--share=network`, `--socket=wayland`,
  `--socket=fallback-x11`, `--device=dri`,
  `--talk-name=org.freedesktop.secrets`. No filesystem access at all — profile
  import goes through the file chooser portal.

### Security

- The engine is fetched from a pinned upstream commit; its Go modules are
  declared as sha256-pinned archives (`go.mod.yml`) rather than a vendored
  copy, so no upstream source code is committed to this repository and every
  hash is independently re-checkable against the module proxy.
- CI: unit and end-to-end tests (a real obfuscated tunnel on loopback,
  requiring no privileges), `govulncheck` against the engine, CodeQL, weekly
  re-verification of every module hash, and a manifest-policy test that fails
  if the permission set is ever widened.
- Any text that might contain a private key — profile contents, engine
  output — is passed through shape-based redaction before it can reach a log
  or the UI.

[0.1.0]: https://github.com/Blackmesa-Canteen/AwgTunnel/releases/tag/v0.1.0
