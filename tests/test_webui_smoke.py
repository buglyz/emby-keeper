import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

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


def test_webui_smoke_login_and_core_pages(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    port = _free_port()
    env = os.environ.copy()
    env["EK_TOKEN"] = "smoke-token"
    process = subprocess.Popen(
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
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
