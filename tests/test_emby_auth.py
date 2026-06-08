import asyncio

import pytest

from embykeeper.cache import cache
from embykeeper.config import config
from curl_cffi import CurlHttpVersion
from curl_cffi.requests import RequestsError

from embykeeper.emby.api import Emby, EmbyPlayError, EmbyStatusError
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


class BrokenJsonResponse(DummyResponse):
    def json(self):
        raise ValueError("invalid json")


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


def test_user_id_cache_without_token_does_not_authenticate(monkeypatch):
    key = "emby.credential.example.com.alice"
    cache.set(key, {"userid": "user-1"})
    account = EmbyAccount(url="https://example.com", username="alice", password="secret")
    emby = Emby(account)

    async def fake_login():
        emby.set_credentials("token-1", "user-1")
        return "token-1"

    monkeypatch.setattr(emby, "login", fake_login)

    assert asyncio.run(emby.ensure_authenticated()) is True
    assert emby.token == "token-1"
    assert emby.user_id == "user-1"


def test_cached_credentials_read_failure_does_not_break_token_lookup(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice")
    emby = Emby(account)

    def fail_get(_key, default=None):
        raise OSError("read failed")

    monkeypatch.setattr("embykeeper.emby.api.cache.get", fail_get)

    assert emby.token is None
    assert emby.user_id is None


def test_set_credentials_survives_cache_write_failure(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice")
    emby = Emby(account)

    def fail_set(_key, _value):
        raise OSError("write failed")

    monkeypatch.setattr("embykeeper.emby.api.cache.set", fail_set)

    emby.set_credentials("token-1", "user-1")

    assert emby.token == "token-1"
    assert emby.user_id == "user-1"


def test_clear_credentials_survives_cache_delete_failure(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice")
    emby = Emby(account)
    emby._token = "token-1"
    emby._user_id = "user-1"

    def fail_delete(_key):
        raise OSError("delete failed")

    monkeypatch.setattr("embykeeper.emby.api.cache.delete", fail_delete)

    emby.set_credentials(None)

    assert emby.token is None
    assert emby.user_id is None


def test_token_authentication_uses_stored_user_id(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice")
    emby = Emby(account)
    emby.set_credentials("token-1", "user-1")

    requests = []

    async def fake_request(method, path, _login=False, **kwargs):
        requests.append((method, path, _login))
        assert method == "GET"
        assert path == "/Users/user-1"
        assert _login is True
        return DummyResponse({"Id": "user-1"})

    monkeypatch.setattr(emby, "_request", fake_request)

    assert asyncio.run(emby.authenticate_with_token()) is True
    assert requests == [("GET", "/Users/user-1", True)]
    assert emby.user_id == "user-1"


def test_watch_retry_uses_default_when_global_emby_config_missing(monkeypatch):
    async def run_test():
        config.set(Config(emby=None))
        account = EmbyAccount(url="https://example.com", username="alice", time=10)
        emby = Emby(account)
        emby.items = {
            "item-1": {
                "Id": "item-1",
                "Name": "Test",
                "MediaType": "Video",
                "RunTimeTicks": 20 * 10000000,
            }
        }
        calls = 0

        async def fail_play(_item, time=10):
            nonlocal calls
            calls += 1
            raise EmbyPlayError("boom")

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(emby, "play", fail_play)
        monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)
        monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", no_sleep)

        assert await emby.watch() is False
        assert calls == 6

    asyncio.run(run_test())


def test_token_authentication_discovers_user_id_from_sessions(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice", device_id="device-1")
    emby = Emby(account)
    emby.set_credentials("token-1")

    requests = []

    async def fake_request(method, path, _login=False, **kwargs):
        requests.append((method, path, _login))
        assert method == "GET"
        assert _login is True
        if path == "/Users/Me":
            resp = DummyResponse({})
            resp.status_code = 500
            resp.ok = False
            return resp
        if path == "/Sessions":
            return DummyResponse(
                [
                    {"UserId": "other-user", "UserName": "bob", "DeviceId": "device-1"},
                    {"UserId": "user-1", "UserName": "alice", "DeviceId": "device-1"},
                ]
            )
        assert path == "/Users/user-1"
        return DummyResponse({"Id": "user-1"})

    monkeypatch.setattr(emby, "_request", fake_request)

    assert asyncio.run(emby.authenticate_with_token()) is True
    assert requests == [
        ("GET", "/Users/Me", True),
        ("GET", "/Sessions", True),
        ("GET", "/Users/user-1", True),
    ]
    assert emby.user_id == "user-1"


def test_token_authentication_ignores_invalid_session_user_ids(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice", device_id="device-1")
    emby = Emby(account)
    emby.set_credentials("token-1")

    requests = []

    async def fake_request(method, path, _login=False, **kwargs):
        requests.append((method, path, _login))
        assert method == "GET"
        assert _login is True
        if path == "/Users/Me":
            resp = DummyResponse({})
            resp.status_code = 500
            resp.ok = False
            return resp
        if path == "/Sessions":
            return DummyResponse(
                [
                    {"UserId": ["invalid"], "UserName": "alice", "DeviceId": "device-1"},
                    {"UserId": "user-1", "UserName": "alice", "DeviceId": "device-1"},
                ]
            )
        assert path == "/Users/user-1"
        return DummyResponse({"Id": "user-1"})

    monkeypatch.setattr(emby, "_request", fake_request)

    assert asyncio.run(emby.authenticate_with_token()) is True
    assert requests == [
        ("GET", "/Users/Me", True),
        ("GET", "/Sessions", True),
        ("GET", "/Users/user-1", True),
    ]
    assert emby.user_id == "user-1"


def test_token_authentication_handles_invalid_success_json(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice")
    emby = Emby(account)
    emby.set_credentials("token-1")

    requests = []

    async def fake_request(method, path, _login=False, **kwargs):
        requests.append(path)
        assert method == "GET"
        assert _login is True
        if path == "/Users/Me":
            return BrokenJsonResponse()
        assert path == "/Sessions"
        return DummyResponse([])

    monkeypatch.setattr(emby, "_request", fake_request)

    assert asyncio.run(emby.authenticate_with_token()) is False
    assert requests == ["/Users/Me", "/Sessions"]


def test_login_handles_invalid_success_json(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice", password="secret")
    emby = Emby(account)

    async def fake_request(method, path, _login=False, **kwargs):
        assert method == "POST"
        assert path == "/Users/AuthenticateByName"
        assert _login is True
        return BrokenJsonResponse()

    monkeypatch.setattr(emby, "_request", fake_request)

    assert asyncio.run(emby.login()) is None
    assert emby.token is None


def test_login_without_access_token_clears_cached_credentials(monkeypatch):
    key = "emby.credential.example.com.alice"
    cache.set(key, {"token": "old-token", "userid": "old-user"})
    account = EmbyAccount(url="https://example.com", username="alice", password="secret")
    emby = Emby(account)

    async def fake_request(method, path, _login=False, **kwargs):
        assert method == "POST"
        assert path == "/Users/AuthenticateByName"
        assert _login is True
        return DummyResponse({"User": {"Id": "user-1"}})

    monkeypatch.setattr(emby, "_request", fake_request)

    assert asyncio.run(emby.login()) is None
    assert emby.token is None
    assert emby.user_id is None
    assert cache.get(key) is None


def test_login_handles_invalid_user_object(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="alice", password="secret")
    emby = Emby(account)

    async def fake_request(method, path, _login=False, **kwargs):
        assert method == "POST"
        assert path == "/Users/AuthenticateByName"
        assert _login is True
        return DummyResponse({"AccessToken": "token-1", "User": "invalid"})

    monkeypatch.setattr(emby, "_request", fake_request)

    assert asyncio.run(emby.login()) is None
    assert emby.token == "token-1"
    assert emby.user_id is None


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


def test_invalid_cached_credentials_are_ignored():
    key = "emby.credential.example.com.alice"
    cache.set(key, ["token-1", "user-1"])

    emby = Emby(EmbyAccount(url="https://example.com", username="alice"))

    assert emby.token is None
    assert emby.user_id is None
    assert cache.get(key) is None


def test_invalid_cached_token_values_are_ignored():
    key = "emby.credential.example.com.alice"
    cache.set(key, {"token": 123, "userid": "user-1"})

    emby = Emby(EmbyAccount(url="https://example.com", username="alice"))

    assert emby.token is None
    assert emby.user_id is None
    assert cache.get(key) is None


def test_credentials_are_normalized_before_caching():
    key = "emby.credential.example.com.alice"
    emby = Emby(EmbyAccount(url="https://example.com", username="alice"))

    emby.set_credentials(" token-1 ", 123)

    assert emby.token == "token-1"
    assert emby.user_id == "123"
    assert cache.get(key) == {"token": "token-1", "userid": "123"}


def test_invalid_cached_env_is_regenerated_from_account_settings():
    key = "emby.env.example.com.alice"
    cache.set(key, ["invalid"])
    account = EmbyAccount(
        url="https://example.com",
        username="alice",
        client="Fileball",
        client_version="1.3.30",
        device="Test Device",
        device_id="test-device-id",
        useragent="Fileball/1.3.30",
    )

    env = Emby(account).env

    assert env.client == "Fileball"
    assert env.client_version == "1.3.30"
    assert env.device == "Test Device"
    assert env.device_id == "test-device-id"
    assert env.useragent == "Fileball/1.3.30"
    assert cache.get(key) == {
        "client": "Fileball",
        "device": "Test Device",
        "device_id": "test-device-id",
        "client_version": "1.3.30",
        "useragent": "Fileball/1.3.30",
    }


def test_fake_env_reuses_cached_useragent_field(monkeypatch):
    key = "emby.env.example.com.alice"
    cache.set(key, {"useragent": "CachedClient/9.9"})
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0.9)
    monkeypatch.setattr("embykeeper.emby.api.random.randint", lambda start, _end: start)
    monkeypatch.setattr(Emby, "get_random_device", staticmethod(lambda: "Test Device"))
    account = EmbyAccount(url="https://example.com", username="alice")

    env = Emby(account).env

    assert env.useragent == "CachedClient/9.9"
    assert cache.get(key)["useragent"] == "CachedClient/9.9"


def test_build_url_preserves_configured_base_path():
    account = EmbyAccount(url="https://example.com/emby", username="alice")
    emby = Emby(account)

    assert emby._build_url("/Users/Me") == "https://example.com/emby/Users/Me"
    assert emby._build_url("/Items/item-1/PlaybackInfo") == (
        "https://example.com/emby/Items/item-1/PlaybackInfo"
    )
    assert emby._build_url("Videos/item-1/stream") == "https://example.com/emby/Videos/item-1/stream"
    assert emby._build_url("/emby/Videos/item-1/stream") == ("https://example.com/emby/Videos/item-1/stream")
    assert emby._build_url("https://cdn.example.com/video.mp4") == "https://cdn.example.com/video.mp4"
    assert emby._build_url("//cdn.example.com/video.mp4") == "https://cdn.example.com/video.mp4"


def test_stream_request_keeps_session_open_until_response_is_closed(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)

        sessions = []

        class DummyStreamResponse:
            status_code = 200
            ok = True
            text = ""
            quit_now = None

            def __init__(self, session):
                self.session = session
                self.closed = False

            async def aiter_content(self, chunk_size=1024):
                assert self.session.closed is False
                yield b"x"
                await self.aclose()

            async def aclose(self):
                self.closed = True

        class DummyStreamSession:
            def __init__(self, headers=None):
                self.headers = headers or {}
                self.closed = False
                self.response = DummyStreamResponse(self)

            async def request(self, method, url, **kwargs):
                assert method == "GET"
                assert url == "https://example.com/Videos/item-1/stream"
                assert kwargs["stream"] is True
                assert "headers" not in kwargs
                return self.response

            async def close(self):
                self.closed = True

        def fake_get_session(headers=None):
            session = DummyStreamSession(headers)
            sessions.append(session)
            return session

        monkeypatch.setattr(emby, "_get_session", fake_get_session)

        resp = await emby._request(
            "GET",
            "/Videos/item-1/stream",
            stream=True,
            headers={"Range": "bytes=0-"},
        )
        assert sessions[0].closed is False
        assert sessions[0].headers["Range"] == "bytes=0-"

        chunks = []
        async for chunk in resp.aiter_content():
            chunks.append(chunk)

        assert chunks == [b"x"]
        assert resp.closed is True
        assert sessions[0].closed is True

    asyncio.run(run_test())


def test_stream_headers_keep_authentication_with_vlc_user_agent():
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

    headers = emby._build_stream_headers("play-session-1", 128)

    assert headers["Range"] == "bytes=128-"
    assert headers["User-Agent"] == "VLC/3.0.21 LibVLC/3.0.21"
    assert headers["Accept-Language"] == "en_US"
    assert headers["X-Playback-Session-Id"] == "play-session-1"
    assert headers["X-Emby-Token"] == "token-1"
    assert "Token=token-1" in headers["X-Emby-Authorization"]
    assert "UserId=user-1" in headers["X-Emby-Authorization"]
    assert "Content-Type" not in headers


def test_stream_headers_can_omit_emby_authentication_for_external_urls():
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

    assert emby._is_same_origin_url("/Videos/item-1/stream") is True
    assert emby._is_same_origin_url("https://example.com/Videos/item-1/stream") is True
    assert emby._is_same_origin_url("//example.com/Videos/item-1/stream") is True
    assert emby._is_same_origin_url("https://cdn.example.net/video.mp4") is False
    assert emby._is_same_origin_url("//cdn.example.net/video.mp4") is False
    assert emby._is_same_origin_url("https://example.com:bad/video.mp4") is False

    headers = emby._build_stream_headers("play-session-1", 0, include_auth=False)

    assert headers["Range"] == "bytes=0-"
    assert headers["User-Agent"] == "VLC/3.0.21 LibVLC/3.0.21"
    assert headers["X-Playback-Session-Id"] == "play-session-1"
    assert "X-Emby-Token" not in headers
    assert "X-Emby-Authorization" not in headers
    assert "Content-Type" not in headers


def test_play_uses_authenticated_user_and_reports_progress(monkeypatch):
    async def run_test():
        account = EmbyAccount(
            url="https://example.com/emby",
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
                assert kwargs["params"]["UserId"] == "user-1"
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {
                                "Id": "media-source-1",
                                "DirectStreamUrl": "/emby/Videos/item-1/stream",
                                "DefaultAudioStreamIndex": 2,
                                "DefaultSubtitleStreamIndex": 3,
                                "LiveStreamId": "live-stream-1",
                                "MediaStreams": [
                                    {"Type": "Video", "Index": 0},
                                    {"Type": "Audio", "Index": 2},
                                    {"Type": "Subtitle", "Index": 3},
                                ],
                            }
                        ],
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
        assert "/Sessions/Playing/Stopped" in paths
        assert "/emby/Videos/item-1/stream" in paths

        stream_requests = [
            kwargs
            for method, path, kwargs in requests
            if method == "GET" and path == "/emby/Videos/item-1/stream"
        ]
        assert stream_requests
        assert all(kwargs["http_version"] == CurlHttpVersion.V1_1 for kwargs in stream_requests)

        playback_info_params = [
            kwargs["params"]
            for method, path, kwargs in requests
            if method == "POST"
            and path == "/Items/item-1/PlaybackInfo"
            and "MediaSourceId" in kwargs["params"]
        ]
        assert playback_info_params
        assert all(params["AudioStreamIndex"] == 2 for params in playback_info_params)

        progress_payloads = [
            kwargs["json"]
            for method, path, kwargs in requests
            if method == "POST" and path == "/Sessions/Playing/Progress"
        ]
        assert progress_payloads
        assert progress_payloads[-1]["AudioStreamIndex"] == 2
        assert progress_payloads[-1]["SubtitleStreamIndex"] == 3
        assert progress_payloads[-1]["LiveStreamId"] == "live-stream-1"
        assert progress_payloads[-1]["EventName"] == "TimeUpdate"
        assert progress_payloads[-1]["QueueableMediaTypes"] == ["Video"]

        stopped_payloads = [
            kwargs["json"]
            for method, path, kwargs in requests
            if method == "POST" and path == "/Sessions/Playing/Stopped"
        ]
        assert stopped_payloads
        assert stopped_payloads[-1]["NowPlayingQueue"] == []
        assert stopped_payloads[-1]["QueueableMediaTypes"] == ["Video"]

    asyncio.run(run_test())


def test_play_omits_emby_auth_headers_for_external_direct_stream_url(monkeypatch):
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

        stream_headers = []

        async def fake_request(method, path, **kwargs):
            if path.endswith("/PlaybackInfo"):
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {
                                "Id": "media-source-1",
                                "DirectStreamUrl": "https://cdn.example.net/video.mp4",
                            }
                        ],
                    }
                )
            if method == "GET" and path == "https://cdn.example.net/video.mp4":
                stream_headers.append(kwargs["headers"])
            return DummyResponse({})

        original_sleep = asyncio.sleep

        async def fake_sleep(_seconds):
            await original_sleep(0)

        monkeypatch.setattr(emby, "_request", fake_request)
        monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", fake_sleep)
        monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda a, b: 0)

        assert await emby.play({"Id": "item-1", "Name": "Movie"}, time=1) is True

        assert stream_headers
        assert all("X-Emby-Token" not in headers for headers in stream_headers)
        assert all("X-Emby-Authorization" not in headers for headers in stream_headers)

    asyncio.run(run_test())


def test_play_uses_transcoding_url_when_direct_stream_is_unavailable(monkeypatch):
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
            if path.endswith("/PlaybackInfo"):
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {
                                "Id": "media-source-1",
                                "TranscodingUrl": "/Videos/item-1/master.m3u8?PlaySessionId=play-session-1",
                                "DefaultAudioStreamIndex": 1,
                            }
                        ],
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

        stream_requests = [
            (path, kwargs)
            for method, path, kwargs in requests
            if method == "GET" and path.startswith("/Videos/item-1/master.m3u8")
        ]
        assert stream_requests
        assert all(kwargs.get("params") is None for _path, kwargs in stream_requests)

        progress_payloads = [
            kwargs["json"]
            for method, path, kwargs in requests
            if method == "POST" and path == "/Sessions/Playing/Progress"
        ]
        assert progress_payloads
        assert progress_payloads[-1]["PlayMethod"] == "Transcode"
        assert progress_payloads[-1]["AudioStreamIndex"] == 1

    asyncio.run(run_test())


def test_play_prefers_media_source_with_direct_stream_url(monkeypatch):
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
            if path.endswith("/PlaybackInfo"):
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {"Id": "media-source-unusable", "Container": "mkv"},
                            {
                                "Id": "media-source-direct",
                                "DirectStreamUrl": "/Videos/item-1/direct.mp4",
                                "DefaultAudioStreamIndex": 2,
                            },
                        ],
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
        assert "/Videos/item-1/direct.mp4" in paths
        assert "/Videos/item-1/stream.mkv" not in paths

        playback_params = [
            kwargs["params"]
            for method, path, kwargs in requests
            if method == "POST"
            and path == "/Items/item-1/PlaybackInfo"
            and kwargs["params"].get("MediaSourceId") == "media-source-direct"
        ]
        assert playback_params
        assert all(params["AudioStreamIndex"] == 2 for params in playback_params)

    asyncio.run(run_test())


def test_play_builds_stream_url_when_server_omits_stream_urls(monkeypatch):
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
            if path.endswith("/PlaybackInfo"):
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {
                                "Id": "media-source-1",
                                "Container": "mkv",
                                "DefaultAudioStreamIndex": 1,
                            }
                        ],
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

        stream_params = [
            kwargs["params"]
            for method, path, kwargs in requests
            if method == "GET" and path == "/Videos/item-1/stream.mkv"
        ]
        assert stream_params
        assert all(params["Static"] == "true" for params in stream_params)
        assert all(params["DeviceId"] == "test-device-id" for params in stream_params)
        assert all(params["MediaSourceId"] == "media-source-1" for params in stream_params)
        assert all(params["PlaySessionId"] == "play-session-1" for params in stream_params)
        assert all(params["api_key"] == "token-1" for params in stream_params)
        assert all(params["AudioStreamIndex"] == 1 for params in stream_params)

    asyncio.run(run_test())


def test_play_restarts_stream_after_range_end_when_bytes_were_read(monkeypatch):
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

        ranges = []

        async def fake_request(method, path, **kwargs):
            if path.endswith("/PlaybackInfo"):
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {"Id": "media-source-1", "DirectStreamUrl": "/Videos/item-1/stream"}
                        ],
                    }
                )
            if method == "GET" and path == "/Videos/item-1/stream":
                range_header = kwargs["headers"]["Range"]
                ranges.append(range_header)
                if range_header != "bytes=0-":
                    raise EmbyStatusError("访问失败: 异常 HTTP 代码 416")
                return DummyResponse({})
            return DummyResponse({})

        original_sleep = asyncio.sleep

        async def fake_sleep(_seconds):
            await original_sleep(0)

        monkeypatch.setattr(emby, "_request", fake_request)
        monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", fake_sleep)
        monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda a, b: 0)

        assert await emby.play({"Id": "item-1", "Name": "Movie"}, time=1) is True

        assert "bytes=0-" in ranges
        assert any(range_header != "bytes=0-" for range_header in ranges)
        assert ranges.count("bytes=0-") >= 2

    asyncio.run(run_test())


def test_play_ignores_optional_additional_parts_status_error(monkeypatch):
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
            if path == "/Videos/item-1/AdditionalParts":
                raise EmbyStatusError("404")
            if path.endswith("/PlaybackInfo"):
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {"Id": "media-source-1", "DirectStreamUrl": "/Videos/item-1/stream"}
                        ],
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
        assert "/Videos/item-1/stream" in paths
        assert "/Sessions/Playing/Stopped" in paths

    asyncio.run(run_test())


def test_play_fails_when_stream_request_fails(monkeypatch):
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

        class BrokenStreamResponse(DummyResponse):
            async def aiter_content(self, chunk_size=1024):
                raise RequestsError("stream denied")
                yield b""

        async def fake_request(method, path, **kwargs):
            if path.endswith("/PlaybackInfo"):
                return DummyResponse(
                    {
                        "PlaySessionId": "play-session-1",
                        "MediaSources": [
                            {"Id": "media-source-1", "DirectStreamUrl": "/Videos/item-1/stream"}
                        ],
                    }
                )
            if method == "GET" and path == "/Videos/item-1/stream":
                return BrokenStreamResponse({})
            return DummyResponse({})

        original_sleep = asyncio.sleep

        async def fake_sleep(_seconds):
            await original_sleep(0)

        monkeypatch.setattr(emby, "_request", fake_request)
        monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", fake_sleep)
        monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda a, b: 0)

        with pytest.raises(EmbyPlayError, match="访问流媒体文件失败"):
            await emby.play({"Id": "item-1", "Name": "Movie"}, time=1)

    asyncio.run(run_test())


def test_play_fails_when_playback_info_json_is_invalid(monkeypatch):
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

        async def fake_request(method, path, **kwargs):
            if path.endswith("/PlaybackInfo"):
                return BrokenJsonResponse()
            return DummyResponse({})

        monkeypatch.setattr(emby, "_request", fake_request)

        with pytest.raises(EmbyPlayError, match="播放信息"):
            await emby.play({"Id": "item-1", "Name": "Movie"}, time=1)

    asyncio.run(run_test())


def test_first_media_source_rejects_non_list_media_sources():
    with pytest.raises(EmbyPlayError, match="媒体源"):
        Emby._first_media_source({"MediaSources": {"Id": "media-source-1"}})


def test_first_media_source_ignores_non_object_media_sources():
    assert Emby._first_media_source(
        {
            "MediaSources": [
                "invalid",
                {"Id": "media-source-1", "DirectStreamUrl": "/Videos/item-1/stream"},
            ]
        }
    ) == {"Id": "media-source-1", "DirectStreamUrl": "/Videos/item-1/stream"}


def test_audio_stream_index_rejects_non_list_media_streams():
    assert Emby._audio_stream_index({"MediaStreams": {"Type": "Audio", "Index": 2}}) is None


def test_audio_stream_index_ignores_non_object_media_streams():
    assert Emby._audio_stream_index({"MediaStreams": ["invalid", {"Type": "Audio", "Index": 2}]}) == 2


def test_item_list_methods_return_empty_lists_for_invalid_json(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)
        emby.set_credentials("token-1", "user-1")

        async def fake_request(method, path, **kwargs):
            return BrokenJsonResponse()

        monkeypatch.setattr(emby, "_request", fake_request)

        assert await emby.get_latest_items() == []
        assert await emby.get_resume_items() == []
        assert await emby.get_folder_items("folder-1") == []

    asyncio.run(run_test())


def test_folder_items_ignore_invalid_items_shape(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)
        emby.set_credentials("token-1", "user-1")

        async def fake_request(method, path, **kwargs):
            return DummyResponse({"Items": "invalid"})

        monkeypatch.setattr(emby, "_request", fake_request)

        assert await emby.get_folder_items("folder-1") == []

    asyncio.run(run_test())


def test_main_page_ignores_invalid_listing_json(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)
        emby.set_credentials("token-1", "user-1")

        async def fake_request(method, path, **kwargs):
            return BrokenJsonResponse()

        original_sleep = asyncio.sleep

        async def fake_sleep(_seconds):
            await original_sleep(0)

        monkeypatch.setattr(emby, "_request", fake_request)
        monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", fake_sleep)

        assert await emby.load_main_page() is None
        assert emby.items == {}

    asyncio.run(run_test())


def test_item_and_user_methods_return_empty_dicts_for_invalid_json(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)
        emby.set_credentials("token-1", "user-1")

        async def fake_request(method, path, **kwargs):
            return BrokenJsonResponse()

        monkeypatch.setattr(emby, "_request", fake_request)

        assert await emby.get_item("item-1") == {}
        assert await emby.get_user() == {}

    asyncio.run(run_test())


def test_item_and_user_methods_return_empty_dicts_for_non_object_json(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)
        emby.set_credentials("token-1", "user-1")

        async def fake_request(method, path, **kwargs):
            return DummyResponse(["invalid"])

        monkeypatch.setattr(emby, "_request", fake_request)

        assert await emby.get_item("item-1") == {}
        assert await emby.get_user() == {}

    asyncio.run(run_test())


def test_mark_played_accepts_any_success_status(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)
        emby.set_credentials("token-1", "user-1")

        async def fake_request(method, path, **kwargs):
            response = DummyResponse({})
            response.status_code = 204
            response.ok = True
            return response

        monkeypatch.setattr(emby, "_request", fake_request)

        assert await emby.mark_played("item-1") is True

    asyncio.run(run_test())


def test_mark_played_rejects_failed_status(monkeypatch):
    async def run_test():
        account = EmbyAccount(url="https://example.com", username="alice")
        emby = Emby(account)
        emby.set_credentials("token-1", "user-1")

        async def fake_request(method, path, **kwargs):
            response = DummyResponse({})
            response.status_code = 500
            response.ok = False
            return response

        monkeypatch.setattr(emby, "_request", fake_request)

        assert await emby.mark_played("item-1") is False

    asyncio.run(run_test())


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (None, 0),
        ({"UserData": []}, 0),
        ({"UserData": {"PlayCount": True}}, 0),
        ({"UserData": {"PlayCount": 2}}, 2),
    ],
)
def test_item_play_count_handles_invalid_shapes(item, expected):
    assert Emby._item_play_count(item) == expected


def test_watch_returns_false_when_no_items():
    account = EmbyAccount(url="https://example.com", username="alice", time=30)
    emby = Emby(account)

    assert asyncio.run(emby.watch()) is False


def test_watch_returns_false_when_all_items_are_too_short():
    account = EmbyAccount(
        url="https://example.com",
        username="alice",
        time=30,
        allow_multiple=False,
    )
    emby = Emby(account)
    emby.items = {
        "item-1": {
            "Id": "item-1",
            "Name": "Short Movie",
            "MediaType": "Video",
            "RunTimeTicks": 10_000_000,
        }
    }

    assert asyncio.run(emby.watch()) is False


def test_watch_returns_false_when_items_have_invalid_shapes():
    account = EmbyAccount(
        url="https://example.com",
        username="alice",
        time=30,
        allow_multiple=False,
    )
    emby = Emby(account)
    emby.items = {
        "item-1": "invalid",
        "item-2": {
            "Id": "item-2",
            "Name": "Short Movie",
            "MediaType": "Video",
            "RunTimeTicks": 10_000_000,
        },
    }

    assert asyncio.run(emby.watch()) is False
