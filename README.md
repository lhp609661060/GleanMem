# gleanmem

面向多 Agent 的**外挂记忆与学习服务**。不绑定任何 Agent 框架，四种接入方式并存：

| 接入方 | 方式 |
|--------|------|
| Dify | MCP（SSE，`recall` / `load_memory` / `memorize`） |
| DSH | REST 直连 或 MCP stdio，附**一键接入脚本** |
| 其他 Agent | REST（per-space API Key） |
| 业务系统 | 推送（观察事件，幂等入收件箱） |

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

召回路径**不引入 loop、不做工具调用、不让 LLM 参与决策**。三路检索（热记忆 / 冷检索 / Wiki）后按规则重排（`weight × 0.4 + ts_rank × 0.6`）。中文检索用 `tsvector + zhparser`，查询语义「AND 优先、空结果降级 OR」，实测自然语言召回 20/20 = 100%、p95 5.4ms。

### 只暴露三个 MCP 工具

`recall` / `load_memory` / `memorize`

早期设计有过 `query_db`（让 Agent 直接查库），后来**删除并写入设计不变量**：不再引入任何暴露 SQL 的工具。把 SQL 能力交给 LLM 是安全性与可控性的双重风险。

工具描述刻意写得克制，例如 `memorize` 明确告知调用方：

> 提交后不会立即生效，在对话结束后由学习模型统一分析处理。如需引用刚提交的信息，从对话上下文获取，不要依赖 recall。

### PostgreSQL 是唯一运行时依赖

不引入 Redis、不引入独立向量库。待处理事件和学习日志都是 PG 表而非消息队列，中文检索用 `tsvector + zhparser`。多一个中间件就多一道部署门槛。

### 学习延迟执行 + 全程可审计

- `memorize` 只写 `pending_events`，真正的学习在 Flush（对话结束的 webhook）时触发
- 跑在按 `agent_id` 加的 **PG advisory lock** 下防并发
- 三种学习模式：LLM 决策 / 启发式 / 直写
- 每条决策写 `learning_logs`，**含 LLM 原始响应**，出问题能追到是哪次推理写错了
- LLM 空/失败决策不静默删事件：保留 + `retry_count` 递增，超阈值才写 failed 日志

### 防幻觉与人工保护

- **evidence 强制**：观察类归纳必须附来源事件引用，不允许凭空产生知识
- **失败不静默**：代码库蒸馏单模块失败即返回 None 并记入错误计数，绝不产出半成品知识卡
- **人工编辑保护**：自动更新跳过 `metadata.protected` 条目，不覆盖人的修正
- **审核过滤**：`pattern` 类归纳必须 `approved` 才进召回，`flagged`/`deprecated` 全类型排除

---

## 架构

```
                    ┌─────────────────┐
   Dify ───MCP─────▶│                 │
   DSH  ──REST/MCP─▶│   gleanmem-    │────▶ PostgreSQL（唯一运行时依赖）
   其他 Agent ─REST─▶│   service       │         ├─ long_term_memories
   业务系统 ─推送───▶│                 │         ├─ wiki_documents
                    └─────────────────┘         ├─ pending_events（统一收件箱）
                          │                      └─ learning_logs（审计）
              ┌───────────┴────────────┐
              ▼                        ▼
     RecallOrchestrator         LearningModel
     （函数，规则重排）          （延迟执行 + 审计）
              │                        ▲
              ▼                        │
     平台管理台（admin key）   V1.5 代码库蒸馏（批量 + 增量）
     空间管理 / 文档 / 审核
```

| 模块 | 职责 |
|------|------|
| `backend/src/.../mcp/` | 3 个 MCP 工具 + SSE server + stdio 入口 |
| `backend/src/.../orchestrator/` | 召回编排与规则重排 |
| `backend/src/.../core/learning.py` | 学习模型（三种模式） |
| `backend/src/.../core/long_term/` | 长期记忆存储（含权重衰减） |
| `backend/src/.../core/wiki/` | Wiki 文档存储（Skill 式 description 匹配） |
| `backend/src/.../core/codebase/` | 代码库蒸馏（批量 + 增量双轨） |
| `backend/src/.../api/` | REST 接口（含 per-space API Key 鉴权） |
| `frontend/` | Vue 3 管理后台（平台管理台 + 空间业务视图） |
| `dsh/` | DSH 一键接入脚本 + skill |

---

## 目录结构

```
yd-agent/
├── docs/gleanmem/     # 设计文档（01-design 为权威，v3.4）+ 评审记录
├── gleanmem/
│   ├── backend/                # FastAPI 后端（src layout）
│   │   ├── src/gleanmem/
│   │   ├── tests/              # 69 个 pytest 用例（打真实 PG）
│   │   └── alembic/            # 数据库迁移
│   ├── frontend/               # Vue 3 + Vite 管理后台
│   ├── dsh/                    # DSH 一键接入（install.py + skill）
│   └── docker-compose.yml      # 本地 PostgreSQL（含 zhparser 镜像）
└── spike/                      # Dify/MCP 验证脚本（非 V1 代码）
```

---

## 快速开始

```bash
# 1. 起 PostgreSQL（含 zhparser 扩展的 PG14 镜像）
docker compose -f gleanmem/docker-compose.yml up -d

# 2. 后端
cd gleanmem/backend
uv sync
cp .env.example .env            # 填数据库与 LLM 配置（可选）
uv run alembic upgrade head
uv run uvicorn gleanmem.main:app --host 127.0.0.1 --port 8000

# 3. 前端（可选）
cd gleanmem/frontend && npm install && npm run dev
```

测试需要本地 PG 容器运行（用例打真实数据库）：

```bash
cd gleanmem/backend && uv run pytest
```

---

## 技术栈

Python 3.12+ / FastAPI / SQLAlchemy 2.0 async / PostgreSQL（`tsvector + zhparser`）/ MCP SSE + stdio / Vue 3 + Vite / Docker Compose

---

## 状态与边界

- 后端实现 **V1 + V1.5a**（批量代码库蒸馏）+ **V1.5b**（事件增量）+ N6 审核过滤，设计文档见 `docs/gleanmem/01-design.md`
- 平台管理台：**admin key** 登录管理所有空间（创建 / 编辑 / 归档 / 轮换 key），空间业务视图按 space key 隔离
- 前端 7 页：空间管理 / 文档（Dify + DSH 接入指南）/ 记忆与审核 / 学习日志 / 代码库知识卡 / 蒸馏审计 / 检索预览
- 测试 69 个用例全绿
- **诚实说明**：这是个人项目，用于验证「Agent 外挂记忆」这套设计思路，未经大规模生产流量验证。设计过程做了三轮递进式评审（找 bug → 找未验证假设 → 质疑根本方向），并据此主动砍掉了部分过度设计的功能。

---

## License

MIT
