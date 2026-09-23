from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.rate_limit import key_builder
from app.modules.rate_limit.schemas import RatePolicySpec, RateScopeKind


def _safe_set_role(session: Session, role_name: str) -> None:
    """Attempt SET LOCAL ROLE if running against PostgreSQL; ignore in SQLite."""
    bind = session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        session.execute(text(f"RESET ROLE; SET LOCAL ROLE {role_name}"))


def _safe_set_workspace(session: Session, workspace_id: UUID | None) -> None:
    bind = session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        if workspace_id is not None:
            session.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"),
                {"ws": str(workspace_id)},
            )
        else:
            session.execute(text("SELECT set_config('app.workspace_id', '', true)"))


class RatePolicyRepository:
    """Read-only resolution of the `rate_scopes` / `tenant_rate_policies`
    rows applicable to one send attempt, for the `app_worker_send` role.

    Resolved decision (see plan): absence of a specific policy row for a
    given kind/key means that scope contributes no cap at all -- it is never
    treated as a denial or as "unlimited overall" (other applicable policies
    still apply).
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve_applicable_policies(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID | None,
        mailbox_id: UUID,
        provider: str,
        provider_account_id: str | None,
        unit: str = "MESSAGE",
    ) -> list[RatePolicySpec]:
        _safe_set_role(self.session, "app_worker_send")
        _safe_set_workspace(self.session, workspace_id)

        policies: list[RatePolicySpec] = []

        # --- rate_scopes: PLATFORM (always applies), PROVIDER, PROVIDER_ACCOUNT ---
        scope_keys: list[tuple[RateScopeKind, str]] = [
            (RateScopeKind.PLATFORM, key_builder.PLATFORM_SCOPE_KEY),
            (RateScopeKind.PROVIDER, key_builder.provider_scope_key(provider)),
        ]
        if provider_account_id:
            scope_keys.append(
                (
                    RateScopeKind.PROVIDER_ACCOUNT,
                    key_builder.provider_account_scope_key(
                        provider, provider_account_id
                    ),
                )
            )

        for kind, scope_key in scope_keys:
            rows = (
                self.session.execute(
                    text(
                        """
                    SELECT id, unit, window_seconds, limit_value, cooldown_seconds,
                           min_spacing_seconds, version
                    FROM public.rate_scopes
                    WHERE kind = :kind AND external_scope_key = :scope_key
                      AND unit = :unit
                    """
                    ),
                    {"kind": kind.value, "scope_key": scope_key, "unit": unit},
                )
                .mappings()
                .all()
            )
            for row in rows:
                policies.append(
                    RatePolicySpec(
                        kind=kind,
                        source_id=str(row["id"]),
                        scope_key=scope_key,
                        unit=row["unit"],
                        window_seconds=row["window_seconds"],
                        limit_value=row["limit_value"],
                        cooldown_seconds=row["cooldown_seconds"],
                        min_spacing_seconds=row["min_spacing_seconds"],
                        policy_version=row["version"],
                        window_kind="ROLLING",
                    )
                )

        # --- tenant_rate_policies: WORKSPACE, CAMPAIGN, MAILBOX ---
        tenant_filters: list[tuple[RateScopeKind, str, dict[str, Any]]] = [
            (
                RateScopeKind.WORKSPACE,
                "kind = 'WORKSPACE' AND workspace_id = :workspace_id",
                {"workspace_id": str(workspace_id)},
            ),
            (
                RateScopeKind.MAILBOX,
                "kind = 'MAILBOX' AND workspace_id = :workspace_id "
                "AND mailbox_id = :mailbox_id",
                {"workspace_id": str(workspace_id), "mailbox_id": str(mailbox_id)},
            ),
        ]
        if campaign_id is not None:
            tenant_filters.append(
                (
                    RateScopeKind.CAMPAIGN,
                    "kind = 'CAMPAIGN' AND workspace_id = :workspace_id "
                    "AND campaign_id = :campaign_id",
                    {
                        "workspace_id": str(workspace_id),
                        "campaign_id": str(campaign_id),
                    },
                )
            )

        for kind, where_clause, params in tenant_filters:
            rows = (
                self.session.execute(
                    text(
                        f"""
                    SELECT id, unit, window_seconds, limit_value, cooldown_seconds,
                           min_spacing_seconds, version, window_kind
                    FROM public.tenant_rate_policies
                    WHERE {where_clause} AND unit = :unit
                    """
                    ),
                    {**params, "unit": unit},
                )
                .mappings()
                .all()
            )
            for row in rows:
                scope_key = str(
                    {
                        RateScopeKind.WORKSPACE: workspace_id,
                        RateScopeKind.CAMPAIGN: campaign_id,
                        RateScopeKind.MAILBOX: mailbox_id,
                    }[kind]
                )
                policies.append(
                    RatePolicySpec(
                        kind=kind,
                        source_id=str(row["id"]),
                        scope_key=scope_key,
                        unit=row["unit"],
                        window_seconds=row["window_seconds"],
                        limit_value=row["limit_value"],
                        cooldown_seconds=row["cooldown_seconds"],
                        min_spacing_seconds=row["min_spacing_seconds"],
                        policy_version=row["version"],
                        window_kind=row["window_kind"],
                    )
                )

        return policies


class RateControlRepository:
    """Durable `rate_control` singleton + supporting reads, for the
    dedicated `app_rate_controller` role only. Never used by the send
    worker itself (see docs/architecture/WORKERS.md role separation)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_status(self) -> dict[str, Any] | None:
        _safe_set_role(self.session, "app_rate_controller")
        row = (
            self.session.execute(
                text(
                    """
                SELECT generation, status, recovery_watermark, recovery_started_at,
                       policy_version, version
                FROM public.rate_control
                WHERE id
                """
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def enter_recovering(self, *, next_generation: int) -> None:
        _safe_set_role(self.session, "app_rate_controller")
        self.session.execute(
            text(
                """
                INSERT INTO public.rate_control
                    (id, generation, status, recovery_started_at)
                VALUES
                    (true, :generation, 'RECOVERING',
                     pg_catalog.transaction_timestamp())
                ON CONFLICT (id) DO UPDATE SET
                    generation = EXCLUDED.generation,
                    status = 'RECOVERING',
                    recovery_watermark = NULL,
                    recovery_started_at = pg_catalog.transaction_timestamp(),
                    version = public.rate_control.version + 1
                """
            ),
            {"generation": next_generation},
        )

    def mark_ready(self, *, generation: int, recovery_watermark: datetime) -> bool:
        _safe_set_role(self.session, "app_rate_controller")
        result = self.session.execute(
            text(
                """
                UPDATE public.rate_control
                SET status = 'READY',
                    recovery_watermark = :watermark,
                    version = version + 1
                WHERE id AND generation = :generation AND status = 'RECOVERING'
                """
            ),
            {"generation": generation, "watermark": recovery_watermark},
        )
        return result.rowcount > 0

    def list_open_tenant_policies(self) -> list[dict[str, Any]]:
        """All tenant_rate_policies rows, used by the controller to rebuild
        Redis bucket/rolling-window state on recovery."""
        _safe_set_role(self.session, "app_rate_controller")
        _safe_set_workspace(self.session, None)
        rows = (
            self.session.execute(
                text(
                    """
                SELECT id, workspace_id, campaign_id, mailbox_id, kind, unit,
                       window_seconds, limit_value, cooldown_seconds,
                       min_spacing_seconds, window_kind, last_authorized_at
                FROM public.tenant_rate_policies
                """
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    def list_rate_scopes(self) -> list[dict[str, Any]]:
        _safe_set_role(self.session, "app_rate_controller")
        rows = (
            self.session.execute(
                text(
                    """
                SELECT id, kind, external_scope_key, unit, window_seconds,
                       limit_value, cooldown_seconds, min_spacing_seconds,
                       last_authorized_at
                FROM public.rate_scopes
                """
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    def list_recent_capacity_debit_scopes(
        self, *, since: datetime
    ) -> list[dict[str, Any]]:
        """Durable evidence of capacity already consumed, used to rebuild
        Redis rolling-window sorted sets so recovery never under-counts
        capacity already spent (RATE_LIMITING.md: "rebuild long-window
        debits and known cooldowns")."""
        _safe_set_role(self.session, "app_rate_controller")
        _safe_set_workspace(self.session, None)
        rows = (
            self.session.execute(
                text(
                    """
                SELECT cds.rate_scope_id, cds.tenant_rate_policy_id, cds.unit,
                       cds.quantity, cds.authorized_at, cds.window_kind,
                       cd.reservation_id
                FROM public.capacity_debit_scopes cds
                JOIN public.capacity_debits cd
                  ON cd.workspace_id = cds.workspace_id AND cd.id = cds.debit_id
                WHERE cds.authorized_at >= :since
                  AND cd.settlement_status = 'CONSUMED'
                """
                ),
                {"since": since},
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
