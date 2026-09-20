from __future__ import annotations

from datetime import datetime

from croniter import croniter
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gleanmem.config import settings
from gleanmem.core.database import get_db
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models.agent_space import AgentSpace
from gleanmem.core.models.learning_log import LearningLog
from gleanmem.orchestrator.scheduler import InvalidSchedule, normalize_cron

from .deps import require_agent, resolve_identity

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])


class EventCreate(BaseModel):
    type: str = "agent_mark"  # user_feedback | agent_mark
    context: str
    marked_type: str | None = None
    source: str = "chat"


@router.post("/events")
async def submit_event(
    body: EventCreate,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """= memorize 的 REST 版：提交学习事件进收件箱。"""
    mgr = MemoryManager(db)
    event = await mgr.memorize(
        agent_id=agent_id,
        event_type=body.type,
        context=body.context,
        marked_type=body.marked_type,
        source=body.source,
    )
    await db.commit()
    return {
        "status": "submitted",
        "event_id": event.id,
        "message": "已提交，flush 后统一分析处理",
    }


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
                    "enabled": (space.config or {}).get("schedule_enabled", True) is not False,
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
                "enabled": (space.config or {}).get("schedule_enabled", True) is not False,
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
