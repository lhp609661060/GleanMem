from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gleanmem.config import settings

# pool_recycle：回收空闲连接，规避 PG 端 idle timeout 后的 SSL/EOF 报错；
# pool_pre_ping：借用前先探活，失效连接自动重建，杜绝「连接已断但仍被复用」。
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=20,
    pool_recycle=1800,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
