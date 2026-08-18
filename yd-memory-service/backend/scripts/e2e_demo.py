"""端到端演示：创建 Space → push 观察事件 → flush → recall → 审计链路验证。

用法（服务已起，`uv run yd-memory`）：
    uv run python scripts/e2e_demo.py [base_url]

输出每一步的请求/结果，最后打印审计链路结论。
"""
from __future__ import annotations

import asyncio
import sys

import httpx


async def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    print(f"=== yd-memory-service 端到端演示（{base}）===\n")

    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        # 1. 创建 Space（拿 key）
        r = await client.post("/api/v1/spaces", json={"name": f"e2e-{__import__('uuid').uuid4().hex[:6]}"})
        r.raise_for_status()
        space = r.json()
        key = space["space_key"]
        print(f"[1] 创建 Space: agent_id={space['agent_id'][:8]}… key_prefix={space['key_prefix']}")

        headers = {"Authorization": f"Bearer {key}"}

        # 2. 无 key 请求应 401
        r401 = await client.get("/api/v1/memories")
        assert r401.status_code == 401, f"预期 401，实际 {r401.status_code}"
        print("[2] 无 key 访问 → 401 ✅（身份层生效）")

        # 3. push 观察事件（发货单生命周期）
        events = [
            {"event_id": "d-1-c", "entity": "shipment", "event": "entity_created", "change": {"to": "待确认"}, "occurred_at": "2026-08-18T09:00:00Z"},
            {"event_id": "d-1-x", "entity": "shipment", "event": "entity_changed", "change": {"from": "待确认", "to": "待发货"}, "occurred_at": "2026-08-18T09:15:00Z"},
            {"event_id": "d-1-f", "entity": "shipment", "event": "entity_changed", "change": {"from": "待发货", "to": "已发货"}, "occurred_at": "2026-08-18T11:00:00Z"},
            {"event_id": "d-r-w", "entity": "shipment_rule", "event": "rule_observed", "change": {"rule": "发货单重量单位统一使用吨，不使用千克"}, "occurred_at": "2026-08-18T16:00:00Z"},
        ]
        for ev in events:
            rr = await client.post("/api/v1/observations", json=ev, headers=headers)
            assert rr.json()["status"] == "accepted"
        print(f"[3] push {len(events)} 个观察事件 → accepted")

        # 4. 幂等：重复 push 第一条
        rr = await client.post("/api/v1/observations", json=events[0], headers=headers)
        assert rr.json()["status"] == "duplicate"
        print("[4] 重复 push 同一 event_id → duplicate ✅（幂等生效）")

        # 5. flush
        rf = await client.post("/api/v1/learning/flush", headers=headers)
        flush = rf.json()
        assert flush["status"] == "ok" and flush["processed"] == 4
        print(f"[5] flush → processed={flush['processed']} actions={flush['actions'][:4]}…")

        # 6. recall（自然语言检索）
        rr = await client.post("/api/v1/recall", json={"intent": "发货单重量用什么单位"}, headers=headers)
        rec = rr.json()
        assert rec["memories"], "recall 应有结果"
        print(f"[6] recall『发货单重量用什么单位』→ {len(rec['memories'])} 条记忆，Top1: {rec['memories'][0]['title']}")
        assert "重量单位" in rec["memories"][0]["title"] or "shipment_rule" in rec["memories"][0]["title"]

        # 7. 审计链路
        rl = await client.get("/api/v1/learning/logs", headers=headers)
        logs = rl.json()
        assert len(logs) == 4 and all(lg["source"] == "observation" for lg in logs)
        print(f"[7] 审计：learning_logs {len(logs)} 条，全部 source=observation，decision 可溯源 ✅")

        # 8. 隔离：另一个 Space 看不到
        r2 = await client.post("/api/v1/spaces", json={"name": "e2e-other"})
        key2 = r2.json()["space_key"]
        rp = await client.get("/api/v1/memories", headers={"Authorization": f"Bearer {key2}"})
        assert rp.json() == []
        print("[8] 第二个 Space 用自己 key 查询 → 空 ✅（按 agent_id 隔离）")

    print("\n=== ✅ 端到端演示全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
