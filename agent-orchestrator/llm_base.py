import re
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

FALLBACK_ACTION_TEXT = "抱歉，请您稍后再说一遍好吗？"


@dataclass
class LLMAction:
    type: str  # "say" | "ask" | "handoff" | "end"
    text: str
    intent: str = ""
    labels: list[str] = field(default_factory=list)


class LLMEngine(ABC):
    @abstractmethod
    async def invoke(self, prompt: str, schema: dict | None = None) -> str:
        """调用 LLM，返回原始文本响应"""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """调用 Embedding 模型，返回向量"""

    @abstractmethod
    async def health_check(self) -> bool: ...


# 全局引擎实例（由 main.py 初始化）
_llm_engine: LLMEngine | None = None


def set_llm_engine(engine: LLMEngine) -> None:
    global _llm_engine
    _llm_engine = engine


async def get_embedding(text: str) -> list[float] | None:
    """获取文本向量嵌入（供 RAG 检索使用）"""
    if _llm_engine is None:
        return None
    try:
        return await _llm_engine.embed(text)
    except Exception:
        logger.warning(f"embedding failed for text: {text[:50]}")
        return None


def parse_llm_response(raw: str) -> LLMAction:
    """解析 LLM 响应为结构化动作"""
    try:
        data = json.loads(raw)
        return LLMAction(
            type=data.get("action", "say"),
            text=data.get("text", FALLBACK_ACTION_TEXT),
            intent=data.get("intent", ""),
            labels=data.get("labels", []),
        )
    except (json.JSONDecodeError, AttributeError):
        pass

    action_match = re.search(r'"action"\s*:\s*"(\w+)"', raw)
    text_match = re.search(r'"text"\s*:\s*"([^"]+)"', raw)
    if action_match and text_match:
        return LLMAction(type=action_match.group(1), text=text_match.group(1))

    logger.warning(f"LLM 响应解析失败，使用兜底: {raw[:100]}")
    return LLMAction(type="say", text=FALLBACK_ACTION_TEXT)
