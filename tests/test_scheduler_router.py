import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus, _running_runs
from embykeeper.schema import Config
from embykeeperapi.models import SchedulePreviewRequest
from embykeeperapi.routers.scheduler import (
    cancel_schedule_run,
    get_dashboard_status,
    get_health_status,
    healthz,
    list_runs,
    list_schedule,
    preview_schedule,
    run_now,
)
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


def test_dashboard_status_tolerates_mixed_watch_time_timezones(monkeypatch):
    async def run_test():
        class WebAccounts:
            def get_all(self):
                return {
                    "alice": {"enabled": True},
                    "bob": {"enabled": True},
                }

        latest = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
        statuses = {
            "alice": {"last_watch_time": datetime(2026, 1, 1, 8, 0)},
            "bob": {"last_watch_time": latest},
        }

        bridge.web_accounts = WebAccounts()
        monkeypatch.setattr(bridge, "get_account_status", lambda account_id: statuses[account_id])

        response = await get_dashboard_status(user="tester")

        assert response.last_global_watch_time == latest

    asyncio.run(run_test())


def test_run_history_lists_indexed_runs(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        cache._setup_json_cache()
        _running_runs.clear()

        try:
            run = RunContext(id="RUN123", description="Manual watch: alice@example.com")
            run.start()
            run.finish(RunStatus.SUCCESS, "done")

            response = await list_runs(limit=10, user="tester")

            assert len(response) == 1
            assert response[0].run_id == "RUN123"
            assert response[0].status == "success"
            assert response[0].status_info == "done"
            assert response[0].account_spec == "alice@example.com"
        finally:
            _running_runs.clear()
            config.reset()

    asyncio.run(run_test())


def test_schedule_preview_uses_global_defaults(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(emby={"interval_days": "7", "time_range": "<10:00AM,11:00AM>"}))

        response = await preview_schedule(SchedulePreviewRequest(), user="tester")

        assert response.interval_days == "7"
        assert response.time_range == "<10:00AM,11:00AM>"
        assert response.next_time is not None

    asyncio.run(run_test())
    config.reset()


def test_schedule_preview_rejects_invalid_values(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())

        with pytest.raises(HTTPException) as exc:
            await preview_schedule(
                SchedulePreviewRequest(interval_days="<9,3>", time_range="not-a-time"),
                user="tester",
            )

        assert exc.value.status_code == 400

    asyncio.run(run_test())
    config.reset()


def test_cancel_schedule_run_delegates_to_bridge(monkeypatch):
    async def run_test():
        bridge.web_accounts = object()
        seen = {}

        def fake_cancel(account_id):
            seen["account_id"] = account_id
            return True

        monkeypatch.setattr(bridge, "cancel_account_task", fake_cancel)

        response = await cancel_schedule_run("emby.watch.alice@example.com", user="tester")

        assert seen["account_id"] == "alice@example.com"
        assert response.status == "cancelled"

    asyncio.run(run_test())


def test_cancel_schedule_run_returns_404_when_nothing_running(monkeypatch):
    async def run_test():
        bridge.web_accounts = object()
        monkeypatch.setattr(bridge, "cancel_account_task", lambda _account_id: False)

        with pytest.raises(HTTPException) as exc:
            await cancel_schedule_run("alice@example.com", user="tester")

        assert exc.value.status_code == 404

    asyncio.run(run_test())


def test_health_status_reports_runtime_state(tmp_path, monkeypatch):
    async def run_test():
        class WebAccounts:
            def get_all(self):
                return {"alice": {}, "bob": {}}

        config.basedir = tmp_path
        config.set(Config(notifier={"enabled": True, "method": "apprise", "apprise_uri": "dummy://n"}))
        bridge.web_accounts = WebAccounts()
        bridge.emby_manager = object()
        monkeypatch.setattr(bridge, "get_schedule_info", lambda: [{"id": "one"}])
        monkeypatch.setenv("EK_TOKEN", "secret")

        response = await get_health_status(user="tester")

        assert response.status == "ok"
        assert response.config_loaded is True
        assert response.scheduler_initialized is True
        assert response.account_count == 2
        assert response.schedule_count == 1
        assert response.auth_configured is True
        assert response.notifier_configured is True

    asyncio.run(run_test())
    config.reset()
