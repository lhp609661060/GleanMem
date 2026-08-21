"""REST 身份解析：Authorization: Bearer <key> → (role, agent_id)。

身份不变式：agent_id 只出现在接入层，解析后注入核心；
MCP SSE 走 X-Agent-ID header（Dify），REST 走 per-space API Key 或平台 admin key。
"""
from __future__ import annotations

import hashlib

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.config import settings
from yd_memory_service.core.database import get_db
from yd_memory_service.core.models.agent_space import AgentSpace


def _hash_key(space_key: str) -> str:
    return hashlib.sha256(space_key.encode()).hexdigest()


async def resolve_identity(
    request: Request, db: AsyncSession = Depends(get_db)
) -> tuple[str, str | None]:
    """解析 Bearer key → (role, agent_id)。role ∈ {admin, space}。

    - admin：平台管理台用 YDM_ADMIN_KEY，可管理所有 Space；
    - space：per-space key，解析到该 Space 的 agent_id（归档后失效）。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization: Bearer <key>")
    key = auth.removeprefix("Bearer ").strip()
    if not key:
        raise HTTPException(status_code=401, detail="key 为空")

    if settings.admin_key and key == settings.admin_key:
        return "admin", None

    result = await db.execute(
        select(AgentSpace.agent_id).where(
            AgentSpace.api_key_hash == _hash_key(key),
            # 归档 = 停用：归档后原 key 立即失效（不能再读写该 Space）
            AgentSpace.status != "archived",
        )
    )
    agent_id = result.scalar_one_or_none()
    if agent_id:
        return "space", agent_id

    raise HTTPException(status_code=401, detail="key 无效")


async def require_agent(
    identity: tuple[str, str | None] = Depends(resolve_identity),
) -> str:
    """要求 space 身份，返回 agent_id。"""
    role, agent_id = identity
    if role != "space" or not agent_id:
        raise HTTPException(status_code=401, detail="需要 space key")
    return agent_id


async def require_admin(
    identity: tuple[str, str | None] = Depends(resolve_identity),
) -> str:
    """要求 admin 身份（平台管理台）。"""
    role, _ = identity
    if role != "admin":
        raise HTTPException(status_code=401, detail="需要 admin key")
    return "admin"
