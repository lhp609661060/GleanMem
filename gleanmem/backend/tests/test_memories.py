"""C1：DELETE /api/v1/memories/{id} 软删端点测试（此前无 memories 专门测试）。"""
from __future__ import annotations

from uuid import uuid4

import httpx
from httpx import ASGITransport
from sqlalchemy import select

from gleanmem.core.database import async_session_factory
from gleanmem.core.models import LongTermMemory
from gleanmem.core.types import ReviewStatus
from gleanmem.main import app


def _client(space_key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {space_key}"},
    )


def _noauth_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_memory(agent_id: str, title: str = "待删记忆") -> str:
    async with async_session_factory() as s:
        m = LongTermMemory(
            agent_id=agent_id,
            type="reference",
            title=title,
            content="内容",
            weight=0.5,
            review_status="approved",
        )
        s.add(m)
        await s.commit()
        return m.id


async def test_delete_memory_soft_deletes_and_deprecates(space_client):
    """C1：DELETE 软删记忆 → is_deleted=True + review_status=deprecated，列表不再出现。"""
    key, agent_id = await space_client()
    mem_id = await _seed_memory(agent_id)

    async with _client(key) as client:
        r = await client.delete(f"/api/v1/memories/{mem_id}")
        assert r.status_code == 200
        assert r.json()["is_deleted"] is True

    async with async_session_factory() as s:
        m = (
            await s.execute(
                select(LongTermMemory).where(LongTermMemory.id == mem_id)
            )
        ).scalar_one()
        assert m.is_deleted is True
        assert m.review_status == ReviewStatus.DEPRECATED.value

        active = (
            (
                await s.execute(
                    select(LongTermMemory).where(
                        LongTermMemory.agent_id == agent_id,
                        LongTermMemory.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(row.id != mem_id for row in active)


async def test_delete_memory_404_for_unknown(space_client):
    """C1：删不存在的记忆（合法 UUID 但库里没有）→ 404。"""
    key, _ = await space_client()
    async with _client(key) as client:
        r = await client.delete(f"/api/v1/memories/{uuid4()}")
        assert r.status_code == 404


async def test_delete_memory_404_for_other_space(space_client):
    """C1：删别的 Space 的记忆 → 404（归属校验，不泄漏存在性），原记忆仍存活。"""
    key_a, _ = await space_client()
    key_b, agent_b = await space_client()
    mem_b = await _seed_memory(agent_b)

    async with _client(key_a) as client:
        r = await client.delete(f"/api/v1/memories/{mem_b}")
        assert r.status_code == 404

    async with async_session_factory() as s:
        m = (
            await s.execute(
                select(LongTermMemory).where(LongTermMemory.id == mem_b)
            )
        ).scalar_one()
        assert m.is_deleted is False


# ---------------------------------------------------------------- D2：/health 探活（200 分支）


async def test_health_returns_200_when_db_ok():
    """D2：DB 连通时 /health 返 200 ok（与 503 分支互补，覆盖探活双向行为）。"""
    async with _noauth_client() as client:
        r = await client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
