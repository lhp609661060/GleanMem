"""REST 身份解析：Authorization: Bearer <space_key> → agent_id。

身份不变式：agent_id 只出现在接入层，解析后注入核心；
MCP SSE 走 X-Agent-ID header（Dify），REST 走 per-space API Key。
"""
from __future__ import annotations

import hashlib

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.core.models.agent_space import AgentSpace


def _hash_key(space_key: str) -> str:
    return hashlib.sha256(space_key.encode()).hexdigest()


async def require_agent(
    request: Request, db: AsyncSession = Depends(get_db)
) -> str:
    """从 Bearer key 解析 agent_id。无 key / 无效 key → 401。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="缺少 Authorization: Bearer <space_key>"
        )
    space_key = auth.removeprefix("Bearer ").strip()
    if not space_key:
        raise HTTPException(status_code=401, detail="space_key 为空")

    result = await db.execute(
        select(AgentSpace.agent_id).where(
            AgentSpace.api_key_hash == _hash_key(space_key)
        )
    )
    agent_id = result.scalar_one_or_none()
    if not agent_id:
        raise HTTPException(status_code=401, detail="space_key 无效")
    return agent_id
