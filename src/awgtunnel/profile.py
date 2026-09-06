"""Parsing and validation of wg-quick / awg-quick profiles.

The engine reads obfuscation parameters straight out of the ``[Interface]``
section, so profiles are passed through essentially verbatim rather than
translated. What this module adds is *validation up front*: the engine's
constraints are re-implemented here so a bad profile is rejected at import time
with a message naming the offending field, instead of failing later as an
unexplained handshake timeout.

Constraint sources, mirrored from the engine's ``ValidateASecConfig``:
Jc within 1..128; Jmin <= Jmax; Jmax <= 1280; the four padded packet sizes
(S1+148, S2+92, S3+64, S4+32) pairwise distinct; H1-H4 intervals non-overlapping;
and S1-S4 >= 12 whenever HeaderProtectionKey is set.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

# Padded packet sizes must stay distinct, or the peer cannot tell message
# types apart. Base sizes are fixed by the WireGuard message formats.
_MESSAGE_INITIATION_SIZE = 148
_MESSAGE_RESPONSE_SIZE = 92
_MESSAGE_COOKIE_REPLY_SIZE = 64
_MESSAGE_TRANSPORT_SIZE = 32

# Mirrors device.HeaderCipherNonceSize in amneziawg-go.
_HEADER_CIPHER_NONCE_SIZE = 12

_DEFAULT_MAGIC_HEADERS = {"H1": 1, "H2": 2, "H3": 3, "H4": 4}

AWG_INT_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4")
AWG_RANGE_KEYS = (
    "H1",
    "H2",
    "H3",
    "H4",
    "ContentPaddingAddition",
    "RekeyAfterTime",
    "RekeyTimeout",
    "RejectAfterTime",
    "KeepaliveTimeout",
    "MaxHandshakeAttempts",
)
AWG_STR_KEYS = ("I1", "I2", "I3", "I4", "I5", "HeaderProtectionKey")
AWG_BOOL_KEYS = ("RandomTrailers", "DisableCookies")

AWG_KEYS = frozenset(AWG_INT_KEYS + AWG_RANGE_KEYS + AWG_STR_KEYS + AWG_BOOL_KEYS)

# Keys whose values must never be shown or logged.
SECRET_KEYS = frozenset({"PrivateKey", "PresharedKey"})

_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]$")
# Curve25519 keys are 32 bytes, i.e. 43 base64 characters plus one '=' of
# padding. Matching on shape rather than on key names means secrets are caught
# in free-form engine diagnostics too, not just in profile text.
_BASE64_KEY_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{43}=(?![A-Za-z0-9+/=])")


class ProfileError(ValueError):
    """A profile is malformed or violates a protocol constraint."""


def redact(text: str) -> str:
    """Replace anything shaped like a WireGuard key with a placeholder.

    Applied to engine output and to any profile text that reaches a log or the
    UI. Deliberately shape-based rather than key-name-based, so it also catches
    keys appearing in free-form engine diagnostics.
    """
    return _BASE64_KEY_RE.sub("<redacted>", text)


@dataclass
class Section:
    name: str
    entries: list[tuple[str, str]] = field(default_factory=list)

    def get(self, key: str) -> str | None:
        lowered = key.lower()
        for entry_key, value in self.entries:
            if entry_key.lower() == lowered:
                return value
        return None

    def has(self, key: str) -> bool:
        return self.get(key) is not None


@dataclass
class Profile:
    """A parsed profile. ``sections`` preserves original order and casing."""

    sections: list[Section]

    @property
    def interface(self) -> Section:
        for section in self.sections:
            if section.name.lower() == "interface":
                return section
        raise ProfileError("Profile has no [Interface] section")

    @property
    def peers(self) -> list[Section]:
        return [s for s in self.sections if s.name.lower() == "peer"]

    @property
    def awg_params(self) -> dict[str, str]:
        """Obfuscation parameters present in the profile, in canonical order."""
        interface = self.interface
        found: dict[str, str] = {}
        for key in AWG_INT_KEYS + AWG_RANGE_KEYS + AWG_STR_KEYS + AWG_BOOL_KEYS:
            value = interface.get(key)
            if value is not None:
                found[key] = value
        return found

    @property
    def is_obfuscated(self) -> bool:
        return bool(self.awg_params)

    @property
    def endpoint(self) -> str | None:
        peers = self.peers
        return peers[0].get("Endpoint") if peers else None

    def redacted_text(self) -> str:
        return redact(self.to_text())

    def to_text(self) -> str:
        out: list[str] = []
        for section in self.sections:
            out.append(f"[{section.name}]")
            for key, value in section.entries:
                out.append(f"{key} = {value}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    def to_engine_config(self, socks_bind: str) -> str:
        """Render an engine configuration.

        The profile's own sections are emitted unchanged; only a ``[Socks5]``
        section is appended. Not rewriting the user's parameters is deliberate:
        the engine is the authority on their meaning.
        """
        return f"{self.to_text()}\n[Socks5]\nBindAddress = {socks_bind}\n"


def _iter_lines(text: str) -> Iterator[tuple[int, str]]:
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        yield number, line


def parse(text: str) -> Profile:
    """Parse profile text.

    Hand-rolled rather than using ``configparser`` because wg-quick profiles
    routinely contain repeated ``[Peer]`` sections, which ``configparser``
    rejects outright.
    """
    sections: list[Section] = []
    current: Section | None = None

    for number, line in _iter_lines(text):
        match = _SECTION_RE.match(line)
        if match:
            current = Section(name=match.group("name").strip())
            sections.append(current)
            continue

        if current is None:
            raise ProfileError(f"Line {number}: setting outside of any section")

        if "=" not in line:
            raise ProfileError(f"Line {number}: expected 'Key = Value'")

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ProfileError(f"Line {number}: empty setting name")
        current.entries.append((key, value))

    if not sections:
        raise ProfileError("Profile is empty")

    profile = Profile(sections=sections)
    validate(profile)
    return profile


def _require(section: Section, key: str, where: str) -> str:
    value = section.get(key)
    if not value:
        raise ProfileError(f"[{where}] is missing required setting '{key}'")
    return value


def _parse_int(section: Section, key: str) -> int | None:
    raw = section.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ProfileError(f"'{key}' must be a whole number, got '{raw}'") from exc


def parse_range(key: str, raw: str) -> tuple[int, int]:
    """Parse an AmneziaWG interval parameter, written ``a`` or ``a-b``."""
    trimmed = raw.strip()
    if not trimmed:
        raise ProfileError(f"'{key}' is empty")

    parts = trimmed.split("-")
    if len(parts) > 2 or not parts[0]:
        raise ProfileError(f"'{key}' must be 'value' or 'min-max', got '{raw}'")

    try:
        low = int(parts[0])
        high = int(parts[1]) if len(parts) == 2 and parts[1] else low
    except ValueError as exc:
        raise ProfileError(f"'{key}' must contain whole numbers, got '{raw}'") from exc

    if len(parts) == 2 and not parts[1]:
        raise ProfileError(f"'{key}' must be 'value' or 'min-max', got '{raw}'")
    if low > high:
        raise ProfileError(f"'{key}' lower bound {low} exceeds upper bound {high}")
    if low < 0 or high > 0xFFFFFFFF:
        raise ProfileError(f"'{key}' must fit in 32 bits, got '{raw}'")
    return low, high


def validate(profile: Profile) -> None:
    """Raise :class:`ProfileError` if the profile could not possibly connect."""
    interface = profile.interface
    _require(interface, "PrivateKey", "Interface")
    _require(interface, "Address", "Interface")

    peers = profile.peers
    if not peers:
        raise ProfileError("Profile has no [Peer] section")
    for peer in peers:
        _require(peer, "PublicKey", "Peer")
        # The engine dials the peer directly; without an endpoint there is
        # nothing to dial, since a userspace client can never be the responder.
        _require(peer, "Endpoint", "Peer")

    _validate_obfuscation(interface)


def _validate_obfuscation(interface: Section) -> None:
    jc = _parse_int(interface, "Jc")
    if jc is not None and not 1 <= jc <= 128:
        raise ProfileError(f"'Jc' must be between 1 and 128, got {jc}")

    jmin = _parse_int(interface, "Jmin")
    jmax = _parse_int(interface, "Jmax")
    if jmin is not None and jmax is not None and jmin > jmax:
        raise ProfileError(f"'Jmin' ({jmin}) must not exceed 'Jmax' ({jmax})")
    if jmax is not None and jmax > 1280:
        raise ProfileError(f"'Jmax' must be at most 1280, got {jmax}")

    paddings = {
        "S1": (_parse_int(interface, "S1"), _MESSAGE_INITIATION_SIZE),
        "S2": (_parse_int(interface, "S2"), _MESSAGE_RESPONSE_SIZE),
        "S3": (_parse_int(interface, "S3"), _MESSAGE_COOKIE_REPLY_SIZE),
        "S4": (_parse_int(interface, "S4"), _MESSAGE_TRANSPORT_SIZE),
    }
    sizes: dict[int, str] = {}
    for key, (value, base) in paddings.items():
        if value is None:
            continue
        if value < 0:
            raise ProfileError(f"'{key}' must not be negative, got {value}")
        total = base + value
        if total in sizes:
            raise ProfileError(
                f"'{key}' collides with '{sizes[total]}': both produce {total}-byte "
                f"packets, so the peer cannot distinguish the message types"
            )
        sizes[total] = key

    intervals: list[tuple[str, int, int]] = []
    for key, default in _DEFAULT_MAGIC_HEADERS.items():
        raw = interface.get(key)
        low, high = parse_range(key, raw) if raw is not None else (default, default)
        intervals.append((key, low, high))

    for index, (key, low, high) in enumerate(intervals):
        for other_key, other_low, other_high in intervals[index + 1 :]:
            if low <= other_high and other_low <= high:
                raise ProfileError(
                    f"'{key}' and '{other_key}' overlap; the H1-H4 magic header "
                    f"ranges must be mutually exclusive"
                )

    for key in AWG_RANGE_KEYS:
        if key in _DEFAULT_MAGIC_HEADERS:
            continue
        raw = interface.get(key)
        if raw is not None:
            parse_range(key, raw)

    if interface.has("HeaderProtectionKey"):
        for key in ("S1", "S2", "S3", "S4"):
            value = _parse_int(interface, key)
            if value is None or value < _HEADER_CIPHER_NONCE_SIZE:
                raise ProfileError(
                    f"'HeaderProtectionKey' requires S1-S4 to all be at least "
                    f"{_HEADER_CIPHER_NONCE_SIZE}; '{key}' is "
                    f"{'unset' if value is None else value}"
                )
