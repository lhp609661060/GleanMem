from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.core.database import get_db
from yd_memory_service.core.manager import MemoryManager
from yd_memory_service.core.models.agent_space import AgentSpace

router = APIRouter(prefix="/api/v1/spaces", tags=["spaces"])


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
async def list_spaces(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgentSpace).order_by(AgentSpace.created_at.desc()))
    return result.scalars().all()


@router.get("/{agent_id}")
async def get_space(agent_id: str, db: AsyncSession = Depends(get_db)):
    mgr = MemoryManager(db)
    space = await mgr.get_space(agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    return space


@router.put("/{agent_id}")
async def update_space(agent_id: str, name: str | None = None, config: dict | None = None, db: AsyncSession = Depends(get_db)):
    space = await db.get(AgentSpace, agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    if name:
        space.name = name
    if config:
        space.config = {**space.config, **config}
    await db.flush()
    return space


@router.delete("/{agent_id}")
async def archive_space(agent_id: str, db: AsyncSession = Depends(get_db)):
    space = await db.get(AgentSpace, agent_id)
    if not space:
        raise HTTPException(404, "Space not found")
    space.status = "archived"
    await db.flush()
    return {"status": "archived"}
