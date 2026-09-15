"""Week 2：观察分析器——llm 归纳（wiki 产物 / 证据防幻觉）与批绑定语义。"""
from __future__ import annotations

import json

from sqlalchemy import select

from gleanmem.config import settings
from gleanmem.core.database import async_session_factory
from gleanmem.core.learning import LearningModel, MemoryDecision
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models import (
    LearningLog,
    LongTermMemory,
    PendingEvent,
    WikiDocument,
)
from gleanmem.core.types import DecisionAction, MemoryType


async def _add_obs_events(session, agent_id: str):
    events = []
    for i, ev in enumerate(
        [
            ("ship-001-created", "entity_created", {"entity": "shipment", "change": {"to": "待确认"}}),
            ("ship-002-created", "entity_created", {"entity": "shipment", "change": {"to": "待确认"}}),
        ]
    ):
        e = PendingEvent(
            agent_id=agent_id,
            source="observation",
            event_type=ev[1],
            context=json.dumps(ev[2], ensure_ascii=False),
            dedup_key=f"obs:{ev[0]}",
        )
        session.add(e)
        events.append(e)
    await session.flush()
    await session.commit()
    return events


async def test_observation_llm_batch_commit_wiki_and_consume_events(make_space):
    """llm 观察归纳：wiki 产物 + 溯源 + 整批事件消费 + 审计 decision_target=wiki。"""
    agent_id = await make_space(learning_mode="llm")

    async def fake(events):
        return [
            MemoryDecision(
                action=DecisionAction.STORE,
                event_id="",  # 批绑定
                title="发货单状态四段流转",
                content="发货单按待确认→待发货→已发货→已签收四段流转",
                target="wiki",
                description="发货单状态流转规则，共四段",
                evidence=["obs:ship-001-created", "obs:ship-002-created"],
                raw_response="RAW-OBS",
            )
        ], "RAW-OBS"

    async with async_session_factory() as s:
        events = await _add_obs_events(s, agent_id)
        mgr = MemoryManager(s)
        mgr._learning._observation_analyze_llm = fake
        result = await mgr.flush(agent_id)
        await s.commit()

        assert result["status"] == "ok"
        assert result["processed"] == 1

        docs = (
            (await s.execute(select(WikiDocument).where(WikiDocument.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(docs) == 1
        assert docs[0].extra_meta["source"] == "observation"
        assert docs[0].extra_meta["evidence"] == ["obs:ship-001-created", "obs:ship-002-created"]

        remaining = (
            (await s.execute(select(PendingEvent).where(PendingEvent.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert remaining == []

        logs = (
            (await s.execute(select(LearningLog).where(LearningLog.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(logs) == 1
        assert logs[0].decision_target == "wiki"
        assert logs[0].llm_raw_response == "RAW-OBS"


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


async def test_observation_llm_requires_evidence(monkeypatch):
    """防幻觉：无 evidence 的 store 决策被降级为 discard。"""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kw: _FakeOpenAI(
            '[{"action":"store","title":"无引用规则","content":"c","evidence":[]}]'
        ),
    )
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [
        PendingEvent(
            agent_id="x", source="observation", event_type="entity_created",
            context='{"entity":"shipment"}', dedup_key="obs:e1",
        )
    ]
    decisions, raw = await lm._observation_analyze_llm(events)
    assert len(decisions) == 1
    assert decisions[0].action == DecisionAction.DISCARD


async def test_observation_llm_parses_target_and_evidence(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kw: _FakeOpenAI(
            '[{"action":"store","title":"厦门规则","content":"厦门用顺丰",'
            '"target":"memory","memory_type":"feedback","evidence":["obs:e1"]}]'
        ),
    )
    lm = LearningModel(None)  # type: ignore[arg-type]
    events = [
        PendingEvent(
            agent_id="x", source="observation", event_type="rule_observed",
            context='{"entity":"shipping_rule"}', dedup_key="obs:e1",
        )
    ]
    decisions, _ = await lm._observation_analyze_llm(events)
    assert decisions[0].target == "memory"
    assert decisions[0].evidence == ["obs:e1"]
    assert decisions[0].memory_type == MemoryType.FEEDBACK


async def test_observation_heuristic_rule_analyzer(make_space):
    """heuristic 观察分析器：逐事件存 reference 记忆，evidence 固化。"""
    agent_id = await make_space(learning_mode="heuristic")
    async with async_session_factory() as s:
        await _add_obs_events(s, agent_id)
        mgr = MemoryManager(s)
        result = await mgr.flush(agent_id)
        await s.commit()
        assert result["processed"] == 2
        mems = (
            (await s.execute(select(LongTermMemory).where(LongTermMemory.agent_id == agent_id)))
            .scalars()
            .all()
        )
        assert len(mems) == 2
        assert all(m.extra_meta["source"] == "observation" for m in mems)
