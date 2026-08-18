"""共享 fixture：测试 Space 创建与清理。

测试跑在本地 PG（docker compose 起的 ydm 库），每个用例独立 agent_id，
结束后清理该 Space 的全部数据，避免污染开发库。
"""
from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import delete

from yd_memory_service.core.database import async_session_factory
from yd_memory_service.core.models import (
    AgentSpace,
    CodebaseRun,
    LearningLog,
    LongTermMemory,
    PendingEvent,
    WikiDocument,
)
from yd_memory_service.main import app

_CLEANUP_MODELS = (WikiDocument, LearningLog, LongTermMemory, PendingEvent, CodebaseRun)


async def _purge_space(agent_id: str) -> None:
    async with async_session_factory() as s:
        for model in _CLEANUP_MODELS:
            await s.execute(delete(model).where(model.agent_id == agent_id))
        await s.execute(delete(AgentSpace).where(AgentSpace.agent_id == agent_id))
        await s.commit()


@pytest.fixture
async def make_space():
    """创建带自定义 config 的测试 Space，返回 agent_id；用例结束后清理其全部数据。"""
    created: list[str] = []

    async def _make(**config_overrides: object) -> str:
        config: dict = {
            "learning_mode": "heuristic",
            "decay_per_day": 0.95,
            "min_weight": 0.1,
            "max_memories": 5000,
        }
        config.update(config_overrides)
        async with async_session_factory() as s:
            space = AgentSpace(name=f"test-{uuid4().hex[:8]}", config=config)
            s.add(space)
            await s.flush()
            await s.commit()
            created.append(space.agent_id)
            return space.agent_id

    yield _make

    for agent_id in created:
        await _purge_space(agent_id)


@pytest.fixture
async def space_client():
    """经 API 创建 Space（返回 space_key 与 agent_id），可选覆盖 config；结束自动清理。"""
    created: list[str] = []

    async def _make(config_overrides: dict | None = None) -> tuple[str, str]:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/v1/spaces", json={"name": f"t-{uuid4().hex[:8]}"}
            )
            assert r.status_code == 200, r.text
            key = r.json()["space_key"]
            agent_id = r.json()["agent_id"]
        created.append(agent_id)
        if config_overrides:
            async with async_session_factory() as s:
                space = await s.get(AgentSpace, agent_id)
                space.config = {**space.config, **config_overrides}
                await s.commit()
        return key, agent_id

    yield _make

    for agent_id in created:
        await _purge_space(agent_id)
