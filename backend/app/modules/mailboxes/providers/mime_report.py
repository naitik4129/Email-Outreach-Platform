from __future__ import annotations

from email import policy
from email.message import Message
from email.parser import BytesParser

# A delivery-status report only needs the per-recipient fields and the headers
# of the message that bounced; bounding the text keeps an attacker-controlled
# NDR from inflating the sync page.
_MAX_REPORT_TEXT_CHARS = 20_000

_DSN_CONTENT_TYPES = {"message/delivery-status", "message/global-delivery-status"}
_RETURNED_HEADER_TYPES = {"text/rfc822-headers"}
_RETURNED_MESSAGE_TYPES = {"message/rfc822", "message/global"}


def parse_mime_bytes(raw: bytes) -> Message:
    return BytesParser(policy=policy.default).parsebytes(raw)


def is_delivery_report(msg: Message) -> bool:
    """True for a multipart/report whose report-type is delivery-status."""
    if msg.get_content_type() != "multipart/report":
        return False
    report_type = str(msg.get_param("report-type") or "").lower()
    return report_type in ("delivery-status", "")


def extract_report_text(msg: Message) -> str | None:
    """Collect the machine-readable parts of a delivery-status report.

    Returns the delivery-status fields (Final-Recipient, Action, Status,
    Diagnostic-Code) and the headers of the returned original message
    (Message-ID, In-Reply-To), which is what associates a bounce with the
    outbound email it refers to.
    """
    chunks: list[str] = []
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type in _DSN_CONTENT_TYPES:
            # The payload of a delivery-status part is a list of header blocks.
            payload = part.get_payload()
            if isinstance(payload, list):
                for block in payload:
                    chunks.append(_headers_as_text(block))
            elif isinstance(payload, str):
                chunks.append(payload)
        elif content_type in _RETURNED_HEADER_TYPES:
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                chunks.append(payload.decode("utf-8", errors="replace"))
            elif isinstance(payload, str):
                chunks.append(payload)
        elif content_type in _RETURNED_MESSAGE_TYPES:
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                chunks.append(_headers_as_text(payload[0]))
    text = "\n".join(c for c in chunks if c).strip()
    return text[:_MAX_REPORT_TEXT_CHARS] if text else None


def _headers_as_text(message: object) -> str:
    if not isinstance(message, Message):
        return ""
    return "\n".join(f"{name}: {value}" for name, value in message.items())
