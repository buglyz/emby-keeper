import asyncio

import pytest

from embykeeper.config import config
from embykeeper.emby.main import EmbyManager
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
