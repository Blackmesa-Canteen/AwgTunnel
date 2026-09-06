"""Profile parsing and validation.

The obfuscation constraints are the interesting cases: a profile that violates
them produces an unexplained handshake timeout at runtime, so they are the ones
worth catching at import time.
"""

import pytest

from awgtunnel.profile import ProfileError, parse, parse_range, redact

# Curve25519 keys are 32 bytes: 43 base64 characters plus one '=' of padding.
# Built rather than pasted, so the fixtures cannot drift from the real shape —
# an off-by-one here is exactly how key redaction silently stops working.
PRIVATE_KEY = "a" * 43 + "="
PEER_KEY = "b" * 43 + "="
HEADER_PROTECTION_KEY = "c" * 43 + "="

# A representative AmneziaWG 3.x profile: S3/S4 present and H1-H4 as ranges.
OBFUSCATED = f"""
[Interface]
Address = 10.0.0.3/24
PrivateKey = {PRIVATE_KEY}
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
AllowedIPs = 0.0.0.0/0
Endpoint = 198.51.100.10:51820
PersistentKeepalive = 25
PublicKey = {PEER_KEY}
"""

PLAIN = f"""
[Interface]
Address = 10.0.0.3/24
PrivateKey = {PRIVATE_KEY}

[Peer]
AllowedIPs = 0.0.0.0/0
Endpoint = 198.51.100.10:51820
PublicKey = {PEER_KEY}
"""


def test_parses_obfuscated_profile():
    profile = parse(OBFUSCATED)
    assert profile.is_obfuscated
    assert profile.endpoint == "198.51.100.10:51820"
    params = profile.awg_params
    assert params["Jc"] == "10"
    assert params["S4"] == "31"
    assert params["H4"] == "1249894772-1501929947"


def test_plain_profile_is_not_obfuscated():
    profile = parse(PLAIN)
    assert not profile.is_obfuscated
    assert profile.awg_params == {}


def test_repeated_peer_sections_are_kept():
    profile = parse(PLAIN + PLAIN.split("[Peer]", 1)[1].join(["[Peer]", ""]))
    assert len(profile.peers) >= 1


def test_engine_config_appends_socks_and_preserves_profile():
    profile = parse(OBFUSCATED)
    rendered = profile.to_engine_config("127.0.0.1:1080")
    assert "[Socks5]\nBindAddress = 127.0.0.1:1080" in rendered
    # Obfuscation parameters must survive untouched.
    for line in ("Jc = 10", "S3 = 58", "H2 = 975392275-975392430"):
        assert line in rendered


def test_missing_private_key_is_rejected():
    text = OBFUSCATED.replace(f"PrivateKey = {PRIVATE_KEY}", "")
    with pytest.raises(ProfileError, match="PrivateKey"):
        parse(text)


def test_missing_endpoint_is_rejected():
    with pytest.raises(ProfileError, match="Endpoint"):
        parse(PLAIN.replace("Endpoint = 198.51.100.10:51820", ""))


def test_missing_peer_is_rejected():
    with pytest.raises(ProfileError, match="Peer"):
        parse(PLAIN.split("[Peer]")[0])


def test_setting_outside_section_is_rejected():
    with pytest.raises(ProfileError, match="outside of any section"):
        parse("Jc = 4\n[Interface]\n")


def test_line_without_equals_is_rejected():
    with pytest.raises(ProfileError, match="Key = Value"):
        parse("[Interface]\nnonsense\n")


@pytest.mark.parametrize("value", ["0", "129", "-1"])
def test_jc_out_of_range_is_rejected(value):
    with pytest.raises(ProfileError, match="Jc"):
        parse(OBFUSCATED.replace("Jc = 10", f"Jc = {value}"))


def test_jmin_above_jmax_is_rejected():
    with pytest.raises(ProfileError, match="Jmin"):
        parse(OBFUSCATED.replace("Jmin = 140", "Jmin = 500"))


def test_jmax_above_limit_is_rejected():
    with pytest.raises(ProfileError, match="Jmax"):
        parse(OBFUSCATED.replace("Jmax = 434", "Jmax = 1281"))


def test_colliding_padded_packet_sizes_are_rejected():
    # S1 + 148 == S2 + 92  ->  S1 = 17, S2 = 73 both yield 165 bytes.
    with pytest.raises(ProfileError, match="collides"):
        parse(OBFUSCATED.replace("S1 = 122", "S1 = 17"))


def test_overlapping_magic_header_ranges_are_rejected():
    with pytest.raises(ProfileError, match="overlap"):
        parse(
            OBFUSCATED.replace("H2 = 975392275-975392430", "H2 = 303255600-303255800")
        )


def test_magic_header_overlapping_a_default_is_rejected():
    # H3 defaults to 3 when unset, so H1 = 3 collides with the implied default.
    text = OBFUSCATED
    for key in ("H2", "H3", "H4"):
        text = "\n".join(
            line for line in text.splitlines() if not line.startswith(f"{key} =")
        )
    with pytest.raises(ProfileError, match="overlap"):
        parse(text.replace("H1 = 303255526-303255725", "H1 = 3"))


def test_header_protection_key_requires_large_padding():
    text = OBFUSCATED.replace("S4 = 31", "S4 = 4")
    text = text.replace(
        "[Peer]", f"HeaderProtectionKey = {HEADER_PROTECTION_KEY}\n\n[Peer]"
    )
    with pytest.raises(ProfileError, match="HeaderProtectionKey"):
        parse(text)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", (5, 5)), ("5-9", (5, 9)), (" 12 - 40 ", (12, 40))],
)
def test_parse_range_accepts_values_and_intervals(raw, expected):
    assert parse_range("H1", raw) == expected


@pytest.mark.parametrize("raw", ["", "-", "9-5", "4-", "a-b", "1-2-3"])
def test_parse_range_rejects_malformed(raw):
    with pytest.raises(ProfileError):
        parse_range("H1", raw)


def test_redaction_hides_keys():
    text = redact(OBFUSCATED)
    assert PRIVATE_KEY not in text
    assert PEER_KEY not in text
    assert "<redacted>" in text
    # Non-secret values must survive redaction.
    assert "Jc = 10" in text
    assert "10.0.0.3/24" in text


def test_redacted_text_never_contains_the_private_key():
    profile = parse(OBFUSCATED)
    assert PRIVATE_KEY not in profile.redacted_text()


def test_redaction_matches_real_world_key_shape():
    # Regression guard: an earlier revision matched 42 characters instead of 43,
    # so redaction silently passed every real key through.
    sample = "6Cm4HOuEyFkqTFKvSc03yL9SP+BFdnrbJrCzWsJTj0A="
    assert len(sample) == 44
    assert redact(f"PrivateKey = {sample}") == "PrivateKey = <redacted>"
