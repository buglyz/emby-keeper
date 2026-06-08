import asyncio

from embykeeper.cache import cache
from embykeeper.clean import clean_cache, cleaner
from embykeeper.config import config
from embykeeper.schema import Config


def test_clean_all_except_credentials_uses_exact_prefixes(tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}

    cache.set("emby.credential.example.token", "kept")
    cache.set("config.value", "kept")
    cache.set("emby.credentialed.example.token", "deleted")

    result = clean_cache(cache_prefix="all_except_credentials")

    assert "共 1 条" in result
    assert cache.get("emby.credential.example.token") == "kept"
    assert cache.get("config.value") == "kept"
    assert cache.get("emby.credentialed.example.token") is None

    config.reset()


def test_cleaner_sorts_specific_cache_key_options(tmp_path, monkeypatch):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}

    cache.set("emby.credential.zed.token", "kept")
    cache.set("emby.credential.alice.token", "deleted")

    monkeypatch.setattr("embykeeper.clean.Prompt.ask", lambda *_args, **_kwargs: "5.1")
    monkeypatch.setattr("embykeeper.clean.console.print", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("embykeeper.clean.console.rule", lambda *_args, **_kwargs: None)

    asyncio.run(cleaner())

    assert cache.get("emby.credential.alice.token") is None
    assert cache.get("emby.credential.zed.token") == "kept"

    config.reset()
