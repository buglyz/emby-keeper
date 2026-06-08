from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.runinfo import RunContext, _running_runs
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
