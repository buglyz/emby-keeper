import asyncio
import base64

from embykeeper.config import ConfigManager


def test_reload_conf_rejects_invalid_env_config(monkeypatch):
    async def run_test():
        manager = ConfigManager()
        invalid_toml = base64.b64encode(b"[emby").decode()
        monkeypatch.setenv("EK_CONFIG", invalid_toml)

        assert await manager.reload_conf() is False
        assert manager._cache is None

    asyncio.run(run_test())


def test_reload_conf_loads_valid_env_config(monkeypatch):
    async def run_test():
        manager = ConfigManager()
        config_toml = b"""
[emby]
time_range = "<8:00AM,9:00AM>"
interval_days = "7"
"""
        monkeypatch.setenv("EK_CONFIG", base64.b64encode(config_toml).decode())

        assert await manager.reload_conf() is True
        assert manager.emby.time_range == "<8:00AM,9:00AM>"
        assert manager.emby.interval_days == "7"

    asyncio.run(run_test())


def test_start_observer_waits_for_previous_observer_cancel(tmp_path, monkeypatch):
    async def run_test():
        manager = ConfigManager(tmp_path / "config.toml")

        async def never_yield_awatch(_path):
            await asyncio.Event().wait()
            yield []

        monkeypatch.setattr("embykeeper.config.awatch", never_yield_awatch)

        old_task = asyncio.create_task(asyncio.Event().wait())
        manager._observer = old_task

        await manager.start_observer()

        try:
            assert old_task.done()
            assert old_task.cancelled()
        finally:
            manager._observer.cancel()
            await asyncio.gather(manager._observer, return_exceptions=True)

    asyncio.run(run_test())
