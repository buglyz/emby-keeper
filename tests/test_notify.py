import asyncio

from embykeeper.config import config
from embykeeper.schema import Config
import embykeeper.notify as notify


def test_start_notifier_registers_change_callback_when_apprise_uri_missing(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(notifier={"enabled": True, "method": "apprise"}))
        notify.change_handle_notifier = None

        try:
            assert await notify.start_notifier() is None
            assert notify.change_handle_notifier is not None
        finally:
            if notify.change_handle_notifier:
                notify.change_handle_notifier.__exit__(None, None, None)
                notify.change_handle_notifier = None
            await notify._stop_notifier()
            config.reset()

    asyncio.run(run_test())
