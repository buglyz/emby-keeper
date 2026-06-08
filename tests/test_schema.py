import pytest
from pydantic import ValidationError

from embykeeper.schema import Config


def test_legacy_non_emby_config_sections_are_ignored():
    cfg = Config(
        emby=[{"url": "https://example.com", "username": "alice", "ua": "Fileball/1.3.30"}],
        telegram={"account": [{"phone": "+8613800000000"}]},
        checkiner={"timeout": 120},
        subsonic={"account": [{"url": "https://music.example.com", "username": "bob", "password": "secret"}]},
        site={"checkiner": ["all"]},
        listentime="8:00AM",
    )

    assert cfg.emby.account[0].useragent == "Fileball/1.3.30"
    assert not hasattr(cfg, "telegram")
    assert not hasattr(cfg, "checkiner")
    assert not hasattr(cfg, "subsonic")


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


def test_legacy_global_alias_does_not_mutate_source_dict():
    raw = {"emby": {"account": []}, "interval": "7"}

    cfg = Config(**raw)

    assert cfg.emby.interval_days == "7"
    assert raw == {"emby": {"account": []}, "interval": "7"}


def test_legacy_global_alias_replaces_none_emby_section():
    cfg = Config(emby=None, interval="7")

    assert cfg.emby.interval_days == "7"


def test_use_str_fields_accept_integer_values():
    cfg = Config(emby={"interval_days": 7})

    assert cfg.emby.interval_days == "7"


def test_use_str_fields_reject_boolean_values():
    with pytest.raises(ValidationError):
        Config(emby={"interval_days": True})


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
