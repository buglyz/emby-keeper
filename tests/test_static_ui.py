from pathlib import Path

STATIC_INDEX = Path(__file__).resolve().parents[1] / "embykeeperapi" / "static" / "index.html"
STATIC_CORE = STATIC_INDEX.parent / "app-core.js"
STATIC_STYLE = STATIC_INDEX.parent / "app-style.css"
STATIC_VENDOR = STATIC_INDEX.parent / "vendor"


def read_static_source():
    return "\n".join(
        [
            STATIC_INDEX.read_text(encoding="utf-8"),
            STATIC_CORE.read_text(encoding="utf-8"),
            STATIC_STYLE.read_text(encoding="utf-8"),
        ]
    )


def test_empty_server_description_does_not_break_template_quotes():
    html = read_static_source()

    assert 'description="暂无服务器，点击添加服务器开始"' in html
    assert 'description="暂无服务器，点击"添加服务器"开始"' not in html


def test_edit_form_only_sends_credentials_when_present():
    html = read_static_source()

    assert "const authChanged = isEdit.value" in html
    assert "(!isEdit.value || authChanged)" in html
    assert "data.access_token = normalized.access_token" in html
    assert "data.password = normalized.password" in html
    assert "access_token: form.auth_method === 'token' ? form.access_token : null" not in html
    assert "password: form.auth_method === 'password' ? form.password : null" not in html


def test_server_form_trims_text_payload_before_save():
    html = read_static_source()

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
    html = read_static_source()

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
    html = read_static_source()

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
    html = read_static_source()

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
    html = read_static_source()

    assert (
        "await API.updateConfig(data);\n"
        "            message.success('配置已保存');\n"
        "            await loadData();"
    ) in html


def test_login_form_trims_credentials_before_exchange():
    html = read_static_source()

    assert "API.tokenExchange(tokenInput.value.trim())" in html
    assert "API.passwordLogin(passwordInput.value.trim())" in html
    assert "API.tokenExchange(tokenInput.value)" not in html
    assert "API.passwordLogin(passwordInput.value)" not in html


def test_runtime_template_placeholders_avoid_raw_angle_brackets():
    html = read_static_source()

    assert 'placeholder="保活间隔天数 (如 7,12 或 7)"' in html
    assert 'placeholder="保活时间范围 (如 11:00AM,11:00PM)"' in html
    assert 'placeholder="间隔天数，如 7,12"' in html
    assert 'placeholder="时间范围，如 11:00AM,11:00PM"' in html
    assert 'placeholder="保活间隔天数 (如 <7,12> 或 7)"' not in html
    assert 'placeholder="保活时间范围 (如 <11:00AM,11:00PM>)"' not in html
    assert 'placeholder="间隔天数，如 <7,12>"' not in html
    assert 'placeholder="时间范围，如 <11:00AM,11:00PM>"' not in html


def test_runtime_template_v_else_empty_states_are_adjacent():
    html = read_static_source()

    assert '<div v-if="runs.length" class="data-table-wrap">' in html
    assert '<n-data-table v-if="runs.length"' not in html
    assert '<div v-if="schedules.length" class="data-table-wrap">' in html
    assert '<n-data-table v-if="schedules.length"' not in html
    assert '<div v-if="backups.length" class="data-table-wrap">' in html
    assert '<n-data-table v-if="backups.length"' not in html


def test_login_page_only_shows_available_auth_methods():
    html = read_static_source()

    assert 'v-if="hasToken" name="token"' in html
    assert 'v-if="hasPassword" name="password"' in html
    assert "authMethodsLoaded && !hasAuthMethod" in html
    assert "当前服务未配置 WebUI 登录方式" in html
    assert '@keyup.enter="handleTokenLogin"' in html
    assert '@keyup.enter="handlePasswordLogin"' in html
    assert ':disabled="!tokenInput.trim()"' in html
    assert ':disabled="!passwordInput.trim()"' in html


def test_api_unauthorized_preserves_response_detail_for_auth_routes():
    core = STATIC_CORE.read_text(encoding="utf-8")

    assert "if (!path.startsWith('/api/auth/'))" in core
    assert "this._formatErrorDetail(data && data.detail) || '登录状态已失效'" in core
    assert "throw new Error('Unauthorized')" not in core


def test_notifier_payload_preserves_existing_telegram_target_without_token():
    html = read_static_source()

    assert "const existingTelegram = existing.configured && existing.method === 'telegram'" in html
    assert "if (botToken) {" in html
    assert "配置 Telegram 通知时必须输入 Bot Token 和 Chat ID" in html
    assert "修改 Telegram Chat ID 时必须重新输入 Bot Token" in html


def test_frontend_fallback_checks_actual_naive_ui_global():
    html = read_static_source()

    assert "typeof naiveUI === 'undefined'" in html
    assert "window.EK_SYNC_NAIVE()" in html
    assert "typeof naive === 'undefined'" not in html


def test_frontend_vendor_assets_are_packaged_locally():
    html = read_static_source()

    assert "window.EK_STATIC_PATH = `${window.EK_BASE_PATH}/static/`" in html
    assert "window.EK_LOAD_STATIC = function (filename)" in html
    assert "window.EK_LOAD_STATIC('app-core.js')" in html
    assert "app-style.css" in html
    assert STATIC_STYLE.is_file()
    assert STATIC_STYLE.stat().st_size > 1000
    assert STATIC_CORE.is_file()
    assert STATIC_CORE.stat().st_size > 1000
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


def test_frontend_remote_cdn_uses_jsdelivr_fallback():
    html = read_static_source()

    assert "window.EK_PRIMARY_CDN" not in html
    assert "window.EK_FALLBACK_CDN" not in html
    assert "window.EK_CDN_ORIGIN = 'https://cdn.jsdelivr.net'" in html
    assert "window.EK_LOAD_CDN = function (origin, path)" in html
    assert "window.EK_LOAD_CDN(window.EK_CDN_ORIGIN,'/npm/vue@3/dist/vue.global.prod.js')" in html
    assert "window.EK_LOAD_CDN(window.EK_CDN_ORIGIN,'/npm/naive-ui@2/dist/index.prod.js')" in html
    assert (
        "window.EK_LOAD_CDN(window.EK_CDN_ORIGIN,'/npm/vue-router@4/dist/vue-router.global.prod.js')"
        in html
    )


def test_frontend_api_base_respects_reverse_proxy_prefix():
    html = read_static_source()

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
    html = read_static_source()

    assert "_formatErrorDetail(detail)" in html
    assert "Array.isArray(detail)" in html
    assert "item.loc.join('.')" in html
    assert "detail.msg || detail.message || JSON.stringify(detail)" in html
    assert "this._formatErrorDetail(data && data.detail) || 'Request failed'" in html


def test_frontend_only_sends_authorization_header_with_token():
    html = read_static_source()

    assert "if (token) headers.Authorization = `Bearer ${token}`" in html
    assert "if (body !== null && body !== undefined) opts.body = JSON.stringify(body)" in html
    assert "post(path, body = null)" in html
    assert "return { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }" not in html


def test_frontend_exposes_run_history_and_cancel_actions():
    html = read_static_source()

    assert "getRuns({ limit = 50, offset = 0, status = null } = {})" in html
    assert "getRunLogs(id)" in html
    assert "cleanupRuns(days)" in html
    assert "const RunHistoryPage = {" in html
    assert "logModalVisible" in html
    assert "openRunLogs(row.run_id)" in html
    assert "API.getRunLogs(runId)" in html
    assert "statusFilter" in html
    assert "loadMore" in html
    assert "handleCleanup" in html
    assert "{ path: 'runs', component: RunHistoryPage }" in html
    assert "cancelWatch(id)" in html
    assert "cancelSchedule(id)" in html
    assert "取消任务" in html


def test_schedule_page_refreshes_after_manual_run():
    html = read_static_source()

    assert "message.success(res && res.message ? res.message : '任务已启动')" in html
    assert "await loadData();" in html


def test_server_actions_refresh_after_runtime_operations():
    html = read_static_source()

    assert "async function runUiAction({" in html
    assert "function responseMessage(res, fallback)" in html
    assert "setTimeout(async () =>" in html
    assert "hasRunning ? 5000 : 30000" in html
    assert "clearTimeout(pollTimer)" in html
    assert "let disposed = false" in html
    assert "if (disposed) return" in html
    assert "if (!disposed) refreshPoll()" in html
    assert "disposed = true" in html
    assert "responseMessage(res, '保活任务已启动')" in html
    assert "responseMessage(res, '全部保活任务已启动')" in html
    assert "message.success(res && res.message ? res.message : '保活已启动')" in html
    assert "message.success(res.message || '登录测试已完成')" in html
    assert ':loading="watchAllLoading"' in html
    assert "const watchAllLoading = ref(false)" in html
    assert "watchAllLoading.value = value" in html
    assert "await API.toggleServer(route.params.id, enabled)" in html
    assert "await loadData();" in html


def test_frontend_layout_has_responsive_app_shell():
    html = read_static_source()

    assert ".header-bar { background: rgba(255,255,255,0.96)" in html
    assert ".header-actions { display: flex" in html
    assert ".nav-button.active" in html
    assert ".mobile-nav { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));" in html
    assert ".mobile-nav .nav-button { width: 100%; justify-content: center;" in html
    assert "function isActiveRoute(path)" in html
    assert "current === '/' || current.startsWith('/servers')" in html
    assert 'cols="1 s:2 m:4" responsive="screen"' in html
    assert 'class="dashboard-toolbar"' in html
    assert 'class="server-card"' in html
    assert 'class="truncate-text"' in html
    assert "@media (max-width: 760px)" in html


def test_frontend_exposes_registrar_quick_run_page():
    html = read_static_source()

    assert "listRegistrarAccounts() { return this.get('/api/registrar/accounts'); }" in html
    assert "quickRegister(data) { return this.post('/api/registrar/quick-run', data); }" in html
    assert "cancelRegistrarRun(runId)" in html
    assert "getRun(id) { return this.get(`/api/runs/${encodeURIComponent(id)}`); }" in html
    assert "const RegistrarPage = {" in html
    assert "一键抢注" in html
    assert "选择用于抢注的 Telegram 账号" in html
    assert "选择全部启用账号" in html
    assert "清空选择" in html
    assert "已选择 {{ selectedAccountCount }} 个 / 可用 {{ enabledAccountCount }} 个" in html
    assert "generatePassword" in html
    assert "重试间隔" in html
    assert "最长运行" in html
    assert "interval_seconds: 1" in html
    assert "timeout_minutes: 30" in html
    assert "interval_seconds: form.interval_seconds" in html
    assert "timeout_minutes: form.timeout_minutes" in html
    assert ':disabled="!canSubmit"' in html
    assert "const canSubmit = computed" in html
    assert "isIntegerInRange(form.interval_seconds, 1, 60)" in html
    assert "Bot 用户名格式不正确" in html
    assert "replace(/\\/+$/, '')" in html
    assert ":type=\"runAlertType\"" in html
    assert "runStatusInfo.value = '正在取消抢注任务'" in html
    assert "const isCompact = ref(window.innerWidth < 760)" in html
    assert "window.addEventListener('resize', updateCompactState)" in html
    assert "window.removeEventListener('resize', updateCompactState)" in html
    assert "重试间隔必须是 1 到 60 秒之间的整数" in html
    assert "最长运行时间必须是 1 到 1440 分钟之间的整数" in html
    assert "pollRunStatus" in html
    assert "stopRunPolling();" in html
    assert "canCancelLastRun" in html
    assert "注册账号和注册密码不能包含空白字符" in html
    assert "show-password-on=\"click\"" in html
    assert "form.password = ''" in html
    assert "{ path: 'registrar', component: RegistrarPage }" in html
    assert "$router.push('/registrar')" in html
    assert "login|schedule|runs|config|registrar" in html


def test_config_page_exposes_backup_and_health_diagnostics():
    html = read_static_source()

    assert "exportConfig() { return this.get('/api/config/export'); }" in html
    assert "getAutomationConfig() { return this.get('/api/config/automation'); }" in html
    assert "updateAutomationConfig(data) { return this.put('/api/config/automation', data); }" in html
    assert "backupConfig() { return this.post('/api/config/backup'); }" in html
    assert "function downloadJson(filename, data)" in html
    assert "handleExportConfig" in html
    assert "handleCreateBackup" in html
    assert "下载脱敏快照" in html
    assert "emby-keeper-redacted-" in html
    assert "脱敏快照已下载" in html
    assert "创建本地备份" in html
    assert "scheduler_task_running" in html
    assert "web_accounts_writable" in html
    assert "notifier_ready" in html
    assert "latest_run_id" in html
    assert "config_path" in html
    assert "web_accounts_path" in html
    assert "const isCompact = ref(window.innerWidth < 760)" in html
    assert "function descriptionColumns(count)" in html
    assert ':column="descriptionColumns(3)"' in html
    assert ':column="descriptionColumns(2)"' in html
    assert "window.addEventListener('resize', updateCompactState)" in html
    assert "window.removeEventListener('resize', updateCompactState)" in html


def test_config_page_exposes_automation_controls():
    html = read_static_source()

    assert "自动化配置" in html
    assert "签到站点" in html
    assert "签到时间范围" in html
    assert "签到间隔天数" in html
    assert "签到超时秒数" in html
    assert "定时抢注 Bot" in html
    assert "添加 Bot" in html
    assert "保存自动化配置" in html
    assert "automationForm.registrar_schedules" in html
    assert "assignAutomationForm" in html
    assert "normalizeBotForConfig" in html
    assert "splitListText" in html
    assert "optionalNonNegativeInteger" in html
    assert "API.updateAutomationConfig" in html
    assert "已保留 {{ automationForm.preserved_registrar_sites.length }} 个非模板抢注站点" in html
    assert ".automation-grid" in html


def test_frontend_exposes_schedule_preview_and_health_status():
    html = read_static_source()

    assert "previewSchedule(data)" in html
    assert "调度预览" in html
    assert "previewResult.value = null" in html
    assert "getHealth()" in html
    assert "运行健康" in html


def test_frontend_exposes_telegram_notifier_controls_without_echoing_token():
    html = read_static_source()

    assert "getNotifier()" in html
    assert "updateNotifier(data)" in html
    assert "testNotifier(data)" in html
    assert "Telegram" in html
    assert "telegram_bot_token" in html
    assert 'show-password-on="click"' in html
    assert "留空则保留现有 Token" in html
    assert "修改 Telegram Chat ID 时必须重新输入 Bot Token" in html


def test_frontend_uses_console_shell_and_direct_spa_route_mapping():
    html = read_static_source()

    assert 'class="app-shell"' in html
    assert 'class="sidebar"' in html
    assert 'class="mobile-nav"' in html
    assert "运维控制台" in html
    assert "function directRouteFromPathname()" in html
    assert "path === '/schedule' || path === '/runs' || path === '/config' || path === '/registrar'" in html
    assert "path === '/servers' || path.startsWith('/servers/')" in html
    assert "function directRouteHashUrl(route)" in html
    assert "window.history.replaceState(window.history.state, '', directRouteHashUrl(directRoute))" in html
    assert "Capture direct SPA routes before hash history normalizes an empty hash to #/." in html
    assert html.index("const directRoute = !window.location.hash ? directRouteFromPathname() : null") < html.index(
        "VueRouter.createRouter"
    )
    assert "VueRouter.createWebHashHistory(window.EK_BASE_PATH || '/')" in html
    assert "router.replace(directRoute)" in html


def test_frontend_dashboard_surfaces_operational_status():
    html = read_static_source()

    assert "GET /api/status/health" in html
    assert "latestRunSummary" in html
    assert "notifierSummary" in html
    assert "nextScheduleLabel" in html
    assert "最近保活" in html
    assert "Promise.all([" in html
    assert "API.getSchedule()" in html


def test_frontend_run_logs_can_filter_and_copy_errors():
    html = read_static_source()

    assert "logLevelFilter" in html
    assert "filteredRunLogs" in html
    assert "copyRunLogs" in html
    assert "copyErrorLogs" in html
    assert "复制错误信息" in html
    assert "navigator.clipboard.writeText(text)" in html
    assert "document.execCommand('copy')" in html


def test_config_page_has_required_group_labels():
    html = read_static_source()

    for label in ("Emby 保活", "Telegram 签到", "定时抢注", "通知", "备份 / 恢复"):
        assert label in html
    assert "仅使用用户手动配置的 Apprise URI 或 Telegram Bot Token + Chat ID" in html
