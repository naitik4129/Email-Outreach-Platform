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
    parts = token.split(".")
    tampered_sig = ("x" if parts[2][0] != "x" else "y") + parts[2][1:]
    tampered = f"{parts[0]}.{parts[1]}.{tampered_sig}"
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


def _es256_shaped_token() -> str:
    # Only the header is read before the (mocked) signing-key lookup.
    import base64
    import json

    def part(obj: dict[str, object]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = part({"alg": "ES256", "kid": "k1", "typ": "JWT"})
    return ".".join([header, part({}), "sig"])


class _FailingJwksClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_signing_key_from_jwt(self, token: str) -> object:
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        jwt.PyJWKClientConnectionError("Fail to fetch data from the url"),
        ConnectionResetError("reset by peer"),
    ],
)
def test_jwks_fetch_failure_is_503_not_401(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    # A signing-key fetch failure never judged the token, so it must not read
    # as an invalid session (the client would sign the user out).
    monkeypatch.setattr(
        "app.core.auth._jwks_client", lambda url: _FailingJwksClient(error)
    )
    with pytest.raises(AppError) as exc_info:
        verify_access_token(_es256_shaped_token(), _settings())
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "auth_unavailable"


def test_unknown_signing_key_is_still_401(monkeypatch: pytest.MonkeyPatch) -> None:
    error = jwt.PyJWKClientError("Unable to find a signing key that matches: k1")
    monkeypatch.setattr(
        "app.core.auth._jwks_client", lambda url: _FailingJwksClient(error)
    )
    with pytest.raises(AppError) as exc_info:
        verify_access_token(_es256_shaped_token(), _settings())
    assert exc_info.value.status_code == 401
