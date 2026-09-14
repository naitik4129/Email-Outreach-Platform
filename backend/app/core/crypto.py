from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings
from app.core.errors import AppError


def _resolve_master_key(raw_key: str | None = None) -> bytes:
    """Resolve a 32-byte AES-256 key from settings or explicit argument.

    Supports 64-character hex strings or exact 32-byte ASCII keys. If empty in
    development/test, uses a deterministic derived key from the app name so
    test suites can run without manual KMS setup.
    """
    if raw_key is None:
        raw_key = Settings.current().mailbox_encryption_key

    if not raw_key:
        # Fallback deterministic key for local dev / tests
        return hashlib.sha256(b"email-outreach-platform-default-dev-key").digest()

    raw_key = raw_key.strip()
    if len(raw_key) == 64:
        try:
            return bytes.fromhex(raw_key)
        except ValueError:
            pass

    if len(raw_key.encode("utf-8")) == 32:
        return raw_key.encode("utf-8")

    # Hash any other arbitrary secret string to guarantee 32 bytes
    return hashlib.sha256(raw_key.encode("utf-8")).digest()


def credential_aad(workspace_id: UUID, mailbox_id: UUID, provider: str) -> bytes:
    return f"{workspace_id}:{mailbox_id}:{provider}".encode("utf-8")


def verifier_aad(workspace_id: UUID, actor_id: UUID, provider: str) -> bytes:
    return f"{workspace_id}:{actor_id}:{provider}".encode("utf-8")


def encrypt_credentials(
    payload: dict[str, Any],
    workspace_id: UUID,
    mailbox_id: UUID,
    provider: str,
    key_id: str | None = None,
) -> tuple[bytes, str, bytes]:
    """Encrypt a credential dictionary using AES-256-GCM.

    Returns:
        (ciphertext, key_id, nonce)
    """
    if key_id is None:
        key_id = Settings.current().mailbox_encryption_key_id

    key = _resolve_master_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    aad = credential_aad(workspace_id, mailbox_id, provider)

    data = json.dumps(payload).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, data, aad)
    return ciphertext, key_id, nonce


def decrypt_credentials(
    ciphertext: bytes,
    nonce: bytes,
    workspace_id: UUID,
    mailbox_id: UUID,
    provider: str,
    key_id: str | None = None,
) -> dict[str, Any]:
    """Decrypt and verify credentials with associated data.

    Fails safely if the ciphertext, nonce, or associated data (workspace,
    mailbox, or provider) do not match the authentication tag.
    """
    key = _resolve_master_key()
    aesgcm = AESGCM(key)
    aad = credential_aad(workspace_id, mailbox_id, provider)

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
        result = json.loads(decrypted_bytes.decode("utf-8"))
        if not isinstance(result, dict):
            raise AppError(
                "crypto_error",
                "Decrypted credential payload is invalid",
                status_code=500,
            )
        return result
    except Exception as exc:
        raise AppError(
            "crypto_error",
            "Failed to decrypt credentials or integrity check failed",
            status_code=500,
        ) from exc


def encrypt_verifier(
    verifier: str,
    workspace_id: UUID,
    actor_id: UUID,
    provider: str,
    key_id: str | None = None,
) -> tuple[bytes, str, bytes]:
    """Encrypt a PKCE code verifier bound to actor, workspace, and provider."""
    if key_id is None:
        key_id = Settings.current().mailbox_encryption_key_id

    key = _resolve_master_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    aad = verifier_aad(workspace_id, actor_id, provider)

    ciphertext = aesgcm.encrypt(nonce, verifier.encode("utf-8"), aad)
    return ciphertext, key_id, nonce


def decrypt_verifier(
    ciphertext: bytes,
    nonce: bytes,
    workspace_id: UUID,
    actor_id: UUID,
    provider: str,
    key_id: str | None = None,
) -> str:
    """Decrypt a PKCE code verifier and verify bound actor/workspace."""
    key = _resolve_master_key()
    aesgcm = AESGCM(key)
    aad = verifier_aad(workspace_id, actor_id, provider)

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
        return decrypted_bytes.decode("utf-8")
    except Exception as exc:
        raise AppError(
            "crypto_error",
            "Failed to decrypt verifier or integrity check failed",
            status_code=500,
        ) from exc
