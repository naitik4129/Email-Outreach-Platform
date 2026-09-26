"""Delivery-status notification (bounce) detection and parsing.

A bounce arrives as an ordinary inbound message from the recipient's mail
system. It is not a reply: it must never stop a sequence as a "replied" lead or
count as Replied. It IS the authoritative evidence that a message was not
delivered, and it names the original message, which is how it is attached to the
right campaign email even days after the send.

Only facts present in the notification are extracted; nothing is guessed. The
hard/soft split follows the RFC 3463 status class the remote server reported
(5.x.x permanent, 4.x.x transient) and falls back to the DSN ``Action`` field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MAX_DIAGNOSTIC_CHARS = 300

_DSN_SENDER_PREFIXES = ("mailer-daemon@", "postmaster@")
_DSN_SUBJECT_MARKERS = (
    "undeliverable",
    "delivery status notification",
    "delivery failure",
    "mail delivery failed",
    "mail delivery system",
    "returned mail",
    "failure notice",
    "undelivered mail",
    "message not delivered",
    "delivery has failed",
)

_FINAL_RECIPIENT_RE = re.compile(
    r"(?im)^\s*(?:Final|Original)-Recipient:\s*(?:rfc822\s*;)?\s*<?([^\s<>;,]+@[^\s<>;,]+)>?"
)
_ACTION_RE = re.compile(r"(?im)^\s*Action:\s*([a-z-]+)")
_STATUS_HEADER_RE = re.compile(r"(?im)^\s*Status:\s*([245])\.(\d{1,3})\.(\d{1,3})")
_STATUS_ANYWHERE_RE = re.compile(r"(?<![\d.])([245])\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_DIAGNOSTIC_RE = re.compile(r"(?im)^\s*Diagnostic-Code:\s*(?:smtp\s*;)?\s*(.+)$")
_ORIGINAL_MESSAGE_ID_RE = re.compile(r"(?im)^\s*Message-I[Dd]:\s*(<[^>\s]+>)")
_ORIGINAL_REFERENCE_RE = re.compile(r"(?im)^\s*(?:In-Reply-To|References):\s*(.+)$")
_ANGLE_ID_RE = re.compile(r"<[^>\s]+>")
_EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class DeliveryReport:
    """What a bounce notification says about the message it refers to."""

    bounce_type: str  # HARD | SOFT | UNKNOWN
    failed_recipient: str | None
    status_code: str | None
    diagnostic: str | None
    # Message-ID of the bounced email itself (from its returned headers).
    original_message_ids: list[str] = field(default_factory=list)
    # In-Reply-To/References of the returned headers: weaker, earlier messages.
    referenced_message_ids: list[str] = field(default_factory=list)


def looks_like_delivery_report(
    *, from_address: str, subject: str, headers: dict[str, str]
) -> bool:
    """Cheap header-level test for "this may be a bounce"."""
    sender = (from_address or "").strip().lower()
    content_type = headers.get("content-type", "").lower()
    if "multipart/report" in content_type or "delivery-status" in content_type:
        return True
    if "x-ms-exchange-message-is-ndr" in {k.lower() for k in headers}:
        return True
    if sender.startswith(_DSN_SENDER_PREFIXES):
        return True
    subject_looks_like_ndr = any(
        marker in (subject or "").lower() for marker in _DSN_SUBJECT_MARKERS
    )
    return subject_looks_like_ndr and (
        headers.get("auto-submitted", "").lower() not in ("", "no")
        or sender.startswith(("microsoftexchange", "no-reply@", "noreply@"))
    )


def _classify(status_class: str | None, subcode: str | None, action: str | None) -> str:
    if status_class == "5":
        # 5.2.2 (mailbox full) is reported as permanent but is recoverable.
        return "SOFT" if subcode == "2" else "HARD"
    if status_class == "4":
        return "SOFT"
    if status_class == "2":
        return "UNKNOWN"
    if action == "failed":
        return "HARD"
    if action in ("delayed", "delivered", "relayed", "expanded"):
        return "SOFT" if action == "delayed" else "UNKNOWN"
    return "UNKNOWN"


def parse_delivery_report(
    *,
    from_address: str,
    subject: str,
    headers: dict[str, str],
    content_text: str | None,
    report_text: str | None,
) -> DeliveryReport | None:
    """Return the parsed report, or None if the message is not a failure/delay
    notification (a successful-delivery DSN is not a bounce)."""
    if not looks_like_delivery_report(
        from_address=from_address, subject=subject, headers=headers
    ):
        return None

    # The machine-readable part is authoritative; the human text is a fallback
    # (Exchange/Outlook NDRs often carry everything only in the body).
    structured = report_text or ""
    body = content_text or ""
    haystacks = [structured, body] if structured else [body]

    action_match = _ACTION_RE.search(structured) or _ACTION_RE.search(body)
    action = action_match.group(1).lower() if action_match else None

    status_match = _STATUS_HEADER_RE.search(structured)
    if status_match is None:
        for text in haystacks:
            status_match = _STATUS_ANYWHERE_RE.search(text)
            if status_match:
                break
    status_class = status_match.group(1) if status_match else None
    subcode = status_match.group(2) if status_match else None
    status_code = (
        ".".join(status_match.groups()) if status_match else None
    )

    bounce_type = _classify(status_class, subcode, action)
    if bounce_type == "UNKNOWN" and status_class == "2":
        return None  # a "delivered" receipt, not a failure
    if action in ("delivered", "relayed", "expanded"):
        return None

    recipient = None
    for candidate in (
        _FINAL_RECIPIENT_RE.search(structured),
        _FINAL_RECIPIENT_RE.search(body),
    ):
        if candidate:
            recipient = candidate.group(1).lower()
            break
    if recipient is None and headers.get("x-failed-recipients"):
        found = _EMAIL_RE.search(headers["x-failed-recipients"])
        recipient = found.group(0).lower() if found else None

    diagnostic_match = _DIAGNOSTIC_RE.search(structured)
    diagnostic = (
        " ".join(diagnostic_match.group(1).split())[:_MAX_DIAGNOSTIC_CHARS]
        if diagnostic_match
        else None
    )

    ids: list[str] = []
    referenced: list[str] = []
    for text in haystacks:
        for match in _ORIGINAL_MESSAGE_ID_RE.finditer(text):
            if match.group(1) not in ids:
                ids.append(match.group(1))
        for match in _ORIGINAL_REFERENCE_RE.finditer(text):
            for ref in _ANGLE_ID_RE.findall(match.group(1)):
                if ref not in referenced and ref not in ids:
                    referenced.append(ref)

    return DeliveryReport(
        bounce_type=bounce_type,
        failed_recipient=recipient,
        status_code=status_code,
        diagnostic=diagnostic,
        original_message_ids=ids,
        referenced_message_ids=referenced,
    )
