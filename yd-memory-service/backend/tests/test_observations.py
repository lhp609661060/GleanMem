"""Week 2：观察入站口——幂等、限长、鉴权、heuristic 蒸馏链路。"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from yd_memory_service.main import app

EVENT = {
    "event_id": "ship-001-created",
    "entity": "shipment",
    "event": "entity_created",
    "change": {"to": "待确认"},
    "occurred_at": "2026-08-18T10:00:00Z",
}


def _client(headers: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def test_observation_idempotent_push(space_client):
    key, _ = await space_client({"learning_mode": "heuristic", "decay_per_day": 1.0, "min_weight": 1.0})
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r1 = await client.post("/api/v1/observations", json=EVENT)
        assert r1.status_code == 200
        assert r1.json()["status"] == "accepted"
        # 重复推送（业务方重试）→ duplicate，不产生第二条事件
        r2 = await client.post("/api/v1/observations", json=EVENT)
        assert r2.json()["status"] == "duplicate"


async def test_observation_heuristic_flush_produces_memory_with_provenance(space_client):
    key, _ = await space_client({"learning_mode": "heuristic", "decay_per_day": 1.0, "min_weight": 1.0})
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        assert (await client.post("/api/v1/observations", json=EVENT)).status_code == 200
        flush = (await client.post("/api/v1/learning/flush")).json()
        assert flush["status"] == "ok"
        assert flush["processed"] == 1

        mems = (await client.get("/api/v1/memories")).json()
        assert len(mems) == 1
        assert mems[0]["metadata"]["source"] == "observation"
        assert mems[0]["metadata"]["origin"] == "shipment"
        assert mems[0]["metadata"]["evidence"] == ["obs:ship-001-created"]

        logs = (await client.get("/api/v1/learning/logs")).json()
        assert any(lg["source"] == "observation" for lg in logs)


async def test_observation_payload_over_4kb_rejected(space_client):
    key, _ = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        big = {"event_id": "big-1", "entity": "s", "event": "x", "payload": {"blob": "中" * 3000}}
        r = await client.post("/api/v1/observations", json=big)
        assert r.status_code == 413


async def test_observation_requires_key():
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/api/v1/observations", json=EVENT)).status_code == 401
