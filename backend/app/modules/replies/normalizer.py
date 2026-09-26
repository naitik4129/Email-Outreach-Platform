from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parseaddr
from html.parser import HTMLParser

from app.modules.mailboxes.providers.base import ProviderInboundMessage
from app.modules.replies.dsn import parse_delivery_report
from app.modules.replies.schemas import InboundClassification, NormalizedInboundMessage
from app.modules.templates.sanitizer import sanitize_html_preview

# Limits matching database check constraints
MAX_SUBJECT_LENGTH = 500
MAX_CONTENT_TEXT_LENGTH = 50000
MAX_IN_REPLY_TO_BYTES = 4096
MAX_REFERENCES_BYTES = 16384
MAX_PROVIDER_ID_LENGTH = 1024

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CLEAN_RFC_ID_RE = re.compile(r"<[^>]+>")


class _HTMLToTextExtractor(HTMLParser):
    """Safe, lightweight HTML-to-text converter using stdlib HTMLParser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "head", "title", "meta", "link", "iframe", "object"):
            self._skip_depth += 1
        elif tag_lower in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "head", "title", "meta", "link", "iframe", "object"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif tag_lower in ("p", "div", "tr"):
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.text_parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        lines = [line.strip() for line in raw.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract_text_from_html(html_content: str | None) -> str | None:
    if not html_content:
        return None
    try:
        extractor = _HTMLToTextExtractor()
        extractor.feed(html_content)
        extractor.close()
        text = extractor.get_text()
        return text if text else None
    except Exception:
        # Fallback to regex-based tag stripping on parser error
        stripped = re.sub(r"<[^>]+>", " ", html_content)
        return " ".join(stripped.split()) if stripped.strip() else None


def sanitize_header_value(value: str | None, max_length: int | None = None) -> str | None:
    if not value:
        return None
    # Strip null bytes and control characters
    cleaned = _CONTROL_CHARS_RE.sub("", value).strip()
    if max_length and len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned if cleaned else None


def extract_rfc_message_ids(header_value: str | None) -> list[str]:
    """Safely extract all RFC Message-IDs (<...>) from an In-Reply-To or References header."""
    if not header_value:
        return []
    cleaned = _CONTROL_CHARS_RE.sub("", header_value)
    # Extract bracketed IDs
    bracketed = _CLEAN_RFC_ID_RE.findall(cleaned)
    # Remove bracketed IDs to find any unbracketed tokens
    remaining = _CLEAN_RFC_ID_RE.sub(" ", cleaned)
    tokens = [t.strip(",; ") for t in remaining.split() if t.strip(",; ")]
    unbracketed = [t for t in tokens if "@" in t or len(t) > 3]

    seen = set()
    result = []
    for item in bracketed + unbracketed:
        formatted = f"<{item.strip('<> ')}>"
        if formatted not in seen and len(formatted) > 3:
            seen.add(formatted)
            result.append(formatted)
    return result


def classify_inbound_message(
    *,
    subject: str,
    headers: dict[str, str],
    content_text: str | None = None,
    is_automated_hint: bool = False,
) -> str:
    """Classify inbound email into HUMAN_REPLY, OUT_OF_OFFICE, or AUTOMATED."""
    sub_lower = subject.lower()
    auto_sub = headers.get("auto-submitted", "").lower()
    precedence = headers.get("precedence", "").lower()
    x_autoreply = headers.get("x-autoreply", "").lower()

    # Out of office detection
    if any(
        phrase in sub_lower
        for phrase in (
            "out of office",
            "automatic reply:",
            "auto reply:",
            "autoreply:",
            "away from the office",
            "vacation response",
            "out of the office",
            "absenza dall'ufficio",
            "abwesend",
        )
    ):
        return InboundClassification.OUT_OF_OFFICE.value

    # Auto-submitted / bulk / automated notification detection
    if (
        (auto_sub and auto_sub != "no")
        or precedence in ("bulk", "junk", "auto_reply")
        or x_autoreply in ("yes", "true")
        or is_automated_hint
    ):
        # If auto-submitted is auto-replied and text suggests OOO
        if auto_sub == "auto-replied" or "auto" in auto_sub:
            if content_text and any(
                phrase in content_text.lower()
                for phrase in ("out of office", "returning on", "away from my email", "annual leave")
            ):
                return InboundClassification.OUT_OF_OFFICE.value
            return InboundClassification.AUTOMATED.value
        return InboundClassification.AUTOMATED.value

    return InboundClassification.HUMAN_REPLY.value


def normalize_inbound_message(
    provider_msg: ProviderInboundMessage,
    *,
    provider: str,
) -> NormalizedInboundMessage:
    """Transform raw provider inbound message into normalized, secure internal representation."""
    provider_message_id = sanitize_header_value(
        provider_msg.provider_message_id, max_length=MAX_PROVIDER_ID_LENGTH
    )
    if not provider_message_id:
        raise ValueError("Inbound message must have a valid provider_message_id")

    provider_thread_id = sanitize_header_value(provider_msg.provider_thread_id, max_length=1024)

    # Sanitize and extract RFC IDs
    rfc_message_id = sanitize_header_value(provider_msg.rfc_message_id, max_length=1024)
    if rfc_message_id and not rfc_message_id.startswith("<") and "@" in rfc_message_id:
        rfc_message_id = f"<{rfc_message_id.strip()}>"

    # In-Reply-To: extract first valid ID
    in_reply_to_ids = extract_rfc_message_ids(provider_msg.in_reply_to)
    in_reply_to = in_reply_to_ids[0] if in_reply_to_ids else None

    # References: combine all references from header and provider msg
    raw_refs = list(provider_msg.references)
    if "references" in provider_msg.headers:
        raw_refs.extend(extract_rfc_message_ids(provider_msg.headers["references"]))
    all_refs: list[str] = []
    for r in raw_refs:
        for ref_id in extract_rfc_message_ids(r):
            if ref_id not in all_refs:
                all_refs.append(ref_id)

    # Subject sanitization
    subject = sanitize_header_value(provider_msg.subject, max_length=MAX_SUBJECT_LENGTH) or ""

    # Participant normalization
    from_name, from_email = parseaddr(provider_msg.from_address)
    if not from_email:
        from_email = provider_msg.from_address.strip()
    from_email = _CONTROL_CHARS_RE.sub("", from_email).lower()
    from_name = sanitize_header_value(provider_msg.from_name or from_name, max_length=200)

    to_addrs = [
        _CONTROL_CHARS_RE.sub("", a).lower()
        for a in provider_msg.to_addresses
        if a and "@" in a
    ]
    cc_addrs = [
        _CONTROL_CHARS_RE.sub("", a).lower()
        for a in provider_msg.cc_addresses
        if a and "@" in a
    ]
    bcc_addrs = [
        _CONTROL_CHARS_RE.sub("", a).lower()
        for a in provider_msg.bcc_addresses
        if a and "@" in a
    ]

    # HTML and Text sanitization
    clean_html = sanitize_html_preview(provider_msg.body_html or "")
    content_html = clean_html if clean_html else None

    content_text = provider_msg.body_text
    if not content_text and content_html:
        content_text = extract_text_from_html(content_html)

    if content_text:
        content_text = _CONTROL_CHARS_RE.sub("", content_text)
        if len(content_text) > MAX_CONTENT_TEXT_LENGTH:
            content_text = content_text[:MAX_CONTENT_TEXT_LENGTH]

    # Classification
    delivery_report = parse_delivery_report(
        from_address=from_email,
        subject=subject,
        headers=provider_msg.headers,
        content_text=content_text,
        report_text=provider_msg.report_text,
    )
    if delivery_report is not None:
        classification = InboundClassification.BOUNCE.value
    else:
        classification = classify_inbound_message(
            subject=subject,
            headers=provider_msg.headers,
            content_text=content_text,
            is_automated_hint=provider_msg.is_automated,
        )

    received_at = provider_msg.received_at or datetime.now(UTC)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)

    return NormalizedInboundMessage(
        provider=provider.upper(),
        provider_message_id=provider_message_id,
        provider_thread_id=provider_thread_id,
        rfc_message_id=rfc_message_id,
        in_reply_to=in_reply_to,
        references=all_refs,
        from_address=from_email,
        from_name=from_name,
        to_addresses=to_addrs,
        cc_addresses=cc_addrs,
        bcc_addresses=bcc_addrs,
        subject=subject,
        content_text=content_text,
        content_html=content_html,
        received_at=received_at,
        headers=provider_msg.headers,
        is_automated=(classification != InboundClassification.HUMAN_REPLY.value),
        classification=classification,
        delivery_report=delivery_report,
    )
