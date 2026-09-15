"""V1.5b：codebase 增量——source 分派、指纹 diff、单模块重生成、protected、审计。

覆盖的不变式：
- source=codebase 事件绝不能落进 chat 分支（分派白名单，V1.5b 修复的 bug）
- 指纹无变化 → 不调 LLM（省 token）
- protected 卡在增量路径同样不被覆盖
- failed 事件保留并累加 retry_count（P1-1 复用），成功事件才消费
- 每个事件写 learning_logs（决策级）+ run 写 codebase_runs（run 级），审计不断
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from gleanmem.core.codebase.incremental import (
    IncrementalDistiller,
    fingerprints_changed,
)
from gleanmem.core.codebase.store import CodebaseStore, card_id
from gleanmem.core.database import async_session_factory
from gleanmem.core.learning import LearningModel
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models import (
    CodebaseRun,
    LearningLog,
    PendingEvent,
    WikiDocument,
)


def _change_context(module: str, files: list[dict], *, commit="c0ffee1", change="modified") -> str:
    return json.dumps(
        {"module": module, "change": change, "commit_sha": commit, "files": files},
        ensure_ascii=False,
    )


async def _add_event(s, agent_id: str, module: str, files: list[dict], **kw) -> PendingEvent:
    event = PendingEvent(
        agent_id=agent_id,
        source="codebase",
        event_type="module_changed",
        context=_change_context(module, files, **kw),
        dedup_key=f"cb:{kw.get('commit', 'c0ffee1')}:{module}",
        extra_meta={"module": module, "commit_sha": kw.get("commit", "c0ffee1")},
    )
    s.add(event)
    await s.flush()
    return event


# -- 指纹 diff --------------------------------------------------------------

def test_fingerprints_changed_detects_all_cases():
    card = type("Doc", (), {"extra_meta": {"fingerprints": {"a.py": "sha256:1"}}})()
    # 相同指纹 → 无变化
    assert fingerprints_changed(card, [{"path": "a.py", "fingerprint": "sha256:1"}]) is False
    # 内容变了
    assert fingerprints_changed(card, [{"path": "a.py", "fingerprint": "sha256:2"}]) is True
    # 新增文件
    assert fingerprints_changed(
        card, [{"path": "a.py", "fingerprint": "sha256:1"}, {"path": "b.py", "fingerprint": "sha256:3"}]
    ) is True
    # 文件被删
    assert fingerprints_changed(card, []) is True
    # 卡不存在 → 需生成
    assert fingerprints_changed(None, [{"path": "a.py", "fingerprint": "sha256:1"}]) is True


async def test_unchanged_fingerprint_skips_llm(make_space):
    """指纹无变化 → skipped_unchanged，绝不调 LLM（省 token）。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        s.add(
            WikiDocument(
                id=card_id(agent_id, "core/a"),
                agent_id=agent_id,
                title="core/a",
                description="模块 a 的职责说明",
                content="旧内容",
                extra_meta={
                    "source": "codebase",
                    "fingerprints": {"core/a/x.py": "sha256:same"},
                },
            )
        )
        event = await _add_event(
            s, agent_id, "core/a",
            [{"path": "core/a/x.py", "fingerprint": "sha256:same", "snippet": "code"}],
        )
        await s.commit()

    async with async_session_factory() as s:
        outcomes = await IncrementalDistiller(s).process(agent_id, [event])
        assert [o.action for o in outcomes] == ["skipped_unchanged"]
        assert outcomes[0].tokens_used == 0  # 没花钱
        assert outcomes[0].consumed is True


async def test_protected_card_survives_incremental(make_space):
    """增量路径同样不覆盖人的修订。"""
    agent_id = await make_space()
    cid = card_id(agent_id, "core/b")
    async with async_session_factory() as s:
        s.add(
            WikiDocument(
                id=cid, agent_id=agent_id, title="人写的标题",
                description="人写的描述", content="人工修订内容",
                extra_meta={"source": "codebase", "protected": True,
                            "fingerprints": {"core/b/y.py": "sha256:old"}},
            )
        )
        event = await _add_event(
            s, agent_id, "core/b",
            [{"path": "core/b/y.py", "fingerprint": "sha256:NEW", "snippet": "new code"}],
        )
        await s.commit()

    async with async_session_factory() as s:
        outcomes = await IncrementalDistiller(s).process(agent_id, [event])
        assert [o.action for o in outcomes] == ["skipped_protected"]
        await s.commit()

    async with async_session_factory() as s:
        doc = await s.get(WikiDocument, cid)
        assert doc.content == "人工修订内容"


async def test_deleted_module_soft_deletes_card(make_space):
    agent_id = await make_space()
    cid = card_id(agent_id, "core/gone")
    async with async_session_factory() as s:
        s.add(
            WikiDocument(
                id=cid, agent_id=agent_id, title="将被删除", description="描述",
                content="内容", extra_meta={"source": "codebase"},
            )
        )
        event = await _add_event(s, agent_id, "core/gone", [], change="deleted")
        await s.commit()

    async with async_session_factory() as s:
        outcomes = await IncrementalDistiller(s).process(agent_id, [event])
        assert [o.action for o in outcomes] == ["deleted"]
        await s.commit()

    async with async_session_factory() as s:
        doc = await s.get(WikiDocument, cid)
        assert doc.is_deleted is True
        assert doc.extra_meta["deleted_by_commit"] == "c0ffee1"


async def test_missing_snippet_fails_and_retries(make_space):
    """没有 snippet 无法在服务端重生成 → failed，事件保留给 P1-1 重试。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        event = await _add_event(
            s, agent_id, "core/c", [{"path": "core/c/z.py", "fingerprint": "sha256:new"}]
        )
        await s.commit()

    async with async_session_factory() as s:
        outcomes = await IncrementalDistiller(s).process(agent_id, [event])
        assert [o.action for o in outcomes] == ["failed"]
        assert outcomes[0].consumed is False
        assert "snippet" in outcomes[0].error


# -- flush 分派（V1.5b 修的 bug）--------------------------------------------

async def test_codebase_events_never_enter_chat_branch(make_space):
    """source=codebase 必须走 codebase 分支。

    修复前 chat_events 是 `source != "observation"`，codebase 事件会被当聊天素材
    学成 long_term_memory —— 产物类型完全错误。
    """
    agent_id = await make_space(learning_mode="heuristic", decay_per_day=1.0, min_weight=1.0)
    async with async_session_factory() as s:
        await _add_event(
            s, agent_id, "core/d", [{"path": "core/d/a.py", "fingerprint": "sha256:n"}]
        )
        await s.commit()

    async with async_session_factory() as s:
        mgr = MemoryManager(s)
        result = await mgr.flush(agent_id)
        await s.commit()
    assert result["status"] == "ok"

    async with async_session_factory() as s:
        from gleanmem.core.models import LongTermMemory

        mems = (
            await s.execute(
                select(LongTermMemory).where(LongTermMemory.agent_id == agent_id)
            )
        ).scalars().all()
        # 关键断言：codebase 事件不得产出 long_term_memory
        assert mems == []

        logs = (
            await s.execute(
                select(LearningLog).where(
                    LearningLog.agent_id == agent_id, LearningLog.source == "codebase"
                )
            )
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].decision_target == "wiki"


async def test_flush_writes_both_decision_and_run_audit(make_space):
    """增量同时写 learning_logs（决策级）与 codebase_runs（run 级），审计链不断。"""
    agent_id = await make_space(learning_mode="heuristic", decay_per_day=1.0, min_weight=1.0)
    async with async_session_factory() as s:
        # 一条 unchanged（消费）+ 一条 protected（消费）
        s.add(
            WikiDocument(
                id=card_id(agent_id, "m1"), agent_id=agent_id, title="m1",
                description="m1 描述", content="c",
                extra_meta={"source": "codebase", "fingerprints": {"m1/a.py": "sha256:s"}},
            )
        )
        s.add(
            WikiDocument(
                id=card_id(agent_id, "m2"), agent_id=agent_id, title="m2",
                description="m2 描述", content="人工内容",
                extra_meta={"source": "codebase", "protected": True},
            )
        )
        await _add_event(s, agent_id, "m1", [{"path": "m1/a.py", "fingerprint": "sha256:s"}])
        await _add_event(s, agent_id, "m2", [{"path": "m2/b.py", "fingerprint": "sha256:x", "snippet": "code"}])
        await s.commit()

    async with async_session_factory() as s:
        mgr = MemoryManager(s)
        await mgr.flush(agent_id)
        await s.commit()

    async with async_session_factory() as s:
        logs = (
            await s.execute(
                select(LearningLog).where(
                    LearningLog.agent_id == agent_id, LearningLog.source == "codebase"
                )
            )
        ).scalars().all()
        assert len(logs) == 2
        assert {lg.event_type for lg in logs} == {
            "module_changed:skipped_unchanged", "module_changed:skipped_protected"
        }

        runs = (
            await s.execute(
                select(CodebaseRun).where(CodebaseRun.agent_id == agent_id)
            )
        ).scalars().all()
        assert len(runs) == 1
        assert runs[0].mode == "incremental"
        assert runs[0].commit_sha == "c0ffee1"
        assert runs[0].cards_skipped_protected == 1
        assert runs[0].finished_at is not None

        # 消费掉的事件已出收件箱
        left = (
            await s.execute(
                select(PendingEvent).where(
                    PendingEvent.agent_id == agent_id, PendingEvent.source == "codebase"
                )
            )
        ).scalars().all()
        assert left == []


async def test_failed_event_stays_with_retry_count(make_space):
    """无 snippet → failed → 事件保留、retry_count 递增（P1-1 复用）。"""
    agent_id = await make_space(learning_mode="heuristic", decay_per_day=1.0, min_weight=1.0)
    async with async_session_factory() as s:
        await _add_event(
            s, agent_id, "m3", [{"path": "m3/a.py", "fingerprint": "sha256:new"}]
        )
        await s.commit()

    async with async_session_factory() as s:
        mgr = MemoryManager(s)
        await mgr.flush(agent_id)
        await s.commit()

    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(PendingEvent).where(
                    PendingEvent.agent_id == agent_id, PendingEvent.source == "codebase"
                )
            )
        ).scalars().all()
        assert len(rows) == 1  # 未被静默丢弃
        assert rows[0].retry_count == 1


async def test_mixed_sources_dispatch_independently(make_space):
    """chat + observation + codebase 混在一批：各走各的分析器，互不干扰。"""
    agent_id = await make_space(learning_mode="heuristic", decay_per_day=1.0, min_weight=1.0)
    async with async_session_factory() as s:
        s.add(
            PendingEvent(
                agent_id=agent_id, source="chat", event_type="agent_mark",
                context="记住：发货单确认后不可修改",
            )
        )
        s.add(
            PendingEvent(
                agent_id=agent_id, source="observation", event_type="entity_created",
                context=json.dumps({"entity": "shipment", "event": "entity_created",
                                    "change": {"to": "待确认"}}, ensure_ascii=False),
                dedup_key="obs:mix-1",
            )
        )
        await _add_event(s, agent_id, "m4", [{"path": "m4/a.py", "fingerprint": "sha256:q"}])
        await s.commit()

    async with async_session_factory() as s:
        mgr = MemoryManager(s)
        result = await mgr.flush(agent_id)
        await s.commit()
    assert result["status"] == "ok"

    async with async_session_factory() as s:
        logs = (
            await s.execute(
                select(LearningLog).where(LearningLog.agent_id == agent_id)
            )
        ).scalars().all()
        sources = {lg.source for lg in logs}
        assert sources == {"chat", "observation", "codebase"}


# -- 重生成成功路径（stub LLM，验证卡真的被更新 + 指纹推进）------------------

async def test_regeneration_updates_card_and_advances_fingerprint(make_space, monkeypatch):
    """指纹变化 + 有 snippet → 卡被重写、指纹推进、wiki_sync_pending 置位。"""
    agent_id = await make_space()
    cid = card_id(agent_id, "core/e")
    async with async_session_factory() as s:
        s.add(
            WikiDocument(
                id=cid, agent_id=agent_id, title="旧标题", description="旧描述",
                content="旧内容",
                extra_meta={
                    "source": "codebase", "run_id": "old-run",
                    "fingerprints": {"core/e/a.py": "sha256:OLD"},
                    "wiki_sync_pending": False,
                },
            )
        )
        event = await _add_event(
            s, agent_id, "core/e",
            [{"path": "core/e/a.py", "fingerprint": "sha256:NEW", "snippet": "def f(): ..."}],
            commit="newsha1",
        )
        await s.commit()

    async def fake_regenerate(self, module, source_text, file_count):
        assert "def f()" in source_text  # 用的是事件里的 snippet，没读磁盘
        return (
            {
                "title": "新标题", "description": "重生成后的模块描述",
                "content": "新内容", "narrative": "新叙述", "tags": ["x"],
            },
            '{"title":"新标题"}',
            777,
        )

    monkeypatch.setattr(IncrementalDistiller, "_regenerate", fake_regenerate)

    async with async_session_factory() as s:
        outcomes = await IncrementalDistiller(s).process(agent_id, [event])
        await s.commit()
    assert [o.action for o in outcomes] == ["updated"]
    assert outcomes[0].tokens_used == 777

    async with async_session_factory() as s:
        doc = await s.get(WikiDocument, cid)
        assert doc.title == "新标题"
        assert doc.content == "新内容"
        # 指纹推进：下次同 commit 再来会被 skipped_unchanged
        assert doc.extra_meta["fingerprints"]["core/e/a.py"] == "sha256:NEW"
        assert doc.extra_meta["commit_sha"] == "newsha1"
        assert doc.extra_meta["narrative"] == "新叙述"
        assert doc.extra_meta["wiki_sync_pending"] is True

        # 幂等性：同一批再跑一次 → 指纹已一致 → 不再花 token
        again = await _add_event(
            s, agent_id, "core/e",
            [{"path": "core/e/a.py", "fingerprint": "sha256:NEW"}], commit="newsha2",
        )
        await s.commit()
        outcomes2 = await IncrementalDistiller(s).process(agent_id, [again])
        assert [o.action for o in outcomes2] == ["skipped_unchanged"]
        assert outcomes2[0].tokens_used == 0
