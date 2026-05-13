"""LLM 服务层 - LangChain 1.2 ChatOpenAI + 结构化输出"""
import json
import logging
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from config import settings

logger = logging.getLogger(__name__)

FALLBACK_ACTION_TEXT = "抱歉，请再说一遍。"


class LLMAction(BaseModel):
    """LLM 返回的结构化动作"""
    action: str = Field(description="动作类型: say | ask | handoff | end")
    text: str = Field(description="回复用户的文本")
    intent: str = Field(default="", description="识别到的意图")
    labels: dict = Field(default_factory=dict, description="附加标签")


class LLMService:
    """LLM 服务封装 - LangChain 1.2 结构化输出"""

    def __init__(self):
        self._chat = ChatOpenAI(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout=settings.llm_timeout_sec,
            max_tokens=256,
            temperature=0.7,
        )
        # 结构化输出绑定（LangChain 1.2 with_structured_output）
        self._structured_chat = self._chat.with_structured_output(
            LLMAction,
            method="json_mode",
        )
        self._embeddings = OpenAIEmbeddings(
            base_url=settings.llm_base_url,
            model=settings.llm_embedding_model,
        )

    async def chat(self, messages: list) -> str:
        """发送对话消息，返回文本响应"""
        lc_messages = self._to_lc_messages(messages)
        response = await self._chat.ainvoke(lc_messages)
        return response.content

    async def chat_for_action(self, messages: list) -> LLMAction:
        """发送对话并解析为结构化动作（LangChain 1.2 结构化输出）"""
        lc_messages = self._to_lc_messages(messages)
        try:
            result = await self._structured_chat.ainvoke(lc_messages)
            if isinstance(result, LLMAction):
                return result
            # 兜底：如果返回非结构化结果，手动解析
            return self._parse_fallback(str(result))
        except Exception as e:
            logger.error(f"结构化输出失败，尝试手动解析: {e}")
            try:
                raw = await self.chat(messages)
                return self._parse_fallback(raw)
            except Exception:
                return LLMAction(action="say", text=FALLBACK_ACTION_TEXT)

    async def get_embeddings(self, text: str) -> list[float]:
        """获取文本嵌入向量"""
        return await self._embeddings.aembed_query(text)

    async def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """批量获取嵌入向量"""
        return await self._embeddings.aembed_documents(texts)

    async def health_check(self) -> bool:
        """检查 LLM 服务可用性"""
        try:
            response = await self._chat.ainvoke([HumanMessage(content="ping")])
            return bool(response.content)
        except Exception as e:
            logger.error(f"LLM 健康检查失败: {e}")
            return False

    @staticmethod
    def _to_lc_messages(messages: list) -> list:
        """转换消息列表为 LangChain 消息对象"""
        lc_messages = []
        for msg in messages:
            if isinstance(msg, (SystemMessage, HumanMessage, AIMessage)):
                lc_messages.append(msg)
                continue
            role = msg.get("role", "user") if isinstance(msg, dict) else "user"
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))
        return lc_messages

    @staticmethod
    def _parse_fallback(raw: str) -> LLMAction:
        """手动 JSON 解析兜底"""
        try:
            data = json.loads(raw)
            return LLMAction(
                action=data.get("action", data.get("type", "say")),
                text=data.get("text", FALLBACK_ACTION_TEXT),
                intent=data.get("intent", ""),
                labels=data.get("labels", {}),
            )
        except (json.JSONDecodeError, AttributeError):
            pass

        import re
        action_match = re.search(r'"action"\s*:\s*"(\w+)"', raw)
        text_match = re.search(r'"text"\s*:\s*"([^"]+)"', raw)
        if action_match and text_match:
            return LLMAction(action=action_match.group(1), text=text_match.group(1))

        logger.warning(f"LLM 响应解析失败，使用兜底: {raw[:100]}")
        return LLMAction(action="say", text=FALLBACK_ACTION_TEXT)


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service
