---
name: feedback-collector
description: >
  Use when collecting reader feedback (comments, likes, views) from published
  articles on 公众号 / 知乎 / 掘金, aggregating them into content/feedback/ for
  system optimization and article rewrites.
---

# 收集反馈（feedback-collector）

收集已发布文章在各平台的反馈（评论 / 点赞 / 阅读 / 收藏），汇总到 `content/feedback/`，提炼可执行结论，反哺系统优化与文章重写。

## 工作流

1. 读 `content/index.md`，找已发布的文章与链接。
2. 按平台收集反馈（见下）。
3. 汇总到 `content/feedback/<编号>-<主题>/<平台>.md`，格式：原始反馈 + 归类 + 提炼结论。
4. 把可执行结论回写到 `content/index.md` 的「反馈结论」区，供 article-writer 重写和系统优化引用。

## 各平台反馈收集

### 知乎 / 掘金（可自动化）

- Playwright 打开文章链接，抓取评论区（用户名 + 内容 + 时间 + 点赞数）。
- 掘金额外抓点赞 / 收藏 / 阅读量。
- 复用 `content/.browser/` 登录态；失效则提示人工扫码一次。

### 公众号（半自动）

- 公众号后台「图文分析」查看阅读 / 点赞 / 在看 / 留言。
- 留言需后台查看，人工导出后贴回 `content/feedback/`，或由本 skill 引导整理。

## 反馈归类（统一四类）

| 类别 | 含义 | 对应动作 |
|------|------|---------|
| 正面 validation | 印证了什么、哪里被认可 | 强化该论点 |
| 负面 critique | 反对 / 质疑什么 | 检查是否站得住，或改进系统 |
| 建议 suggestion | 希望补什么、期待什么功能 | 记入系统优化清单 |
| 疑问 question | 读者没看懂 / 想追问什么 | 改文章表达，补说明 |

## 反哺系统（关键步骤）

收集完反馈后，提炼「可执行结论」，回答两个问题：

1. **文章问题**：哪里没讲清楚、哪里被误解 → 回写 article-writer，用于下一轮重写。
2. **系统问题**：哪里被质疑、缺什么能力 → 记入 `content/feedback/<编号>-<主题>/system-todos.md`，作为 GleanMem 优化清单。

## 数据来源约束

- 只记录公开可见的反馈（评论区、公开数据），不记录任何用户隐私信息。
- 反馈数据只存本地 `content/feedback/`（gitignore），不上传 github。
