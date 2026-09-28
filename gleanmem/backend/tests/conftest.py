"""共享 fixture：测试 Space 创建与清理。

测试跑在本地 PG（docker compose 起的 ydm 库），每个用例独立 agent_id，
结束后清理该 Space 的全部数据，避免污染开发库。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete

from gleanmem.core.database import async_session_factory
from gleanmem.core.manager import MemoryManager
from gleanmem.core.models import (
    AgentSpace,
    CodebaseRun,
    LearningLog,
    LongTermMemory,
    PendingEvent,
    WikiDocument,
)

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
    """直写库创建 Space（返回 space_key 与 agent_id），可选覆盖 config；结束自动清理。

    不走 POST /api/v1/spaces：D17 起该端点要求 admin key，而测试 Space 的创建
    不该依赖被测代码的鉴权路径（否则 S2 的用例挂了会连带拖垮全部用 space_client 的用例）。
    """
    created: list[str] = []

    async def _make(config_overrides: dict | None = None) -> tuple[str, str]:
        async with async_session_factory() as s:
            mgr = MemoryManager(s)
            space, key = await mgr.create_space(name=f"t-{uuid4().hex[:8]}")
            if config_overrides:
                space.config = {**space.config, **config_overrides}
            await s.commit()
            created.append(space.agent_id)
            return key, space.agent_id

    yield _make

    for agent_id in created:
        await _purge_space(agent_id)
