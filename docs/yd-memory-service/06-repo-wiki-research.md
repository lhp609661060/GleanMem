# 06 · 需求反馈：Qoder Repo Wiki 调研与 yd-memory-service 对照

> **来源**：DSH 使用场景中的需求讨论（「类似 Qoder 的 wiki 记忆」「bug 不能重犯」「代码库认知」）。
> **性质**：需求反馈 / 外部产品调研。**已纳入 [01-design.md](./01-design.md) v3.1 §代码库蒸馏（正式候选方向，V1.5 候选主线）**——本文保留调研细节与对照表，设计决策以 01-design.md 为准。
> **结论先行**：yd-memory-service 的 wiki 定位（人工维护 + Skill 机制）与 Qoder Repo Wiki（代码库自动蒸馏）**不是同一件事**，两者互补；「代码库自动蒸馏」已确认纳入 v3.1 覆盖范围。

---

## 1. 背景

在 DSH（DeepSeek Harness）的使用中，用户提出三类记忆需求：

1. **用户记忆**：跨会话记住用户偏好；
2. **wiki 记忆**：类似 Qoder 的 Repo Wiki；
3. **工作区记忆**：踩过的 bug 不能重犯。

其中 1、3 已由 DSH 生态的 memory-evolve 类插件覆盖（五轨记忆 + 项目关键记忆）。本调研聚焦 2 —— Qoder Repo Wiki 的作用与实现方式，并对照 yd-memory-service 现有设计给出需求建议。

## 2. Qoder Repo Wiki 调研

### 2.1 定位：不是「文档库」，是「项目理解层」

Qoder 知识引擎把知识产物分为三层（官方博客《AI-Native 软件工程领域自迭代知识引擎》）：

| 层 | 服务对象 | 内容 | 组织方式 |
|----|---------|------|---------|
| Wiki | 人类 | 项目结构、模块职责、架构叙述 | 按人类阅读习惯，叙述性、可导航 |
| 知识卡（Knowledge Card） | Agent | 模块边界、调用关系、配置约定、变更注意事项 | 从 Wiki / 代码中提炼的紧凑事实 |
| 记忆（Memory） | Agent + 人 | 用户偏好、项目经验、历史决策 | 文件式（SOUL / USER / MEMORY.md） |

关键设计判断（官方原话）：*「若将 Wiki 直接作为 Agent 的任务上下文，Agent 仍需在执行过程中从长篇文档中重新提取模块边界、调用关系……知识卡片由此被引入」*——**Wiki 主要给人读，给 Agent 的是提炼后的知识卡**。这是 Repo Wiki 与普通文档库的本质区别。

### 2.2 实现方式

Repo Wiki 本质是一条「代码库 → 离线蒸馏 → 结构化 Markdown → 增量同步」流水线：

1. **初次生成**：LLM 批量分析项目结构与代码，产出按模块组织的 `.md`（产物在 `<项目根>/.lingma/repowiki/{zh,en}/content/`）。成本不低——4,000 文件的仓库约 **120 分钟**（参考值，来自官方文档）。
2. **增量更新**：监听代码变更（函数签名、类定义、API 端点），只重新生成受影响部分（单次变更控制在 10,000 行以内）；Git 中直接修改 Markdown 也会被检测并同步。
3. **人工修订保护**：团队改过的知识页被标记保护，**自动更新不覆盖人的判断**，反而反向同步回知识卡。
4. **前置引导**：`.qoder/repowiki/wiki_plan.yaml` 可注入生成意图（template：architecture / product_requirement；notes 引导提示；documents 页面白名单；scope include / exclude），随 Git 共享给团队。
5. **共享**：把 `.lingma/repowiki/` 提交进仓库，团队 pull 即得，零额外配置。
6. **消费端**：多路召回（结构索引 / 工具检索 / 执行中查询），按任务得分、结果稳定性、Token 消耗、Agent 轮次、工具调用数评估（官方数据：无知识基线 30.2 分 → 知识增强后显著提升）。

## 3. 与 yd-memory-service 现状对照

依据 01-design.md（v3）与 05-solutions.md：

| 能力维度 | Qoder Repo Wiki | yd-memory-service（v3） | 差距 |
|---------|----------------|------------------------|------|
| Wiki 内容来源 | **代码库自动蒸馏**（LLM 批量分析 + diff 增量） | 人工维护（`wiki_documents`）+ 业务观察蒸馏（`source=observation` → LLM 归纳业务规律） | **核心差距**：无「代码库 → 结构化文档」的自动生成能力 |
| Wiki 组织形态 | 双层：叙述性 Wiki（人读）+ 知识卡（Agent 读） | 单层：Skill 机制（description + 按需加载），面向 Agent 检索 | 部分对齐：Skill 机制 ≈ 知识卡思路，但缺「面向人的叙述层」 |
| 更新机制 | 增量更新 + 人工修订保护（人的修改不被覆盖） | 无自动更新；wiki 条目由人工 / 观察蒸馏写入 | 差距：无 diff 检测、无修订保护 |
| 团队共享 | 随 Git 提交共享 | 服务端按 `agent_id` 隔离，REST 读取 | 互补：yd 是服务形态（多平台接入），Repo Wiki 是仓库形态 |
| 接入范围 | IDE 内嵌 | 独立服务（MCP SSE / REST），多 Agent 平台 | **yd 的优势**：跨平台，DSH/Dify/业务系统均可接 |
| 检索 | 多路召回 + 评估 | RecallOrchestrator 三路（热记忆 / 冷记忆 tsvector / wiki tsvector）+ 规则重排 | 已有基础，可复用 |

**一句话结论**：yd-memory-service 的 wiki 是「**人工 + 观察喂养的业务词典**」（V1 已覆盖）；Repo Wiki 是「**代码库的自动蒸馏认知层**」。两者互补，后者是现有设计未覆盖的新维度。

## 4. 需求建议（已纳入 01-design.md v3.1 §代码库蒸馏，V1.5 实施）

以下要点与 01-design.md v3.1 保持一致，沿 v3 的「收件箱 + source × trigger」不变式扩展，不新起炉灶：

1. **新增 source：`codebase`（代码库蒸馏）**
   - 素材进收件箱：代码库扫描产物（模块清单、文件哈希 / AST 指纹）。
   - 分析器：`CodebaseAnalyzer`（LLM 批量生成模块级结构化文档）。
   - 产物：仍落 `wiki_documents`（沿用 Skill 机制），或新增 `wiki_codebase` 分表区分来源。
   - 溯源：`metadata` 固化文件路径、提交 SHA、指纹，保持「每条知识可 trace 回代码」。

2. **增量与修订保护**
   - 初次全量生成后，按文件指纹 diff 触发局部重生成（对齐 Repo Wiki 的「只更新受影响部分」）。
   - 人工修订标记（`metadata.protected` 或独立标志），自动更新跳过受保护条目。

3. **Wiki 双层化（可选，V1.5+）**
   - 现状 Skill 机制已覆盖「Agent 消费层」；如需「人类阅读层」，可让 CodebaseAnalyzer 同时产出叙述性概览（模块职责 / 架构），与检索卡片分离存储。

4. **触发方式**
   - `cron`（定时全量/增量）或 `webhook-flush`（CI 提交后回调 push），均不新增 trigger 类型。

5. **不做的事（对齐 Repo Wiki 教训）**
   - 不在 V1 范围内做前端知识编辑页；不把 wiki 产物塞进每次对话上下文（维持 Skill 按需加载）。

## 5. 优先级建议

| 优先级 | 方向 | 理由 |
|-------|------|------|
| P1（若做） | `source=codebase` 代码库蒸馏 + 溯源 | 复用收件箱管线，增量成本可控，填补「自动生成」空白 |
| P2 | 增量更新 + 人工修订保护 | 无保护则自动生成会覆盖人的修订，是自动化的前置条件 |
| P3 | Wiki 双层（叙述层 + 卡片层） | 与 DSH/Claude 类平台的「docs 即知识」诉求相关，但非阻塞 |

## 6. 附：参考资料

- [Repo Wiki 用户指南（阿里云）](https://www.alibabacloud.com/help/zh/lingma/qoder-cn/user-guide/repo-wiki)
- [Qoder 博客：Repo Wiki —— 让隐性知识自动浮现](https://qoder.com/zh/blog/repo-wiki-surfacing-implicit-knowledge)
- [Qoder 博客：AI-Native 软件工程领域自迭代知识引擎](https://qoder.com/zh/blog/qoder-knowledge-engine)
