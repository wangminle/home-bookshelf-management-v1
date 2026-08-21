import { createRouter, createWebHistory } from 'vue-router'
import { probeSession } from '@/stores/session'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'bookshelf',
      component: () => import('@/views/BookshelfView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/books/:id',
      name: 'book-detail',
      component: () => import('@/views/BookDetailView.vue'),
      props: true,
      meta: { requiresAuth: true },
    },
    {
      path: '/stats',
      name: 'stats',
      component: () => import('@/views/StatsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/overview',
      name: 'overview',
      component: () => import('@/views/OverviewView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 统一登录页（权限阶段 2）：Owner/Member 共用，不内嵌于授权页
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
    },
    {
      // 匿名共享书架（权限阶段 1：C 模式），无需登录
      path: '/shared',
      name: 'shared-bookshelf',
      component: () => import('@/views/SharedBookshelfView.vue'),
    },
    {
      path: '/agent',
      name: 'agent-connect',
      component: () => import('@/views/AgentConnectView.vue'),
    },
    {
      path: '/agent-authorization',
      name: 'agent-authorization',
      component: () => import('@/views/AgentAuthorizationView.vue'),
    },
    {
      path: '/agent-access',
      name: 'agent-access-list',
      component: () => import('@/views/AgentAccessListView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

// 权限阶段 1：业务页面（书架/详情/统计/概览）要求登录；
// 未登录自动降级到匿名共享书架（可信局域网内可直接浏览，基线 §2.1/§10）。
// 会话探测走根路径 /auth/session（CHK-071：此前误用 /api/v1/auth/session 恒 404）；
// 缓存与失效由 stores/session.ts 管理（登录/登出后 invalidateSession）。
router.beforeEach(async (to) => {
  if (!to.meta.requiresAuth) return true
  const authed = await probeSession()
  if (authed) return true
  return { name: 'shared-bookshelf' }
})

export default router
