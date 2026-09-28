"""D18：flush 两阶段化 —— 快照(短事务) → LLM(无事务无行锁) → 应用(短事务)。

钉住三条重构后必须仍成立的性质：
1. 批上限（settings.flush_batch_size）限定单次消费条数，余量留给下一次 flush；
2. 阶段③ 期间被外部删除的事件，在阶段④ 直接跳过（不写决策、不产生日志）；
3. 分析失败/无决策时事件保留走 P1-1，且 advisory lock 必然释放。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import delete, func, select

from gleanmem.config import settings
from gleanmem.core.database import async_session_factory
from gleanmem.core.learning import LearningModel
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models import LearningLog, LongTermMemory, PendingEvent


async def _add_events(session, agent_id: str, n: int, prefix: str = "规则"):
    for i in range(n):
        session.add(
            PendingEvent(
                agent_id=agent_id,
                event_type="user_feedback",
                context=f"{prefix}{i}：厦门客户使用顺丰快递",
            )
        )
    await session.flush()
    await session.commit()


async def _count(model, agent_id: str) -> int:
    async with async_session_factory() as s:
        return (
            await s.execute(
                select(func.count()).select_from(model).where(model.agent_id == agent_id)
            )
        ).scalar_one()


async def test_flush_respects_batch_size(make_space, monkeypatch):
    """批上限：12 条事件、batch=5 → 一次只消费最早的 5 条，余下 7 条留在收件箱。"""
    monkeypatch.setattr(settings, "flush_batch_size", 5)
    agent_id = await make_space(learning_mode="heuristic")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, 12)
        mgr = MemoryManager(s)
        result = await mgr.flush(agent_id)
        assert result["status"] == "ok"
        assert result["processed"] == 5

    assert await _count(PendingEvent, agent_id) == 7
    assert await _count(LongTermMemory, agent_id) == 5

    # 再 flush 一次继续消费（事件不会因批处理而被卡住）
    async with async_session_factory() as s:
        result2 = await MemoryManager(s).flush(agent_id)
        assert result2["processed"] == 5
    assert await _count(PendingEvent, agent_id) == 2


async def test_flush_skips_events_deleted_during_analysis(make_space, monkeypatch):
    """阶段③（LLM 期间）事件被外部删除 → 阶段④ 重选时跳过，不留孤儿决策/日志。"""
    agent_id = await make_space(learning_mode="heuristic")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, 3)
        victim = (
            (
                await s.execute(
                    select(PendingEvent.id).where(PendingEvent.agent_id == agent_id).limit(1)
                )
            )
            .scalars()
            .one()
        )

    real_analyze = LearningModel._analyze

    async def _analyze_then_delete(self, *a, **kw):
        analysis = await real_analyze(self, *a, **kw)
        # 模拟 LLM 往返期间业务方/运维把这条事件清掉了
        async with async_session_factory() as ext:
            await ext.execute(delete(PendingEvent).where(PendingEvent.id == victim))
            await ext.commit()
        return analysis

    monkeypatch.setattr(LearningModel, "_analyze", _analyze_then_delete)

    async with async_session_factory() as s:
        result = await MemoryManager(s).flush(agent_id)

    assert result["status"] == "ok"
    assert result["processed"] == 2, "消失的事件不应产生决策"
    assert await _count(PendingEvent, agent_id) == 0
    assert await _count(LongTermMemory, agent_id) == 2
    assert await _count(LearningLog, agent_id) == 2


async def test_llm_failure_keeps_events_and_releases_lock(make_space, monkeypatch):
    """无 LLM key → 空决策走 P1-1：事件保留、retry+1、无记忆；锁已释放可再次 flush。"""
    monkeypatch.setattr(settings, "llm_api_key", "")
    agent_id = await make_space(learning_mode="llm")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, 2)

    async with async_session_factory() as s:
        result = await MemoryManager(s).flush(agent_id)
    assert result["status"] == "ok"
    assert result["processed"] == 0
    assert await _count(LongTermMemory, agent_id) == 0

    async with async_session_factory() as s:
        events = (
            (await s.execute(select(PendingEvent).where(PendingEvent.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(events) == 2
        assert all(e.retry_count == 1 for e in events)

    # 锁没漏：紧接着的第二次 flush 能正常拿到锁（否则返回 skipped）
    async with async_session_factory() as s:
        assert (await MemoryManager(s).flush(agent_id))["status"] == "ok"


async def test_concurrent_flush_second_returns_none(make_space, monkeypatch):
    """advisory lock 跨阶段仍互斥：分析慢的时候第二个 flush 必须让路。"""
    agent_id = await make_space(learning_mode="heuristic")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, 2)

    real_analyze = LearningModel._analyze

    async def _slow_analyze(self, *a, **kw):
        await asyncio.sleep(0.5)
        return await real_analyze(self, *a, **kw)

    monkeypatch.setattr(LearningModel, "_analyze", _slow_analyze)

    async def run_one():
        async with async_session_factory() as s:
            return await LearningModel(s).run_pipeline(agent_id, "heuristic")

    first, second = await asyncio.gather(run_one(), run_one())
    outcomes = [first, second]
    assert sum(o is not None for o in outcomes) == 1, "并发 flush 必须恰好一个执行、一个让路"
    assert await _count(PendingEvent, agent_id) == 0
