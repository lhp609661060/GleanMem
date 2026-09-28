"""REST/MCP 身份解析：space_key → agent_id。

身份不变式：agent_id 只出现在接入层，解析后注入核心。
REST 走 Authorization: Bearer <key>；MCP SSE 走 X-Space-Key（同一套哈希查库强度）。
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gleanmem.config import settings
from gleanmem.core.database import get_db
from gleanmem.core.models.agent_space import AgentSpace


def _hash_key(space_key: str) -> str:
    return hashlib.sha256(space_key.encode()).hexdigest()


async def resolve_space_key(space_key: str, db: AsyncSession) -> str | None:
    """space_key → agent_id；无效或已归档（归档 = 停用）返 None。

    REST 与 MCP 共用这一个解析函数——两套接入身份强度必须相同，
    否则弱的那一套会抵消强的那一套（07 评审 R6 的教训）。
    """
    result = await db.execute(
        select(AgentSpace.agent_id).where(
            AgentSpace.api_key_hash == _hash_key(space_key),
            AgentSpace.status != "archived",
        )
    )
    return result.scalar_one_or_none()


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

    if settings.admin_key and hmac.compare_digest(key, settings.admin_key):
        return "admin", None

    agent_id = await resolve_space_key(key, db)
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
    """要求 admin 身份（平台管理台）。

    未配置 YDM_ADMIN_KEY 时返 503 而非 401：这不是「你的 key 不对」，
    而是「平台管理面尚未初始化」——调用方需要的是运维动作，不是换 key。
    """
    if not settings.admin_key:
        raise HTTPException(
            status_code=503, detail="平台未配置 admin key，管理面不可用"
        )
    role, _ = identity
    if role != "admin":
        raise HTTPException(status_code=401, detail="需要 admin key")
    return "admin"
