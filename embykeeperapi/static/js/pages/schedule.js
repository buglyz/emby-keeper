/* Emby Keeper WebUI — Schedule page. Registers EK.pages.SchedulePage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, reactive, onMounted } = Vue;
  const { useMessage } = naiveUI;
  const { formatDate, optionalText } = EK.util;

  EK.pages.SchedulePage = {
    template: `
      <div class="page-container">
        <div class="page-header">
          <div>
            <div class="page-kicker">任务调度</div>
            <h1 class="page-title">调度</h1>
            <p class="page-subtitle">查看所有保活计划、运行状态和下一次执行时间，也可以即时触发或取消正在运行的任务。</p>
          </div>
          <div class="page-actions">
            <n-button @click="loadData">刷新</n-button>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">计划任务</div>
              <div class="panel-desc">来自 GET /api/schedule</div>
            </div>
          </div>
          <n-spin :show="loading">
            <div v-if="schedules.length" class="data-table-wrap">
              <n-data-table :columns="columns" :data="schedules" :bordered="false" />
            </div>
            <n-empty v-else description="暂无计划任务" />
          </n-spin>
        </div>

        <div class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">调度预览</div>
              <div class="panel-desc">输入间隔和时间范围后预览下次运行时间。</div>
            </div>
          </div>
          <n-space vertical :size="12">
            <div class="form-grid">
              <n-input v-model:value="previewForm.interval_days" placeholder="间隔天数，如 &lt;7,12&gt;" />
              <n-input v-model:value="previewForm.time_range" placeholder="时间范围，如 &lt;11:00AM,11:00PM&gt;" />
            </div>
            <n-space>
              <n-button :loading="previewLoading" @click="handlePreview">预览</n-button>
            </n-space>
            <n-alert v-if="previewResult" type="success" :bordered="false">
              下次运行：{{ formatDate(previewResult.next_time) }}
            </n-alert>
          </n-space>
        </div>
      </div>
    `,
    setup() {
      const message = useMessage();
      const schedules = ref([]);
      const loading = ref(true);
      const previewLoading = ref(false);
      const previewResult = ref(null);
      const previewForm = reactive({ interval_days: '', time_range: '' });

      const columns = [
        { title: 'ID', key: 'id', width: 200 },
        { title: '账号', key: 'account_spec' },
        { title: '间隔', key: 'interval_days' },
        { title: '时间范围', key: 'time_range' },
        { title: '状态', key: 'is_running', render(row) {
          return Vue.h(naiveUI.NTag, { type: row.is_running ? 'warning' : 'info', size: 'small' }, () => row.is_running ? '运行中' : '等待');
        }},
        { title: '操作', key: 'actions', render(row) {
          return Vue.h(naiveUI.NSpace, { size: 8 }, () => [
            Vue.h(naiveUI.NButton, { size: 'small', onClick: () => handleRunNow(row.id) }, () => '立即执行'),
            row.is_running ? Vue.h(naiveUI.NButton, { size: 'small', type: 'warning', onClick: () => handleCancel(row.id) }, () => '取消') : null,
          ]);
        }},
      ];

      async function loadData() {
        loading.value = true;
        try { schedules.value = await EK.API.getSchedule(); }
        catch (e) { message.error(e.message); }
        finally { loading.value = false; }
      }

      async function handleRunNow(id) {
        try {
          const res = await EK.API.runNow(id);
          message.success(res && res.message ? res.message : '任务已启动');
          await loadData();
        } catch (e) { message.error(e.message); }
      }

      async function handleCancel(id) {
        try {
          await EK.API.cancelSchedule(id);
          message.success('已请求取消任务');
          await loadData();
        } catch (e) { message.error(e.message); }
      }

      async function handlePreview() {
        previewLoading.value = true;
        previewResult.value = null;
        try {
          previewResult.value = await EK.API.previewSchedule({
            interval_days: optionalText(previewForm.interval_days),
            time_range: optionalText(previewForm.time_range),
          });
        } catch (e) { message.error(e.message); }
        finally { previewLoading.value = false; }
      }

      onMounted(loadData);
      return { schedules, loading, columns, previewForm, previewLoading, previewResult, loadData, handlePreview, formatDate };
    }
  };
}());
