import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException

from embykeeperapi.routers.scheduler import get_dashboard_status, healthz, list_schedule, run_now
from embykeeperapi.scheduler_bridge import bridge


@pytest.fixture(autouse=True)
def reset_bridge_state():
    original = {
        "emby_manager": bridge.emby_manager,
        "web_accounts": bridge.web_accounts,
        "base_emby_accounts": bridge._base_emby_accounts,
        "running_tasks": bridge._running_tasks,
        "account_status": bridge._account_status,
        "scheduler_task": bridge._scheduler_task,
        "initialized": bridge._initialized,
    }
    yield
    bridge.emby_manager = original["emby_manager"]
    bridge.web_accounts = original["web_accounts"]
    bridge._base_emby_accounts = original["base_emby_accounts"]
    bridge._running_tasks = original["running_tasks"]
    bridge._account_status = original["account_status"]
    bridge._scheduler_task = original["scheduler_task"]
    bridge._initialized = original["initialized"]


def test_healthz_does_not_require_scheduler_bridge():
    async def run_test():
        bridge.web_accounts = None

        assert await healthz() == {"status": "ok"}

    asyncio.run(run_test())


@pytest.mark.parametrize(
    "handler,args", [(list_schedule, ()), (get_dashboard_status, ()), (run_now, ("unified",))]
)
def test_scheduler_routes_return_503_before_bridge_initializes(handler, args):
    async def run_test():
        bridge.web_accounts = None

        with pytest.raises(HTTPException) as exc:
            await handler(*args, user="tester")

        assert exc.value.status_code == 503

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("schedule_id", "account_id", "bridge_result"),
    [
        (
            "alice@example.com",
            "alice@example.com",
            {"run_id": "", "status": "running", "message": "Watch task already running"},
        ),
        (
            "emby.watch.alice@emby.watch.example",
            "alice@emby.watch.example",
            {"run_id": "", "status": "running", "message": "Watch task already running"},
        ),
        (
            "unified",
            "unified",
            {"run_id": "", "status": "skipped", "message": "Started 0 watch task(s)"},
        ),
    ],
)
def test_run_now_preserves_bridge_status(schedule_id, account_id, bridge_result, monkeypatch):
    async def run_test():
        bridge.web_accounts = object()

        async def fake_trigger_watch(triggered_account_id):
            assert triggered_account_id == account_id
            return bridge_result

        async def fake_trigger_watch_many(unified_only=False):
            assert unified_only is True
            return bridge_result

        monkeypatch.setattr(bridge, "trigger_watch", fake_trigger_watch)
        monkeypatch.setattr(bridge, "trigger_watch_many", fake_trigger_watch_many)

        assert await run_now(schedule_id, user="tester") == bridge_result

    asyncio.run(run_test())


def test_dashboard_status_reports_latest_watch_time(monkeypatch):
    async def run_test():
        class WebAccounts:
            def get_all(self):
                return {
                    "alice": {"enabled": True},
                    "bob": {"enabled": False},
                    "carol": {"enabled": True},
                }

        statuses = {
            "alice": {
                "is_running": True,
                "is_online": True,
                "last_watch_time": datetime(2026, 1, 1, 8, 0),
            },
            "bob": {
                "is_running": False,
                "is_online": False,
                "last_watch_time": "invalid",
            },
            "carol": {
                "is_running": False,
                "is_online": True,
                "last_watch_time": datetime(2026, 1, 2, 9, 0),
            },
        }

        bridge.web_accounts = WebAccounts()
        monkeypatch.setattr(bridge, "get_account_status", lambda account_id: statuses[account_id])

        response = await get_dashboard_status(user="tester")

        assert response.total_servers == 3
        assert response.enabled_servers == 2
        assert response.running_servers == 1
        assert response.online_servers == 2
        assert response.last_global_watch_time == datetime(2026, 1, 2, 9, 0)

    asyncio.run(run_test())
