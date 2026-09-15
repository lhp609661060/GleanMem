# gleanmem 方案可行性评审

## 评审方法

从 6 个维度逐一推演：MCP 工具运行时行为、会话/事件生命周期、并发边界、数据一致性、失败模式、V1 范围合理性。每个问题标注严重程度：

- 🔴 阻塞项：不改会导致系统不可用或数据损坏
- 🟡 应修项：V1 落地前应处理
- 🟢 可接受：V1 可接受，V2 优化

---

## 一、MCP 工具与 Dify 的适配——运行时实际行为

### 🔴 1.1 MCP 工具无法自动获取 agent_id 或 conversation 上下文

**问题**：Dify 的 MCP SSE 集成在调用工具时，只传递工具 schema 中定义的参数。不会透传 `sys.conversation_id`、`app_id` 等 Dify 上下文。MCP 服务端无法从请求中推断"是哪个 Agent 在调我"。

**推演**：
- Dify 中一个 MCP Server 配置对应一个 SSE URL
- 同一个 memory-service 要为多个 Agent 服务
- 如果多个 Agent 共用同一个 MCP URL，服务端无法区分调用来自哪个 Agent

**⚠ 结论**：此问题**已通过方案中的 agent_id-per-URL 设计规避**——每个 Agent Space 获得唯一的 MCP 端点 URL：`/mcp/sse?agent_id={uuid}`。但方案文档没有显式说明这一点，需要补充。

**修正**：管理界面创建 Agent Space 后，显示的 MCP URL 示例应包含 agent_id 参数。Dify 用户为每个 Agent 注册独立的 MCP Server 配置。

---

### 🟡 1.2 memorize 和 search_memory 的可见性延迟

**问题**：Agent 调用 `memorize` → 事件写入待处理队列 → 同一轮对话中 Agent 再次调用 `search_memory` → **找不到刚刚提交的内容**（事件还未被 LearningModel 分析并写入长期记忆）。

**推演场景**：
```
Agent: memorize(type="user_feedback", context="用户喜欢简洁回答")
Agent: search_memory("用户偏好")  → 空结果（刚提交的还没生效）
Agent: 认为没有相关记忆，按默认风格回答
```

Agent 会困惑——它刚"记住"的东西查不到。

**评估**：这不是 bug，是异步学习的固有特性。需要明确两点：
1. MCP 工具描述中说明：`memorize` 提交后不会立即生效，将在对话结束后由学习模型处理
2. 如果 Agent 需要在同一轮对话中引用刚记住的信息，它应该直接用对话上下文，而不是依赖 search_memory

**结论**：工具描述中明确此行为即足够。

---

### 🟡 1.3 search_memory 空结果无法区分"真没有"和"搜不好"

**问题**：Agent 构造了一个差的 query → ILIKE 全文匹配没命中 → 返回空结果。Agent 看到空结果，不知道是"真的没有相关记忆"还是"有但我搜词不对"。它可能错误地认为没有记忆，也可能反复尝试不同 query 浪费 token。

**推演**：
```
Agent: search_memory("解析 PDF")
  → 零命中（记忆标题是"附件单据提取规则"）
Agent: 认为没有相关规则，按照默认逻辑处理
  → 错过了重要的业务规则
```

**评估**：这是 ILIKE 全文匹配的固有局限，V1 无法根治（需要 V2 语义搜索）。但可以缓解——在工具返回中附带提示。

**建议**：search_memory 返回空结果时，附带 `hint: "未找到匹配的记忆。请尝试使用不同的关键词重新搜索，或基于当前对话上下文直接回答。"`

---

## 二、会话生命周期——与 Dify 的实际适配

### 🔴 2.1 Session 概念在 V1 中可能不需要

**问题**：方案中设计了 `POST /api/v1/sessions` 端点，但回顾 V1 的实际需求——我们不需要会话级别的消息历史（Dify 管理对话历史），不需要短期 K/V（Dify 变量覆盖），也不需要 per-session 事件分组。LearningModel 分析的是某个 agent_id 的所有待处理事件——和属于哪个会话无关。

**反思**：在 V1 没有 HTTP 注入、不做短期记忆的情况下，session 的唯一作用是：
1. 给 memorize 提交的 LearningEvent 分配 session_id 字段
2. 作为 flush 时的分组键

但这两个都可以去掉——event 只需要 agent_id 就够了。不同会话的事件混在一起分析反而更有利于跨对话发现模式。

**推演**——如果没有 session：
```
Agent A (会话1, conversation_abc): memorize("用户喜欢简洁回答")
Agent A (会话2, conversation_xyz): memorize("厦门客户用顺丰")

Flush 触发:
  → LearningModel 分析 2 个待处理事件
  → 2 个都是 agent_id=A 的事件
  → LLM 分别判断: 都 store
  → 写入 2 条长期记忆
```

完全正常。session_id 只是 metadata，对学习决策没有影响。

**结论**：
- 去掉 `/api/v1/sessions` 端点
- 去掉 ShortTerm / Redis 依赖（V1 完全不需要）
- memorize 的事件写入 Redis 列表 `pending_events:{agent_id}`
- Flush → 原子取出所有事件 → 分析 → 提交 → 写入 learning_logs
- 部署简化：V1 只需 PostgreSQL + memory-service（Redis 可选，仅用于 pending events 的持久化队列）

**或者**：pending events 也可以存在 PG 的 `learning_events` 表中，状态为 `pending`。Flush 时 SELECT FOR UPDATE 锁定该 agent 的 pending 事件，分析后 UPDATE 状态为 `processed`。这样完全去掉 Redis 依赖。

---

### 🟡 2.2 Flush 节点与 Dify workflow 执行模型

**问题**：方案中 workflow 结构是 `[Start] → [Agent] → [Flush HTTP] → [End]`。但 Dify workflow 的节点是顺序执行的——Agent 节点完成后才会执行 Flush。如果 Agent 节点调用了 memorize，这些调用发生在 Agent 节点执行期间。Flush 在 Agent 节点完成后执行。顺序上是正确的。

**但有一个时序问题**：如果用户在 Agent 还在处理时关闭了对话窗口，workflow 会中断吗？如果中断，Flush 节点不会执行，pending events 丢失。

**评估**：Dify workflow 的节点是顺序执行的，Agent 节点完成后才会执行下一个节点。用户关窗口不会中断 workflow（它是异步后台执行的）。所以 Flush 一定会执行。

但还有一种情况：workflow 执行超时或异常终止。此时 Flush 不执行，pending events 留在队列中，下次同一 agent 的 flush 时一起处理。**这是可以接受的**。

**建议**：Flush 设计为幂等——同一批 pending events 被多次 flush 时，LearningModel 的 heuristics 去重逻辑会处理重复。但如果使用 LLM 分析，重复的 event 可能产生重复的记忆。需要在 flush 时加分布式锁。

---

### 🔴 2.3 并发 Flush 的竞态条件

**问题**：同一个 agent_id 可能同时有多个 Dify conversation 在运行。如果两个 conversation 同时结束，两个 Flush HTTP 请求同时到达，同时运行 `LearningModel.run_pipeline()`，同时操作同一批 pending events。

**推演**：
```
时间线：
T1: Conversation A 结束 → Flush A 开始 → analyze(events_1)
T2: Conversation B 结束 → Flush B 开始 → analyze(events_1 + events_2)
T3: Flush A commit(decisions_A)
T4: Flush B commit(decisions_B)  ← decisions_B 可能包含 decisions_A 已经处理的内容
```

**后果**：重复记忆、数据不一致。

**修正**：在 flush 处理中加 agent_id 级别的锁：
- Redis 锁：`SETNX flush_lock:{agent_id} 1 EX 10`
- 或 PG advisory lock：`SELECT pg_try_advisory_lock(hashtext('flush_' || agent_id))`
- 获取锁失败 → 直接返回（另一个 flush 正在处理，本回合的事件留到下次）

---

## 三、LearningModel 的数据一致性问题

### 🔴 3.1 LLM 分析失败时事件静默丢失

**问题**（yd-agent 原始代码的 bug）：

```python
async def run_pipeline(self, session_id):
    decisions = await self.analyze(session_id)   # 如果返回 []
    await self.commit(decisions)                  # no-op
    self._pending = [e for e in self._pending if e.session_id != session_id]  # 事件被移除！
    return decisions
```

如果 LLM 返回无法解析的 JSON（`_parse_decisions` 返回 `[]`），事件仍然会被 `run_pipeline` 移除。**事件静默丢失，没有重试，没有告警。**

**推演**：
```
Agent: memorize("用户纠正发货单位应该用吨")
  → event 写入 pending 队列
Flush:
  → LLM 返回格式错误: "我觉得这个应该存下来" (不是 JSON)
  → _parse_decisions → []
  → commit([]) → no-op
  → 事件从 pending 移除
  → 用户纠正永远丢失
```

**修正**：
1. `analyze()` 返回空列表时，不调用 `commit()` 也不移除事件——让事件留在队列中，下次 flush 重试
2. 设置最大重试次数（如 3 次），超过后写入 learning_logs 标记为 `failed`，然后移除
3. 管理界面中"学习日志"页面显示失败事件，支持手动重试

---

### 🟡 3.2 LLM 分析路径缺少"已有记忆去重"

**问题**：`_build_analysis_prompt()` 只把当前批次的事件发给 LLM，不包含已存在的记忆。LLM 不知道哪些记忆已经存在了，无法判断"这条和已有的重复吗？"。所以 LLM 路径可能产生重复记忆。

**对比**：`_heuristic_analyze()` 会先 `await self._long_term.search(title, limit=3)` 查找已有记忆，然后根据 title_key 匹配决定 `merge` 还是 `store`。这是正确的做法。

**LLM 路径没有这一步。** 如果两周前的会话中 Agent 已经存储了"厦门客户用顺丰"，本周另一个会话又 memorize 了同样的内容，LLM 不知道这是一个重复，会再 store 一条。

**评估**：对于 V1，这个问题有四种处理方式：
1. 在 LLM prompt 中加入已有记忆的摘要（token 成本高）
2. LLM 返回后，用 heuristics 做二次去重（推荐）
3. 依赖 LLM 自己判断（不可靠）
4. 完全用 heuristic 模式（可靠但缺少 LLM 的判断力）

**建议**：LLM 返回 decisions 后，对 `action=store` 的 decisions 跑一次 `_title_key()` 去重检查——和已有的长期记忆对比，重复的改为 `merge`。这是 `_heuristic_analyze` 已经实现的逻辑，可以直接复用。

---

### 🟡 3.3 commit 非事务性

**问题**：`commit()` 逐条执行 decisions。如果第 3 条写入失败（如 DB 连接中断），前 2 条已经写入，后 N 条未写入。没有回滚。

**评估**：每条 decision 是独立的，部分成功比全部失败好。V1 可接受。但应该记录哪些成功了、哪些失败了，以便排查。

---

## 四、部署与运维问题

### 🟡 4.1 V1 真的需要 Redis 吗？

**重新评估**：

| 功能 | V1 是否需要 | 存储 |
|------|-----------|------|
| ShortTerm K/V (remember/recall) | ❌ 不做 | - |
| ShortTerm 消息历史 | ❌ Dify 管理 | - |
| Pending events 队列 | ✅ memorize 需要 | Redis 或 PG |
| Flush 分布式锁 | ✅ 需要 | Redis 或 PG advisory lock |

Pending events 可以用 PG 表代替 Redis：

```sql
CREATE TABLE pending_events (
    id UUID PRIMARY KEY,
    agent_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    context TEXT NOT NULL,
    marked_type VARCHAR,
    metadata JSONB DEFAULT '{}',
    retry_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_pending_agent ON pending_events(agent_id, created_at);
```

Flush 时：`SELECT ... FOR UPDATE` + `DELETE` 在同一事务中。比 Redis 更可靠（不丢数据），不需要额外基础设施。

**结论**：**V1 可以完全去掉 Redis 依赖**。PostgreSQL 单数据库完成所有功能。这对部署是一个重大简化。

---

### 🟡 4.2 管理界面无认证

**问题**：管理界面嵌在 FastAPI 中，`/admin/` 路径没有认证。任何能访问服务的人都能查看/编辑/删除记忆。

**评估**：V1 是内部工具，部署在内网。可接受。V2 需要加入简单的 API Key 或 Basic Auth。

---

### 🟢 4.3 记忆软删除

**问题**：`DELETE /api/v1/memories/{id}` 是物理删除。但 `learning_logs` 引用了 `memory_id`。删除记忆后，日志中的引用变成悬空指针。

**建议**：V1 使用软删除（`is_deleted` 标记），管理界面隐藏已删除的记忆，但审计日志保留完整引用链。V2 再做物理清理。

---

## 五、V1 范围反思

### 🟢 5.1 哪些可以进一步砍掉

| 功能 | 当前状态 | 是否可以砍 | 影响 |
|------|---------|-----------|------|
| Redis 依赖 | 方案中包含 | ✅ 可砍 | pending events 改用 PG 表，部署更简单 |
| Session 管理 | 方案中包含 | ✅ 可砍 | 简化 API，减少概念 |
| ShortTerm Store | 代码中提取 | ✅ 可砍 | V1 完全不需要 |
| 管理界面的仪表盘 | 方案中包含 | ⚠️ 可推迟到最后 | 记忆列表 + 审核功能是 MVP，仪表盘是 nice-to-have |
| Agent Space 配置项 | 方案中包含 | ⚠️ 可简化 | V1 只保留名称和描述，衰减率等用全局默认 |

### 🟢 5.2 不应该砍的

| 功能 | 原因 |
|------|------|
| learning_logs 表 | 没有它就没有审计能力，商业 Agent 不可接受 |
| Agent Space 管理 | 记忆隔离的基础，不能省 |
| MCP SSE Server | 核心集成方式 |
| 管理界面的记忆列表 + 审核 | MVP 核心功能 |
| 管理界面的记忆详情 + 审计时间线 | 审计能力的关键展示 |

---

## 六、总结：修正后的 V1 最小可行方案

### 修正点

1. **去掉 Session 概念** — memorize 事件用 agent_id 分组，不用 session_id
2. **去掉 Redis** — pending events 用 PG 表，flush 锁用 PG advisory lock
3. **去掉 ShortTerm Store** — V1 完全不需要
4. **LLM 分析失败不丢事件** — 返回空时保留事件，设置最大重试
5. **LLM 决策后二次去重** — 复用 heuristic 的 title_key 去重逻辑
6. **并发 flush 加锁** — PG advisory lock
7. **MCP URL 显式说明** — 管理界面展示每个 Space 的 MCP 端点 URL（含 agent_id）
8. **search_memory 空结果提示** — 附带 hint 引导 Agent 换关键词
9. **memorize 工具描述明确异步性** — 注明"不会立即生效"
10. **软删除** — 记忆删除改为标记 `is_deleted`

### 修正后的架构（V1 最小版）

```
gleanmem/
├── backend/
│   └── FastAPI + MCP SSE Server
│       ├── API: spaces / memories / learning / webhooks
│       ├── MCP: 2 工具 (search_memory, memorize)
│       └── Core: MemoryManager + LearningModel + PGLoneTermStore
├── frontend/
│   └── Vue 3 管理界面 (5 页)
├── PostgreSQL — 唯一的存储依赖
│   ├── agent_spaces
│   ├── long_term_memories
│   ├── pending_events
│   └── learning_logs
└── docker-compose: memory-service + PostgreSQL
```

### 修正后的 Dify Workflow（不变）

```
[Start] → [Agent + MCP] → [Flush HTTP] → [End]
```

### 修正后的 MCP 工具（不变，描述增强）

```
search_memory(query, limit=5)
  描述: 搜索当前 Agent 的历史长期记忆。当上下文信息不足时使用。
        未找到匹配记忆时，请尝试更换关键词重新搜索。

memorize(type, context, marked_type?)
  描述: 提交值得长期记住的信息。提交后不会立即生效，
        将在对话结束后由学习模型统一分析处理。
```

### 修正后的 Agent Prompt 锚点（不变）

```
你的 agent_id 是 {{agent_id}}。
每次回答前先用 search_memory 检索相关历史记忆。
当发现值得长期记住的信息时使用 memorize 提交。
```

---

## 评审结论

**方案总体可行，没有发现会推翻整体方向的根本性问题。** 10 个修正点中 3 个是阻塞项（🔴），均可在实现阶段纠正。最大的发现是 V1 可以完全去掉 Redis 和 Session 概念，简化部署和 API 面。

建议先修正方案文档中的这 10 个点，再开始实现。
