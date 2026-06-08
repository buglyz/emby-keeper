import asyncio
import stat

import pytest
import tomli as tomllib
from fastapi import HTTPException
from pydantic import ValidationError

from embykeeper.config import config
from embykeeper.schema import Config
from embykeeperapi.models import GlobalConfigUpdate, NotifierConfigUpdate, ProxyConfigUpdate
from embykeeperapi.routers import config as config_router
from embykeeperapi.routers.config import (
    get_config,
    get_notifier_config,
    test_notifier as send_test_notifier,
    update_config,
    update_notifier_config,
)


def test_global_config_models_reject_boolean_numeric_values():
    with pytest.raises(ValidationError):
        GlobalConfigUpdate(emby_concurrency=True)

    with pytest.raises(ValidationError):
        ProxyConfigUpdate(hostname="127.0.0.1", port=True, scheme="socks5")


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
        assert not list(tmp_path.glob(".config.toml.*.tmp"))

    asyncio.run(run_test())
    config.reset()


def test_get_config_handles_missing_emby_section(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(emby=None))

        response = await get_config(user="tester")

        assert response.emby_time_range is None
        assert response.emby_interval_days is None
        assert response.emby_concurrency is None

    asyncio.run(run_test())
    config.reset()


def test_update_config_creates_missing_emby_section(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text("", encoding="utf-8")
        config.basedir = tmp_path
        config.set(Config(emby=None))

        await update_config(GlobalConfigUpdate(emby_concurrency=2), user="tester")

        assert config._cache.emby.concurrency == 2
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert data["emby"]["concurrency"] == 2

    asyncio.run(run_test())
    config.reset()


def test_update_config_preserves_loaded_config_file(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text("[emby]\nconcurrency = 1\n", encoding="utf-8")
        config.basedir = tmp_path
        config.set(Config(emby={"concurrency": 1}))
        config._conf_file = config_file

        await update_config(GlobalConfigUpdate(emby_concurrency=2), user="tester")

        assert config._conf_file == config_file

    asyncio.run(run_test())
    config.reset()


def test_update_config_persists_to_loaded_config_file(tmp_path):
    async def run_test():
        basedir = tmp_path / "data"
        loaded_dir = tmp_path / "loaded"
        basedir.mkdir()
        loaded_dir.mkdir()
        default_file = basedir / "config.toml"
        loaded_file = loaded_dir / "custom.toml"
        default_file.write_text("[emby]\nconcurrency = 1\n", encoding="utf-8")
        loaded_file.write_text("[emby]\nconcurrency = 1\n", encoding="utf-8")
        config.basedir = basedir
        config.set(Config(emby={"concurrency": 1}))
        config._conf_file = loaded_file

        await update_config(GlobalConfigUpdate(emby_concurrency=2), user="tester")

        assert tomllib.loads(loaded_file.read_text(encoding="utf-8"))["emby"]["concurrency"] == 2
        assert tomllib.loads(default_file.read_text(encoding="utf-8"))["emby"]["concurrency"] == 1

    asyncio.run(run_test())
    config.reset()


def test_write_text_atomic_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text("old-content", encoding="utf-8")

    original_replace = type(config_file).replace

    def fail_replace(self, target):
        if target == config_file:
            raise OSError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(type(config_file), "replace", fail_replace)

    with pytest.raises(OSError):
        config_router._write_text_atomic(config_file, "new-content")

    assert config_file.read_text(encoding="utf-8") == "old-content"
    assert not config_file.with_suffix(".toml.tmp").exists()
    assert not list(tmp_path.glob(".config.toml.*.tmp"))


def test_write_text_atomic_creates_missing_parent(tmp_path):
    config_file = tmp_path / "missing" / "config.toml"

    config_router._write_text_atomic(config_file, "[emby]\nconcurrency = 1\n")

    assert tomllib.loads(config_file.read_text(encoding="utf-8"))["emby"]["concurrency"] == 1
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def test_write_text_atomic_cleans_temp_file_on_type_error(tmp_path):
    config_file = tmp_path / "config.toml"

    with pytest.raises(TypeError):
        config_router._write_text_atomic(config_file, object())

    assert not config_file.exists()
    assert not list(tmp_path.glob(".config.toml.*.tmp"))


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


@pytest.mark.parametrize("concurrency", ["2", True])
def test_update_config_rejects_non_integer_concurrency_when_validation_is_bypassed(tmp_path, concurrency):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(emby={"concurrency": 1}))

        invalid_req = GlobalConfigUpdate.model_construct(
            emby_concurrency=concurrency,
            _fields_set={"emby_concurrency"},
        )

        with pytest.raises(HTTPException) as exc:
            await update_config(invalid_req, user="tester")

        assert exc.value.status_code == 400
        assert config._cache.emby.concurrency == 1

    asyncio.run(run_test())
    config.reset()


def test_update_config_removes_concurrency_when_set_to_null(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[emby]
time_range = "<8:00AM,9:00AM>"
interval_days = "7"
concurrency = 1
""".strip(),
            encoding="utf-8",
        )
        config.basedir = tmp_path
        config.set(
            Config(
                emby={
                    "time_range": "<8:00AM,9:00AM>",
                    "interval_days": "7",
                    "concurrency": 1,
                }
            )
        )

        req = GlobalConfigUpdate.model_construct(
            emby_concurrency=None,
            _fields_set={"emby_concurrency"},
        )
        await update_config(req, user="tester")

        assert config._cache.emby.concurrency is None
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert "concurrency" not in data["emby"]

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


def test_update_config_trims_global_schedule_values(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text("", encoding="utf-8")
        config.basedir = tmp_path
        config.set(Config(emby={"time_range": "<8:00AM,9:00AM>", "interval_days": "7"}))

        await update_config(
            GlobalConfigUpdate(
                emby_time_range=" <10:00AM,11:00AM> ",
                emby_interval_days=" 12 ",
            ),
            user="tester",
        )

        assert config._cache.emby.time_range == "<10:00AM,11:00AM>"
        assert config._cache.emby.interval_days == "12"
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert data["emby"]["time_range"] == "<10:00AM,11:00AM>"
        assert data["emby"]["interval_days"] == "12"

    asyncio.run(run_test())
    config.reset()


def test_update_config_rejects_non_string_global_schedule_when_validation_is_bypassed(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(emby={"time_range": "<8:00AM,9:00AM>", "interval_days": "7"}))

        invalid_req = GlobalConfigUpdate.model_construct(
            emby_time_range=123,
            _fields_set={"emby_time_range"},
        )

        with pytest.raises(HTTPException) as exc:
            await update_config(invalid_req, user="tester")

        assert exc.value.status_code == 400
        assert config._cache.emby.time_range == "<8:00AM,9:00AM>"

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


def test_update_config_rejects_non_string_proxy_hostname(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())

        invalid_proxy = ProxyConfigUpdate.model_construct(
            hostname=123,
            _fields_set={"hostname"},
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
        config.set(Config(proxy={"hostname": "127.0.0.1", "port": 1080, "scheme": "socks5"}))

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


def test_update_config_trims_proxy_hostname(tmp_path):
    async def run_test():
        config_file = tmp_path / "config.toml"
        config_file.write_text("", encoding="utf-8")
        config.basedir = tmp_path
        config.set(Config())

        await update_config(
            GlobalConfigUpdate(
                proxy=ProxyConfigUpdate(hostname=" 127.0.0.1 ", port=1080, scheme="socks5"),
            ),
            user="tester",
        )

        assert config._cache.proxy.hostname == "127.0.0.1"
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert data["proxy"]["hostname"] == "127.0.0.1"

    asyncio.run(run_test())
    config.reset()


def test_update_config_blank_proxy_hostname_clears_field(tmp_path):
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

        await update_config(
            GlobalConfigUpdate(proxy=ProxyConfigUpdate(hostname=" ")),
            user="tester",
        )

        assert config._cache.proxy.hostname is None
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert "hostname" not in data["proxy"]

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


def test_update_notifier_config_saves_telegram_as_apprise_uri(tmp_path, monkeypatch):
    async def run_test():
        async def noop_refresh():
            return None

        monkeypatch.setattr(config_router, "_refresh_notifier", noop_refresh)
        config_file = tmp_path / "config.toml"
        config_file.write_text("", encoding="utf-8")
        config.basedir = tmp_path
        config.set(Config())

        response = await update_notifier_config(
            NotifierConfigUpdate(
                enabled=True,
                method="telegram",
                telegram_bot_token="123456:ABCDEF",
                telegram_chat_id="-1001234567890",
            ),
            user="tester",
        )

        assert response.enabled is True
        assert response.method == "telegram"
        assert response.configured is True
        assert response.telegram_chat_id == "-1001234567890"
        assert "ABCDEF" not in response.model_dump_json()
        assert config._cache.notifier.apprise_uri == "tgram://123456:ABCDEF/-1001234567890"
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        assert data["notifier"]["enabled"] is True
        assert data["notifier"]["method"] == "apprise"
        assert data["notifier"]["apprise_uri"] == "tgram://123456:ABCDEF/-1001234567890"

    asyncio.run(run_test())
    config.reset()


def test_get_notifier_config_masks_existing_apprise_secret(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(
            Config(
                notifier={
                    "enabled": True,
                    "method": "apprise",
                    "apprise_uri": "mailto://user:secret@example.com",
                }
            )
        )

        response = await get_notifier_config(user="tester")

        assert response.enabled is True
        assert response.configured is True
        assert response.target_label == "Apprise URI configured"
        assert "secret" not in response.model_dump_json()

    asyncio.run(run_test())
    config.reset()


def test_update_notifier_config_rejects_enabled_without_target(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config())

        with pytest.raises(HTTPException) as exc:
            await update_notifier_config(NotifierConfigUpdate(enabled=True, clear=True), user="tester")

        assert exc.value.status_code == 400

    asyncio.run(run_test())
    config.reset()


def test_notifier_test_sends_to_telegram_target(tmp_path, monkeypatch):
    class FakeStream:
        ready = True

        def __init__(self, uri):
            calls.append(("init", uri))

        def write(self, message):
            calls.append(("write", message))

        async def join(self):
            calls.append(("join",))

    calls = []

    async def run_test():
        config.basedir = tmp_path
        config.set(Config())
        monkeypatch.setattr(config_router, "AppriseStream", FakeStream)

        response = await send_test_notifier(
            NotifierConfigUpdate(
                telegram_bot_token="123456:ABCDEF",
                telegram_chat_id="@channel",
            ),
            user="tester",
        )

        assert response == {"status": "sent"}
        assert calls == [
            ("init", "tgram://123456:ABCDEF/@channel"),
            ("write", "INFO#Emby Keeper notification test"),
            ("join",),
        ]

    asyncio.run(run_test())
    config.reset()
