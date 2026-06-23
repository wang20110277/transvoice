"""Conversation history — plain Redis LIST (无 RediSearch 依赖).

原实现用 langchain_redis.RedisChatMessageHistory,它依赖 RediSearch 模块 (FT.*)。
本地 Redis 未装该模块 (FT._LIST 报 unknown command),故改为直接用 redis.asyncio 的
LIST 存消息 JSON,功能等价、零外部模块依赖。

存储: key = cb:chat:{biz_type}:{call_id}, RPUSH 交替的 human/ai 消息 JSON, TTL 1h。
"""
import json
import logging

import redis.asyncio as aioredis
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "cb:chat:"
_TTL_SECONDS = 3600

_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    # 模块级懒加载单例,避免每轮通话新建连接
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


def _history_key(call_id: str, biz_type: str) -> str:
    return f"{_KEY_PREFIX}{biz_type}:{call_id}"


def _encode(role: str, content: str) -> str:
    return json.dumps({"role": role, "content": content}, ensure_ascii=False)


def _decode(raw: str) -> BaseMessage | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    role, content = obj.get("role"), obj.get("content", "")
    if role == "human":
        return HumanMessage(content=content)
    if role == "ai":
        return AIMessage(content=content)
    return None


async def load_chat_history(call_id: str, biz_type: str) -> list[BaseMessage]:
    """加载本轮之前的完整对话历史 (human/ai 交替)。Redis 异常降级为空列表,不阻断通话。"""
    try:
        raws = await _redis().lrange(_history_key(call_id, biz_type), 0, -1)
    except Exception as e:
        logger.warning("load_chat_history failed (call_id=%s): %s", call_id, e)
        return []
    return [m for m in (_decode(r) for r in raws) if m is not None]


async def save_turn(call_id: str, biz_type: str, user_text: str, ai_text: str) -> None:
    """追加一轮对话。user_text 可为空 (如纯开场问候);两者皆空则整轮跳过。"""
    user_text = (user_text or "").strip()
    ai_text = (ai_text or "").strip()
    if not user_text and not ai_text:
        return
    key = _history_key(call_id, biz_type)
    pipe = _redis().pipeline()
    if user_text:
        pipe.rpush(key, _encode("human", user_text))
    if ai_text:
        pipe.rpush(key, _encode("ai", ai_text))
    pipe.expire(key, _TTL_SECONDS)
    try:
        await pipe.execute()
    except Exception as e:
        logger.warning("save_turn failed (call_id=%s): %s", call_id, e)
