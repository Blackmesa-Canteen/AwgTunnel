"""Lifecycle and state tracking for the tunnel engine subprocess.

The engine runs as a child process inside the same Flatpak sandbox as the UI,
so there is no IPC across a sandbox boundary and no permission is needed to
reach it. State is derived from the engine's own ``/metrics`` endpoint, which
reports the WireGuard IPC counters (handshake timestamp, byte counters), rather
than from "did the process start" — the UI must never claim a working tunnel on
the strength of a successful ``exec``.

One deliberate behaviour: if the handshake does not complete within
:data:`HANDSHAKE_TIMEOUT`, the engine is stopped rather than left running. A
live SOCKS proxy in front of a dead tunnel accepts connections and then
blackholes them, which is a worse failure than refusing to connect at all.
"""

from __future__ import annotations

import contextlib
import enum
import os
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .profile import Profile, redact

#: Grace period for the engine to bind its ports before we expect metrics.
STARTUP_GRACE = 4.0

#: How long to wait for a first handshake before declaring failure.
HANDSHAKE_TIMEOUT = 25.0

#: A handshake older than this, with no recent traffic, means "idle or stalled".
STALE_HANDSHAKE_AGE = 180.0

#: Traffic seen more recently than this keeps the tunnel classified healthy.
RECENT_TRAFFIC_WINDOW = 60.0

_LOG_LINES = 400


class State(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    HANDSHAKING = "handshaking"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"

    @property
    def is_active(self) -> bool:
        """True when the engine is running or coming up."""
        return self in {
            State.STARTING,
            State.HANDSHAKING,
            State.CONNECTED,
            State.DEGRADED,
        }


@dataclass
class Status:
    state: State = State.IDLE
    detail: str = ""
    endpoint: str | None = None
    last_handshake: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    socks_address: str | None = None

    @property
    def handshake_age(self) -> float | None:
        if not self.last_handshake:
            return None
        return max(0.0, time.time() - self.last_handshake)


@dataclass
class _Metrics:
    endpoint: str | None = None
    last_handshake: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    peers: int = 0


@dataclass
class _Runtime:
    process: subprocess.Popen[bytes]
    config_path: Path
    socks_port: int
    info_port: int
    started_at: float
    log: deque[str] = field(default_factory=lambda: deque(maxlen=_LOG_LINES))
    reader: threading.Thread | None = None
    last_rx: int = 0
    last_rx_change: float = 0.0


class EngineError(RuntimeError):
    """The engine could not be started."""


def find_engine() -> str:
    """Locate the engine binary, preferring an explicit override."""
    override = os.environ.get("AWG_TUNNEL_ENGINE")
    if override:
        if not os.access(override, os.X_OK):
            raise EngineError(f"AWG_TUNNEL_ENGINE is not executable: {override}")
        return override

    found = shutil.which("awg-tunnel-engine")
    if found:
        return found
    raise EngineError(
        "Tunnel engine 'awg-tunnel-engine' not found. This normally means the "
        "application was built incorrectly."
    )


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _runtime_dir() -> Path:
    """A private directory for the generated configuration.

    Preferring ``XDG_RUNTIME_DIR`` keeps the rendered profile — which contains
    the private key — on a per-user tmpfs that does not survive logout, instead
    of in persistent storage.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    directory = Path(base) / "awg-tunnel" if base else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)
    return directory


class Engine:
    """Owns at most one engine process."""

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary
        self._runtime: _Runtime | None = None
        self._status = Status()
        self._lock = threading.Lock()

    @property
    def status(self) -> Status:
        return self._status

    @property
    def is_running(self) -> bool:
        runtime = self._runtime
        return runtime is not None and runtime.process.poll() is None

    def log_lines(self) -> list[str]:
        runtime = self._runtime
        return list(runtime.log) if runtime else []

    def validate(self, profile: Profile) -> None:
        """Run the engine's own config test, so errors match what it enforces."""
        binary = self._binary or find_engine()
        config_path = self._write_config(profile, socks_port=_pick_free_port())
        try:
            result = subprocess.run(
                [binary, "-n", "-c", str(config_path)],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError("Engine config test timed out") from exc
        finally:
            _unlink(config_path)

        if result.returncode != 0:
            message = redact(
                (result.stderr or result.stdout).decode("utf-8", "replace")
            ).strip()
            raise EngineError(message or "Engine rejected the profile")

    def start(self, profile: Profile) -> None:
        with self._lock:
            if self.is_running:
                raise EngineError("Engine is already running")

            binary = self._binary or find_engine()
            socks_port = _pick_free_port()
            info_port = _pick_free_port()
            config_path = self._write_config(profile, socks_port)

            try:
                process = subprocess.Popen(
                    [binary, "-c", str(config_path), "-i", f"127.0.0.1:{info_port}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    # Own session, so the whole engine process group can be
                    # signalled without ever touching the UI process.
                    start_new_session=True,
                )
            except OSError as exc:
                _unlink(config_path)
                raise EngineError(f"Could not start the engine: {exc}") from exc

            runtime = _Runtime(
                process=process,
                config_path=config_path,
                socks_port=socks_port,
                info_port=info_port,
                started_at=time.monotonic(),
            )
            runtime.reader = threading.Thread(
                target=self._drain_output,
                args=(runtime,),
                name="engine-log",
                daemon=True,
            )
            runtime.reader.start()

            self._runtime = runtime
            self._status = Status(
                state=State.STARTING,
                detail="Starting tunnel",
                endpoint=profile.endpoint,
                socks_address=f"127.0.0.1:{socks_port}",
            )

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            runtime = self._runtime
            self._runtime = None

        if runtime is None:
            self._status = Status()
            return

        self._status = Status(state=State.STOPPING, detail="Stopping tunnel")
        process = runtime.process
        if process.poll() is None:
            _terminate(process, timeout)
        _unlink(runtime.config_path)
        self._status = Status()

    def poll(self) -> Status:
        """Refresh and return the current status. Safe to call from a timer."""
        runtime = self._runtime
        if runtime is None:
            self._status = Status()
            return self._status

        exit_code = runtime.process.poll()
        if exit_code is not None:
            detail = self._exit_detail(runtime, exit_code)
            _unlink(runtime.config_path)
            self._runtime = None
            self._status = Status(
                state=State.FAILED,
                detail=detail,
                endpoint=self._status.endpoint,
            )
            return self._status

        elapsed = time.monotonic() - runtime.started_at
        metrics = self._read_metrics(runtime)

        if metrics is None:
            state = State.STARTING if elapsed < STARTUP_GRACE else State.HANDSHAKING
            detail = (
                "Starting tunnel"
                if state is State.STARTING
                else "Waiting for the engine to report status"
            )
            if elapsed > HANDSHAKE_TIMEOUT:
                self.stop()
                self._status = Status(
                    state=State.FAILED,
                    detail="The engine did not report any status. See the log for details.",
                )
                return self._status
            self._status = Status(
                state=state,
                detail=detail,
                endpoint=self._status.endpoint,
                socks_address=f"127.0.0.1:{runtime.socks_port}",
            )
            return self._status

        now = time.monotonic()
        if metrics.rx_bytes != runtime.last_rx:
            runtime.last_rx = metrics.rx_bytes
            runtime.last_rx_change = now

        status = Status(
            endpoint=metrics.endpoint or self._status.endpoint,
            last_handshake=metrics.last_handshake,
            rx_bytes=metrics.rx_bytes,
            tx_bytes=metrics.tx_bytes,
            socks_address=f"127.0.0.1:{runtime.socks_port}",
        )

        if not metrics.last_handshake:
            if elapsed > HANDSHAKE_TIMEOUT:
                self.stop()
                self._status = Status(
                    state=State.FAILED,
                    detail=(
                        "No response from the server. The peer may not be "
                        "configured on the server, the endpoint may be "
                        "unreachable, or the keys may not match."
                    ),
                    endpoint=status.endpoint,
                )
                return self._status
            status.state = State.HANDSHAKING
            status.detail = "Negotiating with the server"
            self._status = status
            return self._status

        age = status.handshake_age or 0.0
        traffic_is_recent = (
            runtime.last_rx_change > 0.0
            and now - runtime.last_rx_change < RECENT_TRAFFIC_WINDOW
        )
        if age > STALE_HANDSHAKE_AGE and not traffic_is_recent:
            # Not treated as failure: an idle tunnel legitimately stops
            # rehandshaking until there is traffic to carry.
            status.state = State.DEGRADED
            status.detail = "Connected, but idle — no recent traffic from the server"
        else:
            status.state = State.CONNECTED
            status.detail = "Connected"

        self._status = status
        return self._status

    def _write_config(self, profile: Profile, socks_port: int) -> Path:
        directory = _runtime_dir()
        handle, raw_path = tempfile.mkstemp(
            prefix="tunnel-", suffix=".conf", dir=directory
        )
        path = Path(raw_path)
        try:
            os.fchmod(handle, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(profile.to_engine_config(f"127.0.0.1:{socks_port}"))
        except Exception:
            _unlink(path)
            raise
        return path

    def _read_metrics(self, runtime: _Runtime) -> _Metrics | None:
        url = f"http://127.0.0.1:{runtime.info_port}/metrics"
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
                body = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return _parse_metrics(body)

    def _drain_output(self, runtime: _Runtime) -> None:
        stream = runtime.process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = redact(raw.decode("utf-8", "replace").rstrip())
            if line:
                runtime.log.append(line)
        stream.close()

    def _exit_detail(self, runtime: _Runtime, exit_code: int) -> str:
        tail = [line for line in list(runtime.log)[-4:] if line]
        suffix = f" Last output: {tail[-1]}" if tail else ""
        if exit_code < 0:
            return f"The engine was terminated by signal {-exit_code}.{suffix}"
        return f"The engine exited unexpectedly (code {exit_code}).{suffix}"


def _parse_metrics(body: str) -> _Metrics:
    """Parse the engine's WireGuard IPC dump.

    Peer-scoped keys repeat per peer; byte counters are summed and the newest
    handshake wins, so a multi-peer profile reports the tunnel as a whole.
    """
    metrics = _Metrics()
    for raw in body.splitlines():
        key, _, value = raw.strip().partition("=")
        if not key or not value:
            continue
        if key == "public_key":
            metrics.peers += 1
        elif key == "endpoint" and metrics.endpoint is None:
            metrics.endpoint = value
        elif key == "last_handshake_time_sec":
            try:
                metrics.last_handshake = max(metrics.last_handshake, int(value))
            except ValueError:
                continue
        elif key in {"rx_bytes", "tx_bytes"}:
            try:
                amount = int(value)
            except ValueError:
                continue
            if key == "rx_bytes":
                metrics.rx_bytes += amount
            else:
                metrics.tx_bytes += amount
    return metrics


def _terminate(process: subprocess.Popen[bytes], timeout: float) -> None:
    """Stop the engine's whole process group, escalating if it ignores us."""
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        return

    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, signal_number)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            continue


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
