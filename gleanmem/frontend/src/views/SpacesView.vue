<script setup>
/**
 * 空间管理（平台管理台）：列出/创建/编辑/归档所有 Space，点击「进入」切到某空间的业务视图。
 * 「进入」= 轮换该空间的 space_key（旧 key 立即失效），拿新 key 进入。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, setKey, setRole } from '../api/client'

const router = useRouter()
const spaces = ref([])
const loading = ref(false)
const error = ref('')
const info = ref('')

// 创建
const creating = ref(false)
const newName = ref('')
const newDescription = ref('')
const createdKey = ref('')

// 编辑（编辑中的空间 id + 表单）
const editingId = ref('')
const editName = ref('')
const editDescription = ref('')
const editLearningMode = ref('heuristic')
const editDecay = ref('0.95')
const editMinWeight = ref('0.1')
const editMaxMemories = ref('5000')

async function load() {
  loading.value = true
  error.value = ''
  try {
    spaces.value = await api.listSpaces()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function create() {
  error.value = ''
  info.value = ''
  const trimmed = newName.value.trim()
  if (!trimmed) {
    error.value = '名称不能为空'
    return
  }
  creating.value = true
  try {
    const res = await api.createSpace({
      name: trimmed,
      description: newDescription.value.trim() || null,
    })
    createdKey.value = res.space_key
    newName.value = ''
    newDescription.value = ''
    info.value = 'Space 已创建。下面的 key 仅显示这一次，请立即复制保存。'
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

function enterCreated() {
  setKey(createdKey.value)
  setRole('space')
  router.push({ name: 'memories' })
}

function startEdit(s) {
  editingId.value = s.agent_id
  editName.value = s.name || ''
  editDescription.value = s.description || ''
  const c = s.config || {}
  editLearningMode.value = c.learning_mode || 'heuristic'
  editDecay.value = String(c.decay_per_day ?? 0.95)
  editMinWeight.value = String(c.min_weight ?? 0.1)
  editMaxMemories.value = String(c.max_memories ?? 5000)
}

function cancelEdit() {
  editingId.value = ''
}

async function saveEdit() {
  error.value = ''
  info.value = ''
  const config = {
    learning_mode: editLearningMode.value,
    decay_per_day: parseFloat(editDecay.value),
    min_weight: parseFloat(editMinWeight.value),
    max_memories: parseInt(editMaxMemories.value, 10),
  }
  if ([config.decay_per_day, config.min_weight, config.max_memories].some(Number.isNaN)) {
    error.value = '数值字段格式不正确'
    return
  }
  try {
    await api.updateSpace(editingId.value, {
      name: editName.value,
      description: editDescription.value,
      config,
    })
    info.value = '已保存'
    editingId.value = ''
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function archive(s) {
  const ok = confirm(`归档后「${s.name}」的 key 将立即失效、无法再访问。确定归档？`)
  if (!ok) return
  error.value = ''
  info.value = ''
  try {
    await api.archiveSpace(s.agent_id)
    info.value = `已归档「${s.name}」`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function enter(s) {
  const ok = confirm(`进入「${s.name}」将重新签发该空间的 space_key（旧 key 立即失效）。确定进入？`)
  if (!ok) return
  error.value = ''
  try {
    const res = await api.rotateKey(s.agent_id)
    setKey(res.space_key)
    setRole('space')
    router.push({ name: 'memories' })
  } catch (e) {
    error.value = e.message
  }
}

function statusClass(st) {
  return st === 'active' ? 'ok' : 'danger'
}

onMounted(load)
</script>

<template>
  <div class="head">
    <h1>空间管理</h1>
    <button @click="load" :disabled="loading">{{ loading ? '加载中…' : '刷新' }}</button>
  </div>

  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="info" class="muted" style="color: var(--ok)">{{ info }}</p>

  <!-- 创建新 Space -->
  <div class="card">
    <h2 style="font-size: 14px; margin: 0 0 4px">创建新 Space</h2>
    <p class="muted" style="font-size: 12px; margin-top: 0">
      每个 Space 生成一把独立 <span class="mono">space_key</span>（<span class="mono">ydm_…</span>），
      明文仅返回一次；服务端只存哈希与前缀。
    </p>
    <div class="row" style="margin-bottom: 10px">
      <input v-model="newName" placeholder="名称" class="grow" />
    </div>
    <textarea
      v-model="newDescription"
      placeholder="描述（可选）"
      rows="2"
      style="width: 100%; margin-bottom: 10px"
    ></textarea>
    <button class="primary" @click="create" :disabled="creating">
      {{ creating ? '创建中…' : '创建 Space' }}
    </button>

    <div v-if="createdKey" style="margin-top: 12px">
      <p class="muted" style="font-size: 12px; margin: 0 0 6px">
        新 Space 的 key（仅显示这一次，请复制保存）：
      </p>
      <pre style="word-break: break-all">{{ createdKey }}</pre>
      <button class="ok" style="margin-top: 10px" @click="enterCreated">进入该 Space</button>
    </div>
  </div>

  <!-- 编辑 Space -->
  <div v-if="editingId" class="card">
    <h2 style="font-size: 14px; margin: 0 0 4px">编辑 Space</h2>
    <p class="muted mono" style="font-size: 12px; margin-top: 0">{{ editingId }}</p>
    <div class="row" style="margin-bottom: 10px">
      <input v-model="editName" placeholder="名称" class="grow" />
    </div>
    <textarea
      v-model="editDescription"
      placeholder="描述（可选）"
      rows="2"
      style="width: 100%; margin-bottom: 10px"
    ></textarea>
    <div class="row" style="margin-bottom: 10px">
      <label style="color: var(--muted)">learning_mode</label>
      <select v-model="editLearningMode">
        <option value="heuristic">heuristic</option>
        <option value="llm">llm</option>
        <option value="direct">direct</option>
      </select>
    </div>
    <div class="row" style="margin-bottom: 10px">
      <label style="color: var(--muted); min-width: 120px">decay_per_day</label>
      <input v-model="editDecay" type="number" step="0.01" min="0" max="1" style="width: 130px" />
      <label style="color: var(--muted); min-width: 120px">min_weight</label>
      <input v-model="editMinWeight" type="number" step="0.01" min="0" max="1" style="width: 130px" />
      <label style="color: var(--muted); min-width: 120px">max_memories</label>
      <input v-model="editMaxMemories" type="number" step="1" min="1" style="width: 130px" />
    </div>
    <div class="row">
      <button class="primary" @click="saveEdit">保存</button>
      <button @click="cancelEdit">取消</button>
    </div>
  </div>

  <!-- 空间列表 -->
  <div class="card" style="padding: 0">
    <table v-if="spaces.length">
      <thead>
        <tr>
          <th>名称</th>
          <th>状态</th>
          <th>key 前缀</th>
          <th>agent_id</th>
          <th>创建时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in spaces" :key="s.agent_id">
          <td>
            <div>{{ s.name }}</div>
            <div v-if="s.description" class="muted" style="font-size: 12px">{{ s.description }}</div>
          </td>
          <td><span class="badge" :class="statusClass(s.status)">{{ s.status }}</span></td>
          <td class="mono">{{ s.api_key_prefix }}…</td>
          <td class="mono truncate" style="max-width: 180px">{{ s.agent_id }}</td>
          <td class="mono" style="font-size: 12px">{{ s.created_at }}</td>
          <td>
            <div class="row" style="gap: 6px">
              <button class="ok" :disabled="s.status !== 'active'" @click="enter(s)">进入</button>
              <button @click="startEdit(s)">编辑</button>
              <button class="danger" :disabled="s.status !== 'active'" @click="archive(s)">归档</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else-if="!loading" class="empty">暂无空间。用上面的表单创建第一个 Space。</div>
  </div>
</template>
