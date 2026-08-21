import { createRouter, createWebHashHistory } from 'vue-router'
import { getRole, hasKey } from '../api/client'

const routes = [
  { path: '/', redirect: '/memories' },
  {
    path: '/spaces',
    name: 'spaces',
    component: () => import('../views/SpacesView.vue'),
    meta: { title: '空间管理' },
  },
  {
    path: '/docs',
    name: 'docs',
    component: () => import('../views/DocsView.vue'),
    meta: { title: '文档' },
  },
  {
    path: '/memories',
    name: 'memories',
    component: () => import('../views/MemoriesView.vue'),
    meta: { title: '记忆与审核' },
  },
  {
    path: '/logs',
    name: 'logs',
    component: () => import('../views/LogsView.vue'),
    meta: { title: '学习日志' },
  },
  {
    path: '/cards',
    name: 'cards',
    component: () => import('../views/CardsView.vue'),
    meta: { title: '代码库知识卡' },
  },
  {
    path: '/runs',
    name: 'runs',
    component: () => import('../views/RunsView.vue'),
    meta: { title: '蒸馏审计' },
  },
  {
    path: '/recall',
    name: 'recall',
    component: () => import('../views/RecallView.vue'),
    meta: { title: '检索预览' },
  },
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
]

const router = createRouter({
  // hash 模式：无需后端配置 history fallback
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  if (to.name !== 'login' && !hasKey()) return { name: 'login' }

  // 角色分区：admin 只在管理台（spaces/users），space 只在业务视图
  const role = getRole()
  const adminOnly = ['spaces', 'docs', 'users']
  const spaceOnly = ['memories', 'logs', 'cards', 'runs', 'recall']
  if (role === 'admin' && spaceOnly.includes(to.name)) return { name: 'spaces' }
  if (role === 'space' && adminOnly.includes(to.name)) return { name: 'memories' }
  return true
})

export default router
