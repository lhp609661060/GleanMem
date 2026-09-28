from __future__ import annotations

from datetime import datetime

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gleanmem.config import settings
from gleanmem.core.database import get_db
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models.agent_space import AgentSpace
from gleanmem.core.models.learning_log import LearningLog
from gleanmem.core.models.pending_event import PendingEvent
from gleanmem.orchestrator.scheduler import (
    InvalidSchedule,
    normalize_cron,
    schedule_enabled,
)

from .deps import require_agent, resolve_identity

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])


class EventCreate(BaseModel):
    type: str = "agent_mark"  # user_feedback | agent_mark
    context: str
    marked_type: str | None = None
    source: str = "chat"
    dedup_key: str | None = None


@router.post("/events")
async def submit_event(
    body: EventCreate,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """= memorize 的 REST 版：提交学习事件进收件箱。

    带 dedup_key 时幂等：同 Space 同 key 重复提交（客户端重试）不产生第二条事件，
    返回既有 event_id，与 observation/codebase 的 on_conflict_do_nothing 语义一致。
    """
    mgr = MemoryManager(db)
    try:
        event = await mgr.memorize(
            agent_id=agent_id,
            event_type=body.type,
            context=body.context,
            marked_type=body.marked_type,
            source=body.source,
            dedup_key=body.dedup_key,
        )
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(PendingEvent.id).where(
                    PendingEvent.agent_id == agent_id,
                    PendingEvent.dedup_key == body.dedup_key,
                )
            )
        ).scalar_one()
        return {"status": "duplicate", "event_id": existing, "message": "已存在，未重复提交"}
    await db.commit()
    return {
        "status": "submitted",
        "event_id": event.id,
        "message": "已提交，flush 后统一分析处理",
    }


@router.get("/events")
async def list_events(
    status: str = "dead",
    page: int = 1,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """收件箱事件列表（默认只看死信），每页 50 条倒序。

    死信 = 重试达阈值后标 dead 的事件：数据仍在库、不再进 flush（D19）。
    """
    stmt = (
        select(PendingEvent)
        .where(PendingEvent.agent_id == agent_id, PendingEvent.status == status)
        .order_by(PendingEvent.created_at.desc())
        .offset((max(page, 1) - 1) * 50)
        .limit(50)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "context": e.context,
            "source": e.source,
            "session_id": e.session_id,
            "retry_count": e.retry_count,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]


@router.post("/events/{event_id}/revive")
async def revive_event(
    event_id: str,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """复活死信：status 回 pending、retry_count 归零，下次 flush 重新处理。

    只允许复活本 Space 的 dead 事件——pending 事件返 404（它正在管线里，不需要复活）。
    """
    event = (
        await db.execute(
            select(PendingEvent).where(
                PendingEvent.id == event_id,
                PendingEvent.agent_id == agent_id,
                PendingEvent.status == "dead",
            )
        )
    ).scalar_one_or_none()
    if not event:
        raise HTTPException(404, "死信事件不存在、不属于当前 Space，或不是 dead 状态")
    event.status = "pending"
    event.retry_count = 0
    await db.commit()
    return {"status": "revived", "event_id": event_id}


@router.post("/flush")
async def flush(
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """触发学习管线（Dify workflow 结尾节点 / 外部 cron 共用）。

    agent_id 由 Bearer key 解析，body 不传身份——身份不变式。
    """
    mgr = MemoryManager(db)
    result = await mgr.flush(agent_id)
    await db.commit()
    return result


@router.get("/schedules")
async def list_schedules(
    identity: tuple[str, str | None] = Depends(resolve_identity),
    db: AsyncSession = Depends(get_db),
):
    """调度状态：每个带 cron 的 Space 的表达式、下次触发时间、最近触发结果。

    admin 看全部（平台管理台），space 只看自己。只读，改配置走 PUT /api/v1/spaces/{id}。
    """
    role, agent_id = identity
    stmt = (
        select(AgentSpace)
        .where(AgentSpace.status != "archived")  # 归档 Space 的调度永不触发，列出来只会误导
        .order_by(AgentSpace.created_at)
    )
    if role == "space":
        stmt = stmt.where(AgentSpace.agent_id == agent_id)
    spaces = (await db.execute(stmt)).scalars().all()

    now = datetime.now()
    items = []
    for space in spaces:
        expr = str((space.config or {}).get("schedule", "") or "")
        if not expr:
            continue
        try:
            expr = normalize_cron(expr)
        except InvalidSchedule as exc:
            items.append(
                {
                    "agent_id": space.agent_id,
                    "name": space.name,
                    "cron": expr,
                    "valid": False,
                    "enabled": schedule_enabled(space.config),
                    "error": str(exc),
                    "next_fire_at": None,
                }
            )
            continue
        items.append(
            {
                "agent_id": space.agent_id,
                "name": space.name,
                "cron": expr,
                "valid": True,
                "enabled": schedule_enabled(space.config),
                "next_fire_at": croniter(expr, now).get_next(datetime).isoformat(),
                "last_fired_slot": space.last_fired_slot,
                "last_scheduled_flush": (
                    space.last_scheduled_flush.isoformat()
                    if space.last_scheduled_flush
                    else None
                ),
            }
        )
    return {
        # cron 按服务器本地时区判定，而时间戳统一存 UTC —— 不标出来会差一个时区看不懂。
        # 只给 tzname() 会得到 "CST" 这类歧义缩写，故带上 UTC 偏移。
        "cron_timezone": datetime.now().astimezone().strftime("%Z (%z)"),
        "scheduler_enabled": settings.scheduler_enabled,
        "items": items,
    }


@router.get("/logs")
async def list_logs(
    page: int = 1,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """学习审计日志（每页 50 条，倒序）。"""
    stmt = (
        select(LearningLog)
        .where(LearningLog.agent_id == agent_id)
        .order_by(LearningLog.created_at.desc())
        .offset((max(page, 1) - 1) * 50)
        .limit(50)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": lg.id,
            "source": lg.source,
            "event_type": lg.event_type,
            "event_context": lg.event_context,
            "decision_action": lg.decision_action,
            "decision_target": lg.decision_target,
            "memory_id": lg.memory_id,
            "analyzer_mode": lg.analyzer_mode,
            "llm_raw_response": lg.llm_raw_response,
            "error_message": lg.error_message,
            "created_at": lg.created_at.isoformat() if lg.created_at else None,
        }
        for lg in rows
    ]
