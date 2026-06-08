import asyncio
from datetime import datetime

import pytest

import embykeeper.schedule as schedule_module
from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus, _running_runs
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


@pytest.mark.parametrize("time_range", ["8", "800"])
def test_scheduler_from_str_rejects_date_only_time_text(time_range):
    with pytest.raises(ValueError):
        Scheduler.from_str(noop, interval_days="7", time_range=time_range)


def test_scheduler_from_str_accepts_valid_interval_range():
    scheduler = Scheduler.from_str(noop, interval_days="<7,12>", time_range="<8:00AM,9:00AM>")

    assert scheduler.days == [7, 12]
    assert scheduler.start_time.hour == 8
    assert scheduler.end_time.hour == 9


def test_scheduler_from_str_trims_outer_schedule_whitespace():
    scheduler = Scheduler.from_str(noop, interval_days=" <7,12> ", time_range=" <8:00AM,9:00AM> ")

    assert scheduler.days == [7, 12]
    assert scheduler.start_time.hour == 8
    assert scheduler.end_time.hour == 9


def test_scheduler_from_str_accepts_integer_interval():
    scheduler = Scheduler.from_str(noop, interval_days=7, time_range="8:00AM")

    assert scheduler.days == 7


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


@pytest.mark.parametrize("days", [-1, True, "7", [7], [12, 7], [-1, 7], [True, 7]])
def test_scheduler_constructor_rejects_invalid_days(days):
    with pytest.raises(ValueError):
        Scheduler(noop, days=days, start_time=None, end_time=None)


@pytest.mark.parametrize("start_time", [8, True, object()])
def test_scheduler_constructor_rejects_invalid_time_values(start_time):
    with pytest.raises(ValueError):
        Scheduler(noop, days=1, start_time=start_time, end_time=None)


def test_scheduler_constructor_accepts_zero_day_range():
    scheduler = Scheduler(noop, days=[0, 0], start_time=None, end_time=None)

    assert scheduler.days == [0, 0]


def test_scheduler_zero_to_nonzero_day_range_repeats(monkeypatch):
    async def run_test():
        calls = 0

        async def func(_ctx):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError()

        scheduler = Scheduler(func, days=[0, 1], start_time=None, end_time=None)
        monkeypatch.setattr(scheduler, "_get_next_time", lambda: datetime.now())

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(scheduler.schedule(), timeout=1)

        assert calls == 2

    asyncio.run(run_test())


def test_scheduler_ignores_invalid_cached_next_time(tmp_path, monkeypatch):
    from embykeeper.cache import cache

    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    scheduler = Scheduler(
        noop,
        days=1,
        start_time="8:00AM",
        end_time="8:00AM",
        sid="test.invalid-cache",
    )
    cache.set(
        "scheduler.test.invalid-cache",
        {
            "config_hash": scheduler._get_scheduler_config(),
            "next_time": "not-a-date",
        },
    )
    monkeypatch.setattr(
        schedule_module,
        "next_random_datetime",
        lambda *, start_time, end_time, interval_days: datetime(2026, 1, 1, 8, 0),
    )

    try:
        assert scheduler.next_time == datetime(2026, 1, 1, 8, 0)
    finally:
        cache.delete("scheduler.test.invalid-cache")


def test_scheduler_ignores_non_object_cached_next_time(tmp_path, monkeypatch):
    from embykeeper.cache import cache

    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    scheduler = Scheduler(
        noop,
        days=1,
        start_time="8:00AM",
        end_time="8:00AM",
        sid="test.invalid-cache-type",
    )
    cache.set("scheduler.test.invalid-cache-type", "not-an-object")
    monkeypatch.setattr(
        schedule_module,
        "next_random_datetime",
        lambda *, start_time, end_time, interval_days: datetime(2026, 1, 1, 8, 0),
    )

    try:
        assert scheduler.next_time == datetime(2026, 1, 1, 8, 0)
    finally:
        cache.delete("scheduler.test.invalid-cache-type")


def test_scheduler_ignores_timezone_aware_cached_next_time(tmp_path, monkeypatch):
    from embykeeper.cache import cache

    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    scheduler = Scheduler(
        noop,
        days=1,
        start_time="8:00AM",
        end_time="8:00AM",
        sid="test.aware-cache-time",
    )
    cache.set(
        "scheduler.test.aware-cache-time",
        {
            "config_hash": scheduler._get_scheduler_config(),
            "next_time": "2026-01-01T08:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        schedule_module,
        "next_random_datetime",
        lambda *, start_time, end_time, interval_days: datetime(2026, 1, 1, 8, 0),
    )

    try:
        assert scheduler.next_time == datetime(2026, 1, 1, 8, 0)
    finally:
        cache.delete("scheduler.test.aware-cache-time")


def test_scheduler_ignores_cache_read_failure(monkeypatch):
    def fail_get(_key, default=None):
        raise OSError("read failed")

    monkeypatch.setattr("embykeeper.cache.cache.get", fail_get)
    monkeypatch.setattr(
        schedule_module,
        "next_random_datetime",
        lambda *, start_time, end_time, interval_days: datetime(2026, 1, 1, 8, 0),
    )
    scheduler = Scheduler(noop, days=1, start_time="8:00AM", end_time="8:00AM", sid="test.read-fail")

    assert scheduler.next_time == datetime(2026, 1, 1, 8, 0)


def test_scheduler_ignores_cache_write_failure(monkeypatch):
    def fail_set(_key, _value):
        raise OSError("write failed")

    monkeypatch.setattr("embykeeper.cache.cache.set", fail_set)
    monkeypatch.setattr(
        schedule_module,
        "next_random_datetime",
        lambda *, start_time, end_time, interval_days: datetime(2026, 1, 1, 8, 0),
    )
    scheduler = Scheduler(noop, days=1, start_time="8:00AM", end_time="8:00AM", sid="test.write-fail")

    assert scheduler.next_time == datetime(2026, 1, 1, 8, 0)


def test_scheduler_ignores_cache_delete_failure(monkeypatch):
    async def run_test():
        calls = 0

        async def func(_ctx):
            nonlocal calls
            calls += 1

        def fail_delete(_key):
            raise OSError("delete failed")

        scheduler = Scheduler(func, days=0, start_time=None, end_time=None, sid="test.delete-fail")
        monkeypatch.setattr(scheduler, "_get_next_time", lambda: datetime.now())
        monkeypatch.setattr("embykeeper.cache.cache.delete", fail_delete)

        await asyncio.wait_for(scheduler.schedule(), timeout=1)

        assert calls == 1

    asyncio.run(run_test())


def test_scheduler_cancels_running_function_when_schedule_is_cancelled(monkeypatch):
    async def run_test():
        started = asyncio.Event()
        blocker = asyncio.Event()
        worker_cancelled = asyncio.Event()

        async def func(_ctx):
            started.set()
            try:
                await blocker.wait()
            except asyncio.CancelledError:
                worker_cancelled.set()
                raise

        scheduler = Scheduler(func, days=0, start_time=None, end_time=None)
        monkeypatch.setattr(scheduler, "_get_next_time", lambda: datetime.now())

        task = asyncio.create_task(scheduler.schedule())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()

        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(worker_cancelled.wait(), timeout=1)
        finally:
            blocker.set()
            await asyncio.sleep(0)

    asyncio.run(run_test())


def test_scheduler_marks_function_self_cancellation_as_error(monkeypatch):
    async def run_test():
        ctx = RunContext.prepare("self-cancel")

        async def func(_ctx):
            raise asyncio.CancelledError()

        scheduler = Scheduler(
            func,
            days=0,
            start_time=None,
            end_time=None,
            on_next_time=lambda _next_time: ctx,
        )
        monkeypatch.setattr(scheduler, "_get_next_time", lambda: datetime.now())

        try:
            with pytest.raises(asyncio.CancelledError):
                await scheduler.schedule()

            assert ctx.status == RunStatus.ERROR
            assert ctx.status_info == "任务在运行时被取消"
        finally:
            _running_runs.clear()

    asyncio.run(run_test())


def test_scheduler_ignores_next_time_callback_failure(monkeypatch):
    async def run_test():
        calls = 0

        async def func(_ctx):
            nonlocal calls
            calls += 1

        def fail_on_next_time(_next_time):
            raise RuntimeError("callback failed")

        scheduler = Scheduler(
            func,
            days=0,
            start_time=None,
            end_time=None,
            on_next_time=fail_on_next_time,
        )
        monkeypatch.setattr(scheduler, "_get_next_time", lambda: datetime.now())

        await asyncio.wait_for(scheduler.schedule(), timeout=1)

        assert calls == 1

    asyncio.run(run_test())
