from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class MetricsRegistry:
    """Thread-safe, low-cardinality in-memory operational metrics collector.

    Provides counters for failure, retry, recovery, and reconciliation operations
    following Prometheus formatting conventions. Labels are strictly low-cardinality
    (e.g. provider name, error category, outcome). High-cardinality values like
    message IDs, recipients, or freeform error texts are strictly forbidden per
    project rules.
    """

    _instance: MetricsRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._mutex = threading.Lock()

    @classmethod
    def get(cls) -> MetricsRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset all counters, useful for tests."""
        with cls._lock:
            if cls._instance is not None:
                with cls._instance._mutex:
                    cls._instance._counters.clear()
            else:
                cls._instance = cls()

    def increment(
        self,
        name: str,
        value: int = 1,
        **labels: str,
    ) -> None:
        # Sort labels to ensure consistent dict keys
        label_key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._mutex:
            self._counters[name][label_key] += value

    def get_count(self, name: str, **labels: str) -> int:
        with self._mutex:
            if not labels:
                # Sum across all labels if no labels specified
                return sum(self._counters[name].values())
            total = 0
            for label_key, count in self._counters[name].items():
                label_dict = dict(label_key)
                if all(label_dict.get(k) == str(v) for k, v in labels.items()):
                    total += count
            return total

    def snapshot(self) -> dict[str, Any]:
        with self._mutex:
            out: dict[str, Any] = {}
            for name, labeled_counts in self._counters.items():
                out[name] = {
                    ",".join(f"{k}={v}" for k, v in lk): count
                    for lk, count in labeled_counts.items()
                }
            return out


metrics = MetricsRegistry.get()


# Helper convenience recording functions with safe, low-cardinality labels
def record_retry_scheduled(provider: str, reason: str | None = None) -> None:
    metrics.increment(
        "email_retry_scheduled_total",
        provider=provider.upper() if provider else "UNKNOWN",
        reason=reason.lower() if reason else "unspecified",
    )


def record_retry_executed(provider: str) -> None:
    metrics.increment(
        "email_retry_executed_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_retry_exhausted(provider: str) -> None:
    metrics.increment(
        "email_retry_exhausted_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_ambiguous_outcome(provider: str, error_category: str | None = None) -> None:
    metrics.increment(
        "email_ambiguous_outcome_total",
        provider=provider.upper() if provider else "UNKNOWN",
        error_category=error_category or "UNKNOWN",
    )


def record_stale_execution_recovered(count: int = 1) -> None:
    metrics.increment("email_stale_execution_recovered_total", value=count)


def record_provider_throttle(provider: str) -> None:
    metrics.increment(
        "email_provider_throttle_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_auth_failure(provider: str) -> None:
    metrics.increment(
        "email_auth_failure_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_terminal_failure(provider: str, reason: str | None = None) -> None:
    metrics.increment(
        "email_terminal_failure_total",
        provider=provider.upper() if provider else "UNKNOWN",
        reason=reason.lower() if reason else "unspecified",
    )


def record_reconciliation(provider: str, result: str) -> None:
    metrics.increment(
        "email_reconciliation_total",
        provider=provider.upper() if provider else "UNKNOWN",
        result=result.lower(),
    )


def record_worker_crash_recovery(count: int = 1) -> None:
    metrics.increment("email_worker_crash_recovery_total", value=count)


def record_duplicate_task_noop(reason: str | None = None) -> None:
    metrics.increment(
        "email_duplicate_task_noop_total",
        reason=reason.lower() if reason else "unspecified",
    )


# Event Processing Pipeline Metrics (Phase 12)
def record_event_received(provider: str, event_type: str | None = None) -> None:
    metrics.increment(
        "inbound_events_received_total",
        provider=provider.upper() if provider else "UNKNOWN",
        event_type=event_type.upper() if event_type else "UNKNOWN",
    )


def record_event_verified(provider: str) -> None:
    metrics.increment(
        "inbound_events_verified_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_event_rejected(provider: str, reason: str | None = None) -> None:
    metrics.increment(
        "inbound_events_rejected_total",
        provider=provider.upper() if provider else "UNKNOWN",
        reason=reason.lower() if reason else "unspecified",
    )


def record_event_deduplicated(provider: str) -> None:
    metrics.increment(
        "inbound_events_deduplicated_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_event_processed(provider: str, event_type: str) -> None:
    metrics.increment(
        "inbound_events_processed_total",
        provider=provider.upper() if provider else "UNKNOWN",
        event_type=event_type.upper() if event_type else "UNKNOWN",
    )


def record_event_failed(provider: str, reason: str | None = None) -> None:
    metrics.increment(
        "inbound_events_failed_total",
        provider=provider.upper() if provider else "UNKNOWN",
        reason=reason.lower() if reason else "unspecified",
    )


def record_event_retried(provider: str) -> None:
    metrics.increment(
        "inbound_events_retried_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_bounce_processed(provider: str, bounce_type: str = "UNKNOWN") -> None:
    metrics.increment(
        "inbound_bounces_processed_total",
        provider=provider.upper() if provider else "UNKNOWN",
        bounce_type=bounce_type.upper(),
    )


def record_complaint_processed(provider: str) -> None:
    metrics.increment(
        "inbound_complaints_processed_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_unsubscribe_processed(provider: str) -> None:
    metrics.increment(
        "inbound_unsubscribes_processed_total",
        provider=provider.upper() if provider else "UNKNOWN",
    )


def record_suppression_created(reason: str) -> None:
    metrics.increment(
        "inbound_suppression_created_total",
        reason=reason.upper() if reason else "UNKNOWN",
    )


def record_message_cancelled_due_to_event(reason: str) -> None:
    metrics.increment(
        "messages_cancelled_due_to_event_total",
        reason=reason.lower() if reason else "unspecified",
    )


def record_message_blocked_at_send_due_to_suppression() -> None:
    metrics.increment("messages_blocked_at_send_due_to_suppression_total")

