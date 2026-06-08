import asyncio

import pytest

from embykeeper.schema import ProxyConfig
from embykeeper.utils import (
    AsyncTaskPool,
    batch,
    distribute_numbers,
    format_byte_human,
    get_proxy_str,
    nonblocking,
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


def test_format_byte_human_uses_byte_pluralization():
    assert format_byte_human(1) == "1 Byte"
    assert format_byte_human(2) == "2 Bytes"


def test_format_byte_human_supports_petabytes():
    assert format_byte_human(1024**5) == "1.00 PB"


def test_get_proxy_str_quotes_credentials():
    proxy = ProxyConfig(
        scheme="http",
        hostname="127.0.0.1",
        port=1080,
        username="user@example.com",
        password="p@ss:word",
    )

    assert get_proxy_str(proxy) == "http://user%40example.com:p%40ss%3Aword@127.0.0.1:1080"


def test_distribute_numbers_accepts_default_min_distance():
    values = distribute_numbers(0, 10, num_elements=3)

    assert len(values) == 3
    assert all(0 <= value <= 10 for value in values)


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
