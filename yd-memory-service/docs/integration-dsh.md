# 集成指南 · DSH / 其他 Agent（REST 直连）

> 对应 01-design.md §集成指南。任何支持 HTTP 的 Agent 平台（DSH、自研编排器等）都可以通过 REST 接入，身份 = per-space API Key。

## 1. 拿 space_key

```bash
curl -X POST http://localhost:8000/api/v1/spaces \
  -H 'Content-Type: application/json' \
  -d '{"name":"ds h-助手的记忆空间"}'
# → 返回 agent_id 与 space_key（仅此一次明文，之后不可再查）
```

所有请求带 `Authorization: Bearer <space_key>`。

## 2. 端点速查

| 动作 | 端点 |
|------|------|
| 检索记忆 | `POST /api/v1/recall` `{"intent":"自然语言描述需要什么"}` |
| 提交学习事件 | `POST /api/v1/learning/events` `{"type":"user_feedback|agent_mark","context":"..."}` |
| 触发学习（会话结束） | `POST /api/v1/learning/flush` |
| 查看记忆 | `GET /api/v1/memories?status=approved&page=1` |
| 审计日志 | `GET /api/v1/learning/logs?page=1` |
| 观察事件 push | `POST /api/v1/observations`（见 integration-push.md） |

## 3. 平台侧接线约定（以 DSH 为例）

在 DSH 的 Agent 编排里接三个 hook：

1. **检索**：Agent 需要历史信息时 → 调 `POST /api/v1/recall`，把 `memories[].summary` + `hint` 注入上下文
2. **记忆**：用户告知规则/纠正 → 调 `POST /api/v1/learning/events`
3. **会话结束**：调 `POST /api/v1/learning/flush`（可放平台的生命周期回调）

若平台支持 MCP client，也可复用 SSE 端点（`/mcp/sse` + `X-Agent-ID`），与 Dify 同一套。

## 4. 隔离保证

- `agent_id` 永远不出现在 body/参数里，只由 key 解析（身份不变式）
- A 的 key 无法读写 B 的空间（测试覆盖：`tests/test_auth.py::test_space_key_isolates_data`）
- flush 不接收 body 身份字段，传了也被忽略
