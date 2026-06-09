function getApiBasePath() {
  if (typeof window.EK_BASE_PATH === 'string') return window.EK_BASE_PATH;
  const origin = window.location.origin;
  const marker = '/static/vendor/';
  const localVendorScript = Array.from(document.scripts).find((script) => (
    script.src.startsWith(origin) && script.src.includes(marker)
  ));
  if (localVendorScript) {
    const basePath = localVendorScript.src.slice(origin.length, localVendorScript.src.indexOf(marker));
    if (basePath) return basePath;
  }
  const path = window.location.pathname.replace(/\/$/, '');
  return path === '/' ? '' : path;
}

const API_BASE_PATH = getApiBasePath();
const API = {
  baseUrl: `${window.location.origin}${API_BASE_PATH}`,
  _getHeaders() {
    const token = sessionStorage.getItem('ek_jwt');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  },
  _formatErrorDetail(detail) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
          const msg = item.msg || item.message || JSON.stringify(item);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return item == null ? '' : String(item);
      }).filter(Boolean);
      return messages.join('; ');
    }
    if (detail && typeof detail === 'object') {
      return detail.msg || detail.message || JSON.stringify(detail);
    }
    return detail == null ? '' : String(detail);
  },
  async request(method, path, body = null) {
    const opts = { method, headers: this._getHeaders() };
    if (body !== null && body !== undefined) opts.body = JSON.stringify(body);
    const resp = await fetch(`${this.baseUrl}${path}`, opts);
    const text = await resp.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); }
      catch (e) { data = { detail: text }; }
    }
    if (resp.status === 401) {
      if (!path.startsWith('/api/auth/')) {
        sessionStorage.removeItem('ek_jwt');
        window.location.hash = '#/login';
      }
      throw new Error(this._formatErrorDetail(data && data.detail) || '登录状态已失效');
    }
    if (!resp.ok) throw new Error(this._formatErrorDetail(data && data.detail) || 'Request failed');
    return data;
  },
  get(path) { return this.request('GET', path); },
  post(path, body = null) { return this.request('POST', path, body); },
  put(path, body) { return this.request('PUT', path, body); },
  patch(path, body) { return this.request('PATCH', path, body); },
  delete(path) { return this.request('DELETE', path); },

  // Auth
  authMethods() { return this.get('/api/auth/methods'); },
  tokenExchange(token) { return this.post('/api/auth/token-exchange', { token }); },
  passwordLogin(password) { return this.post('/api/auth/login', { password }); },
  verifyToken() { return this.get('/api/auth/me'); },

  // Servers
  encodeId(id) { return encodeURIComponent(id); },
  serverPath(id, suffix = '') { return `/api/servers/${this.encodeId(id)}${suffix}`; },
  listServers() { return this.get('/api/servers'); },
  getServer(id) { return this.get(this.serverPath(id)); },
  createServer(data) { return this.post('/api/servers', data); },
  updateServer(id, data) { return this.put(this.serverPath(id), data); },
  deleteServer(id) { return this.delete(this.serverPath(id)); },
  toggleServer(id, enabled) { return this.patch(this.serverPath(id, '/toggle'), { enabled }); },

  // Actions
  triggerLogin(id) { return this.post(this.serverPath(id, '/login')); },
  triggerWatch(id) { return this.post(this.serverPath(id, '/watch')); },
  cancelWatch(id) { return this.post(this.serverPath(id, '/cancel')); },
  watchAll() { return this.post('/api/servers/actions/watch-all'); },

  // Schedule & Status
  getSchedule() { return this.get('/api/schedule'); },
  runNow(id) { return this.post(`/api/schedule/${encodeURIComponent(id)}/run-now`); },
  cancelSchedule(id) { return this.post(`/api/schedule/${encodeURIComponent(id)}/cancel`); },
  previewSchedule(data) { return this.post('/api/schedule/preview', data); },
  getStatus() { return this.get('/api/status'); },
  getHealth() { return this.get('/api/status/health'); },
  getRuns({ limit = 50, offset = 0, status = null } = {}) {
    const params = new URLSearchParams({ limit, offset });
    if (status) params.set('status', status);
    return this.get(`/api/runs?${params.toString()}`);
  },
  getRun(id) { return this.get(`/api/runs/${encodeURIComponent(id)}`); },
  getRunLogs(id) { return this.get(`/api/runs/${encodeURIComponent(id)}/logs`); },
  cleanupRuns(days) { return this.delete(`/api/runs?days=${encodeURIComponent(days)}`); },
  healthz() { return this.get('/healthz'); },

  // Config
  getConfig() { return this.get('/api/config'); },
  updateConfig(data) { return this.put('/api/config', data); },
  getAutomationConfig() { return this.get('/api/config/automation'); },
  updateAutomationConfig(data) { return this.put('/api/config/automation', data); },
  exportConfig() { return this.get('/api/config/export'); },
  backupConfig() { return this.post('/api/config/backup'); },
  listBackups() { return this.get('/api/config/backups'); },
  restoreBackup(id) { return this.post(`/api/config/backups/${encodeURIComponent(id)}/restore`, { confirm: true }); },
  getNotifier() { return this.get('/api/config/notifier'); },
  updateNotifier(data) { return this.put('/api/config/notifier', data); },
  testNotifier(data) { return this.post('/api/config/notifier/test', data); },

  // Registrar
  listRegistrarAccounts() { return this.get('/api/registrar/accounts'); },
  quickRegister(data) { return this.post('/api/registrar/quick-run', data); },
  cancelRegistrarRun(runId) { return this.post(`/api/registrar/runs/${encodeURIComponent(runId)}/cancel`); },
};

function responseMessage(res, fallback) {
  return res && res.message ? res.message : fallback;
}

async function runUiAction({ setLoading, action, message, success, refresh }) {
  if (setLoading) setLoading(true);
  try {
    const res = await action();
    const text = typeof success === 'function' ? success(res) : success;
    if (text) message.success(text);
    if (refresh) await refresh(res);
    return res;
  } catch (e) {
    message.error(e.message);
    return null;
  } finally {
    if (setLoading) setLoading(false);
  }
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
