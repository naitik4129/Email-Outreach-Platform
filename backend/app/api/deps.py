from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import AuthenticatedPrincipal, verify_access_token
from app.core.config import Settings
from app.core.errors import AppError
from app.db.context import set_transaction_context
from app.db.session import session_scope

_bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    """One database session per request, scoped to a single transaction.

    Every protected request runs inside BEGIN -> SET LOCAL ROLE app_api ->
    (transaction-local user/workspace context) -> COMMIT/ROLLBACK, per the
    contract documented in supabase/migrations/0001_initial.sql. SET LOCAL is
    transaction-scoped, so returning this connection to the pool afterwards
    cannot leak role or GUC context into the next request.
    """
    settings = Settings.current()
    with session_scope(settings) as session:
        session.execute(text("SET LOCAL ROLE app_api"))
        yield session


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthenticatedPrincipal:
    """Resolve the authenticated identity from a verified bearer token only.

    Never trusts a client-supplied user id. Lazily ensures a profiles row
    exists for this identity (self-only INSERT, permitted by the existing
    profiles_api_insert RLS policy) so downstream endpoints can rely on it.
    """
    if credentials is None or not credentials.credentials:
        raise AppError("unauthenticated", "Missing bearer token", status_code=401)

    settings = Settings.current()
    principal = verify_access_token(credentials.credentials, settings)

    set_transaction_context(db, user_id=principal.user_id)
    db.execute(
        text("INSERT INTO profiles (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"),
        {"id": str(principal.user_id)},
    )
    return principal


@dataclass(frozen=True)
class WorkspaceContext:
    """An authenticated user's re-validated membership in one workspace.

    role_code is resolved server-side from the current ACTIVE membership on
    every request; it is never derived from a JWT claim or a client-supplied
    value.
    """

    workspace_id: UUID
    user_id: UUID
    role_code: str


def get_workspace_context(
    workspace_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceContext:
    """Validate active membership in the requested workspace and resolve its role.

    A workspace id in the URL never grants access by itself: this always
    re-checks the current ACTIVE membership via app_current_workspace_role(),
    which is backed by RLS, not by trusting the path parameter alone. Returns
    404 (not 403) for both a nonexistent workspace and one the caller is not a
    member of, to avoid disclosing cross-tenant existence.
    """
    set_transaction_context(db, workspace_id=workspace_id)
    role_code = db.execute(
        text("SELECT public.app_current_workspace_role()")
    ).scalar()
    if role_code is None:
        raise AppError("not_found", "Workspace not found", status_code=404)
    return WorkspaceContext(
        workspace_id=workspace_id, user_id=principal.user_id, role_code=role_code
    )


@dataclass(frozen=True)
class PlatformOperatorPrincipal:
    """Explicit platform administrator identity, separate from any workspace role."""

    user_id: UUID | None
    email: str | None


def get_platform_operator(
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    operator_key: str | None = None,
) -> PlatformOperatorPrincipal:
    """Verifies server-side platform operator authority.

    Never trusts client-side role claims, workspace permissions, or route parameters.
    Requires caller's verified email to match platform_operator_emails or valid operator key.
    """
    settings = Settings.current()
    if operator_key and settings.platform_operator_key and operator_key == settings.platform_operator_key:
        return PlatformOperatorPrincipal(user_id=principal.user_id, email=principal.email)

    if principal.email:
        allowed = {e.strip().lower() for e in settings.platform_operator_emails.split(",") if e.strip()}
        if principal.email.strip().lower() in allowed:
            return PlatformOperatorPrincipal(user_id=principal.user_id, email=principal.email)

    raise AppError("forbidden", "Platform operator authority required", status_code=403)
