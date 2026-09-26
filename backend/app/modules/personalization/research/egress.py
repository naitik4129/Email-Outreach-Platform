"""SSRF-safe HTTPS fetch for research (ADR-0013, SECURITY_ARCHITECTURE).

Policy: https only, port 443 only, no credentials in the URL, every resolved
address must be public (any unsafe answer rejects the host), one validated
address is pinned for the connection (so DNS cannot change between validation
and connect) while TLS still verifies the ORIGINAL hostname via SNI, redirects
are followed manually (max 3, same host only, each hop re-validated), no
proxy/cookies/credentials, a total deadline, a streamed byte cap and a content
type allow-list. Nothing here logs URLs' query strings or page content.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.modules.mailboxes.providers.ssrf import is_unsafe_ip

USER_AGENT = "OutlyResearchBot/1.0 (+company-website summary for personalization)"
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
TOTAL_DEADLINE_SECONDS = 8.0
DNS_TIMEOUT_SECONDS = 3.0
_ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="research-dns")

Resolver = Callable[[str], list[str]]


class FetchBlocked(Exception):
    """Refused by policy (unsafe destination, bad scheme/port, off-site
    redirect, wrong content type). Safe to cache negatively."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FetchFailed(Exception):
    """Network/server failure. Best-effort: the caller degrades, never fails."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class FetchResponse:
    status_code: int
    final_url: str
    content_type: str
    body: bytes


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return [
        str(info[4][0])
        for info in infos
        if info[0] in (socket.AF_INET, socket.AF_INET6)
    ]


def _registrable_key(host: str) -> str:
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def validate_url(url: str) -> tuple[str, str, str]:
    """(host, path_with_query, normalized_url) or FetchBlocked."""
    if not url or any(ord(c) < 33 or ord(c) == 127 for c in url) or len(url) > 2048:
        raise FetchBlocked("invalid_url")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise FetchBlocked("scheme_not_https")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise FetchBlocked("credentials_in_url")
    try:
        port = parts.port
    except ValueError as exc:
        raise FetchBlocked("invalid_port") from exc
    if port not in (None, 443):
        raise FetchBlocked("port_not_allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or len(host) > 253:
        raise FetchBlocked("invalid_host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # IP literals cannot present a valid certificate for the name and are the
        # classic SSRF vector; company sites are always hostnames.
        raise FetchBlocked("ip_literal_host")
    for label in host.split("."):
        if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
            raise FetchBlocked("invalid_host")
        if not all(c.isalnum() or c == "-" for c in label):
            raise FetchBlocked("invalid_host")
    if "." not in host:
        raise FetchBlocked("invalid_host")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return host, path, f"https://{host}{path}"


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        transport: httpx.BaseTransport | None = None,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        total_deadline_seconds: float = TOTAL_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver or default_resolver
        self._transport = transport
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._deadline = total_deadline_seconds
        self._clock = clock

    def _pin_address(self, host: str) -> str:
        future = _DNS_EXECUTOR.submit(self._resolver, host)
        try:
            addresses = future.result(timeout=DNS_TIMEOUT_SECONDS)
        except FutureTimeoutError as exc:
            future.cancel()
            raise FetchFailed("dns_timeout") from exc
        except OSError as exc:
            raise FetchFailed("dns_failure") from exc
        if not addresses:
            raise FetchFailed("dns_failure")
        pinned: str | None = None
        for raw in addresses:
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise FetchBlocked("unsafe_destination") from exc
            # Any unsafe answer rejects the whole host (no picking a public one
            # out of a mixed result set).
            if is_unsafe_ip(ip):
                raise FetchBlocked("unsafe_destination")
            if pinned is None:
                pinned = raw
        assert pinned is not None
        return pinned

    def fetch(self, url: str) -> FetchResponse:
        started = self._clock()
        current_url = url
        original_host: str | None = None
        for hop in range(self._max_redirects + 1):
            host, path, normalized = validate_url(current_url)
            if original_host is None:
                original_host = host
            elif _registrable_key(host) != _registrable_key(original_host):
                raise FetchBlocked("offsite_redirect")
            remaining = self._deadline - (self._clock() - started)
            if remaining <= 0:
                raise FetchFailed("deadline_exceeded")
            ip = self._pin_address(host)
            response = self._request_once(host, path, ip, remaining, started)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.location
                if not location:
                    raise FetchFailed("redirect_without_location")
                if hop == self._max_redirects:
                    raise FetchBlocked("too_many_redirects")
                current_url = urljoin(normalized, location)
                continue
            return FetchResponse(
                status_code=response.status_code,
                final_url=normalized,
                content_type=response.content_type,
                body=response.body,
            )
        raise FetchBlocked("too_many_redirects")  # pragma: no cover

    def _request_once(
        self, host: str, path: str, ip: str, remaining: float, started: float
    ) -> _RawResponse:
        target_host = f"[{ip}]" if ":" in ip else ip
        timeout = httpx.Timeout(
            min(remaining, self._deadline), connect=min(remaining, 4.0)
        )
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,text/plain;q=0.9",
                },
            ) as client:
                with client.stream(
                    "GET",
                    f"https://{target_host}{path}",
                    headers={"Host": host},
                    # Verify the certificate and send SNI for the ORIGINAL name even
                    # though the socket connects to the pinned address.
                    extensions={"sni_hostname": host},
                ) as response:
                    status = response.status_code
                    location = response.headers.get("location")
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if status in (301, 302, 303, 307, 308):
                        return _RawResponse(status, content_type, b"", location)
                    if status == 200 and content_type not in _ALLOWED_CONTENT_TYPES:
                        raise FetchBlocked("unsupported_content_type")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) >= self._max_bytes:
                            del body[self._max_bytes :]
                            break
                        if self._clock() - started > self._deadline:
                            raise FetchFailed("deadline_exceeded")
                    return _RawResponse(status, content_type, bytes(body), location)
        except httpx.TimeoutException as exc:
            raise FetchFailed("timeout") from exc
        except httpx.HTTPError as exc:
            raise FetchFailed("network_error") from exc


@dataclass(frozen=True)
class _RawResponse:
    status_code: int
    content_type: str
    body: bytes
    location: str | None
