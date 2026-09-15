---
name: ydm-memory-client
description: >
  Use when the DSH agent needs to read/write the external multi-agent memory
  service gleanmem: recall memories before answering, memorize user
  feedback or corrections, flush learning at session end, and inspect audit
  logs.
---

# gleanmem REST 客户端（DSH 接入）

让 DSH Agent 通过 REST 使用外部记忆平台 gleanmem。身份 = per-space API Key。

## 前置

- 服务地址：`http://127.0.0.1:8000`（后端 `gleanmem/backend`）
- 后端启动命令：
  ```bash
  cd <repo>/gleanmem/backend
  PYTHONPATH=src uv run uvicorn gleanmem.main:app --host 127.0.0.1 --port 8000
  ```
- Space Key 读取方式（先 `cd` 到 backend）：
  ```bash
  grep '^YDM_SPACE_KEY=' .env | cut -d= -f2
  ```
- 所有请求带 `Authorization: Bearer <space_key>`。**agent_id 永远不出现在 URL/body/参数里**，由 key 解析。

## 动作

### 1. 检索记忆（回答用户前或需要历史信息时）

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/recall \
  -H "Authorization: Bearer $YDM_SPACE_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"intent":"用自然语言描述需要什么信息"}'
```

把返回的 `memories[].summary` + `hint` 注入上下文；空结果就如实告诉用户当前没有相关记忆。

### 2. 提交学习事件（用户说“记住/以后…/纠正”时）

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/learning/events \
  -H "Authorization: Bearer $YDM_SPACE_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"type":"user_feedback","context":"用户明确要求记住的事实或规则"}'
```

`type` 选 `user_feedback`（用户偏好/纠正）或 `agent_mark`（Agent 自己判断值得记）。

### 3. 会话结束触发学习落库

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/learning/flush \
  -H "Authorization: Bearer $YDM_SPACE_KEY"
```

不调用则事件停留在收件箱，不会进入长期记忆。每个用户任务收尾时调用一次。

### 4. 查看 / 审计

```bash
curl -sS "http://127.0.0.1:8000/api/v1/memories?status=approved&page=1" \
  -H "Authorization: Bearer $YDM_SPACE_KEY"
curl -sS "http://127.0.0.1:8000/api/v1/learning/logs?page=1" \
  -H "Authorization: Bearer $YDM_SPACE_KEY"
```

## 坑与约定

- 提交事件只是进收件箱（pending），flush 后才分析落库。
- 不要拼 agent_id 字段；A 的 key 只能读写 A 的空间。
- 健康检查 `GET /health`；后端依赖 PostgreSQL 与 migrations。
- 观察事件走 `POST /api/v1/observations`（业务 push，幂等键 event_id），详见 `docs/integration-push.md`。
