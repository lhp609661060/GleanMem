from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gleanmem.core.database import get_db
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models.learning_log import LearningLog

from .deps import require_agent

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
