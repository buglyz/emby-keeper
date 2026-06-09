import asyncio
from datetime import datetime, time

import pytest

from embykeeper.schema import ProxyConfig
from embykeeper.utils import (
    AsyncTaskPool,
    batch,
    distribute_numbers,
    format_exception_summary,
    format_byte_human,
    get_proxy_str,
    looks_like_time_text,
    nonblocking,
    next_random_datetime,
    truncate_str,
)


def test_truncate_str_uses_requested_prefix_length():
    assert truncate_str("abcdefghijklmnop", 10) == "abcdefghij..."


def test_truncate_str_keeps_short_text():
    assert truncate_str("abc", 10) == "abc"


def test_batch_splits_iterable():
    assert list(batch([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_batch_rejects_non_positive_size():
    with pytest.raises(ValueError):
        list(batch([1, 2, 3], 0))


def test_looks_like_time_text_requires_explicit_time_marker():
    assert looks_like_time_text("8:00AM") is True
    assert looks_like_time_text("8pm") is True
    assert looks_like_time_text("800") is False
    assert looks_like_time_text("spam") is False


def test_format_byte_human_uses_byte_pluralization():
    assert format_byte_human(1) == "1 Byte"
    assert format_byte_human(2) == "2 Bytes"


def test_format_byte_human_supports_petabytes():
    assert format_byte_human(1024**5) == "1.00 PB"


def test_format_exception_summary_includes_type_and_message_with_limit():
    summary = format_exception_summary(RuntimeError("x" * 80), limit=40)

    assert summary.startswith("RuntimeError:")
    assert len(summary) <= 40
    assert summary.endswith("...")


def test_get_proxy_str_quotes_credentials():
    proxy = ProxyConfig(
        scheme="http",
        hostname="127.0.0.1",
        port=1080,
        username="user@example.com",
        password="p@ss:word",
    )

    assert get_proxy_str(proxy) == "http://user%40example.com:p%40ss%3Aword@127.0.0.1:1080"


def test_get_proxy_str_brackets_ipv6_hosts():
    proxy = ProxyConfig(scheme="http", hostname="::1", port=1080)

    assert get_proxy_str(proxy) == "http://[::1]:1080"


def test_get_proxy_str_ignores_incomplete_proxy():
    assert get_proxy_str(ProxyConfig(scheme="http", hostname=None, port=1080)) is None
    assert get_proxy_str(ProxyConfig(scheme="http", hostname="127.0.0.1", port=None)) is None
    assert get_proxy_str(ProxyConfig(scheme=None, hostname="127.0.0.1", port=1080)) is None


def test_next_random_datetime_keeps_overnight_tail_in_target_interval(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 1, 1, 12, 0)

    monkeypatch.setattr("embykeeper.utils.datetime", FrozenDateTime)
    monkeypatch.setattr("embykeeper.utils.random_time", lambda _start, _end: time(0, 30))

    assert next_random_datetime(time(23, 0), time(1, 0), interval_days=1) == datetime(2026, 1, 3, 0, 30)


def test_distribute_numbers_accepts_default_min_distance():
    values = distribute_numbers(0, 10, num_elements=3)

    assert len(values) == 3
    assert all(0 <= value <= 10 for value in values)


def test_distribute_numbers_does_not_mutate_base_values():
    base = [8, 2]

    distribute_numbers(0, 10, num_elements=1, base=base)

    assert base == [8, 2]


def test_distribute_numbers_accepts_single_point_range():
    assert distribute_numbers(0, 0, num_elements=1) == [0]


def test_distribute_numbers_can_fill_exact_distance_endpoints():
    assert distribute_numbers(0, 10, num_elements=2, min_distance=5, base=[5]) == [0, 10]


def test_distribute_numbers_respects_max_distance_between_neighbors(monkeypatch):
    seen = []

    def fake_uniform(start, end):
        seen.append((start, end))
        return start

    monkeypatch.setattr("embykeeper.utils.random.uniform", fake_uniform)

    assert distribute_numbers(0, 10, num_elements=1, min_distance=1, max_distance=5, base=[0]) == [1]
    assert seen == [(1, 5)]


def test_distribute_numbers_rejects_negative_min_distance():
    with pytest.raises(ValueError):
        distribute_numbers(0, 10, min_distance=-1)


def test_distribute_numbers_rejects_negative_num_elements():
    with pytest.raises(ValueError):
        distribute_numbers(0, 10, num_elements=-1)


def test_distribute_numbers_rejects_max_distance_below_min_distance():
    with pytest.raises(ValueError):
        distribute_numbers(0, 10, min_distance=1, max_distance=0)


def test_distribute_numbers_rejects_base_values_outside_range():
    with pytest.raises(ValueError):
        distribute_numbers(0, 10, base=[11])


def test_distribute_numbers_rejects_base_values_violating_distance_range():
    with pytest.raises(ValueError):
        distribute_numbers(0, 10, min_distance=3, base=[1, 2])

    with pytest.raises(ValueError):
        distribute_numbers(0, 10, max_distance=3, base=[1, 5])


def test_async_task_pool_yields_all_precompleted_tasks():
    async def run_test():
        pool = AsyncTaskPool()

        async def done(value):
            return value

        pool.add(done(1))
        pool.add(done(2))
        await asyncio.gather(*pool.tasks)

        results = []
        async for task in pool.as_completed():
            results.append(task.result())

        assert sorted(results) == [1, 2]

    asyncio.run(asyncio.wait_for(run_test(), timeout=1))


def test_async_task_pool_accepts_future_without_name():
    async def run_test():
        pool = AsyncTaskPool()
        future = asyncio.Future()
        future.set_result("done")

        task = pool.add(future)

        assert task.get_name() == "async-task"
        assert await pool.wait() == ["done"]

    asyncio.run(asyncio.wait_for(run_test(), timeout=1))


def test_async_task_pool_cancellation_reaches_inner_task():
    async def run_test():
        pool = AsyncTaskPool()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        blocker = asyncio.Event()

        async def worker():
            started.set()
            try:
                await blocker.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = pool.add(worker())
        await started.wait()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert cancelled.is_set()

    asyncio.run(asyncio.wait_for(run_test(), timeout=1))


def test_nonblocking_lock_acquires_available_lock():
    async def run_test():
        lock = asyncio.Lock()

        async with nonblocking(lock) as acquired:
            assert acquired is True
            assert lock.locked() is True

        assert lock.locked() is False

    asyncio.run(run_test())


def test_nonblocking_lock_does_not_wait_for_locked_lock():
    async def run_test():
        lock = asyncio.Lock()
        await lock.acquire()

        try:
            async with nonblocking(lock) as acquired:
                assert acquired is False
                assert lock.locked() is True
        finally:
            lock.release()

    asyncio.run(asyncio.wait_for(run_test(), timeout=1))
