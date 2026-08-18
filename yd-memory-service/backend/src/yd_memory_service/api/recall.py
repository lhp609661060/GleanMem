from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.orchestrator.recall import recall as orchestrator_recall

from .deps import require_agent

router = APIRouter(prefix="/api/v1", tags=["recall"])


class RecallRequest(BaseModel):
    intent: str


@router.post("/recall")
async def recall_endpoint(
    body: RecallRequest,
    agent_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """HTTP 集成用，与 MCP recall 工具等价；agent_id 取自 key。"""
    result = await orchestrator_recall(body.intent, agent_id, db)
    return result.to_dict()
