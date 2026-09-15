<script setup>
/**
 * 蒸馏审计（codebase_runs，D12）。
 *
 * batch 不走收件箱 → 无 learning_logs，故审计落在 run 粒度：
 * 卡 metadata.run_id → run → commit SHA → 文件，审计链在此闭合。
 * files_excluded 与 cards_skipped_protected 是体积治理与修订保护的可见证据。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'

const runs = ref([])
const loading = ref(false)
const error = ref('')
const page = ref(1)

const totals = computed(() => ({
  runs: runs.value.length,
  tokens: runs.value.reduce((s, r) => s + (r.tokens_used || 0), 0),
  cards: runs.value.reduce((s, r) => s + (r.cards_written || 0), 0),
  protectedSkips: runs.value.reduce((s, r) => s + (r.cards_skipped_protected || 0), 0),
}))

async function load() {
  loading.value = true
  error.value = ''
  try {
    runs.value = await api.listRuns(page.value)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function statusClass(s) {
  return { succeeded: 'ok', failed: 'danger', running: 'warn' }[s] || ''
}

function duration(r) {
  if (!r.started_at || !r.finished_at) return '-'
  const ms = new Date(r.finished_at) - new Date(r.started_at)
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function go(delta) {
  const next = page.value + delta
  if (next < 1) return
  page.value = next
  load()
}

onMounted(load)
</script>

<template>
  <div class="head">
    <h1>蒸馏审计</h1>
    <button @click="load" :disabled="loading">{{ loading ? '加载中…' : '刷新' }}</button>
  </div>

  <div class="card">
    <div class="stat">
      <div><span>run 数</span><b>{{ totals.runs }}</b></div>
      <div><span>累计 token</span><b>{{ totals.tokens.toLocaleString() }}</b></div>
      <div><span>写入知识卡</span><b>{{ totals.cards }}</b></div>
      <div><span>修订保护生效</span><b style="color: var(--warn)">{{ totals.protectedSkips }}</b></div>
    </div>
    <p class="muted" style="font-size: 12px; margin: 12px 0 0">
      <span class="mono">batch</span> 是初次全量生成（不走收件箱，故只有 run 级审计）；
      <span class="mono">incremental</span> 是指纹 diff 后的单模块重生成（同时写 learning_logs）。
      token 受 Space 级硬预算约束（<span class="mono">config.codebase_token_budget</span>，默认 300k）。
    </p>
  </div>

  <p v-if="error" class="error">{{ error }}</p>

  <div class="card" style="padding: 0">
    <table v-if="runs.length">
      <thead>
        <tr>
          <th>开始时间</th>
          <th>模式</th>
          <th>commit</th>
          <th>文件（有效/排除）</th>
          <th>卡片（写入/保护跳过）</th>
          <th>token</th>
          <th>耗时</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in runs" :key="r.id">
          <td class="mono">{{ (r.started_at || '').replace('T', ' ').slice(0, 19) }}</td>
          <td><span class="badge" :class="{ accent: r.mode === 'batch' }">{{ r.mode }}</span></td>
          <td class="mono">{{ (r.commit_sha || '-').slice(0, 8) }}</td>
          <td class="mono">{{ r.files_scanned }} / {{ r.files_excluded }}</td>
          <td class="mono">
            {{ r.cards_written }}
            <span v-if="r.cards_skipped_protected" style="color: var(--warn)">
              / {{ r.cards_skipped_protected }}
            </span>
            <span v-else class="muted">/ 0</span>
          </td>
          <td class="mono">{{ (r.tokens_used || 0).toLocaleString() }}</td>
          <td class="mono">{{ duration(r) }}</td>
          <td>
            <span class="badge" :class="statusClass(r.status)">{{ r.status }}</span>
            <div v-if="r.error_message" class="muted truncate" style="font-size: 12px">
              {{ r.error_message }}
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else-if="!loading" class="empty">
      暂无蒸馏记录。运行 <span class="mono">gleanmem-distill run &lt;repo&gt; --key …</span> 后可见。
    </div>
  </div>

  <div class="row">
    <button @click="go(-1)" :disabled="page === 1 || loading">上一页</button>
    <span class="muted">第 {{ page }} 页</span>
    <button @click="go(1)" :disabled="runs.length < 50 || loading">下一页</button>
  </div>
</template>
