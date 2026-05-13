"""三层记忆聚合器 - Redis 热记忆 + PG 事实 + PG 向量"""
import logging
from memory.redis_memory import RedisHotMemory
from memory.store import get_recent_facts
from config import settings

logger = logging.getLogger(__name__)


class MemoryAssembler:
    def __init__(self):
        self.redis = RedisHotMemory(settings.redis_url)

    async def assemble(self, biz_type: str, user_key: str, user_input: str = "") -> str:
        parts = []

        try:
            hot_facts = await self.redis.get_all_facts(biz_type, user_key)
            if hot_facts:
                lines = [f"- [{k}]: {v}" for k, v in list(hot_facts.items())[:5]]
                parts.append("## 用户记忆（近期）\n" + "\n".join(lines))
        except Exception as e:
            logger.warning(f"Redis 记忆读取失败: {e}")

        try:
            pg_facts = await get_recent_facts(biz_type, user_key, days=90, top_k=5)
            if pg_facts:
                lines = [f"- [{f['fact_type']}]: {f['fact_value']} ({f['last_seen_ts'].date()})" for f in pg_facts]
                parts.append("## 用户记忆（长期）\n" + "\n".join(lines))
        except Exception as e:
            logger.warning(f"PG 记忆读取失败: {e}")

        return "\n\n".join(parts)
