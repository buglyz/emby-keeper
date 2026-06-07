import asyncio
import stat

import pytest
import tomli as tomllib
from fastapi import HTTPException

from embykeeper.config import config
from embykeeper.schema import Config
from embykeeperapi.models import GlobalConfigUpdate, ProxyConfigUpdate
from embykeeperapi.routers import config as config_router
from embykeeperapi.routers.config import update_config


def test_update_config_persists_without_removing_existing_accounts(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[emby]
time_range = "<8:00AM,9:00AM>"
interval_days = "3"
concurrency = 1

[[emby.account]]
url = "https://example.com"
username = "alice"
password = "secret"
""".strip(),
            encoding="utf-8",
        )

        config.basedir = tmp_path
        config.set(
            Config(
                emby={
                    "time_range": "<8:00AM,9:00AM>",
                    "interval_days": "3",
                    "concurrency": 1,
                    "account": [
                        {
                            "url": "https://example.com",
                            "username": "alice",
                            "password": "secret",
                        }
                    ],
                }
            )
        )

        await update_config(
            GlobalConfigUpdate(
                emby_time_range="<10:00AM,11:00AM>",
                emby_interval_days="7",
                emby_concurrency=2,
                proxy=ProxyConfigUpdate(hostname="127.0.0.1", port=1080, scheme="socks5"),
            ),
            user="tester",
        )

        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert data["emby"]["time_range"] == "<10:00AM,11:00AM>"
        assert data["emby"]["interval_days"] == "7"
        assert data["emby"]["concurrency"] == 2
        assert data["emby"]["account"][0]["username"] == "alice"
        assert data["proxy"] == {"hostname": "127.0.0.1", "port": 1080, "scheme": "socks5"}
        assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
        assert not (tmp_path / "config.toml.tmp").exists()

    asyncio.run(run_test())
    config.reset()


def test_write_text_atomic_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text("old-content", encoding="utf-8")

    original_replace = type(config_file).replace

    def fail_replace(self, target):
        if self == config_file.with_suffix(".toml.tmp"):
            raise OSError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(type(config_file), "replace", fail_replace)

    with pytest.raises(OSError):
        config_router._write_text_atomic(config_file, "new-content")

    assert config_file.read_text(encoding="utf-8") == "old-content"
    assert not config_file.with_suffix(".toml.tmp").exists()


def test_update_config_rejects_invalid_runtime_values(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(emby={"concurrency": 1}))

        invalid_req = GlobalConfigUpdate.model_construct(
            emby_concurrency=0,
            _fields_set={"emby_concurrency"},
        )

        with pytest.raises(HTTPException) as exc:
            await update_config(invalid_req, user="tester")

        assert exc.value.status_code == 400
        assert config._cache.emby.concurrency == 1

    asyncio.run(run_test())
    config.reset()


def test_update_config_rejects_invalid_schedule_values(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(emby={"time_range": "<8:00AM,9:00AM>", "interval_days": "7"}))

        with pytest.raises(HTTPException) as exc:
            await update_config(
                GlobalConfigUpdate(emby_time_range="not-a-time", emby_interval_days="<9,3>"),
                user="tester",
            )

        assert exc.value.status_code == 400
        assert config._cache.emby.time_range == "<8:00AM,9:00AM>"
        assert config._cache.emby.interval_days == "7"

    asyncio.run(run_test())
    config.reset()


def test_update_config_does_not_mutate_runtime_when_persist_fails(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text("[emby\ntime_range = 'broken'", encoding="utf-8")
        config.basedir = tmp_path
        config.set(Config(emby={"time_range": "<8:00AM,9:00AM>", "interval_days": "7"}))

        with pytest.raises(HTTPException) as exc:
            await update_config(
                GlobalConfigUpdate(emby_time_range="<10:00AM,11:00AM>"),
                user="tester",
            )

        assert exc.value.status_code == 500
        assert config._cache.emby.time_range == "<8:00AM,9:00AM>"
        assert config._cache.emby.interval_days == "7"

    asyncio.run(run_test())
    config.reset()


def test_update_config_rejects_empty_global_schedule_values(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(emby={"time_range": "<8:00AM,9:00AM>", "interval_days": "7"}))

        with pytest.raises(HTTPException) as exc:
            await update_config(
                GlobalConfigUpdate(emby_time_range=None),
                user="tester",
            )

        assert exc.value.status_code == 400
        assert config._cache.emby.time_range == "<8:00AM,9:00AM>"

    asyncio.run(run_test())
    config.reset()


def test_update_config_rejects_invalid_proxy_runtime_values(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())

        invalid_proxy = ProxyConfigUpdate.model_construct(
            port=0,
            _fields_set={"port"},
        )
        invalid_req = GlobalConfigUpdate.model_construct(
            proxy=invalid_proxy,
            _fields_set={"proxy"},
        )

        with pytest.raises(HTTPException) as exc:
            await update_config(invalid_req, user="tester")

        assert exc.value.status_code == 400
        assert config._cache.proxy is None

    asyncio.run(run_test())
    config.reset()


def test_update_config_removes_proxy_when_all_proxy_fields_are_empty(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[proxy]
hostname = "127.0.0.1"
port = 1080
scheme = "socks5"
""".strip(),
            encoding="utf-8",
        )
        config.basedir = tmp_path
        config.set(
            Config(proxy={"hostname": "127.0.0.1", "port": 1080, "scheme": "socks5"})
        )

        await update_config(
            GlobalConfigUpdate(
                proxy=ProxyConfigUpdate(hostname=None, port=None, scheme=None),
            ),
            user="tester",
        )

        assert config._cache.proxy is None
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert "proxy" not in data

    asyncio.run(run_test())
    config.reset()


def test_update_config_removes_proxy_when_proxy_is_null(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[proxy]
hostname = "127.0.0.1"
port = 1080
scheme = "socks5"
""".strip(),
            encoding="utf-8",
        )
        config.basedir = tmp_path
        config.set(Config(proxy={"hostname": "127.0.0.1", "port": 1080, "scheme": "socks5"}))

        req = GlobalConfigUpdate.model_construct(proxy=None, _fields_set={"proxy"})
        await update_config(req, user="tester")

        assert config._cache.proxy is None
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert "proxy" not in data

    asyncio.run(run_test())
    config.reset()
