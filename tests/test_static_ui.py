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
    assert "data.access_token = normalized.access_token" in html
    assert "data.password = normalized.password" in html
    assert "access_token: form.auth_method === 'token' ? form.access_token : null" not in html
    assert "password: form.auth_method === 'password' ? form.password : null" not in html


def test_server_form_trims_text_payload_before_save():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "function trimText(value)" in html
    assert "function optionalText(value)" in html
    assert "const normalized = {" in html
    for snippet in (
        "url: trimText(form.url)",
        "username: trimText(form.username)",
        "name: optionalText(form.name)",
        "access_token: trimText(form.access_token)",
        "password: trimText(form.password)",
        "play_id: optionalText(form.play_id)",
        "interval_days: optionalText(form.interval_days)",
        "time_range: optionalText(form.time_range)",
        "useragent: optionalText(form.useragent)",
        "client: optionalText(form.client)",
        "client_version: optionalText(form.client_version)",
        "device: optionalText(form.device)",
        "device_id: optionalText(form.device_id)",
    ):
        assert snippet in html
    assert "if (!normalized.url || !normalized.username)" in html
    assert "!normalized.access_token" in html
    assert "!normalized.password" in html
    assert "url: normalized.url" in html
    assert "username: normalized.username" in html
    assert "name: normalized.name" in html
    assert "data.access_token = normalized.access_token" in html
    assert "data.password = normalized.password" in html


def test_config_form_trims_text_payload_before_save():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "emby_time_range: optionalText(editConfig.emby_time_range)" in html
    assert "emby_interval_days: optionalText(editConfig.emby_interval_days)" in html
    assert "hostname: optionalText(editConfig.proxy_hostname)" in html
    assert "emby_time_range: editConfig.emby_time_range || null" not in html
    assert "emby_interval_days: editConfig.emby_interval_days || null" not in html
    assert "hostname: editConfig.proxy_hostname || null" not in html


def test_login_form_trims_credentials_before_exchange():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "API.tokenExchange(tokenInput.value.trim())" in html
    assert "API.passwordLogin(passwordInput.value.trim())" in html
    assert "API.tokenExchange(tokenInput.value)" not in html
    assert "API.passwordLogin(passwordInput.value)" not in html


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


def test_frontend_only_sends_authorization_header_with_token():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "if (token) headers.Authorization = `Bearer ${token}`" in html
    assert "return { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }" not in html
