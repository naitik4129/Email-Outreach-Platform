from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from app.core.errors import AppError
from app.modules.campaigns.scheduling import project_into_window

# Bit 0 = Monday .. bit 6 = Sunday, matching campaign_settings_versions.weekday_set /
# settings_service.py's _weekdays_to_bitmask.
_MON_FRI = 0b0011111
_ALL_DAYS = 0b1111111


def _utc(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Same-day window, no DST involved
# ---------------------------------------------------------------------------


def test_lower_bound_inside_window_returned_unchanged() -> None:
    # 2024-06-03 is a Monday. 13:00 UTC = 09:00 America/New_York (EDT, UTC-4).
    lower = _utc(2024, 6, 3, 13, 0)
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_MON_FRI,
        window_start_local=time(9, 0),
        window_end_local=time(17, 0),
    )
    assert result == lower


def test_lower_bound_before_window_advances_to_window_start() -> None:
    # 05:00 UTC = 01:00 EDT -- before the 09:00-17:00 window opens.
    lower = _utc(2024, 6, 3, 5, 0)
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_MON_FRI,
        window_start_local=time(9, 0),
        window_end_local=time(17, 0),
    )
    assert result == _utc(2024, 6, 3, 13, 0)


def test_lower_bound_after_window_advances_to_next_allowed_day() -> None:
    # 22:00 UTC = 18:00 EDT -- after the window has closed for the day.
    lower = _utc(2024, 6, 3, 22, 0)
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_MON_FRI,
        window_start_local=time(9, 0),
        window_end_local=time(17, 0),
    )
    assert result == _utc(2024, 6, 4, 13, 0)


def test_friday_after_window_skips_weekend_to_monday() -> None:
    # 2024-06-07 is a Friday. Weekend excluded by weekday_set -- must land
    # on the following Monday, not Saturday/Sunday.
    lower = _utc(2024, 6, 7, 22, 0)
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_MON_FRI,
        window_start_local=time(9, 0),
        window_end_local=time(17, 0),
    )
    assert result == _utc(2024, 6, 10, 13, 0)


def test_all_days_allowed_lands_on_saturday() -> None:
    # Same starting instant as above, but every day is allowed -- must land
    # on Saturday, confirming weekday_set (not a hardcoded weekday skip) is
    # what drives the skip in the previous test.
    lower = _utc(2024, 6, 7, 22, 0)
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_ALL_DAYS,
        window_start_local=time(9, 0),
        window_end_local=time(17, 0),
    )
    assert result == _utc(2024, 6, 8, 13, 0)


# ---------------------------------------------------------------------------
# DST: America/New_York spring-forward gap (2024-03-10, 02:00 -> 03:00)
# ---------------------------------------------------------------------------


def test_spring_forward_gap_shifts_forward_to_first_valid_instant() -> None:
    lower = _utc(2024, 3, 10, 0, 0)
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_ALL_DAYS,
        window_start_local=time(2, 30),  # inside the nonexistent 02:00-03:00 hour
        window_end_local=time(17, 0),
    )
    # First valid local instant after the gap is 03:00 EDT = 07:00 UTC.
    assert result == _utc(2024, 3, 10, 7, 0)


# ---------------------------------------------------------------------------
# DST: America/New_York fall-back ambiguity (2024-11-03, 02:00 EDT -> 01:00 EST)
# ---------------------------------------------------------------------------


def test_fall_back_ambiguous_window_start_picks_later_occurrence() -> None:
    lower = _utc(2024, 11, 3, 0, 0)
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_ALL_DAYS,
        window_start_local=time(1, 30),  # occurs twice: EDT then EST
        window_end_local=time(17, 0),
    )
    # The later (EST, UTC-5) occurrence: 01:30 EST = 06:30 UTC.
    assert result == _utc(2024, 11, 3, 6, 30)


def test_fall_back_ambiguous_window_end_picks_earlier_occurrence() -> None:
    # Window is [00:00 EDT, 01:30 EDT) = [04:00 UTC, 05:30 UTC) -- the earlier
    # occurrence of the ambiguous end bound, conservatively shortening the
    # window rather than extending it into the repeated hour.
    lower = _utc(2024, 11, 3, 5, 0)  # 01:00 EDT, inside that window
    result = project_into_window(
        lower,
        timezone="America/New_York",
        weekday_set=_ALL_DAYS,
        window_start_local=time(0, 0),
        window_end_local=time(1, 30),
    )
    assert result == lower


# ---------------------------------------------------------------------------
# Exhaustion / determinism
# ---------------------------------------------------------------------------


def test_no_eligible_window_raises_after_max_days() -> None:
    with pytest.raises(AppError) as exc_info:
        project_into_window(
            _utc(2024, 6, 3),
            timezone="UTC",
            weekday_set=0,  # no weekday allowed at all
            window_start_local=time(9, 0),
            window_end_local=time(17, 0),
            max_days=10,
        )
    assert exc_info.value.status_code == 500


def test_projection_is_idempotent() -> None:
    lower = _utc(2024, 6, 3, 5, 0)
    kwargs = {
        "timezone": "America/New_York",
        "weekday_set": _MON_FRI,
        "window_start_local": time(9, 0),
        "window_end_local": time(17, 0),
    }
    once = project_into_window(lower, **kwargs)
    twice = project_into_window(once, **kwargs)
    assert once == twice
