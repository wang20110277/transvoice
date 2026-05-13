"""数据库异步引擎与会话工厂"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from config import settings

engine = create_async_engine(
    settings.pg_dsn,
    pool_size=settings.pg_pool_size,
    max_overflow=settings.pg_max_overflow,
    echo=False,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """获取数据库会话（用于依赖注入）"""
    async with async_session_factory() as session:
        yield session
