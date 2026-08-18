# 集成指南 · 业务系统（观察事件 push）

> 对应 01-design.md §c 业务观察学习（push-first）。业务方把「业务行为」作为事件推过来，服务持续积累、flush 时蒸馏成带溯源的业务知识。

## 1. 拿 space_key

同 integration-dsh.md 第 1 节。

## 2. 事件 schema

```
POST /api/v1/observations        Authorization: Bearer <space_key>
```

```json
{
  "event_id": "ship-20260716-001",          // 幂等键：业务方生成，全局唯一
  "entity":   "shipment",                   // 业务对象
  "event":    "status_changed",             // 发生了什么
  "change":   {"from": "待确认", "to": "待发货"},   // 这就是 diff
  "payload":  { "...": "..." },             // 可选原始上下文（≤4KB）
  "occurred_at": "2026-07-16T10:00:00Z"     // 业务发生时间，非推送时间
}
```

响应：`{"status": "accepted" | "duplicate", "event_id": "..."}`

## 3. 约定与护栏

| 护栏 | 规则 |
|------|------|
| 幂等 | `event_id` 即幂等键（`dedup_key`），业务方重试推送返回 `duplicate`，不产生重复事件 |
| 限长 | payload 序列化后 ≤ 4KB，超限返回 413（拒绝而非截断） |
| 脱敏 | 业务方推送前自行脱敏；服务不做 payload 内容二次清洗 |
| 时效 | `occurred_at` 用业务发生时间；服务端仅按到达顺序进收件箱 |

## 4. 蒸馏（flush 时）

- **heuristic/direct 模式**：事件逐条存为 reference 记忆，`metadata` 固化 `source/origin/evidence`
- **llm 模式**（`YDM_LLM_API_KEY` 配好 + Space `learning_mode=llm`）：按冻结契约归纳业务规则——单事件明确规则 store、同实体稳定模式归纳成一条、噪音 discard；**无 evidence 引用的 store 一律降级 discard（防幻觉）**；产物可选 `target=wiki` 落业务词典（Skill 检索）
- 失败/空结果按 P1-1 保留事件重试（≥3 次记 failed 日志）

## 5. 快速演示（feeder）

```bash
# 起服务后：创建 Space 拿 key，然后
cd backend && uv run python scripts/feeder_observations.py <space_key>
curl -X POST http://localhost:8000/api/v1/learning/flush -H "Authorization: Bearer <space_key>"
curl "http://localhost:8000/api/v1/memories" -H "Authorization: Bearer <space_key>"
```

## 6. pull（抓取）什么时候做

V1 只做 push。若将来遇到「对方只有数据库、没有事件出口」的场景，在其他项目把「连接器 + 快照 diff」练成熟后，作为新的 producer 产出同一种 `ObservationEvent` 回融——下游零改动（01-design §c 回融纪律）。
