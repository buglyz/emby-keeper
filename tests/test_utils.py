import asyncio

import pytest

from embykeeper.utils import AsyncTaskPool, batch, format_byte_human, truncate_str


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
