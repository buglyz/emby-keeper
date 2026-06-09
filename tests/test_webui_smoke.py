import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from embykeeperapi.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_health(url, timeout=10):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as e:
            last_error = e
            time.sleep(0.2)
    raise AssertionError(f"WebUI health check failed: {last_error}")


def _json_request(url, method="GET", json_body=None, headers=None):
    body = None
    request_headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(request, timeout=3) as response:
        payload = response.read()
        return SimpleNamespace(
            status_code=response.status,
            body=payload,
            json=lambda: json.loads(payload.decode("utf-8")) if payload else None,
        )


def _start_webui_process(tmp_path, port, extra_env=None):
    env = os.environ.copy()
    env["EK_TOKEN"] = "smoke-token"
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "embykeeperapi",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--basedir",
            str(tmp_path),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _stop_process(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def _asgi_request(app, method: str, path: str, json_body=None, headers=None):
    body = b""
    request_headers = [(b"host", b"testserver")]
    if headers:
        request_headers.extend(
            (key.lower().encode("ascii"), value.encode("utf-8")) for key, value in headers.items()
        )
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers.append((b"content-type", b"application/json"))

    messages = []
    sent = False
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": request_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status_code = next(message["status"] for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )

    def parse_json():
        return json.loads(response_body.decode("utf-8")) if response_body else None

    return SimpleNamespace(status_code=status_code, body=response_body, json=parse_json)


def test_webui_serves_entry_assets_and_accepts_mock_token(monkeypatch):
    monkeypatch.setenv("EK_TOKEN", "smoke-token")
    monkeypatch.delenv("EK_WEBPASS", raising=False)
    app = create_app()

    async def run_test():
        index_response = await _asgi_request(app, "GET", "/")
        assert index_response.status_code == 200
        index_html = index_response.body.decode("utf-8")
        assert "<title>Emby Keeper</title>" in index_html
        assert '<div id="app"></div>' in index_html
        assert "window.EK_LOAD_STATIC('app-core.js')" in index_html

        asset_response = await _asgi_request(app, "GET", "/static/app-core.js")
        assert asset_response.status_code == 200
        assert b"tokenExchange(token)" in asset_response.body
        assert b"verifyToken()" in asset_response.body

        methods_response = await _asgi_request(app, "GET", "/api/auth/methods")
        assert methods_response.status_code == 200
        assert methods_response.json() == {"token": True, "password": False}

        login_response = await _asgi_request(
            app,
            "POST",
            "/api/auth/token-exchange",
            {"token": "smoke-token"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        me_response = await _asgi_request(
            app,
            "GET",
            "/api/auth/me",
            headers={"authorization": f"Bearer {access_token}"},
        )
        assert me_response.status_code == 200
        assert me_response.json() == {"user": "admin", "valid": True}

    asyncio.run(run_test())


def test_webui_process_serves_entry_and_authenticates_with_mock_token(tmp_path):
    port = _free_port()
    process = _start_webui_process(tmp_path, port)
    try:
        base_url = f"http://127.0.0.1:{port}"
        _wait_for_health(f"{base_url}/healthz")

        index_response = _json_request(f"{base_url}/")
        assert index_response.status_code == 200
        index_html = index_response.body.decode("utf-8")
        assert "<title>Emby Keeper</title>" in index_html
        assert "window.EK_LOAD_STATIC('app-core.js')" in index_html

        asset_response = _json_request(f"{base_url}/static/app-core.js")
        assert asset_response.status_code == 200
        assert b"tokenExchange(token)" in asset_response.body

        methods_response = _json_request(f"{base_url}/api/auth/methods")
        assert methods_response.status_code == 200
        assert methods_response.json() == {"token": True, "password": False}

        login_response = _json_request(
            f"{base_url}/api/auth/token-exchange",
            method="POST",
            json_body={"token": "smoke-token"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        me_response = _json_request(
            f"{base_url}/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_response.status_code == 200
        assert me_response.json() == {"user": "admin", "valid": True}
    finally:
        _stop_process(process)


def test_webui_process_supports_base_prefix_with_mock_token(tmp_path):
    port = _free_port()
    process = _start_webui_process(tmp_path, port, {"EK_BASE_PREFIX": "/emby"})
    try:
        base_url = f"http://127.0.0.1:{port}/emby"
        _wait_for_health(f"http://127.0.0.1:{port}/healthz")

        index_response = _json_request(f"{base_url}/")
        assert index_response.status_code == 200
        index_html = index_response.body.decode("utf-8")
        assert "<title>Emby Keeper</title>" in index_html
        assert "window.EK_LOAD_STATIC('app-core.js')" in index_html

        asset_response = _json_request(f"{base_url}/static/app-core.js")
        assert asset_response.status_code == 200
        assert b"const API_BASE_PATH = getApiBasePath()" in asset_response.body

        methods_response = _json_request(f"{base_url}/api/auth/methods")
        assert methods_response.status_code == 200
        assert methods_response.json() == {"token": True, "password": False}

        login_response = _json_request(
            f"{base_url}/api/auth/token-exchange",
            method="POST",
            json_body={"token": "smoke-token"},
        )
        assert login_response.status_code == 200
    finally:
        _stop_process(process)


def test_webui_smoke_login_and_core_pages(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    port = _free_port()
    process = _start_webui_process(tmp_path, port)
    try:
        base_url = f"http://127.0.0.1:{port}"
        _wait_for_health(f"{base_url}/healthz")
        try:
            with sync_api.sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(base_url, wait_until="networkidle")
                page.get_by_placeholder("请输入预共享 Token").fill("smoke-token")
                page.get_by_role("button", name="登录").click()
                page.get_by_text("服务器列表").wait_for(timeout=5000)
                page.goto(f"{base_url}/#/config", wait_until="networkidle")
                page.get_by_text("自动化配置").wait_for(timeout=5000)
                page.goto(f"{base_url}/#/registrar", wait_until="networkidle")
                page.get_by_text("一键抢注").wait_for(timeout=5000)
                browser.close()
        except Exception as e:
            pytest.skip(f"Playwright browser is not available: {e}")
    finally:
        _stop_process(process)
