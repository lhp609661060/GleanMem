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


# -- Space 端点鉴权与密钥哈希不外泄（前端接入时发现的缺陷）------------------

async def test_space_endpoints_require_auth(space_client):
    """原实现 GET/PUT/DELETE /spaces 完全无鉴权：可枚举全部 Space、可改他人配置。"""
    _, agent_id = await space_client()
    async with _client() as client:
        assert (await client.get("/api/v1/spaces")).status_code == 401
        assert (await client.get(f"/api/v1/spaces/{agent_id}")).status_code == 401
        assert (await client.put(f"/api/v1/spaces/{agent_id}?name=hacked")).status_code == 401
        assert (await client.delete(f"/api/v1/spaces/{agent_id}")).status_code == 401


async def test_space_response_never_leaks_key_hash(space_client):
    """api_key_hash 是 space_key 的 SHA-256，绝不能出现在响应里。"""
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        listed = (await client.get("/api/v1/spaces")).json()
        one = (await client.get(f"/api/v1/spaces/{agent_id}")).json()

    assert len(listed) == 1 and listed[0]["agent_id"] == agent_id
    for payload in (listed[0], one):
        assert "api_key_hash" not in payload
        assert "api_key_prefix" in payload  # 前缀可见（UI 辨认用）


async def test_space_list_scoped_to_own_key(space_client):
    """列表只返回自己的 Space，不枚举他人。"""
    key_a, agent_a = await space_client()
    key_b, agent_b = await space_client()

    async with _client({"Authorization": f"Bearer {key_a}"}) as ca:
        ids = [s["agent_id"] for s in (await ca.get("/api/v1/spaces")).json()]
        assert ids == [agent_a]
        assert agent_b not in ids
        # 直接按 id 取他人 Space → 404（不泄露存在性）
        assert (await ca.get(f"/api/v1/spaces/{agent_b}")).status_code == 404


async def test_cross_space_mutation_blocked(space_client):
    """A 不能改/归档 B 的 Space。"""
    key_a, _ = await space_client()
    key_b, agent_b = await space_client()

    async with _client({"Authorization": f"Bearer {key_a}"}) as ca:
        assert (await ca.put(f"/api/v1/spaces/{agent_b}?name=hacked")).status_code == 404
        assert (await ca.delete(f"/api/v1/spaces/{agent_b}")).status_code == 404

    async with _client({"Authorization": f"Bearer {key_b}"}) as cb:
        assert (await cb.get(f"/api/v1/spaces/{agent_b}")).json()["name"] != "hacked"


async def test_own_space_update_persists(space_client):
    """自己的 Space 可改，且改动真落库（原实现只 flush 未 commit）。"""
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.put(f"/api/v1/spaces/{agent_id}?name=renamed")
        assert r.status_code == 200
        assert r.json()["name"] == "renamed"
        # 重新读取确认持久化
        assert (await client.get(f"/api/v1/spaces/{agent_id}")).json()["name"] == "renamed"
