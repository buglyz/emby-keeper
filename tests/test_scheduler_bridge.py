import asyncio

import pytest

from embykeeper.cache import cache
from embykeeper.config import config
from embykeeperapi.crypto import encrypt_token
from embykeeperapi.scheduler_bridge import SchedulerBridge


@pytest.fixture(autouse=True)
def reset_config_callbacks():
    callbacks = {
        key: {name: handlers[:] for name, handlers in value.items()}
        for key, value in config._callbacks.items()
    }
    yield
    config.reset()
    config._callbacks = callbacks


def test_api_bridge_uses_defaults_without_config_file(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
                "interval_days": "7",
                "time_range": "8:00AM",
            },
        )

        assert bridge.emby_manager is not None
        assert account_id in bridge.emby_manager._schedulers
        assert account_id in bridge.emby_manager._scheduler_tasks

        status = bridge.get_account_status(account_id)
        assert status["has_token"] is True
        assert status["next_schedule_time"] is not None

        schedules = bridge.get_schedule_info()
        assert schedules[0]["id"] == f"emby.watch.{account_id}"
        assert schedules[0]["account_spec"] == account_id
        assert schedules[0]["next_time"] is not None

        await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_watch_many_skips_disabled_and_independent_when_requested(tmp_path, monkeypatch):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)
        bridge.web_accounts.add("global@example.com", {"enabled": True})
        bridge.web_accounts.add(
            "independent@example.com",
            {"enabled": True, "interval_days": "7"},
        )
        bridge.web_accounts.add("disabled@example.com", {"enabled": False})

        triggered = []

        async def fake_trigger_watch(account_id):
            triggered.append(account_id)
            return {"run_id": account_id, "status": "started"}

        monkeypatch.setattr(bridge, "trigger_watch", fake_trigger_watch)

        result = await bridge.trigger_watch_many(unified_only=True)

        assert result["status"] == "started"
        assert triggered == ["global@example.com"]

        await bridge.shutdown()

    asyncio.run(run_test())


def test_prepare_emby_uses_stored_user_id(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_data = {
            "url": "https://example.com",
            "username": "alice",
            "encrypted_token": encrypt_token("token-1", tmp_path),
            "user_id": "user-1",
            "enabled": True,
        }

        emby, _ = bridge._prepare_emby(account_data)

        assert emby.token == "token-1"
        assert emby.user_id == "user-1"
        assert cache.get("emby.credential.example.com.alice") == {
            "token": "token-1",
            "userid": "user-1",
        }

        await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_login_remembers_user_id(tmp_path, monkeypatch):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        async def fake_authenticate(emby):
            emby.set_credentials("token-1", "user-1")
            return True

        monkeypatch.setattr(bridge, "_authenticate_emby", fake_authenticate)

        result = await bridge.trigger_login(account_id)

        assert result["status"] == "success"
        assert bridge.web_accounts.get(account_id)["user_id"] == "user-1"
        assert cache.get("emby.credential.example.com.alice") == {
            "token": "token-1",
            "userid": "user-1",
        }

        await bridge.shutdown()

    asyncio.run(run_test())
