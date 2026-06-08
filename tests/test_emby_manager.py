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


def test_same_emby_origin_requires_matching_scheme():
    account = EmbyAccount(url="http://example.com:443", username="alice")

    assert _same_emby_origin(account, urlparse("https://example.com")) is False


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
