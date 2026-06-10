/* Emby Keeper WebUI — AppLayout + router + bootstrap.
 * Loaded last: assembles EK.pages.* into routes, mounts the Vue app.
 * Build-free: classic script, IIFE-scoped.
 */
(function () {
  'use strict';
  const EK = (window.EK = window.EK || {});
  const { createApp, computed, h } = Vue;
  const { useRouter, useRoute, createRouter, createWebHashHistory } = VueRouter;
  const { zhCN, dateZhCN } = naiveUI;
  const pages = EK.pages || {};

  // ---- Inline SVG nav icons (stroke currentColor) ----
  const ICONS = {
    dashboard: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
    schedule: 'M12 7v5l3 3M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
    runs: 'M4 5h16M4 12h16M4 19h10',
    registrar: 'M12 2l2.4 7.4H22l-6 4.5 2.3 7.1L12 16.7 5.7 21l2.3-7.1-6-4.5h7.6L12 2z',
    config: 'M10.3 3.2a1 1 0 0 1 1-.8h1.4a1 1 0 0 1 1 .8l.3 1.6a7 7 0 0 1 1.7 1l1.5-.6a1 1 0 0 1 1.2.4l.7 1.2a1 1 0 0 1-.2 1.3l-1.2 1a7 7 0 0 1 0 2l1.2 1a1 1 0 0 1 .2 1.3l-.7 1.2a1 1 0 0 1-1.2.4l-1.5-.6a7 7 0 0 1-1.7 1l-.3 1.6a1 1 0 0 1-1 .8h-1.4a1 1 0 0 1-1-.8l-.3-1.6a7 7 0 0 1-1.7-1l-1.5.6a1 1 0 0 1-1.2-.4l-.7-1.2a1 1 0 0 1 .2-1.3l1.2-1a7 7 0 0 1 0-2l-1.2-1a1 1 0 0 1-.2-1.3l.7-1.2a1 1 0 0 1 1.2-.4l1.5.6a7 7 0 0 1 1.7-1l.3-1.6zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  };
  const NavIcon = {
    props: { name: String },
    render() {
      return h('svg', {
        class: 'nav-ico', viewBox: '0 0 24 24', fill: 'none',
        stroke: 'currentColor', 'stroke-width': '1.8',
        'stroke-linecap': 'round', 'stroke-linejoin': 'round',
      }, [h('path', { d: ICONS[this.name] || '' })]);
    },
  };

  const NAV_ITEMS = [
    { path: '/', icon: 'dashboard', label: '仪表盘', short: '仪表盘' },
    { path: '/schedule', icon: 'schedule', label: '调度', short: '调度' },
    { path: '/runs', icon: 'runs', label: '运行历史', short: '日志' },
    { path: '/registrar', icon: 'registrar', label: '一键抢注', short: '抢注' },
    { path: '/config', icon: 'config', label: '配置', short: '配置' },
  ];

  const AppLayout = {
    components: { NavIcon },
    template: `
      <div class="app-shell">
        <aside class="sidebar">
          <div class="sidebar-brand">
            <div class="sidebar-logo">EK</div>
            <div class="brand-block">
              <span class="header-title">Emby Keeper</span>
              <span class="header-subtitle">运维控制台</span>
            </div>
          </div>
          <nav class="sidebar-nav">
            <div class="nav-section-label">导航</div>
            <n-button v-for="item in navItems" :key="item.path" text class="nav-button"
              :class="{ active: isActiveRoute(item.path) }" @click="$router.push(item.path)">
              <nav-icon :name="item.icon" />
              <span>{{ item.label }}</span>
            </n-button>
          </nav>
          <div class="sidebar-footer">
            <n-button block secondary type="error" @click="handleLogout">退出登录</n-button>
          </div>
        </aside>
        <main class="main-shell">
          <header class="header-bar">
            <div class="mobile-nav">
              <n-button v-for="item in navItems" :key="item.path" text class="nav-button"
                :class="{ active: isActiveRoute(item.path) }" @click="$router.push(item.path)">{{ item.short }}</n-button>
            </div>
            <div class="brand-block">
              <n-text class="page-kicker">当前页面</n-text>
              <n-text class="section-title">{{ activeTitle }}</n-text>
            </div>
            <div class="header-actions">
              <n-button size="small" @click="$router.push('/servers/new')">新增服务器</n-button>
              <n-button size="small" secondary type="error" @click="handleLogout">退出</n-button>
            </div>
          </header>
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </main>
      </div>
    `,
    setup() {
      const router = useRouter();
      const route = useRoute();
      const activeTitle = computed(() => {
        const current = route.path;
        if (current === '/' || current.startsWith('/servers')) return '仪表盘 / 服务器';
        if (current.startsWith('/schedule')) return '调度';
        if (current.startsWith('/runs')) return '运行日志';
        if (current.startsWith('/registrar')) return '一键抢注';
        if (current.startsWith('/config')) return '配置';
        return '控制台';
      });
      function isActiveRoute(path) {
        const current = route.path;
        if (path === '/') return current === '/' || current.startsWith('/servers');
        return current === path || current.startsWith(`${path}/`);
      }
      function handleLogout() {
        sessionStorage.removeItem('ek_jwt');
        router.replace('/login');
      }
      return { navItems: NAV_ITEMS, activeTitle, isActiveRoute, handleLogout };
    },
  };

  // ---- Routes ----
  const routes = [
    { path: '/login', component: pages.LoginPage },
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', component: pages.DashboardPage },
        { path: 'servers', component: pages.DashboardPage },
        { path: 'servers/new', component: pages.ServerFormPage },
        { path: 'servers/:id', component: pages.ServerDetailPage },
        { path: 'servers/:id/edit', component: pages.ServerFormPage },
        { path: 'schedule', component: pages.SchedulePage },
        { path: 'registrar', component: pages.RegistrarPage },
        { path: 'runs', component: pages.RunHistoryPage },
        { path: 'config', component: pages.ConfigPage },
      ],
    },
  ];

  function directRouteFromPathname() {
    const basePath = typeof window.EK_BASE_PATH === 'string' ? window.EK_BASE_PATH : '';
    let path = window.location.pathname || '/';
    if (basePath && path.startsWith(basePath)) {
      path = path.slice(basePath.length) || '/';
    }
    path = path.replace(/\/$/, '') || '/';
    if (path === '/' || path === '/login' || path === '/schedule' || path === '/runs' || path === '/config' || path === '/registrar') {
      return path;
    }
    if (path === '/servers' || path.startsWith('/servers/')) {
      return path;
    }
    return null;
  }

  function directRouteHashUrl(route) {
    const basePath = typeof window.EK_BASE_PATH === 'string' ? window.EK_BASE_PATH : '';
    const base = basePath || '/';
    return `${base}#${route}`;
  }

  // Capture direct SPA routes before hash history normalizes an empty hash to #/.
  const directRoute = !window.location.hash ? directRouteFromPathname() : null;
  if (directRoute && directRoute !== '/') {
    window.history.replaceState(window.history.state, '', directRouteHashUrl(directRoute));
  }

  const router = createRouter({
    history: createWebHashHistory(window.EK_BASE_PATH || '/'),
    routes,
  });

  // Navigation guard: check auth
  router.beforeEach((to, from, next) => {
    const token = sessionStorage.getItem('ek_jwt');
    if (to.path === '/login') {
      next();
    } else if (!token) {
      next('/login');
    } else {
      next();
    }
  });

  if (directRoute && directRoute !== '/') {
    router.replace(directRoute);
  }

  const app = createApp({
    setup() {
      return { zhCN, dateZhCN };
    },
    template: `
      <n-config-provider :theme="null" :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
        <n-message-provider>
          <n-notification-provider>
            <n-dialog-provider>
              <router-view />
            </n-dialog-provider>
          </n-notification-provider>
        </n-message-provider>
      </n-config-provider>
    `,
    data() {
      return {
        themeOverrides: {
          common: {
            primaryColor: '#4f46e5',
            primaryColorHover: '#4338ca',
            primaryColorPressed: '#3730a3',
            primaryColorSuppl: '#4f46e5',
            borderRadius: '9px',
            borderRadiusSmall: '7px',
            fontWeightStrong: '700',
          },
        },
      };
    },
  });
  app.use(router);

  // Register all Naive UI components globally (templates use kebab-case n-* tags).
  for (const [name, comp] of Object.entries(naiveUI)) {
    if (name.startsWith('N') && comp) {
      app.component(name, comp);
    }
  }

  app.mount('#app');
}());
