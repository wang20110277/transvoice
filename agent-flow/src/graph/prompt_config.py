"""提示词加载 — Redis 缓存 → DB 两级降级。

键维度:(tenant_id, biz_type, scenario)。Console(Drizzle)与 agent-flow 共用同一物理表、
同一 Redis key 命名,发布时由 Console 直删缓存实现零延迟生效。
"""
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


def _redis_key(tenant_id: str, biz_type: str, scenario: str) -> str:
    return f"cb:prompt:{tenant_id}:{biz_type}:{scenario}"


async def get_system_prompt(tenant_id: str, biz_type: str, scenario: str = "default") -> str:
    """两级降级加载系统提示词:Redis → DB。键为 (tenant_id, biz_type, scenario)。"""
    key = _redis_key(tenant_id, biz_type, scenario)

    # 1. Redis 缓存
    try:
        rds = _get_redis()
        cached = await rds.get(key)
        if cached:
            logger.info("Prompt cache hit: tenant=%s biz_type=%s scenario=%s", tenant_id, biz_type, scenario)
            return cached
    except Exception as e:
        logger.warning("Redis read failed, fallback to DB: %s", e)

    # 2. 数据库查询
    try:
        async with async_session() as session:
            stmt = select(PromptConfig.system_prompt).where(
                PromptConfig.tenant_id == tenant_id,
                PromptConfig.biz_type == biz_type,
                PromptConfig.scenario == scenario,
                PromptConfig.is_active.is_(True),
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                logger.info(
                    "Prompt loaded from DB: tenant=%s biz_type=%s scenario=%s %d chars",
                    tenant_id, biz_type, scenario, len(row),
                )
                # 回填 Redis
                try:
                    await _get_redis().set(key, row, ex=_PROMPT_TTL)
                except Exception:
                    pass
                return row
    except Exception as e:
        logger.error(
            "DB query failed for prompt: tenant=%s biz_type=%s scenario=%s: %s",
            tenant_id, biz_type, scenario, e,
        )

    logger.warning(
        "No prompt found: tenant=%s biz_type=%s scenario=%s", tenant_id, biz_type, scenario
    )
    return ""


async def invalidate_prompt_cache(tenant_id: str, biz_type: str, scenario: str = "default") -> None:
    """清除提示词缓存(Console 发布/回滚后由 Console 直调,零延迟生效)。"""
    try:
        await _get_redis().delete(_redis_key(tenant_id, biz_type, scenario))
    except Exception:
        pass
