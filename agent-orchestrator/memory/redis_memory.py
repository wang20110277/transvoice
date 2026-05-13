"""Redis 热记忆 - 异步客户端"""
import redis.asyncio as aioredis
from datetime import datetime


class RedisHotMemory:
    def __init__(self, url: str):
        self._redis = aioredis.from_url(url, decode_responses=True)

    def _key(self, biz_type: str, user_key: str) -> str:
        yyyymm = datetime.now().strftime("%Y%m")
        return f"cb:mem:hot:{biz_type}:{user_key}:{yyyymm}"

    async def set_fact(self, biz_type: str, user_key: str, field: str, value: str, ttl_days: int = 90):
        key = self._key(biz_type, user_key)
        await self._redis.hset(key, field, value)
        await self._redis.expire(key, ttl_days * 86400)

    async def get_fact(self, biz_type: str, user_key: str, field: str) -> str | None:
        key = self._key(biz_type, user_key)
        return await self._redis.hget(key, field)

    async def get_all_facts(self, biz_type: str, user_key: str) -> dict:
        key = self._key(biz_type, user_key)
        return await self._redis.hgetall(key)

    async def set_do_not_call(self, biz_type: str, user_key: str):
        await self.set_fact(biz_type, user_key, "do_not_call", "true")

    async def is_do_not_call(self, biz_type: str, user_key: str) -> bool:
        return await self.get_fact(biz_type, user_key, "do_not_call") == "true"
