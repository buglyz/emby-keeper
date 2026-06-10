/* Emby Keeper WebUI — Server add/edit form page. Registers EK.pages.ServerFormPage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, reactive, computed, onMounted } = Vue;
  const { useMessage } = naiveUI;
  const { trimText, optionalText } = EK.util;

  EK.pages.ServerFormPage = {
    template: `
      <div class="page-container">
        <n-card class="form-shell" :title="isEdit ? '编辑服务器' : '添加服务器'">
          <template #header-extra>
            <n-button text @click="$router.back()">返回</n-button>
          </template>
          <n-alert v-if="errorMsg" type="error" :bordered="false" style="margin-bottom:16px">{{ errorMsg }}</n-alert>

          <div class="form-section-title">认证方式</div>
          <n-space vertical :size="12" style="margin-bottom:12px">
            <n-select v-model:value="form.auth_method" :options="authOptions" />
            <n-alert v-if="form.auth_method === 'token'" type="success" :bordered="false">
              推荐：直接提供 AccessToken，更安全，无需密码
            </n-alert>
            <n-alert v-if="form.auth_method === 'password'" type="warning" :bordered="false">
              密码仅用于一次性换取 Token，不会被保存。登录成功后密码立即丢弃
            </n-alert>
            <n-input v-if="form.auth_method === 'token'" v-model:value="form.access_token" type="password" show-password-on="click" placeholder="请输入 AccessToken" />
            <n-input v-if="form.auth_method === 'password'" v-model:value="form.password" type="password" show-password-on="click" placeholder="请输入密码（一次性使用）" />
          </n-space>

          <div class="form-section-title">基本信息</div>
          <n-space vertical :size="12">
            <n-input v-model:value="form.url" placeholder="Emby 服务器地址 (如 https://emby.example.com:8096)" />
            <n-input v-model:value="form.username" placeholder="用户名" />
            <n-input v-model:value="form.name" placeholder="显示名称（可选）" />
            <n-switch v-model:value="form.enabled"><template #checked>启用</template><template #unchecked>禁用</template></n-switch>
          </n-space>

          <div class="form-section-title">保活设置</div>
          <n-space vertical :size="12">
            <n-space :size="12" align="center">
              <n-input-number v-model:value="form.time_min" :min="60" :step="60" :precision="0" placeholder="最小秒数" style="width:120px" />
              <n-text>至</n-text>
              <n-input-number v-model:value="form.time_max" :min="60" :step="60" :precision="0" placeholder="最大秒数" style="width:120px" />
              <n-text>秒</n-text>
            </n-space>
            <n-switch v-model:value="form.allow_multiple"><template #checked>允许多视频播放</template><template #unchecked>仅单视频</template></n-switch>
            <n-switch v-model:value="form.allow_stream"><template #checked>允许无时长流播放</template><template #unchecked>需时长信息</template></n-switch>
          </n-space>

          <div class="form-section-title">调度设置</div>
          <n-space vertical :size="12">
            <n-input v-model:value="form.interval_days" placeholder="保活间隔天数 (如 &lt;7,12&gt; 或 7)" />
            <n-input v-model:value="form.time_range" placeholder="保活时间范围 (如 &lt;11:00AM,11:00PM&gt;)" />
          </n-space>

          <div class="form-section-title">高级设置</div>
          <n-space vertical :size="12">
            <n-switch v-model:value="form.use_proxy"><template #checked>使用代理</template><template #unchecked>不使用代理</template></n-switch>
            <n-input v-model:value="form.play_id" placeholder="指定视频 ID（可选）" />
            <n-input v-model:value="form.useragent" placeholder="User-Agent（可选）" />
            <n-input v-model:value="form.client" placeholder="客户端名称（可选）" />
            <n-input v-model:value="form.client_version" placeholder="客户端版本（可选）" />
            <n-input v-model:value="form.device" placeholder="设备名称（可选）" />
            <n-input v-model:value="form.device_id" placeholder="设备 ID（可选）" />
          </n-space>

          <n-divider />
          <n-space justify="end">
            <n-button @click="$router.back()">取消</n-button>
            <n-button type="primary" :loading="saving" @click="handleSave">{{ isEdit ? '保存' : '创建' }}</n-button>
          </n-space>
        </n-card>
      </div>
    `,
    setup() {
      const router = VueRouter.useRouter();
      const route = VueRouter.useRoute();
      const message = useMessage();
      const isEdit = computed(() => route.params.id && route.params.id !== 'new');
      const saving = ref(false);
      const errorMsg = ref('');
      const originalAuthMethod = ref(null);

      const authOptions = [
        { label: 'Token（推荐，更安全）', value: 'token' },
        { label: '密码（一次性换取 Token）', value: 'password' },
      ];

      const form = reactive({
        url: '', username: '', name: '', auth_method: 'token',
        access_token: '', password: '',
        time_min: 300, time_max: 600,
        allow_multiple: true, allow_stream: false, use_proxy: true,
        enabled: true, play_id: '',
        interval_days: '', time_range: '',
        useragent: '', client: '', client_version: '', device: '', device_id: '',
      });

      onMounted(async () => {
        if (isEdit.value) {
          try {
            const server = await EK.API.getServer(route.params.id);
            form.url = server.url;
            form.username = server.username;
            form.name = server.name || '';
            form.auth_method = server.auth_method || 'token';
            originalAuthMethod.value = form.auth_method;
            form.enabled = server.enabled;
            form.allow_multiple = server.allow_multiple;
            form.allow_stream = server.allow_stream;
            form.use_proxy = server.use_proxy;
            form.play_id = server.play_id || '';
            form.interval_days = server.interval_days || '';
            form.time_range = server.time_range || '';
            form.useragent = server.useragent || '';
            form.client = server.client || '';
            form.client_version = server.client_version || '';
            form.device = server.device || '';
            form.device_id = server.device_id || '';
            if (Array.isArray(server.time)) {
              form.time_min = server.time[0] || 300;
              form.time_max = server.time[1] || 600;
            } else {
              form.time_min = server.time || 300;
              form.time_max = server.time || 300;
            }
          } catch (e) { message.error(e.message); router.replace('/'); }
        }
      });

      function isValidWatchTime(value) {
        return Number.isInteger(value) && value >= 60;
      }

      async function handleSave() {
        saving.value = true; errorMsg.value = '';
        const normalized = {
          url: trimText(form.url),
          username: trimText(form.username),
          name: optionalText(form.name),
          access_token: trimText(form.access_token),
          password: trimText(form.password),
          play_id: optionalText(form.play_id),
          interval_days: optionalText(form.interval_days),
          time_range: optionalText(form.time_range),
          useragent: optionalText(form.useragent),
          client: optionalText(form.client),
          client_version: optionalText(form.client_version),
          device: optionalText(form.device),
          device_id: optionalText(form.device_id),
        };
        if (!normalized.url || !normalized.username) {
          errorMsg.value = '服务器地址和用户名不能为空';
          saving.value = false;
          return;
        }
        if (!isValidWatchTime(form.time_min) || !isValidWatchTime(form.time_max)) {
          errorMsg.value = '播放时长必须是至少 60 秒的整数';
          saving.value = false;
          return;
        }
        if (form.time_min > form.time_max) {
          errorMsg.value = '最小播放时长不能大于最大播放时长';
          saving.value = false;
          return;
        }
        const authChanged = isEdit.value && originalAuthMethod.value && form.auth_method !== originalAuthMethod.value;
        if (form.auth_method === 'token' && !normalized.access_token && (!isEdit.value || authChanged)) {
          errorMsg.value = 'Token 模式下必须提供 AccessToken';
          saving.value = false;
          return;
        }
        if (form.auth_method === 'password' && !normalized.password && (!isEdit.value || authChanged)) {
          errorMsg.value = '密码模式下必须提供密码';
          saving.value = false;
          return;
        }
        const watchTime = form.time_min === form.time_max ? form.time_min : [form.time_min, form.time_max];
        const data = {
          url: normalized.url,
          username: normalized.username,
          name: normalized.name,
          auth_method: form.auth_method,
          time: watchTime,
          allow_multiple: form.allow_multiple,
          allow_stream: form.allow_stream,
          use_proxy: form.use_proxy,
          enabled: form.enabled,
          play_id: normalized.play_id,
          interval_days: normalized.interval_days,
          time_range: normalized.time_range,
          useragent: normalized.useragent,
          client: normalized.client,
          client_version: normalized.client_version,
          device: normalized.device,
          device_id: normalized.device_id,
        };
        if (form.auth_method === 'token' && normalized.access_token) {
          data.access_token = normalized.access_token;
        }
        if (form.auth_method === 'password' && normalized.password) {
          data.password = normalized.password;
        }
        try {
          if (isEdit.value) {
            await EK.API.updateServer(route.params.id, data);
            message.success('服务器已更新');
          } else {
            await EK.API.createServer(data);
            message.success('服务器已创建');
          }
          router.replace('/');
        } catch (e) { errorMsg.value = e.message; }
        finally { saving.value = false; }
      }

      return { form, isEdit, saving, errorMsg, authOptions, handleSave };
    }
  };
}());
