from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.runinfo import RunContext
from embykeeper.schema import Config


def test_run_context_get_ignores_corrupt_cached_record(tmp_path):
    config.set(Config())
    config.basedir = tmp_path
    cache._cache_file = tmp_path / "cache.json"
    cache._data = {}
    cache.set("runinfo.BADRUN", '{"id": 123}')

    assert RunContext.get("BADRUN") is None

    config.reset()
