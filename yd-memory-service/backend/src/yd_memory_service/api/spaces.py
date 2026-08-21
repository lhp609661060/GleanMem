from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.core.manager import MemoryManager
from yd_memory_service.core.models.agent_space import AgentSpace

from .deps import require_agent, resolve_identity

router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])


def _space_dict(space: AgentSpace) -> dict:
    """Space 的对外表示。

    **绝不外泄 `api_key_hash`**——它是 space_key 的 SHA-256，泄露后可离线爆破/比对。
    只暴露 `api_key_prefix`（前 8 位，UI 辨认用，设计原意）。
    """
    return {
        "agent_id": space.agent_id,
        "name": space.name,
        "description": space.description,
        "api_key_prefix": space.api_key_prefix,
        "config": space.config,
        "status": space.status,
        "created_at": space.created_at.isoformat() if space.created_at else None,
        "updated_at": space.updated_at.isoformat() if space.updated_at else None,
    }


class SpaceCreate(BaseModel):
    name: str
    description: str | None = None


class SpaceUpdate(BaseModel):
    """更新请求体：只改传入的字段；config 为浅合并（不整体覆盖）。"""

    name: str | None = None
    description: str | None = None
    config: dict | None = None


@router.post("")
async def create_space(body: SpaceCreate, db: AsyncSession = Depends(get_db)):
    mgr = MemoryManager(db)
    space, space_key = await mgr.create_space(
        name=body.name, description=body.description or ""
    )
    await db.commit()
    return {
        "agent_id": space.agent_id,
        "name": space.name,
        "status": space.status,
        # 仅本次返回明文 key，库中只存哈希与前缀
        "space_key": space_key,
        "key_prefix": space.api_key_prefix,
    }


@router.get("")
async def list_spaces(
    identity: tuple[str, str | None] = Depends(resolve_identity),
    db: AsyncSession = Depends(get_db),
):
    """admin 返回全部 Space（平台管理台）；space 只返回自己（身份不变式）。"""
    role, agent_id = identity
    if role == "admin":
        result = await db.execute(select(AgentSpace).order_by(AgentSpace.created_at))
        return [_space_dict(s) for s in result.scalars().all()]
    space = await db.get(AgentSpace, agent_id)
    return [_space_dict(space)] if space else []


@router.get("/{agent_id}")
async def get_space(
    agent_id: str,
    caller_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    if agent_id != caller_id:
        raise HTTPException(404, "Space not found")
    space = await db.get(AgentSpace, agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    return _space_dict(space)


@router.put("/{agent_id}")
async def update_space(
    agent_id: str,
    body: SpaceUpdate,
    identity: tuple[str, str | None] = Depends(resolve_identity),
    db: AsyncSession = Depends(get_db),
):
    role, caller_id = identity
    if role == "space" and agent_id != caller_id:
        raise HTTPException(404, "Space not found")
    space = await db.get(AgentSpace, agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    if body.name is not None:
        space.name = body.name
    if body.description is not None:
        space.description = body.description
    if body.config is not None:
        # 浅合并：传入的键覆盖，未传入的键保留（decay/min_weight/max_memories/learning_mode 各自独立）
        space.config = {**space.config, **body.config}
    await db.commit()
    return _space_dict(space)


@router.post("/{agent_id}/keys")
async def rotate_space_key(
    agent_id: str,
    identity: tuple[str, str | None] = Depends(resolve_identity),
    db: AsyncSession = Depends(get_db),
):
    """轮换 space_key：admin 可轮换任意 Space，space 只能轮换自己。

    返回新 key 明文（仅本次）；旧 key 立即失效。管理台「进入空间」靠它签发 key。
    """
    role, caller_id = identity
    if role == "space" and agent_id != caller_id:
        raise HTTPException(404, "Space not found")

    mgr = MemoryManager(db)
    result = await mgr.rotate_key(agent_id)
    if not result:
        raise HTTPException(404, "Space not found")
    space, space_key = result
    await db.commit()
    return {
        "agent_id": space.agent_id,
        "name": space.name,
        "space_key": space_key,
        "key_prefix": space.api_key_prefix,
    }


@router.delete("/{agent_id}")
async def archive_space(
    agent_id: str,
    identity: tuple[str, str | None] = Depends(resolve_identity),
    db: AsyncSession = Depends(get_db),
):
    """归档：admin 可归档任意 Space，space 只能归档自己。归档后其 key 立即失效。"""
    role, caller_id = identity
    if role == "space" and agent_id != caller_id:
        raise HTTPException(404, "Space not found")
    space = await db.get(AgentSpace, agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    space.status = "archived"
    await db.commit()
    return {"status": "archived"}
