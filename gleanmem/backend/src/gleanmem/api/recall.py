from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from gleanmem.core.metrics import timed_recall
from gleanmem.orchestrator.recall import recall as orchestrator_recall

from .deps import require_agent

router = APIRouter(prefix="/api/v1", tags=["recall"])


class RecallRequest(BaseModel):
    intent: str


@router.post("/recall")
async def recall_endpoint(
    body: RecallRequest,
    agent_id: str = Depends(require_agent),
):
    """HTTP 集成用，与 MCP recall 工具等价；agent_id 取自 key。

    V2：recall 内部起独立 session 并发三路，端点不再持有 db 依赖。
    """
    with timed_recall():
        result = await orchestrator_recall(body.intent, agent_id)
    return result.to_dict()
