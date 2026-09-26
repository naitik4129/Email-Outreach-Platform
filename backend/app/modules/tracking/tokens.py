from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID

_MAC_BYTES = 16
_ID_BYTES = 32  # workspace_id + message_id
_TIME_BYTES = 4  # unsigned big-endian epoch seconds (v2 only)
# Domain-separates open tokens from any other HMAC made with the same key.
_CONTEXT = b"outly-open-v1|"


class OpenToken(NamedTuple):
    workspace_id: UUID
    message_id: UUID
    # When the email was sent. None for v1 tokens, which carry no send time.
    sent_at: datetime | None


def _mac(payload: bytes, key: str) -> bytes:
    digest = hmac.new(key.encode("utf-8"), _CONTEXT + payload, hashlib.sha256)
    return digest.digest()[:_MAC_BYTES]


def make_open_token(
    workspace_id: UUID,
    message_id: UUID,
    key: str,
    sent_at: datetime | None = None,
) -> str:
    """Opaque token naming one outbound message. It carries no personal data and
    cannot be forged or reused for another message without the signing key.

    With ``sent_at`` (v2) the send time is inside the signed payload, so the
    public endpoint can tell an automated delivery-time fetch from a later
    human open without reading the database.
    """
    payload = workspace_id.bytes + message_id.bytes
    if sent_at is not None:
        payload += int(sent_at.timestamp()).to_bytes(_TIME_BYTES, "big")
    token = base64.urlsafe_b64encode(payload + _mac(payload, key))
    return token.rstrip(b"=").decode("ascii")


def parse_open_token(token: str, key: str) -> OpenToken | None:
    """Return the token contents when valid, else None.

    Verified without touching the database, so invalid or forged requests cost
    nothing and can never write. Accepts v1 tokens (already in sent emails).
    """
    if not key or not token or len(token) > 128:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) == _ID_BYTES + _MAC_BYTES:
        payload_len = _ID_BYTES
    elif len(raw) == _ID_BYTES + _TIME_BYTES + _MAC_BYTES:
        payload_len = _ID_BYTES + _TIME_BYTES
    else:
        return None
    payload, mac = raw[:payload_len], raw[payload_len:]
    if not hmac.compare_digest(mac, _mac(payload, key)):
        return None
    sent_at = (
        datetime.fromtimestamp(int.from_bytes(payload[_ID_BYTES:], "big"), UTC)
        if payload_len > _ID_BYTES
        else None
    )
    return OpenToken(UUID(bytes=payload[:16]), UUID(bytes=payload[16:32]), sent_at)
