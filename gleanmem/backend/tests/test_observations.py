"""Week 2：观察入站口——幂等、限长、鉴权、heuristic 蒸馏链路。"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from gleanmem.main import app

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


# ---------------------------------------------------------------- V2-3：批量 push


def _batch_events(n: int) -> list[dict]:
    return [
        {
            "event_id": f"batch-evt-{i}",
            "entity": "shipment",
            "event": "entity_changed",
            "change": {"to": f"状态{i}"},
            "occurred_at": "2026-08-18T10:00:00Z",
        }
        for i in range(n)
    ]


async def test_observation_batch_push_accepted(space_client):
    """V2-3：批量推 3 条全 accepted，1 次 HTTP。"""
    key, _ = await space_client({"learning_mode": "heuristic"})
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.post("/api/v1/observations/batch", json=_batch_events(3))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["count"] == 3
        assert all(item["status"] == "accepted" for item in body["items"])


async def test_observation_batch_idempotent_mixed(space_client):
    """V2-3：批量含已存在 event_id → 逐条 accepted/duplicate，幂等不重复入库。"""
    key, _ = await space_client({"learning_mode": "heuristic"})
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        # 先单条推一条（建立 duplicate 候选）
        first = {
            "event_id": "batch-dup",
            "entity": "s",
            "event": "x",
            "occurred_at": "2026-08-18T10:00:00Z",
        }
        assert (await client.post("/api/v1/observations", json=first)).status_code == 200
        # 批量：第1条重复，2-3条新
        batch = [
            first,  # 已存在 → duplicate
            {"event_id": "batch-new-1", "entity": "s", "event": "x"},
            {"event_id": "batch-new-2", "entity": "s", "event": "x"},
        ]
        r = await client.post("/api/v1/observations/batch", json=batch)
        assert r.status_code == 200, r.text
        items = {i["event_id"]: i["status"] for i in r.json()["items"]}
        assert items["batch-dup"] == "duplicate"
        assert items["batch-new-1"] == "accepted"
        assert items["batch-new-2"] == "accepted"


async def test_observation_batch_empty_rejected(space_client):
    """V2-3：空 batch → 400。"""
    key, _ = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.post("/api/v1/observations/batch", json=[])
        assert r.status_code == 400


async def test_observation_batch_over_100_rejected(space_client):
    """V2-3：超过 100 条上限 → 413，整批不入库。"""
    key, _ = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.post("/api/v1/observations/batch", json=_batch_events(101))
        assert r.status_code == 413


async def test_observation_batch_requires_key():
    """V2-3：batch 端点同样要求 space_key，无 key → 401。"""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/api/v1/observations/batch", json=_batch_events(1))
        assert r.status_code == 401
