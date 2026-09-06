# Using AWG Tunnel

AWG Tunnel gives you a **local SOCKS5 proxy** that carries traffic through a
WireGuard or AmneziaWG server. Applications must be told to use that proxy;
nothing is routed automatically.

## 1. Import a profile

Use **＋ → Import from File…** and pick a `wg-quick`/`awg-quick` `.conf`, or
**＋ → Paste from Clipboard**.

A profile looks like this — the `Jc`/`S*`/`H*` settings are the AmneziaWG
obfuscation parameters, and they are optional:

```ini
[Interface]
Address = 10.0.0.3/24
PrivateKey = <your key>
DNS = 10.0.0.1
MTU = 1420
Jc = 10
Jmin = 140
Jmax = 434
S1 = 122
S2 = 73
S3 = 58
S4 = 31
H1 = 303255526-303255725
H2 = 975392275-975392430
H3 = 861321606-861321678
H4 = 1249894772-1501929947

[Peer]
PublicKey = <server key>
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

The profile is validated on import. If a parameter is out of range or two
parameters conflict, you get a message naming the field instead of a silent
failure later.

`AllowedIPs` is accepted but has no effect: everything reaching the proxy goes
through the tunnel, and everything else never touches it.

## 2. Connect

Flip **Connect**. The status row reports what is actually happening:

| Status | Meaning |
|---|---|
| Starting | the engine is coming up |
| Negotiating | handshake sent, waiting for the server |
| Connected | handshake completed; traffic can flow |
| Idle | connected earlier, but no recent traffic from the server |
| Failed | see the reason and the engine log |

"Connected" is derived from the tunnel's real handshake timestamp and byte
counters, not from the engine having started. If the handshake does not complete
within 25 seconds the engine is stopped, so you never get a proxy that accepts
connections and quietly discards them.

Press **Test** to fetch your visible IP address through the tunnel. It should
show your server's address. (This contacts `icanhazip.com` through the tunnel.)

## 3. Point applications at the proxy

Copy the address with the button next to **SOCKS5 proxy**.

**Firefox** — Settings → Network Settings → Manual proxy configuration. Set
SOCKS Host to `127.0.0.1` and the port shown, choose SOCKS v5, and tick
**Proxy DNS when using SOCKS v5** so lookups go through the tunnel too.

**Chromium / Chrome / Electron apps**

```bash
chromium --proxy-server="socks5://127.0.0.1:PORT"
```

**GNOME** — Settings → Network → Proxy → Manual → Socks Host.

**KDE Plasma** — System Settings → Network → Proxy → Manually.

**curl / git / ssh**

```bash
curl --socks5-hostname 127.0.0.1:PORT https://example.com
git -c http.proxy=socks5h://127.0.0.1:PORT clone https://…
ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:PORT %h %p' user@host
```

Use `socks5h`/`--socks5-hostname` rather than `socks5`, so DNS is resolved
inside the tunnel instead of locally.

## Limits worth understanding

- **Not a VPN.** Applications that ignore the proxy use your normal connection.
  Setting a desktop-wide proxy helps, but many applications bypass it.
- **No kill switch.** If the tunnel drops, proxied applications fail; they do
  not fall back silently, but unproxied traffic was never protected to begin
  with.
- **UDP is limited.** SOCKS5 UDP works only with applications that implement
  UDP ASSOCIATE. Most browsers do not, so QUIC typically falls back to TCP.
- **Ports change.** A free port is chosen each time you connect, so re-copy the
  address after reconnecting.

## Troubleshooting

**"No response from the server"** — the peer may not exist on the server, the
endpoint may be unreachable, or the keys may not match. A WireGuard server
silently ignores unknown peers, so this is what a deleted or mistyped peer looks
like. Check that the public key in your profile matches the server and that the
peer is still configured there.

**Connects, but nothing loads** — check that the application is really using the
proxy, and that `DNS` in the profile is reachable from inside the tunnel.

**Status shows Idle** — normal for a tunnel with no traffic. It returns to
Connected as soon as data flows.

For anything else, open **Diagnostics → Engine log**. Keys are redacted, so it
is safe to attach to a bug report — but read it before you post.
