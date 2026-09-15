---
name: article-publisher
description: >
  Use when publishing a finished article version to 公众号 / 知乎 / 掘金.
  Handles platform-specific publish steps, login-state management, and records
  publish status in content/index.md.
---

# 文章发布（article-publisher）

把 `content/articles/` 里的平台版本发布到对应平台，并在 `content/index.md` 记录发布状态。

## 发布现实（诚实边界，务必遵守）

- **公众号**：微信后台强反爬 + 扫码登录 + 素材/群发限制，**全自动不可靠且可能触发账号风控**。默认半自动——产出「发布就绪」排版 + 分步操作指引，由人工在后台粘贴发布。
- **知乎 / 掘金**：网页版发布，可尝试 Playwright 自动化，但依赖登录态（cookie / 扫码），登录态过期需人工重新授权一次。

## 工作流

1. 读 `content/index.md`，确认待发布的文章编号与平台版本。
2. 按平台执行发布（见下）。
3. 发布成功后，在 `content/index.md` 更新：平台、发布日期、链接、版本号。

## 各平台发布方式

### 公众号（半自动）

- 产出「发布就绪」文本（标题 + 已排版正文 + 摘要建议 + 封面建议）。
- 附操作指引：
  1. 打开 mp.weixin.qq.com 登录
  2. 新建图文 → 粘贴标题与正文
  3. 配封面、摘要 → 群发 / 发布
- **不尝试自动化登录和发布**（风控风险）。

### 知乎 / 掘金（可自动化）

- 用 Playwright 打开平台，复用持久化登录态（`content/.browser/` 的 user_data_dir），先检查登录是否有效。
- 登录态失效则提示用户**手动扫码登录一次**，再继续自动化。
- 知乎：走「写文章」，粘贴标题 + Markdown 转富文本。
- 掘金：走「写文章」，Markdown 编辑器直接粘贴正文。

## 登录态维护

- Playwright 使用持久化 `user_data_dir`（默认 `content/.browser/`）保存登录态，避免每次扫码。
- 登录态是敏感凭据，只存本地 `content/.browser/`（已 gitignore），**绝不提交、不打印**。

## 发布记录

发布后更新 `content/index.md` 的发布状态表，字段：文章编号 / 平台 / 标题 / 链接 / 发布日期 / 版本。
