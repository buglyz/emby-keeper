import asyncio
from urllib.parse import urlparse

import pytest

from embykeeper.config import config
from embykeeper.emby.main import EmbyManager, _extract_emby_item_id, _same_emby_origin
from embykeeper.runinfo import RunStatus, _running_runs
from embykeeper.schema import Config, EmbyAccount


@pytest.fixture(autouse=True)
def reset_config(tmp_path):
    callbacks = {
        key: {name: handlers[:] for name, handlers in value.items()}
        for key, value in config._callbacks.items()
    }
    config.basedir = tmp_path
    config.set(Config())
    yield
    config.reset()
    config._callbacks = callbacks


def test_account_disable_cancels_independent_schedule(monkeypatch):
    async def run_test():
        manager = EmbyManager()
        scheduled_tasks = []

        async def idle_schedule():
            await asyncio.Event().wait()

        class DummyScheduler:
            def schedule(self):
                return idle_schedule()

        def fake_schedule_independent_account(account):
            scheduler = DummyScheduler()
            manager._schedulers[manager.get_spec(account)] = scheduler
            return scheduler

        monkeypatch.setattr(manager, "schedule_independent_account", fake_schedule_independent_account)

        original_start_scheduler = manager._start_scheduler

        def record_start_scheduler(account_spec, scheduler):
            original_start_scheduler(account_spec, scheduler)
            scheduled_tasks.append(manager._scheduler_tasks[account_spec])

        monkeypatch.setattr(manager, "_start_scheduler", record_start_scheduler)

        enabled = EmbyAccount(
            url="https://example.com",
            username="alice",
            interval_days="7",
            time_range="8:00AM",
        )
        disabled = enabled.model_copy(update={"enabled": False})

        config.set(Config(emby={"account": [enabled]}))
        await asyncio.sleep(0)

        assert manager.get_spec(enabled) in manager._scheduler_tasks
        assert scheduled_tasks and not scheduled_tasks[0].cancelled()

        config.set(Config(emby={"account": [disabled]}))
        await asyncio.sleep(0)

        assert manager.get_spec(enabled) not in manager._scheduler_tasks
        assert scheduled_tasks[0].cancelled()

    asyncio.run(run_test())


def test_watch_main_ignores_disabled_accounts(monkeypatch):
    async def run_test():
        manager = EmbyManager()
        disabled = EmbyAccount(url="https://example.com", username="alice", enabled=False)

        def fail_if_emby_is_created(_account):
            raise AssertionError("disabled accounts should not create Emby clients")

        monkeypatch.setattr("embykeeper.emby.main.Emby", fail_if_emby_is_created)

        assert await manager._watch_main([disabled], instant=True) is None

    asyncio.run(run_test())


def test_watch_main_handles_missing_play_id_item_without_exception_log(monkeypatch):
    async def run_test():
        manager = EmbyManager()
        account = EmbyAccount(url="https://example.com", username="alice", play_id="missing-item")

        class DummyLog:
            def info(self, *_args, **_kwargs):
                return None

            def warning(self, *_args, **_kwargs):
                return None

        class DummyEmby:
            log = DummyLog()

            def __init__(self, _account):
                return None

            async def ensure_authenticated(self):
                return True

            async def get_item(self, _item_id):
                return None

            async def watch(self):
                raise AssertionError("watch should not run without an item")

        def fail_show_exception(*_args, **_kwargs):
            raise AssertionError("missing item should not be treated as an unexpected exception")

        monkeypatch.setattr("embykeeper.emby.main.Emby", DummyEmby)
        monkeypatch.setattr("embykeeper.emby.main.show_exception", fail_show_exception)

        ctx = await manager._watch_main([account], instant=True)

        assert ctx.status == RunStatus.FAIL
        assert ctx.status_info == "保活失败"

    asyncio.run(run_test())


def test_schedule_all_handles_missing_emby_config():
    async def run_test():
        config.set(Config(emby=None))
        manager = EmbyManager()

        try:
            assert await manager.schedule_all() is None
            assert await manager.run_all(instant=True) is None
        finally:
            await manager.shutdown()

    asyncio.run(run_test())


def test_watch_main_marks_context_cancelled(monkeypatch):
    async def run_test():
        manager = EmbyManager()
        account = EmbyAccount(url="https://example.com", username="alice")
        started = asyncio.Event()
        blocker = asyncio.Event()

        class DummyLog:
            def info(self, *_args, **_kwargs):
                return None

            def warning(self, *_args, **_kwargs):
                return None

        class DummyEmby:
            log = DummyLog()

            def __init__(self, _account):
                return None

            async def ensure_authenticated(self):
                started.set()
                await blocker.wait()
                return True

        monkeypatch.setattr("embykeeper.emby.main.Emby", DummyEmby)
        _running_runs.clear()

        task = asyncio.create_task(manager._watch_main([account], instant=True))
        await asyncio.wait_for(started.wait(), timeout=1)
        ctx = next(iter(_running_runs.values()))
        task.cancel()

        try:
            with pytest.raises(asyncio.CancelledError):
                await task

            assert ctx.status == RunStatus.CANCELLED
            assert ctx.id not in _running_runs
        finally:
            _running_runs.clear()

    asyncio.run(run_test())


def test_completed_independent_watch_task_is_removed(monkeypatch):
    async def run_test():
        manager = EmbyManager()
        account = EmbyAccount(
            url="https://example.com",
            username="alice",
            interval_days="7",
            time_range="8:00AM",
        )

        async def fake_watch_main(_accounts, _instant):
            return None

        monkeypatch.setattr(manager, "_watch_main", fake_watch_main)

        try:
            scheduler = manager.schedule_independent_account(account)
            task = scheduler.func(None)

            assert manager._tasks[manager.get_spec(account)] is task
            await task
            await asyncio.sleep(0)

            assert manager.get_spec(account) not in manager._tasks
        finally:
            await manager.shutdown()

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("url", "item_id"),
    [
        ("https://example.com/web/#/details?id=item-1&serverId=server-1", "item-1"),
        ("https://example.com/web/index.html#!/item?id=item-2&serverId=server-1", "item-2"),
        ("https://example.com/web/index.html?id=item-3&serverId=server-1", "item-3"),
        ("https://example.com/web/index.html?itemId=item-4", "item-4"),
    ],
)
def test_extract_emby_item_id_from_supported_play_urls(url, item_id):
    assert _extract_emby_item_id(urlparse(url)) == item_id


def test_extract_emby_item_id_returns_none_for_invalid_url():
    assert _extract_emby_item_id(urlparse("https://example.com/web/#/home")) is None


def test_extract_emby_item_id_trims_blank_values():
    assert _extract_emby_item_id(urlparse("https://example.com/web/index.html?id=%20item-1%20")) == "item-1"
    assert _extract_emby_item_id(urlparse("https://example.com/web/index.html?id=%20%20")) is None


def test_same_emby_origin_requires_matching_scheme():
    account = EmbyAccount(url="http://example.com:443", username="alice")

    assert _same_emby_origin(account, urlparse("https://example.com")) is False


def test_same_emby_origin_rejects_invalid_url_port():
    account = EmbyAccount(url="https://example.com", username="alice")

    assert _same_emby_origin(account, urlparse("https://example.com:invalid/web")) is False


def test_shutdown_unregisters_config_callback(monkeypatch):
    async def run_test():
        manager = EmbyManager()
        calls = []

        def fake_schedule_unified_accounts():
            calls.append("scheduled")

        monkeypatch.setattr(manager, "schedule_unified_accounts", fake_schedule_unified_accounts)

        await manager.shutdown()

        account = EmbyAccount(url="https://example.com", username="alice")
        config.set(Config(emby={"account": [account]}))

        assert calls == []

    asyncio.run(run_test())
