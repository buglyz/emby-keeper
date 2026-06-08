import json

import pytest

from embykeeper.cache import Cache
from embykeeper.config import config
from embykeeper.schema import Config


def test_json_cache_creates_parent_directory(tmp_path):
    basedir = tmp_path / "missing" / "cache-dir"
    config.set(Config())
    config.basedir = basedir

    cache = Cache()
    cache.set("scheduler.example", {"next_time": "2026-01-01T00:00:00"})

    assert (basedir / "cache.json").is_file()

    config.reset()


def test_json_cache_writes_atomically(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.example", {"next_time": "2026-01-01T00:00:00"})

    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8")) == {
        "scheduler": {"example": {"next_time": "2026-01-01T00:00:00"}}
    }
    assert not list(tmp_path.glob(".cache.json.*.tmp"))

    config.reset()


def test_json_cache_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.example", {"next_time": "old"})
    cache_file = tmp_path / "cache.json"
    original_replace = type(cache_file).replace

    def fail_replace(self, target):
        if target == cache_file:
            raise OSError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(type(cache_file), "replace", fail_replace)

    with pytest.raises(OSError):
        cache.set("scheduler.example", {"next_time": "new"})

    assert json.loads(cache_file.read_text(encoding="utf-8")) == {
        "scheduler": {"example": {"next_time": "old"}}
    }
    assert not list(tmp_path.glob(".cache.json.*.tmp"))

    config.reset()
