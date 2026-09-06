"""SOCKS5 address encoding.

Getting the address type wrong is the kind of bug that only shows up against a
strict proxy, so it is asserted at the byte level.
"""

import pytest

from awgtunnel.socks import SocksError, encode_address


def test_ipv4_literal_uses_address_type_1():
    assert encode_address("10.10.0.1") == bytes([0x01, 10, 10, 0, 1])


def test_ipv6_literal_uses_address_type_4():
    encoded = encode_address("2001:db8::1")
    assert encoded[0] == 0x04
    assert len(encoded) == 17
    assert encoded[1:] == b"\x20\x01\x0d\xb8" + b"\x00" * 11 + b"\x01"


def test_hostname_uses_address_type_3_with_length_prefix():
    encoded = encode_address("example.com")
    assert encoded[0] == 0x03
    assert encoded[1] == len(b"example.com")
    assert encoded[2:] == b"example.com"


def test_internationalised_hostname_is_idna_encoded():
    encoded = encode_address("bücher.example")
    assert encoded[0] == 0x03
    assert b"xn--" in encoded


def test_hostname_is_sent_unresolved():
    # DNS must happen inside the tunnel, so the name has to go on the wire
    # verbatim rather than being resolved locally first.
    assert encode_address("icanhazip.com")[2:] == b"icanhazip.com"


def test_overlong_hostname_is_rejected():
    with pytest.raises(SocksError):
        encode_address("a" * 256 + ".example")


def test_empty_hostname_is_rejected():
    with pytest.raises(SocksError):
        encode_address("")
