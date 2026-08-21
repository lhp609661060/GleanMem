# yd-memory-service

面向多 Agent 的**外挂记忆与学习服务**。不绑定任何 Agent 框架，三种接入方式并存：Dify 走 MCP、其他 Agent 走 REST、业务系统走推送。

目标是把「Agent 的长期记忆」做成可复用的基础设施，而不是某个平台的内置功能。

---

## 为什么做这个

多数 Agent 框架把记忆做成内置能力，换框架就得重做。而记忆真正的难点不在存储，在于：

- **什么值得记** —— 全记会膨胀，检索质量被陈旧信息拖垮
- **记错了怎么办** —— LLM 归纳出的"知识"可能是幻觉
- **人的修正会不会被自动更新覆盖**

这个服务针对这三点做设计。

---

## 核心设计决策

### 召回编排器是函数，不是 Agent

召回路径**不引入 loop、不做工具调用、不让 LLM 参与决策**。三路检索（热记忆 / 冷检索 / Wiki）后按规则重排。

召回是高频路径，LLM 介入会让延迟和成本都不可预测。规则重排足以覆盖主要场景；LLM 重排留给后续版本。

### 只暴露三个 MCP 工具

`recall` / `load_memory` / `memorize`

早期设计有过 `query_db`（让 Agent 直接查库），后来**删除并写入设计不变量**：不再引入任何暴露 SQL 的工具。把 SQL 能力交给 LLM 是安全性与可控性的双重风险。

工具描述刻意写得克制，例如 `memorize` 明确告知调用方：

> 提交后不会立即生效，在对话结束后由学习模型统一分析处理。如需引用刚提交的信息，从对话上下文获取，不要依赖 recall。

给 LLM 讲清副作用边界，比事后修补它的误用更有效。

### PostgreSQL 是唯一运行时依赖

不引入 Redis、不引入独立向量库。待处理事件和学习日志都是 PG 表而非消息队列，中文检索用 `tsvector + zhparser`。

这东西要给别人接入，**多一个中间件就多一道部署门槛**。

### 学习延迟执行 + 全程可审计

- `memorize` 只写 `pending_events`，真正的学习在 Flush（对话结束的 webhook）时触发
- 跑在按 `agent_id` 加的 **PG advisory lock** 下防并发
- 三种学习模式：LLM 决策 / 启发式 / 直写
- 每条决策写 `learning_logs`，**含 LLM 原始响应**，出问题能追到是哪次推理写错了

### 防幻觉与人工保护

- **evidence 强制**：观察类归纳必须附来源事件引用，不允许凭空产生知识
- **失败不静默**：代码库蒸馏单模块失败即返回 None 并记入错误计数，绝不产出半成品知识卡
- **人工编辑保护**：自动更新跳过 `metadata.protected` 条目，不覆盖人的修正

---

## 架构

```
                  ┌──────────────┐
   Dify ──MCP────▶│              │
   其他 Agent ──REST──▶  本服务   │───▶ PostgreSQL
   业务系统 ──推送──▶│              │     (唯一依赖)
                  └──────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
     RecallOrchestrator      LearningModel
     (函数，规则重排)         (延迟执行 + 审计)
```

| 模块 | 职责 |
|------|------|
| `mcp/` | 3 个 MCP 工具 + SSE server |
| `orchestrator/` | 召回编排与规则重排 |
| `core/learning.py` | 学习模型（三种模式） |
| `core/long_term/` | 长期记忆存储（含权重衰减） |
| `core/wiki/` | Wiki 文档存储（Skill 式 description 匹配） |
| `core/codebase/` | 代码库蒸馏（批量 + 增量双轨） |
| `api/` | REST 接口 |
| `frontend/` | Vue 3 管理后台 |

---

## 快速开始

```bash
# 1. 起 PostgreSQL
docker compose up -d

# 2. 后端
cd backend
uv sync
cp .env.example .env        # 填入 DB 与 LLM 配置
uv run alembic upgrade head
uv run uvicorn yd_memory_service.main:app --reload

# 3. 前端（可选）
cd frontend && npm install && npm run dev
```

测试需要本地 PG 容器运行（用例打真实数据库）：

```bash
cd backend && uv run pytest
```

---

## 技术栈

Python 3.11+ / FastAPI / SQLAlchemy 2.0 async / PostgreSQL（tsvector + zhparser）/ MCP SSE / Vue 3 + Vite / Docker Compose

---

## 状态与边界

- 后端实现 V1 + V1.5a（批量代码库蒸馏）+ V1.5b（事件增量）
- 测试 59 个用例
- 前端 Vue 3 管理后台（记忆 / 知识卡 / 日志 / 召回调试 / 运行记录）
- **诚实说明**：这是个人项目，用于验证「Agent 外挂记忆」这套设计思路，未经大规模生产流量验证。设计过程做了三轮递进式评审（找 bug → 找未验证假设 → 质疑根本方向），并据此主动砍掉了部分过度设计的功能。

---

## License

MIT
