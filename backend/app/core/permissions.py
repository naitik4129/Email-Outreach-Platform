from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import Depends

from app.core.errors import AppError

if TYPE_CHECKING:
    from app.api.deps import WorkspaceContext

#: Byte-for-byte mirror of app_has_permission() in
#: supabase/migrations/0001_initial.sql. Keep in sync with that function and
#: with docs/product/USER_ROLES.md; unknown capabilities deny by default. RLS
#: is the authoritative enforcement point in the database; this matrix exists
#: only to produce clean, centralized 403s in the API layer.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "product.read": frozenset({"VIEWER", "MEMBER", "MANAGER", "ADMIN", "OWNER"}),
    "contacts.manage": frozenset({"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
    "templates.manage": frozenset({"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
    "campaigns.draft": frozenset({"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
    "campaigns.execute": frozenset({"MANAGER", "ADMIN", "OWNER"}),
    "mailboxes.manage": frozenset({"MANAGER", "ADMIN", "OWNER"}),
    "inbox.manage": frozenset({"MANAGER", "ADMIN", "OWNER"}),
    "suppression.add": frozenset({"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
    "suppression.release_manual": frozenset({"ADMIN", "OWNER"}),
    "workspace.manage": frozenset({"ADMIN", "OWNER"}),
    "audit.read": frozenset({"ADMIN", "OWNER"}),
    "team.manage": frozenset({"OWNER"}),
    "ownership.manage": frozenset({"OWNER"}),
}


def has_permission(role_code: str | None, capability: str) -> bool:
    if role_code is None:
        return False
    allowed = ROLE_PERMISSIONS.get(capability)
    if allowed is None:
        return False
    return role_code in allowed


def require_permission(capability: str) -> Callable[..., WorkspaceContext]:
    """Dependency factory enforcing one capability from ROLE_PERMISSIONS.

    Composes on top of get_workspace_context, which has already validated
    active membership and resolved the role server-side. This is the single
    place route handlers check authorization; it never inspects the raw JWT
    or a client-supplied role.
    """

    from app.api.deps import get_workspace_context

    def _dependency(
        context: WorkspaceContext = Depends(get_workspace_context),
    ) -> WorkspaceContext:
        if not has_permission(context.role_code, capability):
            raise AppError(
                "forbidden",
                "You do not have permission to perform this action",
                status_code=403,
            )
        return context

    return _dependency
