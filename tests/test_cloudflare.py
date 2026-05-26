import sys

import pytest

from embykeeper.cloudflare import get_cf_clearance


@pytest.mark.parametrize("module_name", ["embykeeper.telegram.session", "embykeeper.telegram.link"])
def test_cloudflare_solver_does_not_import_telegram(module_name):
    sys.modules.pop(module_name, None)


def test_cloudflare_solver_returns_empty_credentials_without_telegram():
    result = pytest.importorskip("asyncio").run(get_cf_clearance("https://example.com"))

    assert result == (None, None)
    assert "embykeeper.telegram.session" not in sys.modules
    assert "embykeeper.telegram.link" not in sys.modules
