import pytest
from pydantic import ValidationError

from embykeeper.schema import Config, ConfigModel, EmbyAccount


class MinimalConfig(ConfigModel):
    beta: int = 1
    alpha: int = 1


def test_telegram_checkiner_config_sections_are_supported():
    cfg = Config(
        emby=[{"url": "https://example.com", "username": "alice", "ua": "Fileball/1.3.30"}],
        telegram={"account": [{"phone": "+86 138 0000 0000", "checkin": False, "send": False}]},
        checkiner={"timeout": 120},
        registrar={"concurrency": 2, "templ_a<XiguaEmbyBot>": {"times": ["9:00AM"]}},
        subsonic={"account": [{"url": "https://music.example.com", "username": "bob", "password": "secret"}]},
        site={"checkiner": ["all"], "registrar": ["templ_a<XiguaEmbyBot>"]},
        service={"checkiner": ["terminus"]},
        time="8:00AM",
        concurrent=2,
        random=5,
        listentime="8:00AM",
    )

    assert cfg.emby.account[0].useragent == "Fileball/1.3.30"
    assert cfg.telegram.account[0].phone == "+8613800000000"
    assert cfg.telegram.account[0].checkiner is False
    assert cfg.telegram.account[0].messager is False
    assert cfg.checkiner.timeout == 120
    assert cfg.checkiner.time_range == "8:00AM"
    assert cfg.checkiner.concurrency == 2
    assert cfg.checkiner.random_start == 5
    assert cfg.site.checkiner == ["all"]
    assert cfg.site.registrar == ["templ_a<XiguaEmbyBot>"]
    assert cfg.registrar.concurrency == 2
    assert cfg.registrar.get_site_config("templ_a<XiguaEmbyBot>")["times"] == ["9:00AM"]
    assert not hasattr(cfg, "subsonic")


def test_schema_mutable_defaults_are_isolated():
    first = Config()
    second = Config()

    first.emby.account.append(EmbyAccount(url="https://example.com", username="alice"))
    first.telegram.account.append({"phone": "+8613800000000"})
    first.notifier.enabled = True
    first.site = {"checkiner": ["all"]}

    assert second.emby.account == []
    assert second.telegram.account == []
    assert second.notifier.enabled is False
    assert second.site is None

    first_account = EmbyAccount(url="https://example.com", username="alice")
    second_account = EmbyAccount(url="https://example.net", username="bob")
    first_account.time.append(900)

    assert second_account.time == [300, 600]


def test_unknown_field_error_lists_are_deterministic():
    with pytest.raises(ValidationError) as exc_info:
        MinimalConfig(gamma=1, delta=2)

    message = str(exc_info.value)

    assert "包含未知设置项：delta, gamma" in message
    assert "允许的设置项: alpha, beta" in message


def test_legacy_emby_account_alias_does_not_mutate_source_dict():
    raw = {
        "emby": [
            {
                "url": "https://example.com",
                "username": "alice",
                "ua": "Fileball/1.3.30",
            }
        ]
    }

    cfg = Config(**raw)

    assert cfg.emby.account[0].useragent == "Fileball/1.3.30"
    assert raw["emby"][0] == {
        "url": "https://example.com",
        "username": "alice",
        "ua": "Fileball/1.3.30",
    }


def test_legacy_emby_account_schedule_aliases_are_applied():
    cfg = Config(
        emby={
            "account": [
                {
                    "url": "https://example.com",
                    "username": "alice",
                    "interval": 7,
                    "watchtime": "<8:00AM,9:00AM>",
                    "ua": "Fileball/1.3.30",
                }
            ]
        }
    )

    account = cfg.emby.account[0]
    assert account.interval_days == "7"
    assert account.time_range == "<8:00AM,9:00AM>"
    assert account.useragent == "Fileball/1.3.30"


def test_legacy_emby_account_aliases_do_not_override_canonical_fields():
    account = EmbyAccount(
        url="https://example.com",
        username="alice",
        interval=7,
        interval_days="12",
        watchtime="<8:00AM,9:00AM>",
        time_range="<10:00AM,11:00AM>",
        ua="LegacyAgent",
        useragent="CurrentAgent",
    )

    assert account.interval_days == "12"
    assert account.time_range == "<10:00AM,11:00AM>"
    assert account.useragent == "CurrentAgent"


def test_legacy_global_alias_does_not_mutate_source_dict():
    raw = {"emby": {"account": []}, "interval": "7"}

    cfg = Config(**raw)

    assert cfg.emby.interval_days == "7"
    assert raw == {"emby": {"account": []}, "interval": "7"}


def test_legacy_global_alias_does_not_override_canonical_field():
    cfg = Config(emby={"interval_days": "7"}, interval="12")

    assert cfg.emby.interval_days == "7"


def test_legacy_global_alias_replaces_none_emby_section():
    cfg = Config(emby=None, interval="7")

    assert cfg.emby.interval_days == "7"


def test_use_str_fields_accept_integer_values():
    cfg = Config(emby={"interval_days": 7})

    assert cfg.emby.interval_days == "7"

    cfg = Config(
        emby={
            "account": [
                {
                    "url": "https://example.com",
                    "username": "alice",
                    "interval_days": 7,
                }
            ]
        }
    )

    assert cfg.emby.account[0].interval_days == "7"


def test_emby_account_url_trims_outer_whitespace_and_adds_scheme():
    cfg = Config(emby={"account": [{"url": " example.com/path ", "username": "alice"}]})

    assert str(cfg.emby.account[0].url) == "https://example.com/path"

    cfg = Config(emby={"account": [{"url": " https://example.net ", "username": "bob"}]})

    assert str(cfg.emby.account[0].url) == "https://example.net/"


def test_emby_account_url_rejects_internal_whitespace():
    with pytest.raises(ValidationError):
        Config(emby={"account": [{"url": "exa mple.com", "username": "alice"}]})


def test_emby_account_name_accepts_explicit_null():
    cfg = Config(emby={"account": [{"url": "https://example.com", "username": "alice", "name": None}]})

    assert cfg.emby.account[0].name is None


def test_use_str_fields_reject_boolean_values():
    with pytest.raises(ValidationError):
        Config(emby={"interval_days": True})

    with pytest.raises(ValidationError):
        Config(
            emby={
                "account": [
                    {
                        "url": "https://example.com",
                        "username": "alice",
                        "interval_days": True,
                    }
                ]
            }
        )


def test_numeric_config_fields_reject_boolean_values():
    with pytest.raises(ValidationError):
        Config(proxy={"hostname": "127.0.0.1", "port": True, "scheme": "socks5"})

    with pytest.raises(ValidationError):
        Config(emby={"concurrency": True})

    with pytest.raises(ValidationError):
        Config(
            emby={
                "account": [
                    {
                        "url": "https://example.com",
                        "username": "alice",
                        "time": True,
                    }
                ]
            }
        )


def test_emby_account_time_rejects_invalid_ranges():
    with pytest.raises(ValidationError):
        Config(emby={"account": [{"url": "https://example.com", "username": "alice", "time": 0}]})

    with pytest.raises(ValidationError):
        Config(
            emby={
                "account": [
                    {
                        "url": "https://example.com",
                        "username": "alice",
                        "time": [600, 300],
                    }
                ]
            }
        )

    with pytest.raises(ValidationError):
        Config(
            emby={
                "account": [
                    {
                        "url": "https://example.com",
                        "username": "alice",
                        "time": [300, 600, 900],
                    }
                ]
            }
        )


def test_emby_account_time_accepts_positive_integer_and_range():
    cfg = Config(
        emby={
            "account": [
                {"url": "https://example.com", "username": "alice", "time": 300},
                {"url": "https://example.net", "username": "bob", "time": [300, 600]},
            ]
        }
    )

    assert cfg.emby.account[0].time == 300
    assert cfg.emby.account[1].time == [300, 600]


def test_notifier_legacy_fixed_bot_fields_are_rejected():
    with pytest.raises(ValidationError):
        Config(notifier={"account": 2})

    with pytest.raises(ValidationError):
        Config(notifier={"immediately": True})

    with pytest.raises(ValidationError):
        Config(notifier={"once": True})

    with pytest.raises(ValidationError):
        Config(notifier={"enabled": True, "method": "telegram"})


def test_notifier_boolean_shorthand_only_toggles_enabled():
    cfg = Config(notifier=True)

    assert cfg.notifier.enabled is True
    assert cfg.notifier.method == "apprise"


def test_notifier_string_and_integer_shorthand_are_no_longer_supported():
    with pytest.raises(ValidationError):
        Config(notifier="telegram-account")

    with pytest.raises(ValidationError):
        Config(notifier=1)


def test_legacy_fixed_bot_top_level_fields_are_no_longer_ignored():
    with pytest.raises(ValidationError):
        Config(bot={"token": "legacy-token"})

    with pytest.raises(ValidationError):
        Config(notify_immediately=True)
