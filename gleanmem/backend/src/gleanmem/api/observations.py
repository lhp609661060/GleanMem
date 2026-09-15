"""观察入站口（push-first）：业务方推观察事件，幂等进收件箱。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gleanmem.core.database import get_db
from gleanmem.core.models.pending_event import PendingEvent

from .deps import require_agent

router = APIRouter(prefix="/api/v1/observations", tags=["observations"])

PAYLOAD_MAX_BYTES = 4 * 1024  # 护栏：payload 限 4KB，超限拒绝而非截断
BATCH_MAX_ITEMS = 100  # 护栏：批量 push 每批上限，超限拒绝
BATCH_MAX_BYTES = 256 * 1024  # 护栏：批量 payload 总大小上限


class ObservationIn(BaseModel):
    event_id: str  # 幂等键：业务方生成，全局唯一
    entity: str  # 业务对象
    event: str  # 发生了什么（entity_changed 等）
    change: dict | None = None  # 这就是 diff
    payload: dict | None = None  # 可选原始上下文
    occurred_at: str | None = None  # 业务发生时间，非推送时间


def _payload_size(obs: ObservationIn) -> int:
    """单条 payload 序列化后字节数（护栏用，超限拒绝而非截断）。"""
    payload_json = json.dumps(obs.payload, ensure_ascii=False) if obs.payload else "null"
    return len(payload_json.encode("utf-8"))


def _build_observation_stmt(obs: ObservationIn, agent_id: str):
    """构造幂等 upsert 语句（单条与 batch 共用，保证逻辑一致）。"""
    context = json.dumps(
        {
            "entity": obs.entity,
            "event": obs.event,
            "change": obs.change,
            "payload": obs.payload,
            "occurred_at": obs.occurred_at,
        },
        ensure_ascii=False,
    )
    return (
        pg_insert(PendingEvent)
        .values(
            agent_id=agent_id,
            source="observation",
            event_type=obs.event,
            context=context,
            dedup_key=f"obs:{obs.event_id}",
            extra_meta={"origin": obs.entity, "occurred_at": obs.occurred_at},
        )
        .on_conflict_do_nothing(
            index_elements=["agent_id", "dedup_key"],
            index_where=text("dedup_key IS NOT NULL"),
        )
    )


@router.post("")
async def push_observation(
    body: ObservationIn,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """单条 push（V1）。幂等：相同 event_id 重复推返 duplicate 不重复入库。"""
    if _payload_size(body) > PAYLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="payload 超过 4KB 上限")

    result = await db.execute(_build_observation_stmt(body, agent_id))
    await db.commit()
    inserted = result.rowcount > 0
    return {
        "status": "accepted" if inserted else "duplicate",
        "event_id": body.event_id,
    }


@router.post("/batch")
async def push_observations_batch(
    body: list[ObservationIn],
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """批量 push（V2）。回放历史/批量上报 1 次 HTTP，幂等 upsert。

    整批事务：任一条超 payload 上限整批回滚，保证原子性。
    部分重复不视为失败（幂等语义：accepted/duplicate 逐条返回）。
    """
    if not body:
        raise HTTPException(status_code=400, detail="batch 不能为空")
    if len(body) > BATCH_MAX_ITEMS:
        raise HTTPException(
            status_code=413, detail=f"批量超过 {BATCH_MAX_ITEMS} 条上限"
        )

    total = 0
    for obs in body:
        size = _payload_size(obs)
        if size > PAYLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"event {obs.event_id} payload 超过 4KB 上限",
            )
        total += size
    if total > BATCH_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"批量总 payload 超过 {BATCH_MAX_BYTES // 1024}KB 上限",
        )

    items = []
    for obs in body:
        result = await db.execute(_build_observation_stmt(obs, agent_id))
        items.append(
            {
                "event_id": obs.event_id,
                "status": "accepted" if result.rowcount > 0 else "duplicate",
            }
        )
    await db.commit()
    return {"status": "ok", "count": len(items), "items": items}
