import asyncio

from embykeeper.config import config
import embykeeper.data as data
from embykeeper.schema import Config


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", body: bytes = b""):
        self.status_code = status_code
        self.text = text
        self._body = body
        self.headers = {"content-length": str(len(body))}

    async def aiter_bytes(self, chunk_size=512):
        if self._body:
            yield self._body


def fake_async_client(responses, calls):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            calls.append(url)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    return FakeAsyncClient


def collect_datas(name):
    async def run_test():
        results = []
        async for item in data.get_datas(name):
            results.append(item)
        return results

    return asyncio.run(run_test())


def test_build_cdn_urls_normalizes_custom_origin():
    urls = data._build_cdn_urls("cdn.example.com/")

    assert urls[0] == "https://cdn.example.com/gh/emby-keeper/emby-keeper-data"
    assert "https://cdn.jsdelivr.net/gh/emby-keeper/emby-keeper-data" in urls
    assert data._build_cdn_urls("") == data.DEFAULT_CDN_URLS


def test_custom_cdn_origin_env_can_disable_custom_cdn(monkeypatch):
    monkeypatch.delenv("EK_CDN_ORIGIN", raising=False)

    assert data._custom_cdn_origin_from_env() is None
    assert data._build_cdn_urls(data._custom_cdn_origin_from_env()) == data.DEFAULT_CDN_URLS

    monkeypatch.setenv("EK_CDN_ORIGIN", "   ")

    assert data._custom_cdn_origin_from_env() is None
    assert data._build_cdn_urls(data._custom_cdn_origin_from_env() or "") == data.DEFAULT_CDN_URLS


def test_refresh_version_falls_back_after_failed_cdn(monkeypatch, tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    data.versions.clear()
    monkeypatch.setattr(data, "cdn_urls", ["https://bad.example", "https://good.example"])
    responses = [
        FakeResponse(500),
        FakeResponse(200, text="latest.yaml=actual.yaml\nignored-line\nkey=a=b\n"),
    ]
    calls = []
    monkeypatch.setattr(data.httpx, "AsyncClient", fake_async_client(responses, calls))

    assert asyncio.run(data.refresh_version()) is True

    assert calls == ["https://bad.example/version", "https://good.example/version"]
    assert data.versions["latest.yaml"] == "actual.yaml"
    assert data.versions["key"] == "a=b"
    config.reset()


def test_refresh_version_skips_when_lock_is_held(monkeypatch, tmp_path):
    async def run_test():
        config.set(Config())
        config.basedir = tmp_path

        class FailingAsyncClient:
            def __init__(self, *args, **kwargs):
                raise AssertionError("network should not be used when refresh lock is held")

        monkeypatch.setattr(data.httpx, "AsyncClient", FailingAsyncClient)

        await data.lock.acquire()
        try:
            assert await data.refresh_version() is False
        finally:
            data.lock.release()
            config.reset()

    asyncio.run(run_test())


def test_get_datas_falls_back_to_versioned_name_once(monkeypatch, tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    data.versions.clear()
    monkeypatch.setattr(data, "cdn_urls", ["https://cdn.example"])
    responses = [
        FakeResponse(404),
        FakeResponse(200, text="latest.yaml=actual.yaml\n"),
        FakeResponse(200, body=b"content"),
    ]
    calls = []
    monkeypatch.setattr(data.httpx, "AsyncClient", fake_async_client(responses, calls))

    results = collect_datas("latest.yaml")

    assert results == [tmp_path / "actual.yaml"]
    assert (tmp_path / "actual.yaml").read_bytes() == b"content"
    assert calls == [
        "https://cdn.example/data/latest.yaml",
        "https://cdn.example/version",
        "https://cdn.example/data/actual.yaml",
    ]
    config.reset()


def test_get_datas_yields_none_once_when_all_sources_fail(monkeypatch, tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    monkeypatch.setattr(data, "cdn_urls", ["https://bad.example", "https://worse.example"])
    responses = [FakeResponse(500), FakeResponse(502)]
    calls = []
    monkeypatch.setattr(data.httpx, "AsyncClient", fake_async_client(responses, calls))

    results = collect_datas("missing.yaml")

    assert results == [None]
    assert calls == [
        "https://bad.example/data/missing.yaml",
        "https://worse.example/data/missing.yaml",
    ]
    config.reset()


def test_get_datas_rejects_unsafe_requested_name(monkeypatch, tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsafe resource names must not trigger network access")

    monkeypatch.setattr(data.httpx, "AsyncClient", FailingAsyncClient)

    results = collect_datas("../secret.txt")

    assert results == []
    assert not (tmp_path.parent / "secret.txt").exists()
    config.reset()


def test_get_datas_rejects_symlinked_existing_file(monkeypatch, tmp_path):
    basedir = tmp_path / "basedir"
    basedir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    symlink = basedir / "model.bin"
    try:
        symlink.symlink_to(outside)
    except OSError:
        return
    config.set(Config())
    config.basedir = basedir

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsafe symlinked resource paths must not trigger network access")

    monkeypatch.setattr(data.httpx, "AsyncClient", FailingAsyncClient)

    results = collect_datas("model.bin")

    assert results == []
    assert outside.read_text(encoding="utf-8") == "secret"
    config.reset()


def test_get_datas_rejects_parent_symlink_escape(monkeypatch, tmp_path):
    basedir = tmp_path / "basedir"
    outside = tmp_path / "outside"
    basedir.mkdir()
    outside.mkdir()
    symlink_dir = basedir / "models"
    try:
        symlink_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    config.set(Config())
    config.basedir = basedir

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsafe symlinked resource paths must not trigger network access")

    monkeypatch.setattr(data.httpx, "AsyncClient", FailingAsyncClient)

    results = collect_datas("models/model.bin")

    assert results == []
    assert not (outside / "model.bin").exists()
    config.reset()


def test_get_datas_rejects_unsafe_version_mapping(monkeypatch, tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    data.versions.clear()
    monkeypatch.setattr(data, "cdn_urls", ["https://cdn.example"])
    responses = [
        FakeResponse(404),
        FakeResponse(200, text="latest.yaml=../secret.txt\n"),
    ]
    calls = []
    monkeypatch.setattr(data.httpx, "AsyncClient", fake_async_client(responses, calls))

    results = collect_datas("latest.yaml")

    assert results == [None]
    assert not (tmp_path.parent / "secret.txt").exists()
    assert calls == [
        "https://cdn.example/data/latest.yaml",
        "https://cdn.example/version",
    ]
    config.reset()


def test_get_datas_creates_parent_directory_for_safe_nested_name(monkeypatch, tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    monkeypatch.setattr(data, "cdn_urls", ["https://cdn.example"])
    responses = [FakeResponse(200, body=b"nested")]
    calls = []
    monkeypatch.setattr(data.httpx, "AsyncClient", fake_async_client(responses, calls))

    results = collect_datas("ocr/model.onnx")

    assert results == [tmp_path / "ocr" / "model.onnx"]
    assert (tmp_path / "ocr" / "model.onnx").read_bytes() == b"nested"
    assert calls == ["https://cdn.example/data/ocr/model.onnx"]
    config.reset()
