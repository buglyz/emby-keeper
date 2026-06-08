import asyncio

import pytest
from fastapi import HTTPException

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


def test_create_server_rejects_invalid_interval_without_saving(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://example.com",
                        username="alice",
                        auth_method="token",
                        access_token="token-1",
                        interval_days="<12,7>",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get("alice@example.com") is None
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_rejects_empty_username_without_saving(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://example.com",
                        username="",
                        auth_method="token",
                        access_token="token-1",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_rejects_blank_username_without_saving(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://example.com",
                        username="   ",
                        auth_method="token",
                        access_token="token-1",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_rejects_blank_token_without_saving(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://example.com",
                        username="alice",
                        auth_method="token",
                        access_token="   ",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_rejects_url_with_internal_whitespace(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://exa mple.com",
                        username="alice",
                        auth_method="token",
                        access_token="token-1",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_rejects_url_userinfo(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://user:pass@example.com",
                        username="alice",
                        auth_method="token",
                        access_token="token-1",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


@pytest.mark.parametrize("url", ["https://example.com?token=secret", "https://example.com/#/home"])
def test_create_server_rejects_url_query_or_fragment(tmp_path, url):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url=url,
                        username="alice",
                        auth_method="token",
                        access_token="token-1",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_rejects_blank_password_without_login(tmp_path, monkeypatch):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        async def fail_login(_self):
            raise AssertionError("login should not be called for blank password")

        monkeypatch.setattr(Emby, "login", fail_login)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://example.com",
                        username="alice",
                        auth_method="password",
                        password="   ",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_trims_url_username_and_name(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            response = await create_server(
                EmbyServerCreate(
                    url=" https://example.com ",
                    username=" alice ",
                    name=" primary ",
                    auth_method="token",
                    access_token=" token-1 ",
                ),
                user="tester",
            )

            assert response.id == "alice@primary"
            stored = bridge.web_accounts.get("alice@primary")
            assert stored["url"] == "https://example.com"
            assert stored["username"] == "alice"
            assert stored["name"] == "primary"
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_update_server_rejects_invalid_schedule_settings_without_mutating_account(tmp_path):
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
                "interval_days": "7",
            },
        )

        try:
            with pytest.raises(HTTPException) as exc:
                await update_server(
                    account_id,
                    EmbyServerUpdate(time_range="<bad"),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get(account_id)["interval_days"] == "7"
            assert "time_range" not in bridge.web_accounts.get(account_id)
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_update_server_trims_text_identity_fields(tmp_path):
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
                "name": "old",
                "encrypted_token": encrypt_token("token-old", tmp_path),
            },
        )

        try:
            response = await update_server(
                account_id,
                EmbyServerUpdate(url=" https://new.example ", username=" bob ", name=" "),
                user="tester",
            )

            assert response.id == "bob@new.example"
            stored = bridge.web_accounts.get("bob@new.example")
            assert stored["url"] == "https://new.example"
            assert stored["username"] == "bob"
            assert "name" not in stored
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_update_server_rejects_blank_required_text_without_mutating_account(tmp_path):
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
            },
        )

        try:
            with pytest.raises(HTTPException) as exc:
                await update_server(
                    account_id,
                    EmbyServerUpdate(username="   "),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get(account_id)["username"] == "alice"
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_update_server_rejects_blank_credentials_without_mutating_account(tmp_path):
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
                "auth_method": "token",
            },
        )
        original = bridge.web_accounts.get(account_id).copy()

        try:
            with pytest.raises(HTTPException) as exc:
                await update_server(
                    account_id,
                    EmbyServerUpdate(access_token="   "),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get(account_id) == original
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_update_server_rejects_blank_password_without_login(tmp_path, monkeypatch):
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
                "auth_method": "password",
            },
        )
        original = bridge.web_accounts.get(account_id).copy()

        async def fail_login(_self):
            raise AssertionError("login should not be called for blank password")

        monkeypatch.setattr(Emby, "login", fail_login)

        try:
            with pytest.raises(HTTPException) as exc:
                await update_server(
                    account_id,
                    EmbyServerUpdate(password="   "),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get(account_id) == original
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_create_server_rejects_invalid_schedule_settings(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        await bridge.initialize(tmp_path)

        try:
            with pytest.raises(HTTPException) as exc:
                await create_server(
                    EmbyServerCreate(
                        url="https://example.com",
                        username="alice",
                        auth_method="token",
                        access_token="token-1",
                        interval_days="<9,3>",
                        time_range="8:00AM",
                    ),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get_all() == {}
        finally:
            await reset_bridge()

    asyncio.run(run_test())


def test_update_server_rejects_invalid_schedule_settings(tmp_path):
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
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        try:
            with pytest.raises(HTTPException) as exc:
                await update_server(
                    account_id,
                    EmbyServerUpdate(interval_days="7", time_range="not-a-time"),
                    user="tester",
                )

            assert exc.value.status_code == 400
            assert bridge.web_accounts.get(account_id).get("time_range") is None
        finally:
            await reset_bridge()

    asyncio.run(run_test())
