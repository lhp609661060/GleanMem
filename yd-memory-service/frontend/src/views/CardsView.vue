<script setup>
/**
 * 代码库知识卡（V1.5 蒸馏产物）。
 *
 * 展示双层产物与修订保护：protected 卡不被自动更新覆盖，
 * wiki_sync_pending 表示卡已更新、md 投影待 CLI sync（D11 单向投影）。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'

const cards = ref([])
const loading = ref(false)
const error = ref('')
const onlyPending = ref(false)
const openId = ref('')

const counts = computed(() => ({
  total: cards.value.length,
  protected: cards.value.filter((c) => c.protected).length,
  pending: cards.value.filter((c) => c.wiki_sync_pending).length,
}))

async function load() {
  loading.value = true
  error.value = ''
  try {
    cards.value = await api.listCards(onlyPending.value ? true : null)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="head">
    <h1>代码库知识卡</h1>
    <div class="row">
      <label class="row muted" style="font-size: 13px">
        <input type="checkbox" v-model="onlyPending" @change="load" />
        仅看待 sync
      </label>
      <button @click="load" :disabled="loading">刷新</button>
    </div>
  </div>

  <div class="card">
    <div class="stat">
      <div><span>知识卡</span><b>{{ counts.total }}</b></div>
      <div><span>受保护（人工修订）</span><b style="color: var(--warn)">{{ counts.protected }}</b></div>
      <div><span>md 待 sync</span><b style="color: var(--accent)">{{ counts.pending }}</b></div>
    </div>
    <p class="muted" style="font-size: 12px; margin: 12px 0 0">
      知识卡是唯一事实源，<span class="mono">.yd-memory/wiki/*.md</span> 是它的单向投影（D11）。
      标记 <span class="badge warn">protected</span> 的卡不被自动蒸馏覆盖；
      <span class="badge accent">待 sync</span> 表示卡已更新、md 尚未重建（运行
      <span class="mono">ydm-distill sync</span>）。
      <span class="mono">description</span> 是 tsvector 检索匹配的唯一依据。
    </p>
  </div>

  <p v-if="error" class="error">{{ error }}</p>

  <div class="card" style="padding: 0">
    <table v-if="cards.length">
      <thead>
        <tr>
          <th>模块</th>
          <th>description（检索命脉）</th>
          <th>commit</th>
          <th>状态</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="c in cards" :key="c.id">
          <tr>
            <td class="mono">{{ c.module || c.id }}</td>
            <td>
              <div>{{ c.description }}</div>
              <div class="muted" style="font-size: 12px">{{ c.description.length }} 字 · {{ (c.tags || []).join(' / ') }}</div>
            </td>
            <td class="mono">{{ (c.commit_sha || '-').slice(0, 8) }}</td>
            <td>
              <span v-if="c.protected" class="badge warn">protected</span>
              <span v-if="c.wiki_sync_pending" class="badge accent">待 sync</span>
              <span v-if="!c.protected && !c.wiki_sync_pending" class="badge ok">已同步</span>
            </td>
            <td>
              <button @click="openId = openId === c.id ? '' : c.id">
                {{ openId === c.id ? '收起' : '查看' }}
              </button>
            </td>
          </tr>
          <tr v-if="openId === c.id">
            <td colspan="5" style="background: var(--panel-2)">
              <div class="muted" style="font-size: 12px">{{ c.title }}</div>
              <div class="muted" style="font-size: 12px; margin-top: 8px">知识卡正文（Agent 读）</div>
              <pre>{{ c.content }}</pre>
              <div v-if="c.narrative" class="muted" style="font-size: 12px; margin-top: 8px">
                叙述层（人读，md 投影来源）
              </div>
              <pre v-if="c.narrative">{{ c.narrative }}</pre>
              <div class="muted mono" style="font-size: 12px; margin-top: 8px">
                溯源：run={{ (c.run_id || '-').slice(0, 8) }} · 文件 {{ (c.file_paths || []).length }} 个
              </div>
              <pre v-if="(c.file_paths || []).length">{{ (c.file_paths || []).join('\n') }}</pre>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <div v-else-if="!loading" class="empty">
      暂无知识卡。运行 <span class="mono">ydm-distill run &lt;repo&gt; --key …</span> 生成。
    </div>
  </div>
</template>
