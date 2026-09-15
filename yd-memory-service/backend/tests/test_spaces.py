"""空间管理端点：config 请求体更新 + 归档后 key 失效。

覆盖上轮评估发现的两个 🟡：
- PUT /spaces/{id} 的 config 原为 dict 型 query param，HTTP 层改不动 → 改请求体后验证。
- archive 只置 status=archived，require_agent 不查 status → 归档后原 key 仍可访问 → 验证已失效。
"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from yd_memory_service.main import app


def _client(headers: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def test_update_config_merges_not_overwrites(space_client):
    """config 请求体浅合并：传入键覆盖、未传键保留。"""
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.put(
            f"/api/v1/spaces/{agent_id}",
            json={"config": {"decay_per_day": 0.5, "learning_mode": "llm"}},
        )
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert cfg["decay_per_day"] == 0.5
        assert cfg["learning_mode"] == "llm"
        # 未传入的键保留（不是整体覆盖）
        assert cfg["min_weight"] == 0.1
        assert cfg["max_memories"] == 5000

        # 重新读取确认持久化
        one = (await client.get(f"/api/v1/spaces/{agent_id}")).json()
        assert one["config"]["decay_per_day"] == 0.5
        assert one["config"]["learning_mode"] == "llm"


async def test_update_name_and_description_via_body(space_client):
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.put(
            f"/api/v1/spaces/{agent_id}",
            json={"name": "renamed", "description": "我的空间"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "renamed"
        assert r.json()["description"] == "我的空间"


async def test_update_empty_body_is_noop(space_client):
    """空 body 不改任何字段（字段均为可选）。"""
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        before = (await client.get(f"/api/v1/spaces/{agent_id}")).json()
        r = await client.put(f"/api/v1/spaces/{agent_id}", json={})
        assert r.status_code == 200, r.text
        after = r.json()
        assert after["name"] == before["name"]
        assert after["config"] == before["config"]


async def test_archive_invalidates_key(space_client):
    """归档后原 key 立即失效（require_agent 过滤 status != archived）。"""
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.delete(f"/api/v1/spaces/{agent_id}")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "archived"

        # 归档后原 key 访问任何受保护接口都 401
        assert (await client.get("/api/v1/spaces")).status_code == 401
        assert (await client.get("/api/v1/memories")).status_code == 401


# ---------------------------------------------------------------- C3：GET /me 去冗余


async def test_get_my_space_uses_caller_no_path_param(space_client):
    """C3：GET /api/v1/spaces/me 用 caller_id 查自己，无需传 path agent_id。

    此前 GET /{agent_id} 的 path 参数冗余（必须等于 caller）。/me 直接用
    key 解析的 caller_id，调用方更简单且无 path 伪造面。
    """
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.get("/api/v1/spaces/me")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["agent_id"] == agent_id
        # _space_dict 契约：绝不外泄 api_key_hash，只暴露 prefix
        assert "api_key_hash" not in body
        assert "api_key_prefix" in body


async def test_get_my_space_requires_auth(space_client):
    """C3：/me 同样要求 space_key 鉴权，无 key → 401。"""
    await space_client()
    async with _client() as client:
        r = await client.get("/api/v1/spaces/me")
        assert r.status_code == 401


async def test_get_me_not_swallowed_by_path_route(space_client):
    """C3：/me 路由必须在 /{agent_id} 之前声明，否则会被当 agent_id='me' 匹配。

    验证 /me 不会被 /{agent_id} 吞掉——返回的是真 Space 而非 404。
    """
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as client:
        r = await client.get("/api/v1/spaces/me")
        assert r.status_code == 200
        assert r.json()["agent_id"] == agent_id
