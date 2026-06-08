import json
import stat

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


def test_json_cache_ignores_non_object_file(tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    (tmp_path / "cache.json").write_text("[]", encoding="utf-8")

    cache = Cache()
    cache.set("scheduler.example", {"next_time": "new"})

    assert cache.get("scheduler.example") == {"next_time": "new"}

    config.reset()


def test_json_cache_writes_atomically(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.example", {"next_time": "2026-01-01T00:00:00"})

    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8")) == {
        "scheduler": {"example": {"next_time": "2026-01-01T00:00:00"}}
    }
    assert stat.S_IMODE((tmp_path / "cache.json").stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".cache.json.*.tmp"))

    config.reset()


def test_json_cache_write_ignores_chmod_failure(tmp_path, monkeypatch):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    original_chmod = type(tmp_path).chmod

    def fail_tmp_chmod(self, mode):
        if self.name.startswith(".cache.json."):
            raise OSError("chmod unsupported")
        return original_chmod(self, mode)

    monkeypatch.setattr(type(tmp_path), "chmod", fail_tmp_chmod)

    cache.set("scheduler.example", {"next_time": "new"})

    assert cache.get("scheduler.example") == {"next_time": "new"}
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8")) == {
        "scheduler": {"example": {"next_time": "new"}}
    }

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
    assert cache.get("scheduler.example") == {"next_time": "old"}
    assert not list(tmp_path.glob(".cache.json.*.tmp"))

    config.reset()


def test_json_cache_cleans_temp_file_when_json_dump_fails(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.example", {"next_time": "old"})

    with pytest.raises(TypeError):
        cache.set("scheduler.example", {"next_time": object()})

    assert cache.get("scheduler.example") == {"next_time": "old"}
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8")) == {
        "scheduler": {"example": {"next_time": "old"}}
    }
    assert not list(tmp_path.glob(".cache.json.*.tmp"))

    config.reset()


def test_json_cache_isolates_mutable_values(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    value = {"items": ["a"], "meta": {"source": "test"}}
    cache.set("example.value", value)
    value["items"].append("from-source")
    value["meta"]["source"] = "changed-source"

    cached = cache.get("example.value")
    cached["items"].append("from-return")
    cached["meta"]["source"] = "changed-return"

    assert cache.get("example.value") == {"items": ["a"], "meta": {"source": "test"}}

    config.reset()


def test_json_cache_preserves_empty_object_values(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("example.empty", {})

    assert cache.get("example.empty") == {}
    assert cache.get("example.missing") is None

    config.reset()


def test_json_cache_set_replaces_non_object_parent(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler", "bad")
    cache.set("scheduler.example.next_time", "new")

    assert cache.get("scheduler.example.next_time") == "new"
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8")) == {
        "scheduler": {"example": {"next_time": "new"}}
    }

    config.reset()


def test_json_cache_delete_missing_key_is_noop(tmp_path, monkeypatch):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.example", {"next_time": "old"})

    def fail_write(*_args, **_kwargs):
        raise AssertionError("delete of missing key should not write")

    monkeypatch.setattr(cache, "_write_json_cache", fail_write)

    cache.delete("scheduler.missing")
    assert cache.get("scheduler.example") == {"next_time": "old"}

    config.reset()


def test_json_cache_delete_removes_empty_parent_dicts(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.example.next_time", "old")
    cache.delete("scheduler.example.next_time")

    assert cache.get("scheduler") is None
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8")) == {}

    config.reset()


def test_json_cache_delete_many_removes_empty_parent_dicts(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.one.next_time", "old")
    cache.set("scheduler.two.next_time", "old")
    cache.delete_many(["scheduler.one.next_time", "scheduler.two.next_time"])

    assert cache.get("scheduler") is None
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8")) == {}

    config.reset()


def test_json_cache_delete_by_prefix_uses_batch_delete(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("scheduler.one.next_time", "old")
    cache.set("scheduler.two.next_time", "old")
    cache.set("credential.one.token", "token")
    cache.delete_by_prefix("scheduler.")

    assert cache.get("scheduler") is None
    assert cache.get("credential.one.token") == "token"

    config.reset()


def test_json_cache_delete_by_prefix_removes_empty_object_values(tmp_path):
    config.set(Config())
    config.basedir = tmp_path

    cache = Cache()
    cache.set("example.empty", {})
    cache.set("other.value", "kept")

    assert cache.find_by_prefix("example.") == ["example.empty"]

    cache.delete_by_prefix("example.")

    assert cache.get("example.empty") is None
    assert cache.get("other.value") == "kept"

    config.reset()


def test_mongo_cache_find_by_prefix_escapes_regex_metacharacters():
    seen = {}

    class FakeCollection:
        def find(self, query, projection):
            seen["query"] = query
            seen["projection"] = projection
            return [{"_id": "emby.credential.example"}]

    cache = Cache.__new__(Cache)
    cache._mongo_client = object()
    cache._collection = FakeCollection()

    assert cache.find_by_prefix("emby.credential") == ["emby.credential.example"]
    assert seen["query"] == {"_id": {"$regex": r"^emby\.credential"}}
    assert seen["projection"] == {"_id": 1}


def test_mongo_cache_get_ignores_documents_without_value():
    class FakeCollection:
        def find_one(self, query):
            return {"_id": query["_id"]}

    cache = Cache.__new__(Cache)
    cache._mongo_client = object()
    cache._collection = FakeCollection()

    assert cache.get("missing-value", default="fallback") == "fallback"
