"""平台管理台（admin key）测试：全量空间列表、轮换 key、归档任意、身份探测。"""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from gleanmem.config import settings
from gleanmem.main import app

ADMIN_KEY = "ydm_admin_test_key_12345"


@pytest.fixture(autouse=True)
def _admin_key():
    old = settings.admin_key
    settings.admin_key = ADMIN_KEY
    yield
    settings.admin_key = old


def _client(headers: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


def _admin() -> dict:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


async def test_auth_me_returns_role(space_client):
    key, agent_id = await space_client()
    async with _client({"Authorization": f"Bearer {key}"}) as c:
        r = await c.get("/api/v1/auth/me")
        assert r.status_code == 200
        assert r.json() == {"role": "space", "agent_id": agent_id}

    async with _client(_admin()) as c:
        r = await c.get("/api/v1/auth/me")
        assert r.status_code == 200
        assert r.json() == {"role": "admin", "agent_id": None}


async def test_admin_lists_all_spaces(space_client):
    key_a, aid_a = await space_client()
    _, aid_b = await space_client()

    # space key 只能看到自己
    async with _client({"Authorization": f"Bearer {key_a}"}) as c:
        ids = [s["agent_id"] for s in (await c.get("/api/v1/spaces")).json()]
        assert ids == [aid_a]

    # admin 看到全部（含 aid_a 和 aid_b）
    async with _client(_admin()) as c:
        ids = [s["agent_id"] for s in (await c.get("/api/v1/spaces")).json()]
        assert aid_a in ids and aid_b in ids


async def test_admin_rotate_any_space_key(space_client):
    key, aid = await space_client()
    async with _client(_admin()) as c:
        r = await c.post(f"/api/v1/spaces/{aid}/keys")
        assert r.status_code == 200, r.text
        new_key = r.json()["space_key"]
        assert new_key.startswith("ydm_")

    # 旧 key 失效
    async with _client({"Authorization": f"Bearer {key}"}) as c:
        assert (await c.get("/api/v1/spaces")).status_code == 401
    # 新 key 生效
    async with _client({"Authorization": f"Bearer {new_key}"}) as c:
        assert (await c.get("/api/v1/spaces")).status_code == 200


async def test_admin_archive_any_space(space_client):
    _, aid = await space_client()
    async with _client(_admin()) as c:
        r = await c.delete(f"/api/v1/spaces/{aid}")
        assert r.status_code == 200
        assert r.json()["status"] == "archived"

    # 归档后 admin 列表里该空间 status=archived
    async with _client(_admin()) as c:
        spaces = (await c.get("/api/v1/spaces")).json()
        one = next(s for s in spaces if s["agent_id"] == aid)
        assert one["status"] == "archived"


async def test_space_cannot_rotate_others_key(space_client):
    _, aid_a = await space_client()
    key_b, _ = await space_client()
    async with _client({"Authorization": f"Bearer {key_b}"}) as c:
        assert (await c.post(f"/api/v1/spaces/{aid_a}/keys")).status_code == 404


async def test_invalid_admin_key_rejected():
    async with _client({"Authorization": "Bearer wrong-admin"}) as c:
        assert (await c.get("/api/v1/auth/me")).status_code == 401
