# yd-memory-service 问题解决与验证手册

## 使用说明

三轮评审共发现 30+ 个问题。本文档为每个 🔴 阻塞项和高价值 🟡 应修项提供：
1. **解决方法**（具体怎么改）
2. **验证方法**（怎么确认解决了）
3. **投入估算**（多少工作量）

按优先级排序，从上往下做。

---

## 🎯 P0：动手前必须完成（1 周 Spike）

### P0-1: Dify MCP 集成能力验证

**问题**：整个方案假设 Dify 支持 MCP SSE 集成，但从未验证。

**解决方法**：
1. 部署一个测试 Dify 实例（docker）
2. 用 Python `mcp` SDK 写一个最简 SSE MCP Server：

```python
# minimum_mcp_server.py
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from fastapi import FastAPI
from starlette.routing import Mount, Route

server = Server("test-server")

@server.list_tools()
async def list_tools():
    return [{
        "name": "echo",
        "description": "回显输入内容用于测试",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"]
        }
    }]

@server.call_tool()
async def call_tool(name, arguments):
    if name == "echo":
        return [{"type": "text", "text": f"Echo: {arguments['message']}"}]

sse = SseServerTransport("/mcp/messages/")
app = FastAPI()

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

app.router.routes.append(Route("/mcp/sse", endpoint=handle_sse))
app.router.routes.append(Mount("/mcp/messages/", app=sse.handle_post_message))
```

3. 在 Dify 后台注册这个 MCP Server (`http://localhost:8000/mcp/sse`)
4. 创建 workflow，Agent 节点绑定 echo 工具
5. 对话触发工具调用

**验证方法**（打勾就是通过）：
- [ ] Dify 后台能成功注册 MCP Server（不报错）
- [ ] Dify 能自动发现 echo 工具
- [ ] Agent 节点选择工具列表中出现 echo
- [ ] 对话中 Agent 能调用 echo 工具并收到响应
- [ ] 可以为**不同的 App/Workflow 配置不同的 MCP Server URL**（验证 agent_id-per-URL 方案可行）
- [ ] 记录 Dify 版本号

**如果不通过**：
- 检查 Dify 版本是否支持 MCP（需要较新版本，如 1.0+）
- 尝试 streamable-http 而不是 SSE
- 如果 MCP 完全不支持 → **方案改为 HTTP 工具或 Dify 插件路线**

**投入**：1 天

---

### P0-2: Agent 主动调用工具的行为验证

**问题**：假设 Agent 会主动调 search_memory / memorize，但 LLM 的实际行为未知。

**解决方法**：
1. 基于 P0-1 的 spike，扩展 MCP Server 加两个真工具：`search_memory` 和 `memorize`
2. 用 in-memory dict 模拟存储（不需要 DB）
3. Agent system prompt 加行为锚点：
```
你是发货助手。
你的 agent_id 是 test-001。
每次回答前先用 search_memory 检索相关历史记忆。
当用户告知新规则或纠正时使用 memorize 提交。
```
4. 手动进行 20 轮对话，覆盖场景：
   - 用户询问历史信息类（"上次说的怎么处理？"）
   - 用户告知新规则类（"以后厦门用顺丰"）
   - 用户提问业务类（"帮我处理今天的发货单"）
   - 闲聊类（"你好"）

**验证方法**：
- [ ] search_memory 调用率：目标 ≥ 60%（业务问题）
- [ ] memorize 调用率：用户明显告知新规则时 ≥ 80%
- [ ] 闲聊等无关场景**不**调用工具的比例 ≥ 90%
- [ ] Agent 构造的 query 质量：至少 50% 的 query 包含关键实体词，而不只是重复用户原话

**记录数据**：
```
| 对话场景 | 期望调 search | 实际调了吗 | query 质量 |
|---------|--------------|-----------|-----------|
| ...     | ...          | ...       | ...       |
```

**如果不通过**：
- 调用率过低 → 加强 system prompt 中的指令强度，加入 few-shot 示例
- Query 质量差 → 在工具描述中加入 query 构造指引
- 兜底：workflow 中强制加 HTTP 节点做初始检索，Agent 只负责回复和 memorize

**投入**：1 天（含手动测试对话）

---

### P0-3: ILIKE 中文搜索效果验证

**问题**：中文 ILIKE 匹配效果未知，可能命中率极低。

**解决方法**：
1. 构造测试数据集：
```sql
INSERT INTO test_memories (title, content) VALUES
('发货单重量单位规则', '发货单的重量单位统一使用吨，不使用千克'),
('厦门客户快递规则', '厦门地区客户发货统一使用顺丰快递'),
('单据附件解析', 'PDF 附件解析规则：先识别表格结构再提取字段'),
('异常处理经验', '发货单确认异常需要双人复核'),
... (共 100 条真实业务描述)
```

2. 准备 20 个测试 query（模拟 Agent 会构造的查询）：
```
Q1: "帮我处理今天的发货单"
  期望命中: 发货单单位规则、附件解析
Q2: "厦门客户特殊要求"
  期望命中: 厦门快递规则
Q3: "重量单位怎么填"
  期望命中: 发货单单位规则
...
```

3. 跑 ILIKE 搜索，统计命中率：
```python
for q in queries:
    hits = await search(q, limit=5)
    print(f"{q}: 期望 {expected}, 命中 {actual}, 精确度 {precision}")
```

**验证方法**：
- [ ] 平均召回率 ≥ 40%（能搜到期望结果的比例）
- [ ] 平均精确率 ≥ 50%（搜到的结果中相关的比例）
- [ ] 单次搜索延迟 < 100ms（10万条记忆库规模）
- [ ] 无匹配的 query 比例 < 30%

**如果不通过**：
- 命中率太低 → 上 PostgreSQL `tsvector` + `zhparser` 扩展（中文分词全文搜索）
- 依然不行 → V1 直接引入 embedding（小模型即可），跳过 ILIKE

**投入**：0.5 天

---

### P0-4: Spike 总结与决策

**解决方法**：写一份 spike 报告，记录：
- P0-1/2/3 每项的验证结果
- Dify 版本、MCP 具体行为、限制
- Agent 工具调用率的实际数据
- ILIKE 中文搜索命中率
- 是否继续 V1 完整版 / 精简版 / 换路线

**验证方法**：报告能明确回答"下一步做什么"

**投入**：0.5 天

**Spike 总投入：3 天**（不是我之前说的 1 周，实际更少）

---

## 🔴 P1：V1 实施前必须处理

### P1-1: LLM 分析失败时事件不丢失

**问题**：`run_pipeline` 中 `analyze` 返回空列表时 `_pending` 仍被清空，事件永久丢失。

**解决方法**：修改 `learning.py::run_pipeline`：

```python
async def run_pipeline(self, session_id: str) -> list[MemoryDecision]:
    events = [e for e in self._pending if e.session_id == session_id]
    if not events:
        return []
    
    decisions = await self.analyze(session_id)
    
    if not decisions and self._llm:
        # LLM 分析失败，事件保留，累加 retry_count
        for e in events:
            e.metadata['retry_count'] = e.metadata.get('retry_count', 0) + 1
            e.metadata['last_error'] = 'analysis_returned_empty'
            
            if e.metadata['retry_count'] >= 3:
                # 达到最大重试，写入 learning_logs 标记 failed，然后移除
                await self._log_failed_event(e)
                self._pending.remove(e)
        return []
    
    await self.commit(decisions)
    # 只清理有对应 decision 的 event，保留其他
    self._pending = [e for e in self._pending if e.session_id != session_id]
    return decisions
```

**验证方法**：
- [ ] 单元测试：mock LLM 返回无效 JSON → 事件仍在 `_pending`
- [ ] 单元测试：连续 3 次失败 → 事件被移除并写入 learning_logs
- [ ] 集成测试：flush 后查询数据库确认事件保留

**投入**：0.5 天

---

### P1-2: 并发 Flush 加锁

**问题**：同一 agent_id 同时被两个 flush 处理，产生重复记忆。

**解决方法**：用 PG advisory lock：

```python
async def run_pipeline_with_lock(self, agent_id: str):
    session = self._db.session()
    try:
        # 获取 agent_id 级别的锁（非阻塞）
        result = await session.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"),
            {"key": f"flush_{agent_id}"}
        )
        acquired = result.scalar()
        
        if not acquired:
            logger.info(f"Flush for {agent_id} already running, skip")
            return []
        
        try:
            return await self.run_pipeline(agent_id)
        finally:
            await session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": f"flush_{agent_id}"}
            )
    finally:
        await session.close()
```

**验证方法**：
- [ ] 并发测试：同时发起 5 个 flush 请求 → 只有 1 个真正执行
- [ ] 未获得锁的请求返回 `{"status": "skipped", "reason": "another_flush_running"}`
- [ ] Lock 会在异常时自动释放（用 try/finally）

**投入**：0.5 天

---

### P1-3: 去掉 Redis 和 Session 概念

**问题**：V1 不需要 Redis，简化架构。

**解决方法**：
1. 删除 `ShortTermStore` 相关代码
2. 新增 `pending_events` 表：
```sql
CREATE TABLE pending_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR NOT NULL,
    session_id VARCHAR,  -- 保留作为审计维度（不作为分组键）
    event_type VARCHAR NOT NULL,
    context TEXT NOT NULL,
    marked_type VARCHAR,
    metadata JSONB DEFAULT '{}',
    retry_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_pending_agent ON pending_events(agent_id, created_at);
```
3. 修改 `MemoryManager.memorize()` 直接写 PG，不写 Redis
4. Flush 逻辑改为 `SELECT ... WHERE agent_id=? FOR UPDATE` + `DELETE`
5. 删除 `/api/v1/sessions` 端点
6. `docker-compose.yml` 移除 Redis service
7. `pyproject.toml` 移除 `redis` 依赖

**验证方法**：
- [ ] 服务只依赖 PG，`docker-compose up` 后能启动
- [ ] MCP memorize 调用后，`pending_events` 表出现记录
- [ ] Flush 后，`pending_events` 中该 agent_id 的记录被清空，`long_term_memories` 有新数据
- [ ] 部署文档中不再提及 Redis

**投入**：1 天

---

### P1-4: Agent Space 逻辑删除

**问题**：物理删除 Space 导致关联数据悬空。

**解决方法**：
1. `agent_spaces` 表加字段 `status VARCHAR DEFAULT 'active'`（active | archived | pending_purge）
2. `DELETE /api/v1/spaces/{id}` 改为标记 `status='archived'`
3. 归档 Space 的记忆和日志保留，但被过滤（不参与检索、不在列表中默认显示）
4. 新增 `POST /api/v1/spaces/{id}/purge` — 需要 3 天缓冲期后才能真正物理删除
5. 管理界面 Space 详情页显示"归档"和"永久删除"两个不同按钮

**验证方法**：
- [ ] 归档 Space 后，其记忆不出现在 `search` 结果中
- [ ] 归档 Space 后，其 `learning_logs` 仍可查询
- [ ] 立即调用 `purge` 返回 403（未过缓冲期）
- [ ] 归档 3 天后 `purge` 成功

**投入**：0.5 天

---

## 🟡 P2：应修，可以在 V1 中做

### P2-1: MCP 工具描述增强

**问题**：Agent 需要更清晰的工具描述才能正确使用。

**解决方法**：
```python
TOOLS = [
    {
        "name": "search_memory",
        "description": """搜索当前 Agent 的历史长期记忆。
        
使用场景：
- 用户询问"上次"、"之前"、"以前"相关信息
- 你需要了解用户的偏好、习惯、历史决策
- 当前问题可能有相关的业务规则或经验

Query 构造建议：
- 使用关键实体词（客户名、产品、地区、操作类型）
- 避免直接用用户原话（可能包含无关词）

未找到匹配的记忆时，可以尝试更换关键词，或基于当前对话上下文直接回答。""",
        "parameters": {...}
    },
    {
        "name": "memorize",
        "description": """提交值得长期记住的信息。

使用场景：
- 用户明确告知新规则、偏好、纠正 (type=user_feedback)
- 你自己判断信息有长期价值 (type=agent_mark)

⚠️ 注意：提交后不会立即生效，在对话结束后由学习模型统一分析处理。
如果需要在当前对话中引用刚提交的信息，请直接从对话上下文获取，不要依赖 search_memory。""",
        "parameters": {...}
    }
]
```

**验证方法**：
- [ ] 用 P0-2 的 20 个测试对话重新跑一遍
- [ ] 对比之前的调用率，是否有显著提升

**投入**：0.5 天（含二次测试）

---

### P2-2: LLM 决策后二次去重

**问题**：LLM 分析路径缺少已有记忆去重。

**解决方法**：在 `commit()` 中，对 `action=store` 的决策做一次去重检查：

```python
async def _commit_long_term(self, d: MemoryDecision):
    memory_type = MemoryType(d.memory_type) if d.memory_type else MemoryType.REFERENCE
    merged_metadata = {**d.metadata, "_tags": d.tags} if d.tags else dict(d.metadata)
    
    if d.action in ("update", "merge") and d.merge_with_id:
        # ... 原有逻辑
        return
    
    # 新增：store 前做二次去重
    if d.action == "store":
        title_key = LearningModel._title_key(d.title)
        existing = await self._long_term.search(d.title, limit=3)
        for mem in existing:
            if LearningModel._title_key(mem.title) == title_key:
                # 相似标题已存在 → 改为 merge
                mem.content = mem.content + "\n---\n" + d.content
                mem.metadata.update(merged_metadata)
                await self._long_term.update(mem.id, mem)
                return
    
    # 无重复 → 正常 store
    memory = LongTermMemory(...)
    await self._long_term.create(memory)
```

**验证方法**：
- [ ] 单元测试：先 store "厦门客户用顺丰"，再让 LLM 返回 store "厦门顺丰规则" → 结果是合并而不是两条
- [ ] 集成测试：反复提交相似内容 100 次 → 长期记忆条数远小于 100

**投入**：0.5 天

---

### P2-3: 简化 event_type

**问题**：`pattern_detected` 在 Dify 场景下无触发路径。

**解决方法**：
1. MCP `memorize` 工具的 `type` 参数枚举改为：`user_feedback | agent_mark`
2. 工具描述更新，去掉 pattern_detected 和 session_end
3. 后端仍接受历史枚举值（向后兼容）

**验证方法**：
- [ ] MCP schema 显示新的枚举
- [ ] Agent 只会使用两个类型
- [ ] 数据库中仍可以看到旧类型的历史记录

**投入**：0.5 小时

---

### P2-4: 记忆条数上限保护

**问题**：无上限会导致 ILIKE 全表扫描性能崩溃。

**解决方法**：
1. `agent_spaces` 表加字段 `max_memories INT DEFAULT 5000`
2. 每次 store 前检查该 Space 的 active 记忆条数
3. 达到上限时：
   - 新记忆先进入 `pending_review` 状态
   - 触发权重最低的 10% 记忆自动归档
   - 管理界面提示"Space 记忆已满"

```python
async def check_memory_limit(self, agent_id: str):
    count = await self._long_term.count(agent_id=agent_id, status='active')
    space = await self.get_space(agent_id)
    if count >= space.max_memories:
        # 触发归档
        await self.archive_lowest_weight(agent_id, ratio=0.1)
```

**验证方法**：
- [ ] 造 5000 条记忆后再 store → 触发归档
- [ ] 归档的记忆在 `search` 中不出现
- [ ] 管理界面能看到归档记忆列表

**投入**：0.5 天

---

### P2-5: 前端管理界面精简

**问题**：全部 5 个页面工作量过大，V1 应聚焦核心。

**解决方法**：**V1 只做 2 个页面**：
1. `SpaceList.vue` — Agent Space 管理（创建、列表、显示 MCP URL）
2. `MemoryList.vue` — 记忆列表 + 快速审核 + 详情展开（弹窗展示，不做单独详情页）

推到 V1.1 / V2：
- `MemoryDetail.vue` + `AuditTrail.vue` — 完整审计链路
- `LearningLogs.vue` — 学习日志
- `Dashboard.vue` — 仪表盘

**验证方法**：
- [ ] 用户能通过管理界面创建 Space、复制 MCP URL
- [ ] 用户能看到 Agent 存储的记忆、审核（approved/flagged）、删除
- [ ] 前端工作量从 40% 降到 15-20%

**投入**：-2 天（相比原方案节省）

---

## 🟡 P3：可选，视目标决定

### P3-1: LLM 分析可关闭

**问题**：Learning Model 的复杂度可能不值得。

**解决方法**：Agent Space 配置增加 `learning_mode`：
- `heuristic` — 只用启发式（复用 yd-agent 已有的 `_heuristic_analyze`）
- `llm` — 用 LLM 分析（默认）
- `direct` — memorize 直接存，跳过分析（简化模式）

用户根据成本和效果偏好选择。

**验证方法**：
- [ ] 三种模式都能正确工作
- [ ] 切换模式后，新的事件按新模式处理
- [ ] `learning_logs` 中记录使用的 `analyzer_mode`

**投入**：0.5 天

---

### P3-2: 成本估算文档

**问题**：LLM 分析成本未估算。

**解决方法**：写一份成本估算表放在部署文档中：

```
| 规模 | Agent 数 | 每日 flush 数 | 每月 token | 每月费用 (DeepSeek-V3) |
|------|---------|--------------|-----------|----------------------|
| 小 | 10 | 500 | 12M | ¥6 |
| 中 | 100 | 10000 | 240M | ¥120 |
| 大 | 500 | 50000 | 1200M | ¥600 |

选 `heuristic` 或 `direct` 模式则零 LLM 成本。
```

**验证方法**：
- [ ] 部署文档中有成本表
- [ ] 管理界面显示当月实际 LLM 调用次数

**投入**：2 小时

---

### P3-3: 中文 tsvector 全文搜索（如 ILIKE 效果不佳）

**问题**：ILIKE 中文效果差。

**解决方法**（视 P0-3 结果决定）：
1. PG 安装 `zhparser` 扩展
2. `long_term_memories` 表加 `search_vector tsvector` 列
3. 触发器自动更新 search_vector
4. 搜索改为：
```sql
WHERE search_vector @@ plainto_tsquery('zhparser', :query)
ORDER BY ts_rank(search_vector, plainto_tsquery('zhparser', :query)) DESC
```

**验证方法**：
- [ ] 重跑 P0-3 的测试集
- [ ] 命中率提升 ≥ 20%
- [ ] 搜索延迟 < 50ms

**投入**：1 天（含 zhparser 部署）

---

## 汇总：完整落地路径

### 阶段 A：Spike（3 天）

- P0-1: Dify MCP 集成验证（1 天）
- P0-2: Agent 行为验证（1 天）
- P0-3: ILIKE 效果验证（0.5 天）
- P0-4: Spike 总结报告（0.5 天）

**决策点**：根据 Spike 结果决定路径 A/B/C

### 阶段 B：V1 精简版（如果继续，2-3 周）

必做（P1）：
- P1-1: LLM 失败事件不丢失（0.5 天）
- P1-2: 并发 flush 加锁（0.5 天）
- P1-3: 去掉 Redis + Session（1 天）
- P1-4: Space 逻辑删除（0.5 天）

应做（P2）：
- P2-1: MCP 工具描述增强（0.5 天）
- P2-2: LLM 决策后二次去重（0.5 天）
- P2-3: 简化 event_type（0.5 小时）
- P2-4: 记忆条数上限（0.5 天）
- P2-5: 前端精简到 2 页（3-4 天）

其他（后端 + MCP + 集成 + 部署）：约 5-7 天

**总投入**：约 12-15 天

### 阶段 C：V1.1 完善（视需要，1-2 周）

- P3-1: LLM 分析可关闭
- P3-2: 成本估算文档
- P3-3: tsvector 全文搜索（如 P0-3 不通过则提到 V1）
- 前端补齐：审计、日志、仪表盘

---

## 验证方法总原则

对每个问题的解决，都要满足：

1. **有可执行的测试用例** — 不是"看起来对"，是"运行后能验证"
2. **有明确的通过/失败标准** — 数字或勾选，不是主观判断
3. **有失败兜底方案** — 如果验证不通过怎么办

**如果一个"解决方法"不能被验证，那它不算解决。**

---

## 一个诚实的结论

30 个问题看起来吓人，但拆解到具体动作，总投入是可控的：
- Spike：**3 天**
- V1 精简版：**12-15 天**
- V1.1 完善：**7-10 天**

**总共 20-25 天的工作量**。如果目标是简历背书或技术验证，做完 Spike + V1 精简版（约 3 周）就够了。

**不要被评审的深度吓退。评审做得深，是为了让实施做得快。**

现在你有：
- 方案（做什么）
- 三轮评审（有什么问题）
- 本文档（怎么解决、怎么验证）

**信息完备。可以决定动手了。**
