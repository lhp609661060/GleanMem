<script setup>
/** 登录：输入 space_key。key 只进 sessionStorage，见 api/client.js。 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, setAdminKey, setKey, setRole } from '../api/client'

const router = useRouter()
const key = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  const trimmed = key.value.trim()
  if (!trimmed) {
    error.value = '请输入 space_key'
    return
  }
  busy.value = true
  setKey(trimmed)
  try {
    // 用一次真实请求验证 key 并探明身份：admin 进管理台，space 进业务视图
    const me = await api.authMe()
    if (me.role === 'admin') {
      setRole('admin')
      setAdminKey(trimmed)
      router.push({ name: 'spaces' })
    } else {
      setRole('space')
      setAdminKey('')
      router.push({ name: 'memories' })
    }
  } catch (e) {
    setKey('')
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div style="max-width: 460px; margin: 12vh auto">
    <div class="card">
      <h1 style="margin: 0 0 6px; font-size: 19px">yd-memory-service 管理端</h1>
      <p class="muted" style="margin-top: 0">
        输入 <span class="mono">admin_key</span> 进入平台管理台（管理所有空间），或输入
        <span class="mono">space_key</span> 进入某个空间。
      </p>

      <form @submit.prevent="submit">
        <input
          v-model="key"
          type="password"
          placeholder="ydm_…"
          autocomplete="off"
          style="width: 100%; margin-bottom: 10px"
        />
        <button class="primary" type="submit" :disabled="busy" style="width: 100%">
          {{ busy ? '验证中…' : '进入' }}
        </button>
      </form>

      <p v-if="error" class="error" style="margin-top: 12px">{{ error }}</p>

      <p class="muted" style="font-size: 12px; margin-bottom: 0">
        key 仅存于当前标签页（sessionStorage），关闭即失效；服务端只存其哈希。<br />
        创建 Space：<span class="mono">POST /api/v1/spaces</span>（返回的明文 key 仅显示一次）。
      </p>
    </div>
  </div>
</template>
