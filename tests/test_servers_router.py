import asyncio

import pytest

from embykeeper.config import config
from embykeeper.emby.api import Emby
from embykeeper.schema import Config
from embykeeperapi.crypto import encrypt_token
from embykeeperapi.models import EmbyServerCreate, EmbyServerUpdate
from embykeeperapi.routers.servers import create_server, update_server
from embykeeperapi.scheduler_bridge import bridge


@pytest.fixture(autouse=True)
def reset_config_callbacks():
    callbacks = {
        key: {name: handlers[:] for name, handlers in value.items()}
        for key, value in config._callbacks.items()
    }
    yield
    config.reset()
    config._callbacks = callbacks


async def reset_bridge():
    await bridge.shutdown()
    bridge.emby_manager = None
    bridge.web_accounts = None
    bridge._base_emby_accounts = []
    bridge._running_tasks = {}
    bridge._account_status = {}
    bridge._scheduler_task = None
    bridge._initialized = False


def test_create_password_server_uses_connection_settings(tmp_path, monkeypatch):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        seen = {}

        async def fake_login(self):
            seen.update(
                {
                    "use_proxy": self.a.use_proxy,
                    "client": self.a.client,
                    "client_version": self.a.client_version,
                    "device": self.a.device,
                    "device_id": self.a.device_id,
                    "useragent": self.a.useragent,
                }
            )
            self.set_credentials("token-1", "user-1")
            return "token-1"

        monkeypatch.setattr(Emby, "login", fake_login)

        try:
            await create_server(
                EmbyServerCreate(
                    url="https://example.com",
                    username="alice",
                    auth_method="password",
                    password="secret",
                    use_proxy=False,
                    client="Fileball",
                    client_version="1.3.30",
                    device="Test Device",
                    device_id="device-1",
                    useragent="Fileball/1.3.30",
                ),
                user="tester",
            )

            assert seen == {
                "use_proxy": False,
                "client": "Fileball",
                "client_version": "1.3.30",
                "device": "Test Device",
                "device_id": "device-1",
                "useragent": "Fileball/1.3.30",
            }
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_update_password_server_reuses_existing_connection_settings(tmp_path, monkeypatch):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-old", tmp_path),
                "enabled": False,
                "use_proxy": False,
                "client": "Fileball",
                "client_version": "1.3.30",
                "device": "Test Device",
                "device_id": "device-1",
                "useragent": "Fileball/1.3.30",
            },
        )

        seen = {}

        async def fake_login(self):
            seen.update(
                {
                    "use_proxy": self.a.use_proxy,
                    "client": self.a.client,
                    "client_version": self.a.client_version,
                    "device": self.a.device,
                    "device_id": self.a.device_id,
                    "useragent": self.a.useragent,
                }
            )
            self.set_credentials("token-new", "user-1")
            return "token-new"

        monkeypatch.setattr(Emby, "login", fake_login)

        try:
            await update_server(
                account_id,
                EmbyServerUpdate(password="new-secret"),
                user="tester",
            )

            assert seen == {
                "use_proxy": False,
                "client": "Fileball",
                "client_version": "1.3.30",
                "device": "Test Device",
                "device_id": "device-1",
                "useragent": "Fileball/1.3.30",
            }
        finally:
            await reset_bridge()

    asyncio.run(run_test())
