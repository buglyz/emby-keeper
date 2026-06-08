import asyncio
import importlib
import os
from pathlib import Path
import sys
import types

import typer
from typer.testing import CliRunner

import pytest

import embykeeper
import embykeeper.cli as cli_module
from embykeeper.cli import app
from embykeeper.config import ConfigManager, config
from embykeeper.schema import Config

runner = CliRunner()


@pytest.fixture()
def in_temp_dir(tmp_path: Path):
    current = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(current)


def test_version():
    result = runner.invoke(app, ["--version"])
    assert embykeeper.__version__ in result.stdout
    assert result.exit_code == 0


def test_main_module_import_does_not_start_cli():
    module = importlib.import_module("embykeeper.__main__")

    assert module.app is app


def test_create_config(in_temp_dir: Path):
    result = runner.invoke(app, ["--example-config"])
    assert "这是一个配置文件范例" in result.stdout
    assert "[emby]" in result.stdout
    assert "[[emby.account]]" in result.stdout
    assert 'method = "apprise"' in result.stdout
    for removed in (
        "Telegram",
        "telegram",
        "checkiner",
        "registrar",
        "Subsonic",
        "subsonic",
        "签到",
    ):
        assert removed not in result.stdout
    assert result.exit_code == 0


@pytest.mark.parametrize("mongodb", [None, "mongodb://example.invalid"])
def test_cache_self_check_failure_exits_nonzero(tmp_path, monkeypatch, mongodb):
    async def fake_reload_conf(self, conf_file=None):
        self.set(Config(mongodb=mongodb))
        return True

    class BrokenCache:
        def set(self, *_args, **_kwargs):
            raise OSError("cache unavailable")

    monkeypatch.setattr(ConfigManager, "reload_conf", fake_reload_conf)
    monkeypatch.setattr("embykeeper.log.initialize", lambda *args, **kwargs: None)
    monkeypatch.setattr("embykeeper.log.apply_logging_adapter", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_module, "show_exception", lambda *args, **kwargs: None)
    cache_module = types.ModuleType("embykeeper.cache")
    cache_module.cache = BrokenCache()
    monkeypatch.setitem(sys.modules, "embykeeper.cache", cache_module)

    try:
        with pytest.raises(typer.Exit) as exc_info:
            asyncio.run(
                cli_module.main(
                    config_file=None,
                    help=False,
                    emby=True,
                    version=False,
                    example_config=False,
                    instant=False,
                    once=True,
                    verbosity=0,
                    debug_cron=False,
                    debug_notify=False,
                    simple_log=True,
                    disable_color=True,
                    play=None,
                    windows=False,
                    basedir=tmp_path,
                    noexit=False,
                    clean=False,
                )
            )
    finally:
        config.reset()

    assert exc_info.value.exit_code == 1


def test_run_exit_handlers_continues_after_handler_call_error():
    calls = []

    def fail_before_await():
        calls.append("fail")
        raise RuntimeError("boom")

    async def succeed():
        calls.append("succeed")

    asyncio.run(cli_module._run_exit_handlers([fail_before_await, succeed]))

    assert calls == ["fail", "succeed"]


def test_run_exit_handlers_ignores_non_awaitable_return_values():
    calls = []

    def sync_handler():
        calls.append("sync")

    asyncio.run(cli_module._run_exit_handlers([sync_handler]))

    assert calls == ["sync"]
