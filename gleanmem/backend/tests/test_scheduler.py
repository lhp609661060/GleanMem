"""V2 内置调度器：cron 校验、槽位判定、多副本互斥、端到端 tick。

纯函数用例不碰库；DB 用例走本地 PG（conftest 的 make_space 自动清理）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from gleanmem.core.database import async_session_factory
from gleanmem.core.models import AgentSpace
from gleanmem.main import app
from gleanmem.orchestrator import scheduler
from gleanmem.orchestrator.scheduler import (
    InvalidSchedule,
    claim_slot,
    is_due,
    normalize_cron,
    run_due_flushes,
    catch_up_on_startup,
    slot_of,
)

NOW = datetime(2026, 9, 20, 3, 0, 12)  # 周日 03:00


# ------------------------------------------------------------------ 纯函数


def test_normalize_cron_accepts_five_fields():
    assert normalize_cron("0  3 * * *") == "0 3 * * *"


@pytest.mark.parametrize("expr", ["0 3 * *", "0 3 * * * 2026", "not a cron", ""])
def test_normalize_cron_rejects_bad(expr: str):
    with pytest.raises(InvalidSchedule):
        normalize_cron(expr)


def test_is_due_hits_matching_minute():
    assert is_due("0 3 * * *", NOW, None) == "2026-09-20T03:00"


def test_is_due_silent_when_not_matching():
    assert is_due("0 4 * * *", NOW, None) is None


def test_is_due_silent_when_slot_already_fired():
    assert is_due("0 3 * * *", NOW, slot_of(NOW)) is None


def test_is_due_matches_weekday_cron():
    assert is_due("0 3 * * 0", NOW, None) == "2026-09-20T03:00"  # 2026-09-20 是周日
    assert is_due("0 3 * * 1", NOW, None) is None


def test_slot_of_is_minute_granular():
    assert slot_of(NOW) == "2026-09-20T03:00"


@pytest.mark.parametrize(
    "value, enabled",
    [
        (True, True),
        (False, False),
        ("false", False),  # JSONB 可被 curl 写成字符串，"false" 必须真的暂停
        ("0", False),
        ("true", True),
    ],
)
def test_schedule_enabled_normalizes_jsonb_values(value, enabled):
    assert scheduler.schedule_enabled({"schedule_enabled": value}) is enabled
    assert scheduler.schedule_enabled({}) is True, "缺省即启用"
    assert scheduler.schedule_enabled(None) is True


# ------------------------------------------------------------------ 占槽


async def test_claim_slot_first_wins(make_space):
    agent_id = await make_space()
    slot = slot_of(NOW)
    assert await claim_slot(agent_id, slot) is True
    assert await claim_slot(agent_id, slot) is False, "同一槽位不得重复占用"
    assert await claim_slot(agent_id, slot_of(NOW + timedelta(days=1))) is True


# ------------------------------------------------------------------ tick 全链路


async def test_tick_flushes_only_due_enabled_spaces(make_space, monkeypatch):
    due = await make_space(schedule="0 3 * * *")
    paused = await make_space(schedule="0 3 * * *", schedule_enabled=False)
    not_due = await make_space(schedule="0 5 * * *")

    fired: list[str] = []

    async def record(agent_id: str, expr: str) -> dict:
        fired.append(agent_id)
        return {"agent_id": agent_id, "cron": expr, "status": "ok", "processed": 0}

    monkeypatch.setattr(scheduler, "fire_flush", record)

    results = await run_due_flushes(NOW)

    assert fired == [due], "只触发到点且未暂停的 Space"
    assert results[0]["cron"] == "0 3 * * *"
    # 同一分钟再 tick 不重复；下一分钟该 cron 不到点也不触发
    assert await run_due_flushes(NOW) == []
    assert await run_due_flushes(NOW + timedelta(minutes=1)) == []
    assert paused and not_due  # 仅表明它们在扫描范围内


async def test_one_space_failure_does_not_block_others(make_space, monkeypatch):
    """一个 Space 的 flush 抛异常，同批次其它 Space 仍要执行。"""
    bad = await make_space(schedule="0 3 * * *")
    good = await make_space(schedule="0 3 * * *")

    async def flaky(agent_id: str, expr: str) -> dict:
        if agent_id == bad:
            raise RuntimeError("LLM down")
        return {"agent_id": agent_id, "cron": expr, "status": "ok", "processed": 1}

    monkeypatch.setattr(scheduler, "fire_flush", flaky)
    results = await run_due_flushes(datetime(2026, 9, 20, 3, 0))

    by_agent = {r["agent_id"]: r for r in results}
    assert by_agent[bad]["status"] == "error"
    assert by_agent[good]["status"] == "ok", "失败不应中断整批"


async def test_failed_slot_is_not_retried(make_space, monkeypatch):
    """at-most-once 契约：占槽先于执行，flush 失败不回退槽位、后续 tick 不重跑。

    事件留在收件箱由下一批兜住，最坏是延迟；换来 LLM 故障期间无重试风暴。
    把这里"修"成重试属于回归，先读 scheduler 模块 docstring。
    """
    agent_id = await make_space(schedule="0 3 * * *")
    calls: list[str] = []

    async def failing(agent_id: str, expr: str) -> dict:
        calls.append(agent_id)
        raise RuntimeError("LLM down")

    monkeypatch.setattr(scheduler, "fire_flush", failing)
    results = await run_due_flushes(NOW)
    assert results[0]["status"] == "error"
    assert await run_due_flushes(NOW + timedelta(seconds=30)) == []
    assert calls == [agent_id], "失败后不得重跑同一槽位"

    async with async_session_factory() as s:
        space = await s.get(AgentSpace, agent_id)
        assert space.last_fired_slot == slot_of(NOW), "失败后槽位必须保持占用"


async def test_fire_flush_writes_observability_fields(make_space):
    """真跑一次 fire_flush：pending_events 被蒸馏、last_scheduled_flush 落库。"""
    from gleanmem.core.manager import MemoryManager

    agent_id = await make_space(schedule="0 3 * * *")
    async with async_session_factory() as s:
        mgr = MemoryManager(s)
        await mgr.memorize(
            agent_id=agent_id,
            event_type="user_feedback",
            context="调度器测试规则：厦门客户发货统一使用顺丰快递",
            source="chat",
        )
        await s.commit()

    result = await scheduler.fire_flush(agent_id, "0 3 * * *")
    assert result["status"] == "ok", result
    assert result["processed"] >= 1

    async with async_session_factory() as s:
        space = (
            await s.execute(select(AgentSpace).where(AgentSpace.agent_id == agent_id))
        ).scalar_one()
        assert space.last_scheduled_flush is not None, "调度触发必须留痕"


# ------------------------------------------------------------------ 启动补跑


async def test_catch_up_skips_spaces_without_flag(make_space, monkeypatch):
    agent_id = await make_space(schedule="0 3 * * *")
    calls: list[str] = []

    async def record(agent_id: str, expr: str) -> dict:
        calls.append(agent_id)
        return {"agent_id": agent_id, "status": "ok"}

    monkeypatch.setattr(scheduler, "fire_flush", record)
    # 03:00 刚过 5 分钟，若开 catch_up 就会补跑
    assert await catch_up_on_startup(datetime(2026, 9, 20, 3, 5)) == []
    assert calls == []

    async with async_session_factory() as s:
        space = (
            await s.execute(select(AgentSpace).where(AgentSpace.agent_id == agent_id))
        ).scalar_one()
        space.config = {**space.config, "catch_up": True}
        await s.commit()

    assert len(await catch_up_on_startup(datetime(2026, 9, 20, 3, 5))) == 1
    assert calls == [agent_id]
    # 已补跑过，再来一次不重复
    assert await catch_up_on_startup(datetime(2026, 9, 20, 3, 6)) == []


async def test_catch_up_skips_paused_space(make_space, monkeypatch):
    """暂停（schedule_enabled=false）优先于补跑：catch_up=true 也不该在启动时触发。"""
    agent_id = await make_space(
        schedule="0 3 * * *", catch_up=True, schedule_enabled=False
    )

    async def record(agent_id: str, expr: str) -> dict:
        return {"agent_id": agent_id, "status": "ok"}

    monkeypatch.setattr(scheduler, "fire_flush", record)
    assert await catch_up_on_startup(datetime(2026, 9, 20, 3, 5)) == []
    assert agent_id


async def test_catch_up_ignores_slots_outside_window(make_space, monkeypatch):
    await make_space(schedule="0 3 * * *", catch_up=True)

    fired: list[str] = []

    async def record(agent_id: str, expr: str) -> dict:
        fired.append(agent_id)
        return {"agent_id": agent_id, "status": "ok"}

    monkeypatch.setattr(scheduler, "fire_flush", record)
    # 03:00 的槽距今 12.5h，超出默认 720 分钟窗口 → 不补跑
    assert await catch_up_on_startup(datetime(2026, 9, 20, 15, 30)) == []
    assert fired == []


# ------------------------------------------------------------------ API 契约


def _client(key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    )


async def test_put_space_rejects_invalid_cron(space_client):
    key, agent_id = await space_client()
    async with _client(key) as c:
        r = await c.put(
            f"/api/v1/spaces/{agent_id}", json={"config": {"schedule": "每天三点"}}
        )
        assert r.status_code == 422, r.text
        # detail 必须是可直接展示的中文句子（管理台把它当错误提示原文显示）
        assert "cron 需要 5 段" in r.json()["detail"]
        async with async_session_factory() as s:
            space = await s.get(AgentSpace, agent_id)
            assert "schedule" not in space.config, "校验失败不得落库"


async def test_put_space_can_clear_schedule(space_client):
    """空串是同义旧写法（管理台仍用它），语义与 null 一致：真的停掉调度。"""
    key, agent_id = await space_client()
    async with _client(key) as c:
        assert (
            await c.put(
                f"/api/v1/spaces/{agent_id}", json={"config": {"schedule": "0 3 * * *"}}
            )
        ).status_code == 200
        r = await c.put(f"/api/v1/spaces/{agent_id}", json={"config": {"schedule": ""}})
        assert r.status_code == 200, r.text
        assert r.json()["config"]["schedule"] == ""
        assert (await c.get("/api/v1/learning/schedules")).json()["items"] == []


async def test_put_space_null_removes_config_key(space_client):
    """null 删键：取消调度的规范写法，键彻底消失而非留空串哨兵。"""
    key, agent_id = await space_client()
    async with _client(key) as c:
        assert (
            await c.put(
                f"/api/v1/spaces/{agent_id}",
                json={"config": {"schedule": "0 3 * * *", "catch_up": True}},
            )
        ).status_code == 200
        r = await c.put(
            f"/api/v1/spaces/{agent_id}",
            json={"config": {"schedule": None, "catch_up": None}},
        )
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert "schedule" not in cfg and "catch_up" not in cfg, "null 必须删键"
        assert cfg["decay_per_day"] == 0.95, "未传入的键必须保留"
        assert (await c.get("/api/v1/learning/schedules")).json()["items"] == []


async def test_put_space_accepts_cron_and_resets_slot(space_client):
    key, agent_id = await space_client()
    async with async_session_factory() as s:
        space = await s.get(AgentSpace, agent_id)
        space.last_fired_slot = "2026-09-19T03:00"
        await s.commit()

    async with _client(key) as c:
        r = await c.put(
            f"/api/v1/spaces/{agent_id}", json={"config": {"schedule": "30 4 * * *"}}
        )
        assert r.status_code == 200, r.text
        assert r.json()["config"]["schedule"] == "30 4 * * *"

    async with async_session_factory() as s:
        space = await s.get(AgentSpace, agent_id)
        assert space.last_fired_slot is None, "改周期必须清旧槽位，否则新 cron 不生效"


async def test_schedules_endpoint_reports_next_fire(space_client):
    key, agent_id = await space_client({"schedule": "0 3 * * *"})
    async with _client(key) as c:
        body = (await c.get("/api/v1/learning/schedules")).json()
    assert set(body) == {"cron_timezone", "scheduler_enabled", "items"}
    item = body["items"][0]
    assert item["agent_id"] == agent_id
    assert item["valid"] is True and item["enabled"] is True
    assert item["cron"] == "0 3 * * *"
    assert item["next_fire_at"] > datetime.now().isoformat()


async def test_schedules_endpoint_hides_other_spaces(space_client):
    key_a, _ = await space_client()
    _, agent_b = await space_client({"schedule": "0 3 * * *"})
    async with _client(key_a) as c:
        body = (await c.get("/api/v1/learning/schedules")).json()
    assert body["items"] == [], "身份不变式：A 看不到 B 的调度"
    assert agent_b
