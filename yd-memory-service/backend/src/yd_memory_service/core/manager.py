"""MemoryManager — the central coordinator for memory operations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yd_memory_service.config import settings

from .learning import LearningModel
from .metrics import timed_flush
from .long_term.pg_store import LongTermStore
from .models.agent_space import AgentSpace
from .models.pending_event import PendingEvent
from .wiki.db_store import WikiStore


class MemoryManager:
    """Façade over LongTermStore + WikiStore + LearningModel.

    This is what api / mcp layers talk to — they never reach into stores directly.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.memories = LongTermStore(session)
        self.wiki = WikiStore(session)
        self._learning = LearningModel(session)

    # -- Spaces ----------------------------------------------------------

    async def create_space(self, name: str, **kwargs) -> tuple[AgentSpace, str]:
        """创建 Space 并生成 space_key（仅本次返回明文，库中只存哈希与前缀）。"""
        import hashlib
        import secrets

        space_key = f"ydm_{secrets.token_urlsafe(32)}"
        space = AgentSpace(
            name=name,
            api_key_hash=hashlib.sha256(space_key.encode()).hexdigest(),
            api_key_prefix=space_key[:8],
            **kwargs,
        )
        self.session.add(space)
        await self.session.flush()
        return space, space_key

    async def rotate_key(self, agent_id: str) -> tuple[AgentSpace, str] | None:
        """轮换 Space 的 space_key：旧 key 立即失效，返回新 key 明文（仅本次）。"""
        import hashlib
        import secrets

        space = await self.session.get(AgentSpace, agent_id)
        if not space:
            return None
        space_key = f"ydm_{secrets.token_urlsafe(32)}"
        space.api_key_hash = hashlib.sha256(space_key.encode()).hexdigest()
        space.api_key_prefix = space_key[:8]
        await self.session.flush()
        return space, space_key

    async def get_space(self, agent_id: str) -> AgentSpace | None:
        return await self.session.get(AgentSpace, agent_id)

    # -- Memorize --------------------------------------------------------

    async def memorize(
        self,
        agent_id: str,
        event_type: str,
        context: str,
        marked_type: str | None = None,
        session_id: str | None = None,
        source: str = "chat",
        dedup_key: str | None = None,
        metadata: dict | None = None,
    ) -> PendingEvent:
        event = PendingEvent(
            agent_id=agent_id,
            source=source,
            event_type=event_type,
            context=context,
            marked_type=marked_type,
            session_id=session_id,
            dedup_key=dedup_key,
            extra_meta=metadata or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    # -- Flush -----------------------------------------------------------

    async def flush(self, agent_id: str, session_id: str | None = None) -> dict:
        space = await self.get_space(agent_id)
        if not space:
            return {"status": "error", "message": f"Space {agent_id} not found"}

        mode = space.config.get("learning_mode", settings.default_learning_mode)
        decay_per_day = float(
            space.config.get("decay_per_day", settings.default_decay_per_day)
        )
        min_weight = float(
            space.config.get("min_weight", settings.default_min_weight)
        )
        max_memories = int(
            space.config.get("max_memories", settings.default_max_memories)
        )
        with timed_flush():
            decisions = await self._learning.run_pipeline(
                agent_id, mode, decay_per_day, min_weight, max_memories
            )
        if decisions is None:
            return {"status": "skipped", "reason": "another_flush_running"}
        return {
            "status": "ok",
            "processed": len(decisions),
            "actions": [d.action.value for d in decisions],
        }
