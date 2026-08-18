# 集成指南 · Dify（MCP SSE + Flush webhook）

> 对应 01-design.md §集成指南。前置：yd-memory-service 已部署（`docker compose up -d` + `uv run alembic upgrade head` + `uv run yd-memory`），且已在管理端创建 Agent Space 并拿到 `agent_id` 与 `space_key`。

## 1. 注册 MCP Server

Dify 后台 → 工具 → MCP → 添加服务器：

| 配置项 | 值 |
|--------|-----|
| Transport | SSE |
| URL | `http://<你的IP>:8000/mcp/sse` |
| Headers | `X-Agent-ID: <你的 agent_id>` |

- 注册成功会自动发现 3 个工具：`recall` / `load_memory` / `memorize`
- 每个需要独立记忆空间的 Agent 配一个 MCP Server（不同 `X-Agent-ID`）；共享记忆则共用同一 Server

**已知坑（spike 实测）**：
- MCP 注册 403 → Dify `.env` 加 `SSRF_PROXY_ALLOW_PRIVATE_IPS=<宿主机IP>`
- Dify **不透传** `conversation_id` / `app_id` / `user_id`——身份只能靠注册时填的 `X-Agent-ID`

## 2. 导入 Workflow 模板

`docs/dify-workflow-template.yml` 导入 Dify（工作室 → 导入 DSL），三节点结构：

```
[Start] → [Agent + recall/load_memory/memorize] → [Flush HTTP] → [End]
```

导入后修改：
1. Agent 节点 system prompt 里的【业务描述】
2. Agent 节点绑定 MCP 工具（recall / load_memory / memorize）
3. Flush 节点 URL → 你的服务地址
4. Flush 节点 Headers → `Authorization: Bearer <space_key>`（模板里是 `ydm_REPLACE_WITH_SPACE_KEY`）

## 3. 数据流

```
用户提问 → Agent 先调 recall(intent=自然语言) → 拿到记忆摘要/wiki 引用 → 回答
用户告知规则 → Agent 调 memorize → 写 pending_events（source=chat）
对话结束 → Flush HTTP 节点 → POST /api/v1/learning/flush（Bearer key）
        → LearningModel（默认 heuristic；llm 模式需配 YDM_LLM_API_KEY）
        → long_term_memories + learning_logs（含 llm_raw_response 审计）
```

## 4. 行为锚点（Agent system prompt 片段）

```
需要历史信息或参考知识时，先调用 recall 检索。
需要长期记住的新信息（用户告知规则、纠正等）时，调用 memorize 提交。
memorize 提交后不会立即生效，对话结束后统一分析处理。
```
