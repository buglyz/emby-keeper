import asyncio
import json
import stat
from datetime import time
from types import SimpleNamespace

import pytest

from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.emby.api import Emby
from embykeeper.schema import Config
from embykeeperapi.crypto import encrypt_token
from embykeeperapi.scheduler_bridge import SchedulerBridge, WebAccountData


@pytest.fixture(autouse=True)
def reset_config_callbacks():
    callbacks = {
        key: {name: handlers[:] for name, handlers in value.items()}
        for key, value in config._callbacks.items()
    }
    yield
    config.reset()
    config._callbacks = callbacks


def test_api_bridge_uses_defaults_without_config_file(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
                "interval_days": "7",
                "time_range": "8:00AM",
            },
        )

        assert bridge.emby_manager is not None
        assert account_id in bridge.emby_manager._schedulers
        assert account_id in bridge.emby_manager._scheduler_tasks

        status = bridge.get_account_status(account_id)
        assert status["has_token"] is True
        assert status["next_schedule_time"] is not None

        schedules = bridge.get_schedule_info()
        assert schedules[0]["id"] == f"emby.watch.{account_id}"
        assert schedules[0]["account_spec"] == account_id
        assert schedules[0]["next_time"] is not None

        await bridge.shutdown()

    asyncio.run(run_test())


def test_api_bridge_initialize_twice_replaces_scheduler_state(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)
        old_task = bridge._scheduler_task
        old_manager = bridge.emby_manager

        await bridge.initialize(tmp_path)

        assert old_task.done()
        assert bridge._scheduler_task is not old_task
        assert bridge.emby_manager is not old_manager

        await bridge.shutdown()

    asyncio.run(run_test())


def test_api_bridge_shutdown_resets_runtime_state(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        await bridge.shutdown()

        assert bridge.emby_manager is None
        assert bridge.web_accounts is None
        assert bridge._scheduler_task is None
        assert bridge._initialized is False

    asyncio.run(run_test())


def test_initialize_twice_replaces_previous_bridge_state(tmp_path):
    async def run_test():
        first_dir = tmp_path / "first"
        second_dir = tmp_path / "second"
        first_dir.mkdir()
        second_dir.mkdir()

        bridge = SchedulerBridge()
        await bridge.initialize(first_dir)
        first_scheduler_task = bridge._scheduler_task

        bridge.add_account(
            "alice@example.com",
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", first_dir),
                "enabled": True,
            },
        )

        await bridge.initialize(second_dir)

        assert first_scheduler_task.done()
        assert bridge.web_accounts.basedir == second_dir
        assert bridge.web_accounts.get_all() == {}

        await bridge.shutdown()

    asyncio.run(run_test())


def test_shutdown_resets_bridge_state(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)
        bridge._record_status("alice@example.com", is_online=True)

        await bridge.shutdown()

        assert bridge.emby_manager is None
        assert bridge.web_accounts is None
        assert bridge._base_emby_accounts == []
        assert bridge._running_tasks == {}
        assert bridge._account_status == {}
        assert bridge._scheduler_task is None
        assert bridge._initialized is False

    asyncio.run(run_test())


def test_api_bridge_skips_malformed_web_account_credentials(tmp_path):
    async def run_test():
        accounts_file = tmp_path / "web_accounts.json"
        accounts_file.write_text(
            json.dumps(
                {
                    "broken": {
                        "url": "https://example.com",
                        "encrypted_token": encrypt_token("token-1", tmp_path),
                    }
                }
            ),
            encoding="utf-8",
        )

        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        try:
            assert bridge.get_schedule_info() == []
            assert bridge.get_account_status("broken") == {}
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_api_bridge_merge_accounts_creates_missing_emby_config(tmp_path):
    config.basedir = tmp_path
    config.set(Config(emby=None))
    bridge = SchedulerBridge()
    bridge.web_accounts = WebAccountData(tmp_path)

    bridge._merge_accounts()

    assert config._cache.emby is not None
    assert config._cache.emby.account == []


def test_api_bridge_merge_accounts_preserves_loaded_config_file(tmp_path):
    config_file = tmp_path / "config.toml"
    config.basedir = tmp_path
    config.set(Config())
    config._conf_file = config_file
    bridge = SchedulerBridge()
    bridge.web_accounts = WebAccountData(tmp_path)

    bridge._merge_accounts()

    assert config._conf_file == config_file


def test_api_bridge_skips_invalid_encrypted_token_cache_on_initialize(tmp_path):
    async def run_test():
        cache.delete("emby.credential.example.com.alice")
        accounts_file = tmp_path / "web_accounts.json"
        accounts_file.write_text(
            json.dumps(
                {
                    "alice@example.com": {
                        "url": "https://example.com",
                        "username": "alice",
                        "encrypted_token": "not-a-fernet-token",
                    }
                }
            ),
            encoding="utf-8",
        )

        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        try:
            assert bridge.web_accounts.get("alice@example.com")["encrypted_token"] == "not-a-fernet-token"
            assert cache.get("emby.credential.example.com.alice") is None
        finally:
            cache.delete("emby.credential.example.com.alice")
            await bridge.shutdown()

    asyncio.run(run_test())


def test_api_bridge_skips_credential_cache_for_urls_without_hostname(tmp_path):
    async def run_test():
        cache.delete("emby.credential..alice")
        bridge = SchedulerBridge()
        bridge.web_accounts = WebAccountData(tmp_path)

        bridge._cache_account_credentials(
            {
                "url": "not-a-url",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
            }
        )

        assert cache.get("emby.credential..alice") is None

    asyncio.run(run_test())


def test_web_account_user_id_is_normalized(tmp_path):
    accounts = WebAccountData(tmp_path)

    assert accounts._get_account_user_id({"user_id": " user-1 "}) == "user-1"
    assert accounts._get_account_user_id({"userid": 123}) == "123"
    assert accounts._get_account_user_id({"user_id": ["invalid"]}) == ""
    assert accounts._get_account_user_id({"user_id": True}) == ""


def test_web_account_data_falls_back_to_legacy_userid_when_user_id_is_blank(tmp_path):
    accounts = WebAccountData(tmp_path)

    accounts.add(
        "alice@example.com",
        {
            "url": "https://example.com",
            "username": "alice",
            "user_id": " ",
            "userid": " legacy-user ",
        },
    )

    assert accounts.get("alice@example.com")["user_id"] == "legacy-user"


def test_cached_account_credentials_use_normalized_user_id(tmp_path):
    cache_key = "emby.credential.example.com.alice"
    cache.delete(cache_key)
    bridge = SchedulerBridge()
    bridge.web_accounts = WebAccountData(tmp_path)

    bridge._cache_account_credentials(
        {
            "url": "https://example.com",
            "username": "alice",
            "encrypted_token": encrypt_token("token-1", tmp_path),
            "user_id": 123,
        }
    )

    try:
        assert cache.get(cache_key) == {"token": "token-1", "userid": "123"}
    finally:
        cache.delete(cache_key)


def test_cached_account_credentials_ignore_cache_write_failure(tmp_path, monkeypatch):
    bridge = SchedulerBridge()
    bridge.web_accounts = WebAccountData(tmp_path)

    def fail_set(_key, _value):
        raise OSError("write failed")

    monkeypatch.setattr("embykeeper.cache.cache.set", fail_set)

    bridge._cache_account_credentials(
        {
            "url": "https://example.com",
            "username": "alice",
            "encrypted_token": encrypt_token("token-1", tmp_path),
        }
    )


def test_cancel_unified_task_cancels_global_web_account_tasks(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        bridge.web_accounts = WebAccountData(tmp_path)
        bridge.web_accounts.add("alice@example.com", {"url": "https://a.example.com", "username": "alice"})
        bridge.web_accounts.add(
            "bob@example.com",
            {
                "url": "https://b.example.com",
                "username": "bob",
                "interval_days": "7",
            },
        )

        alice_task = asyncio.create_task(asyncio.sleep(60))
        bob_task = asyncio.create_task(asyncio.sleep(60))
        bridge._running_tasks = {
            "alice@example.com": alice_task,
            "bob@example.com": bob_task,
        }

        try:
            assert bridge.cancel_account_task("unified") is True
            assert alice_task.cancelled() or alice_task.cancelling()
            assert not bob_task.cancelled()
            assert bridge._running_tasks == {"bob@example.com": bob_task}
            assert bridge.get_account_status("alice@example.com")["last_watch_status"] == "cancelled"
        finally:
            bob_task.cancel()
            for task in (alice_task, bob_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(run_test())


def test_web_account_data_backs_up_invalid_json_shapes(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        '[{"url":"https://example.com","username":"alice"}]',
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    assert accounts.get_all() == {}
    assert not accounts_file.exists()
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '[{"url":"https://example.com","username":"alice"}]'


def test_web_account_data_backs_up_corrupt_json(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text('{"alice":', encoding="utf-8")

    accounts = WebAccountData(tmp_path)

    assert accounts.get_all() == {}
    assert not accounts_file.exists()
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"alice":'


def test_web_account_data_ignores_symlinked_accounts_file(tmp_path):
    outside = tmp_path / "outside-web-accounts.json"
    outside.write_text(
        '{"alice@example.com":{"url":"https://example.com","username":"alice"}}',
        encoding="utf-8",
    )
    accounts_file = tmp_path / "web_accounts.json"
    try:
        accounts_file.symlink_to(outside)
    except OSError:
        return

    accounts = WebAccountData(tmp_path)

    assert accounts.get_all() == {}
    assert accounts_file.is_symlink()
    assert json.loads(outside.read_text(encoding="utf-8")) == {
        "alice@example.com": {"url": "https://example.com", "username": "alice"}
    }


def test_web_account_data_rejects_save_to_symlinked_accounts_file(tmp_path):
    outside = tmp_path / "outside-web-accounts.json"
    outside.write_text("{}", encoding="utf-8")
    accounts_file = tmp_path / "web_accounts.json"
    try:
        accounts_file.symlink_to(outside)
    except OSError:
        return
    accounts = WebAccountData(tmp_path)

    with pytest.raises(OSError, match="symlink"):
        accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    assert accounts_file.is_symlink()
    assert outside.read_text(encoding="utf-8") == "{}"


def test_web_account_data_creates_missing_basedir(tmp_path):
    basedir = tmp_path / "missing" / "accounts"
    accounts = WebAccountData(basedir)

    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    assert (basedir / "web_accounts.json").is_file()


def test_web_account_data_normalizes_added_accounts_before_save(tmp_path):
    accounts = WebAccountData(tmp_path)

    accounts.add(
        "alice@example.com",
        {
            "url": " https://example.com ",
            "username": " alice ",
            "enabled": "false",
            "time": ["300", "600"],
            "client": " Fileball ",
            "device_id": " ",
        },
    )

    expected = {
        "url": "https://example.com",
        "username": "alice",
        "enabled": False,
        "time": [300, 600],
        "client": "Fileball",
    }
    assert accounts.get("alice@example.com") == expected
    assert json.loads((tmp_path / "web_accounts.json").read_text(encoding="utf-8")) == {
        "alice@example.com": expected
    }


def test_web_account_data_drops_unknown_and_plain_secret_fields(tmp_path):
    accounts = WebAccountData(tmp_path)

    accounts.add(
        "alice@example.com",
        {
            "url": "https://example.com",
            "username": "alice",
            "access_token": "plain-token",
            "password": "plain-password",
            "metadata": {"api_key": "nested-secret"},
            "unknown": "value",
            "userid": " user-1 ",
        },
    )

    expected = {
        "url": "https://example.com",
        "username": "alice",
        "user_id": "user-1",
    }
    assert accounts.get("alice@example.com") == expected
    assert json.loads((tmp_path / "web_accounts.json").read_text(encoding="utf-8")) == {
        "alice@example.com": expected
    }


def test_web_account_data_rejects_invalid_added_accounts(tmp_path):
    accounts = WebAccountData(tmp_path)

    with pytest.raises(ValueError):
        accounts.add("broken", {"url": "", "username": "alice"})

    with pytest.raises(ValueError):
        accounts.add("missing-url", {"username": "alice"})

    with pytest.raises(ValueError):
        accounts.add("missing-username", {"url": "https://example.com"})

    assert accounts.get_all() == {}


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "ftp://example.com",
        "https://example.com:bad",
        "https://user:pass@example.com",
        "https://example.com?token=secret",
        "https://example.com#fragment",
        "https://exa mple.com",
    ],
)
def test_web_account_data_rejects_invalid_added_account_urls(tmp_path, url):
    accounts = WebAccountData(tmp_path)

    with pytest.raises(ValueError):
        accounts.add("alice@example.com", {"url": url, "username": "alice"})

    assert accounts.get_all() == {}


def test_web_account_data_normalizes_updated_accounts_before_save(tmp_path):
    accounts = WebAccountData(tmp_path)
    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    assert (
        accounts.update(
            "alice@example.com",
            {
                "username": " alice2 ",
                "enabled": "true",
                "time": "450",
                "client": " Infuse ",
            },
            new_account_id="alice2@example.com",
        )
        == "alice2@example.com"
    )

    expected = {
        "url": "https://example.com",
        "username": "alice2",
        "enabled": True,
        "time": 450,
        "client": "Infuse",
    }
    assert accounts.get_all() == {"alice2@example.com": expected}


def test_web_account_data_rejects_updates_that_remove_required_fields(tmp_path):
    accounts = WebAccountData(tmp_path)
    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    with pytest.raises(ValueError):
        accounts.update("alice@example.com", {"url": None})

    with pytest.raises(ValueError):
        accounts.update("alice@example.com", {"username": " "})

    assert accounts.get_all() == {"alice@example.com": {"url": "https://example.com", "username": "alice"}}


def test_web_account_data_filters_non_object_accounts(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        '{"alice@example.com":{"url":"https://example.com","username":"alice"},"bad":"value"}',
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {"alice@example.com": {"url": "https://example.com", "username": "alice"}}
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {
        "alice@example.com": {"url": "https://example.com", "username": "alice"},
        "bad": "value",
    }


def test_web_account_data_filters_accounts_missing_required_fields(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {"url": "https://example.com", "username": "alice"},
                "missing-username": {"url": "https://example.com"},
                "blank-url": {"url": "   ", "username": "bob"},
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {"alice@example.com": {"url": "https://example.com", "username": "alice"}}
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert "missing-username" in backup_data
    assert "blank-url" in backup_data


def test_web_account_data_filters_non_string_required_fields(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {"url": "https://example.com", "username": "alice"},
                "numeric-url": {"url": 123, "username": "bob"},
                "numeric-username": {"url": "https://example.net", "username": 456},
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {"alice@example.com": {"url": "https://example.com", "username": "alice"}}
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert "numeric-url" in backup_data
    assert "numeric-username" in backup_data


def test_web_account_data_filters_accounts_with_invalid_urls(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {"url": "https://example.com", "username": "alice"},
                "bad-scheme": {"url": "ftp://example.com", "username": "bob"},
                "bad-port": {"url": "https://example.com:bad", "username": "carol"},
                "with-secret": {"url": "https://user:pass@example.com", "username": "dave"},
                "with-query": {"url": "https://example.com?token=secret", "username": "erin"},
                "with-fragment": {"url": "https://example.com#fragment", "username": "frank"},
                "with-space": {"url": "https://exa mple.com", "username": "grace"},
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {"alice@example.com": {"url": "https://example.com", "username": "alice"}}
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert "bad-scheme" in backup_data
    assert "bad-port" in backup_data
    assert "with-secret" in backup_data
    assert "with-query" in backup_data
    assert "with-fragment" in backup_data
    assert "with-space" in backup_data


def test_web_account_data_trims_required_fields_from_legacy_file(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {
                    "url": " https://example.com ",
                    "username": " alice ",
                    "encrypted_token": "token-1",
                }
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {
        "alice@example.com": {
            "url": "https://example.com",
            "username": "alice",
            "encrypted_token": "token-1",
        }
    }
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {
        "alice@example.com": {
            "url": " https://example.com ",
            "username": " alice ",
            "encrypted_token": "token-1",
        }
    }


def test_web_account_data_drops_unknown_fields_from_legacy_file(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {
                    "url": "https://example.com",
                    "username": "alice",
                    "access_token": "plain-token",
                    "password": "plain-password",
                    "metadata": {"api_key": "nested-secret"},
                    "userid": "user-1",
                }
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {
        "alice@example.com": {
            "url": "https://example.com",
            "username": "alice",
            "user_id": "user-1",
        }
    }
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backup_data["alice@example.com"]["access_token"] == "plain-token"
    assert backup_data["alice@example.com"]["metadata"]["api_key"] == "nested-secret"


def test_web_account_data_normalizes_legacy_encrypted_token_field(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {
                    "url": "https://example.com",
                    "username": "alice",
                    "encrypted_token": " token-1 ",
                },
                "blank@example.com": {
                    "url": "https://blank.example.com",
                    "username": "blank",
                    "encrypted_token": "   ",
                },
                "numeric@example.com": {
                    "url": "https://numeric.example.com",
                    "username": "numeric",
                    "encrypted_token": 123,
                },
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {
        "alice@example.com": {
            "url": "https://example.com",
            "username": "alice",
            "encrypted_token": "token-1",
        },
        "blank@example.com": {
            "url": "https://blank.example.com",
            "username": "blank",
        },
        "numeric@example.com": {
            "url": "https://numeric.example.com",
            "username": "numeric",
        },
    }
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backup_data["alice@example.com"]["encrypted_token"] == " token-1 "
    assert backup_data["blank@example.com"]["encrypted_token"] == "   "
    assert backup_data["numeric@example.com"]["encrypted_token"] == 123


def test_web_account_data_normalizes_legacy_boolean_fields(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {
                    "url": "https://example.com",
                    "username": "alice",
                    "enabled": "false",
                    "allow_stream": "true",
                    "use_proxy": "0",
                    "allow_multiple": "maybe",
                }
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {
        "alice@example.com": {
            "url": "https://example.com",
            "username": "alice",
            "enabled": False,
            "allow_stream": True,
            "use_proxy": False,
        }
    }
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    assert (
        json.loads(backups[0].read_text(encoding="utf-8"))["alice@example.com"]["allow_multiple"] == "maybe"
    )


def test_web_account_data_normalizes_legacy_time_fields(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {
                    "url": "https://example.com",
                    "username": "alice",
                    "time": "300",
                },
                "bob@example.net": {
                    "url": "https://example.net",
                    "username": "bob",
                    "time": ["120", "240"],
                },
                "bad@example.org": {
                    "url": "https://example.org",
                    "username": "bad",
                    "time": "bad",
                },
                "bool@example.org": {
                    "url": "https://bool.example.org",
                    "username": "bool",
                    "time": True,
                },
                "reversed@example.org": {
                    "url": "https://reversed.example.org",
                    "username": "reversed",
                    "time": [600, 300],
                },
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {
        "alice@example.com": {
            "url": "https://example.com",
            "username": "alice",
            "time": 300,
        },
        "bob@example.net": {
            "url": "https://example.net",
            "username": "bob",
            "time": [120, 240],
        },
        "bad@example.org": {
            "url": "https://example.org",
            "username": "bad",
        },
        "bool@example.org": {
            "url": "https://bool.example.org",
            "username": "bool",
        },
        "reversed@example.org": {
            "url": "https://reversed.example.org",
            "username": "reversed",
        },
    }
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    assert len(accounts.to_emby_accounts()) == 5
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backup_data["bad@example.org"]["time"] == "bad"
    assert backup_data["bool@example.org"]["time"] is True


def test_web_account_data_normalizes_legacy_text_fields(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "alice@example.com": {
                    "url": "https://example.com",
                    "username": "alice",
                    "name": " Alice ",
                    "play_id": " item-1 ",
                    "device": "   ",
                    "device_id": 123,
                    "interval_days": " 7 ",
                    "time_range": " <8:00AM,9:00AM> ",
                },
                "bob@example.com": {
                    "url": "https://bob.example.com",
                    "username": "bob",
                    "interval_days": 12,
                },
                "bool@example.com": {
                    "url": "https://bool.example.com",
                    "username": "bool",
                    "interval_days": True,
                },
                "list@example.com": {
                    "url": "https://list.example.com",
                    "username": "list",
                    "interval_days": [7, 12],
                },
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {
        "alice@example.com": {
            "url": "https://example.com",
            "username": "alice",
            "name": "Alice",
            "play_id": "item-1",
            "interval_days": "7",
            "time_range": "<8:00AM,9:00AM>",
        },
        "bob@example.com": {
            "url": "https://bob.example.com",
            "username": "bob",
            "interval_days": "12",
        },
        "bool@example.com": {
            "url": "https://bool.example.com",
            "username": "bool",
        },
        "list@example.com": {
            "url": "https://list.example.com",
            "username": "list",
        },
    }
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    assert len(accounts.to_emby_accounts()) == 4
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backup_data["alice@example.com"]["device"] == "   "
    assert backup_data["alice@example.com"]["device_id"] == 123
    assert backup_data["bool@example.com"]["interval_days"] is True
    assert backup_data["list@example.com"]["interval_days"] == [7, 12]


def test_web_account_data_normalizes_legacy_auth_method(tmp_path):
    accounts_file = tmp_path / "web_accounts.json"
    accounts_file.write_text(
        json.dumps(
            {
                "token@example.com": {
                    "url": "https://token.example.com",
                    "username": "token",
                    "auth_method": " TOKEN ",
                },
                "password@example.com": {
                    "url": "https://password.example.com",
                    "username": "password",
                    "auth_method": "password",
                },
                "legacy@example.com": {
                    "url": "https://legacy.example.com",
                    "username": "legacy",
                    "auth_method": "legacy",
                },
                "numeric@example.com": {
                    "url": "https://numeric.example.com",
                    "username": "numeric",
                    "auth_method": 123,
                },
            }
        ),
        encoding="utf-8",
    )

    accounts = WebAccountData(tmp_path)

    expected = {
        "token@example.com": {
            "url": "https://token.example.com",
            "username": "token",
            "auth_method": "token",
        },
        "password@example.com": {
            "url": "https://password.example.com",
            "username": "password",
            "auth_method": "password",
        },
        "legacy@example.com": {
            "url": "https://legacy.example.com",
            "username": "legacy",
        },
        "numeric@example.com": {
            "url": "https://numeric.example.com",
            "username": "numeric",
        },
    }
    assert accounts.get_all() == expected
    assert json.loads(accounts_file.read_text(encoding="utf-8")) == expected
    backups = list(tmp_path.glob("web_accounts.json.corrupt.*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backup_data["legacy@example.com"]["auth_method"] == "legacy"
    assert backup_data["numeric@example.com"]["auth_method"] == 123


def test_web_account_data_keeps_memory_unchanged_when_save_fails(tmp_path, monkeypatch):
    accounts = WebAccountData(tmp_path)
    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    def fail_save(_data=None):
        raise OSError("disk full")

    monkeypatch.setattr(accounts, "_save", fail_save)

    with pytest.raises(OSError):
        accounts.add("bob@example.com", {"url": "https://bob.example.com", "username": "bob"})
    assert accounts.get_all() == {"alice@example.com": {"url": "https://example.com", "username": "alice"}}

    with pytest.raises(OSError):
        accounts.update("alice@example.com", {"username": "alice2"})
    assert accounts.get("alice@example.com") == {"url": "https://example.com", "username": "alice"}

    with pytest.raises(OSError):
        accounts.delete("alice@example.com")
    assert accounts.get("alice@example.com") == {"url": "https://example.com", "username": "alice"}


def test_web_account_data_file_is_owner_only(tmp_path):
    accounts = WebAccountData(tmp_path)
    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    mode = stat.S_IMODE((tmp_path / "web_accounts.json").stat().st_mode)

    assert mode == 0o600
    assert not (tmp_path / "web_accounts.json.tmp").exists()
    assert not list(tmp_path.glob(".web_accounts.json.*.tmp"))


def test_web_account_data_save_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    accounts = WebAccountData(tmp_path)
    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})
    accounts_file = tmp_path / "web_accounts.json"

    original_replace = type(accounts_file).replace

    def fail_replace(self, target):
        if target == accounts_file:
            raise OSError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(type(accounts_file), "replace", fail_replace)

    with pytest.raises(OSError):
        accounts._save({"bob@example.com": {"url": "https://example.com", "username": "bob"}})

    assert json.loads(accounts_file.read_text(encoding="utf-8")) == {
        "alice@example.com": {"url": "https://example.com", "username": "alice"}
    }
    assert not (tmp_path / "web_accounts.json.tmp").exists()
    assert not list(tmp_path.glob(".web_accounts.json.*.tmp"))


def test_web_account_data_save_cleans_temp_file_when_json_dump_fails(tmp_path):
    accounts = WebAccountData(tmp_path)
    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    with pytest.raises(TypeError):
        accounts._save(
            {
                "alice@example.com": {"url": "https://example.com", "username": "alice"},
                "bad@example.com": {"url": "https://bad.example.com", "username": object()},
            }
        )

    assert accounts.get_all() == {"alice@example.com": {"url": "https://example.com", "username": "alice"}}
    assert json.loads((tmp_path / "web_accounts.json").read_text(encoding="utf-8")) == {
        "alice@example.com": {"url": "https://example.com", "username": "alice"}
    }
    assert not (tmp_path / "web_accounts.json.tmp").exists()
    assert not list(tmp_path.glob(".web_accounts.json.*.tmp"))


def test_web_account_data_returns_copies(tmp_path):
    accounts = WebAccountData(tmp_path)
    accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    account = accounts.get("alice@example.com")
    account["username"] = "changed"

    all_accounts = accounts.get_all()
    all_accounts["alice@example.com"]["username"] = "changed-again"

    assert accounts.get("alice@example.com") == {"url": "https://example.com", "username": "alice"}


def test_web_account_data_isolates_mutable_account_data(tmp_path):
    accounts = WebAccountData(tmp_path)
    source = {
        "url": "https://example.com",
        "username": "alice",
        "time": [300, 600],
        "metadata": {"device": "phone"},
    }

    accounts.add("alice@example.com", source)
    source["time"][0] = 1
    source["metadata"]["device"] = "tablet"

    account = accounts.get("alice@example.com")
    account["time"][1] = 2

    all_accounts = accounts.get_all()
    all_accounts["alice@example.com"]["time"][0] = 3

    assert accounts.get("alice@example.com") == {
        "url": "https://example.com",
        "username": "alice",
        "time": [300, 600],
    }


def test_web_account_data_update_isolates_mutable_values(tmp_path):
    accounts = WebAccountData(tmp_path)
    accounts.add(
        "alice@example.com",
        {"url": "https://example.com", "username": "alice", "time": [300, 600]},
    )
    update = {"time": [120, 240], "metadata": {"device": "phone"}}

    assert accounts.update("alice@example.com", update) == "alice@example.com"
    update["time"][0] = 1
    update["metadata"]["device"] = "tablet"

    assert accounts.get("alice@example.com") == {
        "url": "https://example.com",
        "username": "alice",
        "time": [120, 240],
    }


def test_trigger_watch_many_skips_disabled_and_independent_when_requested(tmp_path, monkeypatch):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)
        bridge.web_accounts.add(
            "global@example.com",
            {"url": "https://global.example.com", "username": "global", "enabled": True},
        )
        bridge.web_accounts.add(
            "independent@example.com",
            {
                "url": "https://independent.example.com",
                "username": "independent",
                "enabled": True,
                "interval_days": "7",
            },
        )
        bridge.web_accounts.add(
            "disabled@example.com",
            {"url": "https://disabled.example.com", "username": "disabled", "enabled": False},
        )

        triggered = []

        async def fake_trigger_watch(account_id):
            triggered.append(account_id)
            return {"run_id": account_id, "status": "started"}

        monkeypatch.setattr(bridge, "trigger_watch", fake_trigger_watch)

        result = await bridge.trigger_watch_many(unified_only=True)

        assert result["status"] == "started"
        assert triggered == ["global@example.com"]

        await bridge.shutdown()

    asyncio.run(run_test())


def test_cancel_global_account_cancels_unified_manager_task(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        bridge.web_accounts = WebAccountData(tmp_path)
        bridge.web_accounts.add("alice@example.com", {"url": "https://a.example.com", "username": "alice"})
        bridge.web_accounts.add("bob@example.com", {"url": "https://b.example.com", "username": "bob"})
        bridge.web_accounts.add(
            "carol@example.com",
            {
                "url": "https://c.example.com",
                "username": "carol",
                "interval_days": "7",
            },
        )

        unified_task = asyncio.create_task(asyncio.sleep(60))
        bridge.emby_manager = SimpleNamespace(
            _tasks={"unified": unified_task},
            _running={"alice@example.com", "bob@example.com", "carol@example.com"},
            _schedulers={},
        )

        try:
            assert bridge.cancel_account_task("alice@example.com") is True
            assert unified_task.cancelled() or unified_task.cancelling()
            assert bridge.emby_manager._tasks == {}
            assert "alice@example.com" not in bridge.emby_manager._running
            assert "bob@example.com" not in bridge.emby_manager._running
            assert "carol@example.com" in bridge.emby_manager._running
            assert bridge.get_account_status("alice@example.com")["is_running"] is False
            assert bridge.get_account_status("bob@example.com")["last_watch_status"] == "cancelled"
            assert bridge.get_account_status("carol@example.com")["last_watch_status"] is None
        finally:
            try:
                await unified_task
            except asyncio.CancelledError:
                pass

    asyncio.run(run_test())


def test_global_schedule_change_reschedules_running_tasks(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        bridge.add_account(
            "alice@example.com",
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        old_scheduler = bridge.emby_manager._schedulers["unified"]
        old_task = bridge.emby_manager._scheduler_tasks["unified"]

        new_config = config._cache.model_copy(
            update={"emby": config._cache.emby.model_copy(update={"time_range": "<10:00AM,11:00AM>"})}
        )
        assert config.set(new_config) is True

        new_scheduler = bridge.emby_manager._schedulers["unified"]

        assert new_scheduler is not old_scheduler
        assert bridge.emby_manager._scheduler_tasks["unified"] is not old_task
        assert old_task.cancelling() > 0
        assert new_scheduler.start_time.hour == 10
        assert new_scheduler.end_time.hour == 11

        await bridge.shutdown()

    asyncio.run(run_test())


def test_renaming_account_cancels_running_manual_task(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        new_account_id = "alice@renamed"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        bridge._running_tasks[account_id] = task

        try:
            assert bridge.update_account(account_id, {"name": "renamed"}, new_account_id) == new_account_id
            assert account_id not in bridge._running_tasks
            assert new_account_id not in bridge._running_tasks
            assert task.cancelling() > 0

            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_updating_account_cancels_running_manual_task(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        bridge._running_tasks[account_id] = task

        try:
            assert bridge.update_account(account_id, {"play_id": "item-2"}) == account_id
            assert account_id not in bridge._running_tasks
            assert task.cancelling() > 0

            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_deleting_account_cancels_running_manual_task_and_status(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )
        bridge._record_status(account_id, is_online=True)

        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        bridge._running_tasks[account_id] = task

        try:
            bridge.delete_account(account_id)

            assert account_id not in bridge._running_tasks
            assert account_id not in bridge._account_status
            assert task.cancelling() > 0

            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_deleting_account_marks_running_watch_context_cancelled(tmp_path, monkeypatch):
    async def run_test():
        from embykeeper.runinfo import RunContext, RunStatus

        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        started = asyncio.Event()
        blocker = asyncio.Event()

        async def block_authenticate(_emby):
            started.set()
            await blocker.wait()
            return True

        monkeypatch.setattr(bridge, "_authenticate_emby", block_authenticate)

        try:
            result = await bridge.trigger_watch(account_id)
            task = bridge._running_tasks[account_id]
            await started.wait()

            bridge.delete_account(account_id)

            with pytest.raises(asyncio.CancelledError):
                await task

            run = RunContext.get(result["run_id"])
            assert run.status == RunStatus.CANCELLED
            assert run.status_info == "Watch task cancelled"
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_update_account_replaces_old_credential_cache(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@old.example"
        new_account_id = "bob@new.example"
        bridge.add_account(
            account_id,
            {
                "url": "https://old.example",
                "username": "alice",
                "encrypted_token": encrypt_token("token-old", tmp_path),
                "enabled": True,
            },
        )

        assert cache.get("emby.credential.old.example.alice") == {"token": "token-old"}

        try:
            updated_id = bridge.update_account(
                account_id,
                {
                    "url": "https://new.example",
                    "username": "bob",
                    "encrypted_token": encrypt_token("token-new", tmp_path),
                },
                new_account_id,
            )

            assert updated_id == new_account_id
            assert cache.get("emby.credential.old.example.alice") is None
            assert cache.get("emby.credential.new.example.bob") == {"token": "token-new"}
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_delete_account_clears_credential_cache(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        assert cache.get("emby.credential.example.com.alice") == {"token": "token-1"}

        try:
            bridge.delete_account(account_id)

            assert cache.get("emby.credential.example.com.alice") is None
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_prepare_emby_uses_stored_user_id(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_data = {
            "url": "https://example.com",
            "username": "alice",
            "encrypted_token": encrypt_token("token-1", tmp_path),
            "user_id": "user-1",
            "enabled": True,
        }

        emby, _ = bridge._prepare_emby(account_data)

        assert emby.token == "token-1"
        assert emby.user_id == "user-1"
        assert cache.get("emby.credential.example.com.alice") == {
            "token": "token-1",
            "userid": "user-1",
        }

        await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_login_remembers_user_id(tmp_path, monkeypatch):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        async def fake_authenticate(emby):
            emby.set_credentials("token-1", "user-1")
            return True

        monkeypatch.setattr(bridge, "_authenticate_emby", fake_authenticate)

        result = await bridge.trigger_login(account_id)

        assert result["status"] == "success"
        assert bridge.web_accounts.get(account_id)["user_id"] == "user-1"
        assert cache.get("emby.credential.example.com.alice") == {
            "token": "token-1",
            "userid": "user-1",
        }

        await bridge.shutdown()

    asyncio.run(run_test())


@pytest.mark.parametrize("exc_type", [OSError, RuntimeError])
def test_trigger_login_succeeds_when_remembering_user_id_fails(tmp_path, monkeypatch, exc_type):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        async def fake_authenticate(emby):
            emby.set_credentials("token-1", "user-1")
            return True

        def fail_update(*_args, **_kwargs):
            raise exc_type("storage failed")

        monkeypatch.setattr(bridge, "_authenticate_emby", fake_authenticate)
        monkeypatch.setattr(bridge.web_accounts, "update", fail_update)

        result = await bridge.trigger_login(account_id)

        assert result["status"] == "success"
        assert bridge.web_accounts.get(account_id).get("user_id") is None
        assert bridge.get_account_status(account_id)["is_online"] is True

        await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_login_handles_invalid_encrypted_token(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": "not-a-fernet-token",
                "enabled": True,
            },
        )

        try:
            result = await bridge.trigger_login(account_id)

            assert result["status"] == "error"
            assert result["run_id"]
            assert bridge.get_account_status(account_id)["is_online"] is False
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_watch_uses_stored_credentials_and_play_id(tmp_path, monkeypatch):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "user_id": "user-1",
                "play_id": "item-1",
                "enabled": True,
            },
        )

        seen = []

        async def fake_authenticate(emby):
            seen.append(("auth", emby.token, emby.user_id))
            return True

        async def fake_get_item(self, item_id):
            seen.append(("get_item", self.token, self.user_id, item_id))
            return {"Id": item_id, "Name": "Movie", "MediaType": "Video", "RunTimeTicks": 10000000}

        async def fake_watch(self):
            seen.append(("watch", self.token, self.user_id, sorted(self.items)))
            return True

        monkeypatch.setattr(bridge, "_authenticate_emby", fake_authenticate)
        monkeypatch.setattr(Emby, "get_item", fake_get_item)
        monkeypatch.setattr(Emby, "watch", fake_watch)

        result = await bridge.trigger_watch(account_id)
        assert result["status"] == "started"

        task = bridge._running_tasks[account_id]
        await task

        assert seen == [
            ("auth", "token-1", "user-1"),
            ("get_item", "token-1", "user-1", "item-1"),
            ("watch", "token-1", "user-1", ["item-1"]),
        ]
        assert bridge.get_account_status(account_id)["last_watch_status"] == "success"

        await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_watch_handles_invalid_encrypted_token(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": "not-a-fernet-token",
                "enabled": True,
            },
        )

        try:
            result = await bridge.trigger_watch(account_id)
            assert result["status"] == "started"

            task = bridge._running_tasks[account_id]
            await task

            status = bridge.get_account_status(account_id)
            assert status["last_watch_status"] == "error"
        finally:
            await bridge.shutdown()

    asyncio.run(run_test())


def test_trigger_watch_returns_running_for_duplicate_account_task(tmp_path):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "enabled": True,
            },
        )

        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        bridge._running_tasks[account_id] = task

        result = await bridge.trigger_watch(account_id)

        assert result == {
            "run_id": "",
            "status": "running",
            "message": "Watch task already running",
        }
        assert bridge._running_tasks[account_id] is task

        blocker.set()
        await task
        await bridge.shutdown()

    asyncio.run(run_test())


def test_schedule_status_ignores_broken_scheduler_next_time(tmp_path):
    account_id = "alice@example.com"
    bridge = SchedulerBridge()
    bridge.web_accounts = WebAccountData(tmp_path)
    bridge.web_accounts.add(account_id, {"url": "https://example.com", "username": "alice"})

    class BrokenScheduler:
        sid = "emby.watch.alice@example.com"
        days = 7
        start_time = None
        _next_time = None

        @property
        def next_time(self):
            raise RuntimeError("broken next time")

    bridge.emby_manager = SimpleNamespace(_schedulers={account_id: BrokenScheduler()}, _running=set())

    assert bridge.get_account_status(account_id)["next_schedule_time"] is None
    schedules = bridge.get_schedule_info()
    assert schedules[0]["next_time"] is None


def test_schedule_status_tolerates_malformed_display_fields(tmp_path):
    account_id = "alice@example.com"
    bridge = SchedulerBridge()
    bridge.web_accounts = WebAccountData(tmp_path)
    bridge.web_accounts.add(account_id, {"url": "https://example.com", "username": "alice"})

    class PartialScheduler:
        sid = "emby.watch.alice@example.com"
        days = [7]
        start_time = time(8, 30)
        _next_time = None
        next_time = None

    bridge.emby_manager = SimpleNamespace(_schedulers={account_id: PartialScheduler()}, _running=set())

    schedules = bridge.get_schedule_info()

    assert schedules[0]["interval_days"] == "7"
    assert schedules[0]["time_range"] == "08:30"


def test_schedule_info_marks_running_manager_task(tmp_path):
    bridge = SchedulerBridge()
    bridge.web_accounts = WebAccountData(tmp_path)

    class Scheduler:
        sid = "emby.watch.global"
        days = 7
        start_time = time(8, 30)
        _next_time = None
        next_time = None

    class RunningTask:
        def done(self):
            return False

    bridge.emby_manager = SimpleNamespace(
        _schedulers={"unified": Scheduler()},
        _running=set(),
        _tasks={"unified": RunningTask()},
    )

    schedules = bridge.get_schedule_info()

    assert schedules[0]["account_spec"] == "unified"
    assert schedules[0]["is_running"] is True


def test_trigger_watch_cleanup_preserves_newer_task_for_same_account(tmp_path, monkeypatch):
    async def run_test():
        bridge = SchedulerBridge()
        await bridge.initialize(tmp_path)

        account_id = "alice@example.com"
        bridge.add_account(
            account_id,
            {
                "url": "https://example.com",
                "username": "alice",
                "encrypted_token": encrypt_token("token-1", tmp_path),
                "user_id": "user-1",
                "play_id": "item-1",
                "enabled": True,
            },
        )

        first_can_finish = asyncio.Event()
        second_can_finish = asyncio.Event()
        calls = 0

        async def fake_authenticate(_emby):
            return True

        async def fake_get_item(self, item_id):
            return {"Id": item_id, "Name": "Movie", "MediaType": "Video", "RunTimeTicks": 10000000}

        async def fake_watch(_self):
            nonlocal calls
            calls += 1
            if calls == 1:
                await first_can_finish.wait()
            else:
                await second_can_finish.wait()
            return True

        monkeypatch.setattr(bridge, "_authenticate_emby", fake_authenticate)
        monkeypatch.setattr(Emby, "get_item", fake_get_item)
        monkeypatch.setattr(Emby, "watch", fake_watch)

        await bridge.trigger_watch(account_id)
        first_task = bridge._running_tasks[account_id]

        bridge._running_tasks.pop(account_id)
        await bridge.trigger_watch(account_id)
        second_task = bridge._running_tasks[account_id]

        assert first_task is not second_task

        first_can_finish.set()
        await first_task

        assert bridge._running_tasks[account_id] is second_task

        second_can_finish.set()
        await second_task
        assert account_id not in bridge._running_tasks

        await bridge.shutdown()

    asyncio.run(run_test())
