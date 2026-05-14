"""Conversation history — langchain-redis RedisChatMessageHistory"""
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_redis import RedisChatMessageHistory
from config import settings


def get_chat_history(call_id: str, biz_type: str) -> RedisChatMessageHistory:
    return RedisChatMessageHistory(
        session_id=f"{biz_type}:{call_id}",
        redis_url=settings.redis_url,
        key_prefix="cb:chat:",
        ttl=3600,
    )


async def load_chat_history(call_id: str, biz_type: str) -> list[BaseMessage]:
    history = get_chat_history(call_id, biz_type)
    return list(await history.aget_messages())


async def save_turn(history: RedisChatMessageHistory, user_text: str, ai_text: str) -> None:
    await history.aadd_messages([
        HumanMessage(content=user_text),
        AIMessage(content=ai_text),
    ])
