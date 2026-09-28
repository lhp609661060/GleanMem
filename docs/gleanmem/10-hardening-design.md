# 10 · 加固设计（v3.9 提案）：安全边界、flush 事务、事件死信

> 状态：**已实施待验收**（代码与迁移已落地，尚未合入 01-design v3.8 主线）。配套实施规格见 `11-hardening-spec.md`，其文末「实施记录」列出入码后与本文的偏差。
> 输入：全库设计缺陷分析（2026-09-28），四个缺陷簇各对应一个决策 D16–D19，杂项归 D20。

## 0. 缺陷清单（设计输入）

| # | 缺陷 | 现状依据 |
|---|------|----------|
| C1 | MCP SSE 零鉴权，`X-Agent-ID` 明文可伪造，可读写任意 Space | `mcp/server.py:85-97`；01-design 07 评审 R6 自认 |
| C2 | `POST /api/v1/spaces` 无鉴权，未认证者可建 Space 拿合法 key | `api/spaces.py:49-50` |
| C3 | flush 在 advisory lock + `SELECT FOR UPDATE` 的事务内同步调 LLM，锁/事务时长不可控；事件无批上限 | `core/learning.py:82-118,366-369`；07 §3.2 自认 |
| C4 | P1-1 重试 ≥3 次后**删除事件**（留审计不留数据），无死信；与调度 at-most-once 组合后"最坏是丢失" | `core/learning.py:575-600`；scheduler.py:12-14 |
| C5 | 杂项：admin key 非常量时间比较、`api_key_hash` 无索引、CORS 全开硬编码、MCP 异常 `str(exc)` 回显、chat 源 REST 事件无 dedup_key | `api/deps.py:38`、迁移 `edac4c069c8c`、`main.py:52-57`、`mcp/server.py:77`、`api/learning.py` |

## 1. 范围

**做**：C1→D16、C2→D17、C3→D18（chat/observation 主路径）、C4→D19、C5→D20。

**不做**（明确出界，防 scope 蔓延）：

- 检索方案重评估（pgvector / 多路召回）：独立立项，先补独立验证集。
- V1.5a 批蒸馏与 V1.5b 增量的并发竞写互斥：涉及 CLI 直连路径，另议。
- codebase 增量分支在锁内调 LLM 的完整重构：本期只吃批上限与 status 过滤（见 D18 取舍）。
- recall 埋点 / 治理层：维持 08 号文冻结状态。

## 2. 设计决策

### D16 · MCP 端密钥鉴权（修 C1）

MCP SSE 建连时要求 `X-Space-Key: <space_key>`：

1. 对 key 做 SHA-256，查 `agent_spaces.api_key_hash`（含 `status != 'archived'`）解析出 `agent_id`；
2. 若请求同时带 `X-Agent-ID`，必须与 key 解析出的 agent_id 一致，否则 403；
3. 缺 `X-Space-Key` 或 key 无效 → 401，不建立 SSE 连接；
4. 工具执行层的身份来源从"信 header"改为"信 key 解析结果"，`X-Agent-ID` 降级为冗余校验项。

**理由**：Dify 的 MCP 注册本就支持自定义 header（V1 用 `X-Agent-ID` 正是这么配的），把 header 内容从 agent_id 换成 space_key 即可，接入成本一次配置。REST 与 MCP 从此共用同一套身份强度（哈希查库、归档即失效），消除 07 R6。

**取舍**：

- 新增 `YDM_MCP_AUTH_REQUIRED`（默认 **true**）。逃生口供内网演示/spike 脚本用；关掉时行为回退为旧 `X-Agent-ID` 约定。默认值取安全侧，升级方需在 Dify 侧补 header。
- 不做 key 缓存：SSE 是长连接，建连时一次查库足够，不值得引失效逻辑。
- `memorize` 工具参数里的 `dedup_key` 等保持不变；`agent_id` 仍永不进工具参数（身份不变式不动）。

### D17 · Space 创建仅 admin（修 C2）

`POST /api/v1/spaces` 加 `require_admin`。`YDM_ADMIN_KEY` 未配置时该端点返回 503（明确"平台未初始化"而非静默开放）。

**理由**：创建即发 key，是最高权限入口，只能属于平台管理员。现有 `seed_demo_space.py` 直写库、pytest fixture 自建数据，均不受影响；前端空间管理页本就走 admin 身份。

**取舍**：`GET /spaces`、`PUT /{agent_id}`、`POST keys`、`DELETE` 的现有授权矩阵不动（space 可自助改自己的 config / 轮换自己的 key 是当前既定语义，收成"只读 + admin 管理"是另一个议题，不混入本次）。

### D18 · flush 两阶段化：LLM 移出事务 + 批上限（修 C3）

flush 拆为三段，互斥语义保持 advisory lock 不变：

```
阶段0  互斥：独占连接上 pg_try_advisory_lock('flush_<agent_id>')
阶段1  短事务 T1（工作 session）：SELECT 事件快照（status='pending'，
       ORDER BY created_at LIMIT batch_size），只读，立即提交
——     无事务、无行锁：调 LLM（chat/observation 分析器）
阶段2  短事务 T2（工作 session）：SELECT … FOR UPDATE 按事件 id 重选
       （仍在库的才处理）→ 应用决策 → 删已消费事件 → 提交
阶段0' 解锁并释放独占连接
```

要点：

- **锁连接与工作 session 分离**。session 级 advisory lock 挂在连接上、跨事务存活，但 AsyncSession 提交后连接归池。因此锁放在一条独占连接上持有全程；代价是每次 flush 多占一条 idle 连接（LLM 期间），可接受——并发 flush 本就被互斥，不会叠加。
- **阶段2 按 id 重选**是正确性关键：LLM 期间事件被删（如手工清理）则该事件跳过，绝不为已消失的事件写决策。
- **批上限** `YDM_FLUSH_BATCH_SIZE`（默认 50）：超出的留在收件箱等下一次 flush。批次分片天然限定了单次 LLM 请求的 payload 规模。
- chat 分支（决策与事件 1:1 绑定）与 observation 分支（决策与批绑定、任一失败整批保留）的既有语义在阶段2 原样保留，只是作用在"重选后仍在库"的子集上。
- codebase 增量分支：本期仅吃批上限与 status 过滤，`IncrementalDistiller` 仍在锁内调 LLM（单模块粒度、带 token 预算，风险量级不同），完整两阶段化留待后续。**已知残留，明确记录。**

**理由**：行锁/事务的持有时间从"LLM 网络往返"缩到毫秒级；LLM 失败时数据库完全无锁无事务。heuristic/direct 模式无 LLM，走同一路径（阶段1/2 间无耗时），不另设分支。

### D19 · 事件死信化（修 C4）

`pending_events` 加 `status` 列（`'pending' | 'dead'`，默认 `'pending'`）：

- P1-1 重试达阈值（`YDM_EVENT_MAX_RETRIES`，默认 3，即现行为）时：仍写 FAILED 审计日志，但事件**标记 `status='dead'` 而非删除**；
- flush 的 SELECT 一律过滤 `status='pending'`，dead 事件不参与后续处理（重试计数不再增长，与"丢数据"的差别是数据还在）；
- 复活端点：`POST /api/v1/learning/events/{event_id}/revive`（space key 或 admin；校验归属），置 `status='pending', retry_count=0`；
- dead 事件查询：`GET /api/v1/learning/events?status=dead`（require_agent，按 caller 过滤）。

**理由**：scheduler 的 at-most-once 与"重试后删事件"组合的最终语义是数据丢失，两处决策各自合理、组合未被审视。死信化把"丢"改为"冻"，复活成本一个端点；收件箱尺寸有批上限与 dead 分离双保险，不会无限膨胀拖垮 flush。

**取舍**：不做自动重生、不做死信告警（无告警通道是全局现状）；不区分 429/500 调整重试节奏——cron tick 本身提供了时间间隔，manual flush 连打场景由 max_retries 阈值兜底。

### D20 · 杂项加固（修 C5）

| 项 | 方案 |
|----|------|
| admin key 比较 | `hmac.compare_digest` 替换 `==`（`api/deps.py:38`） |
| `api_key_hash` | 加唯一索引（新迁移）——鉴权查库从顺序扫描变索引命中 |
| CORS | `YDM_CORS_ORIGINS`（逗号分隔，默认 `*` 维持现状），不再硬编码 |
| MCP 异常回显 | 工具异常对客户端返回通用 `{"error": "internal error"}`，细节只进日志 |
| chat 源 dedup | `POST /api/v1/learning/events` 接受可选 `dedup_key`，落库走既有 `(agent_id, dedup_key)` 唯一部分索引（MCP `memorize` 已支持，此处补齐 REST 侧） |

## 3. 数据库变更（单条 Alembic 迁移）

1. `pending_events` 加列 `status VARCHAR(8) NOT NULL DEFAULT 'pending'`；
2. `pending_events` 加索引 `(agent_id, status, created_at)`（flush 快照查询路径）；
3. `agent_spaces` 加唯一索引 `(api_key_hash)`。

存量数据：迁移即生效，无回填。

## 4. 新增配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `YDM_MCP_AUTH_REQUIRED` | `true` | MCP SSE 是否强制 X-Space-Key |
| `YDM_FLUSH_BATCH_SIZE` | `50` | 单次 flush 最多取的事件数 |
| `YDM_EVENT_MAX_RETRIES` | `3` | 事件重试达阈值转 dead |
| `YDM_CORS_ORIGINS` | `*` | 逗号分隔来源列表 |

## 5. 兼容性与升级注意

- **破坏性变更一处**：MCP 默认强制鉴权。Dify 侧需把自定义 header 从 `X-Agent-ID: <agent_id>` 改为 `X-Space-Key: <space_key>`（可两者都带，agent_id 作为一致性校验）。内网演示可临时 `YDM_MCP_AUTH_REQUIRED=false`。
- `POST /spaces` 需 admin key：脚本化建 Space 的流程改为直写库或带 admin key。
- 前端管理台：创建 Space 已在 admin 视图内，无需改动；dead 事件管理本期不做 UI。

## 6. 测试要点

- MCP：无 key 401 / 错 key 401 / key-agent_id 不一致 403 / 归档 key 401 / 合法 key 正常读写；`mcp_auth_required=false` 回退旧行为。
- 创建鉴权：无 key 401、space key 403、admin key 201、未配 admin key 503。
- flush 两阶段：LLM 期间事件被外部删除 → 阶段2 跳过且无孤儿决策；批上限 → 一次只消费 N 条；并发 flush 互斥仍由锁保证。
- 死信：重试达阈值事件仍在库且 `status='dead'`；dead 不进 flush；revive 后重新处理；审计日志照写。
- 杂项：常量时间比较（行为不变，代码审查）、hash 唯一索引（轮换后旧哈希即失效不冲突）、CORS 配置生效、MCP 异常不泄内部信息、chat dedup 幂等。
