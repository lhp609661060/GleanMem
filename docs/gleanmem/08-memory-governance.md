# 记忆治理层研究备忘：行业调研与 V3 备选设计（v1.1 · 已冻结）

> **状态：研究备忘，不进入实施（2026-09-24 经 [09-overdesign-audit.md](./09-overdesign-audit.md) 审计冻结）。**
>
> 本文的能力对照、事实核实与借鉴清单（§1–§3）持续有效；§4 起的"V3 治理层"设计**降级为备选方案**——在 §6 触发条件（真实外部用户、真实召回流量、真实改坏事故）出现前不立项、不写代码。它记录的是"行业企业版在做什么"，不是"拾忆现在该做什么"。
>
> ---
>
> **定位**：本文是 2026-09「Agent 记忆模型与开源模块」行业调研的落点。调研本身的对话记录（项目热度、论文方向、7 项治理要求的定性）经事实核实后，转化为三件事：
> 1. 拾忆与主流记忆模块的**能力对照**（我们处在哪）；
> 2. **借鉴清单**：哪些能力值得自建、哪些引用既有模块、哪些明确不做；
> 3. **V3 记忆治理层的备选设计**（Memory Governance Layer）：自动 QA、命中率埋点、版本快照与回滚——**仅供触发条件满足后取用**。
>
> 与既有文档的关系：[01-design.md](./01-design.md) 是权威设计；凡冲突以 01-design 为准。核心不变式（PostgreSQL 唯一依赖、Recall 是函数不是 Agent、身份从 key 解析、学习延迟可审计）在本文继续成立——即便未来取用，治理层也是压在既有库之上的**薄薄一层**，不引入新运行时依赖。

---

## 1. 调研核实结论（2026-09-24）

调研记录中的关键事实声明经联网核实，绝大部分成立，但有若干数字过期或归属偏差，凡引用入文/入设计的以本节为准。

### 1.1 热度数据（GitHub API 实测，2026-09-24）

| 项目 | 调研记录 | 实测 star | 结论 |
|------|---------|----------|------|
| Mem0（mem0ai/mem0） | 约 40k | **65.9k** | 属实且已过期，Mem0 仍是记忆类第一 |
| Graphiti（getzep/graphiti） | 万星级 | **31.1k** | 属实 |
| Cognee（topoteretes/cognee） | 高 | **30.9k** | 属实 |
| GBrain（garrytan/gbrain） | 发布数天 7.1k | **30.3k** | 项目属实；"数天 7.1k"是发布时点快照，当前已 30k+ |
| Letta（letta-ai/letta） | 高 | **24.9k** | 属实 |
| MemOS（MemTensor/MemOS） | 10k+ | **11.6k** | 属实 |
| Honcho（plastic-labs/honcho） | 新锐 | **7.3k** | 属实 |
| Memary（kingjulio8238/Memary） | 中高 | **2.6k** | star 属实，但**最近推送停在 2024-10，实质已停更**，引用价值低 |
| LangMem（langchain-ai/langmem） | 随 LangGraph | **1.7k** | 属实 |

### 1.2 融资与论文

| 声明 | 核实结果 |
|------|---------|
| Mem0 $24M A 轮，Basis Set 领投（2025-10） | ✅ 属实。[TechCrunch 2025-10-28](https://techcrunch.com/2025/10/28/mem0-raises-24m-from-yc-peak-xv-and-basis-set-to-build-the-memory-layer-for-ai-apps/)：$24M，Basis Set 领投，YC / Peak XV 跟投 |
| MemOS 华为哈勃 / 荣耀战投 Pre-A 亿元级 | ✅ 属实（[新浪财经 2026-07-21](https://finance.sina.com.cn/jjxw/2026-07-21/doc-iniiqefa9232596.shtml)） |
| A-Mem，NeurIPS 2025 | ✅ 属实，[NeurIPS 2025 poster 119020](https://neurips.cc/virtual/2025/loc/san-diego/poster/119020) |
| Memory-R1，RL 训练记忆管理 | ✅ 项目属实，但归属不是调研所暗示的独立 arXiv 方向——正式发表为 **ACL 2026**（[aclanthology 2026.acl-long.583](https://aclanthology.org/2026.acl-long.583/)），arXiv 2508.19828 |
| PlugMem，ICML 2026 | ✅ 属实，[ICML 2026 poster 64446](https://icml.cc/virtual/2026/poster/64446) |
| LycheeMemory（压缩记忆 + 端到端 RL），ACL 2026 | ✅ 论文属实，正式标题为 *Dynamic Long Context Reasoning over Compressed Memory via End-to-End Reinforcement Learning*（[aclanthology 2026.acl-long.365](https://aclanthology.org/2026.acl-long.365/)）。"LycheeMemory"是别名，引用时用正式标题 |
| Hermes Agent smart write approval judge（PR #65446） | ✅ PR 存在（GitHub 搜索结果可索引，web_fetch 网络失败未能读全文）。**设计上不应把单一 PR 当作"已稳定发布的产品能力"引用，按"该项目存在 memory write gate 方向"表述** |
| Hermes Agent explicit_only（PR #68848） | ✅ PR 存在 |

### 1.3 一句话定性

调研的判断框架（记忆层成为独立基础设施赛道；7 项治理要求里 2/3 是成熟工程、1/3 是智能判断；缺打包的主因是分层错位 + 赛道年轻）**经核实仍然成立**，可以作为 V3 设计与对外文章的论据骨架；需要修正的只是热度数字与论文归属。

---

## 2. 能力对照：拾忆 vs 主流记忆模块

### 2.1 七项治理能力矩阵

评级口径：✅ 原生有 / ⚠️ 有半成品或需自建 / ❌ 无。"拾忆"列按 2026-09 代码现状（v3.5，132 测试绿）。

| 能力 | Mem0 | Graphiti | Letta | GBrain | Hermes Agent | **拾忆现状** |
|------|------|----------|-------|--------|--------------|-------------|
| ① 人类可读 | ✅ 单句事实 | ✅ 图谱事实 | ✅ persona/human 块 | ✅✅ Markdown | ✅ | ✅ title/content 自然语言 + 前端 |
| ② 审批闸门 | ⚠️ Webhook 自建 | ❌ | ✅ require_approval | ❌ | ✅ explicit_only / write gate | ✅✅ review_status 四级 + 审核端点 + 前端 N6 |
| ③ 自动 QA → 评审 | ⚠️ 外接 Judge | ⚠️ 自动处理矛盾不送审 | ⚠️ 外接 | ⚠️ | ✅ 辅助 LLM 判风险 | ❌ **缺**（V3.1 拟补，见 §4） |
| ④ 来源 / 版本 | ✅ metadata | ✅✅ 双时序 append-only | ✅ 消息级 | ✅ git | ✅ | ✅ 来源完整；⚠️ **版本快照缺**（V3.2 拟补） |
| ⑤ 命中率统计 | ⚠️ 接 Langfuse | ❌ | ❌ | ❌（日志自建） | ❌ | ❌ **缺**（V3.1 拟补埋点，见 §5） |
| ⑥ 回滚 | ❌ 原地改 | ✅✅ 时间旅行 | ⚠️ 块替换不留旧版 | ✅ git revert | ⚠️ 弱 | ⚠️ 软删可恢复；**条目/时间点版本回滚缺**（V3.2） |
| ⑦ 精确删除 | ⚠️ | ✅ 失效不物理删 | ⚠️ | ✅ | ⚠️ | ✅ 软删 + deprecated；物理删除 V3 视合规需求 |

### 2.2 关键洞察

1. **拾忆在②审批上已超过 Mem0/Graphiti，与 Letta/Hermes 同档。** review_status（pending/approved/flagged/deprecated）+ 召回层分级过滤 + 审核端点 + 前端动作，是完整闭环；多数记忆模块把审批留给 webhook 或根本没有。
2. **真正的差距集中在三块：自动 QA（③）、命中率（⑤）、版本回滚（⑥）。** 与调研"没人打包"的判断一致——这正是 V3 的内容。
3. **不缺的不要补**：图谱时序（Graphiti 路线）、OS 式分页（Letta）、记忆运行时（MemOS）都属于**底座级不同范式**，拾忆不追随。拾忆的底座选择（扁平 memory + wiki + 单 PG）已被 P0-3（Top3 100%、p95 20.8ms）和 132 测试验证，换底座是负收益。

---

## 3. 借鉴清单：自建、引用、不做

原则：**只借鉴"薄薄一层"的治理能力，不借鉴底座范式。** 每项给出决策与理由。

### 3.1 自建（V3 主体）

| 能力 | 借鉴对象 | 拾忆做法 |
|------|---------|---------|
| 自动 QA Judge | Hermes 的 smart judge「辅助 LLM 判风险、低风险放行」思路；Mem0 的 guardrails 挂载点 | flush 分析后、commit 前增加一道 QA 评分（复用既有 `YDM_LLM_*` 便宜模型），命中风险规则的记忆落 `review_status=pending`（pattern 已有此纪律，泛化到全类型 + 分数落 metadata） |
| 命中率埋点 | Mem0 + Langfuse 的采纳率埋点思路 | 不引 Langfuse。recall 时写轻量 `recall_events` 表（query / 返回 id / rank），引用归因靠显式反馈与 LLM-judge 离线跑，覆盖采纳率与 Recall@k 两档 |
| 版本快照 / 回滚 | Graphiti 双时序的"旧事实不覆盖只失效"；GBrain 的 git revert | 不做双时序引擎。新增 `memory_revisions` 表，更新/删除前插快照（SCD2 的极简版），支持条目级恢复；时间点回滚用快照表批量还原 |
| 风险分级 | 调研 §5 的"风险分级而非全量拦截" | QA 分两档放行：高风险（PII、与已批准记忆矛盾、无 evidence）必拦；低价值/低不确定度仅打分不拦 |

### 3.2 引用既有模块（克制，仅限无依赖或可直接吸收思想的）

| 模块 | 引用方式 | 不直接依赖的理由 |
|------|---------|----------------|
| **A-Mem 的结构化笔记思想**（NeurIPS 2025） | 吸收其"记忆生成时带结构化元数据 + 自动建立关联链接"的做法，在 memory metadata 增加 `links`（关联记忆 id），启发式去重/召回扩召时使用 | A-Mem 是论文原型，引入它的代码会带来新存储与运行时；思想可以用几十行代码落地 |
| **Mem0 的事实增删改 prompt 模式** | 参考其"抽取原子事实 + ADD/UPDATE/DELETE 决策"的 prompt 结构，增强现有 `_llm_analyze`（当前只输出 store/discard/merge，无显式 UPDATE/矛盾检测） | Mem0 是全家桶底座，与其平行，不嵌入 |
| **Langfuse 的归因口径（仅文档级）** | 在设计中写清"采纳率 = 注入记忆被回答引用的比例"的埋点口径与 Langfuse 对接点，未来用户可自行外接 | 坚持 PG 唯一依赖不变式 |

### 3.3 明确不做（防止过度设计，呼应 04 评审）

| 方向 | 不做的理由 |
|------|-----------|
| Graphiti 式时序知识图谱 / Graphiti 依赖 | 底座范式不同；引入即破坏 PG-only，且拾忆没有"事实高频矛盾演化"的目标场景。版本快照（§3.1）已以 1/10 的成本拿到回滚收益 |
| Letta 式内存分页 / Sleep-time Compute | 拾忆记忆条数上限（max_memories 默认 5000）远不需要分页；sleep-time 与现有 cron flush 重叠 |
| MemOS 式记忆运行时（L1-L3） | 平台级产品，不是组件；与拾忆"薄记忆服务"定位相反 |
| KV-cache 压缩记忆（LycheeMemory 路线） | 属模型推理层改造，不是外挂记忆层的能力边界 |
| Memory-R1 / PlugMem 式 RL 训练 | 研究侧能力，需要训练管线与语料；拾忆是工程产品。跟踪论文，不落地 |
| Memary 相关 | 项目 2024-10 起停更，不引用 |
| 物理删除 + GDPR 审计留痕的完整合规包 | 无受监管真实用户（项目真实驱动是简历/演示，见 04 评审 §1.1）；保留软删，V3 只留接口位，不建全流程 |

---

## 4. V3.1 设计：自动 QA Gate

### 4.1 在管线中的位置

```
pending_events
      ↓ 分析器（chat / observation / codebase，现状不变）
MemoryDecision（候选记忆 + evidence）
      ↓
QA Gate（新增）：规则前置 → LLM Judge 兜底
      ↓                          ↓
高风险：review_status=pending    干净：按原纪律落库（非 pattern 可直接召回）
metadata.qa = {score, issues[], model, judged_at}
      ↓
commit + learning_logs（QA 结果一并入日志，可回放）
```

QA Gate 是 LearningModel 内的**一个纯函数步骤**，不新增外部服务、不新增 Agent，不改变"Recall 是函数"不变式。

### 4.2 两档检测（风险分级，不做全量拦截）

**规则前置（零 token，必做）：**

| 规则 | 判定 | 处置 |
|------|------|------|
| evidence 为空（归纳类 store） | 高风险 | 置 pending（现状防幻觉纪律泛化） |
| PII 正则（手机号/身份证/银行卡/邮箱，中文场景） | 高风险 | 置 pending，issue=pii |
| 与同 Space 已 approved 记忆标题同义、content 结论矛盾 | 高风险 | 置 pending，issue=contradiction |
| 标题/正文过短或纯寒暄 | 低价值 | 仅打分，不拦 |

**LLM Judge 兜底（规则未命中时，复用便宜模型，单次批量）：**

- system prompt 要点：对每条候选记忆输出 `{id, risk: high|low, issues: [pii|duplicate|contradiction|low_value|doubtful_fact]}`；**只判风险不重写内容**；high 门槛宁高勿低（减少误报，避免审批队列淹没）。
- 无 LLM key / 调用失败：**fail-open**——记 `qa=skipped`，按无 QA 放行（与 P1-1 的"故障不丢素材"一致；QA 是增强不是生死闸）。唯一例外：规则前置命中的高风险仍然拦截，不依赖 LLM。
- 原始返回落 `learning_logs.llm_raw_response`（复用现有审计列，不新造审计表）。

### 4.3 配置（Space 级，config JSONB）

```json
{
  "qa_enabled": true,
  "qa_mode": "rules+llm",          // rules | rules+llm | off
  "qa_block_on": ["pii", "contradiction", "no_evidence"],
  "qa_llm_judge": true
}
```

默认值面向"演示 + 单用户"：`qa_enabled=true`、`qa_mode=rules+llm`，但 `qa_block_on` 只含三类高风险——低价值/事实存疑只打分不拦，呼应调研"个人场景全量上会过度"。

---

## 5. V3.1 设计：命中率埋点（先做够用的两档）

不建评测平台，只埋"将来能算"的数据。两张轻量事实（可用同一张表 + 类型区分）：

### 5.1 recall_events（召回事实）

```sql
CREATE TABLE recall_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agent_spaces(agent_id),
    session_id VARCHAR,
    query TEXT NOT NULL,
    results JSONB NOT NULL,        -- [{id, type, rank}]
    latency_ms INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_recallev_agent ON recall_events(agent_id, created_at DESC);
```

写入点：`orchestrator/recall.py` 返回前（同步 INSERT，失败只告警不影响召回）。

### 5.2 三档命中率的落地取舍

| 档位 | 含义 | V3 是否做 | 怎么算 |
|------|------|----------|--------|
| **采纳率（Usage）** | 召回的记忆被回答实际引用的比例 | ✅ 做 | ① Agent/前端可显式回传 `used_ids`（新增可选 `POST /api/v1/feedback/usage`）；② 离线 LLM-judge 拿答案与召回结果跑归因。先上①的最小端点 + 前端可标 |
| **Recall@k** | 该召回的有没有召回到 | ⚠️ 半做 | 需标注集；复用 `scripts/p0_3_zhparser.py` 的评估形态，对已积累的真实 query 沉淀小标注集（20-50 条），出一次性报告，不做在线面板 |
| **贡献度（AB）** | 有记忆 vs 无记忆的质量差 | ❌ 暂不做 | 需对照实验与评测打分，属评测平台；在文档中保留方法，不建代码 |

### 5.3 汇总端点（只读，够用即可）

```
GET /api/v1/metrics/recall?days=7
→ { recall_count, avg_latency_ms, result_count_distribution,
    adoption_rate /* 有 used_ids 回传的比例 */, top_miss_queries }
```

`top_miss_queries` = 召回结果为空的 query 频次 Top10——这是对演示最有说服力、且零标注成本的数字。

---

## 6. V3.2 设计：版本快照与回滚

### 6.1 memory_revisions（极简 SCD2，不做双时序引擎）

```sql
CREATE TABLE memory_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id UUID NOT NULL REFERENCES long_term_memories(id),
    agent_id UUID NOT NULL REFERENCES agent_spaces(agent_id),
    revision_no INT NOT NULL,
    title VARCHAR NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    review_status VARCHAR NOT NULL,
    change_reason VARCHAR,        // merge | qa_review | manual_edit | delete
    changed_by VARCHAR,          // system:<mode> | api_key:<prefix>
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (memory_id, revision_no)
);
CREATE INDEX idx_memrev_agent ON memory_revisions(agent_id, created_at DESC);
```

### 6.2 触发点与回滚档位

- **写快照**：LongTermStore 的 merge 更新、review 端点改状态、DELETE（软删）、未来的人工编辑端点——执行前插一条 revision。新增记忆时 revision_no=1 随当前内容建首版。
- **条目级回滚**：`POST /api/v1/memories/{id}/rollback?to_revision=N` → 先把当前状态存为新 revision，再还原目标版本内容；全程留痕，回滚本身也可再回滚。
- **时间点回滚**：离线脚本（不做在线端点）按 `memory_revisions` 把 Space 内各记忆还原到 ≤ 指定时刻的最后版本。与 Graphiti 时间旅行的差别：**我们靠快照表批量还原，不维护双时序查询**——十分之一的机制，覆盖演示与"改错了能退回"的真实需求。

### 6.3 边界

- 快照只针对 long_term_memories；wiki 有 `protected` 与 codebase run 审计，V3.2 先不做 wiki 版本（可重建投影，价值低）。
- 不做自动过期/事实失效（Graphiti 的核心卖点）；拾忆用 decay + deprecated 表达，不重复建设。

---

## 7. 切片与落地顺序

遵循 01-design 的可演示纪律：每片独立可演示、有测试、不破坏 132 测试基线。

| 切片 | 内容 | 演示卖点 | 前置 |
|------|------|---------|------|
| **V3.1a 命中率埋点** | recall_events 写入 + `GET /metrics/recall` + usage 回传最小端点 | 「命中率第一次看得见」：空结果 query Top10、采纳率 | 无，纯增量 |
| **V3.1b QA Gate（规则）** | evidence/PII/矛盾规则前置 + qa metadata + 日志 | PII 自动进待审队列；无 evidence 不召回 | 无 |
| **V3.1c QA Gate（LLM Judge）** | 批量风险判定 + fail-open + Space 配置 | 「干净记忆自动放行、风险记忆排队等审批」 | V3.1b |
| **V3.2 版本快照与回滚** | memory_revisions + rollback 端点 + 离线时间点脚本 | 「改错了一键退回，每次变更都能翻旧账」 | 建议在 V3.1 后，但无硬依赖 |

排序理由：埋点最便宜且立刻为文章/简历提供"命中率"叙事；QA 规则先于 LLM（零 token、确定性、好测）；回滚机制最独立，放最后。每片完成后更新 01-design §实现现状与差距清单，按惯例补 v3.x 修订记录。

### 与现有 V2 backlog 的关系

V2 剩余项（pull producer、样例 pattern 归纳）保持原优先级，**V3 治理层不阻塞它们、也不被它们阻塞**。若资源有限，建议先 V3.1a/V3.1b——它们同时服务"完善系统"与"对外论述必要性"两个目标，单位投入产出最高。

---

## 8. 对设计不变式的影响（自检）

| 不变式 | V3 是否保持 |
|--------|------------|
| PostgreSQL 唯一运行时依赖 | ✅ 新能力全部是 PG 表 + 既有 LLM 配置，无 Redis / 向量库 / 评测 SaaS 依赖 |
| Recall 是函数不是 Agent | ✅ QA Gate 是 flush 内函数步骤；recall 路径不增加任何 LLM 判定（LLM Judge 只在写侧 flush） |
| 身份从 key/接入层解析 | ✅ 所有新端点沿用 `require_agent`，agent_id 不进参数 |
| 学习延迟 + 可审计 | ✅ QA 判定、usage、版本回滚全部写日志/快照，`llm_raw_response` 纪律延续 |
| 薄、可演示、不过度设计（04 评审） | ✅ 风险分两档、命中率只做两档、回滚不做双时序；明确列出不做清单（§3.3） |

---

## 9. 一句话总结

**拾忆在"可审批"上已经站在第一梯队，V3 要补的不是更强的记忆底座，而是压在底座上的治理薄壳：规则 + LLM 两级 QA Gate、够用即止的命中率埋点、基于快照表的版本回滚——思想借鉴 A-Mem / Mem0 / Hermes / Graphiti，代码不依赖它们中的任何一个。**
