# gleanmem 管理端

Vue 3 + Vite 的管理前端，5 个页面覆盖后端全部能力。**只读 + 审核**，不做数据录入（录入走 REST / MCP / CLI）。

## 起服务

```bash
# 1. 后端（另一个终端，需本地 PG 起着）
cd ../backend && uv run gleanmem

# 2. 前端
npm install
npm run dev          # http://localhost:5173
```

Vite 把 `/api` 代理到 `http://localhost:8000`（见 `vite.config.js`），因此无需处理 CORS。

## 登录

需要一个 Space 的 `space_key`。没有就造一个带演示数据的：

```bash
cd ../backend && uv run python scripts/seed_demo_space.py
```

它会打印 key 和建议的演示动线，末尾附清理命令。

**key 的处置**：只存 `sessionStorage`（关标签页即失效），不写 `localStorage`、不进 URL、不打日志。侧栏只显示前 8 位用于辨认。服务端本身也只存 key 的 SHA-256（`api_key_hash` 永不出现在任何响应里）。

## 页面

| 页面 | 展示什么 | 对应设计 |
|------|---------|---------|
| 记忆与审核 | 记忆列表 + `review_status` + **是否可被召回** + 通过/标记按钮 | N6 审核纪律 |
| 学习日志 | 每条学习决策的来源、动作、分析器模式，展开可看 `llm_raw_response` | 审计不变式 |
| 代码库知识卡 | 蒸馏产出的知识卡、`protected` 修订保护、md 待 sync 状态 | V1.5 双层产物、D11 |
| 蒸馏审计 | `codebase_runs` 的 run 级审计（有效/排除文件数、token、保护跳过次数） | D12 |
| 检索预览 | 与 MCP `recall` 等价的三路召回结果 | 检索侧 |

## 最值得看的一幕（N6 闭环）

1. 「记忆与审核」页找到 `pattern` 类型那条 —— 状态 `pending`，**可召回 = 否**
2. 「检索预览」搜「发货单状态怎么流转」—— 结果里**没有**它
3. 回到记忆页点「通过」
4. 再检索 —— 它出现了

这演示的是防幻觉纪律：LLM 归纳出的规则，人不点头就进不了 Agent 的上下文。

## 构建

```bash
npm run build        # → dist/
```

产物是纯静态文件（约 38KB gzip），可交给任意静态服务器；生产环境需自行把 `/api` 反代到后端。
