from pathlib import Path

STATIC_INDEX = Path(__file__).resolve().parents[1] / "embykeeperapi" / "static" / "index.html"
STATIC_VENDOR = STATIC_INDEX.parent / "vendor"


def test_empty_server_description_does_not_break_template_quotes():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert 'description="暂无服务器，点击添加服务器开始"' in html
    assert 'description="暂无服务器，点击"添加服务器"开始"' not in html


def test_edit_form_only_sends_credentials_when_present():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "const authChanged = isEdit.value" in html
    assert "(!isEdit.value || authChanged)" in html
    assert "data.access_token = form.access_token" in html
    assert "data.password = form.password" in html
    assert "access_token: form.auth_method === 'token' ? form.access_token : null" not in html
    assert "password: form.auth_method === 'password' ? form.password : null" not in html


def test_frontend_fallback_checks_actual_naive_ui_global():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "typeof naiveUI === 'undefined'" in html
    assert "typeof naive === 'undefined'" not in html


def test_frontend_vendor_assets_are_packaged_locally():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "window.EK_VENDOR_PATH = `${window.EK_BASE_PATH}/static/vendor/`" in html
    assert "window.EK_LOAD_VENDOR = function (filename)" in html
    for filename in (
        "vue.global.prod.js",
        "naive-ui.prod.js",
        "vue-router.global.prod.js",
    ):
        path = STATIC_VENDOR / filename
        assert f"window.EK_LOAD_VENDOR('{filename}')" in html
        assert f'src="/static/vendor/{filename}"' not in html
        assert path.is_file()
        assert path.stat().st_size > 10_000


def test_frontend_api_base_respects_reverse_proxy_prefix():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "window.EK_BASE_PATH = basePath === '/' ? '' : basePath" in html
    assert "const routeSuffix =" in html
    assert "function getApiBasePath()" in html
    assert "if (typeof window.EK_BASE_PATH === 'string') return window.EK_BASE_PATH" in html
    assert "const marker = '/static/vendor/'" in html
    assert "localVendorScript.src.indexOf(marker)" in html
    assert "if (basePath) return basePath" in html
    assert "const API_BASE_PATH = getApiBasePath()" in html
    assert "baseUrl: `${window.location.origin}${API_BASE_PATH}`" in html
    assert "baseUrl: window.location.origin" not in html


def test_frontend_formats_structured_api_errors():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "_formatErrorDetail(detail)" in html
    assert "Array.isArray(detail)" in html
    assert "item.loc.join('.')" in html
    assert "detail.msg || detail.message || JSON.stringify(detail)" in html
    assert "this._formatErrorDetail(data && data.detail) || 'Request failed'" in html
