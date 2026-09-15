"""观察事件 feeder：模拟业务系统向 /api/v1/observations push 发货单事件流。

用法（先起服务：uv run gleanmem，再创建 Space 拿 space_key）：
    uv run python scripts/feeder_observations.py <space_key> [base_url]

重跑无害：事件 id 幂等（重复推送返回 duplicate）。
"""
from __future__ import annotations

import asyncio
import sys

import httpx

# 发货单生命周期事件流 + 业务规则观察事件
EVENTS: list[dict] = [
    # 发货单 1：完整生命周期（四段流转）
    {"event_id": "ship-001-created", "entity": "shipment", "event": "entity_created",
     "change": {"to": "待确认"}, "occurred_at": "2026-08-18T09:00:00Z"},
    {"event_id": "ship-001-confirmed", "entity": "shipment", "event": "entity_changed",
     "change": {"from": "待确认", "to": "待发货"}, "occurred_at": "2026-08-18T09:15:00Z"},
    {"event_id": "ship-001-shipped", "entity": "shipment", "event": "entity_changed",
     "change": {"from": "待发货", "to": "已发货"}, "occurred_at": "2026-08-18T11:00:00Z"},
    {"event_id": "ship-001-signed", "entity": "shipment", "event": "entity_changed",
     "change": {"from": "已发货", "to": "已签收"}, "occurred_at": "2026-08-19T10:00:00Z"},
    # 发货单 2：同样的四段流转（稳定模式，供蒸馏归纳）
    {"event_id": "ship-002-created", "entity": "shipment", "event": "entity_created",
     "change": {"to": "待确认"}, "occurred_at": "2026-08-18T14:00:00Z"},
    {"event_id": "ship-002-confirmed", "entity": "shipment", "event": "entity_changed",
     "change": {"from": "待确认", "to": "待发货"}, "occurred_at": "2026-08-18T14:20:00Z"},
    {"event_id": "ship-002-shipped", "entity": "shipment", "event": "entity_changed",
     "change": {"from": "待发货", "to": "已发货"}, "occurred_at": "2026-08-19T09:00:00Z"},
    # 业务规则观察：重量单位
    {"event_id": "rule-weight-unit", "entity": "shipment_rule", "event": "rule_observed",
     "change": {"rule": "发货单重量单位统一使用吨，不使用千克"},
     "occurred_at": "2026-08-18T16:00:00Z"},
    # 业务规则观察：区域快递
    {"event_id": "rule-xiamen-sf", "entity": "shipping_rule", "event": "rule_observed",
     "change": {"rule": "厦门地区客户发货统一使用顺丰快递", "region": "厦门"},
     "occurred_at": "2026-08-18T16:05:00Z"},
    # 一次性噪音事件（应被蒸馏为 discard）
    {"event_id": "noise-temp-remark", "entity": "shipment", "event": "one_off_remark",
     "change": {"remark": "临时口头通知，仅限今天"}, "occurred_at": "2026-08-18T17:00:00Z"},
]


async def main() -> None:
    if len(sys.argv) < 2:
        print("用法: uv run python scripts/feeder_observations.py <space_key> [base_url]")
        raise SystemExit(2)
    space_key = sys.argv[1]
    base_url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"

    headers = {"Authorization": f"Bearer {space_key}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10) as client:
        accepted = 0
        for ev in EVENTS:
            r = await client.post("/api/v1/observations", json=ev)
            body = r.json()
            if body.get("status") == "accepted":
                accepted += 1
            print(f"[{r.status_code}] {ev['event_id']}: {body.get('status')}")
        print(f"\n共推送 {len(EVENTS)} 个事件，新接受 {accepted} 个")
        print("下一步: curl -X POST <base>/api/v1/learning/flush -H 'Authorization: Bearer <space_key>'")


if __name__ == "__main__":
    asyncio.run(main())
