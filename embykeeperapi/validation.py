import re

from dateutil import parser
from fastapi import HTTPException

from embykeeper.schema import DEFAULT_EMBY_INTERVAL_DAYS, DEFAULT_TIME_RANGE
from embykeeper.utils import looks_like_time_text


def _parse_time_value(value: str):
    if not looks_like_time_text(value):
        raise ValueError("time_range must include ':' or AM/PM")
    return parser.parse(value).time()


def validate_schedule_fields(interval_days=None, time_range=None, *, use_defaults: bool = True):
    """Validate scheduler strings before saving Web UI config/account data."""
    interval = interval_days if interval_days is not None or not use_defaults else DEFAULT_EMBY_INTERVAL_DAYS
    watch_time = time_range if time_range is not None or not use_defaults else DEFAULT_TIME_RANGE
    if interval in (None, ""):
        raise HTTPException(status_code=400, detail="interval_days cannot be empty")
    if watch_time in (None, ""):
        raise HTTPException(status_code=400, detail="time_range cannot be empty")

    try:
        interval = str(interval)
        interval_range_match = re.fullmatch(r"<\s*(\d+)\s*,\s*(\d+)\s*>", interval)
        if interval_range_match:
            min_days = int(interval_range_match.group(1))
            max_days = int(interval_range_match.group(2))
            if min_days <= 0 or max_days <= 0:
                raise ValueError("interval_days values must be greater than 0")
            if min_days > max_days:
                raise ValueError("interval_days min must be <= max")
        else:
            fixed_days = int(interval)
            if fixed_days <= 0:
                raise ValueError("interval_days must be greater than 0")

        watch_time = str(watch_time)
        time_range_match = re.fullmatch(r"<\s*(.*?)\s*,\s*(.*?)\s*>", watch_time)
        if time_range_match:
            _parse_time_value(time_range_match.group(1))
            _parse_time_value(time_range_match.group(2))
        else:
            _parse_time_value(watch_time)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid schedule settings: {e}")
