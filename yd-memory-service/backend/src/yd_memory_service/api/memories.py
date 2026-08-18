from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.core.models.long_term_memory import LongTermMemory

from .deps import require_agent

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


@router.get("")
async def list_memories(
    status: str | None = None,
    page: int = 1,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """记忆列表（每页 50 条，按权重倒序；status 过滤 review_status）。"""
    conditions = [
        LongTermMemory.agent_id == agent_id,
        LongTermMemory.is_deleted == False,
    ]
    if status:
        conditions.append(LongTermMemory.review_status == status)

    stmt = (
        select(LongTermMemory)
        .where(*conditions)
        .order_by(LongTermMemory.weight.desc(), LongTermMemory.created_at.desc())
        .offset((max(page, 1) - 1) * 50)
        .limit(50)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "title": m.title,
            "type": m.type,
            "weight": round(m.weight, 2),
            "review_status": m.review_status,
            "metadata": m.extra_meta,
            "summary": m.content[:300],
        }
        for m in rows
    ]
