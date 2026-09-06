# Third-party components

AWG Tunnel bundles the following components. Their licences are reproduced in
full inside the built Flatpak under `/app/share/licenses/`.

## wireproxy-awg — ISC

The tunnel engine. Pinned at `v1.0.18`
(`84f4795ea76f9c3168a61e478d0fe0e5c3238308`).

- Source: <https://github.com/artem-russkikh/wireproxy-awg>
- Licence: ISC
- A fork of [wireproxy](https://github.com/pufferffish/wireproxy) (ISC) adding
  AmneziaWG obfuscation support.

## amneziawg-go — MIT

The AmneziaWG protocol implementation used by the engine, vendored via Go
modules as `github.com/amnezia-vpn/amneziawg-go/v3 v3.1.20260814`.

- Source: <https://github.com/amnezia-vpn/amneziawg-go>
- Licence: MIT
- Itself derived from [wireguard-go](https://git.zx2c4.com/wireguard-go/) (MIT),
  which in turn uses cryptographic code from Google's gVisor netstack (Apache-2.0
  / BSD-3-Clause) for the userspace TCP/IP stack.

## The full set of bundled Go modules

Every Go module compiled into the engine is declared in
[go.mod.yml](go.mod.yml), pinned by sha256 to an archive on the Go module
proxy. That file is the authoritative list of what is bundled; each module's own
licence text ships inside its archive.

To read them locally, build once (`flatpak-builder` keeps the build tree) and
look at the reconstructed vendor directory:

```bash
find .flatpak-builder/build/awg-tunnel-engine*/vendor -iname 'LICENSE*'
```

## Trademarks

AWG Tunnel is not affiliated with, endorsed by, or sponsored by the WireGuard or
Amnezia projects.

"WireGuard" is a registered trademark of Jason A. Donenfeld. "AmneziaWG" and
"Amnezia" are names used by the Amnezia VPN project. Both are referenced here
only to describe protocol compatibility.
