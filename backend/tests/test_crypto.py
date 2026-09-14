from __future__ import annotations

import uuid

import pytest

from app.core.crypto import (
    decrypt_credentials,
    decrypt_verifier,
    encrypt_credentials,
    encrypt_verifier,
)
from app.core.errors import AppError


def test_credential_encryption_roundtrip() -> None:
    workspace_id = uuid.uuid4()
    mailbox_id = uuid.uuid4()
    provider = "GMAIL"
    payload = {
        "access_token": "ya29.test-access-token",
        "refresh_token": "1//test-refresh-token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }

    ciphertext, key_id, nonce = encrypt_credentials(
        payload, workspace_id, mailbox_id, provider
    )

    assert isinstance(ciphertext, bytes)
    assert len(nonce) == 12
    assert key_id == "v1"

    decrypted = decrypt_credentials(
        ciphertext, nonce, workspace_id, mailbox_id, provider, key_id
    )
    assert decrypted == payload


def test_credential_encryption_cross_tenant_tampering_rejected() -> None:
    workspace_a = uuid.uuid4()
    workspace_b = uuid.uuid4()
    mailbox_id = uuid.uuid4()
    payload = {"access_token": "secret"}

    ciphertext, key_id, nonce = encrypt_credentials(
        payload, workspace_a, mailbox_id, "GMAIL"
    )

    # Attempting to decrypt in Workspace B must fail due to AAD mismatch
    with pytest.raises(AppError) as exc_info:
        decrypt_credentials(
            ciphertext, nonce, workspace_b, mailbox_id, "GMAIL", key_id
        )
    assert exc_info.value.status_code == 500
    assert "integrity check failed" in exc_info.value.message


def test_credential_encryption_ciphertext_corruption_rejected() -> None:
    workspace_id = uuid.uuid4()
    mailbox_id = uuid.uuid4()
    payload = {"access_token": "secret"}

    ciphertext, key_id, nonce = encrypt_credentials(
        payload, workspace_id, mailbox_id, "GMAIL"
    )

    # Tamper with the last byte
    corrupted = bytearray(ciphertext)
    corrupted[-1] ^= 0xFF

    with pytest.raises(AppError):
        decrypt_credentials(
            bytes(corrupted), nonce, workspace_id, mailbox_id, "GMAIL", key_id
        )


def test_verifier_encryption_roundtrip_and_actor_binding() -> None:
    workspace_id = uuid.uuid4()
    actor_a = uuid.uuid4()
    actor_b = uuid.uuid4()
    code_verifier = "test-pkce-verifier-string-12345"

    ciphertext, key_id, nonce = encrypt_verifier(
        code_verifier, workspace_id, actor_a, "GMAIL"
    )
    assert len(nonce) == 12

    # Correct actor
    decrypted = decrypt_verifier(
        ciphertext, nonce, workspace_id, actor_a, "GMAIL", key_id
    )
    assert decrypted == code_verifier

    # Wrong actor must fail
    with pytest.raises(AppError):
        decrypt_verifier(ciphertext, nonce, workspace_id, actor_b, "GMAIL", key_id)
