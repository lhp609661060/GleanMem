# yd-memory-service：多智能体外挂记忆平台（v3.1）

> **版本说明**：v3 是对 v2 的产品方向重写；v3.1 在 v3 冻结范围上，把 06 调研的 codebase 蒸馏纳入为正式候选方向。v2 的检索、存储、审计设计大部分保留，变化集中在四处：
> 1. **定位**：从「Dify 专用插件」→「多智能体（Dify / DSH / 其他）共享的外挂记忆平台」
> 2. **学习体系**：从单一 memorize/flush 管线 → 四类学习需求（聊天喂养 / 样例学习 / 业务观察 / 定时任务）共用一套知识收件箱
> 3. **业务观察**：确立 **push-first**（业务方推事件）；pull 抓取推迟到其他项目、成熟后以 producer 形式回融
> 4. **代码库蒸馏**（v3.1 新增）：纳入 [06-repo-wiki-research.md](./06-repo-wiki-research.md) 的 Qoder Repo Wiki 调研，作为 V1.5 候选主线，见 §代码库蒸馏
>
> 决策记录见 §决策记录。v2 评审过程见 [02-review-round1.md](./02-review-round1.md) ~ [04-review-round3.md](./04-review-round3.md)，遗留修复手册见 [05-solutions.md](./05-solutions.md)，v3.1 架构评审见 [07-architect-review.md](./07-architect-review.md)。
>
> **v3.1 修订记录**（按 07 评审落 5 条修正意见）：Week 1 拆三段（day 1-2 建表链路+入口+P0-3 / day 3-5 数据完整性+审计+pytest / day 6-7 v3 契约迁移+删 query_db）；P0-3 前置 zhparser 三环修复；V1.5 拆 V1.5a/V1.5b；差距清单补 N1/N2 等评审新发现；观察分析器契约 Week 1 末冻结。
>
> **v3.4 修订记录**（2026-08）：N6 关闭——召回层落地 `review_status` 分级过滤（pattern 必须 approved、flagged/deprecated 全类型排除），补审核端点。**差距清单全部 🔴/🟡 已关闭**，剩余仅 🟢 seed.py 双轨与 V2 backlog。
>
> **v3.3 修订记录**（2026-08）：V1.5a + V1.5b 全部落地（45 测试绿；batch 端到端 10 步、增量端到端 8 步全通过）。实现中发现并修复 flush 分派 bug（`source != "observation"` → 白名单 `in ("chat","example")`，否则 codebase 事件被学成 memory）。
>
> **v3.2 修订记录**（2026-08，V1 验收后、V1.5 开工前）：V1 全部 🔴/🟡 差距关闭（23 测试绿、P0-3 100%）；闭环 V1.5 的 5 个开工前设计点 —— 叙述层 md 定为知识卡的**单向投影**（D11）、新增 `codebase_runs` **run 级审计**表（D12）、新增独立 `/api/v1/codebase/*` 端点（D13）、CodebaseAnalyzer 模型与 token 硬预算（D14）。

## 决策记录（v2 → v3）

| # | 决策 | 理由 |
|---|------|------|
| D1 | 定位升级为「多智能体外挂记忆」，不再绑定 Dify | 用户明确：给 Dify、DSH 或其他智能体做外挂记忆 |
| D2 | 身份统一抽象为 `agent_id`；REST 用 **per-space API Key**，MCP SSE 沿用 `X-Agent-ID` header | 一把 API Key 同时解决「DSH 走 REST 接入」和「业务系统 push 观察事件」两件事 |
| D3 | **删除 `query_db` 工具** | 原为 Dify 测试用的临时工具；业务数据访问改由 push-first 观察学习承载，不再让 LLM 直连业务库 |
| D4 | 观察学习 **push-first**；pull 推迟到其他项目，成熟后以 producer 形式回融 | push 信任成本最低、工程最小；pull 的连接器/快照复杂度不在本项目背 |
| D5 | `pending_events` 增加 `source` 维度（chat/example/observation）与 `dedup_key` | 四类学习共用收件箱；push 幂等必需 |
| D6 | 学习来源与触发时机**正交分离**（source × trigger） | 修正「定时任务学习」的定位：它是 trigger，不是第 4 种 source |
| D7 | 样例学习定级 **V1.5**，产物为 pattern 型记忆 + 溯源 | 技术含量最高，但有开放问题未闭环，不塞进 V1 |
| D8 | 知识产物分层：memories / wiki（V1）+ pattern（V1.5） | 扁平记忆表接不住归纳性规则 |
| D9 | **06 调研正式纳入**：codebase 蒸馏成为 V1.5 候选主线 | 需求来自 DSH 工作区场景（Repo Wiki 式代码库认知）；与 v3 骨架兼容，但需新增 batch 管线与修订保护 |
| D10 | **codebase 蒸馏取代样例学习成为 V1.5 主线候选**，样例学习降级为其下游 pattern 归纳 | 「代码如何写决策」与代码蒸馏同源，不平行立项 |
| D11 | **叙述层 md 是知识卡的单向投影**（知识卡为唯一事实源），md 默认进 Git | 服务端无仓库写权限（push-first 信任纪律），「服务端回写 md」不可实现；投影可重建 → 无需 merge 策略，接受 md 短期滞后 |
| D12 | 新增 `codebase_runs` 表做 **run 级审计**（不是逐卡决策级） | batch 是生成而非学习决策，不走收件箱 → 须补审计链，使「卡 → run → commit SHA → 文件」可 trace |
| D13 | 新增独立 `/api/v1/codebase/*` 端点，**不复用 observations** | 事件结构不同，硬塞会污染 observation 分析器契约（07 评审 §5.2 建议） |
| D14 | CodebaseAnalyzer 用既有 `YDM_LLM_*` 模型 + Space 级 token 硬预算 + dry-run 先报后跑 | 不新增供应商依赖；防一次全量烧光演示预算（07 评审 §5.4） |

---

## Context

yd-agent 项目已冻结代码开发。四层记忆体系 + LLM 驱动学习模型是该项目的技术资产，现以独立服务形式提供给**多个智能体平台**使用（Dify 为第一个集成方，DSH 及其他 Agent 平台走 REST 接入）。

**核心矛盾不变**：Agent 侧的 LLM 判断力有限，不适合承担复杂的多路检索决策。检索编排收敛到后端的 **Recall Orchestrator**（智能编排函数，非 Agent），Agent 侧只暴露高层工具，隐藏记忆分层、语义/关键词/权重多路检索、合并去重。这降低 Agent 认知负担，让检索策略独立演进。

**学习侧的变化**：v2 只有一条「memorize → flush」管线；v3 把它泛化为**知识收件箱模型**——所有学习素材（聊天、样例、业务观察）统一进收件箱，由 LearningModel 在合适时机统一分析。详见 §核心概念模型。

---

## 核心概念模型

这是 v3 最重要的一节：三个正交维度取代 v2 的单一管线。

### 维度 1：知识产物（存什么）

| 产物 | 落点 | 形态 | 版本 |
|------|------|------|------|
| **memory** | `long_term_memories` | 单条事实/反馈/规则 | V1 |
| **wiki** | `wiki_documents` | 人工维护的文档/业务词典 | V1 |
| **pattern** | `long_term_memories`（`type='pattern'`）+ 溯源 | 从样例**归纳**出的通用规则 | V1.5 |

### 维度 2：学习来源 source（素材从哪来）

| source | 含义 | 素材形态 |
|--------|------|---------|
| `chat` | 聊天框主喂养（需求 a） | 单条事实/规则，Agent 调 `memorize` 提交 |
| `example` | 样例学习（需求 b） | 样例语料集：聊天记录样例、代码决策样例 |
| `observation` | 业务数据观察（需求 c） | 业务方 push 的观察事件（快照/diff） |
| `codebase` | 代码库蒸馏（06 候选方向，V1.5） | 代码库扫描产物：模块清单、文件指纹、diff 变更集 |

### 维度 3：触发时机 trigger（何时分析）

| trigger | 含义 | 实现 |
|---------|------|------|
| `webhook-flush` | 会话/业务动作结束时 | 外部调 `POST /api/v1/learning/flush`（Dify 工作流结尾节点、业务系统回调） |
| `cron` | 定时批量（需求 d） | V1：外部 cron 调 flush；V2：内置调度器 |

### 三维度组合（需求 → 设计映射）

| 用户需求 | source | 分析方式 | 产物 | trigger |
|---------|--------|---------|------|---------|
| a 聊天框主喂养 | chat | memorize → LearningModel（llm/heuristic/direct） | memory | webhook-flush |
| b 样例学习 | example | LLM 归纳管线（V1.5） | pattern + 溯源 | cron 或手动 |
| c 业务观察 | observation | 观察蒸馏（LLM 归纳业务规律） | memory / wiki + 溯源 | webhook-flush 或 cron |
| d 定时任务学习 | —（非 source） | 对任意来源积压素材批量跑 flush | 对应来源的产物 | cron |
| 代码库蒸馏（V1.5 候选主线） | codebase | CodebaseAnalyzer：batch 全量 + event 增量 | wiki 双层（叙述层 md + 知识卡） | cron 或 CI webhook |

**不变式：所有来源共用同一个收件箱 `pending_events` 和同一条 flush 管线**（advisory lock、审计、衰减全部复用）。新学习方式 = 新增「素材怎么进收件箱」+「收件箱里怎么分析」，不新起炉灶。

---

## 架构总览

```
┌─────────────┐   ┌──────────────┐   ┌────────────────┐
│   Dify      │   │  DSH / 其他   │   │   业务系统      │
│  MCP SSE    │   │  REST        │   │  REST push     │
│ X-Agent-ID  │   │ Bearer key   │   │ Bearer key     │
└──────┬──────┘   └──────┬───────┘   └───────┬────────┘
       └─────────────────┼───────────────────┘
                         ▼
        ┌────────────────────────────────────────┐
        │          yd-memory-service             │
        │                                        │
        │  接入层：MCP SSE / REST                 │
        │  （统一解析身份 → agent_id）            │
        └────────────────┬───────────────────────┘
                         ▼
        ┌────────────────────────────────────────┐
        │  知识收件箱 pending_events              │
        │  source: chat|example|observation      │
        │  + dedup_key（幂等）                    │
        └────────────────┬───────────────────────┘
                         ▼  trigger: webhook-flush | cron
        ┌────────────────────────────────────────┐
        │  LearningModel（按 source 选择分析器）  │
        └───────┬──────────────────┬─────────────┘
                ▼                  ▼
        ┌─────────────┐   ┌──────────────────┐
        │long_term_   │   │ wiki_documents   │
        │memories     │   │（+ pattern V1.5） │
        │(+learning_logs 审计)                │
        └──────┬──────┘   └────────┬─────────┘
               └────────┬──────────┘
                        ▼
        ┌────────────────────────────────────────┐
        │  RecallOrchestrator（读侧：三路检索）   │
        └────────────────────────────────────────┘
依赖：PostgreSQL（含 zhparser 扩展）
```

---

## 身份与接入层

### 隔离单元：`agent_id`

一切数据（记忆、wiki、事件、日志）按 `agent_id` 隔离。**`agent_id` 永远不进入工具参数或业务字段**，只出现在接入层，由接入层解析后注入核心。

### 接入方式与身份解析

| 接入方 | 协议 | 身份来源 | 鉴权 |
|--------|------|---------|------|
| Dify | MCP SSE | `X-Agent-ID` header（Dify MCP Server 配置时填写） | V1 无鉴权，**内网信任边界** |
| DSH / 其他 Agent | REST（`/api/v1/recall` 等） | `Authorization: Bearer <space_key>` → 查得 agent_id | per-space API Key |
| 业务系统（观察 push） | REST（`/api/v1/observations`） | 同上 | per-space API Key |
| 管理端（前端） | REST | 同上（admin key，V1.1） | per-space API Key |

### API Key 设计（V1）

- 每个 Agent Space 创建时生成一把 `space_key`（如 `ydm_<32位随机>`），**只展示一次**
- 库中只存 `api_key_hash`（SHA-256）+ `api_key_prefix`（前 8 位，用于 UI 辨认）
- 请求带 `Authorization: Bearer <space_key>` → 中间件 hash 比对 → 注入 `agent_id`
- 轮换接口 `POST /api/v1/spaces/{agent_id}/keys` 推迟到 V1.1
- **Dify MCP 路径不受影响**：仍走 `X-Agent-ID`，与 API Key 两者都解析到同一个 `agent_id` 概念

> 为什么 MCP 不加鉴权：Dify 的 MCP 注册只支持自定义 header，不支持 Bearer 流程；V1 面向内网部署（v2 已明确「认证鉴权 V1 内网」），MCP 端点按信任网络处理。若将来暴露公网，MCP 端再加 key 校验（在 `X-Agent-ID` 之外允许 `Authorization`）。

### 新增接入层不改变核心

未来支持 OpenAI tools 协议 / 自建 Agent SDK 时，只是**多一个接入层**，身份解析后同样落到 `agent_id` + MemoryManager，核心零改动。

---

## 知识收件箱（pending_events v3）

v2 的 `pending_events` 是「memorize 的暂存表」；v3 升级为**所有学习素材的统一收件箱**：

```sql
CREATE TABLE pending_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agent_spaces(agent_id),
    source VARCHAR NOT NULL DEFAULT 'chat',  -- chat | example | observation | codebase(V1.5)
    event_type VARCHAR NOT NULL,             -- 来源内细分：
                                             --   chat: user_feedback | agent_mark
                                             --   observation: entity_changed | entity_created | stat_snapshot ...
                                             --   example: 由 b 归纳管线定义
    context TEXT NOT NULL,                   -- 素材内容（chat=描述；observation=事件 JSON；example=样例文本）
    marked_type VARCHAR,
    dedup_key VARCHAR,                       -- 幂等键（observation 必填 = 业务方 event_id）
    metadata JSONB DEFAULT '{}',             -- 溯源信息（见下）
    session_id VARCHAR,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_pending_dedup ON pending_events(agent_id, dedup_key)
    WHERE dedup_key IS NOT NULL;
CREATE INDEX idx_pending_agent ON pending_events(agent_id, created_at);
```

**关键约定**：

- `dedup_key` + 唯一索引实现幂等：业务方 push 重试、我方重复消费都不产生重复记忆（`ON CONFLICT DO NOTHING`）。
- `metadata` 承载**溯源**：`source`、来源系统、实体标识、样例 id 列表、快照指纹等。知识落地时随 `learning_logs` 一并固化，实现「每条知识可 trace 回素材」。
- flush 管线（advisory lock → SELECT FOR UPDATE → 分析 → commit → 日志 → 删除 → 衰减）**保持不变**，只是分析器按 `source` 分派。

---

## 四种学习方式

### a. 聊天框主喂养（V1，现状已基本覆盖）

Agent 在对话中调用 `memorize` 提交值得长期记住的信息 → 写入收件箱（`source=chat`）→ 会话结束 webhook 触发 flush → LearningModel 三模式（llm / heuristic / direct，默认 heuristic）分析入库。

**与 v2 的差距只有一个**：v2 的触发绑死 Dify 工作流。v3 中 flush 是标准 REST 接口 + API Key，**任何平台**（DSH 编排的 Agent 结束回调、业务系统回调）都能触发，Dify 只是其中一个调用方。

### b. 样例学习（V1.5，核心差异化）

**概念**：从 N 个样例中归纳出通用规则——如从多条聊天记录样例归纳「厦门客户发货用顺丰」，从代码决策样例归纳「错误处理应如何写」。

**管线（草图，V1.5 细化）**：

```
examples 表（人工上传 / 后台导入样例语料）
      ↓
归纳器（LLM 单次调用：输入样例集 → 输出候选规则 + 引用样例 id）
      ↓
候选规则 → 落 long_term_memories(type='pattern', metadata.evidence_ids=[...])
      ↓
人工审核（review_status: pending → approved | flagged）
```

**防幻觉纪律（写死）**：
1. 每条 pattern **必须携带 evidence_ids**，无引用的规则直接拒绝
2. pattern 默认 `review_status=pending`，审核后才 `approved` 进入召回
3. 归纳的 LLM 原始输出写入 `learning_logs.llm_raw_response`（补齐 v2 欠账）

**召回层分级过滤（N6，2026-08 已实现，`core/long_term/pg_store.py::review_conditions`）**：

| 类型 | pending | approved | flagged / deprecated |
|------|---------|----------|---------------------|
| `pattern`（归纳类，`MODERATED_TYPES`） | ❌ 不召回 | ✅ 召回 | ❌ |
| 其他类型（user/feedback/project/reference/task） | ✅ 召回 | ✅ 召回 | ❌ |

理由：LLM 归纳是幻觉高发区，「无审核不召回」把幻觉挡在召回层之外；而聊天/观察素材是人喂的，V1 简化为 pending 即可召回（否则 V1 全部记忆都召不回）。`include_pending=False` 可收紧为「所有类型必须 approved」，供管理端/演示用。

**边界（易错点）**：flush 的**去重比对**必须显式 `apply_review_filter=False`——它不是召回，若施加过滤则同名 pending 记忆无法被发现，会重复入库而非 merge。

**开放问题（V1.5 启动前必须闭环）**：
- 样例从哪来：人工上传样例集？还是从历史 chat 自动抽取（那会与 c 重叠）？
- 「代码如何写决策」类样例的形态：代码 diff？决策描述 + 结果对？
- 归纳出的规则与已有 memory 冲突时的合并策略

### c. 业务观察学习（V1，push-first）

**定位**：业务方把「业务行为」作为事件推过来，服务持续积累、定时蒸馏成业务知识。观察 ≠ 查询——不做 LLM 直连业务库（`query_db` 已按 D3 删除）。

#### 入站接口

```
POST /api/v1/observations        Authorization: Bearer <space_key>
```

```json
{
  "event_id": "ship-20260716-001",        // 幂等键：业务方生成，全局唯一
  "entity":   "shipment",                 // 业务对象
  "event":    "status_changed",           // 发生了什么
  "change":   {"from": "待确认", "to": "待发货"},   // 这就是 diff
  "payload":  { "...": "..." },           // 可选原始上下文（限 4KB）
  "occurred_at": "2026-07-16T10:00:00Z"   // 业务发生时间，非推送时间
}
```

服务端：校验 → 转写为 `pending_events`（`source=observation`、`dedup_key=event_id`）→ 幂等入库 → 立即返回 `{status: "accepted"}`。

#### 蒸馏（flush 时）

`source=observation` 的事件由**观察分析器**处理：LLM 以「归纳业务规律」为 prompt（如「同一实体多次事件揭示的稳定规则」），产出 memory 或 wiki 条目，`metadata` 固化溯源（来源系统、entity、事件 id 列表）。

**契约（2026-08 冻结，实现据此编码）**：

- **输入**：同 agent 的 `source=observation` 事件列表（entity/event/change/payload/occurred_at 已序列化进 context）
- **LLM prompt（llm 模式）**：
  - system：`你是业务观察分析助手。输入是业务系统推来的观察事件列表。归纳稳定可复用的业务规则：1) 单事件即明确规则（如状态流转定义）直接 store；2) 多条同实体事件呈现稳定模式 → 归纳成一条；3) 一次性噪音事件 → discard。返回 JSON 数组：[{"action":"store|discard","title":"≤40字","content":"完整规则描述","memory_type":"reference|feedback","target":"memory|wiki","description":"target=wiki 时必填 ≤100字","evidence":["event_id",...]}]`
  - user：事件列表 JSON
- **防幻觉**：`evidence` 必须非空（无引用即拒绝，同 pattern 纪律）；`llm_raw_response` 必落库
- **产物映射**：`target=memory` → `long_term_memories`（metadata 固化 source/origin/evidence）；`target=wiki` → `wiki_documents`（id=`obs-<uuid8>`、description 必填 ≤100 字）
- **失败处理**：同 P1-1（空/坏 JSON → 保留事件 + retry_count，≥3 次写 failed 日志）
- **heuristic/direct 模式**：不做归纳，直接存为 reference 记忆（title=事件描述 ≤200 字），metadata 固化溯源

#### 护栏（观察比聊天更自主，护栏更严）

| 护栏 | 规则 |
|------|------|
| 鉴权 | 必须 API Key，按 agent_id 隔离 |
| 幂等 | `dedup_key` 唯一索引，重复 push 无害 |
| 限长 | payload ≤ 4KB，超限拒绝而非截断 |
| 脱敏 | 约定：业务方推送前自行脱敏；服务不承诺对 payload 内容二次清洗（V1） |
| 审计 | 每次入站 + 每次蒸馏决策均留 `learning_logs` 记录 |

#### pull 的未来回融纪律

V1 不做 pull（连接业务库、快照 diff），该能力在其他项目单独积累。为保证将来无缝回融，**核心层只认规范化的 `ObservationEvent` 对象**：pull 成熟后只是一个新的 producer，把抓取到的 diff 转成同一种 `ObservationEvent` 投进收件箱，下游零改动。工程约束：**不要把 push 特有字段（如业务方 event_id 格式）写进核心层**。

### d. 定时任务学习（trigger，不是 source）

- **V1**：外部 cron 定时调 `POST /api/v1/learning/flush`（带 API Key），对积压素材批量分析。示例：每日凌晨对昨天的 observation 事件跑蒸馏。
- **V2**：内置调度器（APScheduler），Space 配置 `schedule` 字段（cron 表达式），按 Space 独立调度。
- d 与 b/c 的组合就是「定时归纳样例」「定时蒸馏观察」——trigger 与 source 正交的价值所在。

---

## 代码库蒸馏（source=codebase，V1.5 候选方向）

> 需求输入：[06-repo-wiki-research.md](./06-repo-wiki-research.md)（Qoder Repo Wiki 调研）。本节是它的设计落点，调研细节与对照表以 06 为准。

### 定位

为 DSH 等工作区场景提供**代码库认知层**：模块职责、调用关系、配置约定、变更注意事项。与 v3 现有 wiki 互补：

| | 业务 wiki（V1） | 代码库蒸馏（V1.5 候选） |
|---|---|---|
| 内容来源 | 人工维护 + 业务观察蒸馏 | 代码库自动蒸馏（LLM 批量分析 + diff 增量） |
| 服务对象 | Agent（Skill 检索） | 人（叙述层）+ Agent（知识卡层） |
| 更新方式 | 谁写谁负责 | 自动生成 + 人工修订保护 |

### 管线形态二分（关键建模）

codebase 蒸馏是 v3 引入的**第一种 batch 型学习管线**，与既有 event 型 flush 并列：

| 管线 | 形态 | 触发 | 路径 |
|------|------|------|------|
| **batch（初次全量）** | 独立批处理任务：LLM 批量分析 → 直接产出 wiki 文档 | 手动 / cron 首次 | **不走收件箱**（生成不是逐条学习决策，硬塞会污染收件箱） |
| **event（增量 diff）** | 文件指纹变化 → 事件进收件箱 → 复用 flush → 只重生成受影响模块 | cron 校验 / CI webhook | `pending_events`（`source=codebase`） |

不新增 trigger 类型：沿用 webhook-flush 与 cron 两种既有触发。

### 增量机制

- 指纹：文件内容哈希（V1.5 简化版；AST 签名与重命名识别 V2 再补）
- diff 变更集 → 按模块聚合 → 只重生成受影响模块（对齐 Qoder 单次 ≤10k 行约束）
- 触发：CI 提交后回调 push，或 cron 定时校验
- **实现要点（V1.5b 已落地）**：
  - 指纹比对以卡上 `metadata.fingerprints` 为基准，**无变化直接 skip，不花 token**；
  - 服务端零仓库访问权 ⇒ 重生成只用事件里附带的 snippet（≤4KB/文件），不读磁盘；
  - 每个事件独立成败：`updated` / `skipped_unchanged` / `skipped_protected` / `deleted` 消费出箱，`failed` 保留并递增 `retry_count`（复用 P1-1）；
  - 决策级写 `learning_logs`（`event_type=module_changed:<action>`），run 级写 `codebase_runs`（`mode=incremental`），两层审计都不断；
  - flush 的 source 分派必须是**白名单**（`in ("chat","example")`）——原 `!= "observation"` 会让 codebase 事件误入 chat 分支学成 memory。

### 人工修订保护（与增量捆绑发布，缺一不可）

- wiki 条目 `metadata.protected=true` → 自动更新跳过该条目
- 人改过的页面由管理端/编辑器标记 protected（V1.5 简化：手动标记）
- 反向同步（人的修订回写知识卡）V2 再做

### 产物形态（双层，混合形态）

- **叙述层（人读）**：`<repo>/.yd-memory/wiki/*.md`——模块职责、架构叙述，人在 IDE/编辑器直接读，可选随 Git 共享（对齐 Qoder 的「Wiki 给人读」）
- **知识卡层（Agent 读）**：注册进服务 `wiki_documents`，沿用 Skill 机制（description 检索 + 按需加载，即 Qoder 的「知识卡给 Agent 读」）
- 蒸馏器一次产出两层：叙述层落盘文件、知识卡落库

### 一致性策略：md 是知识卡的投影（闭环①，D11）

**问题**：batch 一次产出两层没有一致性问题；但 event 增量在服务端 flush 内执行，**服务端没有 repo 文件系统访问权**——它改不了 `<repo>/.yd-memory/wiki/*.md`。若把「服务端回写 md」作为设计，等于要求服务端持有仓库写权限，与 push-first 的信任纪律冲突。

**裁决**：**知识卡（DB）是唯一事实源，md 是它的本地投影（projection），不是并列副本。**

- **写方向单一**：`知识卡 → md`，永不反向。md 由**客户端侧 CLI**（蒸馏器同一入口，`ydm-distill sync`）拉取本 Space 的 `source=codebase` 知识卡，在本地渲染成文件；服务端只负责 DB。
- **batch**：客户端在本地跑蒸馏 → 上传知识卡 → 顺带落盘 md（一次调用，两层天然同步）。
- **event 增量**：服务端只更新知识卡并给库里打 `wiki_sync_pending=true`；md 在下一次 `ydm-distill sync`（人手动 / CI 一步）时更新。**接受"md 短期滞后于知识卡"**——Agent 读的是卡（始终最新），人读的 md 允许延迟，这是有意的取舍，不是缺陷。
- **投影是可重建的**：md 丢失/损坏不影响任何能力，重跑 sync 即恢复；因此 md **不参与**任何审计不变式。
- **`protected` 例外**：人在 md 里改过并标记 protected 的段落，sync 不覆盖（先读本地 md 的 protected 标记，跳过对应文件），且该文件对应的卡也不被 event 增量重写（见 §人工修订保护）。
- **叙述层是否进 Git（原开放问题 1）**：**默认进 Git**（`.yd-memory/wiki/` 提交，团队 pull 即得，对齐 Qoder）；因为它是可重建投影，冲突的解法永远是"重跑 sync"，不需要 merge 策略。不想共享的仓库自行 gitignore。

### batch run 级审计（闭环②，D12）

**问题**：batch 不走收件箱 → 不写 `learning_logs`，「全部知识可溯源可审计」对 codebase 产物断裂。

**裁决**：新增 `codebase_runs` 表记录 **run 级**审计（不是逐卡决策级——batch 是生成，不是学习决策）：

```sql
CREATE TABLE codebase_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agent_spaces(agent_id),
    mode VARCHAR NOT NULL,              -- batch | incremental
    repo_path TEXT NOT NULL,            -- 客户端上报的仓库标识
    commit_sha VARCHAR(40),             -- 蒸馏基线
    files_scanned INT DEFAULT 0,        -- 排除清单过滤后的有效文件数
    files_excluded INT DEFAULT 0,       -- 被排除数（体积治理可见性）
    cards_written INT DEFAULT 0,
    cards_skipped_protected INT DEFAULT 0,   -- 修订保护生效次数（演示要讲的数字）
    tokens_used INT DEFAULT 0,
    model VARCHAR(100),
    status VARCHAR NOT NULL DEFAULT 'running',  -- running | succeeded | failed
    error_message TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX idx_cbruns_agent ON codebase_runs(agent_id, started_at DESC);
```

- 每张知识卡的 `metadata.run_id` 指向产生它的 run → **卡 → run → commit SHA → 文件路径**审计链闭合。
- event 增量**同时**写 `learning_logs`（逐事件决策，走收件箱）**和** `codebase_runs`（mode=incremental，run 级汇总），两者不互斥。
- 审计入口：`GET /api/v1/codebase/runs`（复用 `learning/logs` 的分页形态）。

### 入站端点（闭环③，D13）

**裁决**：新增独立端点，不复用 `observations`（事件结构完全不同，硬塞会污染 observation 分析器契约）。

```
POST /api/v1/codebase/runs           # batch：开一个 run，返回 run_id（客户端上传卡时带上）
POST /api/v1/codebase/cards          # batch：批量 upsert 知识卡（带 run_id，跳过 protected）
POST /api/v1/codebase/refresh        # event 增量：CI/cron 推变更文件指纹 → 进收件箱（幂等）
GET  /api/v1/codebase/runs           # 审计查询
GET  /api/v1/codebase/cards?sync_pending=true   # CLI sync 拉取投影
```

`refresh` 的入站 body（幂等键 = `cb:{commit_sha}:{module}`）：

```json
{
  "commit_sha": "a1b2c3d",
  "changes": [
    {"module": "core/learning", "files": [{"path": "core/learning.py", "fingerprint": "sha256:..."}], "change": "modified"}
  ]
}
```

→ 按 module 聚合成 `pending_events`（`source=codebase`，`event_type=module_changed`，`dedup_key=cb:{sha}:{module}`），flush 时由 `CodebaseAnalyzer` 只重生成受影响模块的卡。**服务端不读代码**：文件内容由客户端在 `refresh` 时按需附带（≤4KB/文件的片段）或由 CLI 直接走 batch 路径重传该模块——V1.5b 选前者，保持服务端零仓库访问权。

### 体积治理

- 默认排除：`.venv` / `node_modules` / `site-packages` / 构建产物 / 锁文件（本仓库 5839 个文件里大部分是 vendored 依赖，是现成的反面教材）
- 按模块分页；知识卡 description 生成策略：模块职责一句话，≤100 字（对齐 Skill 机制约束）
- 成本参考：4000 文件仓库全量 ≈ 120 分钟（Qoder 官方参考值），实际以仓库规模为准

### 真实蒸馏实测（2026-08，deepseek-chat）

用 `scripts/verify_real_distill.py` 蒸馏本项目自身源码（`src/yd_memory_service`，10 个模块 / 44 个文件 / 137KB）：

| 指标 | 实测值 | 说明 |
|------|--------|------|
| 成功率 | **10/10** | 无解析失败、无 description 缺失 |
| 耗时 | **20s**（并发 4） | 单模块独立调用的并发设计有效 |
| token | **40,482**（预估 46,634） | 估算系数 `BYTES_PER_TOKEN=3` 偏保守 15%，作为预算护栏是安全方向 |
| description 长度 | min 44 / avg 76 / max 100 字 | 未触发截断上限即自然收敛，≤100 字约束不伤表达 |
| **中文召回（Top3）** | **10/10 = 100%** | 阈值 ≥40%，远超。首轮为 9/10，唯一 miss 源于评估脚本把期望值写死成单模块（"中文全文检索"在 `core/long_term` 与 `core/wiki` 都有实现，命中后者同样正确）；期望值改为集合后 100%，产出本身无缺陷 |

**结论**：07 评审 §5.3 担心的「模块一句话 ≤100 字撑不起跨模块检索」这一**已知张力实测不成立**——LLM 生成的 description 自带关键概念词（如「tsvector+zhparser」「Bearer space_key」），tsvector 匹配效果好。V1.5 的 description 策略保持不变，不需要引入多路召回兜底。

单仓全量成本外推：40k token / 44 文件 ≈ 每文件 0.9k token，4000 文件规模约 3.6M token——这正是 `codebase_token_budget`（默认 300k）存在的理由，大仓需显式调高并分批。

### 溯源（维持不变式）

`metadata` 固化：文件路径、commit SHA、指纹、生成时间——保持「每条知识可 trace 回代码」。

### 与样例学习的关系（D10）

「代码如何写决策」类样例与 codebase 蒸馏同源（从代码归纳模式）。**V1.5 主做 codebase 蒸馏，样例学习降级为其下游 pattern 归纳**（蒸馏产出的模块事实 → pattern 化），不再平行立项。

### 开放问题（V1.5 启动前闭环）

| # | 问题 | 状态 |
|---|------|------|
| 1 | 叙述层是否随 Git 提交共享 | ✅ 已闭环（D11）：默认进 Git，因其为可重建投影，冲突解法是重跑 sync |
| 2 | 叙述层与知识卡的同步策略 | ✅ 已闭环（D11）：知识卡为唯一事实源，md 是单向投影，接受 md 短期滞后 |
| 3 | CodebaseAnalyzer 模型选择与成本上限 | ✅ 已闭环（D14）：见下 |
| 4 | batch 的批处理审计日志 | ✅ 已闭环（D12）：新增 `codebase_runs` run 级审计表 |
| 5 | codebase 入站端点 | ✅ 已闭环（D13）：新增 `/api/v1/codebase/*`，不复用 observations |

**模型与成本上限（闭环③补，D14）**：

- 模型走既有 `YDM_LLM_*` 配置（不新增供应商），默认用与 flush 同一个便宜模型（`gpt-4o-mini` 级）；蒸馏 prompt 单模块独立调用，无跨模块上下文依赖 → 可并发、可断点续跑。
- **硬预算**：`codebase_runs` 累计 `tokens_used` 超过 Space 级上限（`agent_spaces.config.codebase_token_budget`，默认 300k）即中止 run 并写 `status=failed`，避免一次全量烧光演示预算。
- **先报后跑**：蒸馏器第一步是 dry-run——按排除清单过滤后报出「有效文件数 + 预估 token + 预估费用」，人确认再执行（对齐 07 评审 §5.4 的要求）。
- 演示规模锚定：**一个小型真实仓库（几千行源码）**，不追 4000 文件量级。

### 切片（V1.5a / V1.5b）

- **V1.5a** ✅ 已完成：batch 全量 + `protected` + 双层产物 + 体积治理 + `codebase_runs` 审计 + `WikiStore.search` source 过滤
- **V1.5b** ✅ 已完成：指纹 diff + 模块聚合 + 单模块重生成 + flush 侧 `source=codebase` 分派 + `ydm-distill refresh`
- **前置依赖**：V1 的 P0-3（zhparser 中文召回）验证通过 ✅；5 个开工前设计点已闭环（D11-D14）✅

---

## 检索侧（沿用 v2，微调）

### 三个 MCP 工具

与 v2 相同：`recall` / `load_memory` / `memorize`。

**v3 变更**：
1. **删除 `query_db`**（D3）：不再向 Agent 暴露任何 SQL 能力。
2. **`load_memory` 增加归属校验**：按 `agent_id` 过滤，只能加载本 Space 的记忆（修复 v2 实现的越权缺陷）。

### Recall Orchestrator

保持 v2 设计不变：并行三路（热记忆 weight Top20 + 冷记忆 tsvector Top5 + wiki tsvector Top3）→ 按 id 合并去重 → 规则重排（weight×0.4 + title 重叠×0.4 + content 重叠×0.2）→ Top5 + hint。**它是函数不是 Agent**：无 loop、无 tool calling、V1 无 LLM 决策。V2 可选小模型 cross-encoder 重排。

**v3 补充**：pattern（V1.5）进入召回后，排在 hot 路之前作为最高优先路（规则是稳定知识，权重应高于普通记忆）。

### Wiki Skill 机制

保持 v2 不变：`description` 是 tsvector 匹配命脉，管理端强制填写。业务观察蒸馏出的「业务词典」也落 wiki，享受同一套 Skill 加载。

---

## DB Schema（v3 全量）

### `agent_spaces`

```sql
CREATE TABLE agent_spaces (
    agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR NOT NULL,
    description TEXT,
    api_key_hash VARCHAR(64),            -- v3 新增：SHA-256(space_key)
    api_key_prefix VARCHAR(8),           -- v3 新增：UI 辨认用
    config JSONB DEFAULT '{"decay_per_day":0.95,"min_weight":0.1,"max_memories":5000,"learning_mode":"heuristic"}',
    status VARCHAR DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### `long_term_memories`

v2 结构不变，两处扩展：
- `type` 增加取值 `pattern`（V1.5，归纳性规则）
- `metadata` 约定键：`evidence_ids`（pattern 的溯源样例 id 列表）、`source`（chat/example/observation）、`origin`（来源系统/实体）

```sql
CREATE TABLE long_term_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agent_spaces(agent_id),
    type VARCHAR NOT NULL,       -- user | feedback | project | reference | task | pattern
    title VARCHAR NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    weight FLOAT DEFAULT 1.0,
    review_status VARCHAR DEFAULT 'pending',
    is_deleted BOOLEAN DEFAULT FALSE,
    search_vector tsvector,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### `wiki_documents`

v2 不变。`metadata` 增加溯源约定：`source=observation` 记录来源；`source=codebase` 记录文件路径 / commit SHA / 指纹 / `run_id`（指向 `codebase_runs`）；`protected=true` 标记人工修订保护（自动更新跳过）；`wiki_sync_pending=true` 表示知识卡已更新、md 投影待 sync（见 D11）。

**V1.5a 需补的检索配合**（07 评审 §5.3）：`WikiStore.search` 加 `source` / `tags` 过滤参数，避免 codebase 卡与业务 wiki 同表混池后挤占 Top3；上线前跑一次「蒸馏 100 张卡 × 20 query」召回评估（对齐 P0-3 的方法）。

### `codebase_runs`（V1.5a 新增）

见 §代码库蒸馏·batch run 级审计（D12）的 DDL。

### `pending_events`

见 §知识收件箱（v3 结构）。

### `learning_logs`

v2 结构 + `source` 列。**v3 强制约束**：`llm` 模式每次分析必须写入 `llm_raw_response`（v2 实现欠账）。

### `examples`（V1.5 新增，草图）

```sql
CREATE TABLE examples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agent_spaces(agent_id),
    kind VARCHAR NOT NULL,       -- chat | code_decision | ...
    content TEXT NOT NULL,       -- 样例原文
    tags JSONB DEFAULT '[]',
    status VARCHAR DEFAULT 'pending',  -- pending | inducted | rejected
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## REST API（v3）

鉴权：除 `/health` 外全部要求 `Authorization: Bearer <space_key>`（V1.1 前管理类接口暂用同 key）。

```
# Agent Space
POST   /api/v1/spaces                        # 创建（返回 space_key，仅一次）
GET    /api/v1/spaces
GET    /api/v1/spaces/{agent_id}
PUT    /api/v1/spaces/{agent_id}
DELETE /api/v1/spaces/{agent_id}             # 归档

# 记忆（V1.1 前端用，本期可只做查询）
GET    /api/v1/memories?agent_id=...&status=...&page=1&sort=weight
GET    /api/v1/memories/{id}
POST   /api/v1/memories                      # 手动录入
PUT    /api/v1/memories/{id}
DELETE /api/v1/memories/{id}
POST   /api/v1/memories/{id}/review          # 审核（pattern 必需人工审核） ✅ 已实现

# Wiki
POST   /api/v1/wiki                          # description 强制
GET    /api/v1/wiki
GET    /api/v1/wiki/{id}
PUT    /api/v1/wiki/{id}
DELETE /api/v1/wiki/{id}

# 检索（HTTP 集成用，与 MCP recall 等价）
POST   /api/v1/recall                        # { "intent": "..." }，agent_id 取自 key

# 学习
POST   /api/v1/learning/events               # = memorize（REST 版）
POST   /api/v1/learning/flush                # 触发分析（webhook / cron 共用）
GET    /api/v1/learning/logs?page=1

# 观察（v3 新增）
POST   /api/v1/observations                  # 业务方 push 观察事件（幂等）

# 代码库蒸馏（V1.5a 新增，见 §代码库蒸馏 D13）
POST   /api/v1/codebase/runs                 # batch：开 run，返回 run_id
POST   /api/v1/codebase/cards                # batch：批量 upsert 知识卡（带 run_id，跳过 protected）
POST   /api/v1/codebase/refresh              # event 增量：CI/cron 推变更指纹进收件箱（幂等）
GET    /api/v1/codebase/runs                 # run 级审计查询
GET    /api/v1/codebase/cards?sync_pending=true   # CLI sync 拉取 md 投影
```

---

## 集成指南

### Dify（沿用 v2 三节点 workflow）

```
[Start] → [Agent + recall/load_memory/memorize] → [HTTP Request: Flush] → [End]
Flush: POST {{MEMORY_URL}}/api/v1/learning/flush
       Headers: Authorization: Bearer {{space_key}}
       Body: { "session_id": "{{sys.conversation_id}}" }   # agent_id 由 key 解析
```

注意：v3 起 Dify 的 MCP 注册仍填 `X-Agent-ID`，而 Flush 的 HTTP 节点改带 API Key（Dify 支持自定义 header）。

### DSH / 其他 Agent（REST 直连）

1. 创建 Space，拿 `space_key`
2. 平台侧把 memory 调用翻译成 REST：检索用 `POST /api/v1/recall`，提交用 `POST /api/v1/learning/events`，会话结束回调 `POST /api/v1/learning/flush`
3. 若平台支持 MCP client，也可复用 SSE 端点（`X-Agent-ID` 方式）

### 业务系统（观察 push）

1. 创建 Space，拿 `space_key`
2. 按 §c 的 schema 调 `POST /api/v1/observations`，事件 id 自己生成、保证幂等
3. 演示场景：仓库内提供 **feeder 脚本**——读 `seed_steel.sql` 类数据，构造「发货单 创建→确认→发货」事件流灌入，全程不需要真业务库

---

## 落地路径

### 前置：Spike ✅ 已完成

见 [spike/SPIKE-REPORT.md](../../spike/SPIKE-REPORT.md)：MCP 通路可行、Agent 会主动调工具、`X-Agent-ID` 隔离有效。**唯一未验证项 P0-3（tsvector + zhparser 中文召回率）顺延到 V1 Week 1 day 1-2**，验证不通过（<40%）则直接换 pgvector（V1 排期 +1~2 周）。**P0-3 的前置是 zhparser 三环修复**（镜像 / 扩展与配置 / 触发器配置，见差距清单 N7/N8）——三环不修，验证对象不存在、测试必然误判。**P0-3 已于 2026-08 实测通过**：plainto_tsquery 的 AND 语义命中率仅 25%，改「AND 优先、空结果降级 OR + ts_rank」后 20/20 = 100%、p95 5.4ms——**保留 tsvector + zhparser 路线，不切 pgvector**。

### V1（约 3 周，在现有 v2 代码骨架上）

| 周 | 任务 |
|----|------|
| Week 1 · day 1-2 | **建表链路 + 启动入口 + P0-3**：重做初始迁移（修 edac4c 损坏默认值、触发器函数 DDL 内联）+ zhparser 三环修复（镜像 / 扩展与配置 / 触发器统一 zhparser / 存量重索引）+ `main()` 入口 + P0-3 中文召回验证（100 条 × 20 query） ✅ 已完成（P0-3 通过 100%） |
| Week 1 · day 3-5 | 学习管线数据完整性 + 审计：P1-1 落地（LLM 空/失败决策不删事件、`retry_count` 递增、failed 日志）+ `llm_raw_response` 写入 + decay 读 `space.config` + `_llm_analyze` 解析修复 + pytest 基建 ✅ 已完成（12 用例全绿） |
| Week 1 · day 6-7 | **v3 契约一次迁移**（source/dedup_key/api_key_hash/api_key_prefix/learning_logs.source + dedup 部分唯一索引）+ 删除 query_db（回到 3 工具）+ **冻结 observation 分析器契约** ✅ 已完成（契约已冻结进 §c；12 测试绿、P0-3 重跑 100%） |
| Week 2 | 身份层：API Key 中间件（flush 改为从 key 解析身份）+ load_memory 归属校验；观察入站口 `/api/v1/observations` + observation 分析器 + feeder 演示脚本 ✅ 已完成（23 测试绿 + 幂等/限长/隔离实测） |
| Week 3 | 集成文档（Dify / DSH REST / 业务 push 三份指南，含 `dify-workflow-template.yml` 更新为 v3.1 flush 形态）+ 端到端演示 + 审计链路验证 ✅ 已完成（e2e_demo.py 8 步全通过） |

### V1.5（候选主线，拆两片）

- **V1.5a（约 1.5-2 周，可独立演示）**：batch 全量管线 + `protected` 修订保护 + 双层产物（叙述层 md + 知识卡进服务）+ 体积治理 + `codebase_runs` run 级审计。演示目标：小型真实仓库自动出 wiki、人改不被覆盖。✅ **已完成**（2026-08：`codebase_runs` 迁移 + scanner/analyzer/store/projection + `/api/v1/codebase/*` 5 端点 + `ydm-distill` CLI（scan/run/sync）+ `WikiStore.search` source 过滤；35 测试绿、端到端 10 步全通过）
- **V1.5b（约 1-1.5 周）**：event 增量（文件指纹 diff + 模块聚合 + 单模块重生成 + md 投影 sync）。✅ **已完成**（2026-08：`core/codebase/incremental.py` 指纹 diff + 单模块重生成 + flush 侧 `source=codebase` 分派 + `ydm-distill refresh`；45 测试绿、端到端 8 步全通过。**顺带修复一处真实 bug**：flush 的分派原为 `source != "observation"`，`source=codebase` 事件会误入 chat 分支被学成 long_term_memory，现改为白名单 `in ("chat","example")`，并有回归测试锁定）
- **开工前 3 个设计点已闭环**（2026-08）：① md 是知识卡的单向投影、接受短期滞后（D11）；② `codebase_runs` run 级审计表（D12）；③ 独立 `/api/v1/codebase/*` 端点（D13）。另闭环模型与成本上限（D14）。
- 样例学习降级为下游 pattern 归纳，视资源实施。

### V2（视需求）

- pull producer 回融（在其他项目积累成熟后）
- 内置调度器（Space 级 cron）
- pgvector 语义检索（若 zhparser 召回不达标）
- 管理前端 2 页 + 学习日志页

---

## 实现现状与差距清单（v2 代码 → v3 的修复项）

现有 `yd-memory-service/backend`（约 1356 行）是 v2 方向的第一版实现，v3 在其上修复与扩展。已识别的差距（含 [07-architect-review.md](./07-architect-review.md) 的补充核验与新发现）：

| 级别 | 差距 | 位置 |
|------|------|------|
| 🔴 | 迁移 `edac4c...` 中 `agent_spaces.config` 默认值被写坏（`"decay_per_day"NULL.95`，`.` 被替换成 NULL），`alembic upgrade head` 无法正确执行 | `alembic/versions/edac4c069c8c_initial_all_5_tables.py:25` ✅ 已修复（day 1-2，根因：`sa.text` 把 `:0`/`:5000` 当绑定参数渲染成 NULL，改 `\:` 转义） |
| 🔴 | zhparser **三环断裂**：镜像无 zhparser（compose 用 `postgres:15-alpine`）、`init-db.sql` 从未 CREATE EXTENSION/CONFIGURATION（`_available_ts_configs` 永远查不到 zhparser）、触发器用 `to_tsvector('simple', ...)` 建向量与查询配置不匹配 | `init-db.sql`、`docker-compose.yml`、`docker/Dockerfile.pg-zhparser` ✅ 已修复（day 1-2：PG14 + scws 1.2.3 + zhparser 源码构建、codeload 下载、with_llvm=no） |
| 🔴 | 迁移先建 trigger、后靠 `init-db.sql` 建函数，标准流程（先 alembic 后 docker init）会失败 | 迁移文件 + `init-db.sql` 顺序 ✅ 已修复（day 1-2：函数 DDL 内联进迁移，init-db.sql 只保留扩展与配置） |
| 🔴 | **N1：P1-1 未落地**——LLM 空/失败决策时本批事件被无条件删除（静默丢素材），`retry_count` 从未使用 | `core/learning.py:106-110`；对照 `05-solutions.md` P1-1 ✅ 已修复（day 3-5：空/部分决策保留事件+retry_count 递增，≥3 次写 failed 日志再删，含单测） |
| 🔴 | **N2：启动入口缺失**——console script 指向不存在的 `main()`，`uv run yd-memory` 直接 AttributeError | `pyproject.toml:25`、`main.py` ✅ 已修复（day 1-2：main.py 补 `def main()`） |
| 🔴 | `load_memory` 无归属校验，任意 memory id 可跨 Space 读取 | `mcp/server.py:51-54` ✅ 已修复（Week 2：改用 `get_scoped(id, agent_id)` 归属过滤，跨 Space 读取返回不存在，含测试） |
| 🟡 | `learning_logs.llm_raw_response` 从未写入（审计不变式未兑现） | `core/learning.py` ✅ 已修复（day 3-5：llm 模式每次决策写入原始输出，含 failed 日志） |
| 🟡 | 权重衰减硬编码 0.95/0.1，未读 `agent_spaces.config` | `core/learning.py:113` ✅ 已修复（day 3-5：manager 读 space.config 传入 run_pipeline） |
| 🟡 | `_llm_analyze` 未剥离 ```json 代码块；失败回退走 `_direct_analyze` 而非 heuristic（与注释不符） | `core/learning.py:207,222` ✅ 已修复（day 3-5：剥离围栏、非数组/无效 JSON 返回空交给 P1-1 重试、不再静默回退） |
| 🟡 | **N5：中文重排退化**——`ts_rank` 计算后被丢弃；ranker 空格 `.split()` 分词使中文意图成为单 token，重叠分≈0，重排退化为纯权重序 | `pg_store.py:74-82`、`orchestrator/ranker.py:9-26` ✅ 已修复（day 3-5：search 携带 ts_rank、ranker 按 weight×0.4+ts_rank×0.6 合并，含单测） |
| 🟡 | **N6：`review_status` 从未参与召回过滤**（审核流装饰性，V1.5 pattern 上线必返工） | `pg_store.py:48-115` ✅ 已修复（2026-08：`review_conditions()` 分级过滤——flagged/deprecated 全类型排除、pattern 必须 approved、其他类型 pending 即可召回；`get_hot`/`search` 接入，去重比对显式关闭过滤；补 review 审核端点；9 用例锁定，含反向验证） |
| 🟡 | `query_db` 工具及其实现待删除（D3） | `mcp/tools.py:73-90`、`mcp/server.py:61-62,75-98` ✅ 已删除（day 6-7，回到 3 工具；asyncpg 双连接随之移除） |
| 🟡 | REST 无鉴权、无 agent_id 隔离；flush 由 body 传 `agent_id`（违反身份不变式） | `api/spaces.py`、`api/webhooks.py:13-20` ✅ 已修复（Week 2：`api/deps.py` require_agent 中间件 + 全路由 Bearer 鉴权 + flush 改 key 解析身份，webhooks.py 并入 learning.py 后删除） |
| 🟢 | 无 `backend/tests/`；`seed.py` 与 Alembic 双轨建表需统一 | — ✅ 测试已建（day 3-5：`tests/` 12 用例全绿，覆盖 P1-1/审计/衰减/解析/锁/ranker；pytest 需 session 级 loop 配置；seed.py 双轨仍未处理） |

---

## 核心设计决策

1. **为什么「共用收件箱」而不是每类学习建一套管线？** —— flush 的锁、审计、衰减、重试都是通用能力；新学习方式的差异只在「入箱」和「分析器」。共用收件箱让 4 类需求共享同一套可靠性。
2. **为什么观察学习 push-first？** —— push 事件自带 diff（「从 A 到 B」），最难的工程（连库、发现 schema、快照 diff）整块消失；信任成本最低（我不碰对方内网）；与现有 webhook 机制同构。pull 的唯一必要性是「对方只有库、没有事件出口」，留作未来 producer。
3. **为什么删除 query_db？** —— 它是 Dify 测试期的临时工具，本质是「LLM 裸 SQL 直查业务库」：越权读面（整库）、无隔离、无审计。业务数据访问的正确形态是观察学习（系统主动、持续、可审计），不是 Agent 现场查询。
4. **为什么身份用 per-space API Key？** —— 业务系统 push 和 DSH REST 接入本质是同一个问题（非 Dify 客户端怎么认身份），一把 key 解两个；MCP 的 `X-Agent-ID` 是 Dify 平台的协议约束，两者并存、解析到同一 `agent_id`。
5. **为什么 pattern 要强制溯源 + 人工审核？** —— 归纳是 LLM 幻觉高发区；「无引用即拒绝」+「pending 才能召回」把幻觉挡在召回层之外，同时保住演示时的审计说服力。
6. **为什么保持 Recall Orchestrator 为函数、LearningModel 为单次调用？** —— 同 v2：记忆服务保持简单，不引入 Agent 复杂度（见 [04-review-round3.md](./04-review-round3.md) 的 over-design 警告）。
7. **为什么 codebase 蒸馏走 batch/event 二分，且是 pull 纪律的合法例外？** —— 初次全量是「生成」而非「逐条学习决策」，走收件箱会污染它；增量 diff 是事件，天然进收件箱。codebase 读的是用户自己的本地代码，无凭证、无越权面，pull 的信任顾虑不成立——它是第一个在本项目实现的 pull 型 producer，正好验证 producer 扩展点的通用性。自动更新必须跳过 `metadata.protected` 条目（增量与修订保护捆绑发布，缺一不可，Qoder 教训）。
8. **为什么检索用「AND 优先、空结果降级 OR」而不是直接换 pgvector？** —— P0-3 实测：`plainto_tsquery` 的 AND 语义对自然语言查询命中率仅 25%（少一个词就全空），但同一数据 OR + ts_rank 达 100%、p95 5.4ms。zhparser 中文分词本身可用，瓶颈在查询语义；降级策略零新依赖，守住「PostgreSQL 唯一依赖」不变式。pgvector 保留为 V2 选项（语义检索的进一步提升空间）。

---

## 验证方案

- **P0-3 中文召回** ✅ 已通过（2026-08 实测）：100 条中文记忆 × 20 query——AND 语义命中率 25% → 「AND 优先、空结果降级 OR + ts_rank」后 Top3 命中 20/20 = 100%、p95 5.4ms（阈值 ≥40% / <100ms）。结论：保留 tsvector + zhparser，不切 pgvector；检索语义见核心设计决策 #8。
- **单元**：pytest 补齐——RecallOrchestrator（并行/去重/排序/降级）、flush 并发锁、observation 幂等（同 dedup_key 双投只产一条）、API Key 中间件、load_memory 归属校验
- **端到端**：docker-compose 起 PG → 建 Space 拿 key → feeder 灌 20 个发货事件 → cron 触发 flush → 验证知识产出 + 溯源可查 + learning_logs 完整（含 llm_raw_response）
- **多平台**：Dify 走 MCP 三工具；DSH 模拟走 REST 三接口，验证同一 Space 两侧读写一致

---

## 版本演进

### V1（约 3 周）
修复差距清单全部 🔴/🟡 + zhparser 验证 + 身份层 + 观察 push + 三份集成指南。

### V1.5（候选主线，拆两片）
V1.5a（约 1.5-2 周）：batch 全量 + `protected` 修订保护 + 双层产物 + `codebase_runs` 审计（可独立演示）；V1.5b（约 1-1.5 周）：event 增量。开工前的 md/知识卡一致性、batch 审计日志、codebase 入站端点三个设计点已闭环（D11-D13，另 D14 定成本上限）。样例学习降级为下游 pattern 归纳。

### V2
业务观察 pull producer 回融、内置调度器、pgvector、管理前端、样例学习剩余部分。

---

## 一句话总结

**一个以 PostgreSQL（tsvector + zhparser）为唯一依赖的多智能体外挂记忆平台：通过 MCP / REST / push 三种接入方式服务 Dify、DSH 及其他 Agent，用统一知识收件箱承载聊天喂养、样例归纳、业务观察四类学习需求，全部知识可溯源、可审计；V1.5 候选主线为代码库自动蒸馏（Qoder Repo Wiki 式认知层）。**
