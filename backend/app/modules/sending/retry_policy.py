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

# Bounded exponential backoff with proportional jitter.
# BASE = 30s, MAX = 3600s (1 hour). Proportional jitter (+/- 20%) ensures that
# retry waves spread across a wide window, eliminating thundering herds and
# retry storms when a provider recovers from an outage.
_BASE_BACKOFF_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 3600.0
_DEFAULT_RETRY_HORIZON = timedelta(hours=48)


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
    anchor_at: datetime | None = None,
    retry_deadline: datetime | None = None,
    retry_horizon: timedelta | None = _DEFAULT_RETRY_HORIZON,
) -> RetryDecision:
    """Decide whether a failed/ambiguous attempt may be retried.

    UNKNOWN_OUTCOME is deliberately never routed through this function --
    ambiguous outcomes are persisted and left for out-of-scope reconciliation,
    never auto-resolved or auto-retried here (RATE_LIMITING.md /
    MESSAGE_STATE_MACHINE.md: "never blindly retry a potentially successful
    send").
    """
    now = now or datetime.now(UTC)

    # 1. Enforce attempt-based retry budget
    if retry_count >= retry_budget:
        return RetryDecision(
            should_retry=False,
            next_retry_at=None,
            terminal_reason="retry_budget_exhausted",
        )

    # 2. Enforce time-based retry deadline if applicable (MESSAGE_STATE_MACHINE.md §79)
    if retry_deadline is not None and now >= retry_deadline:
        return RetryDecision(
            should_retry=False,
            next_retry_at=None,
            terminal_reason="retry_deadline_exceeded",
        )

    if anchor_at is not None and retry_horizon is not None:
        if anchor_at.tzinfo is None:
            anchor_at = anchor_at.replace(tzinfo=UTC)
        if (now - anchor_at) >= retry_horizon:
            return RetryDecision(
                should_retry=False,
                next_retry_at=None,
                terminal_reason="retry_deadline_exceeded",
            )

    # 3. Classify retryability
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

    # 4. Exponential backoff with proportional jitter (+/- 20%)
    raw_backoff = min(_MAX_BACKOFF_SECONDS, _BASE_BACKOFF_SECONDS * (2**retry_count))
    jittered_backoff = raw_backoff * random.uniform(0.8, 1.2)
    backoff_seconds = min(
        _MAX_BACKOFF_SECONDS, max(_BASE_BACKOFF_SECONDS, jittered_backoff)
    )

    if provider_retry_after_seconds is not None and provider_retry_after_seconds > 0:
        # Respect Retry-After / persisted provider cooldown in addition to
        # platform backoff -- never retry sooner than the provider asked.
        backoff_seconds = max(backoff_seconds, provider_retry_after_seconds)

    return RetryDecision(
        should_retry=True,
        next_retry_at=now + timedelta(seconds=backoff_seconds),
        terminal_reason=None,
    )
