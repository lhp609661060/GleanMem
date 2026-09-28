# 11 · 加固实施规格（对应 10-hardening-design.md）

> 状态：**已实施（S1–S5 代码完成，待 `uv run pytest` + `alembic upgrade head` 验收）**。
> 每项含改动点、契约、验收标准。顺序即建议实施顺序（S1→S5），
> S1/S2 独立可先行；S3/S4 共享一次代码重构窗口；S5 随批。
> 全程身份不变式不变：`agent_id` 只由接入层解析注入，永不进工具参数/请求体。
> 与规格不一致处集中记在文末「实施偏差记录」。

## S1 · MCP 端密钥鉴权（D16）

### 改动点

- `mcp/server.py: handle_sse`（核心）：建连前校验 `X-Space-Key`。
- `mcp/server.py: call_tool`：`_agent_id` 的来源改为"建连时由 key 解析并固化"的值（语义不变，仍是 contextvar，但赋值来源变了）。
- `config.py`：新增 `mcp_auth_required: bool = True`。
- 新增小工具函数（建议放 `api/deps.py` 供两侧复用）：
  `async def resolve_space_key(key: str, db) -> str | None` —— SHA-256 查 `agent_spaces.api_key_hash` + `status != 'archived'`，返回 agent_id。

### 校验流程（handle_sse）

```
key = headers["X-Space-Key"]（可带 Bearer 前缀，strip 后兼容裸值）
if settings.mcp_auth_required:
    if not key:                → 401 {"error": "缺少 X-Space-Key header"}
    aid = resolve_space_key(key)
    if aid is None:            → 401 {"error": "space_key 无效或已归档"}
    if X-Agent-ID 存在且 != aid: → 403 {"error": "X-Agent-ID 与 space_key 不一致"}
    _agent_id.set(aid)
else:
    # 逃生口：回退旧行为（仅 X-Agent-ID，缺失仍 401）
```

### 异常回显（同文件 D20 项一并落）

`call_tool` 的 except 分支：对客户端返回 `{"error": "internal error"}`；`str(exc)` 与 traceback 只进 logger。

### 验收标准

1. **G** 无任何 header **W** GET /mcp/sse **T** 401，无 SSE 建立。
2. **G** 有效 space_key（header 换成 `X-Agent-ID: <同 agent_id>` 也带上）**W** 连接成功，recall/memorize 正常，身份为 key 解析的 agent_id。
3. **G** 有效 key + `X-Agent-ID` 为**其他** Space 的 id **W** 403。
4. **G** 已归档 Space 的 key **W** 401。
5. **G** `YDM_MCP_AUTH_REQUIRED=false` **W** 旧行为完整保留（仅 X-Agent-ID 可用）。
6. **G** 工具执行抛异常 **W** 客户端只见 `internal error`，日志含完整 traceback。

### 测试

- `tests/test_mcp.py` 扩展：上述 6 条各一用例（3、4 用 mock/双 Space fixture）。
- 既有 MCP 用例全部改为带 `X-Space-Key`（或测试内 `mcp_auth_required=false`，二选一，前者优先）。

## S2 · Space 创建仅 admin（D17）

### 改动点

- `api/spaces.py: create_space`：签名加 `_: str = Depends(require_admin)`。
- 未配置 `YDM_ADMIN_KEY` 时（`require_admin` 依赖 admin key 存在）：返回 **503** `{"detail": "平台未配置 admin key，无法创建 Space"}`。实现：在 `deps.py` 的 `require_admin` 里，`settings.admin_key` 为空 → 503（对所有 admin 端点生效，语义一致）。

### 验收标准

1. 无 Authorization → 401；space key → 401（require_admin 已如此）。
2. admin key → 201，返回体不变（`space_key` 仅本次可见）。
3. `YDM_ADMIN_KEY` 未配置 → 503，任何 key 都建不了。

### 测试

- `tests/test_admin.py` 加 3 条；`tests/test_spaces.py`（或对应文件）的建 Space fixture 改为 admin 身份或直写库。

## S3 · flush 两阶段化 + 批上限（D18）

### 改动点

- `core/learning.py: run_pipeline / _run` —— 核心重构，结构如下：

```
run_pipeline(agent_id, ...):
    # 阶段0：独占连接上抢锁（engine.connect()，不用工作 session）
    async with engine.connect() as lock_conn:
        got = (await lock_conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:k))"), {...})).scalar()
        if not got: return None
        try:
            snapshot = await self._load_snapshot(agent_id)      # 阶段1（短事务，只读）
            if snapshot.empty: return []
            analyses = await self._analyze(snapshot)            # 无事务：LLM 在此
            return await self._apply(agent_id, analyses, ...)    # 阶段2（短事务）
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(...)"), {...})
```

- 阶段1 `_load_snapshot`：`SELECT id, event_type, context, session_id, dedup_key, source,
  retry_count FROM pending_events WHERE agent_id=:a AND status='pending'
  ORDER BY created_at LIMIT :batch_size`（工作 session，读完 commit）。
- 阶段2 `_apply`：`SELECT ... FOR UPDATE` 按 id 重选 → 在库的才应用决策；
  chat 分支 1:1 绑定、observation 分支整批绑定（任一 FAILED 整批保留）——
  语义与现 `_run` 完全一致，只是事件集合换成"重选后仍在库"的子集；
  codebase 分支整体作为 `_apply` 的一部分（仍锁内 LLM，见设计 D18 已知残留），同样受 batch_size 限制。
- `decay_weights` / `archive_overflow` 保持在阶段2 末尾（事务内）。
- `_llm_analyze` / `_observation_analyze_llm` 入参从 ORM 对象改为快照 dataclass（防 session 脏对象跨事务）。
- `config.py`：新增 `flush_batch_size: int = 50`。
- `core/manager.py: flush`：透传 batch_size（从 settings 读），签名不破坏。

### 不变式（重构后必须逐条仍成立）

1. 同一 agent_id 并发 flush 仍被 advisory lock 互斥（锁现在挂独占连接，跨阶段存活）。
2. 无决策/LLM 失败 → 事件保留、retry_count+1（P1-1），不删。
3. FAILED 决策对应事件保留；仅成功消费的删除。
4. 阶段2 重选缺失的事件：跳过，不写决策、不删、不计 retry。
5. 锁必然释放（finally + 连接关闭双保险）。

### 验收标准

1. **G** 收件箱 120 条、batch=50 **W** 一次 flush 只消费最早 50 条，剩 70 条 status 仍 pending。
2. **G** LLM 分析期间（测试用 sleep-hook）外部删除某事件 **W** 该事件无决策无日志无异常，其余正常。
3. **G** LLM 抛异常 **W** 阶段2 不执行写操作，全部事件 retry_count+1，锁已释放（后续 flush 可立即执行）。
4. **G** heuristic/direct 模式 **W** 行为与重构前一致（现有用例全绿）。
5. **G** 并发两次 flush **W** 一次 None/skip，一次成功（沿用现有互斥用例）。

### 测试

- 新增 `tests/test_flush_two_phase.py`：上述 1–3。
- 既有 learning 管线用例（P1-1、decay、overflow、E2 绑定）全量回归，零改动应全绿。

## S4 · 事件死信化 + 复活（D19）

### 数据库（与 S5 同一条迁移）

```sql
ALTER TABLE pending_events ADD COLUMN status VARCHAR(8) NOT NULL DEFAULT 'pending';
CREATE INDEX idx_pending_events_flush ON pending_events (agent_id, status, created_at);
CREATE UNIQUE INDEX uq_agent_spaces_key_hash ON agent_spaces (api_key_hash);
```

### 改动点

- `core/models/pending_event.py`：加 `status` 列映射。
- `core/learning.py: _handle_undecided`：达 `settings.event_max_retries`（默认 3）时
  写 FAILED 日志（照旧）+ `e.status = 'dead'`（**替换** `await self._s.delete(e)`）。
- `_load_snapshot`（S3）已按 `status='pending'` 过滤——dead 事件不再进任何分支。
- `api/learning.py` 新增两个端点：

```
GET  /api/v1/learning/events?status=dead&page=1   # require_agent，按 caller 过滤，
                                                   # 每页 50 倒序；返回
                                                   # id/event_type/context/source/
                                                   # retry_count/status/created_at
POST /api/v1/learning/events/{event_id}/revive    # require_agent；事件须属 caller，
                                                   # 且 status='dead'，否则 404；
                                                   # 置 status='pending', retry_count=0
                                                   # 返回 {"status": "revived"}
```

- `config.py`：新增 `event_max_retries: int = 3`。

### 验收标准

1. **G** 事件连续 3 次 flush 无决策 **W** 第 3 次后：仍在库、`status='dead'`、有一条 FAILED 审计日志。
2. **G** dead 事件在收件箱 **W** flush 完全忽略它（不分析、不删、retry 不涨）。
3. **G** revive 后 **W** `status='pending'`、`retry_count=0`，下次 flush 正常处理。
4. **G** caller A revive caller B 的 dead 事件 **W** 404。
5. **G** 对 pending 事件调 revive **W** 404（只允许复活 dead）。

### 测试

- 新增 `tests/test_dead_letter.py`：上述 5 条。
- 修改现有 `test_*` 中依赖"重试后事件被删"断言的用例（改为 status='dead' 断言）。

## S5 · 杂项加固（D20）

### 改动点

| 项 | 文件:位置 | 改法 |
|----|-----------|------|
| 常量时间比较 | `api/deps.py:38` | `key == settings.admin_key` → `hmac.compare_digest(key, settings.admin_key)` |
| hash 唯一索引 | 见 S4 迁移 SQL | — |
| CORS 可配 | `main.py:52-57` + `config.py` | `cors_origins: str = "*"`；`allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()]` |
| chat dedup | `api/learning.py: EventCreate` | 加 `dedup_key: str | None = None`，透传 `mgr.memorize(..., dedup_key=body.dedup_key)`（memorize 已支持该参数） |

### 验收标准

1. **G** 带相同 `dedup_key` 两次 POST /learning/events **W** 第二次被 `(agent_id, dedup_key)` 唯一索引挡下（409 或既有 memorize 幂等语义——按 `memorize` 现行为对齐，写明即可）。
2. **G** `YDM_CORS_ORIGINS=https://a.com` **W** 预检/响应头只回该来源；默认 `*` 行为不变。
3. admin key 比较为代码审查项（行为无 observable diff）。

### 测试

- chat dedup 幂等用例 1 条；CORS 配置用例 1 条（TestClient 检查响应头）。

## 实施顺序与依赖

```
S1 (mcp) ─┐
S2 (spaces) ├─ 互相独立，可并行
S5 杂项 ──┘
S3 (flush 重构) → S4 (死信，依赖 S3 的 _load_snapshot 形态)
```

提交切分（Conventional Commits）：

1. `feat(mcp): require space key auth on SSE endpoint`
2. `feat(backend): restrict space creation to admin`
3. `refactor(core): move LLM analysis out of flush transaction, add batch cap`
4. `feat(core): dead-letter exhausted events instead of deleting`
5. `chore(backend): misc hardening (compare_digest, cors config, chat dedup)`
6. `docs(design): merge hardening v3.9 into 01-design`（S1–S5 验收全绿后）

## 全局验收（合入前）

- `uv run pytest` 全绿（含上述新增 15 条左右用例）。
- `scripts/e2e_demo.py`、`e2e_incremental_demo.py` 无需改代码仍跑通（MCP 相关 demo 需补 X-Space-Key，属文档/脚本更新，不算破坏）。
- `docs/gleanmem/01-design.md` §实现现状与差距清单 增补对应 🟢 条目并升版 v3.9。

## 实施记录（与规格的偏差）

代码侧 S1–S5 已落地，以下五处与上文写法不同，按实现为准：

1. **测试文件名**：MCP 用例落在既有的 `tests/test_mcp_server.py`（规格写 `test_mcp.py`），未新建文件。
2. **`require_admin` 的 503 判定顺序**：它在 `resolve_identity` 之后执行，所以「完全不带 Authorization」仍是 401，只有带了一把能解析的 key 而平台未配 admin key 时才 503。规格验收 3「未配置时任何 key 都建不了」据此收窄为「任何**有效** key」。
3. **批上限的读取位置**：`flush_batch_size` 在 `_load_snapshot` 内直接读 `settings`，没有穿到 `MemoryManager.flush` 签名上——flush 的参数已由 Space.config 提供，批上限是运维旋钮不是业务旋钮，多穿一层参数不值。
4. **chat dedup 冲突返 200 而非 409**：与 observation/codebase 的 `on_conflict_do_nothing` 对齐，响应 `{"status":"duplicate","event_id":<既有 id>}`——客户端重试要的是「已收到」，不是错误码。
5. **`_load_snapshot` 未取 `retry_count`**：分析器只用快照做语义判断，重试计数在阶段2 的真实行上累加，快照带这个字段没有消费者。

已知残留（设计 D18 已声明，非偏差）：`source=codebase` 分支的 `IncrementalDistiller` 仍在锁内调 LLM，本期只吃批上限与 status 过滤。

