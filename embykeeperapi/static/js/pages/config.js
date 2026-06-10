/* Emby Keeper WebUI — Config page. Registers EK.pages.ConfigPage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, reactive, computed, onMounted, onUnmounted } = Vue;
  const { useMessage } = naiveUI;
  const { formatDate, optionalText, splitListText, normalizeBot, downloadJson } = EK.util;

  EK.pages.ConfigPage = {
    template: `
      <div class="page-container">
        <div class="page-header">
          <div>
            <div class="page-kicker">系统配置</div>
            <h1 class="page-title">配置</h1>
            <p class="page-subtitle">集中管理 Emby 保活、Telegram 签到、定时抢注、通知以及备份 / 恢复。</p>
          </div>
          <div class="page-actions">
            <n-button @click="loadData">刷新</n-button>
            <n-button :loading="exportingBackup" @click="handleExportConfig">下载脱敏快照</n-button>
            <n-button :loading="creatingBackup" type="primary" @click="handleCreateBackup">创建本地备份</n-button>
          </div>
        </div>
        <n-spin :show="loading">
          <div class="panel">
            <div class="panel-header">
              <div>
                <div class="panel-title">运行健康</div>
                <div class="panel-desc">配置加载、调度器、文件可写性、通知状态和最近运行。</div>
              </div>
            </div>
            <n-descriptions v-if="healthData" label-placement="left" :column="descriptionColumns(3)" bordered>
              <n-descriptions-item label="状态">
                <n-tag :type="healthData.status === 'ok' ? 'success' : 'warning'">{{ healthData.status }}</n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="账号数">{{ healthData.account_count }}</n-descriptions-item>
              <n-descriptions-item label="计划数">{{ healthData.schedule_count }}</n-descriptions-item>
              <n-descriptions-item label="配置已加载">{{ healthData.config_loaded ? '是' : '否' }}</n-descriptions-item>
              <n-descriptions-item label="调度器">{{ healthData.scheduler_initialized ? '已初始化' : '未初始化' }}</n-descriptions-item>
              <n-descriptions-item label="调度任务">{{ healthData.scheduler_task_running ? '运行中' : '未运行' }}</n-descriptions-item>
              <n-descriptions-item label="配置可写">{{ healthData.config_writable ? '是' : '否' }}</n-descriptions-item>
              <n-descriptions-item label="账号文件可写">{{ healthData.web_accounts_writable ? '是' : '否' }}</n-descriptions-item>
              <n-descriptions-item label="Web 认证">{{ healthData.auth_configured ? '已配置' : '未配置' }}</n-descriptions-item>
              <n-descriptions-item label="通知">{{ healthData.notifier_configured ? (healthData.notifier_ready ? '已就绪' : '已配置') : '未配置' }}</n-descriptions-item>
              <n-descriptions-item label="通知发送">{{ healthData.notifier_last_status ? (healthData.notifier_last_status + (healthData.notifier_last_error ? ' / ' + healthData.notifier_last_error : '')) : '-' }}</n-descriptions-item>
              <n-descriptions-item label="最近运行">{{ healthData.latest_run_id ? healthData.latest_run_id + ' / ' + healthData.latest_run_status : '-' }}</n-descriptions-item>
              <n-descriptions-item label="配置路径">{{ healthData.config_path || '-' }}</n-descriptions-item>
              <n-descriptions-item label="账号路径">{{ healthData.web_accounts_path || '-' }}</n-descriptions-item>
            </n-descriptions>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <div class="panel-title">Emby 保活</div>
                <div class="panel-desc">全局保活时间、间隔、并发和代理设置。</div>
              </div>
            </div>
            <n-descriptions v-if="configData" label-placement="left" :column="descriptionColumns(2)" bordered>
              <n-descriptions-item label="Emby 时间范围">
                <n-input v-model:value="editConfig.emby_time_range" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="Emby 间隔天数">
                <n-input v-model:value="editConfig.emby_interval_days" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="Emby 并发数">
                <n-input-number v-model:value="editConfig.emby_concurrency" :min="1" :precision="0" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="代理主机">
                <n-input v-model:value="editConfig.proxy_hostname" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="代理端口">
                <n-input-number v-model:value="editConfig.proxy_port" :min="1" :precision="0" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="代理协议">
                <n-select v-model:value="editConfig.proxy_scheme" :options="[{label:'SOCKS5',value:'socks5'},{label:'HTTP',value:'http'}]" size="small" />
              </n-descriptions-item>
            </n-descriptions>
            <n-divider />
            <n-space justify="end">
              <n-button :loading="saving" type="primary" @click="handleSave">保存配置</n-button>
            </n-space>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <div class="panel-title">自动化配置</div>
                <div class="panel-desc">Telegram 签到和定时抢注共用 Telegram 账号配置，保存后会重新拉取并刷新运行时。</div>
              </div>
            </div>
            <div class="form-section-title">Telegram 签到</div>
            <n-descriptions label-placement="left" :column="descriptionColumns(2)" bordered>
              <n-descriptions-item label="签到站点">
                <n-select v-model:value="automationForm.checkiner_sites" multiple filterable tag :options="checkinerSiteOptions" size="small" placeholder="all 或站点名" />
              </n-descriptions-item>
              <n-descriptions-item label="签到时间范围">
                <n-input v-model:value="automationForm.checkiner_time_range" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="签到间隔天数">
                <n-input v-model:value="automationForm.checkiner_interval_days" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="签到超时秒数">
                <n-input-number v-model:value="automationForm.checkiner_timeout" :min="1" :max="3600" :precision="0" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="签到重试次数">
                <n-input-number v-model:value="automationForm.checkiner_retries" :min="0" :max="20" :precision="0" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="签到并发数">
                <n-input-number v-model:value="automationForm.checkiner_concurrency" :min="1" :max="50" :precision="0" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="随机延迟分钟">
                <n-input-number v-model:value="automationForm.checkiner_random_start" :min="0" :max="1440" :precision="0" size="small" />
              </n-descriptions-item>
              <n-descriptions-item label="抢注并发数">
                <n-input-number v-model:value="automationForm.registrar_concurrency" :min="1" :max="50" :precision="0" size="small" />
              </n-descriptions-item>
            </n-descriptions>
            <n-divider />
            <div class="form-section-title">定时抢注</div>
            <n-space justify="space-between" align="center" style="margin-bottom:12px">
              <n-text strong>定时抢注 Bot</n-text>
              <n-button size="small" @click="addRegistrarSchedule">添加 Bot</n-button>
            </n-space>
            <n-alert v-if="automationForm.preserved_registrar_sites.length" type="info" :bordered="false" style="margin-bottom:12px">
              已保留 {{ automationForm.preserved_registrar_sites.length }} 个非模板抢注站点。
            </n-alert>
            <div v-if="automationForm.registrar_schedules.length">
              <div v-for="(item, index) in automationForm.registrar_schedules" :key="index" class="automation-row">
                <div class="automation-grid">
                  <div>
                    <n-text depth="3" style="font-size:12px">Bot</n-text>
                    <n-input v-model:value="item.bot_username" size="small" placeholder="@ExampleBot" />
                  </div>
                  <div>
                    <n-text depth="3" style="font-size:12px">模式</n-text>
                    <n-select v-model:value="item.mode" :options="registrarModeOptions" size="small" />
                  </div>
                  <div v-if="item.mode === 'times'">
                    <n-text depth="3" style="font-size:12px">时间</n-text>
                    <n-input v-model:value="item.times_text" size="small" placeholder="9:00AM, 9:00PM" />
                  </div>
                  <div v-else>
                    <n-text depth="3" style="font-size:12px">间隔分钟</n-text>
                    <n-input-number v-model:value="item.interval_minutes" :min="1" :max="1440" :precision="0" size="small" />
                  </div>
                  <div>
                    <n-text depth="3" style="font-size:12px">超时/重试</n-text>
                    <n-space :size="6">
                      <n-input-number v-model:value="item.timeout" :min="1" :max="3600" :precision="0" size="small" placeholder="超时" />
                      <n-input-number v-model:value="item.retries" :min="0" :max="20" :precision="0" size="small" placeholder="重试" />
                      <n-button size="small" text type="error" @click="removeRegistrarSchedule(index)">删除</n-button>
                    </n-space>
                  </div>
                </div>
              </div>
            </div>
            <n-empty v-else description="暂无定时抢注 Bot" />
            <n-divider />
            <n-space justify="end">
              <n-button :loading="savingAutomation" type="primary" @click="handleSaveAutomation">保存自动化配置</n-button>
            </n-space>
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <div class="panel-title">备份 / 恢复</div>
                <div class="panel-desc">下载脱敏诊断快照，或从本地备份恢复配置文件。</div>
              </div>
            </div>
            <div v-if="backups.length" class="data-table-wrap">
              <n-data-table :columns="backupColumns" :data="backups" :bordered="false" size="small" />
            </div>
            <n-empty v-else description="暂无本地备份" />
          </div>

          <div class="panel">
            <div class="panel-header">
              <div>
                <div class="panel-title">通知</div>
                <div class="panel-desc">仅使用用户手动配置的 Apprise URI 或 Telegram Bot Token + Chat ID。</div>
              </div>
            </div>
            <n-alert v-if="notifierData && notifierData.configured" type="success" :bordered="false" style="margin-bottom:12px">
              当前目标：{{ notifierData.target_label }}
            </n-alert>
            <n-descriptions label-placement="left" :column="descriptionColumns(2)" bordered>
              <n-descriptions-item label="启用通知">
                <n-switch v-model:value="notifierForm.enabled" />
              </n-descriptions-item>
              <n-descriptions-item label="方式">
                <n-select v-model:value="notifierForm.method" :options="[{label:'Telegram',value:'telegram'},{label:'Apprise URI',value:'apprise'}]" size="small" />
              </n-descriptions-item>
              <n-descriptions-item v-if="notifierForm.method === 'telegram'" label="Bot Token">
                <n-input v-model:value="notifierForm.telegram_bot_token" type="password" show-password-on="click" size="small" placeholder="留空则保留现有 Token" />
              </n-descriptions-item>
              <n-descriptions-item v-if="notifierForm.method === 'telegram'" label="Chat ID">
                <n-input v-model:value="notifierForm.telegram_chat_id" size="small" placeholder="@channel 或 -100..." />
              </n-descriptions-item>
              <n-descriptions-item v-if="notifierForm.method === 'apprise'" label="Apprise URI">
                <n-input v-model:value="notifierForm.apprise_uri" type="password" show-password-on="click" size="small" placeholder="留空则保留现有 URI" />
              </n-descriptions-item>
            </n-descriptions>
            <n-divider />
            <n-space justify="end">
              <n-button :loading="testingNotifier" @click="handleTestNotifier">测试发送</n-button>
              <n-button :loading="savingNotifier" type="primary" @click="handleSaveNotifier">保存通知</n-button>
            </n-space>
          </div>
        </n-spin>
      </div>
    `,
    setup() {
      const message = useMessage();
      const configData = ref(null);
      const automationData = ref(null);
      const healthData = ref(null);
      const notifierData = ref(null);
      const backups = ref([]);
      const loading = ref(true);
      const isCompact = ref(window.innerWidth < 760);
      const saving = ref(false);
      const savingAutomation = ref(false);
      const savingNotifier = ref(false);
      const testingNotifier = ref(false);
      const exportingBackup = ref(false);
      const creatingBackup = ref(false);
      const restoringBackup = ref('');
      const editConfig = reactive({
        emby_time_range: '', emby_interval_days: '', emby_concurrency: 1,
        proxy_hostname: '', proxy_port: null, proxy_scheme: null,
      });
      const automationForm = reactive({
        checkiner_sites: [],
        checkiner_time_range: '',
        checkiner_interval_days: '',
        checkiner_timeout: 120,
        checkiner_retries: 4,
        checkiner_concurrency: 1,
        checkiner_random_start: 60,
        registrar_concurrency: 1,
        registrar_schedules: [],
        preserved_registrar_sites: [],
      });
      const notifierForm = reactive({
        enabled: false, method: 'apprise', apprise_uri: '',
        telegram_bot_token: '', telegram_chat_id: '',
      });
      const checkinerSiteOptions = [
        { label: '全部站点', value: 'all' },
      ];
      const registrarModeOptions = [
        { label: '指定时间', value: 'times' },
        { label: '固定间隔', value: 'interval' },
      ];

      function updateCompactState() {
        isCompact.value = window.innerWidth < 760;
      }

      function descriptionColumns(count) {
        return isCompact.value ? 1 : count;
      }

      const backupColumns = [
        { title: '备份 ID', key: 'id', width: 190 },
        { title: '创建时间', key: 'created_at', render(row) { return formatDate(row.created_at); } },
        { title: '文件', key: 'files', render(row) { return (row.files || []).join(', ') || '-'; } },
        { title: '操作', key: 'actions', width: 100, render(row) {
          return Vue.h(naiveUI.NPopconfirm, { onPositiveClick: () => handleRestoreBackup(row.id) }, {
            trigger: () => Vue.h(naiveUI.NButton, { size: 'small', loading: restoringBackup.value === row.id }, () => '恢复'),
            default: () => `恢复备份 ${row.id}？当前配置会先自动备份。`,
          });
        }},
      ];

      async function loadData() {
        try {
          configData.value = await EK.API.getConfig();
          Object.assign(editConfig, configData.value);
          automationData.value = await EK.API.getAutomationConfig();
          assignAutomationForm(automationData.value);
          healthData.value = await EK.API.getHealth();
          notifierData.value = await EK.API.getNotifier();
          backups.value = await EK.API.listBackups();
          notifierForm.enabled = notifierData.value.enabled;
          notifierForm.method = notifierData.value.method === 'telegram' ? 'telegram' : 'apprise';
          notifierForm.apprise_uri = '';
          notifierForm.telegram_bot_token = '';
          notifierForm.telegram_chat_id = notifierData.value.telegram_chat_id || '';
        } catch (e) { message.error(e.message); }
        finally { loading.value = false; }
      }

      function normalizeBotForConfig(value) {
        return normalizeBot(value);
      }

      function assignAutomationForm(data) {
        automationForm.checkiner_sites = [...(data.checkiner_sites || [])];
        automationForm.checkiner_time_range = data.checkiner_time_range || '';
        automationForm.checkiner_interval_days = data.checkiner_interval_days || '';
        automationForm.checkiner_timeout = data.checkiner_timeout || 120;
        automationForm.checkiner_retries = data.checkiner_retries ?? 4;
        automationForm.checkiner_concurrency = data.checkiner_concurrency || 1;
        automationForm.checkiner_random_start = data.checkiner_random_start ?? 60;
        automationForm.registrar_concurrency = data.registrar_concurrency || 1;
        automationForm.preserved_registrar_sites = [...(data.preserved_registrar_sites || [])];
        automationForm.registrar_schedules = (data.registrar_schedules || []).map((item) => ({
          bot_username: item.bot_username || '',
          mode: item.mode || (item.interval_minutes ? 'interval' : 'times'),
          times_text: (item.times || []).join(', '),
          interval_minutes: item.interval_minutes || 5,
          timeout: item.timeout || 120,
          retries: item.retries ?? 1,
        }));
      }

      function addRegistrarSchedule() {
        automationForm.registrar_schedules.push({
          bot_username: '',
          mode: 'times',
          times_text: '9:00AM',
          interval_minutes: 5,
          timeout: 120,
          retries: 1,
        });
      }

      function removeRegistrarSchedule(index) {
        automationForm.registrar_schedules.splice(index, 1);
      }

      function addScheduleText(data, key, value, label) {
        const normalized = optionalText(value);
        if (normalized === null && configData.value && configData.value[key] != null) {
          throw new Error(`${label}不能为空`);
        }
        if (normalized !== null) data[key] = normalized;
      }

      function optionalPositiveInteger(value, label) {
        if (value == null) return null;
        if (!Number.isInteger(value) || value <= 0) {
          throw new Error(`${label}必须是正整数`);
        }
        return value;
      }

      function optionalNonNegativeInteger(value, label) {
        if (value == null) return null;
        if (!Number.isInteger(value) || value < 0) {
          throw new Error(`${label}必须是非负整数`);
        }
        return value;
      }

      async function handleSave() {
        saving.value = true;
        try {
          const data = {
            emby_concurrency: optionalPositiveInteger(editConfig.emby_concurrency, 'Emby 并发数'),
            proxy: {
              hostname: optionalText(editConfig.proxy_hostname),
              port: optionalPositiveInteger(editConfig.proxy_port, '代理端口'),
              scheme: editConfig.proxy_scheme || null,
            },
          };
          addScheduleText(data, 'emby_time_range', editConfig.emby_time_range, 'Emby 时间范围');
          addScheduleText(data, 'emby_interval_days', editConfig.emby_interval_days, 'Emby 间隔天数');
          await EK.API.updateConfig(data);
          message.success('配置已保存');
          await loadData();
        } catch (e) { message.error(e.message); }
        finally { saving.value = false; }
      }

      async function handleSaveAutomation() {
        savingAutomation.value = true;
        try {
          const schedules = automationForm.registrar_schedules.map((item, index) => {
            const bot = normalizeBotForConfig(item.bot_username);
            if (!bot) throw new Error(`第 ${index + 1} 个抢注 Bot 不能为空`);
            const schedule = {
              bot_username: bot,
              mode: item.mode || 'times',
              timeout: optionalPositiveInteger(item.timeout, '抢注超时秒数'),
              retries: optionalNonNegativeInteger(item.retries, '抢注重试次数'),
            };
            if (schedule.mode === 'interval') {
              schedule.interval_minutes = optionalPositiveInteger(item.interval_minutes, '抢注间隔分钟');
            } else {
              const times = splitListText(item.times_text);
              if (!times.length) throw new Error(`第 ${index + 1} 个抢注 Bot 缺少时间`);
              schedule.times = times;
            }
            return schedule;
          });
          await EK.API.updateAutomationConfig({
            checkiner_sites: automationForm.checkiner_sites || [],
            checkiner_time_range: optionalText(automationForm.checkiner_time_range),
            checkiner_interval_days: optionalText(automationForm.checkiner_interval_days),
            checkiner_timeout: optionalPositiveInteger(automationForm.checkiner_timeout, '签到超时秒数'),
            checkiner_retries: optionalNonNegativeInteger(automationForm.checkiner_retries, '签到重试次数'),
            checkiner_concurrency: optionalPositiveInteger(automationForm.checkiner_concurrency, '签到并发数'),
            checkiner_random_start: optionalNonNegativeInteger(automationForm.checkiner_random_start, '随机延迟分钟'),
            registrar_concurrency: optionalPositiveInteger(automationForm.registrar_concurrency, '抢注并发数'),
            registrar_schedules: schedules,
          });
          message.success('自动化配置已保存');
          await loadData();
        } catch (e) { message.error(e.message); }
        finally { savingAutomation.value = false; }
      }

      function notifierPayload() {
        const data = {
          enabled: notifierForm.enabled,
          method: notifierForm.method,
        };
        if (notifierForm.method === 'telegram') {
          const botToken = optionalText(notifierForm.telegram_bot_token);
          const chatId = optionalText(notifierForm.telegram_chat_id);
          const existing = notifierData.value || {};
          const existingChatId = existing.telegram_chat_id || null;
          const existingTelegram = existing.configured && existing.method === 'telegram';
          if (botToken) {
            if (!chatId) throw new Error('Telegram Chat ID 不能为空');
            data.telegram_bot_token = botToken;
            data.telegram_chat_id = chatId;
          } else if (!existingTelegram) {
            throw new Error('配置 Telegram 通知时必须输入 Bot Token 和 Chat ID');
          } else if (chatId && chatId !== existingChatId) {
            throw new Error('修改 Telegram Chat ID 时必须重新输入 Bot Token');
          }
        } else {
          data.apprise_uri = optionalText(notifierForm.apprise_uri);
        }
        return data;
      }

      async function handleSaveNotifier() {
        savingNotifier.value = true;
        try {
          notifierData.value = await EK.API.updateNotifier(notifierPayload());
          message.success('通知配置已保存');
          await loadData();
        } catch (e) { message.error(e.message); }
        finally { savingNotifier.value = false; }
      }

      async function handleTestNotifier() {
        testingNotifier.value = true;
        try {
          await EK.API.testNotifier(notifierPayload());
          message.success('测试通知已发送');
        } catch (e) { message.error(e.message); }
        finally { testingNotifier.value = false; }
      }

      async function handleExportConfig() {
        exportingBackup.value = true;
        try {
          const data = await EK.API.exportConfig();
          const stamp = new Date().toISOString().replace(/[:.]/g, '-');
          downloadJson(`emby-keeper-redacted-${stamp}.json`, data);
          message.success('脱敏快照已下载');
        } catch (e) { message.error(e.message); }
        finally { exportingBackup.value = false; }
      }

      async function handleCreateBackup() {
        creatingBackup.value = true;
        try {
          const res = await EK.API.backupConfig();
          message.success(`本地备份已创建：${res.backup_dir}`);
          await loadData();
        } catch (e) { message.error(e.message); }
        finally { creatingBackup.value = false; }
      }

      async function handleRestoreBackup(id) {
        restoringBackup.value = id;
        try {
          const res = await EK.API.restoreBackup(id);
          message.success(`已恢复 ${res.restored_files.join(', ')}`);
          await loadData();
        } catch (e) { message.error(e.message); }
        finally { restoringBackup.value = ''; }
      }

      onMounted(() => {
        loadData();
        window.addEventListener('resize', updateCompactState);
      });
      onUnmounted(() => {
        window.removeEventListener('resize', updateCompactState);
      });
      return { configData, automationData, healthData, notifierData, backups, backupColumns, loading, saving, savingAutomation, savingNotifier, testingNotifier, exportingBackup, creatingBackup, restoringBackup, editConfig, automationForm, notifierForm, checkinerSiteOptions, registrarModeOptions, descriptionColumns, loadData, handleSave, handleSaveAutomation, addRegistrarSchedule, removeRegistrarSchedule, handleSaveNotifier, handleTestNotifier, handleExportConfig, handleCreateBackup, handleRestoreBackup };
    }
  };
}());
