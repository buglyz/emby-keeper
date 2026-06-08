import asyncio

import pytest
from starlette.requests import Request
from starlette.responses import Response

from embykeeperapi.app import ProxyFixMiddleware, _normalize_root_path, lifespan
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


def test_proxy_fix_middleware_uses_first_non_empty_forwarded_for():
    async def run_test():
        middleware = ProxyFixMiddleware(app=lambda *_args: None)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/client",
            "headers": [(b"x-forwarded-for", b" , 203.0.113.10, 10.0.0.2")],
            "client": ("original", 12345),
            "scheme": "http",
        }
        request = Request(scope)
        seen = {}

        async def call_next(next_request):
            seen["client"] = next_request.client.host
            return Response("ok")

        await middleware.dispatch(request, call_next)

        assert seen["client"] == "203.0.113.10"

    asyncio.run(run_test())


def test_proxy_fix_middleware_uses_real_ip_when_forwarded_for_missing():
    async def run_test():
        middleware = ProxyFixMiddleware(app=lambda *_args: None)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/client",
            "headers": [(b"x-real-ip", b"198.51.100.8")],
            "client": ("original", 12345),
            "scheme": "http",
        }
        request = Request(scope)
        seen = {}

        async def call_next(next_request):
            seen["client"] = next_request.client.host
            return Response("ok")

        await middleware.dispatch(request, call_next)

        assert seen["client"] == "198.51.100.8"

    asyncio.run(run_test())


def test_proxy_fix_middleware_uses_first_forwarded_proto_value():
    async def run_test():
        middleware = ProxyFixMiddleware(app=lambda *_args: None)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/client",
            "headers": [(b"x-forwarded-proto", b"HTTPS, http")],
            "client": ("original", 12345),
            "server": ("example.com", 80),
            "scheme": "http",
        }
        request = Request(scope)
        seen = {}

        async def call_next(next_request):
            seen["scheme"] = next_request.url.scheme
            return Response("ok")

        await middleware.dispatch(request, call_next)

        assert seen["scheme"] == "https"

    asyncio.run(run_test())
