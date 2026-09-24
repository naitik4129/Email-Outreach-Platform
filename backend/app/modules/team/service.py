from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.notifications.transports import (
    PlatformTransactionalEmailTransport,
    TransactionalEmailMessage,
)
from app.modules.team.schemas import (
    InvitationAcceptIn,
    InvitationAcceptOut,
    InvitationCreateIn,
    InvitationOut,
    InvitationVerifyOut,
    MemberRemoveIn,
    MemberRoleUpdateIn,
    TransferOwnershipIn,
    TransferOwnershipOut,
)

logger = logging.getLogger(__name__)


def hash_token(token: str) -> str:
    """Computes the SHA-256 digest of an invitation token for safe storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_dt(val: Any) -> datetime:
    """Normalizes database timestamps (str in sqlite, datetime in postgres)
    to UTC datetime.
    """
    if isinstance(val, str):
        clean = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
    if isinstance(val, datetime) and val.tzinfo is None:
        return val.replace(tzinfo=UTC)
    return val


class TeamService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @property
    def _is_sqlite(self) -> bool:
        bind = self.db.get_bind()
        return bool(bind and bind.dialect.name == "sqlite")

    @property
    def _for_update(self) -> str:
        if self._is_sqlite:
            return ""
        return "FOR UPDATE"

    def list_members(self, workspace_id: UUID) -> list[dict[str, Any]]:
        """Lists members of current workspace via authoritative database function."""
        rows = (
            self.db.execute(text("SELECT * FROM public.app_list_workspace_members()"))
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def list_invitations(self, workspace_id: UUID) -> list[InvitationOut]:
        """Lists pending and historical invitations for workspace (no secrets)."""
        rows = (
            self.db.execute(
                text(
                    """
                    SELECT id, workspace_id, inviter_id, invited_email, role_code,
                           status, expires_at, created_at
                    FROM workspace_invitations
                    WHERE workspace_id = :workspace_id
                    ORDER BY created_at DESC
                    """
                ),
                {"workspace_id": str(workspace_id)},
            )
            .mappings()
            .all()
        )
        return [InvitationOut(**row) for row in rows]

    def create_invitation(
        self,
        workspace_id: UUID,
        inviter_id: UUID,
        payload: InvitationCreateIn,
        base_url: str = "http://localhost:3000",
    ) -> InvitationOut:
        """Creates a workspace invitation and dispatches transactional notification."""
        raw_token = secrets.token_urlsafe(32)
        digest = hash_token(raw_token)
        expires_at = datetime.now(UTC) + timedelta(days=7)

        # Try calling the SECURITY DEFINER function first if in PostgreSQL
        invite_id = None
        created_at = None
        if not self._is_sqlite:
            try:
                row = (
                    self.db.execute(
                        text(
                            """
                            SELECT * FROM public.app_create_workspace_invitation(
                                :invited_email, :role_code, :token_digest, :expires_at
                            )
                            """
                        ),
                        {
                            "invited_email": payload.email,
                            "role_code": payload.role_code,
                            "token_digest": digest,
                            "expires_at": expires_at,
                        },
                    )
                    .mappings()
                    .first()
                )
                if row:
                    invite_id = row["invitation_id"]
                    created_at = row["created_at"]
            except Exception:
                self.db.rollback()

        if not invite_id:
            self._ensure_inviter_authorized(workspace_id, inviter_id)

            existing_user = self.db.execute(
                text(
                    """
                    SELECT m.id
                    FROM workspace_memberships m
                    JOIN auth.users u ON u.id = m.user_id
                    WHERE m.workspace_id = :ws_id
                      AND lower(u.email) = :email
                      AND m.status = 'ACTIVE'
                    """
                ),
                {"ws_id": str(workspace_id), "email": payload.email},
            ).first()
            if existing_user:
                raise AppError(
                    "conflict",
                    "User is already an active member of this workspace",
                    status_code=409,
                ) from None

            self.db.execute(
                text(
                    """
                    UPDATE workspace_invitations
                    SET status = 'REVOKED', revoked_at = statement_timestamp()
                    WHERE workspace_id = :ws_id
                      AND invited_email = :email
                      AND status = 'PENDING'
                    """
                ),
                {"ws_id": str(workspace_id), "email": payload.email},
            )

            invite_id = uuid.uuid4()
            self.db.execute(
                text(
                    """
                    INSERT INTO workspace_invitations
                        (id, workspace_id, inviter_id, invited_email, role_code,
                         token_digest, status, expires_at)
                    VALUES
                        (:id, :ws_id, :inviter_id, :email, :role_code, :digest,
                         'PENDING', :expires_at)
                    """
                ),
                {
                    "id": str(invite_id),
                    "ws_id": str(workspace_id),
                    "inviter_id": str(inviter_id),
                    "email": payload.email,
                    "role_code": payload.role_code,
                    "digest": digest,
                    "expires_at": expires_at,
                },
            )

            self.db.execute(
                text(
                    """
                    INSERT INTO audit_events
                        (workspace_id, actor_kind, actor_id, action, target_type,
                         target_id, after_state)
                    VALUES
                        (:ws_id, 'USER', :actor_id, 'invitation.created',
                         'workspace_invitation', :target_id, CAST(:state AS jsonb))
                    """
                ),
                {
                    "ws_id": str(workspace_id),
                    "actor_id": str(inviter_id),
                    "target_id": str(invite_id),
                    "state": json.dumps(
                        {"invited_email": payload.email, "role_code": payload.role_code}
                    ),
                },
            )
            created_at = datetime.now(UTC)

        invite_url = f"{base_url.rstrip('/')}/auth/accept-invite?token={raw_token}"

        PlatformTransactionalEmailTransport.send(
            TransactionalEmailMessage(
                to_email=payload.email,
                subject="You've been invited to join a workspace",
                body_text=(
                    f"You have been invited to join the workspace with role "
                    f"{payload.role_code}. Click here to accept: {invite_url}"
                ),
                category="invitation",
            )
        )

        return InvitationOut(
            id=invite_id,
            workspace_id=workspace_id,
            inviter_id=inviter_id,
            invited_email=payload.email,
            role_code=payload.role_code,
            status="PENDING",
            expires_at=expires_at,
            created_at=created_at,
            invite_url=invite_url,
        )

    def revoke_invitation(
        self, workspace_id: UUID, actor_id: UUID, invitation_id: UUID
    ) -> bool:
        """Revokes an outstanding pending invitation."""
        if not self._is_sqlite:
            try:
                result = self.db.execute(
                    text(
                        "SELECT public.app_revoke_workspace_invitation("
                        ":invitation_id)"
                    ),
                    {"invitation_id": str(invitation_id)},
                ).scalar()
                return bool(result)
            except Exception:
                self.db.rollback()

        self._ensure_inviter_authorized(workspace_id, actor_id)
        row = (
            self.db.execute(
                text(
                    """
                    UPDATE workspace_invitations
                    SET status = 'REVOKED', revoked_at = statement_timestamp()
                    WHERE id = :id AND workspace_id = :ws_id AND status = 'PENDING'
                    RETURNING id, invited_email, role_code
                    """
                ),
                {"id": str(invitation_id), "ws_id": str(workspace_id)},
            )
            .mappings()
            .first()
        )
        if not row:
            raise AppError(
                "not_found", "Pending invitation not found", status_code=404
            ) from None

        self.db.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_kind, actor_id, action, target_type,
                     target_id, before_state)
                VALUES
                    (:ws_id, 'USER', :actor_id, 'invitation.revoked',
                     'workspace_invitation', :target_id, CAST(:state AS jsonb))
                """
            ),
            {
                "ws_id": str(workspace_id),
                "actor_id": str(actor_id),
                "target_id": str(invitation_id),
                "state": json.dumps(
                    {
                        "invited_email": row["invited_email"],
                        "role_code": row["role_code"],
                    }
                ),
            },
        )
        return True

    def resend_invitation(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        invitation_id: UUID,
        base_url: str = "http://localhost:3000",
    ) -> InvitationOut:
        """Resends an invitation with a fresh token and extended expiration."""
        self._ensure_inviter_authorized(workspace_id, actor_id)

        existing = (
            self.db.execute(
                text(
                    """
                    SELECT id, invited_email, role_code
                    FROM workspace_invitations
                    WHERE id = :id AND workspace_id = :ws_id
                    """
                ),
                {"id": str(invitation_id), "ws_id": str(workspace_id)},
            )
            .mappings()
            .first()
        )
        if not existing:
            raise AppError("not_found", "Invitation not found", status_code=404)

        return self.create_invitation(
            workspace_id=workspace_id,
            inviter_id=actor_id,
            payload=InvitationCreateIn(
                email=existing["invited_email"], role_code=existing["role_code"]
            ),
            base_url=base_url,
        )

    def verify_invitation_token(self, token: str) -> InvitationVerifyOut:
        """Verifies an invitation token validity without consuming it."""
        digest = hash_token(token)
        row = (
            self.db.execute(
                text(
                    """
                    SELECT i.id, i.workspace_id, w.name AS workspace_name,
                           i.invited_email, i.role_code, i.status, i.expires_at
                    FROM workspace_invitations i
                    JOIN workspaces w ON w.id = i.workspace_id
                    WHERE i.token_digest = :digest
                    """
                ),
                {"digest": digest},
            )
            .mappings()
            .first()
        )
        if not row:
            raise AppError("not_found", "Invitation not found", status_code=404)

        if row["status"] != "PENDING":
            raise AppError(
                "invalid_invitation",
                f"Invitation is {row['status'].lower()}",
                status_code=410,
            )

        if _parse_dt(row["expires_at"]) <= datetime.now(UTC):
            raise AppError(
                "expired_invitation", "Invitation has expired", status_code=410
            )

        return InvitationVerifyOut(
            id=row["id"],
            workspace_id=row["workspace_id"],
            workspace_name=row["workspace_name"],
            invited_email=row["invited_email"],
            role_code=row["role_code"],
            expires_at=row["expires_at"],
        )

    def accept_invitation(
        self,
        actor_id: UUID,
        actor_email: str,
        payload: InvitationAcceptIn,
    ) -> InvitationAcceptOut:
        """Atomically accepts an invitation and establishes workspace membership."""
        digest = hash_token(payload.token)

        if not self._is_sqlite:
            try:
                row = (
                    self.db.execute(
                        text(
                            "SELECT * FROM "
                            "public.app_accept_workspace_invitation(:digest)"
                        ),
                        {"digest": digest},
                    )
                    .mappings()
                    .first()
                )
                if row:
                    return InvitationAcceptOut(
                        workspace_id=row["workspace_id"],
                        membership_id=row["membership_id"],
                        role_code=row["role_code"],
                        workspace_name=row["workspace_name"],
                    )
            except Exception:
                self.db.rollback()

        inv = (
            self.db.execute(
                text(
                    f"""
                    SELECT i.id, i.workspace_id, i.invited_email, i.role_code,
                           i.status, i.expires_at, w.name AS workspace_name
                    FROM workspace_invitations i
                    JOIN workspaces w ON w.id = i.workspace_id
                    WHERE i.token_digest = :digest
                    {self._for_update}
                    """
                ),
                {"digest": digest},
            )
            .mappings()
            .first()
        )
        if not inv:
            raise AppError("not_found", "Invitation token not found", status_code=404)

        if inv["status"] != "PENDING":
            raise AppError(
                "invalid_invitation",
                f"Invitation is {inv['status'].lower()}",
                status_code=400,
            )

        if _parse_dt(inv["expires_at"]) <= datetime.now(UTC):
            self.db.execute(
                text(
                    "UPDATE workspace_invitations SET status = 'EXPIRED' WHERE id = :id"
                ),
                {"id": str(inv["id"])},
            )
            raise AppError(
                "expired_invitation", "Invitation has expired", status_code=410
            )

        if actor_email.strip().lower() != inv["invited_email"]:
            raise AppError(
                "forbidden",
                "This invitation was sent to a different email address",
                status_code=403,
            )

        ws_id = inv["workspace_id"]
        existing_mem = (
            self.db.execute(
                text(
                    f"""
                    SELECT id, role_code, status
                    FROM workspace_memberships
                    WHERE workspace_id = :ws_id AND user_id = :user_id
                    {self._for_update}
                    """
                ),
                {"ws_id": str(ws_id), "user_id": str(actor_id)},
            )
            .mappings()
            .first()
        )

        if existing_mem:
            mem_id = existing_mem["id"]
            if existing_mem["status"] != "ACTIVE":
                self.db.execute(
                    text(
                        """
                        UPDATE workspace_memberships
                        SET status = 'ACTIVE', role_code = :role, revoked_at = NULL,
                            updated_at = statement_timestamp()
                        WHERE id = :id
                        """
                    ),
                    {"id": str(mem_id), "role": inv["role_code"]},
                )
            role_code = inv["role_code"]
        else:
            mem_id = uuid.uuid4()
            self.db.execute(
                text(
                    """
                    INSERT INTO workspace_memberships (
                        id, workspace_id, user_id, role_code, status
                    )
                    VALUES (:id, :ws_id, :user_id, :role_code, 'ACTIVE')
                    """
                ),
                {
                    "id": str(mem_id),
                    "ws_id": str(ws_id),
                    "user_id": str(actor_id),
                    "role_code": inv["role_code"],
                },
            )
            role_code = inv["role_code"]

        self.db.execute(
            text(
                """
                UPDATE workspace_invitations
                SET status = 'ACCEPTED', accepted_membership_id = :mem_id,
                    accepted_at = statement_timestamp()
                WHERE id = :id
                """
            ),
            {"id": str(inv["id"]), "mem_id": str(mem_id)},
        )

        self.db.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_kind, actor_id, action, target_type,
                     target_id, after_state)
                VALUES
                    (:ws_id, 'USER', :actor_id, 'invitation.accepted',
                     'workspace_membership', :mem_id, CAST(:state AS jsonb))
                """
            ),
            {
                "ws_id": str(ws_id),
                "actor_id": str(actor_id),
                "mem_id": str(mem_id),
                "state": json.dumps(
                    {"invitation_id": str(inv["id"]), "role_code": role_code}
                ),
            },
        )

        return InvitationAcceptOut(
            workspace_id=ws_id,
            membership_id=mem_id,
            role_code=role_code,
            workspace_name=inv["workspace_name"],
        )

    def update_member_role(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        membership_id: UUID,
        payload: MemberRoleUpdateIn,
    ) -> dict[str, Any]:
        """Updates a member's role (Owner-only, cannot modify Owner/self)."""
        self._ensure_inviter_authorized(workspace_id, actor_id)

        target = (
            self.db.execute(
                text(
                    f"""
                    SELECT id, user_id, role_code, status, version
                    FROM workspace_memberships
                    WHERE id = :id AND workspace_id = :ws_id
                    {self._for_update}
                    """
                ),
                {"id": str(membership_id), "ws_id": str(workspace_id)},
            )
            .mappings()
            .first()
        )
        if not target:
            raise AppError(
                "not_found", "Member not found in workspace", status_code=404
            )

        if str(target["user_id"]) == str(actor_id):
            raise AppError(
                "forbidden", "Self-role changes are prohibited", status_code=403
            )

        if target["role_code"] == "OWNER":
            raise AppError(
                "forbidden",
                "Cannot modify an OWNER membership directly; use ownership transfer",
                status_code=403,
            )

        if target["version"] != payload.expected_version:
            raise AppError(
                "version_conflict",
                "Membership was modified concurrently; reload and retry",
                status_code=409,
            )

        updated = (
            self.db.execute(
                text(
                    """
                    UPDATE workspace_memberships
                    SET role_code = :role_code,
                        version = version + 1,
                        updated_at = statement_timestamp()
                    WHERE id = :id
                    RETURNING id, user_id, role_code, status, version
                    """
                ),
                {"id": str(membership_id), "role_code": payload.role_code},
            )
            .mappings()
            .first()
        )

        self.db.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_kind, actor_id, action, target_type,
                     target_id, before_state, after_state)
                VALUES
                    (:ws_id, 'USER', :actor_id, 'member.role_changed',
                     'workspace_membership', :target_id, CAST(:before AS jsonb),
                     CAST(:after AS jsonb))
                """
            ),
            {
                "ws_id": str(workspace_id),
                "actor_id": str(actor_id),
                "target_id": str(membership_id),
                "before": json.dumps({"role_code": target["role_code"]}),
                "after": json.dumps({"role_code": payload.role_code}),
            },
        )

        return dict(updated or target)

    def remove_member(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        membership_id: UUID,
        payload: MemberRemoveIn,
    ) -> bool:
        """Revokes a workspace membership (Owner-only, cannot remove Owner or self)."""
        self._ensure_inviter_authorized(workspace_id, actor_id)

        target = (
            self.db.execute(
                text(
                    f"""
                    SELECT id, user_id, role_code, status, version
                    FROM workspace_memberships
                    WHERE id = :id AND workspace_id = :ws_id
                    {self._for_update}
                    """
                ),
                {"id": str(membership_id), "ws_id": str(workspace_id)},
            )
            .mappings()
            .first()
        )
        if not target:
            raise AppError(
                "not_found", "Member not found in workspace", status_code=404
            )

        if str(target["user_id"]) == str(actor_id):
            raise AppError(
                "forbidden",
                "Self-removal from workspace is prohibited",
                status_code=403,
            )

        if target["role_code"] == "OWNER":
            raise AppError(
                "forbidden", "Cannot revoke an active OWNER membership", status_code=403
            )

        if target["version"] != payload.expected_version:
            raise AppError(
                "version_conflict",
                "Membership was modified concurrently; reload and retry",
                status_code=409,
            )

        self.db.execute(
            text(
                """
                UPDATE workspace_memberships
                SET status = 'REVOKED', revoked_at = statement_timestamp(),
                    updated_at = statement_timestamp()
                WHERE id = :id
                """
            ),
            {"id": str(membership_id)},
        )

        self.db.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_kind, actor_id, action, target_type,
                     target_id, before_state, after_state)
                VALUES
                    (:ws_id, 'USER', :actor_id, 'member.removed',
                     'workspace_membership', :target_id, CAST(:before AS jsonb),
                     CAST(:after AS jsonb))
                """
            ),
            {
                "ws_id": str(workspace_id),
                "actor_id": str(actor_id),
                "target_id": str(membership_id),
                "before": json.dumps(
                    {"role_code": target["role_code"], "status": "ACTIVE"}
                ),
                "after": json.dumps({"status": "REVOKED"}),
            },
        )
        return True

    def transfer_ownership(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        payload: TransferOwnershipIn,
    ) -> TransferOwnershipOut:
        """Atomically transfers workspace ownership: demotes owner, promotes target."""
        self.db.execute(
            text(f"SELECT id FROM workspaces WHERE id = :id {self._for_update}"),
            {"id": str(workspace_id)},
        )

        owner = (
            self.db.execute(
                text(
                    f"""
                    SELECT id, user_id, version
                    FROM workspace_memberships
                    WHERE workspace_id = :ws_id
                      AND user_id = :user_id
                      AND role_code = 'OWNER'
                      AND status = 'ACTIVE'
                    {self._for_update}
                    """
                ),
                {"ws_id": str(workspace_id), "user_id": str(actor_id)},
            )
            .mappings()
            .first()
        )
        if not owner:
            raise AppError(
                "forbidden",
                "Only the active workspace owner may transfer ownership",
                status_code=403,
            )

        if owner["version"] != payload.expected_owner_version:
            raise AppError(
                "version_conflict",
                "Owner membership version conflict; reload and retry",
                status_code=409,
            )

        target = (
            self.db.execute(
                text(
                    f"""
                    SELECT id, user_id, version, role_code
                    FROM workspace_memberships
                    WHERE id = :id AND workspace_id = :ws_id AND status = 'ACTIVE'
                    {self._for_update}
                    """
                ),
                {"id": str(payload.target_membership_id), "ws_id": str(workspace_id)},
            )
            .mappings()
            .first()
        )
        if not target:
            raise AppError(
                "not_found",
                "Target active member not found in workspace",
                status_code=404,
            )

        if str(target["id"]) == str(owner["id"]):
            raise AppError(
                "validation_error",
                "Cannot transfer ownership to yourself",
                status_code=422,
            )

        if target["version"] != payload.expected_target_version:
            raise AppError(
                "version_conflict",
                "Target member version conflict; reload and retry",
                status_code=409,
            )

        self.db.execute(
            text(
                """
                UPDATE workspace_memberships
                SET role_code = 'ADMIN',
                    version = version + 1,
                    updated_at = statement_timestamp()
                WHERE id = :id
                """
            ),
            {"id": str(owner["id"])},
        )

        self.db.execute(
            text(
                """
                UPDATE workspace_memberships
                SET role_code = 'OWNER',
                    version = version + 1,
                    updated_at = statement_timestamp()
                WHERE id = :id
                """
            ),
            {"id": str(target["id"])},
        )

        self.db.execute(
            text(
                """
                UPDATE workspaces
                SET owner_policy_version = owner_policy_version + 1,
                    updated_at = statement_timestamp()
                WHERE id = :id
                """
            ),
            {"id": str(workspace_id)},
        )

        self.db.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_kind, actor_id, action, target_type,
                     target_id, before_state, after_state)
                VALUES
                    (:ws_id, 'USER', :actor_id, 'ownership.transferred', 'workspace',
                     :ws_id, CAST(:before AS jsonb), CAST(:after AS jsonb))
                """
            ),
            {
                "ws_id": str(workspace_id),
                "actor_id": str(actor_id),
                "before": json.dumps(
                    {
                        "owner_membership_id": str(owner["id"]),
                        "owner_user_id": str(owner["user_id"]),
                    }
                ),
                "after": json.dumps(
                    {
                        "owner_membership_id": str(target["id"]),
                        "owner_user_id": str(target["user_id"]),
                    }
                ),
            },
        )

        return TransferOwnershipOut(
            workspace_id=workspace_id,
            previous_owner_id=owner["id"],
            new_owner_id=target["id"],
        )

    def _ensure_inviter_authorized(self, workspace_id: UUID, actor_id: UUID) -> None:
        """Verifies caller is an active OWNER with team.manage permission."""
        role = self.db.execute(
            text(
                """
                SELECT role_code
                FROM workspace_memberships
                WHERE workspace_id = :ws_id AND user_id = :user_id AND status = 'ACTIVE'
                """
            ),
            {"ws_id": str(workspace_id), "user_id": str(actor_id)},
        ).scalar()
        if role != "OWNER":
            raise AppError(
                "forbidden",
                "Only workspace owners may manage team memberships and invitations",
                status_code=403,
            )
