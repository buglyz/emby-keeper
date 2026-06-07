import asyncio

import pytest

from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.emby.api import Emby
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


def test_trigger_watch_uses_stored_credentials_and_play_id(tmp_path, monkeypatch):
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
                "user_id": "user-1",
                "play_id": "item-1",
                "enabled": True,
            },
        )

        seen = []

        async def fake_authenticate(emby):
            seen.append(("auth", emby.token, emby.user_id))
            return True

        async def fake_get_item(self, item_id):
            seen.append(("get_item", self.token, self.user_id, item_id))
            return {"Id": item_id, "Name": "Movie", "MediaType": "Video", "RunTimeTicks": 10000000}

        async def fake_watch(self):
            seen.append(("watch", self.token, self.user_id, sorted(self.items)))
            return True

        monkeypatch.setattr(bridge, "_authenticate_emby", fake_authenticate)
        monkeypatch.setattr(Emby, "get_item", fake_get_item)
        monkeypatch.setattr(Emby, "watch", fake_watch)

        result = await bridge.trigger_watch(account_id)
        assert result["status"] == "started"

        task = bridge._running_tasks[account_id]
        await task

        assert seen == [
            ("auth", "token-1", "user-1"),
            ("get_item", "token-1", "user-1", "item-1"),
            ("watch", "token-1", "user-1", ["item-1"]),
        ]
        assert bridge.get_account_status(account_id)["last_watch_status"] == "success"

        await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_watch_returns_running_for_duplicate_account_task(tmp_path):
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

        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        bridge._running_tasks[account_id] = task

        result = await bridge.trigger_watch(account_id)

        assert result == {
            "run_id": "",
            "status": "running",
            "message": "Watch task already running",
        }
        assert bridge._running_tasks[account_id] is task

        blocker.set()
        await task
        await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_watch_cleanup_preserves_newer_task_for_same_account(tmp_path, monkeypatch):
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
                "user_id": "user-1",
                "play_id": "item-1",
                "enabled": True,
            },
        )

        first_can_finish = asyncio.Event()
        second_can_finish = asyncio.Event()
        calls = 0

        async def fake_authenticate(_emby):
            return True

        async def fake_get_item(self, item_id):
            return {"Id": item_id, "Name": "Movie", "MediaType": "Video", "RunTimeTicks": 10000000}

        async def fake_watch(_self):
            nonlocal calls
            calls += 1
            if calls == 1:
                await first_can_finish.wait()
            else:
                await second_can_finish.wait()
            return True

        monkeypatch.setattr(bridge, "_authenticate_emby", fake_authenticate)
        monkeypatch.setattr(Emby, "get_item", fake_get_item)
        monkeypatch.setattr(Emby, "watch", fake_watch)

        await bridge.trigger_watch(account_id)
        first_task = bridge._running_tasks[account_id]

        bridge._running_tasks.pop(account_id)
        await bridge.trigger_watch(account_id)
        second_task = bridge._running_tasks[account_id]

        assert first_task is not second_task

        first_can_finish.set()
        await first_task

        assert bridge._running_tasks[account_id] is second_task

        second_can_finish.set()
        await second_task
        assert account_id not in bridge._running_tasks

        await bridge.shutdown()

    asyncio.run(run_test())
