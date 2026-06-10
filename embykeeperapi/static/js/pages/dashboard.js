/* Emby Keeper WebUI — Dashboard / server list page. Registers EK.pages.DashboardPage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, reactive, computed, onMounted, onUnmounted } = Vue;
  const { useMessage } = naiveUI;
  const { formatDate, responseMessage, runUiAction } = EK.util;

  EK.pages.DashboardPage = {
    template: `
      <div class="page-container">
        <div class="page-header">
          <div>
            <div class="page-kicker">概览</div>
            <h1 class="page-title">仪表盘 / 服务器列表</h1>
            <p class="page-subtitle">服务健康、账号数量、运行任务、最近运行结果和下一次调度集中在这里。</p>
          </div>
          <div class="page-actions">
            <n-button @click="refreshDataAndPoll">刷新</n-button>
            <n-button type="primary" @click="$router.push('/servers/new')">添加服务器</n-button>
            <n-popconfirm @positive-click="handleWatchAll">
              <template #trigger><n-button :loading="watchAllLoading">全部保活</n-button></template>
              确定要对所有已启用服务器执行保活吗？
            </n-popconfirm>
          </div>
        </div>

        <div class="metric-grid">
          <div class="metric-card">
            <span class="metric-label">服务器总数</span>
            <strong class="metric-value">{{ status.total_servers }}</strong>
            <span class="metric-note">启用 {{ status.enabled_servers }}</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">在线账号</span>
            <strong class="metric-value">{{ status.online_servers }}</strong>
            <span class="metric-note">{{ onlineRate }}</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">运行任务</span>
            <strong class="metric-value">{{ status.running_servers }}</strong>
            <span class="metric-note">{{ runningSummary }}</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">下一次调度</span>
            <strong class="metric-value" style="font-size:18px">{{ nextScheduleLabel }}</strong>
            <span class="metric-note">{{ nextScheduleAccount }}</span>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">运行健康</div>
              <div class="panel-desc">来自 GET /api/status/health</div>
            </div>
            <n-tag :type="healthData && healthData.status === 'ok' ? 'success' : 'warning'" size="small" round>
              {{ healthData ? healthData.status : 'loading' }}
            </n-tag>
          </div>
          <div class="health-strip">
            <div class="health-item">
              <span class="health-label">调度器</span>
              <span class="health-value">
                <span class="status-dot" :class="healthData && healthData.scheduler_initialized ? 'online' : 'offline'" />
                {{ healthData && healthData.scheduler_initialized ? '已初始化' : '未初始化' }}
              </span>
            </div>
            <div class="health-item">
              <span class="health-label">通知</span>
              <span class="health-value">
                <span class="status-dot" :class="healthData && healthData.notifier_ready ? 'online' : (healthData && healthData.notifier_configured ? 'running' : 'unknown')" />
                {{ notifierSummary }}
              </span>
            </div>
            <div class="health-item">
              <span class="health-label">最近运行</span>
              <span class="health-value">{{ latestRunSummary }}</span>
            </div>
            <div class="health-item">
              <span class="health-label">最近保活</span>
              <span class="health-value">{{ formatDate(status.last_global_watch_time) }}</span>
            </div>
          </div>
        </div>

        <div class="dashboard-toolbar">
          <n-text class="section-title">服务器列表</n-text>
          <n-space class="dashboard-actions">
            <n-button size="small" @click="$router.push('/schedule')">查看调度</n-button>
            <n-button size="small" @click="$router.push('/runs')">运行日志</n-button>
          </n-space>
        </div>

        <n-spin :show="loading">
          <div v-if="servers.length === 0" class="empty-state">
            <n-empty description="暂无服务器，点击添加服务器开始" />
          </div>
          <div v-else class="server-grid">
            <n-card v-for="s in servers" :key="s.id" class="server-card" size="small" hoverable @click="$router.push('/servers/' + EK.API.encodeId(s.id))">
              <template #header>
                <n-space align="center" :size="8" style="min-width:0">
                  <span class="status-dot" :class="s.is_running ? 'running' : (s.is_online ? 'online' : (s.is_online === false ? 'offline' : 'unknown'))" />
                  <span class="truncate-text">{{ s.name || s.id }}</span>
                </n-space>
              </template>
              <template #header-extra>
                <n-tag :type="s.enabled ? 'success' : 'default'" size="small" round>{{ s.enabled ? '启用' : '禁用' }}</n-tag>
              </template>
              <n-space vertical :size="8">
                <n-text class="truncate-text" depth="3">{{ s.url }}</n-text>
                <div class="server-meta">
                  <span class="server-meta-item truncate-text">用户: {{ s.username }}</span>
                  <span class="server-meta-item truncate-text">下次: {{ formatDate(s.next_schedule_time) }}</span>
                  <span class="server-meta-item truncate-text">上次保活: {{ formatDate(s.last_watch_time) }}</span>
                  <span class="server-meta-item truncate-text">结果: {{ s.last_watch_status || '-' }}</span>
                </div>
                <n-space :size="8">
                  <n-tag :type="s.has_token ? 'info' : 'warning'" size="small">{{ s.has_token ? 'Token 已配置' : '未配置认证' }}</n-tag>
                  <n-tag v-if="s.is_running" type="warning" size="small">运行中</n-tag>
                </n-space>
              </n-space>
              <template #action>
                <n-space class="card-actions">
                  <n-button size="small" @click.stop="handleLogin(s.id)" :loading="actionLoading[s.id]">登录测试</n-button>
                  <n-button size="small" type="primary" @click.stop="handleWatch(s.id)" :loading="actionLoading[s.id+'-w']">保活</n-button>
                  <n-button v-if="s.is_running" size="small" type="warning" @click.stop="handleCancel(s.id)" :loading="actionLoading[s.id+'-c']">取消任务</n-button>
                </n-space>
              </template>
            </n-card>
          </div>
        </n-spin>
      </div>
    `,
    setup() {
      const message = useMessage();
      const servers = ref([]);
      const schedules = ref([]);
      const healthData = ref(null);
      const status = reactive({ total_servers: 0, enabled_servers: 0, online_servers: 0, running_servers: 0, last_global_watch_time: null });
      const loading = ref(true);
      const watchAllLoading = ref(false);
      const actionLoading = reactive({});
      let pollTimer = null;
      let disposed = false;

      const nextSchedule = computed(() => {
        const candidates = schedules.value
          .filter((item) => item.enabled !== false && item.next_time)
          .map((item) => ({ item, time: new Date(item.next_time).getTime() }))
          .filter((entry) => Number.isFinite(entry.time))
          .sort((a, b) => a.time - b.time);
        return candidates.length ? candidates[0].item : null;
      });
      const nextScheduleLabel = computed(() => nextSchedule.value ? formatDate(nextSchedule.value.next_time) : '-');
      const nextScheduleAccount = computed(() => nextSchedule.value ? nextSchedule.value.account_spec : '无计划任务');
      const onlineRate = computed(() => {
        if (!status.total_servers) return '暂无账号';
        return `${Math.round((status.online_servers / status.total_servers) * 100)}% 在线`;
      });
      const runningSummary = computed(() => status.running_servers ? '需要关注任务进度' : '当前空闲');
      const latestRunSummary = computed(() => {
        if (!healthData.value || !healthData.value.latest_run_id) return '-';
        return `${healthData.value.latest_run_status || 'unknown'} / ${healthData.value.latest_run_id}`;
      });
      const notifierSummary = computed(() => {
        if (!healthData.value) return '-';
        if (healthData.value.notifier_ready) return '已就绪';
        if (healthData.value.notifier_configured) return '已配置';
        return '未配置';
      });

      async function loadData() {
        try {
          const [serverList, statusData, health, scheduleList] = await Promise.all([
            EK.API.listServers(),
            EK.API.getStatus(),
            EK.API.getHealth(),
            EK.API.getSchedule(),
          ]);
          servers.value = serverList;
          schedules.value = scheduleList;
          healthData.value = health;
          Object.assign(status, statusData);
        } catch (e) { message.error(e.message); }
        finally { loading.value = false; }
      }

      function refreshPoll() {
        if (disposed) return;
        if (pollTimer) clearTimeout(pollTimer);
        const hasRunning = servers.value.some((server) => server.is_running);
        pollTimer = setTimeout(async () => {
          await loadData();
          if (!disposed) refreshPoll();
        }, hasRunning ? 5000 : 30000);
      }

      async function refreshDataAndPoll() {
        await loadData();
        refreshPoll();
      }

      async function handleLogin(id) {
        await runUiAction({
          setLoading: (value) => { actionLoading[id] = value; },
          action: () => EK.API.triggerLogin(id),
          message,
          success: (res) => responseMessage(res, '登录测试已触发'),
          refresh: refreshDataAndPoll,
        });
      }

      async function handleWatch(id) {
        await runUiAction({
          setLoading: (value) => { actionLoading[id + '-w'] = value; },
          action: () => EK.API.triggerWatch(id),
          message,
          success: (res) => responseMessage(res, '保活任务已启动'),
          refresh: refreshDataAndPoll,
        });
      }

      async function handleCancel(id) {
        await runUiAction({
          setLoading: (value) => { actionLoading[id + '-c'] = value; },
          action: () => EK.API.cancelWatch(id),
          message,
          success: '已请求取消任务',
          refresh: refreshDataAndPoll,
        });
      }

      async function handleWatchAll() {
        await runUiAction({
          setLoading: (value) => { watchAllLoading.value = value; },
          action: () => EK.API.watchAll(),
          message,
          success: (res) => responseMessage(res, '全部保活任务已启动'),
          refresh: refreshDataAndPoll,
        });
      }

      onMounted(async () => {
        disposed = false;
        await loadData();
        refreshPoll();
      });
      onUnmounted(() => {
        disposed = true;
        if (pollTimer) clearTimeout(pollTimer);
      });

      return {
        EK, servers, schedules, healthData, status, loading, watchAllLoading, actionLoading,
        nextScheduleLabel, nextScheduleAccount, onlineRate, runningSummary, latestRunSummary,
        notifierSummary, formatDate, refreshDataAndPoll, handleLogin, handleWatch, handleCancel, handleWatchAll
      };
    }
  };
}());
