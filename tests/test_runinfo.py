from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus, _running_runs
from embykeeper.schema import Config


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
