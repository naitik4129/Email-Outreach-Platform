from __future__ import annotations

from app.modules.rate_limit.schemas import RateScopeKind

_NAMESPACE = "rl"

# Canonical scope key for the single PLATFORM-kind rate_scopes row (there is
# at most one: kind='PLATFORM' with a fixed external_scope_key).
PLATFORM_SCOPE_KEY = "GLOBAL"


def generation_key() -> str:
    return f"{_NAMESPACE}:generation"


def status_key() -> str:
    return f"{_NAMESPACE}:status"


def bucket_key(
    kind: RateScopeKind, scope_key: str, unit: str, window_seconds: int
) -> str:
    """Canonical Redis key for one capacity policy's counter state.

    Shared by the limiter (read/write during reservation) and the rate
    controller (rebuild during recovery) so the two can never drift. A
    token-bucket policy stores a hash here ({tokens, ts}); a rolling-window
    policy stores a sorted set of reservation ids keyed by timestamp.
    """
    return f"{_NAMESPACE}:b:{kind.value}:{scope_key}:{unit}:{window_seconds}"


def spacing_key(
    kind: RateScopeKind, scope_key: str, unit: str, window_seconds: int
) -> str:
    """Last-authorized-at marker used to enforce cooldown/min-spacing,
    independent of the token-bucket/rolling-window mode."""
    return f"{bucket_key(kind, scope_key, unit, window_seconds)}:sp"


def reservation_key(reservation_id: str) -> str:
    return f"{_NAMESPACE}:res:{reservation_id}"


def provider_account_scope_key(provider: str, provider_account_identifier: str) -> str:
    """Canonical convention for `rate_scopes.external_scope_key` when
    kind='PROVIDER_ACCOUNT'. Not pinned by any architecture doc -- this
    function is the single source of truth for the format so limiter,
    controller, and any admin tooling that seeds rate_scopes rows can never
    drift from each other. Flagged in the Phase 10 plan as a convention this
    implementation establishes, not one that was already specified.
    """
    return f"{provider.strip().upper()}:{provider_account_identifier}"


def provider_scope_key(provider: str) -> str:
    """Canonical `external_scope_key` for a PROVIDER-kind rate_scopes row."""
    return provider.strip().upper()
