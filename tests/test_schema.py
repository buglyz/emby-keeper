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
