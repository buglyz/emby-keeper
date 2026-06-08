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
