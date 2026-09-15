<script setup>
/**
 * 学习日志：审计不变式的展示面。
 *
 * 重点是 llm_raw_response —— 设计要求「llm 模式每次决策必须写入原始输出」，
 * 这一页让该承诺可被肉眼验证。
 */
import { onMounted, ref } from 'vue'
import { api } from '../api/client'

const logs = ref([])
const loading = ref(false)
const flushing = ref(false)
const error = ref('')
const notice = ref('')
const page = ref(1)
const expanded = ref(new Set())

async function load() {
  loading.value = true
  error.value = ''
  try {
    logs.value = await api.listLogs(page.value)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function flush() {
  flushing.value = true
  error.value = ''
  notice.value = ''
  try {
    const r = await api.flush()
    notice.value =
      r.status === 'ok'
        ? `flush 完成：处理 ${r.processed} 条${r.actions?.length ? `（${r.actions.join(', ')}）` : ''}`
        : `flush 跳过：${r.reason || r.status}`
    page.value = 1
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    flushing.value = false
  }
}

function toggle(id) {
  const s = new Set(expanded.value)
  s.has(id) ? s.delete(id) : s.add(id)
  expanded.value = s
}

function actionClass(a) {
  return { store: 'ok', merge: 'accent', failed: 'danger', discard: '' }[a] || ''
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
    <h1>学习日志</h1>
    <div class="row">
      <button @click="flush" :disabled="flushing">
        {{ flushing ? '执行中…' : '手动触发 flush' }}
      </button>
      <button @click="load" :disabled="loading">刷新</button>
    </div>
  </div>

  <div class="card">
    <p class="muted" style="margin: 0; font-size: 12px">
      每条学习决策都留痕：<span class="mono">source</span> 标明素材来源（chat / observation /
      codebase），<span class="mono">llm_raw_response</span> 固化 LLM 原始输出以便追溯幻觉来源。
      展开行可查看原始响应与错误信息。
    </p>
  </div>

  <p v-if="notice" class="card" style="border-color: var(--ok); color: var(--ok)">{{ notice }}</p>
  <p v-if="error" class="error">{{ error }}</p>

  <div class="card" style="padding: 0">
    <table v-if="logs.length">
      <thead>
        <tr>
          <th>时间</th>
          <th>来源</th>
          <th>事件类型</th>
          <th>决策</th>
          <th>产物</th>
          <th>分析器</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="lg in logs" :key="lg.id">
          <tr>
            <td class="mono">{{ (lg.created_at || '').replace('T', ' ').slice(0, 19) }}</td>
            <td><span class="badge">{{ lg.source }}</span></td>
            <td class="mono">{{ lg.event_type }}</td>
            <td><span class="badge" :class="actionClass(lg.decision_action)">{{ lg.decision_action || '-' }}</span></td>
            <td class="mono">{{ lg.decision_target || '-' }}</td>
            <td class="mono">{{ lg.analyzer_mode }}</td>
            <td>
              <button @click="toggle(lg.id)">
                {{ expanded.has(lg.id) ? '收起' : '详情' }}
              </button>
            </td>
          </tr>
          <tr v-if="expanded.has(lg.id)">
            <td colspan="7" style="background: var(--panel-2)">
              <div class="muted" style="font-size: 12px">素材内容</div>
              <pre>{{ lg.event_context }}</pre>
              <div v-if="lg.llm_raw_response" class="muted" style="font-size: 12px; margin-top: 8px">
                LLM 原始输出（审计不变式）
              </div>
              <pre v-if="lg.llm_raw_response">{{ lg.llm_raw_response }}</pre>
              <div v-if="lg.error_message" class="error" style="margin-top: 8px">
                {{ lg.error_message }}
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <div v-else-if="!loading" class="empty">暂无学习日志。提交素材并 flush 后即会产生记录。</div>
  </div>

  <div class="row">
    <button @click="go(-1)" :disabled="page === 1 || loading">上一页</button>
    <span class="muted">第 {{ page }} 页</span>
    <button @click="go(1)" :disabled="logs.length < 50 || loading">下一页</button>
  </div>
</template>
