"""Week 2：per-space API Key 鉴权与身份隔离。"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from yd_memory_service.main import app


def _client(headers: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def test_rest_requires_bearer_key(space_client):
    key, _ = await space_client()
    async with _client() as client:
        assert (await client.get("/api/v1/memories")).status_code == 401
        assert (
            await client.get(
                "/api/v1/memories", headers={"Authorization": "Bearer wrong-key"}
            )
        ).status_code == 401


async def test_space_key_isolates_data(space_client):
    """A 的 key 无法读写 B 的空间：flush/列表按 key 解析的 agent_id 隔离。"""
    key_a, agent_a = await space_client()
    key_b, agent_b = await space_client()

    async with _client({"Authorization": f"Bearer {key_b}"}) as cb:
        r = await cb.post(
            "/api/v1/learning/events",
            json={"type": "user_feedback", "context": "B 的私有规则：只发顺丰"},
        )
        assert r.status_code == 200
        assert (await cb.post("/api/v1/learning/flush")).json()["status"] == "ok"
        mems_b = (await cb.get("/api/v1/memories")).json()
        assert len(mems_b) == 1

    # A 侧看不到 B 的记忆，flush 也只处理 A 的事件
    async with _client({"Authorization": f"Bearer {key_a}"}) as ca:
        mems_a = (await ca.get("/api/v1/memories")).json()
        assert mems_a == []
        assert (await ca.post("/api/v1/learning/flush")).json()["processed"] == 0


async def test_flush_identity_from_key_not_body(space_client):
    """flush 不接受 body 里的 agent_id（身份不变式），身份只来自 key。"""
    key_a, _ = await space_client()
    async with _client({"Authorization": f"Bearer {key_a}"}) as client:
        r = await client.post(
            "/api/v1/learning/flush",
            json={"agent_id": "someone-else"},  # body 里塞身份应被忽略
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
