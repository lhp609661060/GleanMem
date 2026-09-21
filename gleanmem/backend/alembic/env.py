import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config, create_async_engine

from gleanmem.config import settings
from gleanmem.core.models.base import Base

# Import all models so Base.metadata is populated
from gleanmem.core.models import (  # noqa: F401
    AgentSpace,
    LongTermMemory,
    WikiDocument,
    PendingEvent,
    LearningLog,
    CodebaseRun,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 数据库地址唯一来源是 settings（YDM_DATABASE_URL / .env），与运行时同一个事实来源。
# 早先用 alembic.ini 里写死的 sqlalchemy.url：改 .env 换库后，`alembic upgrade head`
# 会把 schema 建到旧库、pytest 在新库上跑，报「表不存在」却看不出原因。
# 不走 set_main_option 是因为它会对 URL 里的 % 做插值转义。
DATABASE_URL = settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
