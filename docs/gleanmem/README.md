# gleanmem 设计文档

本目录记录 gleanmem（基于 yd-agent 记忆/学习模型的独立服务，集成到 Dify）从构思到方案的完整过程。

## 阅读顺序

| 文档 | 内容 | 用途 |
|------|------|------|
| [01-design.md](./01-design.md) | 完整设计方案（**v3**：多智能体外挂记忆平台） | **必读** — 最终方案 |
| [02-review-round1.md](./02-review-round1.md) | 一轮评审：代码级问题 | 参考 — 实现细节 |
| [03-review-round2.md](./03-review-round2.md) | 二轮评审：未验证假设 | 参考 — 风险清单 |
| [04-review-round3.md](./04-review-round3.md) | 三轮评审：根本方向质疑 | 参考 — 战略取舍 |
| [05-solutions.md](./05-solutions.md) | 问题解决与验证手册 | **动手前必读** — 逐个问题的解决方案 |
| [06-repo-wiki-research.md](./06-repo-wiki-research.md) | 需求反馈：Qoder Repo Wiki 调研与对照 | 调研细节 — 已纳入 01-design **v3.1** §代码库蒸馏（V1.5 候选主线） |
| [07-architect-review.md](./07-architect-review.md) | v3.1 架构评审报告（差距核验 + 新发现 + 开工顺序） | **动手前必读** — 开工依据 |

## 快速索引

### 我只想了解方案是什么
读 [01-design.md](./01-design.md)。

### 我准备动手做
1. 读 [01-design.md](./01-design.md) 了解方案
2. 读 [07-architect-review.md](./07-architect-review.md) 了解开工顺序（TOP 3 + 修正意见 A-E）
3. 读 [05-solutions.md](./05-solutions.md) 了解每个问题的解决和验证方法
4. 按 01-design §落地路径 从 **V1 Week 1 day 1-2**（建表链路 + 入口 + P0-3）开工

### 我想了解方案是怎么演进的
按 01 → 02 → 03 → 04 → 05 顺序读；06 为外部产品调研，已纳入 01-design v3.1（codebase 蒸馏，V1.5 候选主线）。

## 一句话总结

**一个以 PostgreSQL（tsvector + zhparser）为唯一依赖的多智能体外挂记忆平台：通过 MCP（Dify）/ REST（DSH 及其他 Agent）/ push（业务系统）三种方式接入，用统一知识收件箱（pending_events + source 维度）承载聊天喂养、样例归纳、业务观察、定时任务四类学习需求，所有知识可溯源、可审计。**

投入：**Spike 已完成，V1 3 周（Week 1：建表链路/P0-3 → 数据完整性/审计 → v3 契约迁移）+ V1.5 拆两片（V1.5a batch+双层 1.5-2 周 / V1.5b event 增量 1-1.5 周）**。

## 核心决策速览

- **定位**：多智能体外挂记忆（Dify 只是第一个集成方）
- **接入**：MCP SSE（X-Agent-ID，Dify）+ REST（per-space API Key，DSH/业务系统）
- **检索**：RecallOrchestrator 并行三路（热记忆权重 + 冷记忆 tsvector + wiki tsvector），纯函数非 Agent
- **工具**：3 个 MCP 工具（recall / load_memory / memorize），`query_db` 已删除
- **学习**：4 类需求共用知识收件箱，source（chat/example/observation）× trigger（webhook-flush/cron）正交
- **观察**：push-first，业务方推事件（幂等 + 溯源 + 审计）；pull 推迟到其他项目、以 producer 回融
- **代码库蒸馏**：V1.5 候选主线——batch 全量（不走收件箱）+ event 增量（走收件箱）+ `protected` 修订保护 + 双层产物（叙述层 md 给人读 + 知识卡进服务）
- **Wiki**：走 Skill 机制（description + 按需加载），V1 就做
- **依赖**：只依赖 PostgreSQL（不用 Redis）
- **前端**：V1 只做 2 页（Space 管理 + 记忆列表）
