"""Alembic 异步环境配置"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中，兼容 Docker 和本地开发
_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import asyncio
from logging.config import fileConfig
from sqlalchemy.ext.asyncio import create_async_engine
import sqlalchemy as sa
from alembic import context

from db.models import Base
from config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本"""
    url = settings.pg_dsn
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema="callbot",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    # alembic_version 表在 callbot schema 中，迁移前需确保 schema 已存在
    connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS callbot"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version",
        version_table_schema="callbot",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在线模式：异步执行迁移"""
    connectable = create_async_engine(settings.pg_dsn)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
