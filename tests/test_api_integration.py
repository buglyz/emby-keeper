import asyncio
import json
import stat
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from embykeeper.cache import cache
from embykeeper.config import config
from embykeeper.runinfo import LogRecord, RunContext, RunStatus, _running_runs
from embykeeper.schema import Config
from embykeeperapi.app import create_app
from embykeeperapi.auth import get_current_user
from embykeeperapi.crypto import encrypt_token
from embykeeperapi.scheduler_bridge import WebAccountData, bridge


async def _asgi_request(app, method: str, path: str, json_body=None):
    body = b""
    headers = [(b"host", b"testserver")]
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers.append((b"content-type", b"application/json"))

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
        "headers": headers,
        "client": ("testclient", 12345),
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


@pytest.fixture()
def api_app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: "tester"
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        _running_runs.clear()
        config.reset()
        bridge.emby_manager = None
        bridge.web_accounts = None
        bridge._base_emby_accounts = []
        bridge._running_tasks = {}
        bridge._account_status = {}
        bridge._scheduler_task = None
        bridge._initialized = False


def test_run_logs_api_returns_captured_log_records(api_app):
    run = RunContext(
        id="RUNLOG",
        description="Manual watch: alice@example.com",
        status=RunStatus.ERROR,
        log=[
            LogRecord(level="INFO", message="started", time=datetime(2026, 1, 1, 8, 0)),
            LogRecord(level="ERROR", message="failed", time=datetime(2026, 1, 1, 8, 1)),
        ],
    )
    _running_runs[run.id] = run

    response = asyncio.run(_asgi_request(api_app, "GET", "/api/runs/RUNLOG/logs"))

    assert response.status_code == 200
    assert response.json()["run_id"] == "RUNLOG"
    assert [item["message"] for item in response.json()["logs"]] == ["started", "failed"]


def test_health_api_exposes_operational_diagnostics(tmp_path, api_app):
    config.basedir = tmp_path
    config_file = tmp_path / "config.toml"
    config_file.write_text("[emby]\n", encoding="utf-8")
    config._conf_file = config_file
    config.set(Config())
    bridge.web_accounts = WebAccountData(tmp_path)
    bridge.web_accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})
    bridge.emby_manager = SimpleNamespace(_schedulers={})

    run = RunContext(id="RUNHEALTH", status=RunStatus.FAIL, status_info="failed auth")
    _running_runs[run.id] = run

    response = asyncio.run(_asgi_request(api_app, "GET", "/api/status/health"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["account_count"] == 1
    assert data["config_path"] == str(config_file)
    assert data["web_accounts_path"] == str(tmp_path / "web_accounts.json")
    assert data["web_accounts_writable"] is True
    assert data["latest_run_id"] == "RUNHEALTH"
    assert data["latest_run_status"] == "fail"
    assert data["latest_run_status_info"] == "failed auth"


def test_health_api_degrades_when_schedule_info_fails(tmp_path, api_app, monkeypatch):
    config.basedir = tmp_path
    config.set(Config())
    bridge.web_accounts = WebAccountData(tmp_path)
    bridge.emby_manager = SimpleNamespace(_schedulers={})

    def fail_schedule_info():
        raise RuntimeError("scheduler failed")

    monkeypatch.setattr(bridge, "get_schedule_info", fail_schedule_info)

    response = asyncio.run(_asgi_request(api_app, "GET", "/api/status/health"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["scheduler_error"] == "RuntimeError"


def test_health_api_ignores_latest_run_cache_failure(tmp_path, api_app, monkeypatch):
    config.basedir = tmp_path
    config.set(Config())
    bridge.web_accounts = WebAccountData(tmp_path)
    bridge.emby_manager = SimpleNamespace(_schedulers={})

    def fail_recent(limit=1):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(RunContext, "list_recent", fail_recent)

    response = asyncio.run(_asgi_request(api_app, "GET", "/api/status/health"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["latest_run_id"] is None


def test_config_export_and_backup_api_uses_encrypted_account_data(tmp_path, api_app):
    config.basedir = tmp_path
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '\n'.join(
            [
                'mongodb = "mongodb://user:password@example.com/db"',
                "",
                "[emby]",
                'interval_days = "7"',
                "",
                "[notifier]",
                'apprise_uri = "tgram://bot-token/chat-id"',
                "",
                "[[emby.account]]",
                'url = "https://user:url-secret@example.com/hook?token=query-secret"',
                'username = "alice"',
                'password = "plain-password"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    config._conf_file = config_file
    config.set(Config())
    cache._setup_json_cache()
    bridge.web_accounts = WebAccountData(tmp_path)
    bridge.web_accounts.add(
        "alice@example.com",
        {
            "url": "https://example.com",
            "username": "alice",
            "encrypted_token": encrypt_token("token-1", tmp_path),
            "access_token": "plain-access-token",
            "metadata": {
                "api_key": "nested-api-key",
                "callback_url": "https://callback:callback-secret@example.com/hook",
                "label": "safe-label",
            },
        },
    )

    export_response = asyncio.run(_asgi_request(api_app, "GET", "/api/config/export"))

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported["redacted"] is True
    assert "plain-password" not in exported["config_toml"]
    assert "bot-token" not in exported["config_toml"]
    assert "mongodb://user:password@example.com/db" not in exported["config_toml"]
    assert "url-secret" not in exported["config_toml"]
    assert "query-secret" not in exported["config_toml"]
    assert exported["config_toml"].count("***REDACTED***") >= 4
    assert exported["web_accounts"]["alice@example.com"]["encrypted_token"] == "***REDACTED***"
    assert exported["web_accounts"]["alice@example.com"]["access_token"] == "***REDACTED***"
    assert exported["web_accounts"]["alice@example.com"]["metadata"]["api_key"] == "***REDACTED***"
    assert exported["web_accounts"]["alice@example.com"]["metadata"]["callback_url"] == "***REDACTED***"
    assert exported["web_accounts"]["alice@example.com"]["metadata"]["label"] == "safe-label"

    backup_response = asyncio.run(_asgi_request(api_app, "POST", "/api/config/backup"))

    assert backup_response.status_code == 200
    backup = backup_response.json()
    assert backup["status"] == "created"
    assert sorted(backup["files"]) == ["config.toml", "web_accounts.json"]
    backup_dir = Path(backup["backup_dir"])
    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((backup_dir / "config.toml").stat().st_mode) == 0o600
    assert stat.S_IMODE((backup_dir / "web_accounts.json").stat().st_mode) == 0o600

    backups_response = asyncio.run(_asgi_request(api_app, "GET", "/api/config/backups"))

    assert backups_response.status_code == 200
    backups = backups_response.json()
    assert backups[0]["id"] == backup_dir.name
    assert sorted(backups[0]["files"]) == ["config.toml", "web_accounts.json"]

    config_file.write_text("[emby]\ninterval_days = \"99\"\n", encoding="utf-8")
    bridge.web_accounts.add(
        "bob@example.com",
        {"url": "https://example.org", "username": "bob"},
    )

    restore_response = asyncio.run(
        _asgi_request(api_app, "POST", f"/api/config/backups/{backup_dir.name}/restore", {"confirm": True})
    )

    assert restore_response.status_code == 200
    restored = restore_response.json()
    assert restored["status"] == "restored"
    assert sorted(restored["restored_files"]) == ["config.toml", "web_accounts.json"]
    assert restored["safety_backup_dir"]
    assert 'interval_days = "7"' in config_file.read_text(encoding="utf-8")
    assert set(bridge.web_accounts.get_all()) == {"alice@example.com"}

    second_backup_response = asyncio.run(_asgi_request(api_app, "POST", "/api/config/backup"))

    assert second_backup_response.status_code == 200
    assert second_backup_response.json()["backup_dir"] != backup["backup_dir"]


def test_config_backup_cleans_partial_backup_on_copy_failure(tmp_path, api_app, monkeypatch):
    config.basedir = tmp_path
    config_file = tmp_path / "config.toml"
    config_file.write_text("[emby]\n", encoding="utf-8")
    config._conf_file = config_file
    config.set(Config())
    bridge.web_accounts = WebAccountData(tmp_path)
    bridge.web_accounts.add("alice@example.com", {"url": "https://example.com", "username": "alice"})

    original_copy2 = __import__("shutil").copy2

    def fail_web_accounts_copy(source, target):
        if source.name == "web_accounts.json":
            raise OSError("copy failed")
        return original_copy2(source, target)

    monkeypatch.setattr("embykeeperapi.routers.config.shutil.copy2", fail_web_accounts_copy)

    response = asyncio.run(_asgi_request(api_app, "POST", "/api/config/backup"))

    assert response.status_code == 500
    assert not list((tmp_path / "backups").glob("*"))
