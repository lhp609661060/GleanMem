# 07 · v3.1 架构评审报告（架构自洽性 / 差距清单核验 / codebase 蒸馏可行性 / 时间现实性 / 风险 / 可开工性）

> **评审对象**：`01-design.md` v3.1 方案（权威设计）+ 现有 v2 代码实现（`yd-memory-service/backend`，1356 行，无 git 元数据）
> **评审方式**：只读评审。文档全文通读 + 后端代码逐文件实读 + Alembic 迁移/init-db/seed/docker 配置逐项核验。所有结论附 `文件:行号` 证据；未运行任何需要数据库的命令，未安装依赖。
> **分级**：🔴 阻塞项 / 🟡 应修项 / 🟢 可接受

---

## 0. 评审结论速览

- **总体判断**：方案方向成立、Spike 已验证核心通路（Dify MCP SSE + `X-Agent-ID` 透传 + Agent 主动调工具），v3.1 的收件箱/身份/观察 push/codebase 蒸馏建模**在纸面上自洽**；但**现有代码仍是 v2 形态，01-design §实现现状与差距清单低估了 v3 语义的实现量，且漏了 2 个连开发环境都起不来的 🔴（P1-1 数据丢失未修复、`uv run yd-memory` 启动入口缺失）**。
- **可开工性**：**可以按 V1 Week 1 开工，但开工第一步必须先修"建表链路 + 启动入口"两件事**，否则 `docker compose up` + `alembic upgrade head` + `uv run yd-memory` 三个文档化命令里有 2 个直接失败。
- **TOP 3 开工前必处理**（详见 §8）：
  1. 修复/重做初始迁移与建表链路（edac4c 损坏默认值 + 触发器函数与 `init-db.sql` 耦合 + zhparser 三环断裂）；
  2. 修复学习管线数据完整性与审计欠账（LLM 空/失败决策时事件被无条件删除、`llm_raw_response` 从未写入、`retry_count` 从未使用）；
  3. 一次性落地 v3 契约迁移（`source`/`dedup_key`/`api_key_*` 加列、flush 改从 key 解析身份、删除 `query_db`、补 `main()` 入口）。

---

## 1. 差距清单逐条核验（01-design §实现现状与差距清单）

逐条读代码验证，**全部 10 条属实**，其中 2 条的实际严重程度高于清单标注，另有 1 条的表述需修正：

| 级别 | 清单条目 | 代码证据 | 核验结论 |
|------|---------|---------|---------|
| 🔴 | 迁移 `edac4c...` 中 `agent_spaces.config` 默认值写坏 | `alembic/versions/edac4c069c8c_initial_all_5_tables.py:25`：`'"decay_per_day"NULL.95,"min_weight"NULL.1,"max_memories"NULL,...'::jsonb`，`.` 与 `:` 被替换成 `NULL`，非法 JSON | ✅ **属实，且是"零数据库可用"级**：`alembic upgrade head` 在该列 `server_default` cast 时报错，任何全新库都建不起来 |
| 🔴 | zhparser 未真正接入 | `init-db.sql:7,15` 触发器用 `to_tsvector('simple',...)`；`docker-compose.yml:3` 用 `postgres:15-alpine`（无 zhparser）；`Dockerfile.pg-zhparser:1` 基于 `postgres:16-alpine` | ✅ **属实，且比清单描述的更严重（三环断裂，见 N7）**：镜像没换、扩展/配置从未创建、触发器用 'simple'，三层都不通 |
| 🔴 | 迁移先建 trigger、后靠 `init-db.sql` 建函数 | `edac4c069c8c:100-102` 建 `trg_memories_search_vector`/`trg_wiki_search_vector`，引用 `memories_search_vector_trigger()`/`wiki_search_vector_trigger()`；函数定义在 `init-db.sql:5-19` | ✅ 属实（表述需修正）：docker 标准顺序（`init-db.sql` 先于 `alembic`）能过；但**任何"先跑 alembic"的库（CI、已有 volume、zhparser 镜像自建）必挂**。根因是迁移把函数定义外包给了一个外部 SQL 文件——结构性耦合，不是单纯顺序问题 |
| 🔴 | `load_memory` 无归属校验 | `mcp/server.py:51-54`：`mgr.memories.get(arguments.get("id",""))`，无 `agent_id` 过滤，任意 id 跨 Space 可读 | ✅ 属实。v3.1 检索侧明确承诺"修复 v2 实现的越权缺陷"，代码未兑现 |
| 🟡 | `learning_logs.llm_raw_response` 从未写入 | `learning_log.py:33` 列存在；`learning.py` 全文无任何 `llm_raw_response=` 赋值（grep 证实） | ✅ 属实。v3.1 强制约束"llm 模式每次分析必须写入"未兑现 |
| 🟡 | 权重衰减硬编码 | `learning.py:113`：`await self._store.decay_weights(agent_id, 0.95, 0.1)`；`agent_space.py:18-21` 的 `config` 含 `decay_per_day`/`min_weight` 但全仓库从未读取 | ✅ 属实。`config` 里的衰减参数是死配置 |
| 🟡 | `_llm_analyze` 未剥离 ```json；失败回退 `_direct_analyze` 而非 heuristic | `learning.py:207`：`json.loads(raw)` 直接解析；`:222`：`except` 后 `return self._direct_analyze(events)`；`:163` docstring 却写 "Fall back to heuristic on failure" | ✅ 属实，注释与行为不一致 |
| 🟡 | `query_db` 工具及实现待删（D3） | `mcp/tools.py:73-90`（工具定义，Dify 可见）；`mcp/server.py:61-62`（分支）、`:75-98`（实现，`asyncpg.connect` 直连） | ✅ 属实。违反 D3 不变式；也是代码里唯一的 asyncpg 双连接源（`config.py:16` 的 `database_url_sync` 反而无人用） |
| 🟡 | REST 无鉴权、无 agent_id 隔离 | `api/spaces.py:14-56` 全部端点无任何鉴权；`api/webhooks.py:13-20` flush 由 **body 传 `agent_id`** | ✅ 属实。补充：flush 由 body 传 `agent_id` 还违反 v3.1"`agent_id` 永远不进入工具参数或业务字段"的身份不变式 |
| 🟢 | 无 `backend/tests/`；`seed.py` 与 Alembic 双轨建表 | `backend/tests/` 不存在；`seed.py:10-23` 用 `Base.metadata.create_all` + 建触发器，与 Alembic 各自为政 | ✅ 属实。补充：双轨还造成 schema drift——`agent_space.py:18-21` 模型默认值是正确 JSON，迁移 `:25` 是损坏 JSON，`create_all` 与迁移产出的表默认值不一致 |

**核验小结**：清单 10 条全部属实、定位准确，可以放心按它排 Week 1；但它**只覆盖"v2→v3 已知缺陷"，没有覆盖"v3 语义在代码里的实现缺口"**（source/dedup_key/身份列/观察分析器全部零实现），也没有发现下面 §2 的 2 个 🔴。

---

## 2. 新发现的问题（差距清单遗漏）

### 🔴 N1：LLM 分析返回空/非列表时，pending_events 被无条件删除（P1-1 未落地）

- **位置**：`core/learning.py:90-115`（`_run` 无 decisions 判空直接执行 `:106-110` 的全量 `delete`）；对照 `docs/yd-memory-service/05-solutions.md:190-229`（P1-1"LLM 分析失败时事件不丢失"）
- **问题**：`05-solutions.md` P1-1 声称已修复（分析返回空时事件保留、累加 `retry_count`、3 次后写 `learning_logs` 标记 failed），**代码没有实现**。当前 `_run` 的步骤 5 无条件删除本批次全部事件。触发路径：LLM 返回合法 JSON 但**不是数组**（如 dict）→ `learning.py:217` 的 `enumerate(parsed)` 遍历 dict 键 → `:218` 的 `isinstance(item, dict)` 过滤 → decisions=[] → 事件全删；LLM 返回 `[]` 同理。
- **影响**：用户/Agent 提交的记忆素材静默丢失，无日志、无重试、无告警——正是第一轮评审 🔴3.1 与 P1-1 要堵的洞，**当前代码仍然漏**。`retry_count` 列（`pending_event.py:28`）从未被读取/递增（grep 证实）。
- **修复建议**：`_run` 在 `decisions` 为空且 `learning_mode=llm` 时：事件保留、`retry_count += 1`、超 3 次写 `LearningLog(decision_action='failed', error_message=...)` 后再删；非 llm 模式空 decisions（如 heuristic 恒非空）按现有逻辑即可。
- **验证方式**：单测——mock LLM 返回 `[]` / `{"not":"a list"}` → 断言事件仍在 `pending_events`、`retry_count` 递增；连续 3 次 → 事件删除且 `learning_logs` 出现 `failed` 行。

### 🔴 N2：`uv run yd-memory` 启动入口缺失（文档化命令即失败）

- **位置**：`backend/pyproject.toml:25`：`yd-memory = "yd_memory_service.main:main"`；`src/yd_memory_service/main.py`（50 行）**没有 `def main()`**（grep `def main` 全包零命中）；`AGENTS.md:21` 文档化 `uv run yd-memory` 为启动命令
- **问题**：console script 指向不存在的入口，`uv run yd-memory` 直接 `AttributeError`。
- **影响**：按仓库文档第一步启动服务即失败；演示/评审第一印象差。
- **修复建议**：二选一——在 `main.py` 加 `def main(): uvicorn.run("yd_memory_service.main:app", host=settings.host, port=settings.port)`；或把 entry point 改为 `yd_memory_service.main:app` 并删掉 console script。顺带修正 `AGENTS.md` 命令描述。
- **验证方式**：`uv run yd-memory` 起服务 → `/health` 返回 `{"status":"ok"}`。

### 🟡 N3：v3 收件箱/身份语义在模型与迁移里零实现（清单把实现量低估了）

- **位置**：`core/models/pending_event.py:10-30`（无 `source`、无 `dedup_key`）；`core/models/agent_space.py:10-25`（无 `api_key_hash`/`api_key_prefix`）；`core/models/learning_log.py:10-41`（无 `source` 列）；`alembic/versions/edac4c069c8c_initial_all_5_tables.py` 同
- **问题**：v3.1 的核心概念——收件箱 `source` 维度、观察幂等 `dedup_key`+唯一索引、per-space API Key——在代码里**一个都没有**。`MemoryManager.memorize`（`manager.py:39-56`）不接受 source/dedup_key；`learning.py` 的分析按 `event_type` 分派而非按 `source` 分派。
- **影响**：Week 1/2 的"迁移修正（source/dedup_key/api_key/learning_logs.source）"不是修 bug，是**新增 ~30% 的 schema + 逻辑**；`05-solutions.md` 也没有这些项的 P 级条目。清单的"修复与扩展"框架让排期显得比实际轻。
- **修复建议**：把这些列合并进**同一条** v3 基线迁移（与 N1 修复、query_db 删除同批），一次 `alembic upgrade` 到位，避免 Week1/2 两次动表。
- **验证方式**：`alembic upgrade head` 后 `\d pending_events` 见 `source`/`dedup_key` 与部分唯一索引；`agent_spaces` 见 `api_key_hash`/`api_key_prefix`。

### 🟡 N4：flush 端点收 body 内 `agent_id`，且 Dify workflow 模板仍是 v2 形态

- **位置**：`api/webhooks.py:13-20`（`FlushRequest.agent_id` 必填）；`docs/dify-workflow-template.yml`（头部注释第 4 条"Flush 节点 → Body → 改成你的 agent_id"）
- **问题**：v3.1 §集成指南规定 flush 用 `Authorization: Bearer <space_key>`、body 只带 `session_id`；现状是 body 明文传 `agent_id`（无鉴权），模板文档与 v3.1 不一致（违反 CLAUDE.md 文档同步规则）。
- **影响**：身份层 Week 2 落地前可用；落地时 flush 契约必须改，模板必须同步改，否则"谁都能指定 agent_id flush 别人空间"。
- **修复建议**：Week 2 身份层一并把 flush 改为依赖注入 `agent_id`（中间件解析 key）；同步更新 `dify-workflow-template.yml`。
- **验证方式**：无 key 调 flush → 401；带 A 的 key 无法 flush B 的事件。

### 🟡 N5：`ts_rank` 计算后被丢弃，中文下重排器退化（RecallOrchestrator 排序质量风险）

- **位置**：`core/long_term/pg_store.py:74-82`（`select(..., rank)` 但 `return [row[0] for row in rows]` 丢 rank）；`orchestrator/ranker.py:9-26`（`intent.lower().split()` 空格分词）
- **问题**：① tsvector 的 `ts_rank` 分数在 `search()` 里被丢弃，召回后再由 `ranker` 重新打分；② `ranker` 用 `.split()` 分词，**中文整句无空格 → intent 是单 token**，`title_overlap`/`content_overlap` 几乎恒为 0 或 1，重排得分≈`weight×0.4`。对刚入库（weight=1.0）的记忆，最终顺序≈创建序。设计承诺的"weight×0.4 + title 重叠×0.4 + content 重叠×0.2"对中文基本不生效。
- **影响**：Recall 三路的"规则重排"是项目核心卖点之一（RecallOrchestrator 智能编排），中文下实际退化为"热记忆权重序 + 冷记忆 tsvector 序"的简单拼接；演示时可能看到 Top5 与意图无关。
- **修复建议**：最小改动——`search()` 返回携带 rank 的对象/元组，`ranker` 把 `ts_rank` 并入得分（如 `weight*0.4 + rank*0.6`）；或 ranker 对中文用字符 n-gram/按 zhparser 分词后做重叠。建议 Week 1 与 P0-3 验证同批做，直接用 100×20 数据评估 Top5 质量。
- **验证方式**：P0-3 数据集上断言"期望命中的记忆出现在 recall Top5"的比例 ≥ 设计阈值。

### 🟡 N6：`review_status` 从未参与召回过滤（审核流是装饰性的）

- **位置**：`core/long_term/pg_store.py:48-93,97-115`（`get_hot`/`search` 均无 `review_status` 条件）；`core/learning.py:256-264`（store 时 `review_status` 用默认 `'pending'`）
- **问题**：v3.1 §DB Schema/防幻觉纪律承诺"pattern 默认 `review_status=pending`，审核后才 `approved` 进入召回"；但当前**所有**新记忆默认 pending 且照样被召回（无过滤）。V1 记忆也许可以"pending 即召回"，但这条纪律对 V1.5 pattern 是硬约束，现在代码里没有任何机制。
- **影响**：V1.5 pattern 上线时必须改召回层（filter `review_status='approved'`）；现在不改会导致 V1.5 返工；若 V1 就演示"审核"功能，则功能是假的。
- **修复建议**：Week 1 定策略并在 `get_hot`/`search` 加开关参数（如 `include_pending: bool`），V1 默认含 pending（简化），V1.5 pattern 走 `approved` 过滤。
- **验证方式**：造一条 `review_status='pending'` 与一条 `approved` 的记忆，验证过滤开关行为。

### 🟡 N7：zhparser 容器路径三环断裂（P0-3 验证的前置条件缺三环）

- **位置**：`docker/Dockerfile.pg-zhparser:1`（`postgres:16-alpine`，与 compose 的 15 错位）；`:14` `COPY init-db.sql`（`docker/` 目录下只有 Dockerfile，**构建上下文没有 init-db.sql**，直接 `COPY` 必失败）；`backend/init-db.sql:2` 只 `CREATE EXTENSION pg_trgm`——**没有 `CREATE EXTENSION zhparser`，也没有 `CREATE TEXT SEARCH CONFIGURATION zhparser`**
- **问题**：即使换用 zhparser 镜像，`_available_ts_configs`（`pg_store.py:42-46`）查询 `pg_ts_config WHERE cfgname='zhparser'` 也永远查不到——因为**没有任何地方创建 zhparser 文本搜索配置**。P0-3 的验证对象（zhparser 中文召回）实际上不存在。
- **影响**：V1 Week 1 按现有文件执行"接入 zhparser 并验证 P0-3"会得到一个必然"不通过"或"误通过（simple 兜底）"的结果，浪费一天且误导决策。
- **修复建议**：① Dockerfile 基镜像统一为 compose 所用的 `postgres:15-alpine`（或 compose 升 16，二选一并对齐）；② 修构建上下文（`init-db.sql` 放对路径或用 `docker build -f docker/Dockerfile.pg-zhparser yd-memory-service/`）；③ `init-db.sql` 增加 `CREATE EXTENSION IF NOT EXISTS zhparser; CREATE TEXT SEARCH CONFIGURATION zhparser (PARSER = zhparser);` 并把触发器函数改为 `to_tsvector('zhparser', ...)`；④ 存量行重索引。
- **验证方式**：全新 volume 起 compose → `SELECT cfgname FROM pg_ts_config WHERE cfgname='zhparser'` 命中 → `_available_ts_configs` 返回 zhparser。

### 🟡 N8：触发器配置与查询配置耦合在 'simple'，zhparser 上线需"双改 + 重索引"

- **位置**：`init-db.sql:7,15`（触发器用 'simple' 建向量）；`pg_store.py:69-82`（查询优先用 `zhparser` 配置）
- **问题**：`search_vector` 由触发器用 'simple' 生成，查询却优先用 'zhparser' 分词——**向量与查询配置不一致时命中为空**。`pg_store.py:69-82` 的循环是"先 zhparser、空结果再 simple"，行为上不会出错，但 zhparser 查询对 simple 构建的向量通常空命中，等于多跑一次无效查询且永远拿不到 zhparser 的分词收益。更关键的是：切到 zhparser 后**存量行**的 `search_vector` 不会自动重建，需要全表 UPDATE。
- **影响**：P0-3 验证通过/不通过的判断会被配置错位污染；V1.5 codebase 蒸馏的知识卡若在 zhparser 链路修复前入库，中文召回同样差。
- **修复建议**：触发器与查询统一配置（一个常量），Week 1 提供 `REINDEX`/重建向量的迁移脚本（`UPDATE long_term_memories SET search_vector = ...` 触发触发器重算）。
- **验证方式**：P0-3 数据集在 zhparser 下重跑；断言命中率与延迟达标。

### 🟡 N9：Dify workflow 模板的 Flush 节点与 v3.1 身份模型不一致（文档同步欠账）

- **位置**：`docs/dify-workflow-template.yml`（文件头注释 + Flush HTTP 节点配置）
- **问题**：模板仍是 v2 形态——Body 传 `agent_id`；v3.1 要求 `Bearer <space_key>` + 仅 `session_id`。CLAUDE.md 明确"文档同步规则：01-design 为最终决策"，模板是文档集一部分，需同步。
- **修复建议**：Week 3 集成文档时一并更新模板（加 Authorization header 说明、去 agent_id）。
- **验证方式**：按更新后模板在 Dify 导入并跑通 flush。

### 🟢 G1：`database_url_sync` 与 `psycopg2-binary` 依赖未使用

- **位置**：`config.py:16`（`database_url_sync`）；`pyproject.toml:11`（`psycopg2-binary`）
- **说明**：全仓库无引用（grep 证实）；alembic 走 asyncpg URL。V1 清理或说明用途即可。

### 🟢 G2：CORS 全开 + MCP 头日志 + 无鉴权（内网演示可接受，公网前必须收紧）

- **位置**：`main.py:33-38`（`allow_origins=["*"]`）；`mcp/server.py:110`（以 WARNING 级日志打印完整 headers 含 `X-Agent-ID`）
- **说明**：v3.1 已声明"V1 内网信任边界"，设计决策成立；保留即可，但建议把 header 日志降到 DEBUG（`X-Agent-ID` 属身份信息，WARNING 级噪音且入日志）。

### 🟢 G3：`seed_steel.sql` 是独立业务演示库 DDL

- **位置**：`backend/seed_steel.sql`（106 行：`steel_products`/`steel_price_policy`/`steel_orders`/`steel_order_items` + 数据）
- **说明**：与 yd-memory 服务表（`agent_spaces` 等）同库并存、未隔离 schema——演示时注意命名冲突/混库；配合观察 push feeder 使用是合理的演示资产，属 Week 2 产物。

---

## 3. 重点方向专项核验（任务指定检查点）

### 3.1 MCP SSE 的 contextvars `_agent_id` 传播（handle_sse 与 call_tool 是否同一上下文）

**结论：当前配置（mcp 1.28.1，见 `uv.lock`）下**传播**成立，但依赖隐式机制、零测试、未来易碎**。

证据链（实读 mcp SDK 源码 `.venv/.../site-packages/mcp/`）：
- `mcp/server/sse.py`：`handle_post_message` 只在 **POST 请求自己的 task 上下文**里做校验，然后 `await writer.send(session_message)` 把消息推入内存流（`server/sse.py:270-272`）——POST handler 本身**不**处理工具调用；
- `mcp/server/lowlevel/server.py:646-695`：`run()` 在 **SSE 连接 task 上下文**（即 `mcp/server.py:106-116` 的 `handle_sse`，`_agent_id.set(aid)` 就发生在该 task 内）消费 read_stream，对每条消息 `tg.start_soon(self._handle_message, ...)`（`:684`）；anyio task group 的子 task **继承创建者的 contextvars** → `call_tool`（经 `_handle_message` 派发）能读到 `_agent_id`。
- 经验证据：`spike/mcp-echo/RESULT.md:35-36`（`X-Agent-ID` header 完整透传 ✅）+ `SPIKE-REPORT.md:19`（P0-1 #5 ✅）。

风险：这条链路**没有测试钉住**（无 tests），且 `_agent_id` 默认 `""` 时 `call_tool` 返回的是字符串错误 `"X-Agent-ID header 未设置"`（`server.py:38-41`）而非结构化 401——换 transport（如 streamable-http）或 SDK 改消息处理方式时静默失效。建议：补一个"SSE 连接→POST 消息→工具返回"的集成测试（spike 已有素材），并让错误响应带明确状态码。

### 3.2 advisory lock 与 AsyncSession 事务的交互

**结论：逻辑正确，无数据竞态；有三处值得注意。**

- 锁是 **session 级** advisory lock（`learning.py:62-77`），加锁/解锁发生在同一 session（同一物理连接，`database.py:7` pool 内 session 生命周期绑定连接）——满足"必须同连接"前提 ✅；
- 并发两个 flush（两个请求→两个 session→两条连接）：B 的 `pg_try_advisory_lock` 返回 false → 跳过（`:67-69`），不会出现重复处理 ✅；B 返回的 body 是 `{"status":"ok","processed":0}` 而非设计/05-solutions P1-2 要求的 `{"status":"skipped","reason":"another_flush_running"}`（`manager.py:60-71`）——语义可接受但建议对齐文档；
- `_run` 内 `SELECT ... FOR UPDATE`（`:81-88`）+ 后续 LLM 网络调用（`_llm_analyze`）发生在**同一未提交事务**中——行锁跨网络 I/O 持有，LLM 慢时锁持有时间长；由于 advisory lock 已挡住同 agent 并发，实际风险低，🟢 可接受；
- `hashtext('flush_<agent_id>')` 是 32 位哈希，跨 agent 理论碰撞会误锁——概率可忽略，🟢。

### 3.3 `_llm_analyze` 的 JSON 解析与回退一致性

见 §2 N1 + 清单第 7 条：未剥离 ```json 围栏、失败回退 `_direct_analyze`（与 docstring 的 "heuristic" 不符）、非数组 JSON 静默空决策 + 事件被删（🔴N1）。另注意 `MemoryType(item.get(...))`（`learning.py:215`）遇未知枚举抛 `ValueError` 会被外层 except 吞掉走 direct 回退——行为可用但 `llm_raw_response` 没机会落库。

### 3.4 tsvector 触发器与 Alembic 建表顺序

见 §1 第 3 条核验：迁移建触发器、函数在 `init-db.sql`——docker 标准序能过、alembic 优先序必挂；建议把函数 DDL 内联进迁移（`op.execute`），`init-db.sql` 只保留扩展，彻底解耦。

### 3.5 `init-db.sql` 用 'simple' 而设计要 zhparser

见 §2 N7/N8：三环断裂 + 配置错位，是 Week 1 第一天的活，且是 P0-3 的前置。

### 3.6 asyncpg 双连接（query_db 待删）

`mcp/server.py:75-98` 用 `asyncpg.connect(settings.database_url)` 直连，绕过 SQLAlchemy session——唯一的双连接源；D3 已判死刑，Week 1 删除即可（连同 `config.py:16` 死配置）。

### 3.7 `decay_weights` 硬编码

见 §1 第 6 条：`learning.py:113` 硬编码 0.95/0.1，`agent_spaces.config` 里的 `decay_per_day`/`min_weight` 从未被读。修复：`flush` 时从 space.config 取参传给 `decay_weights`。

### 3.8 `load_memory` 越权

见 §1 第 4 条：`mcp/server.py:51-54` 无归属校验，🔴 属实。

### 3.9 `seed.py` 与 Alembic 双轨建表

见 §1 第 10 条：双轨 + schema drift（模型默认值 vs 迁移默认值不一致）。建议 seed.py 改为"纯数据"脚本（假设表已由 alembic 建好），或干脆删除、以 pytest fixture 代替。

### 3.10 `spaces.py` 无鉴权

见 §1 第 9 条：属实；Week 2 身份层一并处理（含 create_space 生成 `space_key` 并只展示一次——当前 `spaces.py:14-18` 只返回 `agent_id`，**根本没有生成 API Key**）。

### 3.11 无 tests

属实：`backend/tests/` 不存在，`pyproject.toml:27-29` 已配 `asyncio_mode="auto"` + testpaths 但无文件。05-solutions 里所有 P1/P2 的验证方法都要求单测，等于全部欠账。

---

## 4. 架构自洽性检查（维度 1）

v3.1 的三维模型（产物 memory/wiki/pattern × 来源 chat/example/observation/codebase × 触发 webhook-flush/cron）**纸面自洽**：D6（source×trigger 正交）、D8（产物分层）、D9/D10（codebase 蒸馏定位）互相咬合，batch 不走收件箱的例外（§代码库蒸馏）有明确理由。**承诺未兑现（"文档说已做、代码/机制没有"）清单**：

| 承诺（01-design 位置） | 现状 | 证据 |
|------|------|------|
| "`query_db` 已按 D3 删除"（§决策记录 D3、§检索侧） | 仍暴露给 Dify | `mcp/tools.py:73-90` |
| "`load_memory` 增加归属校验（修复 v2 越权）"（§检索侧） | 未实现 | `mcp/server.py:51-54` |
| "v3 强制约束：`llm` 模式每次分析必须写入 `llm_raw_response`"（§DB Schema） | 从未写入 | 见 §1 第 5 条 |
| "所有来源共用同一条 flush 管线（advisory lock、审计、衰减全部复用）"（§核心概念模型） | 管线存在，但**分析器只按 event_type 分派，无 source 分派**；observation 分析器、codebase 事件管线均不存在 | `learning.py:94-99` |
| "衰减全部复用" | 硬编码 0.95/0.1 | `learning.py:113` |
| §REST API 全表（spaces keys / memories / wiki / recall / learning/events / observations） | 仅 `spaces` CRUD + `learning/flush` 存在 | `main.py:41-42` |
| §知识收件箱 DDL（source、dedup_key、唯一索引、ON CONFLICT） | 模型与迁移均无 | §2 N3 |
| §身份与接入层（per-space API Key、hash 存储、只展示一次） | 零实现 | `agent_space.py`、`spaces.py:14-18` |
| "pattern 审核后才进入召回"（防幻觉纪律） | 召回无 review_status 过滤 | §2 N6 |
| "每次入站 + 每次蒸馏决策均留 learning_logs"（观察护栏） | "入站留日志"的确切语义（learning_logs 记 observation 入站？）设计未定义 | `01-design.md` §c 护栏 |

另有两处**设计级悬空**（不是代码问题，是设计未闭环，V1.5 开工前要定）：
- 观察分析器的**契约**（prompt、决策 JSON 格式、产物是 memory 还是 wiki 的映射规则、失败处理）只有一段话草图（01-design §c"蒸馏（flush 时）"）；05-solutions 无对应 P 条目。Week 2 只有一周，分析器契约不冻结会拖累端到端演示。
- codebase 蒸馏"叙述层 md 与知识卡双产物一致性"（01-design §代码库蒸馏·开放问题 2 自认未闭环）：batch 一次产出两层 OK；但 **event 增量重生成知识卡时，叙述层 md 谁回写**未定义。

---

## 5. codebase 蒸馏（V1.5 候选主线）可行性评审（维度 3）

**结论：技术方向成立、与 v3 骨架兼容（收件箱 `source=codebase` + 既有 flush + `metadata.protected`），作为简历/演示主线是合理选择；但存在 1 个顺序依赖、3 个设计未闭环、1 个检索配合风险。**

1. **顺序依赖（硬约束）**：codebase 知识卡走 `wiki_documents` + tsvector 检索，而中文检索链路（zhparser）是 V1 Week 1 才修的（§2 N7/N8）。**V1.5 开工前提是 V1 的 P0-3 验证通过**；若 P0-3 不通过转 pgvector，知识卡检索方案要重写。V1.5 排期必须排在 V1 验收之后，不能并行。
2. **设计未闭环**：
   - 叙述层 md 与知识卡的一致性（见 §4）；
   - batch 不走收件箱 → 不写 `learning_logs`，"全部知识可溯源可审计"的不变式对 codebase 产物断裂。建议：batch 至少写一条批处理 run 级日志（agent_id、repo 路径、commit SHA、生成卡片数、token 用量），保持审计链可 trace；
   - event 增量的入站方式："CI 提交后回调 push"需要一个**新入站端点**，但 v3.1 §REST API 表里没有 codebase webhook——要么新增 `POST /api/v1/codebase/refresh`，要么复用 observations 的 push 语义（event 结构不同，建议新增端点，别硬塞 observation）。
3. **检索配合风险（召回质量与延迟）**：
   - 知识卡与业务 wiki **同表混池**（`wiki_documents`），`recall` 的 wiki 路（`recall.py:38`，Top3）**无法按 source 过滤**；codebase 卡量大（4000 文件仓库按模块分页可能数百张）时，Top3 + ts_rank 的召回质量未验证。建议 V1.5 MVP 给 `WikiStore.search` 加 `source`/`tags` 过滤参数，并跑一次"蒸馏 100 张卡 × 20 query"的召回评估；
   - "知识卡 description ≤100 字"与 Skill 机制（description 是匹配命脉）是**已知张力**：模块一句话描述信息量有限，跨模块检索（"配置约定"、"错误处理习惯"）可能召回不足。Qoder 的做法是多路召回兜底；本项目 V1 只有 tsvector 一路——MVP 阶段建议先接受，用演示仓库实测，别在描述策略上过度设计；
   - 延迟：GIN 索引（`wiki_document.py:29`）下每 agent 数百张卡无压力；但 `_available_ts_configs` 每次搜索多 1 次 `pg_ts_config` 查询（`pg_store.py:42-46`，`wiki/db_store.py:30-34` 同样）——量级可忽略，🟢。
4. **成本与体量治理**：Qoder"4000 文件 ≈ 120 分钟"是官方参考值；本仓库 5839 个文件大部分是 vendored（`.venv`/`node_modules`/`site-packages`），**蒸馏器的第一个功能必须是"按排除清单过滤 + 报出有效文件数与预估 token"**，否则全量扫一次就把演示预算烧光。MVP 建议：固定蒸馏**一个小型真实仓库**（几千行源码），把"排除→扫描→分批生成→写卡"跑通即可，不追求 4000 文件规模。
5. **实现顺序建议（切片）**：V1.5a = batch 全量 + `protected` 跳过 + 双层产物（1.5-2 周，可演示"代码库自动出 wiki + 人改不被覆盖"）；V1.5b = event 增量（指纹 diff + 模块聚合 + 单模块重生成 + md 同步策略，1-1.5 周）。样例学习（pattern 归纳）维持"下游"定位，资源富余再做。

---

## 6. 时间与范围现实性（维度 4）

**V1 3 周：方向可行，但 Week 1 被低估；V1.5 2-3 周：偏乐观，建议拆两片。**

- **Week 1 实际内容盘点**（按清单 + §2 新发现）：修 edac4c 迁移、zhparser 三环（N7）+ P0-3 验证、触发器配置统一 + 存量重索引（N8）、`source`/`dedup_key`/`api_key_*`/`learning_logs.source` 加列迁移（N3）、P1-1 数据完整性（N1）、`llm_raw_response` 写入、decay 读配置、`_llm_analyze` 解析修复、删 query_db、补 `main()`（N2）、补 pytest 基建（从零）——**这不止 1 周，接近 1.5 周**。Week 1 建议明确"day 1-2 建表链路 + 入口 + P0-3；day 3-5 数据完整性与审计 + pytest；day 6-7 v3 契约迁移 + query_db 删除"。
- **P0-3 的分支成本没算进排期**：若不通过转 pgvector，V1 整体 +1~2 周（依赖、迁移、召回层重写）。设计把决策点放在 Week 1 末尾是对的，但排期里要写清分支。
- **V1.5 2-3 周含五要素（batch + event + protected + 双层 + 体积治理）偏乐观**：event 增量管线本身 ≥1.5 周；batch ≥1 周；加上 LLM 调参与成本治理。按 §5 拆 V1.5a/V1.5b 才与现实匹配。
- **简历/演示驱动下的范围纪律（04 号文档立场）**：
  - **可砍/延后**：`spaces` 的 PUT/DELETE 归档流（`spaces.py:36-56`，无真实使用场景）；`config` 可调参数（decay/min_weight/max_memories 现在全是死配置，N3 之外不建议继续扩展）；管理前端保持 2 页承诺不变；
  - **不可砍**：pytest 基建（05-solutions 每个验证方法都要单测，没有它"验证"全是口头）、端到端演示脚本（feeder，Week 2 承诺）、审计链路完整性（llm_raw_response，简历卖点）。
  - **设计不足（该加没加）**：observation 分析器契约（§4）、zhparser 修复路径没有写进 01-design Week 1 任务（清单只有"zhparser 接入并验证"，三环断裂细节没写）、`main()` 入口。

---

## 7. 风险清单（维度 5）

| # | 风险 | 级别 | 现状/证据 | 缓解 |
|---|------|------|----------|------|
| R1 | zhparser 中文召回未验证（P0-3），且前置三环断裂 | 🔴 高 | `SPIKE-REPORT.md:62` 自认未验证；§2 N7/N8 | Week 1 day 1-2 先修链路再跑 100×20 验证；<40% 立即切 pgvector（设计已备兜底，但要把分支成本计入排期） |
| R2 | 学习管线数据丢失 + 审计断裂 | 🔴 高 | §2 N1（空决策删事件）；清单第 5 条（llm_raw_response 空） | Week 1 day 3-5 修 P1-1 + 写审计；补单测 |
| R3 | 幂等（dedup_key）零实现 | 🔴 中 | §2 N3；观察 push 的唯一防线缺失 | v3 契约迁移加列 + 部分唯一索引 + `ON CONFLICT DO NOTHING`；补"同 dedup_key 双投只产一条"测试（设计验证方案已列） |
| R4 | 多平台接入（Dify/DSH/REST）身份一致性 | 🟡 中 | MCP 侧已验证（spike）；REST 身份（API Key）零实现；两侧无共享测试 | Week 2 身份层落地后补"同一 Space MCP/REST 读写一致"集成测试（01-design 验证方案已列） |
| R5 | LLM 成本 | 🟡 低 | 默认 `learning_mode=heuristic`（`config.py:32`）零 LLM 成本；llm 模式才有费用（05 P3-2 估算表） | 保持默认 heuristic；演示时显式切 llm 并记录单次成本 |
| R6 | API Key 安全 | 🟡 中 | 未实现；设计 SHA-256+prefix 方案合理 | 实现时用 `secrets.token_urlsafe(32)` 生成；hash 用 SHA-256 足够（key 高熵）；**MCP 无鉴权是内网信任边界**（01-design 已声明）——任何公网暴露前必须先加 MCP 端鉴权；`X-Agent-ID` 可被伪造（谁能连 /mcp/sse 就能冒任何 agent_id） |
| R7 | 中文重排退化 | 🟡 中 | §2 N5 | ts_rank 并入重排；P0-3 数据上评估 Top5 |
| R8 | review_status 纪律悬空 | 🟡 低 | §2 N6 | Week 1 定召回策略，V1.5 pattern 必须过滤 |
| R9 | Dify 版本漂移 | 🟡 低 | spike 基于 1.16.0-rc1（`RESULT.md:5`）；MCP 支持行为随版本变 | 集成文档记录 Dify 版本与踩坑（SSRF proxy 等，`RESULT.md:15-29`） |
| R10 | seed 双轨 schema drift | 🟢 低 | §1 第 10 条 | seed.py 改纯数据脚本或删除 |

---

## 8. 可开工性判断（维度 6）

### 总体结论

**可以按 V1 Week 1 开工。** 方案方向与 Spike 结论支持继续；差距清单 10 条全部属实可执行；没有发现需要推翻方向的架构级问题。**但开工的第一天不能做"补 pytest 或观察入站"，必须先把两个让开发环境起不来的 🔴 修掉**（建表链路 + 启动入口），否则 `alembic upgrade head` 与 `uv run yd-memory` 两个文档化命令当场失败。

### 开工前必须处理的 TOP 3

1. **修复/重做初始迁移与建表链路**（🔴）：edac4c 损坏默认值（`:25`）+ 触发器函数外置 `init-db.sql` + zhparser 三环断裂（§2 N7）+ Dockerfile 构建上下文缺失。产出：**一条**能 `alembic upgrade head` 到头的迁移（触发器函数 DDL 内联进迁移），compose 换 zhparser 镜像，init SQL 建 zhparser 扩展+配置，触发器与查询统一用 zhparser。验证：全新 `docker compose up -d` → `uv run alembic upgrade head` 零报错 → `SELECT cfgname FROM pg_ts_config WHERE cfgname='zhparser'` 命中。
2. **修复学习管线数据完整性与审计欠账**（🔴）：P1-1（空/失败决策不删事件、`retry_count` 递增、写 `learning_logs.failed`）+ `llm_raw_response` 写入（把 LLM raw 从 `_llm_analyze` 传到 `_commit_and_log`）。验证：05-solutions P1-1 的 3 条检查清单。
3. **一次性落地 v3 契约迁移 + 删除 query_db + 修启动入口**（🔴/🟡）：`source`/`dedup_key`/`api_key_hash`/`api_key_prefix`/`learning_logs.source` 加列 + dedup 部分唯一索引；flush 契约改为从 key 解析身份（Week 2 身份层的前置）；`mcp/tools.py` 回到 3 个工具；`main.py` 补 `def main()`（或改 entry point）。验证：`uv run yd-memory` 起服务；MCP tools 列表 3 个；`uv run pytest` 绿。

### 建议开工顺序（含对 01-design 落地路径的修正意见）

1. **修正意见 A（Week 1 重排）**：把 Week 1 拆为三段——day 1-2「建表链路 + 入口 + P0-3（zhparser 验证）」，day 3-5「数据完整性 + 审计 + pytest 基建」，day 6-7「v3 契约迁移 + query_db 删除」。原清单把"补 pytest"与"修复"并列、把 v3 契约迁移与身份层分在两周，实际应**同批一次迁移**落地（§2 N3），避免 Week 2 二次动表。
2. **修正意见 B（P0-3 前置条件显式化）**：在 01-design Week 1 任务里补写"zhparser 三环修复（镜像/扩展配置/触发器配置/重索引）"为 P0-3 的前置步骤（§2 N7/N8），否则验证对象不存在。
3. **修正意见 C（V1.5 拆片）**：V1.5 拆 V1.5a（batch + protected + 双层产物，1.5-2 周，可演示）与 V1.5b（event 增量，1-1.5 周）；开工前先闭环 3 个设计点——叙述层 md 与知识卡一致性策略、batch 的批处理审计日志、codebase 入站端点（§5）。
4. **修正意见 D（文档同步）**：把 §2 的 🔴N1/N2 补进 01-design §实现现状与差距清单（P1-1 未落地、`main()` 入口缺失），保持"清单=代码事实"（CLAUDE.md 同步规则）；`dify-workflow-template.yml` 随 Week 3 更新为 v3.1 flush 形态。
5. **修正意见 E（观察分析器契约）**：Week 1 末尾冻结 observation 事件→分析器契约（prompt、JSON 决策格式、memory/wiki 产物映射、失败处理、`llm_raw_response` 落库），Week 2 才有东西可写（§4）。

---

## 附录：证据索引（文件:行号）

| 主题 | 证据 |
|------|------|
| 迁移默认值损坏 | `yd-memory-service/backend/alembic/versions/edac4c069c8c_initial_all_5_tables.py:25` |
| 触发器函数外置 | `edac4c069c8c_initial_all_5_tables.py:100-102`；`backend/init-db.sql:5-19` |
| 'simple' 触发器 | `backend/init-db.sql:7,15` |
| compose 无 zhparser | `yd-memory-service/docker-compose.yml:3` |
| zhparser Dockerfile 断裂 | `yd-memory-service/docker/Dockerfile.pg-zhparser:1,14`；`docker/` 目录无 init-db.sql |
| load_memory 越权 | `backend/src/yd_memory_service/mcp/server.py:51-54` |
| query_db 存续 | `mcp/tools.py:73-90`；`mcp/server.py:61-62,75-98`（asyncpg 直连 `:83`） |
| llm_raw_response 未写 | `core/models/learning_log.py:33`（列存在）；`core/learning.py` 无赋值 |
| 衰减硬编码 | `core/learning.py:113`；`core/models/agent_space.py:18-21` |
| _llm_analyze 解析/回退 | `core/learning.py:163,207,222` |
| 空决策删事件 | `core/learning.py:90-115`（`:106-110` 无条件 delete）；对照 `05-solutions.md:190-229` |
| retry_count 未用 | `core/models/pending_event.py:28`；`core/learning.py` 无引用 |
| main 入口缺失 | `backend/pyproject.toml:25`；`src/yd_memory_service/main.py`（无 `def main`）；`AGENTS.md:21` |
| source/dedup_key 缺失 | `core/models/pending_event.py:10-30`；`core/learning.py:94-99`（按 event_type 分派） |
| api_key 缺失 | `core/models/agent_space.py:10-25`；`api/spaces.py:14-18`（不生成 key） |
| flush 收 body agent_id | `api/webhooks.py:13-20` |
| ts_rank 丢弃 + 中文分词退化 | `core/long_term/pg_store.py:74-82`；`orchestrator/ranker.py:9-26` |
| review_status 不生效 | `core/long_term/pg_store.py:48-93,97-115`；`core/learning.py:256-264` |
| 无 tests | `backend/tests/` 不存在；`backend/pyproject.toml:27-29` |
| seed 双轨 | `backend/seed.py:10-23` |
| contextvars 传播（SDK 证据） | `.venv/.../mcp/server/sse.py:270-272`；`.venv/.../mcp/server/lowlevel/server.py:646-695`；mcp 版本 `uv.lock`（1.28.1）；spike `mcp-echo/RESULT.md:35-36`、`SPIKE-REPORT.md:19` |
| 模板仍传 agent_id | `yd-memory-service/docs/dify-workflow-template.yml`（头部注释 + Flush 节点） |
