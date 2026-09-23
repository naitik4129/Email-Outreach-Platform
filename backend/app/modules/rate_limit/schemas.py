from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class RateScopeKind(StrEnum):
    """Matches the `kind` CHECK constraints on `rate_scopes`
    (PLATFORM/PROVIDER/PROVIDER_ACCOUNT) and `tenant_rate_policies`
    (WORKSPACE/CAMPAIGN/MAILBOX) in supabase/migrations/0005_operations_platform.sql.
    Do not add values here without a matching schema constraint.
    """

    PLATFORM = "PLATFORM"
    PROVIDER = "PROVIDER"
    PROVIDER_ACCOUNT = "PROVIDER_ACCOUNT"
    WORKSPACE = "WORKSPACE"
    CAMPAIGN = "CAMPAIGN"
    MAILBOX = "MAILBOX"


# Kinds backed by the global, operator-configured `rate_scopes` table.
RATE_SCOPES_TABLE_KINDS = frozenset(
    {RateScopeKind.PLATFORM, RateScopeKind.PROVIDER, RateScopeKind.PROVIDER_ACCOUNT}
)
# Kinds backed by the per-tenant `tenant_rate_policies` table.
TENANT_POLICY_TABLE_KINDS = frozenset(
    {RateScopeKind.WORKSPACE, RateScopeKind.CAMPAIGN, RateScopeKind.MAILBOX}
)


@dataclass(frozen=True)
class RatePolicySpec:
    """One applicable capacity policy resolved for a specific send attempt.

    Mirrors either a `rate_scopes` row (PLATFORM/PROVIDER/PROVIDER_ACCOUNT,
    globally identified by `scope_key` = `external_scope_key`) or a
    `tenant_rate_policies` row (WORKSPACE/CAMPAIGN/MAILBOX, tenant-scoped).
    `source_id` is the originating row's id, persisted into
    capacity_debit_scopes as rate_scope_id or tenant_rate_policy_id.

    Resolved decision (per plan): a workspace/campaign/mailbox with no
    explicit `tenant_rate_policies` row contributes no `RatePolicySpec` for
    that kind at all -- it is treated as unlimited for that scope, not as a
    denial. PLATFORM/PROVIDER/PROVIDER_ACCOUNT `rate_scopes` rows follow the
    same "absence = no additional cap from this scope" rule, consistently.
    """

    kind: RateScopeKind
    source_id: str
    scope_key: str
    unit: str
    window_seconds: int
    limit_value: int
    cooldown_seconds: int = 0
    min_spacing_seconds: int = 0
    policy_version: int = 1
    window_kind: str = "ROLLING"


@dataclass(frozen=True)
class RateReservationRequest:
    message_id: str
    quantity: int = 1
    policies: tuple[RatePolicySpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ScopeReservationResult:
    kind: RateScopeKind
    source_id: str
    scope_key: str
    unit: str


@dataclass(frozen=True)
class RateReservation:
    reservation_id: str
    generation: int
    granted_at: datetime
    expires_at: datetime
    scopes: tuple[ScopeReservationResult, ...]


@dataclass(frozen=True)
class RateDenial:
    reason: str
    failing_kind: RateScopeKind | None
    failing_scope_key: str | None
    retry_after_seconds: float | None


class RateLimitUnavailable(Exception):
    """Raised when the limiter cannot safely evaluate capacity (Redis
    connection/timeout/protocol error). Callers MUST fail closed -- never
    treat this as "capacity granted". Per RATE_LIMITING.md: "A missing key
    never means an unused full budget."
    """


class RateLimitGenerationStale(RateLimitUnavailable):
    """Raised when the limiter reports NOT_READY or a generation mismatch --
    the rate controller has entered/left RECOVERING since the caller last
    read the generation, or has never bootstrapped. Treated as unavailable
    (fail closed), not merely a denial: the limiter's state cannot be
    trusted for this caller's view.
    """


class RateLimitDenied(Exception):
    """Raised when the limiter is healthy and READY but declines to grant
    capacity for this request. Distinct from `RateLimitUnavailable`: this is
    an ordinary, expected outcome (the message should be deferred/retried
    later), not an infrastructure failure.
    """

    def __init__(self, denial: RateDenial) -> None:
        self.denial = denial
        super().__init__(f"Rate limit denied: {denial.reason}")
