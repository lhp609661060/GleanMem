"""N6：review_status 参与召回过滤（审核纪律不再是装饰性的）。

分级策略（01-design §b 防幻觉纪律）：
- flagged / deprecated：任何类型都不进召回
- pattern（归纳类）：必须 approved 才进召回——LLM 归纳是幻觉高发区
- 其他类型：pending 即可召回（V1 简化，素材是人喂的）

边界：去重比对必须**看得见** pending 记忆，否则同名记忆会重复入库。
"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from yd_memory_service.core.database import async_session_factory
from yd_memory_service.core.long_term.pg_store import LongTermStore
from yd_memory_service.core.models import LongTermMemory
from yd_memory_service.core.manager import MemoryManager
from yd_memory_service.main import app
from yd_memory_service.orchestrator.recall import recall


def _client(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )


async def _add(s, agent_id: str, **kw) -> LongTermMemory:
    defaults = dict(
        agent_id=agent_id, type="reference", title="发货单状态流转规则",
        content="发货单确认后不可修改，需走撤销流程", weight=1.0,
    )
    defaults.update(kw)
    mem = LongTermMemory(**defaults)
    s.add(mem)
    await s.flush()
    return mem


# -- 分级过滤 ---------------------------------------------------------------

async def test_pattern_pending_is_not_recallable(make_space):
    """pattern 默认 pending → 不进召回（防幻觉纪律的兑现）。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        await _add(s, agent_id, type="pattern", title="归纳规则：发货单确认后不可修改",
                   review_status="pending")
        await s.commit()

    async with async_session_factory() as s:
        store = LongTermStore(s)
        assert await store.get_hot(agent_id) == []
        assert await store.search("发货单", agent_id) == []


async def test_pattern_approved_becomes_recallable(make_space):
    agent_id = await make_space()
    async with async_session_factory() as s:
        await _add(s, agent_id, type="pattern", title="归纳规则：发货单确认后不可修改",
                   review_status="approved")
        await s.commit()

    async with async_session_factory() as s:
        store = LongTermStore(s)
        assert len(await store.get_hot(agent_id)) == 1
        assert len(await store.search("发货单", agent_id)) == 1


async def test_non_pattern_pending_is_recallable(make_space):
    """V1 简化：非归纳类 pending 即可召回，否则 V1 全部记忆都召不回。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        await _add(s, agent_id, type="reference", review_status="pending")
        await s.commit()

    async with async_session_factory() as s:
        store = LongTermStore(s)
        assert len(await store.get_hot(agent_id)) == 1
        assert len(await store.search("发货单", agent_id)) == 1


async def test_flagged_and_deprecated_excluded_for_all_types(make_space):
    """人已判定不可用 → 任何类型都不进召回。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        await _add(s, agent_id, type="reference", title="被标记的记忆", review_status="flagged")
        await _add(s, agent_id, type="feedback", title="已废弃的记忆", review_status="deprecated")
        await s.commit()

    async with async_session_factory() as s:
        store = LongTermStore(s)
        assert await store.get_hot(agent_id) == []
        assert await store.search("记忆", agent_id) == []


async def test_include_pending_false_tightens_to_approved_only(make_space):
    """收紧开关：所有类型都必须 approved（管理端/演示用）。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        await _add(s, agent_id, title="pending 的引用", review_status="pending")
        await _add(s, agent_id, title="approved 的引用", review_status="approved")
        await s.commit()

    async with async_session_factory() as s:
        store = LongTermStore(s)
        loose = await store.get_hot(agent_id)
        strict = await store.get_hot(agent_id, include_pending=False)
        assert len(loose) == 2
        assert [m.title for m in strict] == ["approved 的引用"]


async def test_recall_orchestrator_hides_pending_pattern(make_space):
    """端到端：recall 编排器同样看不见未审核的 pattern。"""
    agent_id = await make_space()
    async with async_session_factory() as s:
        await _add(s, agent_id, type="pattern", title="未审核归纳：发货单规则",
                   review_status="pending")
        await _add(s, agent_id, type="reference", title="发货单人工记录",
                   review_status="pending")
        await s.commit()

    async with async_session_factory() as s:
        result = await recall("发货单怎么处理", agent_id, s)
        titles = [m["title"] for m in result.memories]
        assert "发货单人工记录" in titles
        assert "未审核归纳：发货单规则" not in titles


# -- 边界：去重比对不受过滤影响 ---------------------------------------------

async def test_dedupe_still_sees_pending_memories(make_space):
    """关键边界：去重用的 search 必须看见 pending，否则同名记忆重复入库。

    施加审核过滤后若忘记关掉，flush 两次相同素材会产生两条记忆而不是 merge。
    """
    agent_id = await make_space(learning_mode="heuristic", decay_per_day=1.0, min_weight=1.0)

    for _ in range(2):
        async with async_session_factory() as s:
            mgr = MemoryManager(s)
            await mgr.memorize(agent_id=agent_id, event_type="agent_mark",
                               context="发货单确认后不可修改")
            await s.commit()
        async with async_session_factory() as s:
            mgr = MemoryManager(s)
            await mgr.flush(agent_id)
            await s.commit()

    async with async_session_factory() as s:
        store = LongTermStore(s)
        # 两次 flush 同一素材 → 应 merge 成 1 条（pending 状态下也能被去重比对看见）
        all_mems = await store.search(
            "发货单确认后不可修改", agent_id, apply_review_filter=False
        )
        assert len(all_mems) == 1, f"去重失效，产生了 {len(all_mems)} 条重复记忆"


# -- 审核端点 ---------------------------------------------------------------

async def test_review_endpoint_flips_recallability(space_client):
    key, agent_id = await space_client()
    async with async_session_factory() as s:
        mem = await _add(s, agent_id, type="pattern", title="待审归纳规则",
                         review_status="pending")
        mem_id = mem.id
        await s.commit()

    async with _client(key) as client:
        listed = (await client.get("/api/v1/memories")).json()
        assert listed[0]["recallable"] is False  # pattern + pending

        r = await client.post(f"/api/v1/memories/{mem_id}/review", json={"status": "approved"})
        assert r.status_code == 200, r.text
        assert r.json()["recallable"] is True

    async with async_session_factory() as s:
        assert len(await LongTermStore(s).get_hot(agent_id)) == 1

    # flagged → 重新不可召回
    async with _client(key) as client:
        r = await client.post(f"/api/v1/memories/{mem_id}/review", json={"status": "flagged"})
        assert r.json()["recallable"] is False
    async with async_session_factory() as s:
        assert await LongTermStore(s).get_hot(agent_id) == []


async def test_review_rejects_bad_status_and_cross_space(space_client):
    key_a, agent_a = await space_client()
    key_b, _ = await space_client()
    async with async_session_factory() as s:
        mem = await _add(s, agent_a, title="A 的记忆")
        mem_id = mem.id
        await s.commit()

    async with _client(key_a) as client:
        r = await client.post(f"/api/v1/memories/{mem_id}/review", json={"status": "bogus"})
        assert r.status_code == 422

    # 跨 Space 审核必须失败（归属校验）
    async with _client(key_b) as client:
        r = await client.post(f"/api/v1/memories/{mem_id}/review", json={"status": "approved"})
        assert r.status_code == 404
