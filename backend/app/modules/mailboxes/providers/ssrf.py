"""SSRF-safe SMTP network helper.

This module is the single, release-blocking implementation of "is it safe to
open a TCP/TLS connection to this user-supplied SMTP host/port". It is used
identically by the connection-validation path (``SmtpProvider.
validate_connection``) and the actual message-send path (``SmtpProvider.
send_message``) via the shared ``_connect_and_auth`` helper in ``smtp.py`` --
never duplicated, never diverging.

Design constraints (see docs/security/SECURITY_ARCHITECTURE.md and
docs/architecture/PROVIDER_ARCHITECTURE.md "SMTP network boundary"):

- Only approved submission ports/modes: 587 with mandatory STARTTLS, 465
  with implicit TLS. No arbitrary port range.
- Host input must be a bare hostname/IP literal, never a URL/scheme/embedded
  credentials.
- Every resolved IPv4 AND IPv6 address is validated (not just the first
  one); a hostname resolving to any unsafe address is rejected outright,
  even if other resolved addresses are public.
- Exactly one validated address is pinned and used for the actual socket
  connection -- the hostname is never re-resolved after validation, closing
  the DNS-rebinding TOCTOU window. TLS/STARTTLS still verifies the
  certificate against the ORIGINAL hostname for correct SNI/cert-hostname
  matching.
- No environment-variable bypass of any kind exists in this module. The
  only test seam is constructor-level dependency injection on
  ``SmtpProvider`` (see ``smtp.py``), which production code never uses.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from app.core.errors import AppError

ALLOWED_PORTS: frozenset[int] = frozenset({587, 465})

# Defense-in-depth documentation of well-known cloud metadata endpoints.
# These are already covered by is_link_local for IPv4 in the common case
# (169.254.169.254), but are checked explicitly for clarity and to cover
# provider-specific metadata addresses that may not fall under a single
# standard range.
_METADATA_DENYLIST: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS/Azure/GCP/DigitalOcean metadata
        "fd00:ec2::254",  # AWS IMDSv2 IPv6 metadata endpoint
    }
)

_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="smtp-dns")


class UnsafeDestinationError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("unsafe_destination", message, status_code=422)


@dataclass(frozen=True)
class ResolvedTarget:
    """A validated, pinned connection target.

    ``hostname`` is the ORIGINAL user-supplied hostname, used only for
    TLS SNI/certificate-hostname verification. ``resolved_ip`` is the
    single validated IP address the socket must actually connect to.
    """

    hostname: str
    port: int
    resolved_ip: str
    address_family: int  # socket.AF_INET or socket.AF_INET6


def validate_hostname_syntax(host: str) -> str:
    """Reject anything that isn't a bare hostname or IP literal.

    Rejects URI schemes, embedded credentials, control characters,
    whitespace, and path/query fragments -- SMTP host configuration is a
    hostname/IP, never a URL.
    """
    if not host or not host.strip() or host != host.strip():
        raise UnsafeDestinationError(
            "SMTP host must not be empty or contain surrounding whitespace"
        )

    if any(ord(c) < 32 or ord(c) == 127 for c in host):
        raise UnsafeDestinationError("SMTP host contains control characters")

    if any(c in host for c in (" ", "\t")):
        raise UnsafeDestinationError("SMTP host must not contain whitespace")

    lowered = host.lower()
    for scheme_marker in ("://", "smtp:", "smtps:", "http:", "https:", "file:"):
        if scheme_marker in lowered:
            raise UnsafeDestinationError("SMTP host must not include a URI scheme")

    if "@" in host:
        raise UnsafeDestinationError("SMTP host must not contain embedded credentials")

    if "/" in host or "\\" in host or "?" in host or "#" in host:
        raise UnsafeDestinationError("SMTP host must not contain path/query characters")

    # Bracketed IPv6 literal, e.g. "[::1]" -- strip brackets for validation.
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host

    try:
        ipaddress.ip_address(candidate)
        return host
    except ValueError:
        pass

    # Bare hostname: RFC 1123 label syntax, dot-separated.
    labels = candidate.rstrip(".").split(".")
    if not labels or len(candidate) > 253:
        raise UnsafeDestinationError("SMTP host is not a valid hostname")
    for label in labels:
        if not label or len(label) > 63:
            raise UnsafeDestinationError("SMTP host is not a valid hostname")
        if not all(c.isalnum() or c == "-" for c in label):
            raise UnsafeDestinationError("SMTP host is not a valid hostname")
        if label.startswith("-") or label.endswith("-"):
            raise UnsafeDestinationError("SMTP host is not a valid hostname")

    return host


def is_unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return is_unsafe_ip(mapped)

    if str(ip) in _METADATA_DENYLIST:
        return True

    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _resolve_addrinfo(host: str, port: int) -> list[tuple[int, str]]:
    """Return [(address_family, ip_str), ...] for every resolved address."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeDestinationError(f"Could not resolve SMTP host: {host}") from exc

    results: list[tuple[int, str]] = []
    for family, _type, _proto, _canonname, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            results.append((int(family), str(sockaddr[0])))
    if not results:
        raise UnsafeDestinationError(f"Could not resolve SMTP host: {host}")
    return results


def resolve_and_validate(
    host: str,
    port: int,
    *,
    dns_timeout: float = 5.0,
) -> ResolvedTarget:
    """Resolve ``host``, validate every resolved address, and pin one.

    Raises ``UnsafeDestinationError`` if the port isn't approved, the host
    syntax is invalid, resolution fails, or ANY resolved address (IPv4 or
    IPv6) falls in a disallowed range. Never silently picks a "safe" address
    out of a mixed public/private result set -- the whole hostname is
    rejected.
    """
    validate_hostname_syntax(host)

    if port not in ALLOWED_PORTS:
        raise UnsafeDestinationError(
            f"SMTP port {port} is not permitted; "
            f"only {sorted(ALLOWED_PORTS)} are allowed"
        )

    future = _DNS_EXECUTOR.submit(_resolve_addrinfo, host, port)
    try:
        addresses = future.result(timeout=dns_timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        raise UnsafeDestinationError("DNS resolution timed out") from exc

    pinned: tuple[int, str] | None = None
    for family, ip_str in addresses:
        ip_obj = ipaddress.ip_address(ip_str)
        if is_unsafe_ip(ip_obj):
            raise UnsafeDestinationError(
                "SMTP host resolves to a disallowed network address"
            )
        if pinned is None:
            pinned = (family, ip_str)

    assert pinned is not None  # addresses is non-empty and loop always sets it
    family, resolved_ip = pinned
    return ResolvedTarget(
        hostname=host, port=port, resolved_ip=resolved_ip, address_family=family
    )
