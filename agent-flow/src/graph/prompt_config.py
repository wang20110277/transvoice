"""提示词加载 — Redis 缓存 → DB 两级降级。"""
import logging

import redis.asyncio as aioredis
from sqlalchemy import select

from config import settings
from database import async_session
from db.models import PromptConfig

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None

_PROMPT_TTL = 300  # Redis 缓存 5 分钟


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _redis_key(biz_system: str, biz_type: str) -> str:
    return f"cb:prompt:{biz_system}:{biz_type}"


async def get_system_prompt(biz_type: str, biz_system: str = "default") -> str:
    """两级降级加载系统提示词：Redis → DB。"""
    key = _redis_key(biz_system, biz_type)

    # 1. Redis 缓存
    try:
        rds = _get_redis()
        cached = await rds.get(key)
        if cached:
            logger.info("Prompt cache hit: biz_system=%s biz_type=%s", biz_system, biz_type)
            return cached
    except Exception as e:
        logger.warning("Redis read failed, fallback to DB: %s", e)

    # 2. 数据库查询
    try:
        async with async_session() as session:
            stmt = select(PromptConfig.system_prompt).where(
                PromptConfig.biz_system == biz_system,
                PromptConfig.biz_type == biz_type,
                PromptConfig.is_active.is_(True),
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                logger.info("Prompt loaded from DB: biz_system=%s biz_type=%s %d chars", biz_system, biz_type, len(row))
                # 回填 Redis
                try:
                    await _get_redis().set(key, row, ex=_PROMPT_TTL)
                except Exception:
                    pass
                return row
    except Exception as e:
        logger.error("DB query failed for prompt: biz_system=%s biz_type=%s: %s", biz_system, biz_type, e)

    logger.warning("No prompt found for biz_system=%s biz_type=%s", biz_system, biz_type)
    return ""


async def invalidate_prompt_cache(biz_type: str, biz_system: str = "default") -> None:
    """清除提示词缓存（配置变更后调用）。"""
    try:
        await _get_redis().delete(_redis_key(biz_system, biz_type))
    except Exception:
        pass
