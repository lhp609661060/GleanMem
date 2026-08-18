# P0-1 Spike: Dify MCP 集成能力验证

**目标**: 用最小 echo MCP Server 验证 Dify 1.13.3 是否满足
`docs/yd-memory-service/05-solutions.md` P0-1 的六个 checkpoint。
一次性丢弃品,不进 V1 代码库。

## 环境

- Dify: **1.13.3** (docker,已在跑)
- MCP SDK: `mcp>=1.2` (实测装到 1.28.1)
- Python: 3.13.5 (uv 管理)
- Transport: **SSE**(先按方案里的默认走;若 Dify 只吃 streamable-http 再改)
- 端口:**8765** (8000 被 yd-agent-fresh generator 占了)

## 启动

```bash
cd spike/mcp-echo
uv sync
uv run python echo_server.py
# → listening on http://0.0.0.0:8765
```

**Endpoints**:
- `GET /mcp/sse` — SSE 长连接(Dify 注册用)
- `POST /mcp/messages/?session_id=...` — MCP 消息通道(SSE 首帧里返回)

## 已验证(spike 启动阶段)

```
[✓] 服务能起,uvicorn 200 OK
[✓] 宿主 curl http://127.0.0.1:8765/mcp/sse
    → 返回 `event: endpoint` + session URL,SSE 握手成功
[✓] Dify 容器内 curl http://host.docker.internal:8765/mcp/sse
    → 同样返回 endpoint 事件,容器→宿主网络通
```

## 下一步:在 Dify 后台手工验证 P0-1 六项 checkpoint

**Dify 侧 MCP Server URL 填** `http://host.docker.internal:8765/mcp/sse`

### 操作步骤

1. **在 Dify 后台找到 MCP Server 入口**
   - 1.13 应该在"工具"或"插件"或"设置 → MCP"里(具体位置以你实际界面为准,记下路径)
   - 添加一个 MCP Server,URL = `http://host.docker.internal:8765/mcp/sse`

2. **验证 checkpoint(逐条打勾)**

   - [ ] **1️⃣ Dify 后台能成功注册 MCP Server**(点击"添加/保存"不报错)
   - [ ] **2️⃣ Dify 能自动发现 `echo` 工具**(注册后能看到工具名/描述)
   - [ ] **3️⃣ 新建一个 Chatflow/Agent workflow,Agent 节点工具列表出现 `echo`**
   - [ ] **4️⃣ 对话中让 Agent 调用 echo,能看到返回的 JSON**
         (提示词直接说:"调用 echo 工具,把 'hello' 传进去")
   - [ ] **5️⃣ agent_id-per-URL 方案可行**:
         - 再建第二个 workflow,MCP Server URL 改成
           `http://host.docker.internal:8765/mcp/sse?agent_id=test-002`
         - 调用 echo,看返回的 `server_saw_url.query_params` 里
           **有没有 `agent_id=test-002`**
         - 有 → agent_id-per-URL 成立,V1 隔离方案继续
         - 没有(Dify 剥了 query)→ 换用 path 段(`/mcp/sse/test-002`)或
           工具参数(有 LLM 传参不稳的风险)
   - [ ] **6️⃣ Dify 是否透传对话上下文**:
         echo 返回的 `server_saw_url.headers` 里有没有
         `x-dify-conversation-id`/`x-app-id`/类似字段。
         **有** → 未来可以不用把 agent_id 塞 URL,直接用 header 里的
         Dify 上下文,方案能更简洁。
         **没有** → 就按 URL 携带 agent_id 走。

3. **记录 Dify 版本和实际行为到 `RESULT.md`**(下一步创建)

## echo 工具返回长啥样

```json
{
  "echoed": "hello",
  "server_saw_url": {
    "path": "/mcp/sse",
    "query_string": "agent_id=test-002",
    "query_params": {"agent_id": "test-002"},
    "headers": {
      "user-agent": "...",
      "x-dify-conversation-id": "...(如果 Dify 透传)"
    },
    "client": "192.168.65.1:56xxx"
  }
}
```

## 网络注意

- `localhost:8765` 从 Dify 容器内**不可达**(那是容器自己的 loopback)
- macOS Docker Desktop → 用 `host.docker.internal:8765`
- 服务必须 `host="0.0.0.0"`,不能是 `127.0.0.1`

## 卡壳兜底

- 注册报"connection failed" → 先在宿主 curl 一次,再从 Dify 容器 curl 一次,
  两处都通再回 Dify 排查 URL 拼写/路径
- Dify 只允许 streamable-http → 改 `SseServerTransport` 为
  `StreamableHTTPServerTransport`(mcp SDK 同一个包里)
- Dify 完全不支持 MCP Server 注册 → 版本可能编译得不完整,或者需要
  某个 feature flag;这是 spike 主要要暴露的风险
