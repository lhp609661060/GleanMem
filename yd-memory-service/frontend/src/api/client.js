/**
 * REST 客户端：所有请求带 Authorization: Bearer <space_key>。
 *
 * key 只存 sessionStorage（关标签页即失效），不写 localStorage、不进 URL、不打日志——
 * space_key 等同于该 Space 的全部读写权，泄露成本高（后端也只存哈希，见 deps.py）。
 */

const KEY_STORAGE = 'ydm_space_key'

export function getKey() {
  return sessionStorage.getItem(KEY_STORAGE) || ''
}

export function setKey(key) {
  if (key) sessionStorage.setItem(KEY_STORAGE, key)
  else sessionStorage.removeItem(KEY_STORAGE)
}

export function hasKey() {
  return Boolean(getKey())
}

async function request(path, { method = 'GET', body } = {}) {
  const key = getKey()
  if (!key) throw new Error('未设置 space_key')

  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${key}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  })

  if (res.status === 401) throw new Error('space_key 无效或已失效')
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const data = await res.json()
      if (data.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      /* 响应非 JSON，沿用状态码 */
    }
    throw new Error(detail)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  // 记忆 + 审核（N6）
  listMemories: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null)
    ).toString()
    return request(`/memories${q ? `?${q}` : ''}`)
  },
  reviewMemory: (id, status) =>
    request(`/memories/${encodeURIComponent(id)}/review`, {
      method: 'POST',
      body: { status },
    }),

  // 学习审计
  listLogs: (page = 1) => request(`/learning/logs?page=${page}`),
  flush: () => request('/learning/flush', { method: 'POST' }),
  submitEvent: (payload) => request('/learning/events', { method: 'POST', body: payload }),

  // 检索（与 MCP recall 等价）
  recall: (intent) => request('/recall', { method: 'POST', body: { intent } }),

  // codebase 蒸馏审计
  listRuns: (page = 1) => request(`/codebase/runs?page=${page}`),
  listCards: (syncPending = null) =>
    request(`/codebase/cards${syncPending === true ? '?sync_pending=true' : ''}`),

  // Space（当前 key 对应的那个）
  listSpaces: () => request('/spaces'),
}
