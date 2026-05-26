import asyncio

import tomli as tomllib

from embykeeper.config import config
from embykeeper.schema import Config
from embykeeperapi.models import GlobalConfigUpdate, ProxyConfigUpdate
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

    asyncio.run(run_test())
    config.reset()
