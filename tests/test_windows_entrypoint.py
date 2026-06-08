import importlib


def test_windows_entrypoint_module_imports_on_non_windows():
    module = importlib.import_module("embykeeper.windows")

    assert hasattr(module, "main")
