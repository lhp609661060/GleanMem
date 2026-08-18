from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.core.manager import MemoryManager
from yd_memory_service.core.models.agent_space import AgentSpace

from .deps import require_agent

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
    agent_id: str = Depends(require_agent), db: AsyncSession = Depends(get_db)
):
    """只返回当前 key 对应的 Space。

    身份不变式：key 解析出的 agent_id 就是可见范围——不列举他人 Space。
    （修复：原实现无鉴权且返回全部 Space 含 api_key_hash。）
    """
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
    name: str | None = None,
    config: dict | None = None,
    caller_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    if agent_id != caller_id:
        raise HTTPException(404, "Space not found")
    space = await db.get(AgentSpace, agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    if name:
        space.name = name
    if config:
        space.config = {**space.config, **config}
    await db.commit()
    return _space_dict(space)


@router.delete("/{agent_id}")
async def archive_space(
    agent_id: str,
    caller_id: str = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    if agent_id != caller_id:
        raise HTTPException(404, "Space not found")
    space = await db.get(AgentSpace, agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    space.status = "archived"
    await db.commit()
    return {"status": "archived"}
