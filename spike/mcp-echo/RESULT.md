# P0-1 验证结果记录

## 环境快照

- Dify 版本: 1.16.0-rc1 (docker-compose)
- MCP Server: mcp-echo (SSE, port 8765)
- 网络: 10.60.80.165 (局域网 IP),SSRF proxy 已放行
- 验证时间: 2026-07-15

## Checkpoint 结果

### 1️⃣ Dify 能否注册 MCP Server
- ✅ 通过
- URL: `http://10.60.80.165:8765/mcp/sse`
- 初次失败:SSRF proxy deny to_private_networks → 403
- 修复:在 `.env` 加 `SSRF_PROXY_ALLOW_PRIVATE_IPS=10.60.80.165` 后通过

### 2️⃣ 工具自动发现
- ✅ 通过
- 注册后自动发现 echo 工具,description 完整展示

### 3️⃣ Agent 节点工具列表
- ✅ 通过
- Chatflow/Agent 节点均可绑定 MCP 工具

### 4️⃣ 对话触发工具调用
- ✅ 通过
- 单独调用工具:返回完整 JSON
- Agent 调用:model 的深度思考 token 会截断最终输出,需换非 reasoner 模型 + 拉高 max_tokens

### 5️⃣ agent_id 隔离方案

| 方案 | 结果 |
|------|------|
| URL query `?agent_id=xxx` | ✅ Dify 完整透传 |
| Header `X-Agent-ID: xxx` | ✅ Dify 完整透传 |
| 工具参数预填 | ❌ Dify 不支持 |

**采用 Header `X-Agent-ID`**——URL 统一,隔离清晰。

### 6️⃣ Dify 是否透传对话上下文
- ❌ **不透传**
- HEADERS 中仅 `user-agent: python-httpx/0.28.1` + `via: squid`
- 无 `x-dify-conversation-id`、`x-app-id` 等任何业务 header
- → agent_id 必须人工在 MCP Server 配置时指定,无法从 Dify 自动获取

## 结论

- ✅ **全部通过** → P0-1 pass
