import asyncio
from urllib.parse import urlparse

import pytest

from embykeeper.config import config
from embykeeper.emby.main import EmbyManager, _extract_emby_item_id
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
