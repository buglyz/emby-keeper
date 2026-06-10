/* Emby Keeper WebUI — Run history page. Registers EK.pages.RunHistoryPage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, computed, onMounted } = Vue;
  const { useMessage } = naiveUI;
  const { formatDate, runStatusType, copyText } = EK.util;

  EK.pages.RunHistoryPage = {
    template: `
      <div class="page-container">
        <div class="page-header">
          <div>
            <div class="page-kicker">运行记录</div>
            <h1 class="page-title">运行日志</h1>
            <p class="page-subtitle">按状态筛选运行任务，打开详情后可以复制完整日志或仅复制错误信息。</p>
          </div>
          <div class="page-actions">
            <n-select v-model:value="statusFilter" :options="statusOptions" size="small" style="width:130px" @update:value="loadData(true)" />
            <n-input-number v-model:value="cleanupDays" :min="1" :precision="0" size="small" style="width:110px" />
            <n-popconfirm @positive-click="handleCleanup">
              <template #trigger>
                <n-button size="small" :loading="cleanupLoading">清理旧记录</n-button>
              </template>
              清理超过 {{ cleanupDays }} 天的运行记录？
            </n-popconfirm>
            <n-button size="small" @click="loadData(true)">刷新</n-button>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">运行列表</div>
              <div class="panel-desc">最近任务默认加载 50 条，可继续加载更多。</div>
            </div>
          </div>
          <n-spin :show="loading">
            <div v-if="runs.length" class="data-table-wrap">
              <n-data-table :columns="columns" :data="runs" :bordered="false" />
            </div>
            <n-empty v-else description="暂无运行记录" />
            <n-space v-if="runs.length && hasMore" justify="center" style="margin-top:12px">
              <n-button size="small" :loading="loadingMore" @click="loadMore">加载更多</n-button>
            </n-space>
          </n-spin>
        </div>
        <n-modal v-model:show="logModalVisible" preset="card" style="width:min(900px, 92vw)" title="运行日志">
          <n-spin :show="logLoading">
            <n-space vertical :size="8">
              <n-space justify="space-between" align="center">
                <n-text depth="3">Run ID: {{ selectedRunId || '-' }}</n-text>
                <n-space :size="8">
                  <n-select v-model:value="logLevelFilter" :options="logLevelOptions" size="small" style="width:110px" />
                  <n-button size="small" @click="copyRunLogs" :disabled="!filteredRunLogs.length">复制日志</n-button>
                  <n-button size="small" @click="copyErrorLogs" :disabled="!errorLogs.length">复制错误信息</n-button>
                </n-space>
              </n-space>
              <div v-if="filteredRunLogs.length" class="log-viewer">
                <div v-for="(log, index) in filteredRunLogs" :key="index" class="log-line">[{{ formatDate(log.time) }}] {{ log.level }} {{ log.message }}</div>
              </div>
              <n-empty v-else description="暂无日志" />
            </n-space>
          </n-spin>
        </n-modal>
      </div>
    `,
    setup() {
      const message = useMessage();
      const runs = ref([]);
      const loading = ref(true);
      const loadingMore = ref(false);
      const cleanupLoading = ref(false);
      const statusFilter = ref(null);
      const cleanupDays = ref(30);
      const pageSize = 50;
      const hasMore = ref(false);
      const logModalVisible = ref(false);
      const logLoading = ref(false);
      const selectedRunId = ref('');
      const runLogs = ref([]);
      const logLevelFilter = ref(null);

      const logLevelOptions = [
        { label: '全部级别', value: null },
        { label: 'ERROR', value: 'ERROR' },
        { label: 'WARNING', value: 'WARNING' },
        { label: 'INFO', value: 'INFO' },
        { label: 'DEBUG', value: 'DEBUG' },
      ];
      const filteredRunLogs = computed(() => {
        if (!logLevelFilter.value) return runLogs.value;
        return runLogs.value.filter((log) => String(log.level || '').toUpperCase() === logLevelFilter.value);
      });
      const errorLogs = computed(() => runLogs.value.filter((log) => {
        const level = String(log.level || '').toUpperCase();
        return level === 'ERROR' || level === 'FAIL' || /error|exception|traceback|失败|异常/i.test(log.message || '');
      }));

      const columns = [
        { title: 'Run ID', key: 'run_id', width: 120 },
        { title: '任务', key: 'description' },
        { title: '账号', key: 'account_spec' },
        { title: '状态', key: 'status', render(row) {
          return Vue.h(naiveUI.NTag, { type: runStatusType(row.status), size: 'small' }, () => row.status_info || row.status);
        }},
        { title: '开始时间', key: 'start_time', render(row) { return formatDate(row.start_time); } },
        { title: '耗时', key: 'duration', render(row) { return row.duration == null ? '-' : `${row.duration.toFixed(1)}s`; } },
        { title: '操作', key: 'actions', width: 90, render(row) {
          return Vue.h(naiveUI.NButton, { size: 'small', onClick: () => openRunLogs(row.run_id) }, () => '日志');
        }},
      ];

      const statusOptions = [
        { label: '全部状态', value: null },
        { label: '成功', value: 'success' },
        { label: '失败', value: 'fail' },
        { label: '错误', value: 'error' },
        { label: '运行中', value: 'running' },
        { label: '已取消', value: 'cancelled' },
      ];

      async function loadData() {
        loading.value = true;
        try {
          const data = await EK.API.getRuns({ limit: pageSize, offset: 0, status: statusFilter.value });
          runs.value = data;
          hasMore.value = data.length === pageSize;
        }
        catch (e) { message.error(e.message); }
        finally { loading.value = false; }
      }

      async function loadMore() {
        loadingMore.value = true;
        try {
          const data = await EK.API.getRuns({ limit: pageSize, offset: runs.value.length, status: statusFilter.value });
          runs.value = runs.value.concat(data);
          hasMore.value = data.length === pageSize;
        } catch (e) { message.error(e.message); }
        finally { loadingMore.value = false; }
      }

      async function handleCleanup() {
        cleanupLoading.value = true;
        try {
          const res = await EK.API.cleanupRuns(cleanupDays.value || 30);
          message.success(`已清理 ${res.deleted || 0} 条运行记录`);
          await loadData(true);
        } catch (e) { message.error(e.message); }
        finally { cleanupLoading.value = false; }
      }

      async function openRunLogs(runId) {
        selectedRunId.value = runId;
        runLogs.value = [];
        logLevelFilter.value = null;
        logModalVisible.value = true;
        logLoading.value = true;
        try {
          const data = await EK.API.getRunLogs(runId);
          runLogs.value = data.logs || [];
        } catch (e) { message.error(e.message); }
        finally { logLoading.value = false; }
      }

      function logsToText(logs) {
        return logs.map((log) => `[${formatDate(log.time)}] ${log.level} ${log.message}`).join('\n');
      }

      async function copyRunLogs() {
        await copyText(logsToText(filteredRunLogs.value), message, '日志已复制');
      }

      async function copyErrorLogs() {
        await copyText(logsToText(errorLogs.value), message, '错误信息已复制');
      }

      onMounted(loadData);
      return { runs, loading, loadingMore, cleanupLoading, statusFilter, statusOptions, cleanupDays, hasMore, columns, logModalVisible, logLoading, selectedRunId, runLogs, logLevelFilter, logLevelOptions, filteredRunLogs, errorLogs, loadData, loadMore, handleCleanup, openRunLogs, copyRunLogs, copyErrorLogs, formatDate };
    }
  };
}());
