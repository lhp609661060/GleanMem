"""Create tables, triggers, and seed data in one shot."""
import asyncio
from gleanmem.core.database import engine, async_session_factory
from gleanmem.core.models.base import Base
from gleanmem.core.models import AgentSpace, LongTermMemory
from sqlalchemy import text


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "DO $$ BEGIN "
            "CREATE TRIGGER trg_memories_search_vector BEFORE INSERT OR UPDATE ON long_term_memories "
            "FOR EACH ROW EXECUTE FUNCTION memories_search_vector_trigger(); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        ))
        await conn.execute(text(
            "DO $$ BEGIN "
            "CREATE TRIGGER trg_wiki_search_vector BEFORE INSERT OR UPDATE ON wiki_documents "
            "FOR EACH ROW EXECUTE FUNCTION wiki_search_vector_trigger(); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        ))

    async with async_session_factory() as s:
        space = AgentSpace(name="test-agent", description="验证用")
        s.add(space)
        await s.flush()
        aid = space.agent_id

        s.add(LongTermMemory(agent_id=aid, type="reference", title="发货单重量单位", content="发货单重量单位统一使用吨,不使用千克", weight=0.95, review_status="approved"))
        s.add(LongTermMemory(agent_id=aid, type="feedback", title="厦门顺丰规则", content="厦门地区客户发货统一使用顺丰快递", weight=0.90, review_status="approved"))
        s.add(LongTermMemory(agent_id=aid, type="reference", title="异常处理经验", content="发货单确认异常需要双人复核", weight=0.88, review_status="approved"))
        await s.commit()
        print(f"OK agent_id={aid} memories=3")

asyncio.run(main())
