import asyncio

import pytest

from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.emby.api import Emby
from embykeeper.schema import Config, EmbyAccount


class DummyResponse:
    status_code = 200
    ok = True

    def __init__(self, data=None):
        self._data = data or {"Id": "user-1"}

    def json(self):
        return self._data

    async def aiter_content(self, chunk_size=1024):
        yield b"x" * min(chunk_size, 16)

    async def aclose(self):
        return None


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


def test_play_uses_authenticated_user_and_reports_progress(monkeypatch):
    async def run_test():
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
        emby.set_credentials("token-1", "user-1")

        requests = []

        async def fake_request(method, path, **kwargs):
            requests.append((method, path, kwargs))
            assert emby.token == "token-1"
            assert emby.user_id == "user-1"
            header = emby.build_headers()["X-Emby-Authorization"]
            assert "Token=token-1" in header
            assert "UserId=user-1" in header

            if path.endswith("/PlaybackInfo"):
                assert kwargs["params"]["UserID"] == "user-1"
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [{"Id": "media-source-1", "DirectStreamUrl": "/Videos/item-1/stream"}],
                    }
                )
            return DummyResponse({})

        original_sleep = asyncio.sleep

        async def fake_sleep(_seconds):
            await original_sleep(0)

        monkeypatch.setattr(emby, "_request", fake_request)
        monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", fake_sleep)
        monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda a, b: 0)

        assert await emby.play({"Id": "item-1", "Name": "Movie"}, time=1) is True

        paths = [path for _method, path, _kwargs in requests]
        assert "/Videos/item-1/AdditionalParts" in paths
        assert "/Items/item-1/PlaybackInfo" in paths
        assert "/Sessions/Playing" in paths
        assert "/Sessions/Playing/Progress" in paths
        assert "/Videos/item-1/stream" in paths

        progress_payloads = [
            kwargs["json"]
            for method, path, kwargs in requests
            if method == "POST" and path == "/Sessions/Playing/Progress"
        ]
        assert progress_payloads
        assert progress_payloads[-1]["NowPlayingQueue"] == []

    asyncio.run(run_test())
