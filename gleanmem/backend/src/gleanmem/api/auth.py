"""身份探测端点：登录后前端据此区分 admin（平台管理台）与 space（空间业务视图）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .deps import resolve_identity

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me")
async def me(identity: tuple[str, str | None] = Depends(resolve_identity)):
    role, agent_id = identity
    return {"role": role, "agent_id": agent_id}
