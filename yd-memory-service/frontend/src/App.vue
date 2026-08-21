<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  getAdminKey,
  getKey,
  getRole,
  hasKey,
  setAdminKey,
  setKey,
  setRole,
} from './api/client'

const route = useRoute()
const router = useRouter()

const loggedIn = ref(false)
const keyHint = ref('')
const role = ref(getRole())
// 登录 / 进入空间 / 返回管理台 都会改 route.name，借此重读 role（sessionStorage 非响应式）。
// 用 watch+immediate 而非 computed：route 是 shallowReactive，computed 追踪 route.name 会缓存失效
// （getter 返回 true 但 computed 恒为 false，导致侧边栏不渲染）。
watch(
  () => route.name,
  () => {
    role.value = getRole()
    loggedIn.value = hasKey() && route.name !== 'login'
    const k = getKey()
    keyHint.value = k ? `${k.slice(0, 8)}…` : ''
  },
  { immediate: true }
)

const adminLinks = [
  { name: 'spaces', label: '空间管理' },
  { name: 'docs', label: '文档' },
  { name: 'users', label: '用户管理', disabled: true, hint: '开发中' },
]
const spaceLinks = [
  { name: 'memories', label: '记忆与审核' },
  { name: 'logs', label: '学习日志' },
  { name: 'cards', label: '代码库知识卡' },
  { name: 'runs', label: '蒸馏审计' },
  { name: 'recall', label: '检索预览' },
]
const links = computed(() => (role.value === 'admin' ? adminLinks : spaceLinks))
const canBackToAdmin = computed(() => role.value === 'space' && Boolean(getAdminKey()))

function logout() {
  setKey('')
  setRole('space')
  setAdminKey('')
  router.push({ name: 'login' })
}

function backToAdmin() {
  setKey(getAdminKey())
  setRole('admin')
  router.push({ name: 'spaces' })
}
</script>

<template>
  <div class="layout">
    <aside v-if="loggedIn" class="sidebar">
      <div class="brand">
        yd-memory-service
        <small>{{ role === 'admin' ? '平台管理台' : '空间' }} · v0.1</small>
      </div>
      <nav class="nav">
        <template v-for="l in links" :key="l.name">
          <RouterLink
            v-if="!l.disabled"
            :to="{ name: l.name }"
            :class="{ active: route.name === l.name }"
          >{{ l.label }}</RouterLink>
          <span v-else class="nav-disabled" :title="l.hint || ''">
            {{ l.label }} <small>{{ l.hint }}</small>
          </span>
        </template>
      </nav>
      <div style="margin-top: 20px; padding: 0 8px">
        <div class="muted mono" style="margin-bottom: 8px">key {{ keyHint }}</div>
        <div class="row" style="gap: 6px">
          <button v-if="canBackToAdmin" @click="backToAdmin">返回管理台</button>
          <button @click="logout">退出</button>
        </div>
      </div>
    </aside>

    <main class="main">
      <RouterView />
    </main>
  </div>
</template>
