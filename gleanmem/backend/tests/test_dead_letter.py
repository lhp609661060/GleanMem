"""D19：事件死信化 —— 重试耗尽不再删数据，标 dead 冻结 + 可复活。

钉住的语义变化：调度器 at-most-once 与「重试后删事件」组合的最终语义原本是丢数据，
现在最坏是「停在死信里等人处理」。
"""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from gleanmem.config import settings
from gleanmem.core.database import async_session_factory
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models import LearningLog, LongTermMemory, PendingEvent
from gleanmem.main import app


def _client(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )


async def _space_key(agent_id: str) -> str:
    """给测试 Space 设一把已知 key（revive / events 端点要 space 身份）。"""
    async with async_session_factory() as s:
        result = await MemoryManager(s).rotate_key(agent_id)
        assert result is not None
        _space, key = result
        await s.commit()
    return key


async def _drain_to_dead(agent_id: str) -> str:
    """用 llm 模式（无 key → 恒空决策）连打 flush，直到事件转 dead，返回事件 id。"""
    async with async_session_factory() as s:
        s.add(
            PendingEvent(
                agent_id=agent_id,
                event_type="user_feedback",
                context="死信用例：厦门客户使用顺丰快递",
            )
        )
        await s.commit()

    for _ in range(settings.event_max_retries):
        async with async_session_factory() as s:
            await MemoryManager(s).flush(agent_id)
            await s.commit()

    async with async_session_factory() as s:
        return (
            await s.execute(
                select(PendingEvent.id).where(PendingEvent.agent_id == agent_id)
            )
        ).scalar_one()


@pytest.fixture(autouse=True)
def _no_llm_key(monkeypatch):
    """llm 模式无 key → 分析恒返回空决策，稳定触发 P1-1 重试路径。"""
    monkeypatch.setattr(settings, "llm_api_key", "")


async def test_exhausted_event_is_dead_not_deleted(make_space):
    agent_id = await make_space(learning_mode="llm")
    event_id = await _drain_to_dead(agent_id)

    async with async_session_factory() as s:
        ev = (
            await s.execute(select(PendingEvent).where(PendingEvent.id == event_id))
        ).scalar_one_or_none()
        assert ev is not None, "死信化后数据必须仍在库"
        assert ev.status == "dead"
        assert ev.retry_count == settings.event_max_retries

        logs = (
            await s.execute(
                select(LearningLog).where(
                    LearningLog.agent_id == agent_id,
                    LearningLog.decision_action == "failed",
                )
            )
        ).scalars().all()
        assert len(logs) == 1
        assert "dead-lettered" in (logs[0].error_message or "")


async def test_dead_event_never_enters_flush(make_space):
    """dead 事件不进快照：再打 flush 既不处理它、retry 也不再增长。"""
    agent_id = await make_space(learning_mode="llm")
    await _drain_to_dead(agent_id)

    async with async_session_factory() as s:
        result = await MemoryManager(s).flush(agent_id)
        assert result["status"] == "ok"

    async with async_session_factory() as s:
        ev = (
            (await s.execute(select(PendingEvent).where(PendingEvent.agent_id == agent_id)))
            .scalars()
            .one()
        )
        assert ev.retry_count == settings.event_max_retries
        assert ev.status == "dead"


async def test_revive_endpoint_reprocesses_event(make_space):
    """revive → pending/retry=0，下次 flush 重新处理（retry 从 0 重新起算）。"""
    agent_id = await make_space(learning_mode="llm")
    event_id = await _drain_to_dead(agent_id)

    key = await _space_key(agent_id)
    async with _client(key) as c:
        r = await c.post(f"/api/v1/learning/events/{event_id}/revive")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "revived", "event_id": event_id}

    async with async_session_factory() as s:
        await MemoryManager(s).flush(agent_id)
        await s.commit()
    async with async_session_factory() as s:
        ev = (
            await s.execute(select(PendingEvent).where(PendingEvent.id == event_id))
        ).scalar_one()
        assert ev.retry_count == 1, "复活后必须重新进入重试计数"


async def test_revive_rejects_other_space_event(space_client):
    key_a, _aid_a = await space_client()
    # B 必须是 llm 模式（无 key → 恒空决策），事件才会转 dead；heuristic 会直接学掉
    _key_b, aid_b = await space_client({"learning_mode": "llm"})
    event_id = await _drain_to_dead(aid_b)

    async with _client(key_a) as c:
        r = await c.post(f"/api/v1/learning/events/{event_id}/revive")
        assert r.status_code == 404, "跨 Space 不泄露存在性"


async def test_revive_rejects_pending_event(make_space):
    """pending 事件不需要复活（它正在管线里）。"""
    agent_id = await make_space(learning_mode="heuristic")
    async with async_session_factory() as s:
        ev = PendingEvent(agent_id=agent_id, event_type="user_feedback", context="普通事件")
        s.add(ev)
        await s.commit()
        event_id = ev.id

    key = await _space_key(agent_id)
    async with _client(key) as c:
        assert (await c.post(f"/api/v1/learning/events/{event_id}/revive")).status_code == 404


async def test_list_events_returns_only_dead(make_space):
    agent_id = await make_space(learning_mode="llm")
    dead_id = await _drain_to_dead(agent_id)
    async with async_session_factory() as s:
        s.add(PendingEvent(agent_id=agent_id, event_type="user_feedback", context="还在排队的"))
        await s.commit()

    key = await _space_key(agent_id)
    async with _client(key) as c:
        dead = (await c.get("/api/v1/learning/events?status=dead")).json()
        assert [e["id"] for e in dead] == [dead_id]
        assert dead[0]["retry_count"] == settings.event_max_retries

        pending = (await c.get("/api/v1/learning/events?status=pending")).json()
        assert len(pending) == 1
        assert pending[0]["context"] == "还在排队的"


async def test_dead_event_memory_not_created(make_space):
    """死信期间不该有任何记忆产物（数据冻结，不是学坏了）。"""
    agent_id = await make_space(learning_mode="llm")
    await _drain_to_dead(agent_id)
    async with async_session_factory() as s:
        n = (
            await s.execute(
                select(LongTermMemory).where(LongTermMemory.agent_id == agent_id)
            )
        ).scalars().all()
        assert n == []
