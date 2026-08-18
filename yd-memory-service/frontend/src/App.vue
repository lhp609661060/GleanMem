<script setup>
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { getKey, hasKey, setKey } from './api/client'

const route = useRoute()
const router = useRouter()

const loggedIn = computed(() => hasKey() && route.name !== 'login')
// 只显示 key 前缀（够辨认，不足以复用）
const keyHint = computed(() => {
  const k = getKey()
  return k ? `${k.slice(0, 8)}…` : ''
})

const links = [
  { name: 'memories', label: '记忆与审核' },
  { name: 'logs', label: '学习日志' },
  { name: 'cards', label: '代码库知识卡' },
  { name: 'runs', label: '蒸馏审计' },
  { name: 'recall', label: '检索预览' },
]

function logout() {
  setKey('')
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="layout">
    <aside v-if="loggedIn" class="sidebar">
      <div class="brand">
        yd-memory-service
        <small>管理端 v0.1</small>
      </div>
      <nav class="nav">
        <RouterLink
          v-for="l in links"
          :key="l.name"
          :to="{ name: l.name }"
          :class="{ active: route.name === l.name }"
        >{{ l.label }}</RouterLink>
      </nav>
      <div style="margin-top: 20px; padding: 0 8px">
        <div class="muted mono" style="margin-bottom: 8px">key {{ keyHint }}</div>
        <button @click="logout">退出</button>
      </div>
    </aside>

    <main class="main">
      <RouterView />
    </main>
  </div>
</template>
