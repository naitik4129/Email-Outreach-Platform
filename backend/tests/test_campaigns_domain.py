from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.campaigns.schemas import (
    CampaignSettingsCreateIn,
    SequenceStepCreateIn,
)
from app.modules.campaigns.settings_service import (
    _bitmask_to_weekdays,
    _validate_timezone,
    _weekdays_to_bitmask,
)

# ---------------------------------------------------------------------------
# weekday bitmask conversion (Mon=bit0 .. Sun=bit6, documented in schemas.py)
# ---------------------------------------------------------------------------


def test_weekdays_to_bitmask_monday_only() -> None:
    assert _weekdays_to_bitmask([1]) == 0b0000001


def test_weekdays_to_bitmask_sunday_only() -> None:
    assert _weekdays_to_bitmask([7]) == 0b1000000


def test_weekdays_to_bitmask_weekdays_mon_to_fri() -> None:
    assert _weekdays_to_bitmask([1, 2, 3, 4, 5]) == 0b0011111


def test_weekdays_to_bitmask_all_days() -> None:
    assert _weekdays_to_bitmask([1, 2, 3, 4, 5, 6, 7]) == 127


def test_bitmask_to_weekdays_roundtrip() -> None:
    for combo in ([1], [7], [1, 2, 3, 4, 5], [6, 7], list(range(1, 8))):
        mask = _weekdays_to_bitmask(combo)
        assert _bitmask_to_weekdays(mask) == sorted(combo)


def test_bitmask_to_weekdays_single_bit() -> None:
    assert _bitmask_to_weekdays(0b0000100) == [3]


# ---------------------------------------------------------------------------
# timezone validation
# ---------------------------------------------------------------------------


def test_validate_timezone_accepts_known_iana_zone() -> None:
    assert _validate_timezone("America/New_York") == "America/New_York"
    assert _validate_timezone("Asia/Kolkata") == "Asia/Kolkata"
    assert _validate_timezone("Europe/London") == "Europe/London"
    assert _validate_timezone("UTC") == "UTC"


def test_validate_timezone_rejects_offset_string() -> None:
    with pytest.raises(AppError) as exc_info:
        _validate_timezone("UTC+5:30")
    assert exc_info.value.status_code == 422


def test_validate_timezone_rejects_us_abbreviation() -> None:
    with pytest.raises(AppError) as exc_info:
        _validate_timezone("PST")
    assert exc_info.value.status_code == 422


def test_validate_timezone_rejects_garbage() -> None:
    with pytest.raises(AppError):
        _validate_timezone("not-a-timezone")


# ---------------------------------------------------------------------------
# CampaignSettingsCreateIn schema validation
# ---------------------------------------------------------------------------


def _settings_payload(**overrides: object) -> dict:
    payload = {
        "timezone": "America/New_York",
        "weekdays": [1, 2, 3, 4, 5],
        "window_start_local": "09:00:00",
        "window_end_local": "17:00:00",
        "daily_limit": 50,
    }
    payload.update(overrides)
    return payload


def test_settings_schema_accepts_valid_payload() -> None:
    model = CampaignSettingsCreateIn(**_settings_payload())
    assert model.weekdays == [1, 2, 3, 4, 5]


def test_settings_schema_rejects_weekday_out_of_range() -> None:
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(**_settings_payload(weekdays=[0]))
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(**_settings_payload(weekdays=[8]))


def test_settings_schema_rejects_duplicate_weekdays() -> None:
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(**_settings_payload(weekdays=[1, 1, 2]))


def test_settings_schema_rejects_window_end_before_start() -> None:
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(
            **_settings_payload(
                window_start_local="17:00:00", window_end_local="09:00:00"
            )
        )


def test_settings_schema_rejects_equal_window_bounds() -> None:
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(
            **_settings_payload(
                window_start_local="09:00:00", window_end_local="09:00:00"
            )
        )


def test_settings_schema_rejects_negative_daily_limit() -> None:
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(**_settings_payload(daily_limit=-1))


def test_settings_schema_rejects_zero_daily_limit() -> None:
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(**_settings_payload(daily_limit=0))


def test_settings_schema_rejects_daily_limit_above_schema_bound() -> None:
    with pytest.raises(ValidationError):
        CampaignSettingsCreateIn(**_settings_payload(daily_limit=1_000_000))


def test_settings_schema_allows_null_daily_limit() -> None:
    model = CampaignSettingsCreateIn(**_settings_payload(daily_limit=None))
    assert model.daily_limit is None


# ---------------------------------------------------------------------------
# SequenceStepCreateIn schema shape
# ---------------------------------------------------------------------------


def test_sequence_step_email_schema_valid() -> None:
    model = SequenceStepCreateIn(
        kind="EMAIL", position=1, email_subject="Hi", email_body_html="<p>Hi</p>"
    )
    assert model.kind == "EMAIL"


def test_sequence_step_wait_schema_valid() -> None:
    model = SequenceStepCreateIn(kind="WAIT", position=2, wait_duration_minutes=1440)
    assert model.wait_duration_minutes == 1440


def test_sequence_step_wait_duration_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SequenceStepCreateIn(kind="WAIT", position=1, wait_duration_minutes=0)
    with pytest.raises(ValidationError):
        SequenceStepCreateIn(kind="WAIT", position=1, wait_duration_minutes=-5)


def test_sequence_step_position_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        SequenceStepCreateIn(kind="WAIT", position=0, wait_duration_minutes=60)


def test_sequence_step_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        SequenceStepCreateIn(kind="SMS", position=1)  # type: ignore[arg-type]
