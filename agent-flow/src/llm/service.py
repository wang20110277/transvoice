"""LLM 服务层 - 根据 llm_device 自动适配 Ollama(CPU) / vLLM(GPU)"""
import json
import logging
from collections.abc import AsyncIterator
import httpx
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from config import settings
from llm.json_stream import IncrementalJSONParser, StreamEvent

logger = logging.getLogger(__name__)

FALLBACK_ACTION_TEXT = "抱歉，请再说一遍。"


def _make_http_clients(base_url: str) -> dict:
    """Create httpx clients with trust_env=False to bypass system proxy."""
    timeout = httpx.Timeout(timeout=None, connect=30.0)
    return {
        "http_client": httpx.Client(timeout=timeout, trust_env=False),
        "http_async_client": httpx.AsyncClient(timeout=timeout, trust_env=False),
    }


class LLMAction(BaseModel):
    """LLM 返回的结构化动作"""
    action: str = Field(description="动作类型: say | ask | handoff | end")
    text: str = Field(description="回复用户的文本")
    intent: str = Field(default="", description="识别到的意图")
    labels: dict = Field(default_factory=dict, description="附加标签")


class _OllamaChat:
    """Lightweight Ollama native /api/chat client — bypasses OpenAI compat layer to support think:false."""

    def __init__(self, base_url: str, model: str, timeout: float, max_tokens: int, temperature: float):
        self._base_url = base_url.rstrip("/")
        # Convert /v1 base to root for native API
        if self._base_url.endswith("/v1"):
            self._base_url = self._base_url[:-3]
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout=None, connect=30.0), trust_env=False)

    async def ainvoke(self, messages: list) -> str:
        ollama_msgs = []
        for msg in messages:
            role = msg.role if hasattr(msg, "role") else "user"
            content = msg.content if hasattr(msg, "content") else str(msg)
            ollama_msgs.append({"role": role, "content": content})

        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": ollama_msgs,
                "stream": True,
                "think": False,
                "options": {
                    "num_predict": self._max_tokens,
                    "temperature": self._temperature,
                },
            },
        ) as resp:
            resp.raise_for_status()
            chunks = []
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                import json as _json
                try:
                    data = _json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        chunks.append(content)
                    if data.get("done", False):
                        break
                except _json.JSONDecodeError:
                    pass
            return "".join(chunks)


class LLMService:
    """LLM 服务封装 — 自动适配 Ollama(CPU) / vLLM(GPU)"""

    def __init__(self):
        is_gpu = settings.llm_device == "gpu"
        backend = "vLLM(GPU)" if is_gpu else "Ollama(CPU)"
        logger.info("LLM 后端: %s, model=%s, base_url=%s", backend, settings.llm_model, settings.llm_base_url)

        timeout = settings.llm_timeout_sec
        max_tokens = 512 if is_gpu else 256

        if is_gpu:
            self._chat = ChatOpenAI(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                timeout=timeout,
                max_tokens=max_tokens,
                temperature=0.7,
                **_make_http_clients(settings.llm_base_url),
            )
            method = "json_schema"
            self._structured_chat = self._chat.with_structured_output(LLMAction, method=method)
        else:
            self._ollama = _OllamaChat(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=timeout,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            self._chat = self._ollama
            self._structured_chat = None

        self._embeddings = OpenAIEmbeddings(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_embedding_model,
            **_make_http_clients(settings.llm_base_url),
        )
        self._is_gpu = is_gpu

    async def chat(self, messages: list) -> str:
        """发送对话消息，返回文本响应"""
        lc_messages = self._to_lc_messages(messages)
        if self._is_gpu:
            response = await self._chat.ainvoke(lc_messages)
            return response.content
        else:
            return await self._ollama.ainvoke(lc_messages)

    async def astream_text(self, messages: list) -> AsyncIterator[str]:
        """流式输出原始 token。"""
        lc_messages = self._to_lc_messages(messages)
        async for chunk in self._chat.astream(lc_messages):
            if chunk.content:
                yield chunk.content

    async def astream_action(self, messages: list) -> AsyncIterator[StreamEvent]:
        """流式输出 + 增量 JSON 解析，逐步提取 action/text 字段。"""
        lc_messages = self._to_lc_messages(messages)
        parser = IncrementalJSONParser()

        async for chunk in self._chat.astream(lc_messages):
            if chunk.content:
                for event in parser.feed(chunk.content):
                    yield event

        final = parser.finalize()
        yield final

    async def chat_for_action(self, messages: list) -> LLMAction:
        """发送对话并解析为结构化动作"""
        lc_messages = self._to_lc_messages(messages)
        if self._is_gpu:
            try:
                result = await self._structured_chat.ainvoke(lc_messages)
                if isinstance(result, LLMAction):
                    return result
                return self._parse_fallback(str(result))
            except Exception as e:
                logger.error("结构化输出失败: %s", e)
                return LLMAction(action="say", text=FALLBACK_ACTION_TEXT)
        else:
            # Ollama: native API + manual JSON parse
            try:
                raw = await self._ollama.ainvoke(lc_messages)
                return self._parse_fallback(raw)
            except Exception as e:
                logger.error("Ollama 调用失败: %s", e)
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
            if self._is_gpu:
                response = await self._chat.ainvoke([HumanMessage(content="ping")])
                return bool(response.content)
            else:
                content = await self._ollama.ainvoke([HumanMessage(content="ping")])
                return bool(content)
        except Exception as e:
            logger.error("LLM 健康检查失败: %s", e)
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

        logger.warning("LLM 响应非 JSON，直接使用原文: %s", raw[:100])
        return LLMAction(action="say", text=raw.strip())


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service
