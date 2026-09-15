from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.core.models.long_term_memory import LongTermMemory
from yd_memory_service.core.types import MODERATED_TYPES, ReviewStatus

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
            "recallable": m.review_status == ReviewStatus.APPROVED
            or (
                m.type not in MODERATED_TYPES
                and m.review_status == ReviewStatus.PENDING
            ),
            "metadata": m.extra_meta,
            "summary": m.content[:300],
        }
        for m in rows
    ]


class ReviewIn(BaseModel):
    status: str  # approved | flagged | pending


@router.post("/{memory_id}/review")
async def review_memory(
    memory_id: str,
    body: ReviewIn,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """人工审核（N6）：pattern 类记忆必须 approved 才进入召回。

    归属校验按 agent_id，不能审别的 Space 的记忆。
    """
    allowed = {
        ReviewStatus.APPROVED.value,
        ReviewStatus.FLAGGED.value,
        ReviewStatus.PENDING.value,
    }
    if body.status not in allowed:
        raise HTTPException(
            status_code=422, detail=f"status 只能是 {sorted(allowed)}"
        )

    result = await db.execute(
        select(LongTermMemory).where(
            LongTermMemory.id == memory_id,
            LongTermMemory.agent_id == agent_id,
            LongTermMemory.is_deleted == False,
        )
    )
    mem = result.scalar_one_or_none()
    if mem is None:
        raise HTTPException(status_code=404, detail="记忆不存在或不属于当前 Space")

    mem.review_status = body.status
    await db.commit()
    return {
        "id": mem.id,
        "type": mem.type,
        "review_status": mem.review_status,
        "recallable": mem.review_status == ReviewStatus.APPROVED
        or (
            mem.type not in MODERATED_TYPES
            and mem.review_status == ReviewStatus.PENDING
        ),
    }


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """软删记忆（撤销/清理）：标记 is_deleted + review_status=deprecated。

    软删后记忆不再参与召回、不再出现在列表，但行保留以备审计。
    归属校验按 agent_id，不能删别的 Space 的记忆。
    """
    result = await db.execute(
        select(LongTermMemory).where(
            LongTermMemory.id == memory_id,
            LongTermMemory.agent_id == agent_id,
            LongTermMemory.is_deleted == False,  # noqa: E712
        )
    )
    mem = result.scalar_one_or_none()
    if mem is None:
        raise HTTPException(status_code=404, detail="记忆不存在或不属于当前 Space")

    mem.is_deleted = True
    mem.review_status = ReviewStatus.DEPRECATED.value
    await db.commit()
    return {"id": mem.id, "is_deleted": True}
