from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest

from app.core.auth import verify_access_token
from app.core.config import Settings
from app.core.errors import AppError


def _settings() -> Settings:
    return Settings.current()


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    settings = _settings()
    claims: dict[str, object] = {
        "sub": str(uuid4()),
        "email": "user@example.com",
        "aud": settings.supabase_jwt_audience,
        "iss": settings.supabase_jwt_issuer,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


def _sign(claims: dict[str, object], secret: str | None = None) -> str:
    secret = secret or _settings().supabase_jwt_secret
    return jwt.encode(claims, secret, algorithm="HS256")


def test_valid_token_resolves_principal() -> None:
    claims = _claims()
    token = _sign(claims)
    principal = verify_access_token(token, _settings())
    assert str(principal.user_id) == claims["sub"]
    assert principal.email == "user@example.com"


def test_expired_token_rejected() -> None:
    now = int(time.time())
    token = _sign(_claims(iat=now - 7200, exp=now - 3600))
    with pytest.raises(AppError) as exc_info:
        verify_access_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_tampered_signature_rejected() -> None:
    token = _sign(_claims())
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(AppError) as exc_info:
        verify_access_token(tampered, _settings())
    assert exc_info.value.status_code == 401


def test_wrong_secret_rejected() -> None:
    token = _sign(_claims(), secret="a-completely-different-secret")
    with pytest.raises(AppError) as exc_info:
        verify_access_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_wrong_audience_rejected() -> None:
    token = _sign(_claims(aud="some-other-audience"))
    with pytest.raises(AppError) as exc_info:
        verify_access_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_wrong_issuer_rejected() -> None:
    token = _sign(_claims(iss="https://not-my-project.supabase.co/auth/v1"))
    with pytest.raises(AppError) as exc_info:
        verify_access_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_missing_subject_rejected() -> None:
    claims = _claims()
    del claims["sub"]
    with pytest.raises(AppError):
        verify_access_token(_sign(claims), _settings())


def test_non_uuid_subject_rejected() -> None:
    token = _sign(_claims(sub="not-a-uuid"))
    with pytest.raises(AppError) as exc_info:
        verify_access_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_missing_token_rejected() -> None:
    with pytest.raises(AppError) as exc_info:
        verify_access_token("", _settings())
    assert exc_info.value.status_code == 401


def test_malformed_token_rejected() -> None:
    with pytest.raises(AppError) as exc_info:
        verify_access_token("not-a-jwt-at-all", _settings())
    assert exc_info.value.status_code == 401
