import asyncio
from datetime import datetime

import pytest

import embykeeper.schedule as schedule_module
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


def test_scheduler_uses_random_interval_from_range(monkeypatch):
    seen = {}

    def fake_next_random_datetime(*, start_time, end_time, interval_days):
        seen["interval_days"] = interval_days
        return datetime(2026, 1, 1, 8, 0)

    monkeypatch.setattr(schedule_module.random, "randint", lambda start, end: 9)
    monkeypatch.setattr(schedule_module, "next_random_datetime", fake_next_random_datetime)

    scheduler = Scheduler.from_str(noop, interval_days="<7,12>", time_range="<8:00AM,9:00AM>")

    assert scheduler.next_time == datetime(2026, 1, 1, 8, 0)
    assert seen["interval_days"] == 9


def test_scheduler_with_zero_days_runs_once(monkeypatch):
    async def run_test():
        calls = 0

        async def func(_ctx):
            nonlocal calls
            calls += 1

        scheduler = Scheduler(func, days=0, start_time=None, end_time=None)
        monkeypatch.setattr(scheduler, "_get_next_time", lambda: datetime.now())

        await asyncio.wait_for(scheduler.schedule(), timeout=1)

        assert calls == 1

    asyncio.run(run_test())
