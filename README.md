# AWG Tunnel

A desktop client for **WireGuard** and **AmneziaWG** servers that runs entirely
in userspace and exposes the tunnel as a local **SOCKS5 proxy**.

No root access. No kernel module. No system service. Nothing is installed
outside the Flatpak sandbox, which makes it usable on immutable and image-based
systems such as Fedora Silverblue, Kinoite, Bazzite and Aurora, where layering a
DKMS kernel module is painful and a privileged VPN daemon is worse.

## What this is not

**This is a proxy, not a system-wide VPN.** Only applications you point at the
proxy are tunnelled. There is **no kill switch and no leak protection**:
anything that ignores the proxy keeps using your normal connection.

A full-device tunnel fundamentally requires a privileged component on the host
to create a network interface and rewrite the routing table. That is a
deliberate non-goal here — the whole point of this design is that it needs no
such component.

If you need a whole-device tunnel, use `awg-quick` with the AmneziaWG kernel
module or `amneziawg-go` on the host instead.

## How it works

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
`tun` device and no `CAP_NET_ADMIN` are needed. The UI and the engine are parent
and child inside the *same* sandbox, so no IPC crosses a sandbox boundary and no
permission is required to connect them.

Obfuscation parameters are read from your profile by the engine itself and are
passed through **unchanged** — the app never rewrites them. Validation is
duplicated in the app only to produce a useful error message at import time
instead of an unexplained handshake timeout later.

Supported obfuscation parameters (AmneziaWG 1.0 through 3.1): `Jc`, `Jmin`,
`Jmax`, `S1`–`S4`, `H1`–`H4` including ranges, `I1`–`I5`,
`HeaderProtectionKey`, `ContentPaddingAddition`, `RandomTrailers`,
`DisableCookies` and the rekey/timeout intervals.

## Permissions

Every permission the app requests, and why:

| Permission | Why |
|---|---|
| `--share=network` | reach your server's UDP endpoint |
| `--share=ipc`, `--socket=wayland`, `--socket=fallback-x11` | display |
| `--device=dri` | GPU-accelerated rendering for GTK4 |
| `--talk-name=org.freedesktop.secrets` | store private keys in the system keyring |

Deliberately **not** requested: any `--filesystem` access (profile import uses
the file chooser portal), any system bus access, `--device=all`, and
`--talk-name=org.freedesktop.Flatpak`. `tests/test_manifest_policy.py` asserts
this exact set, so widening it fails the test suite.

Private keys are stored in the system keyring via the Secret Service API. If no
keyring is available, the key is kept in the app's own data directory with
`0600` permissions and the app says so in its banner rather than downgrading
quietly. While a tunnel is running, the generated engine configuration lives in
`XDG_RUNTIME_DIR` (a per-user tmpfs) and is deleted on disconnect.

## Building

Requires `flatpak` and `podman`/`docker` (the latter only to vendor Go modules).

```bash
# 1. Fetch the pinned engine source and vendor its Go dependencies.
./scripts/fetch-engine.sh

# 2. Build and install for the current user.
flatpak run --command=flathub-build org.flatpak.Builder --install \
    io.github.blackmesa_canteen.AwgTunnel.yml

# 3. Run it.
flatpak run io.github.blackmesa_canteen.AwgTunnel
```

The build itself has **no network access**: the engine is built from vendored
modules with `-mod=vendor` and `GOTOOLCHAIN=local`, so it cannot silently fetch
code or a toolchain.

### Tests and linting

```bash
python3 -m pytest tests/ -q
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest \
    io.github.blackmesa_canteen.AwgTunnel.yml
```

## Licence

GPL-3.0-or-later. Bundled components and their licences are listed in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

AWG Tunnel is not affiliated with, endorsed by or sponsored by the WireGuard or
Amnezia projects. WireGuard is a registered trademark of Jason A. Donenfeld.
