<script setup>
/**
 * 检索预览：与 MCP recall 工具等价的入口。
 *
 * 演示价值：Agent 拿到的到底是什么——三路召回（热记忆 / 冷记忆 tsvector / wiki）
 * 合并重排后的结果，以及未过审记忆确实不出现在这里（N6）。
 */
import { ref } from 'vue'
import { api } from '../api/client'

const intent = ref('')
const result = ref(null)
const loading = ref(false)
const error = ref('')

const samples = ['发货单怎么处理', '认证是怎么做的', '这个项目的代码结构']

async function run(q) {
  const query = (q ?? intent.value).trim()
  if (!query) return
  intent.value = query
  loading.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await api.recall(query)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="head">
    <h1>检索预览</h1>
  </div>

  <div class="card">
    <form class="row" @submit.prevent="run()">
      <input
        v-model="intent"
        class="grow"
        placeholder="输入自然语言意图，例如：发货单确认后还能改吗"
      />
      <button class="primary" type="submit" :disabled="loading">
        {{ loading ? '检索中…' : 'recall' }}
      </button>
    </form>
    <div class="row" style="margin-top: 10px">
      <span class="muted" style="font-size: 12px">试试：</span>
      <button v-for="s in samples" :key="s" @click="run(s)" :disabled="loading">{{ s }}</button>
    </div>
    <p class="muted" style="font-size: 12px; margin: 12px 0 0">
      与 MCP 的 <span class="mono">recall</span> 工具走同一条编排逻辑（三路并行召回 + 规则重排）。
      未过审的 <span class="mono">pattern</span> 记忆不会出现在结果里（N6 审核纪律）。
    </p>
  </div>

  <p v-if="error" class="error">{{ error }}</p>

  <template v-if="result">
    <div class="card">
      <b>{{ result.hint }}</b>
    </div>

    <div class="card">
      <div class="muted" style="font-size: 12px; margin-bottom: 8px">记忆（Top {{ result.memories.length }}）</div>
      <table v-if="result.memories.length">
        <thead>
          <tr><th>标题</th><th>类型</th><th>权重</th><th>摘要</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in result.memories" :key="m.id">
            <td>{{ m.title }}</td>
            <td><span class="badge">{{ m.type }}</span></td>
            <td class="mono">{{ m.weight }}</td>
            <td class="muted">{{ m.summary }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">无匹配记忆</div>
    </div>

    <div class="card">
      <div class="muted" style="font-size: 12px; margin-bottom: 8px">
        wiki / 知识卡引用（Skill 机制：按 description 匹配，按需加载）
      </div>
      <table v-if="result.wiki_refs.length">
        <thead>
          <tr><th>id</th><th>标题</th><th>description</th></tr>
        </thead>
        <tbody>
          <tr v-for="w in result.wiki_refs" :key="w.id">
            <td class="mono">{{ w.id }}</td>
            <td>{{ w.title }}</td>
            <td class="muted">{{ w.description }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">无匹配文档</div>
    </div>
  </template>
</template>
