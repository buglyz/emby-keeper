/* Emby Keeper WebUI — Registrar (one-click) page. Registers EK.pages.RegistrarPage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, reactive, computed, onMounted, onUnmounted } = Vue;
  const { useMessage } = naiveUI;
  const { trimText, normalizeBot, hasWhitespace, isIntegerInRange } = EK.util;

  EK.pages.RegistrarPage = {
    template: `
      <div class="page-container">
        <div class="page-header">
          <div>
            <div class="page-kicker">Telegram</div>
            <h1 class="page-title">一键抢注</h1>
            <p class="page-subtitle">选择 Bot、Telegram 账号、注册账号和运行限制后启动任务，任务状态会自动轮询。</p>
          </div>
          <div class="page-actions">
            <n-button text @click="loadAccounts">刷新账号</n-button>
            <n-button @click="$router.push('/runs')">查看运行日志</n-button>
          </div>
        </div>

        <div class="panel registrar-card">
          <n-spin :show="loading">
            <n-alert v-if="accounts.length === 0" type="warning" :bordered="false" style="margin-bottom:16px">
              当前配置中没有 Telegram 账号
            </n-alert>
            <n-alert v-else-if="enabledAccountCount === 0" type="warning" :bordered="false" style="margin-bottom:16px">
              当前配置中没有已启用的 Telegram 账号
            </n-alert>
            <div class="registrar-meta">
              <div class="meta-chip">
                <span>可用账号</span>
                <strong>{{ enabledAccountCount }}/{{ accounts.length }}</strong>
              </div>
              <div class="meta-chip">
                <span>已选择</span>
                <strong>{{ selectedAccountCount }}</strong>
              </div>
              <div class="meta-chip">
                <span>Bot</span>
                <strong>{{ botPreview || '-' }}</strong>
              </div>
            </div>
            <n-form :label-placement="isCompact ? 'top' : 'left'" :label-width="isCompact ? undefined : 110">
              <n-form-item label="Telegram 账号">
                <n-space vertical style="width:100%">
                  <n-select
                    v-model:value="form.telegram_account_ids"
                    :options="accountOptions"
                    multiple
                    filterable
                    :max-tag-count="isCompact ? 1 : 3"
                    placeholder="选择用于抢注的 Telegram 账号"
                    @update:value="handleAccountSelectionChange"
                  />
                  <n-space :size="8">
                    <n-button size="small" text @click="selectAllEnabledAccounts">选择全部启用账号</n-button>
                    <n-button size="small" text @click="clearSelectedAccounts">清空选择</n-button>
                  </n-space>
                  <n-text depth="3" style="font-size:12px">已选择 {{ selectedAccountCount }} 个 / 可用 {{ enabledAccountCount }} 个</n-text>
                </n-space>
              </n-form-item>
              <n-form-item label="Bot">
                <n-input v-model:value="form.bot_username" placeholder="@ExampleBot 或 https://t.me/ExampleBot" />
              </n-form-item>
              <n-form-item label="注册账号">
                <n-input v-model:value="form.username" placeholder="要注册到目标站点的用户名，不允许空格" />
              </n-form-item>
              <n-form-item label="注册密码">
                <div class="field-row">
                  <n-input v-model:value="form.password" type="password" show-password-on="click" placeholder="要注册到目标站点的密码，不允许空格" />
                  <n-button @click="generatePassword">生成</n-button>
                </div>
              </n-form-item>
              <n-form-item label="重试间隔">
                <n-space align="center">
                  <n-input-number class="number-input" v-model:value="form.interval_seconds" :min="1" :max="60" :precision="0" />
                  <n-text>秒</n-text>
                </n-space>
              </n-form-item>
              <n-form-item label="最长运行">
                <n-space align="center">
                  <n-input-number class="number-input" v-model:value="form.timeout_minutes" :min="1" :max="1440" :precision="0" />
                  <n-text>分钟</n-text>
                </n-space>
              </n-form-item>
            </n-form>
            <n-space justify="end">
              <n-button :loading="submitting" :disabled="!canSubmit" type="primary" @click="handleSubmit">开始抢注</n-button>
            </n-space>
            <n-alert v-if="lastRun" :type="runAlertType" :bordered="false" style="margin-top:16px">
              <n-space align="center" justify="space-between">
                <div class="run-line">
                  <span class="run-id">任务：{{ lastRun.run_id }}</span>
                  <n-text v-if="runStatusInfo" depth="3" style="font-size:12px">{{ runStatusInfo }}</n-text>
                </div>
                <n-space align="center">
                  <n-tag size="small" :type="runStatusType">{{ runStatusLabel }}</n-tag>
                  <n-button size="small" text @click="$router.push('/runs')">查看日志</n-button>
                  <n-button v-if="canCancelLastRun" size="small" text type="warning" :loading="canceling" @click="handleCancelLastRun">取消</n-button>
                </n-space>
              </n-space>
            </n-alert>
          </n-spin>
        </div>
      </div>
    `,
    setup() {
      const message = useMessage();
      const accounts = ref([]);
      const loading = ref(true);
      const submitting = ref(false);
      const canceling = ref(false);
      const lastRun = ref(null);
      const runStatus = ref('');
      const runStatusInfo = ref('');
      const isCompact = ref(window.innerWidth < 760);
      const accountSelectionTouched = ref(false);
      let pollTimer = null;
      const botUsernamePattern = /^[A-Za-z][A-Za-z0-9_]{4,31}$/;
      const form = reactive({
        telegram_account_ids: [],
        bot_username: '',
        username: '',
        password: '',
        interval_seconds: 1,
        timeout_minutes: 30,
      });

      const enabledAccountCount = computed(() => accounts.value.filter((account) => account.enabled).length);
      const selectedAccountCount = computed(() => form.telegram_account_ids.length);
      const botPreview = computed(() => normalizeBot(form.bot_username));
      const accountOptions = computed(() => accounts.value.map((account) => ({
        label: `${account.phone_masked}${account.enabled ? '' : '（禁用）'}${account.registrar ? '' : ' / 未设定时抢注'}`,
        value: account.id,
        disabled: !account.enabled,
      })));
      const canSubmit = computed(() => {
        const username = trimText(form.username);
        const password = trimText(form.password);
        return (
          selectedAccountCount.value > 0
          && isValidBotUsername(botPreview.value)
          && !!username
          && !!password
          && !hasWhitespace(username)
          && !hasWhitespace(password)
          && isIntegerInRange(form.interval_seconds, 1, 60)
          && isIntegerInRange(form.timeout_minutes, 1, 1440)
        );
      });
      const canCancelLastRun = computed(() => ['pending', 'initializing', 'running'].includes(runStatus.value));
      const statusLabels = {
        pending: '等待',
        initializing: '初始化',
        running: '运行中',
        success: '成功',
        fail: '失败',
        error: '错误',
        cancelled: '已取消',
      };
      const runStatusLabel = computed(() => statusLabels[runStatus.value] || runStatus.value || '已启动');
      const runStatusType = computed(() => {
        if (runStatus.value === 'success') return 'success';
        if (runStatus.value === 'fail' || runStatus.value === 'error') return 'error';
        if (runStatus.value === 'cancelled') return 'default';
        return 'warning';
      });
      const runAlertType = computed(() => {
        if (runStatus.value === 'success') return 'success';
        if (runStatus.value === 'fail' || runStatus.value === 'error') return 'error';
        if (canCancelLastRun.value) return 'warning';
        return 'info';
      });

      async function loadAccounts() {
        loading.value = true;
        try {
          accounts.value = await EK.API.listRegistrarAccounts();
          normalizeSelectedAccountIds();
          if (!accountSelectionTouched.value && !form.telegram_account_ids.length) {
            form.telegram_account_ids = enabledAccountIds();
          }
        } catch (e) { message.error(e.message); }
        finally { loading.value = false; }
      }

      function isValidBotUsername(value) {
        return botUsernamePattern.test(value || '');
      }

      function updateCompactState() {
        isCompact.value = window.innerWidth < 760;
      }

      function enabledAccountIds() {
        return accounts.value.filter((account) => account.enabled).map((account) => account.id);
      }

      function normalizeSelectedAccountIds() {
        const enabledIds = new Set(enabledAccountIds());
        form.telegram_account_ids = form.telegram_account_ids.filter((accountId) => enabledIds.has(accountId));
      }

      function handleAccountSelectionChange(value) {
        accountSelectionTouched.value = true;
        form.telegram_account_ids = Array.isArray(value) ? value : [];
        normalizeSelectedAccountIds();
      }

      function selectAllEnabledAccounts() {
        accountSelectionTouched.value = true;
        form.telegram_account_ids = enabledAccountIds();
      }

      function clearSelectedAccounts() {
        accountSelectionTouched.value = true;
        form.telegram_account_ids = [];
      }

      function generatePassword() {
        const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
        form.password = Array.from({ length: 12 }, () => alphabet[Math.floor(Math.random() * alphabet.length)]).join('');
      }

      function stopRunPolling() {
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = null;
      }

      async function pollRunStatus() {
        if (!lastRun.value || !lastRun.value.run_id) return;
        stopRunPolling();
        try {
          const run = await EK.API.getRun(lastRun.value.run_id);
          runStatus.value = run.status || '';
          runStatusInfo.value = run.status_info || '';
          if (canCancelLastRun.value) {
            pollTimer = setTimeout(pollRunStatus, 3000);
          } else {
            stopRunPolling();
          }
        } catch (e) {
          stopRunPolling();
        }
      }

      function startRunPolling(initialInfo = '') {
        stopRunPolling();
        runStatus.value = 'running';
        runStatusInfo.value = initialInfo;
        pollTimer = setTimeout(pollRunStatus, 1000);
      }

      async function handleSubmit() {
        const botUsername = normalizeBot(form.bot_username);
        const username = trimText(form.username);
        const password = trimText(form.password);
        if (!form.telegram_account_ids.length) {
          message.error('请选择 Telegram 账号');
          return;
        }
        if (!botUsername || !username || !password) {
          message.error('Bot、注册账号和注册密码不能为空');
          return;
        }
        if (!isValidBotUsername(botUsername)) {
          message.error('Bot 用户名格式不正确');
          return;
        }
        if (hasWhitespace(username) || hasWhitespace(password)) {
          message.error('注册账号和注册密码不能包含空白字符');
          return;
        }
        if (!isIntegerInRange(form.interval_seconds, 1, 60)) {
          message.error('重试间隔必须是 1 到 60 秒之间的整数');
          return;
        }
        if (!isIntegerInRange(form.timeout_minutes, 1, 1440)) {
          message.error('最长运行时间必须是 1 到 1440 分钟之间的整数');
          return;
        }
        submitting.value = true;
        try {
          const res = await EK.API.quickRegister({
            telegram_account_ids: form.telegram_account_ids,
            bot_username: botUsername,
            username,
            password,
            interval_seconds: form.interval_seconds,
            timeout_minutes: form.timeout_minutes,
          });
          lastRun.value = res;
          form.password = '';
          startRunPolling(res && res.message ? res.message : '');
          message.success(res && res.message ? res.message : '抢注任务已启动');
        } catch (e) { message.error(e.message); }
        finally { submitting.value = false; }
      }

      async function handleCancelLastRun() {
        if (!lastRun.value || !lastRun.value.run_id) return;
        canceling.value = true;
        try {
          await EK.API.cancelRegistrarRun(lastRun.value.run_id);
          runStatusInfo.value = '正在取消抢注任务';
          message.success('已请求取消抢注任务');
          await pollRunStatus();
        } catch (e) { message.error(e.message); }
        finally { canceling.value = false; }
      }

      onMounted(() => {
        updateCompactState();
        window.addEventListener('resize', updateCompactState);
        loadAccounts();
      });
      onUnmounted(() => {
        stopRunPolling();
        window.removeEventListener('resize', updateCompactState);
      });
      return { accounts, loading, submitting, canceling, lastRun, form, accountOptions, enabledAccountCount, selectedAccountCount, botPreview, isCompact, canSubmit, canCancelLastRun, runStatusInfo, runStatusLabel, runStatusType, runAlertType, loadAccounts, handleAccountSelectionChange, selectAllEnabledAccounts, clearSelectedAccounts, generatePassword, handleSubmit, handleCancelLastRun };
    }
  };
}());
