import pytest

from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.emby.api import Emby
from embykeeper.schema import Config, EmbyAccount


class DummyResponse:
    status_code = 200

    def json(self):
        return {"Id": "user-1"}


@pytest.fixture(autouse=True)
def reset_config_and_cache(tmp_path):
    config.basedir = tmp_path
    config.set(Config())
    cache._setup_json_cache()
    yield
    config.reset()


def test_token_only_account_authenticates_without_password(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice")
    emby = Emby(account)
    emby.set_credentials("token-1")

    login_called = False

    async def fake_login():
        nonlocal login_called
        login_called = True
        return None

    async def fake_request(method, path, _login=False, **kwargs):
        assert method == "GET"
        assert path == "/Users/Me"
        assert _login is True
        return DummyResponse()

    monkeypatch.setattr(emby, "login", fake_login)
    monkeypatch.setattr(emby, "_request", fake_request)

    import asyncio

    assert asyncio.run(emby.ensure_authenticated()) is True
    assert emby.user_id == "user-1"
    assert login_called is False


def test_authorization_header_uses_real_user_id_only():
    account = EmbyAccount(
        url="https://example.com",
        username="alice",
        client="Fileball",
        client_version="1.3.30",
        device="Test Device",
        device_id="test-device-id",
        useragent="Fileball/1.3.30",
    )
    emby = Emby(account)

    emby.set_credentials("token-1")
    header_without_user = emby.build_headers()["X-Emby-Authorization"]
    assert "Token=token-1" in header_without_user
    assert "UserId=" not in header_without_user

    emby.set_credentials("token-1", "user-1")
    header_with_user = emby.build_headers()["X-Emby-Authorization"]
    assert "UserId=user-1" in header_with_user
    assert emby.run_id not in header_with_user
