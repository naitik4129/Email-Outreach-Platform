"""IMAP inbound synchronization for custom-SMTP mailboxes.

SMTP itself has no inbound capability, so reply sync for an SMTP mailbox reads
the same mailbox over IMAP with credentials the user supplies. Identity is
mailbox + UIDVALIDITY + UID (RFC 9051): a UID is only meaningful inside one
UIDVALIDITY epoch, so a changed UIDVALIDITY invalidates the cursor and forces a
rescan. Rescans are deduplicated by RFC Message-ID (used as the provider message
id), so a reset never stores a message twice.
"""

from __future__ import annotations

import imaplib
import json
import re
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any, Protocol, cast

from app.core.config import Settings
from app.core.errors import AppError
from app.modules.mailboxes.providers.base import (
    ProviderInboundMessage,
    SyncPageResult,
)
from app.modules.mailboxes.providers.mime_report import (
    extract_report_text,
    is_delivery_report,
    parse_mime_bytes,
)
from app.modules.mailboxes.providers.ssrf import (
    IMAP_ALLOWED_PORTS,
    ResolvedTarget,
    resolve_and_validate,
)

# A message larger than this is fetched truncated: replies are small, and an
# oversized body must not be able to exhaust worker memory.
MAX_FETCH_BYTES = 2_000_000

_FETCH_UID_RE = re.compile(rb"UID (\d+)")
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


@dataclass(frozen=True)
class FetchedImapMessage:
    uid: int
    raw: bytes
    internal_date: datetime | None = None


class ImapSession(Protocol):
    """The few IMAP operations reply sync needs (seam for tests)."""

    def select_inbox(self) -> int:
        """Open INBOX read-only and return its UIDVALIDITY."""
        ...

    def search_uids(self, *, min_uid: int, since: date | None) -> list[int]: ...

    def fetch(self, uids: list[int]) -> list[FetchedImapMessage]: ...

    def close(self) -> None: ...


ImapConnector = Callable[[Mapping[str, Any]], ImapSession]


def imap_configured(credential: Mapping[str, Any]) -> bool:
    return bool(credential.get("imap_host") and credential.get("imap_port"))


def imap_credential(credential: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the IMAP login: dedicated IMAP credentials when supplied,
    otherwise the SMTP username/password (the common single-account case)."""
    return {
        "host": credential.get("imap_host"),
        "port": credential.get("imap_port"),
        "security_mode": credential.get("imap_security_mode") or "IMPLICIT_TLS",
        "username": credential.get("imap_username") or credential.get("username"),
        "password": credential.get("imap_password") or credential.get("password"),
    }


class _PinnedIMAP4(imaplib.IMAP4):
    """Plain IMAP4 connected to a pre-validated IP (STARTTLS is issued after)."""

    def __init__(self, target: ResolvedTarget, timeout: float) -> None:
        self._pinned_ip = target.resolved_ip
        super().__init__(target.hostname, target.port, timeout=timeout)

    def _create_socket(self, timeout: float | None) -> socket.socket:
        return socket.create_connection((self._pinned_ip, self.port), timeout)


class _PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    """Implicit-TLS IMAP connected to a pre-validated IP with hostname checks."""

    def __init__(self, target: ResolvedTarget, timeout: float) -> None:
        self._pinned_ip = target.resolved_ip
        super().__init__(
            target.hostname,
            target.port,
            ssl_context=ssl.create_default_context(),
            timeout=timeout,
        )

    def _create_socket(self, timeout: float | None) -> socket.socket:
        raw = socket.create_connection((self._pinned_ip, self.port), timeout)
        wrapped = self.ssl_context.wrap_socket(raw, server_hostname=self.host)
        return cast(socket.socket, wrapped)


class ImapLibSession:
    """Real ImapSession over imaplib, connecting only to a validated target."""

    def __init__(
        self,
        credential: Mapping[str, Any],
        *,
        resolver: Callable[..., ResolvedTarget] = resolve_and_validate,
    ) -> None:
        settings = Settings.current()
        target = resolver(
            str(credential["host"]),
            int(credential["port"]),
            dns_timeout=settings.smtp_dns_timeout_seconds,
            allowed_ports=IMAP_ALLOWED_PORTS,
        )
        timeout = settings.smtp_connect_timeout_seconds
        mode = str(credential["security_mode"])
        conn: imaplib.IMAP4
        if mode == "IMPLICIT_TLS":
            conn = _PinnedIMAP4SSL(target, timeout)
        elif mode == "STARTTLS":
            conn = _PinnedIMAP4(target, timeout)
            # Fail closed: never fall back to a plaintext login.
            conn.starttls(ssl_context=ssl.create_default_context())
        else:
            raise AppError(
                "bad_request",
                f"Unsupported IMAP security mode: {mode}",
                status_code=422,
            )
        self._conn = conn
        conn.login(str(credential["username"]), str(credential["password"]))

    def select_inbox(self) -> int:
        status, _ = self._conn.select("INBOX", readonly=True)
        if status != "OK":
            raise AppError(
                "provider_error", "IMAP INBOX could not be opened", status_code=502
            )
        status, data = self._conn.response("UIDVALIDITY")
        if not data or data[0] is None:
            raise AppError(
                "provider_error", "IMAP server gave no UIDVALIDITY", status_code=502
            )
        raw = data[0]
        return int(raw.decode() if isinstance(raw, bytes) else raw)

    def search_uids(self, *, min_uid: int, since: date | None) -> list[int]:
        criteria = ["UID", f"{min_uid}:*"]
        if since is not None:
            day = f"{since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}"
            criteria = ["SINCE", day, *criteria]
        status, data = self._conn.uid("SEARCH", *criteria)
        if status != "OK" or not data or data[0] is None:
            return []
        # "N:*" always includes the highest UID even when it is below N.
        return sorted(int(u) for u in data[0].split() if int(u) >= min_uid)

    def fetch(self, uids: list[int]) -> list[FetchedImapMessage]:
        if not uids:
            return []
        uid_set = ",".join(str(u) for u in uids)
        status, data = self._conn.uid(
            "FETCH", uid_set, f"(UID INTERNALDATE BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)"
        )
        if status != "OK":
            raise AppError("provider_error", "IMAP fetch failed", status_code=502)
        fetched: list[FetchedImapMessage] = []
        for item in data:
            if not isinstance(item, tuple):
                continue
            meta, raw = item[0], item[1]
            match = _FETCH_UID_RE.search(meta)
            if not match:
                continue
            internal: datetime | None = None
            parsed = imaplib.Internaldate2tuple(meta)
            if parsed:
                internal = datetime(*parsed[:6], tzinfo=UTC)
            fetched.append(FetchedImapMessage(int(match.group(1)), raw, internal))
        return fetched

    def close(self) -> None:
        try:
            self._conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def _cursor_state(cursor: str | None) -> tuple[int | None, int]:
    """Return (uidvalidity, last_uid); (None, 0) means "never synced"."""
    if not cursor:
        return None, 0
    try:
        data = json.loads(cursor)
        return int(data["v"]), int(data["u"])
    except (ValueError, KeyError, TypeError):
        return None, 0


def _first_text(msg: Message, preference: tuple[str, ...]) -> str | None:
    try:
        part = msg.get_body(preferencelist=preference)  # type: ignore[attr-defined]
        if part is None:
            return None
        content = part.get_content()
        return content if isinstance(content, str) else None
    except (LookupError, ValueError, KeyError):
        return None


def parse_imap_message(
    fetched: FetchedImapMessage, *, uidvalidity: int
) -> ProviderInboundMessage:
    msg = parse_mime_bytes(fetched.raw)
    headers: dict[str, str] = {}
    for name in (
        "Message-ID", "In-Reply-To", "References", "Auto-Submitted", "Precedence",
        "X-Autoreply", "Content-Type", "X-Failed-Recipients", "From",
    ):  # fmt: skip
        value = msg.get(name)
        if value:
            headers[name.lower()] = " ".join(str(value).split())

    from_name, from_addr = parseaddr(headers.get("from", ""))
    rfc_id = headers.get("message-id")
    references = headers.get("references", "").split()

    received_at = fetched.internal_date
    if received_at is None and msg.get("Date"):
        try:
            received_at = parsedate_to_datetime(str(msg["Date"]))
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            received_at = None

    auto_sub = headers.get("auto-submitted", "").lower()
    precedence = headers.get("precedence", "").lower()
    is_automated = bool(
        (auto_sub and auto_sub != "no")
        or precedence in ("bulk", "junk", "auto_reply")
        or headers.get("x-autoreply", "").lower() in ("yes", "true")
    )

    report_text = extract_report_text(msg) if is_delivery_report(msg) else None
    # Stable across UIDVALIDITY resets: the Message-ID names the message, the
    # UID does not.
    provider_id = (
        rfc_id.strip().strip("<>") if rfc_id else f"imap:{uidvalidity}:{fetched.uid}"
    )
    return ProviderInboundMessage(
        provider_message_id=provider_id,
        provider_thread_id=None,
        rfc_message_id=rfc_id,
        in_reply_to=headers.get("in-reply-to"),
        references=references,
        from_address=from_addr,
        from_name=from_name or None,
        to_addresses=[a for _, a in getaddresses([str(msg.get("To", ""))]) if a],
        cc_addresses=[a for _, a in getaddresses([str(msg.get("Cc", ""))]) if a],
        subject=str(msg.get("Subject", "") or ""),
        body_text=_first_text(msg, ("plain",)),
        body_html=_first_text(msg, ("html",)),
        received_at=received_at,
        headers=headers,
        is_automated=is_automated,
        report_text=report_text,
    )


def sync_imap_page(
    session: ImapSession,
    cursor: str | None,
    *,
    page_size: int,
    horizon_days: int,
    now: datetime | None = None,
) -> SyncPageResult:
    """Read the next page of INBOX messages after the cursor's last UID."""
    uidvalidity = session.select_inbox()
    cursor_validity, last_uid = _cursor_state(cursor)

    if cursor_validity is not None and cursor_validity != uidvalidity:
        # UIDs from the old epoch are meaningless; restart with a bounded
        # rescan (deduplicated by Message-ID downstream).
        return SyncPageResult(messages=[], resync_required=True)

    since = None
    if last_uid == 0:
        since = ((now or datetime.now(UTC)) - timedelta(days=horizon_days)).date()
    uids = session.search_uids(min_uid=last_uid + 1, since=since)
    if not uids:
        # Nothing new: still record the epoch so a later reset is detected.
        return SyncPageResult(
            messages=[],
            next_cursor=json.dumps({"v": uidvalidity, "u": last_uid}),
            has_more=False,
        )

    page_uids = uids[:page_size]
    messages = [
        parse_imap_message(item, uidvalidity=uidvalidity)
        for item in session.fetch(page_uids)
    ]
    return SyncPageResult(
        messages=messages,
        next_cursor=json.dumps({"v": uidvalidity, "u": page_uids[-1]}),
        has_more=len(uids) > page_size,
    )
