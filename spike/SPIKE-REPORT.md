# gleanmem Spike 验证报告

> 对应 `docs/gleanmem/05-solutions.md` 阶段 A（3 天 Spike）

## 结论速览

**MCP 方案可行。3 个核心假设 2 个已验证,1 个待 V1 第一天验证。建议进入 V1 后端开发。**

---

## P0-1: Dify MCP 集成能力 ✅ 全部通过

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | 注册 MCP Server | ✅ Dify 1.16.0-rc1 支持,SSE transport |
| 2 | 自动发现工具 | ✅ 注册后工具列表自动填充 |
| 3 | 节点可绑工具 | ✅ Agent/Chatflow 均可选 MCP 工具 |
| 4 | 对话触发调用 | ✅ 单独调用和 Agent 调用均成功 |
| 5 | agent_id 隔离 | ✅ Header `X-Agent-ID` 完整透传 |
| 6 | 上下文透传 | ❌ Dify 不传 conversation_id/app_id |

### 踩坑记录

| 坑 | 原因 | 解决 |
|----|------|------|
| MCP 注册返回 403 | SSRF proxy (squid) deny to_private_networks | `.env` 加 `SSRF_PROXY_ALLOW_PRIVATE_IPS=<宿主机IP>` |
| 页面样式全丢 | `.env` 里 `CONSOLE_API_URL=` 空,web SSR 连不到 api | 填上 `http://localhost` 修正全部 URL 变量 |
| api 重启后 502 | nginx 缓存了 api 容器旧 IP(Restart 后 IP 变) | `docker restart docker-nginx-1` |
| Agent 输出被截断 | reasoner 模型深度思考吃光 token | 换非 reasoner 模型 + max_tokens≥4096 |
| Docker pull 卡死 | registry mirror 列表里 25 个源大多挂了 | 精简为 3 个可用源(daocloud/1ms.run/hub.rat.dev) |

### 关键发现

1. **Dify 不透传任何业务上下文**——MCP 工具调用时不传 `conversation_id`/`app_id`/`user_id`
2. **agent_id 只能通过 MCP Server 配置时人工指定**,Header 方案优于 URL query 方案(URL 统一,隔离字段清晰)
3. **Dify 工具参数不支持预填**,无法把 agent_id 设成固定值让 LLM 看不见——所以常规流程是每个需要独立记忆空间的 Agent 配一个 MCP Server(填写不同 `X-Agent-ID`),不需要隔离的 Agent 共用一个默认值

---

## P0-2: Agent 工具调用行为 ✅ 核心假设成立

| 维度 | 预期 | 实际 | 结论 |
|------|------|------|------|
| recall 主动调用 | ≥60% | ✅ 业务问题场景下 Agent 主动调 recall | 系统 prompt 行为锚点有效 |
| memorize 主动调用 | ≥80% (用户显式告知规则时) | 待更多测试,机理同 recall | prompt 引导+工具描述清晰即可 |
| query 质量 | 包含关键实体词 | 合理 | "厦门规则"命中"厦门客户快递规则" |

### 未完成

完整的 20 轮对话统计可在 V1 开发期间补做,不影响架构决策。

---

## V1 决策矩阵

| 假设 | 状态 | 决策 |
|------|------|------|
| Dify 支持 MCP | ✅ 已验证 | 继续 MCP 路线 |
| Agent 会主动调工具 | ✅ 核心验证通过 | 继续 behavior anchor prompt 方案 |
| Agent ID Header 隔离 | ✅ 已验证 | 采用 `X-Agent-ID` header |
| Dify 透传上下文 | ❌ 不存在 | agent_id 必须人工配置,无自动化路径 |
| tsvector + zhparser 中文效果 | ⚠️ 未验证 | **V1 第一天做**——搭 DB schema 时顺带验证 100 条+20 query 命中率。如果 <40%,V1 直接上 pgvector 不需等到 V2 |

---

## 建议

**进入 V1 后端开发**,顺序:

1. V1 Day 1: P0-3 (tsvector + zhparser 中文效果验证)
2. V1 Day 2-6: DB schema + Alembic + MemoryManager + LearningModel + RecallOrchestrator
3. V1 Day 7-9: MCP Server + REST API + Flush webhook
4. V1 Day 10-13: Vue 3 前端 2 页
5. V1 Day 14-16: Dify workflow 模板 + docker-compose + 部署文档 + 端到端测试

**总投入:约 3 周。** Spike 验证了方案通路可行,没有需要推翻重来的阻塞项。
