import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'bookshelf',
      component: () => import('@/views/BookshelfView.vue'),
    },
    {
      path: '/books/:id',
      name: 'book-detail',
      component: () => import('@/views/BookDetailView.vue'),
      props: true,
    },
    {
      path: '/stats',
      name: 'stats',
      component: () => import('@/views/StatsView.vue'),
    },
    {
      path: '/overview',
      name: 'overview',
      component: () => import('@/views/OverviewView.vue'),
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

export default router
