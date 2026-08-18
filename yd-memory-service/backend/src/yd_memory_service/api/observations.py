"""观察入站口（push-first）：业务方推观察事件，幂等进收件箱。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.core.models.pending_event import PendingEvent

from .deps import require_agent

router = APIRouter(prefix="/api/v1/observations", tags=["observations"])

PAYLOAD_MAX_BYTES = 4 * 1024  # 护栏：payload 限 4KB，超限拒绝而非截断


class ObservationIn(BaseModel):
    event_id: str  # 幂等键：业务方生成，全局唯一
    entity: str  # 业务对象
    event: str  # 发生了什么（entity_changed 等）
    change: dict | None = None  # 这就是 diff
    payload: dict | None = None  # 可选原始上下文
    occurred_at: str | None = None  # 业务发生时间，非推送时间


@router.post("")
async def push_observation(
    body: ObservationIn,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    # 限长护栏（payload 序列化后 ≤ 4KB）
    payload_json = json.dumps(body.payload, ensure_ascii=False) if body.payload else "null"
    if len(payload_json.encode("utf-8")) > PAYLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="payload 超过 4KB 上限")

    context = json.dumps(
        {
            "entity": body.entity,
            "event": body.event,
            "change": body.change,
            "payload": body.payload,
            "occurred_at": body.occurred_at,
        },
        ensure_ascii=False,
    )

    stmt = (
        pg_insert(PendingEvent)
        .values(
            agent_id=agent_id,
            source="observation",
            event_type=body.event,
            context=context,
            dedup_key=f"obs:{body.event_id}",
            extra_meta={"origin": body.entity, "occurred_at": body.occurred_at},
        )
        .on_conflict_do_nothing(
            index_elements=["agent_id", "dedup_key"],
            index_where=text("dedup_key IS NOT NULL"),
        )
    )
    result = await db.execute(stmt)
    await db.commit()
    inserted = result.rowcount > 0
    return {
        "status": "accepted" if inserted else "duplicate",
        "event_id": body.event_id,
    }
