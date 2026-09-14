from __future__ import annotations

import re
from email.message import EmailMessage

from app.core.errors import AppError
from app.modules.mailboxes.providers.base import OutboundMessageEnvelope

_CRLF_PATTERN = re.compile(r"[\r\n]")


def validate_header_value(name: str, value: str | None) -> None:
    """Ensure no carriage return or newline characters exist in header values.

    Shared by every provider adapter (Gmail, Microsoft Graph, SMTP) so the
    CRLF/header-injection guard is defined once and applied identically
    regardless of transport (raw MIME, Graph JSON, or SMTP DATA).
    """
    if value and _CRLF_PATTERN.search(value):
        raise AppError(
            "header_injection",
            f"Header injection detected: newline characters not permitted in {name}",
            status_code=422,
        )


def build_rfc5322_message(envelope: OutboundMessageEnvelope) -> EmailMessage:
    """Build a safe RFC 5322 MIME message from a provider-neutral envelope.

    Callers must run ``validate_header_value`` over the envelope's
    recipient/sender/subject fields before calling this -- it does not
    re-validate, it only assembles.
    """
    msg = EmailMessage()
    if envelope.from_name:
        msg["From"] = f"{envelope.from_name} <{envelope.from_address}>"
    else:
        msg["From"] = envelope.from_address
    msg["To"] = envelope.to_address
    msg["Subject"] = envelope.subject

    if envelope.rfc_message_id:
        rfc_id = envelope.rfc_message_id.strip("<>")
        msg["Message-ID"] = f"<{rfc_id}>"

    if envelope.body_html:
        msg.set_content(
            envelope.body_text or "This message requires an HTML-capable email client."
        )
        msg.add_alternative(envelope.body_html, subtype="html")
    else:
        msg.set_content(envelope.body_text or "")

    return msg
