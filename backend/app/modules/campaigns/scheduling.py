from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.errors import AppError

# Generous safety bound for the gap-resolution scan below (a real DST gap is
# at most a couple of hours) and for the day-by-day window search (a
# pathological weekday_set/window combination should fail fast, not hang).
_MAX_GAP_SCAN_MINUTES = 1440
_DEFAULT_MAX_DAYS = 400


def _fold_offsets(
    naive: datetime, tz: ZoneInfo
) -> tuple[timedelta | None, timedelta | None]:
    return (
        naive.replace(tzinfo=tz, fold=0).utcoffset(),
        naive.replace(tzinfo=tz, fold=1).utcoffset(),
    )


def _resolve_local_to_utc(
    naive: datetime, tz: ZoneInfo, *, prefer_later: bool
) -> datetime:
    """Resolve one naive local datetime to UTC, handling both DST cases.

    Ambiguous (fall-back) times exist twice: fold=0 is the earlier UTC
    occurrence (larger/daylight offset), fold=1 is the later one
    (smaller/standard offset) -- distinguished here by off0 > off1.

    Nonexistent (spring-forward gap) times never occur on the wall clock at
    all -- distinguished by off1 > off0 (the post-transition offset is
    larger). Per the documented policy, these shift forward to the first
    valid local instant after the gap, found by scanning forward minute by
    minute until fold=0 and fold=1 agree again.
    """
    off0, off1 = _fold_offsets(naive, tz)
    if off0 == off1:
        return naive.replace(tzinfo=tz).astimezone(UTC)

    if off1 is not None and off0 is not None and off1 > off0:
        probe = naive
        for _ in range(_MAX_GAP_SCAN_MINUTES):
            probe += timedelta(minutes=1)
            p0, p1 = _fold_offsets(probe, tz)
            if p0 == p1:
                return probe.replace(tzinfo=tz).astimezone(UTC)
        raise AppError(
            "internal_error",
            f"Could not resolve a DST gap for timezone {tz.key}",
            status_code=500,
        )

    chosen_fold = 1 if prefer_later else 0
    return naive.replace(tzinfo=tz, fold=chosen_fold).astimezone(UTC)


def project_into_window(
    lower_bound_utc: datetime,
    *,
    timezone: str,
    weekday_set: int,
    window_start_local: time,
    window_end_local: time,
    max_days: int = _DEFAULT_MAX_DAYS,
) -> datetime:
    """Earliest UTC instant >= lower_bound_utc that falls inside an allowed
    weekday's sending window, per SCHEDULER.md.

    weekday_set uses the same bit order as
    campaign_settings_versions.weekday_set / settings_service.py's
    _weekdays_to_bitmask: bit 0 = Monday .. bit 6 = Sunday, matching Python's
    date.weekday() directly. A date whose local window collapses to
    end<=start under DST (fall-back shortening it past empty) is skipped
    entirely, not inverted.

    This is the entire scheduling surface Phase 8 needs: it is only ever
    called once per enrollment, for the first Email step, with
    lower_bound_utc = the campaign's activation start_at. The signature is
    written generically enough that a future sequence-progression phase can
    reuse it unchanged for later steps (lower_bound = prior message's
    accepted time + wait duration), but Phase 8 itself never plans past the
    first step.
    """
    tz = ZoneInfo(timezone)
    local_date: date = lower_bound_utc.astimezone(tz).date()

    for _ in range(max_days):
        if not (weekday_set >> local_date.weekday()) & 1:
            local_date += timedelta(days=1)
            continue

        start_utc = _resolve_local_to_utc(
            datetime.combine(local_date, window_start_local), tz, prefer_later=True
        )
        end_utc = _resolve_local_to_utc(
            datetime.combine(local_date, window_end_local), tz, prefer_later=False
        )

        if end_utc > start_utc:
            if start_utc <= lower_bound_utc < end_utc:
                return lower_bound_utc
            if lower_bound_utc < start_utc:
                return start_utc

        local_date += timedelta(days=1)

    raise AppError(
        "internal_error",
        "No eligible sending window found within the configured bound",
        status_code=500,
    )
