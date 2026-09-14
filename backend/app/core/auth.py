from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import cast
from uuid import UUID

import jwt
from jwt import PyJWKClient

from app.core.config import Settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

_UNAUTHENTICATED = "unauthenticated"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Identity resolved from a verified Supabase access token only."""

    user_id: UUID
    email: str | None


def _unauthenticated(message: str) -> AppError:
    return AppError(_UNAUTHENTICATED, message, status_code=401)


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})


def _decode(token: str, settings: Settings) -> dict[str, object]:
    """Verify per the token's own declared algorithm, checked against a
    fixed allowlist -- never inferred from which config happens to be set.
    Supabase projects on the newer JWKS-based signing keys issue ES256/RS256
    tokens even when a legacy HS256 secret is also configured on the
    project, so the secret's mere presence must not force the HS256 path.
    """
    options = {"require": ["exp", "iat", "sub"]}
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _unauthenticated("Malformed token") from exc

    algorithm = header.get("alg")

    if algorithm == "HS256" and settings.supabase_jwt_secret:
        try:
            return cast(
                "dict[str, object]",
                jwt.decode(
                    token,
                    settings.supabase_jwt_secret,
                    algorithms=["HS256"],
                    audience=settings.supabase_jwt_audience,
                    issuer=settings.supabase_jwt_issuer,
                    options=options,
                ),
            )
        except jwt.PyJWTError as exc:
            logger.warning(
                "Rejected HS256 access token: %s (issuer=%s audience=%s)",
                type(exc).__name__,
                settings.supabase_jwt_issuer,
                settings.supabase_jwt_audience,
            )
            raise _unauthenticated("Invalid or expired session") from exc

    if algorithm in _ASYMMETRIC_ALGORITHMS:
        try:
            signing_key = _jwks_client(
                settings.supabase_jwks_url
            ).get_signing_key_from_jwt(token)
            return cast(
                "dict[str, object]",
                jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=list(_ASYMMETRIC_ALGORITHMS),
                    audience=settings.supabase_jwt_audience,
                    issuer=settings.supabase_jwt_issuer,
                    options=options,
                ),
            )
        except jwt.PyJWTError as exc:
            logger.warning(
                "Rejected %s access token: %s (issuer=%s audience=%s jwks_url=%s)",
                algorithm,
                type(exc).__name__,
                settings.supabase_jwt_issuer,
                settings.supabase_jwt_audience,
                settings.supabase_jwks_url,
            )
            raise _unauthenticated("Invalid or expired session") from exc
        except Exception as exc:  # JWKS transport/lookup failures
            logger.warning(
                "Unable to fetch/match a JWKS signing key from %s: %s: %s",
                settings.supabase_jwks_url,
                type(exc).__name__,
                exc,
            )
            raise _unauthenticated("Unable to verify session") from exc

    raise _unauthenticated("Unsupported token signing algorithm")


def verify_access_token(token: str, settings: Settings) -> AuthenticatedPrincipal:
    """Verify a Supabase-issued access token and resolve the authenticated identity.

    Never decodes without verifying signature, issuer, audience and expiry.
    The returned user id is the only trustworthy source of identity for a
    request; a client-supplied user id must never substitute for this.
    """
    if not token:
        raise _unauthenticated("Missing bearer token")

    claims = _decode(token, settings)

    subject = claims.get("sub")
    if not isinstance(subject, str):
        raise _unauthenticated("Token is missing a subject claim")
    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise _unauthenticated("Token subject is not a valid user id") from exc

    email = claims.get("email")
    return AuthenticatedPrincipal(
        user_id=user_id, email=email if isinstance(email, str) else None
    )


def reset_jwks_cache() -> None:
    _jwks_client.cache_clear()
