import asyncio

from embykeeperapi.app import lifespan
from embykeeperapi.scheduler_bridge import bridge


def test_lifespan_resets_bridge_when_initialize_fails(tmp_path, monkeypatch):
    async def run_test():
        async def fail_initialize(_basedir):
            bridge.web_accounts = object()
            raise RuntimeError("boom")

        monkeypatch.setenv("EK_BASEDIR", str(tmp_path))
        monkeypatch.setattr(bridge, "initialize", fail_initialize)

        async with lifespan(None):
            assert bridge.web_accounts is None
            assert bridge._initialized is False

    asyncio.run(run_test())
