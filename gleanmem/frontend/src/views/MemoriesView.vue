<script setup>
/**
 * 记忆与审核（N6 的可视化）。
 *
 * 核心展示：review_status 如何决定「能否被召回」——
 * pattern 类必须 approved 才进召回，其他类型 pending 即可。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'

const memories = ref([])
const loading = ref(false)
const error = ref('')
const statusFilter = ref('')
const acting = ref('')

const counts = computed(() => {
  const c = { total: memories.value.length, recallable: 0, pending: 0, moderated: 0 }
  for (const m of memories.value) {
    if (m.recallable) c.recallable++
    if (m.review_status === 'pending') c.pending++
    if (m.type === 'pattern') c.moderated++
  }
  return c
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    memories.value = await api.listMemories(
      statusFilter.value ? { status: statusFilter.value } : {}
    )
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function review(m, status) {
  acting.value = m.id
  error.value = ''
  try {
    const updated = await api.reviewMemory(m.id, status)
    m.review_status = updated.review_status
    m.recallable = updated.recallable
  } catch (e) {
    error.value = e.message
  } finally {
    acting.value = ''
  }
}

function statusClass(s) {
  return { approved: 'ok', flagged: 'danger', deprecated: 'danger', pending: 'warn' }[s] || ''
}

onMounted(load)
</script>

<template>
  <div class="head">
    <h1>记忆与审核</h1>
    <div class="row">
      <select v-model="statusFilter" @change="load">
        <option value="">全部状态</option>
        <option value="pending">pending</option>
        <option value="approved">approved</option>
        <option value="flagged">flagged</option>
        <option value="deprecated">deprecated</option>
      </select>
      <button @click="load" :disabled="loading">{{ loading ? '加载中…' : '刷新' }}</button>
    </div>
  </div>

  <div class="card">
    <div class="stat">
      <div><span>记忆总数</span><b>{{ counts.total }}</b></div>
      <div><span>可被召回</span><b style="color: var(--ok)">{{ counts.recallable }}</b></div>
      <div><span>待审核</span><b style="color: var(--warn)">{{ counts.pending }}</b></div>
      <div><span>归纳类（须审核）</span><b>{{ counts.moderated }}</b></div>
    </div>
    <p class="muted" style="font-size: 12px; margin: 12px 0 0">
      审核纪律（N6）：<span class="mono">pattern</span> 类必须 approved 才进入召回（LLM 归纳是幻觉高发区）；
      其他类型 pending 即可召回；<span class="mono">flagged</span> /
      <span class="mono">deprecated</span> 任何类型都不召回。
    </p>
  </div>

  <p v-if="error" class="error">{{ error }}</p>

  <div class="card" style="padding: 0">
    <table v-if="memories.length">
      <thead>
        <tr>
          <th>标题</th>
          <th>类型</th>
          <th>权重</th>
          <th>审核状态</th>
          <th>可召回</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="m in memories" :key="m.id">
          <td>
            <div>{{ m.title }}</div>
            <div class="muted truncate" style="font-size: 12px">{{ m.summary }}</div>
          </td>
          <td>
            <span class="badge" :class="{ accent: m.type === 'pattern' }">{{ m.type }}</span>
          </td>
          <td class="mono">{{ m.weight }}</td>
          <td><span class="badge" :class="statusClass(m.review_status)">{{ m.review_status }}</span></td>
          <td>
            <span v-if="m.recallable" class="badge ok">是</span>
            <span v-else class="badge danger">否</span>
          </td>
          <td>
            <div class="row">
              <button
                class="ok"
                :disabled="acting === m.id || m.review_status === 'approved'"
                @click="review(m, 'approved')"
              >通过</button>
              <button
                class="danger"
                :disabled="acting === m.id || m.review_status === 'flagged'"
                @click="review(m, 'flagged')"
              >标记</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else-if="!loading" class="empty">
      该 Space 暂无记忆。可通过 <span class="mono">POST /api/v1/learning/events</span> 提交素材后 flush。
    </div>
  </div>
</template>
