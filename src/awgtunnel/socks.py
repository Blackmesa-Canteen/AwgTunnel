"""A minimal SOCKS5 client, used only to verify that the tunnel carries traffic.

The GNOME runtime ships no SOCKS library, and pulling one in for a single
connectivity check is not worth the dependency.

Host *names* are sent to the proxy unresolved (address type 3) so that DNS is
resolved inside the tunnel — that makes the connectivity check a test of name
resolution as well as routing. IP literals are sent as address type 1 or 4
instead, rather than asking the proxy to "resolve" a numeric string.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl

_SOCKS_VERSION = 5
_NO_AUTH = 0x00
_CMD_CONNECT = 0x01
_ATYP_IPV4 = 0x01
_ATYP_DOMAIN = 0x03
_ATYP_IPV6 = 0x04

_REPLY_ERRORS = {
    0x01: "general SOCKS server failure",
    0x02: "connection not allowed by ruleset",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}


class SocksError(RuntimeError):
    """The proxy refused or failed the connection."""


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise SocksError("proxy closed the connection unexpectedly")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_address(host: str) -> bytes:
    """Encode a destination as a SOCKS5 address: type byte plus payload."""
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        try:
            encoded = host.encode("idna")
        except UnicodeError as exc:
            raise SocksError(f"invalid hostname: {host!r}") from exc
        if not encoded or len(encoded) > 255:
            raise SocksError(f"hostname unusable for SOCKS5: {host!r}") from None
        return bytes([_ATYP_DOMAIN, len(encoded)]) + encoded

    if parsed.version == 4:
        return bytes([_ATYP_IPV4]) + parsed.packed
    return bytes([_ATYP_IPV6]) + parsed.packed


def open_connection(
    proxy_host: str,
    proxy_port: int,
    host: str,
    port: int,
    timeout: float = 15.0,
) -> socket.socket:
    """Return a socket tunnelled to ``host:port`` through the SOCKS5 proxy."""
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.sendall(bytes([_SOCKS_VERSION, 1, _NO_AUTH]))
        version, method = _recv_exactly(sock, 2)
        if version != _SOCKS_VERSION:
            raise SocksError(f"unexpected SOCKS version {version}")
        if method != _NO_AUTH:
            raise SocksError("proxy demands authentication")

        sock.sendall(
            bytes([_SOCKS_VERSION, _CMD_CONNECT, 0x00])
            + encode_address(host)
            + port.to_bytes(2, "big")
        )

        version, reply, _reserved, atyp = _recv_exactly(sock, 4)
        if reply != 0x00:
            raise SocksError(_REPLY_ERRORS.get(reply, f"SOCKS error {reply}"))

        # Drain the bound address, whose length depends on its type.
        if atyp == 0x01:
            _recv_exactly(sock, 4 + 2)
        elif atyp == 0x04:
            _recv_exactly(sock, 16 + 2)
        elif atyp == _ATYP_DOMAIN:
            length = _recv_exactly(sock, 1)[0]
            _recv_exactly(sock, length + 2)
        else:
            raise SocksError(f"unsupported bound address type {atyp}")
    except Exception:
        sock.close()
        raise
    return sock


def _request(host: str, path: str) -> bytes:
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: awg-tunnel/connectivity-check\r\n"
        "Accept: text/plain\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")


def _read_body(stream: socket.socket | ssl.SSLSocket, max_bytes: int) -> str:
    received = bytearray()
    while len(received) < max_bytes:
        chunk = stream.recv(4096)
        if not chunk:
            break
        received.extend(chunk)

    head, _, body = bytes(received).partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    if " 200 " not in status_line:
        raise SocksError(f"unexpected HTTP response: {status_line}")
    return body.decode("utf-8", "replace").strip()


def https_get(
    proxy_host: str,
    proxy_port: int,
    host: str,
    path: str,
    timeout: float = 15.0,
    max_bytes: int = 8192,
) -> str:
    """Perform a small HTTPS GET through the proxy and return the body."""
    sock = open_connection(proxy_host, proxy_port, host, 443, timeout=timeout)
    try:
        context = ssl.create_default_context()
        with context.wrap_socket(sock, server_hostname=host) as tls:
            tls.sendall(_request(host, path))
            return _read_body(tls, max_bytes)
    finally:
        sock.close()


def http_get(
    proxy_host: str,
    proxy_port: int,
    host: str,
    port: int,
    path: str,
    timeout: float = 15.0,
    max_bytes: int = 8192,
) -> str:
    """Plain-HTTP variant, used by the end-to-end tunnel test."""
    sock = open_connection(proxy_host, proxy_port, host, port, timeout=timeout)
    try:
        sock.sendall(_request(host, path))
        return _read_body(sock, max_bytes)
    finally:
        sock.close()
