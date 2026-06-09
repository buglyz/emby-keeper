import asyncio
import sys
from types import SimpleNamespace

from embykeeper.config import config
from embykeeper.schema import Config
from embykeeperapi.automation_runtime import TelegramAutomationRuntime


def test_telegram_automation_runtime_starts_restarts_and_shutdowns_managers(tmp_path, monkeypatch):
    async def run_test():
        calls = []

        class FakeCheckinerManager:
            def __init__(self):
                calls.append("checkiner.init")

            async def schedule_all(self):
                calls.append("checkiner.schedule")
                await asyncio.Event().wait()

            async def shutdown(self):
                calls.append("checkiner.shutdown")

        class FakeRegisterManager:
            def __init__(self):
                calls.append("registrar.init")

            async def start(self):
                calls.append("registrar.start")
                await asyncio.Event().wait()

            async def shutdown(self):
                calls.append("registrar.shutdown")

        monkeypatch.setitem(
            sys.modules,
            "embykeeper.telegram.checkin_main",
            SimpleNamespace(CheckinerManager=FakeCheckinerManager),
        )
        monkeypatch.setitem(
            sys.modules,
            "embykeeper.telegram.registrar_main",
            SimpleNamespace(RegisterManager=FakeRegisterManager),
        )

        config.basedir = tmp_path
        config.set(Config())
        runtime = TelegramAutomationRuntime()

        await runtime.start()
        await asyncio.sleep(0)
        assert calls[:4] == [
            "checkiner.init",
            "registrar.init",
            "checkiner.schedule",
            "registrar.start",
        ]

        await runtime.restart_if_started()
        await asyncio.sleep(0)
        assert calls.count("checkiner.shutdown") == 1
        assert calls.count("registrar.shutdown") == 1
        assert calls.count("checkiner.init") == 2
        assert calls.count("registrar.init") == 2

        await runtime.shutdown()
        assert calls.count("checkiner.shutdown") == 2
        assert calls.count("registrar.shutdown") == 2

    try:
        asyncio.run(run_test())
    finally:
        config.reset()


def test_telegram_automation_runtime_skips_when_config_is_not_loaded(monkeypatch):
    async def run_test():
        runtime = TelegramAutomationRuntime()
        config.reset()

        await runtime.start()

        assert runtime.checkiner_manager is None
        assert runtime.registrar_manager is None

    asyncio.run(run_test())


def test_telegram_automation_runtime_keeps_registrar_when_checkiner_init_fails(tmp_path, monkeypatch):
    async def run_test():
        calls = []

        class BrokenCheckinerManager:
            def __init__(self):
                raise RuntimeError("checkiner failed")

        class FakeRegisterManager:
            def __init__(self):
                calls.append("registrar.init")

            async def start(self):
                calls.append("registrar.start")
                await asyncio.Event().wait()

            async def shutdown(self):
                calls.append("registrar.shutdown")

        monkeypatch.setitem(
            sys.modules,
            "embykeeper.telegram.checkin_main",
            SimpleNamespace(CheckinerManager=BrokenCheckinerManager),
        )
        monkeypatch.setitem(
            sys.modules,
            "embykeeper.telegram.registrar_main",
            SimpleNamespace(RegisterManager=FakeRegisterManager),
        )

        config.basedir = tmp_path
        config.set(Config())
        runtime = TelegramAutomationRuntime()

        await runtime.start()
        await asyncio.sleep(0)

        assert runtime.checkiner_manager is None
        assert runtime.registrar_manager is not None
        assert calls == ["registrar.init", "registrar.start"]

        await runtime.shutdown()
        assert calls[-1] == "registrar.shutdown"

    try:
        asyncio.run(run_test())
    finally:
        config.reset()
