"""Mandatory, release-blocking SSRF protection tests for the SMTP provider's
network helper (app/modules/mailboxes/providers/ssrf.py).

These tests never touch a real network: they exercise resolve_and_validate
directly, either against literal IP addresses (no DNS involved) or against
a monkeypatched socket.getaddrinfo for hostname-based cases.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from app.modules.mailboxes.providers.ssrf import (
    ALLOWED_PORTS,
    UnsafeDestinationError,
    resolve_and_validate,
    validate_hostname_syntax,
)

# -- Hostname syntax validation ------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "http://evil.example.com",
        "smtp://user:pass@evil.example.com",
        "file:///etc/passwd",
        "user@evil.example.com",
        "evil.example.com/../../etc/passwd",
        "evil example.com",
        "evil.example.com\r\n",
        "evil.example.com\x00",
        "",
        "   ",
        "-leading-hyphen.example.com",
    ],
)
def test_validate_hostname_syntax_rejects_malformed_input(host: str) -> None:
    with pytest.raises(UnsafeDestinationError):
        validate_hostname_syntax(host)


def test_validate_hostname_syntax_accepts_plain_hostname() -> None:
    assert validate_hostname_syntax("smtp.example.com") == "smtp.example.com"


def test_validate_hostname_syntax_accepts_ip_literal() -> None:
    assert validate_hostname_syntax("203.0.113.5") == "203.0.113.5"


# -- Port policy -----------------------------------------------------------


@pytest.mark.parametrize("port", [25, 26, 1, 22, 80, 443, 2525, 0, -1, 65536])
def test_disallowed_ports_rejected(port: int) -> None:
    with pytest.raises(UnsafeDestinationError):
        resolve_and_validate("203.0.113.5", port, dns_timeout=1.0)


def test_allowed_ports_are_exactly_587_and_465() -> None:
    assert ALLOWED_PORTS == frozenset({587, 465})


# -- IP-literal SSRF matrix (no DNS involved) ------------------------------


UNSAFE_IP_LITERALS = [
    "127.0.0.1",
    "127.5.5.5",
    "localhost",
    "0.0.0.0",
    "10.0.0.1",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.1.1",
    "169.254.169.254",  # cloud metadata
    "::1",
    "fe80::1",  # IPv6 link-local
    "fc00::1",  # IPv6 unique-local
    "fd00::1",  # IPv6 unique-local
    "::ffff:127.0.0.1",  # IPv4-mapped IPv6 loopback
    "::ffff:10.0.0.1",  # IPv4-mapped IPv6 private
    "224.0.0.1",  # multicast
    "ff02::1",  # IPv6 multicast
]


@pytest.mark.parametrize("host", UNSAFE_IP_LITERALS)
def test_unsafe_ip_literals_rejected(host: str) -> None:
    with pytest.raises(UnsafeDestinationError):
        resolve_and_validate(host, 587, dns_timeout=1.0)


def test_safe_public_ip_literal_is_pinned() -> None:
    # 8.8.8.8 (Google public DNS) is a genuinely globally-routable address,
    # not in any disallowed category -- note that RFC 5737 TEST-NET ranges
    # (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) are deliberately
    # treated as unsafe too: Python's ipaddress.is_private covers the full
    # IANA special-purpose registry, which includes them as
    # non-globally-reachable, so they are not usable as "safe" fixtures.
    target = resolve_and_validate("8.8.8.8", 587, dns_timeout=1.0)
    assert target.resolved_ip == "8.8.8.8"
    assert target.port == 587
    assert target.hostname == "8.8.8.8"


# -- Hostname resolution (mocked DNS) --------------------------------------


def _fake_getaddrinfo(records):
    def _impl(host, port, *args, **kwargs):
        return [
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
            for family, ip in records
        ]

    return _impl


def test_hostname_resolving_to_private_ip_rejected() -> None:
    with patch(
        "socket.getaddrinfo",
        new=_fake_getaddrinfo([(socket.AF_INET, "10.1.2.3")]),
    ):
        with pytest.raises(UnsafeDestinationError):
            resolve_and_validate("internal.example.com", 587, dns_timeout=1.0)


def test_hostname_resolving_to_mixed_public_and_private_rejected() -> None:
    # Even though a public address is present, ANY unsafe resolved address
    # must reject the whole hostname -- never best-effort pick the "safe" one.
    with patch(
        "socket.getaddrinfo",
        new=_fake_getaddrinfo(
            [(socket.AF_INET, "8.8.8.8"), (socket.AF_INET, "192.168.1.5")]
        ),
    ):
        with pytest.raises(UnsafeDestinationError):
            resolve_and_validate("mixed.example.com", 587, dns_timeout=1.0)


def test_hostname_resolving_to_public_ipv4_and_ipv6_accepted() -> None:
    with patch(
        "socket.getaddrinfo",
        new=_fake_getaddrinfo(
            [(socket.AF_INET, "8.8.8.8"), (socket.AF_INET6, "2001:4860:4860::8888")]
        ),
    ):
        target = resolve_and_validate("safe.example.com", 587, dns_timeout=1.0)
        assert target.resolved_ip in ("8.8.8.8", "2001:4860:4860::8888")


def test_dns_resolution_failure_rejected() -> None:
    def _raise(*args, **kwargs):
        raise socket.gaierror("Name or service not known")

    with patch("socket.getaddrinfo", new=_raise):
        with pytest.raises(UnsafeDestinationError):
            resolve_and_validate("does-not-exist.example.com", 587, dns_timeout=1.0)


# -- DNS rebinding: the pinned address must be used at connect time --------


def test_pinned_address_is_used_for_actual_socket_connect() -> None:
    """Simulates DNS rebinding: validation resolves to a safe address, but
    if the connector later re-resolved the hostname independently (instead
    of using the pinned ResolvedTarget), it could get a different,
    malicious address. Assert the low-level connector call is made with the
    PINNED IP literal, never the original hostname.
    """
    with patch(
        "socket.getaddrinfo",
        new=_fake_getaddrinfo([(socket.AF_INET, "8.8.8.8")]),
    ):
        target = resolve_and_validate("rebinding.example.com", 587, dns_timeout=1.0)

    assert target.resolved_ip == "8.8.8.8"
    assert target.hostname == "rebinding.example.com"

    from app.modules.mailboxes.providers.smtp import _PinnedSMTP

    calls: list[tuple[str, int]] = []

    def _fake_create_connection(address, timeout=None, *a, **kw):
        calls.append(address)
        raise ConnectionRefusedError("no real connection in this test")

    with patch("socket.create_connection", new=_fake_create_connection):
        with pytest.raises(ConnectionRefusedError):
            _PinnedSMTP(target, timeout=1.0)

    assert calls == [("8.8.8.8", 587)]
    # The original hostname must never appear as the connect address.
    assert all(addr[0] != "rebinding.example.com" for addr in calls)
