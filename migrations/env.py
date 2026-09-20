import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from urbanflow.settings import Settings
from urbanflow.storage.schema import metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(Settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


asyncio.run(online())
