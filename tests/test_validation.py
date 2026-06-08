import pytest
from fastapi import HTTPException

from embykeeperapi.validation import validate_schedule_fields


def test_validate_schedule_fields_accepts_valid_interval_and_time_range():
    validate_schedule_fields("<7,12>", "<8:00AM,9:00AM>")
    validate_schedule_fields("7", "8:00AM")


@pytest.mark.parametrize(
    ("interval_days", "time_range"),
    [
        ("<12,7>", "8:00AM"),
        ("-7", "8:00AM"),
        ("0", "8:00AM"),
        ("<0,7>", "8:00AM"),
        ("7 trailing", "8:00AM"),
        ("7", "8:00AM trailing"),
        ("7", "<8:00AM>"),
    ],
)
def test_validate_schedule_fields_rejects_invalid_values(interval_days, time_range):
    with pytest.raises(HTTPException) as exc:
        validate_schedule_fields(interval_days, time_range)

    assert exc.value.status_code == 400


def test_validate_schedule_fields_can_reject_missing_global_values():
    with pytest.raises(HTTPException) as exc:
        validate_schedule_fields(None, "8:00AM", use_defaults=False)

    assert exc.value.status_code == 400
    assert "interval_days cannot be empty" in exc.value.detail
