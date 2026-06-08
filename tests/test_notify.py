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


def test_start_notifier_returns_none_when_apprise_stream_is_not_ready(tmp_path, monkeypatch):
    class FakeStream:
        ready = False

        def __init__(self, uri):
            self.uri = uri
            self.closed = False
            self.joined = False
            streams.append(self)

        def write(self, message):
            pass

        def close(self):
            self.closed = True

        async def join(self):
            self.joined = True

    streams = []

    async def run_test():
        config.basedir = tmp_path
        config.set(
            Config(
                notifier={
                    "enabled": True,
                    "method": "apprise",
                    "apprise_uri": "invalid://notifier",
                }
            )
        )
        notify.change_handle_notifier = None
        monkeypatch.setattr(notify, "AppriseStream", FakeStream)

        try:
            assert await notify.start_notifier() is None
            assert notify.stream_log is None
            assert notify.stream_msg is None
            assert len(streams) == 2
            assert all(stream.closed for stream in streams)
            assert all(stream.joined for stream in streams)
            assert notify.change_handle_notifier is not None
        finally:
            _cleanup_change_handle()
            await notify._stop_notifier()
            config.reset()

    asyncio.run(run_test())


def test_stop_notifier_ignores_missing_logger_handlers():
    async def run_test():
        notify.handler_log_id = 999999
        notify.handler_msg_id = 999998

        await notify._stop_notifier()

        assert notify.handler_log_id is None
        assert notify.handler_msg_id is None

    asyncio.run(run_test())


def test_stop_notifier_continues_after_stream_close_failure():
    class FailingStream:
        def close(self):
            raise OSError("close failed")

        async def join(self):
            raise AssertionError("join should not run after close fails")

    class FakeStream:
        def __init__(self):
            self.closed = False
            self.joined = False

        def close(self):
            self.closed = True

        async def join(self):
            self.joined = True

    async def run_test():
        message_stream = FakeStream()
        notify.stream_log = FailingStream()
        notify.stream_msg = message_stream

        await notify._stop_notifier()

        assert notify.stream_log is None
        assert notify.stream_msg is None
        assert message_stream.closed is True
        assert message_stream.joined is True

    asyncio.run(run_test())


def test_config_change_refresh_task_ignores_failures(monkeypatch):
    async def run_test():
        created_tasks = []
        original_create_task = notify.asyncio.create_task

        def capture_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        async def fail_stop_notifier():
            raise RuntimeError("stop failed")

        monkeypatch.setattr(notify.asyncio, "create_task", capture_task)
        monkeypatch.setattr(notify, "_stop_notifier", fail_stop_notifier)

        notify._handle_config_change()

        assert created_tasks
        await created_tasks[0]

    asyncio.run(run_test())
