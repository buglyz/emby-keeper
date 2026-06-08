import pytest

from embykeeper.config import config
from embykeeper.schedule import Scheduler
from embykeeper.schema import Config


@pytest.fixture(autouse=True)
def reset_config():
    config.set(Config())
    yield
    config.reset()


async def noop(_ctx):
    return None


def test_scheduler_from_str_rejects_non_positive_intervals():
    for interval_days in ("0", "-7", "<0,7>", "<7,0>"):
        with pytest.raises(ValueError):
            Scheduler.from_str(noop, interval_days=interval_days, time_range="8:00AM")


def test_scheduler_from_str_rejects_invalid_interval_range_order():
    with pytest.raises(ValueError):
        Scheduler.from_str(noop, interval_days="<12,7>", time_range="8:00AM")


def test_scheduler_from_str_rejects_trailing_interval_text():
    with pytest.raises(ValueError):
        Scheduler.from_str(noop, interval_days="<7,12> trailing", time_range="8:00AM")


def test_scheduler_from_str_accepts_valid_interval_range():
    scheduler = Scheduler.from_str(noop, interval_days="<7,12>", time_range="<8:00AM,9:00AM>")

    assert scheduler.days == [7, 12]
    assert scheduler.start_time.hour == 8
    assert scheduler.end_time.hour == 9
