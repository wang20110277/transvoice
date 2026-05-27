"""LangGraph 1.2 通话状态图 - 7 节点管线 + 流式管线"""
import asyncio
import base64
import logging
import os
import time
import yaml
from collections.abc import Awaitable, Callable
from typing import TypedDict

from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage

from llm.service import LLMAction, FALLBACK_ACTION_TEXT, get_llm_service
from llm.json_stream import StreamEvent
from llm.sentence_splitter import Sentence
from config import settings
from rag.retriever import retrieve_scripts, build_rag_block, should_retrieve, grade_documents, rewrite_query
from graph.prompt import build_messages
from memory.assembler import MemoryAssembler
from memory.chat_history import get_chat_history, save_turn
from clients.mcp import MCPClient
from clients.tts import TTSClient
from clients.asr import ASRClient
from storage import minio_storage

logger = logging.getLogger(__name__)

# Module-level service instances — set by main.py lifespan
_assembler: MemoryAssembler | None = None
_mcp_client: MCPClient | None = None
_tts_client: TTSClient | None = None
_asr_client: ASRClient | None = None
_tts_grpc_client: "TTSGrpcClient | None" = None
_asr_grpc_client: "ASRGrpcClient | None" = None
_tts_ws_client: "TTSWebSocketClient | None" = None
_asr_ws_client: "ASRWebSocketClient | None" = None


def set_services(
    assembler: MemoryAssembler,
    mcp: MCPClient,
    tts: TTSClient,
    asr: ASRClient | None = None,
    tts_grpc: "TTSGrpcClient | None" = None,
    asr_grpc: "ASRGrpcClient | None" = None,
    tts_ws: "TTSWebSocketClient | None" = None,
    asr_ws: "ASRWebSocketClient | None" = None,
) -> None:
    global _assembler, _mcp_client, _tts_client, _asr_client
    global _tts_grpc_client, _asr_grpc_client, _tts_ws_client, _asr_ws_client
    _assembler = assembler
    _mcp_client = mcp
    _tts_client = tts
    _asr_client = asr
    _tts_grpc_client = tts_grpc
    _asr_grpc_client = asr_grpc
    _tts_ws_client = tts_ws
    _asr_ws_client = asr_ws


class CallGraphState(TypedDict, total=False):
    call_id: str
    biz_type: str
    user_key: str
    user_input: str
    audio_bytes: bytes | None
    asr_minio_key: str | None
    identity: dict | None
    credit_result: dict | None
    memory_block: str
    rag_block: str
    rag_retry_count: int
    rag_query: str
    llm_action: LLMAction | None
    tts_minio_key: str | None
    tts_audio: str | None
    chat_history: list[BaseMessage]


# ── Node ①: receive_asr ──

async def receive_asr_node(state: CallGraphState) -> dict:
    call_id = state.get("call_id", "?")

    audio_bytes = state.get("audio_bytes")
    if audio_bytes:
        # Upload ASR input audio to MinIO (fire-and-forget)
        asr_minio_key = minio_storage.build_object_key(prefix="asr", call_id=call_id)
        if asr_minio_key:
            asyncio.create_task(minio_storage.upload_audio_async(audio_bytes, asr_minio_key))
        try:
            if _asr_grpc_client is not None:
                asr_result = await _asr_grpc_client.recognize(audio_bytes, call_id)
                logger.info("[%s] ASR via gRPC: %s", call_id, asr_result.get("text", "")[:50] if asr_result else "None")
            elif _asr_ws_client is not None:
                asr_result = await _asr_ws_client.recognize(audio_bytes, call_id)
                logger.info("[%s] ASR via WS: %s", call_id, asr_result.get("text", "")[:50] if asr_result else "None")
            elif _asr_client is not None:
                asr_result = await _asr_client.recognize(audio_bytes, call_id)
                logger.info("[%s] ASR via HTTP: %s", call_id, asr_result.get("text", "")[:50] if asr_result else "None")
            else:
                asr_result = None
            if asr_result:
                user_input = asr_result.get("text", "")
            else:
                user_input = ""
        except Exception as e:
            logger.error("[%s] ASR 调用失败: %s", call_id, e)
            user_input = ""
    else:
        user_input = state.get("user_input", "")
        asr_minio_key = state.get("asr_minio_key")

    try:
        history = get_chat_history(state["call_id"], state["biz_type"])
        chat_history = list(await history.aget_messages())
    except Exception as e:
        logger.warning("[%s] 对话历史加载失败: %s", call_id, e)
        chat_history = []

    return {
        "user_input": user_input,
        "asr_minio_key": asr_minio_key,
        "chat_history": chat_history,
    }


# ── Node ②: mcp_identity ──

async def mcp_identity_node(state: CallGraphState) -> dict:
    if _mcp_client is None:
        return {"identity": None}
    try:
        result = await _mcp_client.query_user_identity(state["user_key"], state["biz_type"])
        return {"identity": {
            "user_id": result.user_id,
            "phone_masked": result.phone_masked,
            "id_card_last_four": result.id_card_last_four,
        }}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] MCP 身份查询失败: {e}")
        return {"identity": None}


# ── Node ③: credit_query (conditional) ──

async def credit_query_node(state: CallGraphState) -> dict:
    if _mcp_client is None:
        return {"credit_result": None}
    try:
        user_id = state.get("identity", {}).get("user_id", "") if state.get("identity") else ""
        result = await _mcp_client.query_credit_profile(user_id)
        return {"credit_result": {
            "user_id": result.user_id,
            "credit_qualified": result.credit_qualified,
            "risk_level": result.risk_level,
            "details": result.details,
        }}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] 征信查询失败: {e}")
        return {"credit_result": None}


# ── Node ④: recall_memory ──

async def recall_memory_node(state: CallGraphState) -> dict:
    if _assembler is None:
        return {"memory_block": ""}
    try:
        memory_block = await _assembler.assemble(
            biz_type=state["biz_type"],
            user_key=state["user_key"],
            user_input=state["user_input"],
        )
        return {"memory_block": memory_block}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] 记忆召回失败: {e}")
        return {"memory_block": ""}


# ── Node ⑤: rag_retrieve ──

async def rag_retrieve_node(state: CallGraphState) -> dict:
    try:
        rag_query = state["user_input"]

        need_retrieve = await should_retrieve(rag_query, state["biz_type"])
        if not need_retrieve:
            return {"rag_block": ""}

        for attempt in range(settings.rag_max_retries + 1):
            scripts = await retrieve_scripts(state["biz_type"], rag_query)

            if scripts:
                relevant = await grade_documents(rag_query, scripts)
                if relevant:
                    return {"rag_block": build_rag_block(relevant)}

            if attempt < settings.rag_max_retries:
                rag_query = await rewrite_query(rag_query, scripts or [])

        return {"rag_block": ""}
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] RAG 检索失败: %s", e)
        return {"rag_block": ""}


# ── Node ⑥: llm_decide ──

async def llm_decide_node(state: CallGraphState) -> dict:
    llm = get_llm_service()
    biz_type = state["biz_type"]

    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", f"{biz_type}.yaml")
    system_prompt = ""
    if os.path.exists(prompt_path):
        with open(prompt_path) as f:
            data = yaml.safe_load(f)
            system_prompt = data.get("system_prompt", data.get("template", ""))

    messages = build_messages(
        biz_type=biz_type,
        system_prompt=system_prompt,
        user_input=state["user_input"],
        memory_block=state.get("memory_block", ""),
        rag_block=state.get("rag_block", ""),
        chat_history=state.get("chat_history", []),
    )

    try:
        action = await llm.chat_for_action([m.model_dump() for m in messages])
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] LLM 调用失败: {e}")
        action = LLMAction(action="say", text=FALLBACK_ACTION_TEXT)

    return {"llm_action": action}


# ── Node ⑦: tts_synthesize ──

async def tts_synthesize_node(state: CallGraphState) -> dict:
    action = state.get("llm_action")
    if not action:
        return {}

    if settings.tts_skip:
        logger.info("[%s] TTS skipped (tts_skip=true), LLM reply: %s", state.get("call_id", "?"), action.text[:80])
        return {"tts_audio": None, "tts_minio_key": None}

    tts_result = None
    try:
        if action.text:
            if _tts_ws_client is not None:
                tts_result = await _tts_ws_client.synthesize(action.text, state["call_id"], state["biz_type"])
                logger.info("[%s] TTS via WS: %d bytes", state.get("call_id", "?"), len(tts_result.get("audio", "")) if tts_result else 0)
            elif _tts_grpc_client is not None:
                tts_result = await _tts_grpc_client.synthesize(action.text, state["call_id"], state["biz_type"])
                logger.info("[%s] TTS via gRPC: %d bytes", state.get("call_id", "?"), len(tts_result.get("audio", "")) if tts_result else 0)
            elif _tts_client is not None:
                tts_result = await _tts_client.synthesize(action.text, state["call_id"], state["biz_type"])
                logger.info("[%s] TTS via HTTP: %d bytes", state.get("call_id", "?"), len(tts_result.get("audio", "")) if tts_result else 0)
    except Exception as e:
        logger.error(f"[{state.get('call_id', '?')}] TTS 合成异常: {e}")
        tts_result = None

    # Upload TTS output audio to MinIO (fire-and-forget)
    tts_minio_key = None
    if tts_result and tts_result.get("audio"):
        try:
            audio_data = base64.b64decode(tts_result["audio"])
            tts_minio_key = minio_storage.build_object_key(prefix="tts", call_id=state["call_id"])
            if tts_minio_key:
                asyncio.create_task(minio_storage.upload_audio_async(audio_data, tts_minio_key))
        except Exception as e:
            logger.warning("[%s] TTS MinIO upload failed: %s", state.get("call_id", "?"), e)

    try:
        history = get_chat_history(state["call_id"], state["biz_type"])
        await save_turn(history, state["user_input"], action.text)
    except Exception as e:
        logger.warning(f"[{state.get('call_id', '?')}] 对话历史保存失败: {e}")

    return {
        "tts_audio": tts_result.get("audio") if tts_result else None,
        "tts_minio_key": tts_minio_key,
    }


# ── Conditional routing ──

def should_query_credit(state: CallGraphState) -> str:
    if state.get("biz_type") == "marketing":
        return "credit_query"
    return "llm_decide"


# ── Graph builder (HTTP sync path — parallel fan-out) ──

def create_call_graph():
    graph = StateGraph(CallGraphState)

    graph.add_node("receive_asr", receive_asr_node)
    graph.add_node("mcp_identity", mcp_identity_node)
    graph.add_node("credit_query", credit_query_node)
    graph.add_node("recall_memory", recall_memory_node)
    graph.add_node("rag_retrieve", rag_retrieve_node)
    graph.add_node("llm_decide", llm_decide_node)
    graph.add_node("tts_synthesize", tts_synthesize_node)

    graph.set_entry_point("receive_asr")

    # Fan-out: receive_asr 后三个节点并行
    graph.add_edge("receive_asr", "mcp_identity")
    graph.add_edge("receive_asr", "recall_memory")
    graph.add_edge("receive_asr", "rag_retrieve")

    # mcp_identity → credit_query (仅 marketing) 或直接到 llm_decide
    graph.add_conditional_edges("mcp_identity", should_query_credit, {
        "credit_query": "credit_query",
        "llm_decide": "llm_decide",
    })
    graph.add_edge("credit_query", "llm_decide")

    # recall_memory 和 rag_retrieve 直接汇入 llm_decide
    graph.add_edge("recall_memory", "llm_decide")
    graph.add_edge("rag_retrieve", "llm_decide")

    graph.add_edge("llm_decide", "tts_synthesize")
    graph.add_edge("tts_synthesize", END)

    return graph.compile()


# ── Pre-LLM phase (for streaming path) ──

async def run_pre_llm_phase(
    call_id: str, biz_type: str, user_key: str, audio_bytes: bytes,
    precomputed_asr_result: dict | None = None,
) -> CallGraphState:
    """运行 ASR + 并行 fan-out（MCP + Memory + RAG），返回组装好的 state。

    precomputed_asr_result: 如果已通过 gRPC 流式获取了 ASR 结果，直接使用，跳过 HTTP ASR 调用。
    """
    t0 = time.monotonic()

    # Node 1: ASR
    state: CallGraphState = {
        "call_id": call_id,
        "biz_type": biz_type,
        "user_key": user_key,
        "user_input": "",
        "audio_bytes": audio_bytes,
        "asr_minio_key": None,
        "identity": None,
        "credit_result": None,
        "memory_block": "",
        "rag_block": "",
        "llm_action": None,
        "tts_minio_key": None,
        "tts_audio": None,
        "chat_history": [],
    }

    if precomputed_asr_result:
        # Use gRPC/WS-streamed ASR result directly
        state["user_input"] = precomputed_asr_result.get("text", "")
        # Clear audio_bytes so receive_asr_node skips the ASR call (result already known)
        state["audio_bytes"] = None
        # Load chat history
        asr_result = await receive_asr_node(state)
        state.update(asr_result)
        state["user_input"] = precomputed_asr_result.get("text", "") or state.get("user_input", "")
    else:
        asr_result = await receive_asr_node(state)
        state.update(asr_result)

    # Parallel fan-out: mcp_identity + recall_memory + rag_retrieve
    identity_coro = mcp_identity_node(state)
    memory_coro = recall_memory_node(state)
    rag_coro = rag_retrieve_node(state)

    identity, memory, rag = await asyncio.gather(identity_coro, memory_coro, rag_coro)
    state.update(identity)
    state.update(memory)
    state.update(rag)

    # Conditional credit query
    if biz_type == "marketing" and _mcp_client:
        credit = await credit_query_node(state)
        state.update(credit)

    elapsed = (time.monotony() - t0) * 1000
    logger.info("[%s] pre-llm phase done in %.0fms, user_input=%s",
                call_id, elapsed, state.get("user_input", "")[:50])

    return state


# ── Streaming LLM+TTS pipeline ──

WAV_HEADER_SIZE = 44


def _strip_wav_header(wav_bytes: bytes) -> bytes:
    """剥离 44 字节 WAV 头，返回原始 PCM。"""
    if len(wav_bytes) > WAV_HEADER_SIZE and wav_bytes[:4] == b'RIFF':
        return wav_bytes[WAV_HEADER_SIZE:]
    return wav_bytes


async def run_streaming_pipeline(
    state: CallGraphState,
    audio_callback: Callable[[bytes, int], Awaitable[None]],
    action_callback: Callable[[str], Awaitable[None]] | None = None,
) -> LLMAction:
    """流式 LLM+TTS 管线。LLM 流式输出 → 句子拆分 → 并行 TTS → 音频回调。

    Args:
        state: 包含 memory_block, rag_block 等预计算结果的 state
        audio_callback: (pcm_bytes, sentence_index) 每句 TTS 音频就绪时调用
        action_callback: action 类型确定时调用
    """
    from llm.sentence_splitter import SentenceSplitter

    llm = get_llm_service()
    call_id = state.get("call_id", "?")
    biz_type = state["biz_type"]
    t0 = time.monotonic()

    # Build prompt
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", f"{biz_type}.yaml")
    system_prompt = ""
    if os.path.exists(prompt_path):
        with open(prompt_path) as f:
            data = yaml.safe_load(f)
            system_prompt = data.get("system_prompt", data.get("template", ""))

    messages = build_messages(
        biz_type=biz_type,
        system_prompt=system_prompt,
        user_input=state["user_input"],
        memory_block=state.get("memory_block", ""),
        rag_block=state.get("rag_block", ""),
        chat_history=state.get("chat_history", []),
    )

    splitter = SentenceSplitter(
        min_length=settings.splitter_min_length,
        flush_timeout=settings.splitter_flush_timeout,
        eager_first=settings.splitter_eager_first,
    )
    action_sent = False
    full_text = ""
    tts_tasks: list[asyncio.Task] = []

    async def _tts_sentence(sentence: Sentence) -> None:
        """TTS 合成单句并通过回调发送。"""
        if not sentence.text:
            return

        # Streaming TTS path: WS streaming yields raw PCM chunks — 逐块发送
        if settings.tts_streaming_enabled and _tts_ws_client:
            try:
                async for chunk in _tts_ws_client.synthesize_streaming_raw(
                    sentence.text, call_id, biz_type,
                ):
                    if chunk:
                        await audio_callback(chunk, sentence.index)
                return
            except Exception as e:
                logger.error("[%s] streaming TTS sentence %d failed: %s", call_id, sentence.index, e)
                return

        # Batch TTS path: WS > gRPC > HTTP
        client = _tts_ws_client or _tts_grpc_client or _tts_client
        if client is None:
            return
        try:
            wav = await client.synthesize_raw(sentence.text, call_id, biz_type)
            if wav:
                pcm = _strip_wav_header(wav)
                await audio_callback(pcm, sentence.index)
        except Exception as e:
            logger.error("[%s] streaming TTS sentence %d failed: %s", call_id, sentence.index, e)

    # Stream LLM tokens
    try:
        async for event in llm.astream_action([m.model_dump() for m in messages]):
            # Extract action type early
            if event.action and not action_sent:
                action_sent = True
                if action_callback:
                    await action_callback(event.action)

            # Feed text deltas to sentence splitter
            if event.text_delta:
                full_text += event.text_delta
                sentences = splitter.feed(event.text_delta)
                for s in sentences:
                    tts_tasks.append(asyncio.create_task(_tts_sentence(s)))

            # Check timeout flush
            for s in splitter.check_timeout():
                tts_tasks.append(asyncio.create_task(_tts_sentence(s)))

            # Final event
            if event.is_complete:
                # Flush remaining buffer
                final_sent = splitter.flush()
                if final_sent:
                    tts_tasks.append(asyncio.create_task(_tts_sentence(final_sent)))

                if not full_text and event.parsed:
                    full_text = event.parsed.get("text", "")

    except Exception as e:
        logger.error("[%s] streaming LLM failed: %s", call_id, e)

    # Ensure action was sent
    if not action_sent and action_callback:
        await action_callback("say")

    # Wait for all TTS tasks
    if tts_tasks:
        await asyncio.gather(*tts_tasks, return_exceptions=True)

    # Save chat history
    try:
        history = get_chat_history(call_id, biz_type)
        await save_turn(history, state.get("user_input", ""), full_text)
    except Exception as e:
        logger.warning("[%s] streaming save history failed: %s", call_id, e)

    elapsed = (time.monotony() - t0) * 1000
    logger.info("[%s] streaming pipeline done in %.0fms, text=%s",
                call_id, elapsed, full_text[:50])

    # Build final LLMAction
    action_type = "say"
    if action_sent:
        # Try to get from final parsed event
        pass
    return LLMAction(action=action_type, text=full_text)
