from __future__ import annotations

import pytest

from app.core.permissions import ROLE_PERMISSIONS, has_permission

ROLES = ["VIEWER", "MEMBER", "MANAGER", "ADMIN", "OWNER"]


def test_unknown_capability_denies_every_role() -> None:
    for role in ROLES:
        assert has_permission(role, "not.a.real.capability") is False


def test_missing_role_denies_every_capability() -> None:
    for capability in ROLE_PERMISSIONS:
        assert has_permission(None, capability) is False


@pytest.mark.parametrize(
    ("capability", "allowed_roles"),
    [
        ("product.read", {"VIEWER", "MEMBER", "MANAGER", "ADMIN", "OWNER"}),
        ("contacts.manage", {"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
        ("templates.manage", {"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
        ("campaigns.draft", {"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
        ("campaigns.execute", {"MANAGER", "ADMIN", "OWNER"}),
        ("mailboxes.manage", {"MANAGER", "ADMIN", "OWNER"}),
        ("inbox.manage", {"MANAGER", "ADMIN", "OWNER"}),
        ("suppression.add", {"MEMBER", "MANAGER", "ADMIN", "OWNER"}),
        ("suppression.release_manual", {"ADMIN", "OWNER"}),
        ("workspace.manage", {"ADMIN", "OWNER"}),
        ("audit.read", {"ADMIN", "OWNER"}),
        ("team.manage", {"OWNER"}),
        ("ownership.manage", {"OWNER"}),
    ],
)
def test_matrix_matches_app_has_permission_sql(
    capability: str, allowed_roles: set[str]
) -> None:
    """Mirrors app_has_permission() in supabase/migrations/0001_initial.sql.

    Keep this test (and ROLE_PERMISSIONS) in sync with that function and with
    docs/product/USER_ROLES.md whenever either changes.
    """
    for role in ROLES:
        expected = role in allowed_roles
        assert has_permission(role, capability) is expected, (
            f"{capability} for {role}: expected {expected}"
        )
