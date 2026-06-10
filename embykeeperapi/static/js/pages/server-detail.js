/* Emby Keeper WebUI — Server detail page. Registers EK.pages.ServerDetailPage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, reactive, onMounted } = Vue;
  const { useMessage } = naiveUI;

  EK.pages.ServerDetailPage = {
    template: `
      <div class="page-container">
        <n-spin :show="loading">
          <n-card v-if="server" :title="server.name || server.id">
            <template #header-extra>
              <n-space>
                <n-tag :type="server.enabled ? 'success' : 'default'" round>{{ server.enabled ? '启用' : '禁用' }}</n-tag>
                <n-button text @click="$router.push('/servers/' + EK.API.encodeId(server.id) + '/edit')">编辑</n-button>
                <n-button text @click="$router.back()">返回</n-button>
              </n-space>
            </template>

            <n-descriptions label-placement="left" :column="2" bordered>
              <n-descriptions-item label="服务器地址">{{ server.url }}</n-descriptions-item>
              <n-descriptions-item label="用户名">{{ server.username }}</n-descriptions-item>
              <n-descriptions-item label="认证方式">
                <n-tag :type="server.auth_method === 'token' ? 'success' : 'warning'" size="small">
                  {{ server.auth_method === 'token' ? 'Token' : '密码' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="Token 状态">
                <n-tag :type="server.has_token ? 'success' : 'warning'" size="small">
                  {{ server.has_token ? '已配置' : '未配置' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="在线状态">
                <span class="status-dot" :class="server.is_online ? 'online' : 'offline'" />
                {{ server.is_online ? '在线' : '离线' }}
              </n-descriptions-item>
              <n-descriptions-item label="运行状态">
                <n-tag :type="server.is_running ? 'warning' : 'info'" size="small">
                  {{ server.is_running ? '运行中' : '空闲' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="播放时长">
                {{ Array.isArray(server.time) ? server.time[0] + '-' + server.time[1] + '秒' : (server.time || '-') + '秒' }}
              </n-descriptions-item>
              <n-descriptions-item label="允许多视频">{{ server.allow_multiple ? '是' : '否' }}</n-descriptions-item>
              <n-descriptions-item label="保活间隔">{{ server.interval_days || '跟随全局' }}</n-descriptions-item>
              <n-descriptions-item label="时间范围">{{ server.time_range || '跟随全局' }}</n-descriptions-item>
              <n-descriptions-item label="使用代理">{{ server.use_proxy ? '是' : '否' }}</n-descriptions-item>
              <n-descriptions-item label="User-Agent">{{ server.useragent || '默认' }}</n-descriptions-item>
              <n-descriptions-item label="客户端">{{ server.client || '默认' }}</n-descriptions-item>
              <n-descriptions-item label="客户端版本">{{ server.client_version || '默认' }}</n-descriptions-item>
              <n-descriptions-item label="设备名称">{{ server.device || '默认' }}</n-descriptions-item>
              <n-descriptions-item label="设备 ID">{{ server.device_id || '默认' }}</n-descriptions-item>
            </n-descriptions>

            <n-divider />

            <n-space justify="center" :size="12">
              <n-button type="primary" :loading="actionLoading.watch" @click="handleWatch">立即保活</n-button>
              <n-button :loading="actionLoading.login" @click="handleLogin">登录测试</n-button>
              <n-button v-if="server.is_running" type="warning" :loading="actionLoading.cancel" @click="handleCancel">取消任务</n-button>
              <n-button @click="handleToggle(!server.enabled)">
                {{ server.enabled ? '禁用' : '启用' }}
              </n-button>
              <n-popconfirm @positive-click="handleDelete">
                <template #trigger><n-button type="error">删除</n-button></template>
                确定删除此服务器吗？此操作不可恢复
              </n-popconfirm>
            </n-space>
          </n-card>
        </n-spin>
      </div>
    `,
    setup() {
      const router = VueRouter.useRouter();
      const route = VueRouter.useRoute();
      const message = useMessage();
      const server = ref(null);
      const loading = ref(true);
      const actionLoading = reactive({ watch: false, login: false, cancel: false });

      async function loadData() {
        try {
          server.value = await EK.API.getServer(route.params.id);
        } catch (e) { message.error(e.message); router.replace('/'); }
        finally { loading.value = false; }
      }

      async function handleWatch() {
        actionLoading.watch = true;
        try {
          const res = await EK.API.triggerWatch(route.params.id);
          message.success(res && res.message ? res.message : '保活已启动');
          await loadData();
        }
        catch (e) { message.error(e.message); }
        finally { actionLoading.watch = false; }
      }

      async function handleLogin() {
        actionLoading.login = true;
        try {
          const res = await EK.API.triggerLogin(route.params.id);
          message.success(res.message || '登录测试已完成');
          await loadData();
        }
        catch (e) { message.error(e.message); }
        finally { actionLoading.login = false; }
      }

      async function handleCancel() {
        actionLoading.cancel = true;
        try {
          await EK.API.cancelWatch(route.params.id);
          message.success('已请求取消任务');
          await loadData();
        } catch (e) { message.error(e.message); }
        finally { actionLoading.cancel = false; }
      }

      async function handleToggle(enabled) {
        try {
          await EK.API.toggleServer(route.params.id, enabled);
          message.success(enabled ? '已启用' : '已禁用');
          await loadData();
        } catch (e) { message.error(e.message); }
      }

      async function handleDelete() {
        try {
          await EK.API.deleteServer(route.params.id);
          message.success('服务器已删除');
          router.replace('/');
        } catch (e) { message.error(e.message); }
      }

      onMounted(loadData);
      return { EK, server, loading, actionLoading, handleWatch, handleLogin, handleCancel, handleToggle, handleDelete };
    }
  };
}());
