"""通话编排管线 — Pre-LLM 阶段 + 流式 LLM/TTS 管线。

对外暴露两个函数，由 handler.py 通过函数注入调用：
  - run_pre_llm_phase()   — ASR 识别 + 并行扇出（MCP/记忆/RAG）
  - run_streaming_pipeline() — LLM 流式输出 → 句级 TTS → 音频回调

调用链路：
  main.py::lifespan()
    → StreamingCallHandler(pre_llm_fn=run_pre_llm_phase, streaming_fn=run_streaming_pipeline)
    → handler._process_streaming_turn()
        ├── run_pre_llm_phase()       ← Phase 1: ASR + MCP/Memory/RAG 并行
        └── run_streaming_pipeline()  ← Phase 2: LLM 流式 → SentenceSplitter → 句级 TTS
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypedDict

import numpy as np

from langchain_core.messages import BaseMessage

from llm.service import LLMAction, get_llm_service
from llm.sentence_splitter import Sentence
from config import settings
from rag.retriever import retrieve_scripts, build_rag_block, should_retrieve, grade_documents, rewrite_query
from graph.prompt import build_messages
from memory.assembler import MemoryAssembler
from clients.mcp import MCPClient
from clients.tts import TTSClient
from clients.asr import ASRClient
from clients.tts_grpc_client import TTSGrpcClient
from clients.asr_grpc_client import ASRGrpcClient
from clients.tts_ws_client import TTSWebSocketClient
from clients.asr_ws_client import ASRWebSocketClient
from storage import minio_storage

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 服务单例 — 由 main.py lifespan 通过 set_services() 注入
# ═══════════════════════════════════════════════════════════════════

_assembler: MemoryAssembler | None = None
_mcp_client: MCPClient | None = None
_tts_client: TTSClient | None = None
_asr_client: ASRClient | None = None
_tts_grpc_client: TTSGrpcClient | None = None
_asr_grpc_client: ASRGrpcClient | None = None
_tts_ws_client: TTSWebSocketClient | None = None
_asr_ws_client: ASRWebSocketClient | None = None


def set_services(
    assembler: MemoryAssembler,
    mcp: MCPClient,
    tts: TTSClient,
    asr: ASRClient | None = None,
    tts_grpc: TTSGrpcClient | None = None,
    asr_grpc: ASRGrpcClient | None = None,
    tts_ws: TTSWebSocketClient | None = None,
    asr_ws: ASRWebSocketClient | None = None,
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
    logger.info("flow services injected: mcp=%s tts=%s asr=%s tts_grpc=%s asr_grpc=%s tts_ws=%s asr_ws=%s",
                mcp is not None, tts is not None, asr is not None,
                tts_grpc is not None, asr_grpc is not None,
                tts_ws is not None, asr_ws is not None)


# ═══════════════════════════════════════════════════════════════════
# State 定义
# ═══════════════════════════════════════════════════════════════════

class CallGraphState(TypedDict, total=False):
    call_id: str
    biz_type: str
    user_key: str
    user_input: str
    audio_bytes: bytes | None
    identity: dict | None
    credit_result: dict | None
    memory_block: str
    rag_block: str
    chat_history: list[BaseMessage]


# ═══════════════════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════════════════

def _get_asr_client() -> tuple[str, object]:
    """返回 (传输方式, 客户端实例)，优先级: gRPC > WS > HTTP。"""
    if _asr_grpc_client is not None:
        return "gRPC", _asr_grpc_client
    if _asr_ws_client is not None:
        return "WS", _asr_ws_client
    if _asr_client is not None:
        return "HTTP", _asr_client
    return "none", None


def _get_tts_client() -> tuple[str, object]:
    """返回 (传输方式, 客户端实例)，优先级: WS > gRPC > HTTP。"""
    if _tts_ws_client is not None:
        return "WS", _tts_ws_client
    if _tts_grpc_client is not None:
        return "gRPC", _tts_grpc_client
    if _tts_client is not None:
        return "HTTP", _tts_client
    return "none", None


WAV_HEADER_SIZE = 44


def _strip_wav_header(wav_bytes: bytes) -> bytes:
    """剥离 44 字节 WAV 头，返回原始 PCM。"""
    if len(wav_bytes) > WAV_HEADER_SIZE and wav_bytes[:4] == b'RIFF':
        return wav_bytes[WAV_HEADER_SIZE:]
    return wav_bytes


def _resample_pcm(pcm: bytes, orig_rate: int, target_rate: int) -> bytes:
    """PCM int16 重采样 (numpy 线性插值)。"""
    if orig_rate == target_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    target_len = int(len(samples) * target_rate / orig_rate)
    if target_len == 0:
        return b""
    resampled = np.interp(
        np.linspace(0, len(samples) - 1, target_len),
        np.arange(len(samples)),
        samples,
    )
    return resampled.astype(np.int16).tobytes()


# ═══════════════════════════════════════════════════════════════════
# Node 函数 — 被 run_pre_llm_phase 调用
# ═══════════════════════════════════════════════════════════════════

async def _asr_node(state: CallGraphState) -> dict:
    """Node ①: ASR 语音识别。优先 gRPC/WS，回退 HTTP。"""
    call_id = state.get("call_id", "?")
    audio_bytes = state.get("audio_bytes")

    if audio_bytes:
        asr_minio_key = minio_storage.build_object_key(prefix="asr", call_id=call_id)
        if asr_minio_key:
            asyncio.create_task(minio_storage.upload_audio_async(audio_bytes, asr_minio_key))
        try:
            transport, client = _get_asr_client()
            if client is not None:
                asr_result = await client.recognize(audio_bytes, call_id)
                user_input = asr_result.get("text", "") if asr_result else ""
                logger.info("[%s] ASR via %s: %s", call_id, transport, user_input[:50])
            else:
                logger.warning("[%s] no ASR client available", call_id)
                user_input = ""
        except Exception as e:
            logger.error("[%s] ASR failed: %s", call_id, e)
            user_input = ""
    else:
        user_input = state.get("user_input", "")

    # TODO: re-enable after fixing RedisSearch (FT._LIST)
    chat_history: list = []

    return {"user_input": user_input, "chat_history": chat_history}


async def _mcp_identity_node(state: CallGraphState) -> dict:
    """Node ②: MCP 用户身份查询。"""
    if _mcp_client is None:
        return {"identity": None}
    call_id = state.get("call_id", "?")
    try:
        result = await _mcp_client.query_user_identity(state["user_key"], state["biz_type"])
        logger.info("[%s] MCP identity: user_id=%s phone=%s", call_id, result.user_id, result.phone_masked)
        return {"identity": {
            "user_id": result.user_id,
            "phone_masked": result.phone_masked,
            "id_card_last_four": result.id_card_last_four,
        }}
    except Exception as e:
        logger.error("[%s] MCP identity failed: %s", call_id, e)
        return {"identity": None}


async def _credit_query_node(state: CallGraphState) -> dict:
    """Node ③: 征信查询（仅 marketing）。"""
    if _mcp_client is None:
        return {"credit_result": None}
    call_id = state.get("call_id", "?")
    try:
        user_id = state.get("identity", {}).get("user_id", "") if state.get("identity") else ""
        result = await _mcp_client.query_credit_profile(user_id)
        logger.info("[%s] credit: qualified=%s risk=%s", call_id, result.credit_qualified, result.risk_level)
        return {"credit_result": {
            "user_id": result.user_id,
            "credit_qualified": result.credit_qualified,
            "risk_level": result.risk_level,
            "details": result.details,
        }}
    except Exception as e:
        logger.error("[%s] credit query failed: %s", call_id, e)
        return {"credit_result": None}


async def _recall_memory_node(state: CallGraphState) -> dict:
    """Node ④: 记忆召回（Redis 热 + PG 长期）。"""
    if _assembler is None:
        return {"memory_block": ""}
    call_id = state.get("call_id", "?")
    try:
        memory_block = await _assembler.assemble(
            biz_type=state["biz_type"],
            user_key=state["user_key"],
            user_input=state["user_input"],
        )
        logger.info("[%s] memory assembled: %d chars", call_id, len(memory_block))
        return {"memory_block": memory_block}
    except Exception as e:
        logger.error("[%s] memory recall failed: %s", call_id, e)
        return {"memory_block": ""}


async def _rag_retrieve_node(state: CallGraphState) -> dict:
    """Node ⑤: Agentic RAG（自适应检索 + 文档评分 + 查询改写）。"""
    call_id = state.get("call_id", "?")
    try:
        rag_query = state["user_input"]

        need_retrieve = await should_retrieve(rag_query, state["biz_type"])
        if not need_retrieve:
            logger.info("[%s] RAG skipped (greeting/closing)", call_id)
            return {"rag_block": ""}

        for attempt in range(settings.rag_max_retries + 1):
            scripts = await retrieve_scripts(state["biz_type"], rag_query)
            if scripts:
                relevant = await grade_documents(rag_query, scripts)
                if relevant:
                    logger.info("[%s] RAG: %d relevant scripts found (attempt %d)", call_id, len(relevant), attempt + 1)
                    return {"rag_block": build_rag_block(relevant)}

            if attempt < settings.rag_max_retries:
                rag_query = await rewrite_query(rag_query, scripts or [])
                logger.info("[%s] RAG query rewritten (attempt %d): %s", call_id, attempt + 1, rag_query[:50])

        logger.info("[%s] RAG: no relevant scripts after %d attempts", call_id, settings.rag_max_retries + 1)
        return {"rag_block": ""}
    except Exception as e:
        logger.error("[%s] RAG failed: %s", call_id, e)
        return {"rag_block": ""}


# ═══════════════════════════════════════════════════════════════════
# Phase 1: Pre-LLM — ASR + 并行扇出
# ═══════════════════════════════════════════════════════════════════

async def run_pre_llm_phase(
    call_id: str, biz_type: str, user_key: str, audio_bytes: bytes,
    precomputed_asr_result: dict | None = None,
) -> CallGraphState:
    """Phase 1: ASR 识别 + 并行扇出（MCP 身份 + 记忆召回 + RAG 检索）。

    Args:
        call_id: 通话唯一标识
        biz_type: 业务类型 (customer_service/collection/marketing)
        user_key: 用户标识
        audio_bytes: 用户音频 PCM
        precomputed_asr_result: 已通过 gRPC/WS 流式获取的 ASR 结果（跳过 HTTP ASR）

    Returns:
        组装好的 CallGraphState，供 run_streaming_pipeline 使用
    """
    t0 = time.monotonic()
    logger.info("[%s] biz_type=%s user_key=%s", call_id, biz_type, user_key)

    # ── ASR ──
    state: CallGraphState = {
        "call_id": call_id,
        "biz_type": biz_type,
        "user_key": user_key,
        "user_input": "",
        "audio_bytes": audio_bytes,
        "identity": None,
        "credit_result": None,
        "memory_block": "",
        "rag_block": "",
        "chat_history": [],
    }

    if precomputed_asr_result:
        precomputed_text = precomputed_asr_result.get("text", "")
        if precomputed_text:
            state["user_input"] = precomputed_text
            state["audio_bytes"] = None
        asr_result = await _asr_node(state)
        state.update(asr_result)
        if precomputed_text:
            state["user_input"] = precomputed_text
    else:
        asr_result = await _asr_node(state)
        state.update(asr_result)

    logger.info("[%s] ASR done: user_input=%s", call_id, state.get("user_input", "")[:50])

    # ── 并行扇出: MCP 身份 + 记忆召回 + RAG ──
    # TODO: re-enable after fixing MCP phone format + RedisSearch + Ollama structured_output
    # identity, memory, rag = await asyncio.gather(
    #     _mcp_identity_node(state),
    #     _recall_memory_node(state),
    #     _rag_retrieve_node(state),
    # )
    # state.update(identity)
    # state.update(memory)
    # state.update(rag)
    # if biz_type == "marketing" and _mcp_client:
    #     state.update(await _credit_query_node(state))

    elapsed = (time.monotonic() - t0) * 1000
    logger.info("[%s] pre-llm phase done in %.0fms", call_id, elapsed)

    return state


# ═══════════════════════════════════════════════════════════════════
# Phase 2: 流式 LLM + TTS 管线
# ═══════════════════════════════════════════════════════════════════

async def run_streaming_pipeline(
    state: CallGraphState,
    audio_callback: Callable[[bytes, int], Awaitable[None]],
    action_callback: Callable[[str], Awaitable[None]] | None = None,
) -> LLMAction:
    """Phase 2: LLM 流式输出 → SentenceSplitter 句级切分 → 并行 TTS → 音频回调。

    Args:
        state: run_pre_llm_phase 返回的 state（含 memory_block, rag_block 等）
        audio_callback: (pcm_bytes, sentence_index) 每句 TTS 音频就绪时调用
        action_callback: action 类型确定时调用
    """
    from llm.sentence_splitter import SentenceSplitter

    llm = get_llm_service()
    call_id = state.get("call_id", "?")
    biz_type = state["biz_type"]
    t0 = time.monotonic()

    # ── 构建 Prompt ──
    from graph.prompt_config import get_system_prompt
    system_prompt = await get_system_prompt(biz_type)
    logger.info("[%s] biz_type=%s prompt loaded: %d chars", call_id, biz_type, len(system_prompt))
    logger.info("[%s] system_prompt content:\n%s", call_id, system_prompt)
    messages = build_messages(
        biz_type=biz_type,
        system_prompt=system_prompt,
        user_input=state["user_input"],
        memory_block=state.get("memory_block", ""),
        rag_block=state.get("rag_block", ""),
        chat_history=state.get("chat_history", []),
    )

    # ── TTS 句级合成 ──
    splitter = SentenceSplitter(
        min_length=settings.splitter_min_length,
        flush_timeout=settings.splitter_flush_timeout,
        eager_first=settings.splitter_eager_first,
    )
    action_sent = False
    detected_action: str = "say"
    full_text = ""
    tts_tasks: list[asyncio.Task] = []

    async def _tts_sentence(sentence: Sentence) -> None:
        """TTS 合成单句 → 重采样 → 回调发送。"""
        if not sentence.text:
            return

        # Streaming TTS: WS 逐块返回原始 PCM
        if settings.tts_streaming_enabled and _tts_ws_client:
            try:
                async for chunk in _tts_ws_client.synthesize_streaming_raw(
                    sentence.text, call_id, biz_type,
                ):
                    if chunk:
                        resampled = _resample_pcm(chunk, 22050, settings.media_sample_rate)
                        await audio_callback(resampled, sentence.index)
                return
            except Exception as e:
                logger.error("[%s] streaming TTS sentence %d failed: %s", call_id, sentence.index, e)
                return

        # Batch TTS: WS > gRPC > HTTP
        transport, client = _get_tts_client()
        if client is None:
            logger.warning("[%s] no TTS client for sentence %d", call_id, sentence.index)
            return
        try:
            wav = await client.synthesize_raw(sentence.text, call_id, biz_type)
            if wav:
                pcm = _strip_wav_header(wav)
                pcm = _resample_pcm(pcm, 22050, settings.media_sample_rate)
                await audio_callback(pcm, sentence.index)
                logger.debug("[%s] TTS sentence %d via %s: %d bytes", call_id, sentence.index, transport, len(pcm))
        except Exception as e:
            logger.error("[%s] TTS sentence %d via %s failed: %s", call_id, sentence.index, transport, e)

    # ── 流式 LLM ──
    try:
        async for event in llm.astream_action([m.model_dump() for m in messages]):
            if event.action and not action_sent:
                action_sent = True
                detected_action = event.action
                if action_callback:
                    await action_callback(event.action)

            if event.text_delta:
                full_text += event.text_delta
                for s in splitter.feed(event.text_delta):
                    tts_tasks.append(asyncio.create_task(_tts_sentence(s)))

            for s in splitter.check_timeout():
                tts_tasks.append(asyncio.create_task(_tts_sentence(s)))

            if event.is_complete:
                logger.info("[%s] LLM complete: action=%s text=%s", call_id, detected_action, full_text)
                final_sent = splitter.flush()
                if final_sent:
                    tts_tasks.append(asyncio.create_task(_tts_sentence(final_sent)))
                if not full_text and event.parsed:
                    full_text = event.parsed.get("text", "")

    except asyncio.CancelledError:
        logger.info("[%s] streaming pipeline cancelled, cancelling %d TTS tasks", call_id, len(tts_tasks))
        for t in tts_tasks:
            if not t.done():
                t.cancel()
        if tts_tasks:
            await asyncio.gather(*tts_tasks, return_exceptions=True)
        raise
    except Exception as e:
        logger.error("[%s] streaming LLM failed: %s", call_id, e)

    # 兜底: 确保 action 已发送
    if not action_sent and action_callback:
        await action_callback("say")

    # 等待所有 TTS 任务完成
    if tts_tasks:
        await asyncio.gather(*tts_tasks, return_exceptions=True)

    # TODO: re-enable chat history save after fixing RedisSearch
    # try:
    #     history = get_chat_history(call_id, biz_type)
    #     await save_turn(history, state.get("user_input", ""), full_text)
    # except Exception as e:
    #     logger.warning("[%s] save history failed: %s", call_id, e)

    elapsed = (time.monotonic() - t0) * 1000
    logger.info("[%s] streaming pipeline done in %.0fms, %d sentences TTS'd",
                call_id, elapsed, len(tts_tasks))

    return LLMAction(action=detected_action, text=full_text)
