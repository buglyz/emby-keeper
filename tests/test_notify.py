import asyncio

from embykeeper.config import config
from embykeeper.schema import Config
import embykeeper.notify as notify


def _cleanup_change_handle():
    if notify.change_handle_notifier:
        notify.change_handle_notifier.__exit__(None, None, None)
        notify.change_handle_notifier = None


def test_start_notifier_registers_change_callback_when_apprise_uri_missing(tmp_path):
    async def run_test():
        config.basedir = tmp_path
        config.set(Config(notifier={"enabled": True, "method": "apprise"}))
        notify.change_handle_notifier = None

        try:
            assert await notify.start_notifier() is None
            assert notify.change_handle_notifier is not None
        finally:
            _cleanup_change_handle()
            await notify._stop_notifier()
            config.reset()

    asyncio.run(run_test())


def test_start_notifier_replaces_existing_streams(tmp_path, monkeypatch):
    class FakeStream:
        def __init__(self, uri):
            self.uri = uri
            self.closed = False
            self.joined = False

        def write(self, message):
            pass

        def close(self):
            self.closed = True

        async def join(self):
            self.joined = True

    async def run_test():
        config.basedir = tmp_path
        config.set(
            Config(
                notifier={
                    "enabled": True,
                    "method": "apprise",
                    "apprise_uri": "dummy://notifier",
                }
            )
        )
        notify.change_handle_notifier = None
        monkeypatch.setattr(notify, "AppriseStream", FakeStream)

        try:
            first = await notify.start_notifier()
            second = await notify.start_notifier()

            assert first[0].closed is True
            assert first[0].joined is True
            assert first[1].closed is True
            assert first[1].joined is True
            assert second[0].closed is False
            assert second[1].closed is False
        finally:
            _cleanup_change_handle()
            await notify._stop_notifier()
            config.reset()

    asyncio.run(run_test())
