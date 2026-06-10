/* Emby Keeper WebUI — Login page. Registers EK.pages.LoginPage. */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};
  const { ref, computed, onMounted } = Vue;
  const { useMessage } = naiveUI;

  EK.pages.LoginPage = {
    template: `
      <div class="login-container">
        <n-card class="login-card" :bordered="false">
          <template #header>
            <n-space vertical :size="10">
              <div class="login-brand-mark">EK</div>
              <div>
                <div style="font-size:20px;font-weight:750;color:var(--ek-text-strong)">Emby Keeper</div>
                <n-text depth="3" style="font-size:13px">运维控制台 · 登录</n-text>
              </div>
            </n-space>
          </template>
          <template #header-extra>
            <n-text style="font-size:12px;color:#999">v0.1.0</n-text>
          </template>
          <n-alert v-if="errorMsg" type="error" :bordered="false" style="margin-bottom:16px">{{ errorMsg }}</n-alert>
          <n-spin :show="loadingMethods">
            <n-alert v-if="authMethodsLoaded && !hasAuthMethod" type="warning" :bordered="false">
              当前服务未配置 WebUI 登录方式。请设置 EK_TOKEN 或 EK_WEBPASS 后重启服务。
            </n-alert>
            <n-tabs v-else v-model:value="activeTab" type="segment" animated>
              <n-tab-pane v-if="hasToken" name="token" tab="Token 登录">
                <n-space vertical :size="16">
                  <n-input v-model:value="tokenInput" type="password" show-password-on="click" placeholder="请输入预共享 Token" @keyup.enter="handleTokenLogin" />
                  <n-button type="primary" block :loading="loading" :disabled="!tokenInput.trim()" @click="handleTokenLogin">登录</n-button>
                </n-space>
              </n-tab-pane>
              <n-tab-pane v-if="hasPassword" name="password" tab="密码登录">
                <n-space vertical :size="16">
                  <n-input v-model:value="passwordInput" type="password" show-password-on="click" placeholder="请输入管理密码" @keyup.enter="handlePasswordLogin" />
                  <n-button type="primary" block :loading="loading" :disabled="!passwordInput.trim()" @click="handlePasswordLogin">登录</n-button>
                </n-space>
              </n-tab-pane>
            </n-tabs>
          </n-spin>
        </n-card>
      </div>
    `,
    setup() {
      const router = VueRouter.useRouter();
      const message = useMessage();
      const activeTab = ref('token');
      const tokenInput = ref('');
      const passwordInput = ref('');
      const loading = ref(false);
      const loadingMethods = ref(true);
      const authMethodsLoaded = ref(false);
      const errorMsg = ref('');
      const hasToken = ref(false);
      const hasPassword = ref(false);
      const hasAuthMethod = computed(() => hasToken.value || hasPassword.value);

      onMounted(async () => {
        try {
          const methods = await EK.API.authMethods();
          hasToken.value = methods.token;
          hasPassword.value = methods.password;
          if (methods.token) activeTab.value = 'token';
          else if (methods.password) activeTab.value = 'password';
        } catch (e) {
          errorMsg.value = e.message;
        } finally {
          authMethodsLoaded.value = true;
          loadingMethods.value = false;
        }
      });

      async function handleTokenLogin() {
        if (!tokenInput.value.trim()) return;
        loading.value = true; errorMsg.value = '';
        try {
          const res = await EK.API.tokenExchange(tokenInput.value.trim());
          sessionStorage.setItem('ek_jwt', res.access_token);
          message.success('登录成功');
          router.replace('/');
        } catch (e) { errorMsg.value = e.message; }
        finally { loading.value = false; }
      }

      async function handlePasswordLogin() {
        if (!passwordInput.value.trim()) return;
        loading.value = true; errorMsg.value = '';
        try {
          const res = await EK.API.passwordLogin(passwordInput.value.trim());
          sessionStorage.setItem('ek_jwt', res.access_token);
          message.success('登录成功');
          router.replace('/');
        } catch (e) { errorMsg.value = e.message; }
        finally { loading.value = false; }
      }

      return {
        activeTab, tokenInput, passwordInput, loading, loadingMethods, authMethodsLoaded,
        errorMsg, hasToken, hasPassword, hasAuthMethod, handleTokenLogin, handlePasswordLogin
      };
    }
  };
}());
