import asyncio

import pytest

from embykeeperapi.app import _normalize_root_path, lifespan
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("/", ""),
        ("emby", "/emby"),
        ("/emby/", "/emby"),
        ("//nested/app//", "/nested/app"),
    ],
)
def test_normalize_root_path(value, expected):
    assert _normalize_root_path(value) == expected
