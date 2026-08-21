/**
 * REST 客户端：所有请求带 Authorization: Bearer <key>。
 *
 * key 只存 sessionStorage（关标签页即失效），不写 localStorage、不进 URL、不打日志——
 * space_key 等同于该 Space 的全部读写权，泄露成本高（后端也只存哈希，见 deps.py）。
 *
 * 身份两层：
 *  - admin：平台管理台（YDM_ADMIN_KEY），管理所有 Space / 用户；
 *  - space：per-space key，进入某 Space 后的业务视图（记忆/日志/知识卡/审计/检索）。
 */

const KEY_STORAGE = 'ydm_space_key'
const ROLE_STORAGE = 'ydm_role'
const ADMIN_KEY_STORAGE = 'ydm_admin_key'

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

export function getRole() {
  return sessionStorage.getItem(ROLE_STORAGE) || 'space'
}

export function setRole(role) {
  sessionStorage.setItem(ROLE_STORAGE, role)
}

// admin key 单独保留：space 视图「返回管理台」时用它切回平台管理
export function getAdminKey() {
  return sessionStorage.getItem(ADMIN_KEY_STORAGE) || ''
}

export function setAdminKey(key) {
  if (key) sessionStorage.setItem(ADMIN_KEY_STORAGE, key)
  else sessionStorage.removeItem(ADMIN_KEY_STORAGE)
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
  // 身份探测（登录后据此区分 admin / space）
  authMe: () => request('/auth/me'),

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

  // Space 管理（admin：全部；space：仅自己）
  listSpaces: () => request('/spaces'),
  createSpace: (payload) => request('/spaces', { method: 'POST', body: payload }),
  updateSpace: (agentId, payload) =>
    request(`/spaces/${encodeURIComponent(agentId)}`, { method: 'PUT', body: payload }),
  archiveSpace: (agentId) =>
    request(`/spaces/${encodeURIComponent(agentId)}`, { method: 'DELETE' }),
  rotateKey: (agentId) =>
    request(`/spaces/${encodeURIComponent(agentId)}/keys`, { method: 'POST' }),
}
