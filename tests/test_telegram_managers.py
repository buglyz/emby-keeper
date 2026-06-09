import asyncio
from datetime import datetime, timedelta
import inspect

import pytest

from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.schema import Config, TelegramAccount
import embykeeper.telegram.checkin_main as checkin_main
from embykeeper.telegram.checkiner._base import BaseBotCheckin, BotCheckin
from embykeeper.telegram.registrar._base import BaseBotRegister
from embykeeper.telegram.dynamic import get_cls, get_names
from embykeeper.telegram.checkin_main import CheckinerManager
from embykeeper.telegram.registrar_main import RegisterManager


@pytest.fixture(autouse=True)
def reset_config_callbacks(tmp_path):
    callbacks = {
        key: {name: handlers[:] for name, handlers in value.items()}
        for key, value in config._callbacks.items()
    }
    config.basedir = tmp_path
    config.set(Config())
    yield
    config.reset()
    config._callbacks = callbacks


class IdleScheduler:
    async def schedule(self):
        await asyncio.Event().wait()


class DummyClient:
    stop_handlers = []

    class Me:
        full_name = "Tester"

    me = Me()


class DummyCheckin(BaseBotCheckin):
    name = "Dummy"

    async def start(self):
        return self.ctx.finish(RunStatus.SUCCESS)


class DummyRegister(BaseBotRegister):
    name = "Dummy"

    async def start(self):
        return self.ctx.finish(RunStatus.SUCCESS)


def test_checkiner_ignores_disabled_added_accounts():
    async def run_test():
        manager = CheckinerManager()
        account = TelegramAccount(phone="+8613800000000", enabled=False, checkiner=True)

        manager._handle_account_change([account], [])
        await asyncio.sleep(0)

        assert manager._scheduler_tasks == {}
        assert manager._schedulers == {}

    asyncio.run(run_test())


def test_checkiner_config_change_to_disabled_account_cancels_existing_scheduler():
    async def run_test():
        manager = CheckinerManager()
        enabled = TelegramAccount(phone="+8613800000000", enabled=True, checkiner=True)
        disabled = TelegramAccount(phone="+8613800000000", enabled=False, checkiner=True)
        scheduler = IdleScheduler()

        manager._schedulers[enabled.phone] = scheduler
        task = manager._start_scheduler(enabled.phone, scheduler)
        wait_task = asyncio.create_task(manager._wait_pool())
        await asyncio.sleep(0)

        manager._handle_account_change([disabled], [enabled])
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.cancelled()
        assert manager._scheduler_tasks == {}
        assert manager._schedulers == {}
        await wait_task

    asyncio.run(run_test())


def test_checkiner_stop_account_cancels_scheduler_tasks():
    async def run_test():
        manager = CheckinerManager()
        task = manager._start_scheduler("+8613800000000", IdleScheduler())
        wait_task = asyncio.create_task(manager._wait_pool())
        await asyncio.sleep(0)

        manager.stop_account("+8613800000000")
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.cancelled()
        assert manager._scheduler_tasks == {}
        await wait_task

    asyncio.run(run_test())


def test_registrar_stop_account_cancels_scheduler_tasks():
    async def run_test():
        manager = RegisterManager()
        task = manager._start_scheduler("+8613800000000.templ_a<TestBot>", IdleScheduler())
        wait_task = asyncio.create_task(manager._wait_pool())
        await asyncio.sleep(0)

        manager.stop_account("+8613800000000")
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.cancelled()
        assert manager._scheduler_tasks == {}
        await wait_task

    asyncio.run(run_test())


def test_base_checkin_and_register_keep_zero_retries():
    checkin = DummyCheckin(DummyClient(), retries=0, timeout=30)
    register = DummyRegister(DummyClient(), retries=0, timeout=30)

    assert checkin.retries == 0
    assert checkin.timeout == 30
    assert register.retries == 0
    assert register.timeout == 30

    bot_checkin = BotCheckin(DummyClient(), retries=5)
    bot_checkin.max_retries = 0
    assert bot_checkin.valid_retries == 0


def test_checkiner_reschedule_uses_current_site_name(monkeypatch):
    async def run_test():
        manager = CheckinerManager()
        account = TelegramAccount(phone="+8613800000000", checkiner=True)
        client = DummyClient()
        first_cls = type(
            "FirstCheckin",
            (DummyCheckin,),
            {"name": "First", "__module__": "embykeeper.telegram.checkiner.first"},
        )
        second_cls = type(
            "SecondCheckin",
            (DummyCheckin,),
            {"name": "Second", "__module__": "embykeeper.telegram.checkiner.second"},
        )
        scheduled = []

        async def fake_task_main(checkiner, _sem, wait=0):
            if checkiner.name == "First":
                checkiner.ctx.next_time = datetime.now() + timedelta(seconds=1)
                return checkiner, checkiner.ctx.finish(RunStatus.RESCHEDULE)
            return checkiner, checkiner.ctx.finish(RunStatus.SUCCESS)

        def fake_schedule_site(_ctx, _at, _account, site, reschedule=False):
            scheduled.append((site, reschedule))

        monkeypatch.setattr(checkin_main, "get_cls", lambda *_args, **_kwargs: [first_cls, second_cls])
        monkeypatch.setattr(manager, "_task_main", fake_task_main)
        monkeypatch.setattr(manager, "schedule_site", fake_schedule_site)

        await manager._run_account(RunContext.prepare("test"), account, client, instant=True)

        assert scheduled == [("first", True)]

    asyncio.run(run_test())


def test_registrar_times_create_one_scheduler_per_time():
    config.set(
        Config(
            telegram={"account": [{"phone": "+8613800000000", "registrar": True}]},
            site={"registrar": ["templ_a<TestBot>"]},
            registrar={"templ_a<TestBot>": {"times": ["9:00AM", "9:00PM"]}},
        )
    )
    manager = RegisterManager()
    account = config.telegram.account[0]

    schedulers, tasks = manager.schedule_account(account)

    assert tasks == []
    assert len(schedulers) == 2
    assert len(manager._schedulers) == 2
    assert [scheduler.start_time.hour for scheduler in schedulers] == [9, 21]
    assert [scheduler.end_time.hour for scheduler in schedulers] == [9, 21]


def test_registrar_skips_invalid_times_but_keeps_valid_ones():
    config.set(
        Config(
            telegram={"account": [{"phone": "+8613800000000", "registrar": True}]},
            site={"registrar": ["templ_a<TestBot>"]},
            registrar={"templ_a<TestBot>": {"times": ["9:00AM", "not-a-time"]}},
        )
    )
    manager = RegisterManager()
    account = config.telegram.account[0]

    schedulers, tasks = manager.schedule_account(account)

    assert tasks == []
    assert len(schedulers) == 1
    assert schedulers[0].start_time.hour == 9


def test_registrar_rejects_invalid_interval_minutes():
    config.set(
        Config(
            telegram={"account": [{"phone": "+8613800000000", "registrar": True}]},
            site={"registrar": ["templ_a<TestBot>"]},
            registrar={"templ_a<TestBot>": {"interval_minutes": "soon"}},
        )
    )
    manager = RegisterManager()
    account = config.telegram.account[0]

    schedulers, tasks = manager.schedule_account(account)

    assert schedulers == []
    assert tasks == []


def test_pornfans_group_checkiner_imports_with_messager_compatibility():
    get_names.cache_clear()

    clses = get_cls("checkiner", names=["pornfans_group"])

    assert [cls.__name__ for cls in clses] == ["PornfansGroupCheckin"]


def test_pornfans_game_group_uses_client_me_id():
    from embykeeper.telegram.checkiner.pornfans_game_group import PornfansGameGroupCheckin

    source = inspect.getsource(PornfansGameGroupCheckin.send_checkin)

    assert "self.me" not in source
    assert "self.client.me.id" in source


def test_checkiner_shutdown_unregisters_config_callbacks():
    async def run_test():
        manager = CheckinerManager()

        assert manager._handle_account_change in config._callbacks["list_change"]["telegram.account"]
        assert manager._handle_config_change in config._callbacks["change"]["checkiner"]

        await manager.shutdown()

        assert manager._handle_account_change not in config._callbacks["list_change"]["telegram.account"]
        assert manager._handle_config_change not in config._callbacks["change"]["checkiner"]
        assert manager._handle_config_change not in config._callbacks["change"]["site.checkiner"]

    asyncio.run(run_test())


def test_registrar_shutdown_unregisters_config_callbacks():
    async def run_test():
        manager = RegisterManager()

        assert manager._handle_account_change in config._callbacks["list_change"]["telegram.account"]
        assert manager._handle_config_change in config._callbacks["change"]["registrar"]

        await manager.shutdown()

        assert manager._handle_account_change not in config._callbacks["list_change"]["telegram.account"]
        assert manager._handle_config_change not in config._callbacks["change"]["registrar"]
        assert manager._handle_config_change not in config._callbacks["change"]["site.registrar"]

    asyncio.run(run_test())


def test_registrar_run_account_finishes_when_no_site_config():
    async def run_test():
        config.set(
            Config(
                telegram={"account": [{"phone": "+8613800000000", "registrar": True}]},
                site={"registrar": ["templ_a<TestBot>"]},
                registrar={},
            )
        )
        manager = RegisterManager()
        ctx = RunContext.prepare("test registrar")

        result = await manager.run_account(ctx, config.telegram.account[0])
        await manager.shutdown()

        assert result is ctx
        assert ctx.status == RunStatus.NONEED
        assert ctx.status_info == "未配置可执行抢注任务"

    asyncio.run(run_test())
