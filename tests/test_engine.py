"""Engine metrics parsing and log hygiene."""

from awgtunnel.engine import State, Status, _parse_metrics
from awgtunnel.profile import redact

PEER_KEY = "b" * 43 + "="
SECOND_PEER_KEY = "c" * 43 + "="

SINGLE_PEER = f"""errno=0
private_key=REDACTED
listen_port=51820
public_key={PEER_KEY}
preshared_key=REDACTED
endpoint=198.51.100.10:51820
last_handshake_time_sec=1757180000
last_handshake_time_nsec=123
tx_bytes=6738
rx_bytes=18104
persistent_keepalive_interval=25
"""

TWO_PEERS = (
    SINGLE_PEER
    + f"""public_key={SECOND_PEER_KEY}
endpoint=198.51.100.20:51820
last_handshake_time_sec=1757180500
tx_bytes=100
rx_bytes=200
"""
)

NO_HANDSHAKE = f"""errno=0
public_key={PEER_KEY}
endpoint=198.51.100.10:51820
last_handshake_time_sec=0
tx_bytes=6738
rx_bytes=0
"""


def test_parses_single_peer():
    metrics = _parse_metrics(SINGLE_PEER)
    assert metrics.peers == 1
    assert metrics.endpoint == "198.51.100.10:51820"
    assert metrics.last_handshake == 1757180000
    assert (metrics.tx_bytes, metrics.rx_bytes) == (6738, 18104)


def test_sums_counters_and_takes_newest_handshake_across_peers():
    metrics = _parse_metrics(TWO_PEERS)
    assert metrics.peers == 2
    assert metrics.last_handshake == 1757180500
    assert metrics.tx_bytes == 6838
    assert metrics.rx_bytes == 18304
    # The first peer's endpoint is reported, not the last one seen.
    assert metrics.endpoint == "198.51.100.10:51820"


def test_no_handshake_is_reported_as_zero():
    metrics = _parse_metrics(NO_HANDSHAKE)
    assert metrics.last_handshake == 0
    assert metrics.tx_bytes == 6738
    assert metrics.rx_bytes == 0


def test_garbage_is_tolerated():
    metrics = _parse_metrics("nonsense\n\n=\nrx_bytes=notanumber\nrx_bytes=5\n")
    assert metrics.rx_bytes == 5
    assert metrics.last_handshake == 0


def test_metrics_output_carries_no_key_material():
    # The engine redacts keys itself; our redaction is a second line of defence.
    assert "REDACTED" in SINGLE_PEER
    assert "<redacted>" in redact(SINGLE_PEER)


def test_active_states_are_exactly_the_running_ones():
    active = {state for state in State if state.is_active}
    assert active == {
        State.STARTING,
        State.HANDSHAKING,
        State.CONNECTED,
        State.DEGRADED,
    }
    for state in (State.IDLE, State.STOPPING, State.FAILED):
        assert not state.is_active


def test_handshake_age_is_none_without_a_handshake():
    assert Status().handshake_age is None
    assert Status(last_handshake=1).handshake_age is not None
