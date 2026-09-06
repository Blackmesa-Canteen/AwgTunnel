"""End-to-end test: a real obfuscated tunnel with both ends in userspace.

The engine accepts ``ListenPort`` and treats ``Endpoint`` as optional, so it can
act as a *responder* as well as an initiator. That makes it possible to stand up
a complete AmneziaWG tunnel on loopback with no server, no TUN device and no
privileges — and therefore to test the obfuscation parameters, the ``Engine``
state machine and our SOCKS5 client together, in CI.

Topology:

    pytest ──SOCKS5──► client engine ──obfuscated UDP──► server engine
                                                              │
                                                    TCPServerTunnel
                                                              ▼
                                                   local HTTP server

Skipped unless the engine binary is available (``AWG_TUNNEL_ENGINE`` or on
``PATH``), so the default unit run stays hermetic.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from awgtunnel import socks
from awgtunnel.engine import Engine, EngineError, State, find_engine
from awgtunnel.profile import parse

# Throwaway keys, generated for this test only and never used anywhere else.
SERVER_PRIVATE = "UFnJSfpmoz983M0TzrqHfip2huYSf390pPg/55DhLEo="
SERVER_PUBLIC = "QAbTkxtZ9bPtV+R3lV8XKGccPotrdiqg45LX9RTQ+FU="
CLIENT_PRIVATE = "YMe7RTrSIvI8sWYX2+3JKsY3XZDodctdHXuRSBu7Vms="
CLIENT_PUBLIC = "uyDiG5HR/F3Cg5RHOxX43w6qxKJo/kgrhtD1CBhqSFI="

SERVER_TUNNEL_IP = "10.10.0.1"
CLIENT_TUNNEL_IP = "10.10.0.2"
TUNNEL_HTTP_PORT = 8080

# An AmneziaWG 3.x parameter set: junk packets, all four padding sizes, and
# magic headers as ranges. Both ends must agree on these exactly.
OBFUSCATION = """Jc = 10
Jmin = 140
Jmax = 434
S1 = 122
S2 = 73
S3 = 58
S4 = 31
H1 = 303255526-303255725
H2 = 975392275-975392430
H3 = 861321606-861321678
H4 = 1249894772-1501929947"""

PAYLOAD = "tunnel-carried-this-payload"

pytestmark = pytest.mark.e2e


def _engine_binary() -> str:
    try:
        return find_engine()
    except EngineError:
        pytest.skip("engine binary not available; set AWG_TUNNEL_ENGINE")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = PAYLOAD.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass  # keep pytest output clean


@pytest.fixture
def origin_server():
    """A plain HTTP server that should only be reachable through the tunnel."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def server_engine(tmp_path, origin_server):
    """The responder end of the tunnel, plus its in-tunnel TCP forwarder."""
    binary = _engine_binary()
    listen_port = _free_port()

    config = tmp_path / "server.conf"
    config.write_text(
        f"""[Interface]
Address = {SERVER_TUNNEL_IP}/24
PrivateKey = {SERVER_PRIVATE}
ListenPort = {listen_port}
{OBFUSCATION}

[Peer]
PublicKey = {CLIENT_PUBLIC}
AllowedIPs = {CLIENT_TUNNEL_IP}/32

[TCPServerTunnel]
ListenPort = {TUNNEL_HTTP_PORT}
Target = 127.0.0.1:{origin_server}
""",
        encoding="utf-8",
    )

    process = subprocess.Popen(
        [binary, "-c", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        yield listen_port
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
def client_profile(server_engine):
    return parse(
        f"""[Interface]
Address = {CLIENT_TUNNEL_IP}/24
PrivateKey = {CLIENT_PRIVATE}
{OBFUSCATION}

[Peer]
PublicKey = {SERVER_PUBLIC}
Endpoint = 127.0.0.1:{server_engine}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
    )


def _wait_for(engine: Engine, states: set[State], timeout: float = 30.0) -> State:
    deadline = time.monotonic() + timeout
    last = State.IDLE
    while time.monotonic() < deadline:
        last = engine.poll().state
        if last in states:
            return last
        time.sleep(0.5)
    pytest.fail(
        f"engine stayed in {last.value}; log:\n" + "\n".join(engine.log_lines())
    )


def test_obfuscated_tunnel_carries_traffic(client_profile):
    """The whole path: handshake, state machine, SOCKS5 client, payload."""
    engine = Engine(_engine_binary())
    engine.validate(client_profile)
    engine.start(client_profile)
    try:
        state = _wait_for(engine, {State.CONNECTED, State.DEGRADED, State.FAILED})
        assert state is not State.FAILED, engine.status.detail

        status = engine.status
        assert status.last_handshake > 0, "handshake never completed"
        assert status.socks_address

        host, _, port = status.socks_address.rpartition(":")
        body = socks.http_get(
            host, int(port), SERVER_TUNNEL_IP, TUNNEL_HTTP_PORT, "/", timeout=20
        )
        assert body == PAYLOAD

        # Counters must move in both directions once traffic has flowed.
        after = engine.poll()
        assert after.tx_bytes > 0
        assert after.rx_bytes > 0
    finally:
        engine.stop()

    assert not engine.is_running
    assert engine.poll().state is State.IDLE


def test_wrong_key_never_reports_connected(client_profile, tmp_path):
    """A key mismatch must fail, not sit in a false 'connected' state.

    A WireGuard responder silently ignores unknown peers, so this is what a
    deleted or mistyped peer looks like from the client side.
    """
    text = client_profile.to_text().replace(SERVER_PUBLIC, CLIENT_PUBLIC)
    engine = Engine(_engine_binary())
    engine.start(parse(text))
    try:
        state = _wait_for(engine, {State.FAILED, State.CONNECTED}, timeout=40.0)
        assert state is State.FAILED
        assert engine.status.last_handshake == 0
        assert "No response from the server" in engine.status.detail
    finally:
        engine.stop()


def test_engine_log_is_redacted(client_profile):
    engine = Engine(_engine_binary())
    engine.start(client_profile)
    try:
        _wait_for(engine, {State.CONNECTED, State.DEGRADED, State.FAILED})
        joined = "\n".join(engine.log_lines())
        assert CLIENT_PRIVATE not in joined
        assert SERVER_PRIVATE not in joined
    finally:
        engine.stop()
