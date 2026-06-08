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


def test_server_form_rejects_invalid_watch_time_before_save():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert ':precision="0" placeholder="最小秒数"' in html
    assert ':precision="0" placeholder="最大秒数"' in html
    assert "function isValidWatchTime(value)" in html
    assert "return Number.isInteger(value) && value >= 60" in html
    assert "if (!isValidWatchTime(form.time_min) || !isValidWatchTime(form.time_max))" in html
    assert "播放时长必须是至少 60 秒的整数" in html
    assert (
        "const watchTime = form.time_min === form.time_max ? form.time_min : [form.time_min, form.time_max]"
        in html
    )
    assert "time: watchTime" in html
    assert (
        "time: form.time_min === form.time_max ? form.time_min : [form.time_min, form.time_max]" not in html
    )


def test_config_form_trims_text_payload_before_save():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "function addScheduleText(data, key, value, label)" in html
    assert "throw new Error(`${label}不能为空`)" in html
    assert "if (normalized !== null) data[key] = normalized" in html
    assert "addScheduleText(data, 'emby_time_range', editConfig.emby_time_range, 'Emby 时间范围')" in html
    assert (
        "addScheduleText(data, 'emby_interval_days', editConfig.emby_interval_days, 'Emby 间隔天数')" in html
    )
    assert "hostname: optionalText(editConfig.proxy_hostname)" in html
    assert "emby_time_range: optionalText(editConfig.emby_time_range)" not in html
    assert "emby_interval_days: optionalText(editConfig.emby_interval_days)" not in html
    assert "emby_time_range: editConfig.emby_time_range || null" not in html
    assert "emby_interval_days: editConfig.emby_interval_days || null" not in html
    assert "hostname: editConfig.proxy_hostname || null" not in html


def test_config_form_validates_optional_integer_fields_before_save():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert '<n-input-number v-model:value="editConfig.emby_concurrency" :min="1" :precision="0"' in html
    assert '<n-input-number v-model:value="editConfig.proxy_port" :min="1" :precision="0"' in html
    assert "function optionalPositiveInteger(value, label)" in html
    assert "if (value == null) return null" in html
    assert "throw new Error(`${label}必须是正整数`)" in html
    assert "emby_concurrency: optionalPositiveInteger(editConfig.emby_concurrency, 'Emby 并发数')" in html
    assert "port: optionalPositiveInteger(editConfig.proxy_port, '代理端口')" in html
    assert "emby_concurrency: editConfig.emby_concurrency || null" not in html
    assert "port: editConfig.proxy_port || null" not in html


def test_config_page_refreshes_after_save():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert (
        "await API.updateConfig(data);\n"
        "            message.success('配置已保存');\n"
        "            await loadData();"
    ) in html


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
    assert "login|schedule|runs|config" in html
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
    assert "if (body !== null && body !== undefined) opts.body = JSON.stringify(body)" in html
    assert "post(path, body = null)" in html
    assert "return { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }" not in html


def test_frontend_exposes_run_history_and_cancel_actions():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "getRuns(limit = 50)" in html
    assert "getRunLogs(id)" in html
    assert "const RunHistoryPage = {" in html
    assert "logModalVisible" in html
    assert "openRunLogs(row.run_id)" in html
    assert "API.getRunLogs(runId)" in html
    assert "{ path: 'runs', component: RunHistoryPage }" in html
    assert "cancelWatch(id)" in html
    assert "cancelSchedule(id)" in html
    assert "取消任务" in html


def test_schedule_page_refreshes_after_manual_run():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "message.success(res && res.message ? res.message : '任务已启动')" in html
    assert "await loadData();" in html


def test_server_actions_refresh_after_runtime_operations():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "async function runUiAction({" in html
    assert "function responseMessage(res, fallback)" in html
    assert "setTimeout(async () =>" in html
    assert "hasRunning ? 5000 : 30000" in html
    assert "clearTimeout(pollTimer)" in html
    assert "responseMessage(res, '保活任务已启动')" in html
    assert "responseMessage(res, '全部保活任务已启动')" in html
    assert "message.success(res && res.message ? res.message : '保活已启动')" in html
    assert "message.success(res.message || '登录测试已完成')" in html
    assert ':loading="watchAllLoading"' in html
    assert "const watchAllLoading = ref(false)" in html
    assert "watchAllLoading.value = value" in html
    assert "await API.toggleServer(route.params.id, enabled)" in html
    assert "await loadData();" in html


def test_config_page_exposes_backup_and_health_diagnostics():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "exportConfig() { return this.get('/api/config/export'); }" in html
    assert "backupConfig() { return this.post('/api/config/backup'); }" in html
    assert "function downloadJson(filename, data)" in html
    assert "handleExportConfig" in html
    assert "handleCreateBackup" in html
    assert "下载备份" in html
    assert "创建本地备份" in html
    assert "scheduler_task_running" in html
    assert "web_accounts_writable" in html
    assert "notifier_ready" in html
    assert "latest_run_id" in html
    assert "config_path" in html
    assert "web_accounts_path" in html


def test_frontend_exposes_schedule_preview_and_health_status():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "previewSchedule(data)" in html
    assert "调度预览" in html
    assert "previewResult.value = null" in html
    assert "getHealth()" in html
    assert "运行健康" in html


def test_frontend_exposes_telegram_notifier_controls_without_echoing_token():
    html = STATIC_INDEX.read_text(encoding="utf-8")

    assert "getNotifier()" in html
    assert "updateNotifier(data)" in html
    assert "testNotifier(data)" in html
    assert "Telegram" in html
    assert "telegram_bot_token" in html
    assert 'show-password-on="click"' in html
    assert "留空则保留现有 Token" in html
    assert "修改 Telegram Chat ID 时必须重新输入 Bot Token" in html
