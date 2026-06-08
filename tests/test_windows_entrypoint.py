import importlib

import pytest


def test_windows_entrypoint_module_imports_on_non_windows():
    module = importlib.import_module("embykeeper.windows")

    assert hasattr(module, "main")


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["embykeeper-windows", "--once"], ["--once", "-W", "-i"]),
        (["embykeeper-windows", "-I", "--once"], ["-I", "--once", "-W"]),
        (["embykeeper-windows", "--no-instant", "--once"], ["--no-instant", "--once", "-W"]),
    ],
)
def test_windows_entrypoint_preserves_no_instant(monkeypatch, argv, expected):
    module = importlib.import_module("embykeeper.windows")
    captured = {}

    monkeypatch.setattr(module, "generate_config", lambda: None)
    monkeypatch.setattr(module.os, "system", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.var.console, "rule", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "cli", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(module.sys, "argv", argv)

    module.main()

    assert captured["args"] == expected
