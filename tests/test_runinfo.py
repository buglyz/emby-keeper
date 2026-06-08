from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus, _running_runs
from embykeeper.schema import Config


def test_run_context_mutable_defaults_are_isolated():
    first = RunContext(id="FIRST")
    second = RunContext(id="SECOND")

    first.parent_ids.append("PARENT")
    first.set(RunStatus.RUNNING)

    assert second.parent_ids == []
    assert second.log == []


def test_run_context_get_ignores_corrupt_cached_record(tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    cache.set("runinfo.BADRUN", '{"id": 123}')

    assert RunContext.get("BADRUN") is None

    config.reset()


def test_run_context_get_ignores_non_json_cached_record(tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    cache.set("runinfo.BADTYPE", {"id": "BADTYPE"})

    assert RunContext.get("BADTYPE") is None

    config.reset()


def test_run_context_get_ignores_cache_read_failure(monkeypatch):
    _running_runs.clear()

    def fail_get(key, default=None):
        if key == "runinfo.FAILREAD":
            raise OSError("read failed")
        return default

    monkeypatch.setattr("embykeeper.runinfo.cache.get", fail_get)

    assert RunContext.get("FAILREAD") is None


def test_run_context_prepare_avoids_existing_ids(tmp_path, monkeypatch):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    _running_runs.clear()
    cache.set("runinfo.AAAAAA", "{}")

    ids = iter(["AAAAAA", "BBBBBB"])
    monkeypatch.setattr("embykeeper.runinfo.random.choices", lambda *_args, **_kwargs: list(next(ids)))

    run = RunContext.prepare("test")

    try:
        assert run.id == "BBBBBB"
    finally:
        _running_runs.clear()
        config.reset()


def test_run_context_prepare_survives_cache_lookup_failure(monkeypatch):
    _running_runs.clear()
    monkeypatch.setattr("embykeeper.runinfo.random.choices", lambda *_args, **_kwargs: list("LOOKUP"))

    def fail_get(key, default=None):
        if key == "runinfo.LOOKUP":
            raise OSError("read failed")
        return default

    monkeypatch.setattr("embykeeper.runinfo.cache.get", fail_get)

    try:
        run = RunContext.prepare("test")
        assert run.id == "LOOKUP"
    finally:
        _running_runs.clear()


def test_run_context_prepare_replaces_invalid_children_cache(tmp_path, monkeypatch):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    _running_runs.clear()
    cache.set("runinfo.children.PARENT", "invalid")
    monkeypatch.setattr("embykeeper.runinfo.random.choices", lambda *_args, **_kwargs: list("CHILD1"))

    run = RunContext.prepare("child", parent_ids=["PARENT"])

    try:
        assert run.id == "CHILD1"
        assert cache.get("runinfo.children.PARENT") == ["CHILD1"]
    finally:
        run.finish(RunStatus.CANCELLED)
        _running_runs.clear()
        config.reset()


def test_run_context_prepare_survives_children_cache_save_failure(monkeypatch):
    _running_runs.clear()
    monkeypatch.setattr("embykeeper.runinfo.random.choices", lambda *_args, **_kwargs: list("CHILD2"))

    def fail_set(key, value):
        if key == "runinfo.children.PARENT":
            raise OSError("write failed")

    monkeypatch.setattr("embykeeper.runinfo.cache.set", fail_set)

    try:
        run = RunContext.prepare("child", parent_ids=["PARENT"])
        assert run.id == "CHILD2"
        assert _running_runs["CHILD2"] is run
    finally:
        _running_runs.clear()


def test_run_context_finish_survives_cache_save_failure(monkeypatch):
    _running_runs.clear()
    run = RunContext(id="FAILSAVE")
    _running_runs[run.id] = run

    def fail_save(self):
        raise OSError("write failed")

    monkeypatch.setattr(RunContext, "save", fail_save)

    try:
        finished = run.finish(RunStatus.SUCCESS, "done")
    finally:
        _running_runs.clear()

    assert finished is run
    assert run.status == RunStatus.SUCCESS
    assert run.status_info == "done"
    assert run._finished.is_set()


def test_run_context_ignores_invalid_children_cache(tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    cache.set("runinfo.children.PARENT", "invalid")

    run = RunContext(id="PARENT")

    assert run.get_children() == []
    assert run.get_running_children() == []

    config.reset()


def test_run_context_ignores_children_cache_read_failure(monkeypatch):
    run = RunContext(id="PARENT")

    def fail_get(key, default=None):
        if key == "runinfo.children.PARENT":
            raise OSError("read failed")
        return default

    monkeypatch.setattr("embykeeper.runinfo.cache.get", fail_get)

    assert run.get_children() == []
    assert run.get_running_children() == []


def test_run_context_cancel_tree_continues_after_cancel_failure(tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    _running_runs.clear()

    parent = RunContext(id="PARENT")
    failing_child = RunContext(id="CHILD1")
    cancelled_child = RunContext(id="CHILD2")
    calls = []

    def fail_cancel():
        calls.append("fail")
        raise RuntimeError("cancel failed")

    failing_child._cancel = fail_cancel
    cancelled_child._cancel = lambda: calls.append("child")
    parent._cancel = lambda: calls.append("parent")
    _running_runs.update({"CHILD1": failing_child, "CHILD2": cancelled_child})
    cache.set("runinfo.children.PARENT", ["CHILD1", "CHILD2"])

    try:
        parent.cancel_tree()

        assert calls == ["fail", "child", "parent"]
    finally:
        _running_runs.clear()
        config.reset()
