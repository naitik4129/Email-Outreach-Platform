from __future__ import annotations

import base64
import hashlib
import hmac
from uuid import UUID

_MAC_BYTES = 16
_ID_BYTES = 32  # workspace_id + message_id
# Domain-separates open tokens from any other HMAC made with the same key.
_CONTEXT = b"outly-open-v1|"


def _mac(payload: bytes, key: str) -> bytes:
    digest = hmac.new(key.encode("utf-8"), _CONTEXT + payload, hashlib.sha256)
    return digest.digest()[:_MAC_BYTES]


def make_open_token(workspace_id: UUID, message_id: UUID, key: str) -> str:
    """Opaque token naming one outbound message. It carries no personal data and
    cannot be forged or reused for another message without the signing key."""
    payload = workspace_id.bytes + message_id.bytes
    token = base64.urlsafe_b64encode(payload + _mac(payload, key))
    return token.rstrip(b"=").decode("ascii")


def parse_open_token(token: str, key: str) -> tuple[UUID, UUID] | None:
    """Return (workspace_id, message_id) for a valid token, else None.

    Verified without touching the database, so invalid or forged requests cost
    nothing and can never write.
    """
    if not key or not token or len(token) > 128:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) != _ID_BYTES + _MAC_BYTES:
        return None
    payload, mac = raw[:_ID_BYTES], raw[_ID_BYTES:]
    if not hmac.compare_digest(mac, _mac(payload, key)):
        return None
    return UUID(bytes=payload[:16]), UUID(bytes=payload[16:])
