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
