import importlib

import pytest


def test_windows_entrypoint_module_imports_on_non_windows():
    module = importlib.import_module("embykeeper.windows")

    assert hasattr(module, "main")


def test_windows_generate_config_opens_notepad_without_shell(tmp_path, monkeypatch):
    module = importlib.import_module("embykeeper.windows")
    calls = []

    class DummyProcess:
        def wait(self):
            calls.append(("wait",))

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return DummyProcess()

    module.config.basedir = tmp_path
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(module, "getch", lambda: b"")
    monkeypatch.setattr(module, "Popen", fake_popen)
    monkeypatch.setattr(module.var.console, "print", lambda *_args, **_kwargs: None)

    try:
        module.generate_config()
    finally:
        module.config.reset()

    assert calls[0][0] == [r"C:\Windows/System32/notepad.exe", str(tmp_path / "config.toml")]
    assert calls[0][1] == {}
    assert calls[1] == ("wait",)


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
    monkeypatch.setattr(module.var.console, "clear", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.var.console, "rule", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "cli", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(module.sys, "argv", argv)

    module.main()

    assert captured["args"] == expected
