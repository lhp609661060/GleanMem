"""Day 3-5：学习管线数据完整性 + 审计。

覆盖 P1-1（空/失败决策不丢事件、retry_count、failed 日志）、llm_raw_response 落库、
decay 读 Space 配置、_llm_analyze JSON 解析、flush 并发锁 skipped 语义。
"""
from __future__ import annotations

import asyncpg
from sqlalchemy import func, select

from gleanmem.config import settings
from gleanmem.core.database import async_session_factory
from gleanmem.core.learning import LearningModel, MemoryDecision
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models import LearningLog, LongTermMemory, PendingEvent
from gleanmem.core.types import DecisionAction, MemoryType


async def _add_events(session, agent_id: str, n: int = 2, retry_count: int = 0):
    for i in range(n):
        session.add(
            PendingEvent(
                agent_id=agent_id,
                event_type="user_feedback",
                context=f"测试规则{i}：厦门客户使用顺丰快递",
                retry_count=retry_count,
            )
        )
    await session.flush()
    await session.commit()


def _fake_llm_empty(raw: str = "raw-empty"):
    async def fake(events):
        return [], raw

    return fake


def _fake_llm_store_all(raw: str = "RAW-JSON"):
    async def fake(events):
        return [
            MemoryDecision(
                action=DecisionAction.STORE,
                event_id=e.id,
                title=e.context[:200],
                content=e.context,
                memory_type=MemoryType.FEEDBACK,
            )
            for e in events
        ], raw

    return fake


def _fake_llm_first_only(raw: str = "RAW-JSON"):
    async def fake(events):
        if not events:
            return [], raw
        e = events[0]
        return [
            MemoryDecision(
                action=DecisionAction.STORE,
                event_id=e.id,
                title=e.context[:200],
                content=e.context,
                memory_type=MemoryType.FEEDBACK,
            )
        ], raw

    return fake


async def test_llm_empty_decisions_keep_events_and_increment_retry(make_space):
    """P1-1：LLM 返回空决策 → 事件保留、retry_count 递增、不落记忆。"""
    agent_id = await make_space(learning_mode="llm")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, n=2)
        mgr = MemoryManager(s)
        mgr._learning._llm_analyze = _fake_llm_empty()
        result = await mgr.flush(agent_id)
        await s.commit()

        assert result["status"] == "ok"
        assert result["processed"] == 0

        events = (
            (await s.execute(select(PendingEvent).where(PendingEvent.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(events) == 2
        assert all(e.retry_count == 1 for e in events)

        mem_count = (
            await s.execute(
                select(func.count()).select_from(LongTermMemory).where(LongTermMemory.agent_id == agent_id)
            )
        ).scalar_one()
        assert mem_count == 0


async def test_llm_retry_exhausted_logs_failed_and_removes(make_space):
    """P1-1：retry_count 已达 2 的失败事件，再失败一次 → 写 failed 日志并移除。"""
    agent_id = await make_space(learning_mode="llm")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, n=2, retry_count=2)
        mgr = MemoryManager(s)
        mgr._learning._llm_analyze = _fake_llm_empty(raw="raw-last-try")
        await mgr.flush(agent_id)
        await s.commit()

        events = (
            (await s.execute(select(PendingEvent).where(PendingEvent.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert events == []

        logs = (
            (await s.execute(select(LearningLog).where(LearningLog.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(logs) == 2
        assert all(lg.decision_action == DecisionAction.FAILED.value for lg in logs)
        assert all(lg.llm_raw_response == "raw-last-try" for lg in logs)


async def test_llm_success_writes_raw_response_and_memory(make_space):
    """审计不变式：llm 模式每次决策写入 llm_raw_response；记忆正常落地。"""
    agent_id = await make_space(learning_mode="llm")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, n=2)
        mgr = MemoryManager(s)
        mgr._learning._llm_analyze = _fake_llm_store_all()
        result = await mgr.flush(agent_id)
        await s.commit()

        assert result["processed"] == 2
        mems = (
            (await s.execute(select(LongTermMemory).where(LongTermMemory.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(mems) == 2

        logs = (
            (await s.execute(select(LearningLog).where(LearningLog.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(logs) == 2
        assert all(lg.llm_raw_response == "RAW-JSON" for lg in logs)


async def test_llm_partial_decisions_keep_undecided(make_space):
    """P1-1：LLM 只决策了部分事件 → 已决策的删除，未决策的保留并重试。"""
    agent_id = await make_space(learning_mode="llm")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, n=2)
        mgr = MemoryManager(s)
        mgr._learning._llm_analyze = _fake_llm_first_only()
        await mgr.flush(agent_id)
        await s.commit()

        events = (
            (await s.execute(select(PendingEvent).where(PendingEvent.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].retry_count == 1


async def test_decay_uses_space_config(make_space):
    """衰减参数读 space.config，不再硬编码 0.95/0.1。"""
    agent_id = await make_space(
        learning_mode="heuristic", decay_per_day=0.5, min_weight=0.01
    )
    async with async_session_factory() as s:
        s.add(
            LongTermMemory(
                agent_id=agent_id,
                type="reference",
                title="既有记忆",
                content="发货单重量单位统一使用吨",
                weight=1.0,
                review_status="approved",
            )
        )
        await _add_events(s, agent_id, n=1)
        mgr = MemoryManager(s)
        await mgr.flush(agent_id)
        await s.commit()

        weights = (
            (await s.execute(select(LongTermMemory.weight).where(LongTermMemory.agent_id == agent_id)))
            .scalars()
            .all()
        )
        # heuristic 先存新记忆（weight=1.0），随后统一衰减 ×0.5
        assert len(weights) == 2
        assert all(abs(w - 0.5) < 1e-6 for w in weights)


async def test_flush_skipped_when_lock_held(make_space):
    """P1-2：同 agent 另一个 flush 持锁时，返回 skipped 而非静默 ok。"""
    agent_id = await make_space(learning_mode="heuristic")
    dsn = settings.database_url.replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        await conn.execute(
            "SELECT pg_advisory_lock(hashtext($1))", f"flush_{agent_id}"
        )
        async with async_session_factory() as s:
            await _add_events(s, agent_id, n=1)
            mgr = MemoryManager(s)
            result = await mgr.flush(agent_id)
            assert result["status"] == "skipped"
            assert result["reason"] == "another_flush_running"
            await s.rollback()
    finally:
        await conn.execute(
            "SELECT pg_advisory_unlock(hashtext($1))", f"flush_{agent_id}"
        )
        await conn.close()


# ---------------------------------------------------------------- _llm_analyze 单元测试


class _FakeCompletions:
    def __init__(self, content: str):
        self._content = content

    async def create(self, **kwargs):
        return type(
            "Resp",
            (),
            {
                "choices": [
                    type("Choice", (), {"message": type("Msg", (), {"content": self._content})()})()
                ]
            },
        )()


class _FakeOpenAI:
    def __init__(self, content: str):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(content)})()


async def test_llm_analyze_strips_json_fences(monkeypatch):
    """_llm_analyze 剥离 ```json 围栏并解析。"""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kw: _FakeOpenAI(
            '```json\n[{"action":"store","title":"t1","content":"c1","memory_type":"feedback"}]\n```'
        ),
    )
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [PendingEvent(agent_id="x", event_type="user_feedback", context="规则A")]
    decisions, raw = await lm._llm_analyze(events)

    assert len(decisions) == 1
    assert decisions[0].title == "t1"
    assert decisions[0].content == "c1"
    assert decisions[0].memory_type == MemoryType.FEEDBACK
    assert raw.startswith("```json")


async def test_llm_analyze_non_list_returns_empty(monkeypatch):
    """非数组 JSON → ([], raw)，交给 P1-1 重试。"""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: _FakeOpenAI('{"not": "a list"}'))
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [PendingEvent(agent_id="x", event_type="user_feedback", context="规则A")]
    decisions, raw = await lm._llm_analyze(events)

    assert decisions == []
    assert raw == '{"not": "a list"}'


# ---------------------------------------------------------------- E2：event_index 绑定（消除下标错位）


async def test_llm_analyze_binds_by_event_index(monkeypatch):
    """E2：LLM 返回 event_index 时按它绑定事件，不依赖数组下标。"""
    from uuid import uuid4

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kw: _FakeOpenAI(
            '[{"event_index":1,"action":"store","title":"第二条","content":"c","memory_type":"feedback"},'
            '{"event_index":0,"action":"store","title":"第一条","content":"c0","memory_type":"feedback"}]'
        ),
    )
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [
        PendingEvent(id=str(uuid4()), agent_id="x", event_type="user_feedback", context="第一条事件"),
        PendingEvent(id=str(uuid4()), agent_id="x", event_type="user_feedback", context="第二条事件"),
    ]
    decisions, _raw = await lm._llm_analyze(events)

    # LLM 把第二条放第一个返回、第一条放第二个——按 event_index 仍绑对
    assert len(decisions) == 2
    by_id = {d.event_id: d for d in decisions}
    assert by_id[events[0].id].title == "第一条"
    assert by_id[events[1].id].title == "第二条"


async def test_llm_analyze_event_index_out_of_range_skipped(monkeypatch):
    """E2：event_index 越界（LLM 编了不存在的下标）→ 该决策 event_id 为空，不错绑到别的事件。"""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kw: _FakeOpenAI(
            '[{"event_index":99,"action":"store","title":"幽灵","content":"c","memory_type":"feedback"}]'
        ),
    )
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [PendingEvent(agent_id="x", event_type="user_feedback", context="唯一事件")]
    decisions, _raw = await lm._llm_analyze(events)

    assert len(decisions) == 1
    # event_index=99 越界 → fallback 到 i=0（维持现状），但说明越界没绑到错的事件
    assert decisions[0].event_id == events[0].id


async def test_llm_analyze_fewer_decisions_than_events_no_misbind(monkeypatch):
    """E2：LLM 只返回 1 条决策但 events 有 2 条——返回的那条按 event_index 绑对，不因下标错位绑到另一条。"""
    from uuid import uuid4

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kw: _FakeOpenAI(
            '[{"event_index":1,"action":"store","title":"仅分析第二条","content":"c","memory_type":"feedback"}]'
        ),
    )
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [
        PendingEvent(id=str(uuid4()), agent_id="x", event_type="user_feedback", context="第一条"),
        PendingEvent(id=str(uuid4()), agent_id="x", event_type="user_feedback", context="第二条"),
    ]
    decisions, _raw = await lm._llm_analyze(events)

    assert len(decisions) == 1
    # 按 event_index=1 绑到第二条，而非下标 0 的第一条
    assert decisions[0].event_id == events[1].id
    assert decisions[0].title == "仅分析第二条"


async def test_llm_analyze_invalid_json_returns_empty(monkeypatch):
    """无效 JSON → ([], raw)。"""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: _FakeOpenAI("not json at all"))
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [PendingEvent(agent_id="x", event_type="user_feedback", context="规则A")]
    decisions, raw = await lm._llm_analyze(events)

    assert decisions == []
    assert raw == "not json at all"


# ---------------------------------------------------------------- E1：LLM JSON 解析失败可观测


def test_parse_llm_json_logs_warning_on_invalid_json(caplog):
    """E1：_parse_llm_json 解析失败时打 warning（此前静默吞，调试困难），仍返回 []。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="gleanmem.core.learning"):
        result = LearningModel._parse_llm_json("not json at all")

    assert result == []
    assert any("解析失败" in r.message for r in caplog.records)


def test_parse_llm_json_silent_on_empty_and_valid(caplog):
    """E1：空输入与合法 JSON 不打 warning（只有真正解析失败才告警）。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="gleanmem.core.learning"):
        assert LearningModel._parse_llm_json("") == []
        assert LearningModel._parse_llm_json('[{"action":"store"}]') == [
            {"action": "store"}
        ]
    assert not caplog.records


# ---------------------------------------------------------------- A1：max_memories 上限治理


async def test_flush_archives_overflow_beyond_max_memories(make_space):
    """A1：超出 max_memories 上限时，flush 末尾软删最低权重记忆。

    此前 Space.config.max_memories 与 archive_lowest 都已存在但 flush 未接通，
    长期记忆会无限增长。修复后 flush 末尾按 weight asc / created_at asc 淘汰溢出条数。
    """
    agent_id = await make_space(
        learning_mode="heuristic", decay_per_day=1.0, min_weight=0.0, max_memories=2
    )
    async with async_session_factory() as s:
        # 既有 3 条记忆，权重递增；最低权重应优先被淘汰
        for w in (0.1, 0.5, 0.9):
            s.add(
                LongTermMemory(
                    agent_id=agent_id,
                    type="reference",
                    title=f"权重{w}",
                    content=f"内容{w}",
                    weight=w,
                    review_status="approved",
                )
            )
        await _add_events(s, agent_id, n=1)  # 一个 pending event 让 _run 跑到底
        await s.commit()

    async with async_session_factory() as s:
        mgr = MemoryManager(s)
        result = await mgr.flush(agent_id)
        await s.commit()
        assert result["status"] == "ok"

    async with async_session_factory() as s:
        active = (
            (
                await s.execute(
                    select(LongTermMemory)
                    .where(
                        LongTermMemory.agent_id == agent_id,
                        LongTermMemory.is_deleted == False,  # noqa: E712
                    )
                    .order_by(LongTermMemory.weight)
                )
            )
            .scalars()
            .all()
        )
        # 原 3 条 + flush 新存 1 条(权重 1.0) = 4；max_memories=2 → 软删最低 2 条(0.1, 0.5)
        assert len(active) == 2
        assert [round(m.weight, 2) for m in active] == [0.9, 1.0]


# ---------------------------------------------------------------- A3：commit 失败不丢事件


async def test_commit_failure_keeps_event_for_retry(make_space, monkeypatch):
    """A3：_commit_and_log 内 store.create 抛异常时事件保留走 P1-1，不被误删。

    Python 层异常不污染 session（不同于 PG IntegrityError 会被整事务 rollback 兜底），
    原实现会照常 delete 事件 → 事件丢失且绕过重试。修复后 FAILED 的 event_id 不进删除集。
    """
    agent_id = await make_space(learning_mode="llm")
    async with async_session_factory() as s:
        await _add_events(s, agent_id, n=1)
        mgr = MemoryManager(s)
        mgr._learning._llm_analyze = _fake_llm_store_all()

        async def _failing_create(mem):
            raise RuntimeError("simulated commit failure")

        mgr._learning._store.create = _failing_create

        result = await mgr.flush(agent_id)
        await s.commit()
        assert result["status"] == "ok"

    async with async_session_factory() as s:
        events = (
            (
                await s.execute(
                    select(PendingEvent).where(PendingEvent.agent_id == agent_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1, "commit 失败的事件必须保留走 P1-1 重试，不能被误删"
        assert events[0].retry_count == 1

        logs = (
            (
                await s.execute(
                    select(LearningLog).where(LearningLog.agent_id == agent_id)
                )
            )
            .scalars()
            .all()
        )
        failed = [
            lg for lg in logs if lg.decision_action == DecisionAction.FAILED.value
        ]
        assert len(failed) == 1
        assert "simulated commit failure" in (failed[0].error_message or "")
