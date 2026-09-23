from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.modules.mailboxes.providers.base import ErrorCategory

# Error categories the message state machine may retry automatically.
# AUTH_FAILURE/PERMANENT_RECIPIENT_FAILURE/POLICY_REJECTION/CONFIGURATION_FAILURE
# are deliberately excluded -- each requires user action (reconnect mailbox,
# fix recipient/campaign config) that a bounded retry cannot resolve.
_RETRYABLE_CATEGORIES = frozenset(
    {
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.TEMPORARY_PROVIDER_ERROR,
        ErrorCategory.NETWORK_ERROR,
    }
)

# Bounded exponential backoff with jitter. Matches the shape already used by
# OutboxPublisherService's own backoff (workers/... via
# app/modules/scheduler/service.py: min(300, 2**attempts + jitter)) so retry
# pacing conventions stay consistent across the codebase, scaled up for
# provider-level retries (which are coarser-grained than outbox publish
# retries).
_BASE_BACKOFF_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 3600.0
_JITTER_RANGE = (0.5, 2.0)


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    next_retry_at: datetime | None
    terminal_reason: str | None


def classify_and_decide(
    *,
    error_category: str | None,
    is_retryable: bool | None,
    retry_count: int,
    retry_budget: int,
    now: datetime | None = None,
    provider_retry_after_seconds: float | None = None,
) -> RetryDecision:
    """Decide whether a failed/ambiguous attempt may be retried.

    UNKNOWN_OUTCOME is deliberately never routed through this function --
    ambiguous outcomes are persisted and left for out-of-scope reconciliation,
    never auto-resolved or auto-retried here (RATE_LIMITING.md /
    MESSAGE_STATE_MACHINE.md: "never blindly retry a potentially successful
    send").
    """
    now = now or datetime.now(UTC)

    if retry_count >= retry_budget:
        return RetryDecision(
            should_retry=False,
            next_retry_at=None,
            terminal_reason="retry_budget_exhausted",
        )

    category_retryable = (
        ErrorCategory(error_category) in _RETRYABLE_CATEGORIES
        if error_category
        else False
    )
    # `is_retryable` from the provider's own ClassifiedProviderError takes
    # precedence when given (it reflects provider-specific nuance, e.g. a
    # 429 that is nonetheless permanent for this account) but never widens
    # the categories this policy allows -- it can only narrow retryability.
    retryable = category_retryable and (
        is_retryable if is_retryable is not None else True
    )

    if not retryable:
        return RetryDecision(
            should_retry=False,
            next_retry_at=None,
            terminal_reason=(error_category or "non_retryable").lower(),
        )

    backoff_seconds = min(
        _MAX_BACKOFF_SECONDS,
        (_BASE_BACKOFF_SECONDS * (2**retry_count)) + random.uniform(*_JITTER_RANGE),
    )
    if provider_retry_after_seconds is not None:
        # Respect Retry-After / persisted provider cooldown in addition to
        # platform backoff -- never retry sooner than the provider asked.
        backoff_seconds = max(backoff_seconds, provider_retry_after_seconds)

    return RetryDecision(
        should_retry=True,
        next_retry_at=now + timedelta(seconds=backoff_seconds),
        terminal_reason=None,
    )
