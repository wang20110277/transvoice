"""Agent Orchestrator — FastAPI HTTP 服务"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中，兼容 Docker 挂载、本地开发等场景
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Form, WebSocket, Query
from pydantic import BaseModel

from fastapi import HTTPException

from src.config import settings
from src.graph.flow import (
    create_call_graph, set_services, CallGraphState,
    run_pre_llm_phase, run_streaming_pipeline,
)
from src.memory.assembler import MemoryAssembler
from src.clients.mcp import MCPClient
from src.clients.tts import TTSClient
from src.clients.asr import ASRClient
from src.clients.esl import ESLClient
from src.ws.registry import ActiveCallRegistry
from src.ws.denoise import create_denoiser
from src.clients.asr_grpc_client import ASRGrpcClient
from src.clients.tts_grpc_client import TTSGrpcClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

_graph = None
_initialized = False
_ws_handler = None
_streaming_handler = None
_call_registry = ActiveCallRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph, _initialized, _ws_handler, _streaming_handler

    assembler = MemoryAssembler()
    mcp = MCPClient(settings.mcp_server_url, settings.mcp_transport)
    try:
        await mcp.initialize()
    except Exception as e:
        logger.warning("MCP 初始化失败，将跳过身份/征信查询: %s", e)
    tts = TTSClient(settings.tts_adapter_url)
    asr = ASRClient(settings.asr_adapter_url)
    await asr.start()
    await tts.start()

    logger.info("ASR gRPC: enabled=%s target=%s", settings.asr_use_grpc, settings.asr_grpc_target)
    logger.info("TTS gRPC: enabled=%s target=%s", settings.tts_use_grpc, settings.tts_grpc_target)

    # gRPC ASR client (optional — for streaming audio transfer)
    asr_grpc = None
    if settings.asr_use_grpc:
        asr_grpc = ASRGrpcClient(settings.asr_grpc_target)
        await asr_grpc.start()
        logger.info("ASR gRPC client connected to %s", settings.asr_grpc_target)

    # gRPC TTS client (optional — for streaming text-to-speech)
    tts_grpc = None
    if settings.tts_use_grpc:
        tts_grpc = TTSGrpcClient(settings.tts_grpc_target)
        await tts_grpc.start()
        logger.info("TTS gRPC client connected to %s", settings.tts_grpc_target)

    set_services(assembler, mcp, tts, asr, tts_grpc=tts_grpc, asr_grpc=asr_grpc)

    # ESL client (optional — graceful degradation if FreeSWITCH not reachable)
    esl = ESLClient(host=settings.esl_host, port=settings.esl_port, password=settings.esl_password)
    try:
        await esl.start()
        # Subscribe to CHANNEL_HANGUP to detect caller hangup
        async def _on_channel_hangup(event):
            hangup_uuid = event.headers.get("Unique-ID", "")
            if hangup_uuid:
                _call_registry.cancel_call(hangup_uuid)
        esl.on_event("CHANNEL_HANGUP", _on_channel_hangup)
        await esl.subscribe(["CHANNEL_HANGUP"])
        logger.info("ESL subscribed to CHANNEL_HANGUP")
    except Exception as e:
        logger.warning("ESL connection failed (call control disabled): %s", e)
        esl = None

    _graph = create_call_graph()
    _initialized = True

    from src.ws.handler import CallWebSocketHandler, StreamingCallHandler
    denoiser = create_denoiser()
    _ws_handler = CallWebSocketHandler(
        turn_fn=run_audio_pipeline, esl=esl, handoff_extension=settings.handoff_extension,
        vad_aggressiveness=settings.vad_aggressiveness,
        vad_silence_frames=settings.vad_silence_frames,
        vad_min_audio_bytes=settings.vad_min_audio_bytes,
    )
    _streaming_handler = StreamingCallHandler(
        pre_llm_fn=run_pre_llm_phase,
        streaming_fn=run_streaming_pipeline,
        esl=esl,
        handoff_extension=settings.handoff_extension,
        registry=_call_registry,
        vad_aggressiveness=settings.vad_aggressiveness,
        vad_silence_frames=settings.vad_silence_frames,
        vad_min_audio_bytes=settings.vad_min_audio_bytes,
        barge_in_min_audio_bytes=settings.barge_in_min_audio_bytes,
        jitter_target_depth=settings.jitter_target_depth,
        jitter_max_depth=settings.jitter_max_depth,
        denoiser=denoiser,
        asr_grpc_client=asr_grpc,
        use_grpc_streaming=settings.asr_use_grpc,
    )

    logger.info("=== Agent Orchestrator 启动 ===")

    yield

    try:
        await mcp.close()
    except Exception:
        pass
    if _streaming_handler and _streaming_handler._esl:
        await _streaming_handler._esl.close()
    if asr_grpc:
        await asr_grpc.close()
    if tts_grpc:
        await tts_grpc.close()
    await asr.close()
    await tts.close()
    _initialized = False
    logger.info("=== Agent Orchestrator 关闭 ===")


app = FastAPI(title="Agent Orchestrator", lifespan=lifespan)


class TextTurnRequest(BaseModel):
    """文本对话请求体 — POST /call/text-turn 使用。"""
    call_id: str          # 通话唯一标识
    biz_type: str         # 业务类型: customer_service / collection / marketing
    user_key: str         # 用户标识（手机号等）
    text: str             # 用户输入文本
    minio_key: str | None = None  # 可选，已上传音频的 MinIO key


def _build_initial_state(
    call_id: str,
    biz_type: str,
    user_key: str,
    user_input: str = "",
    minio_key: str | None = None,
    audio_bytes: bytes | None = None,
) -> CallGraphState:
    return {
        "call_id": call_id,
        "biz_type": biz_type,
        "user_key": user_key,
        "user_input": user_input,
        "audio_bytes": audio_bytes,
        "asr_minio_key": minio_key,
        "identity": None,
        "credit_result": None,
        "memory_block": "",
        "rag_block": "",
        "llm_action": None,
        "tts_minio_key": None,
        "tts_audio": None,
        "chat_history": [],
    }


async def run_text_pipeline(
    call_id: str, biz_type: str, user_key: str, text: str, minio_key: str | None = None
) -> dict:
    """调用 LangGraph 全流程（文本输入）：MCP查询 ‖ 记忆召回 ‖ RAG检索 → LLM → TTS。"""
    if _graph is None:
        return {"action": "say", "text": "", "tts_audio": None, "tts_minio_key": None}

    initial_state = _build_initial_state(
        call_id=call_id, biz_type=biz_type, user_key=user_key,
        user_input=text, minio_key=minio_key,
    )
    result = await _graph.ainvoke(initial_state)
    action = result.get("llm_action")
    return {
        "action": action.action if action else "say",
        "text": action.text if action else "",
        "tts_minio_key": result.get("tts_minio_key"),
        "tts_audio": result.get("tts_audio"),
    }


async def run_audio_pipeline(
    call_id: str, biz_type: str, user_key: str, audio_bytes: bytes
) -> dict:
    """调用 LangGraph 全流程（音频输入）：ASR → MCP查询 ‖ 记忆召回 ‖ RAG检索 → LLM → TTS。"""
    if _graph is None:
        return {"text": "", "action": "say", "tts_audio_path": None, "tts_minio_key": None}

    initial_state = _build_initial_state(
        call_id=call_id, biz_type=biz_type, user_key=user_key,
        audio_bytes=audio_bytes,
    )
    result = await _graph.ainvoke(initial_state)
    action = result.get("llm_action")
    action_type = action.action if action else "say"
    action_text = action.text if action else ""

    tts_audio_path = None
    tts_audio_b64 = result.get("tts_audio")
    if tts_audio_b64:
        try:
            audio_data = base64.b64decode(tts_audio_b64)
            temp_dir = settings.temp_dir
            os.makedirs(temp_dir, exist_ok=True)
            tts_audio_path = os.path.join(temp_dir, f"{call_id}_response.wav")
            with open(tts_audio_path, "wb") as f:
                f.write(audio_data)
        except Exception as e:
            logger.error("[%s] save tts audio failed: %s", call_id, e)

    return {
        "text": result.get("user_input", action_text),
        "action": action_type,
        "action_text": action_text,
        "tts_audio_path": tts_audio_path,
        "tts_minio_key": result.get("tts_minio_key"),
    }


# ── HTTP 接口 ────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    """健康检查 — 判断编排服务是否初始化完成。

    Returns:
        {"status": "ok" | "initializing"}
    """
    return {"status": "ok" if _initialized else "initializing"}


@app.post("/call/text-turn")
async def handle_text_turn(request: TextTurnRequest):
    """文本对话（同步模式）— 接收文本，执行 LangGraph 7节点全流程，返回 AI 回复。

    流程: 接收文本 → MCP身份查询 ‖ 记忆召回 ‖ RAG检索 → LLM决策 → TTS合成

    Args (JSON body):
        call_id: 通话唯一标识
        biz_type: 业务类型 (customer_service / collection / marketing)
        user_key: 用户标识（手机号等）
        text: 用户输入文本
        minio_key: 可选，已上传音频的 MinIO key

    Returns:
        {"action": str, "text": str, "tts_minio_key": str|None, "tts_audio": str|None}
        action 值: "say"(继续对话) / "ask"(追问) / "handoff"(转人工) / "end"(挂断)
    """
    if _graph is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return await run_text_pipeline(
        call_id=request.call_id, biz_type=request.biz_type,
        user_key=request.user_key, text=request.text, minio_key=request.minio_key,
    )


@app.post("/call/audio-turn")
async def handle_audio_turn(audio: UploadFile, params: str = Form("{}")):
    """音频对话（同步模式）— 上传音频，全链路 ASR → LangGraph → TTS。

    流程: 接收音频 → ASR识别 → MCP身份查询 ‖ 记忆召回 ‖ RAG检索 → LLM决策 → TTS合成

    Args (multipart/form-data):
        audio: 音频文件（PCM/WAV）
        params: JSON 字符串:
            - call_id (str): 通话唯一标识
            - biz_type (str): 业务类型，默认 "marketing"
            - user_key (str): 用户标识

    Returns:
        {"text": str, "confidence": float, "is_final": bool}
    """
    if _graph is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    audio_bytes = await audio.read()
    params_dict = json.loads(params)
    call_id = params_dict.get("call_id", "")
    biz_type = params_dict.get("biz_type", "marketing")
    user_key = params_dict.get("user_key", "")

    turn_result = await run_audio_pipeline(call_id, biz_type, user_key, audio_bytes)
    return {
        "text": turn_result.get("text", ""),
        "confidence": 0.95,
        "is_final": True,
    }


# ── WebSocket 接口 ────────────────────────────────────────────

@app.websocket("/ws/streaming-call")
async def ws_streaming_call(
    websocket: WebSocket,
    call_id: str = Query(...),
    biz_type: str = Query(default="marketing"),
    user_key: str = Query(default=""),
):
    """双向音频流（流式模式，生产路径）— FreeSWITCH mod_audio_fork 连接。

    连接地址: ws://host:8000/ws/streaming-call?call_id=xxx&biz_type=marketing&user_key=138xxx

    协议:
        接收 (FreeSWITCH → agent-flow): 二进制 PCM 16-bit 音频帧
        发送 (agent-flow → FreeSWITCH): 二进制 PCM 16-bit TTS 音频帧（句级流式）
        控制: 文本 JSON 帧 {"type": "action", "action": "say|ask|handoff|end", "turn": int}

    流程:
        1. JitterBuffer 抖动平滑 → 降噪 → WebRTC VAD 端点检测
        2. ASR 识别 → MCP/记忆/RAG 并发查询（fan-out）
        3. LLM 流式输出 → SentenceSplitter 分句 → 每句并行 TTS
        4. TTSOutputBuffer 稳态 30ms 帧回传 FreeSWITCH
        5. 支持 barge-in（打断）：VAD 检测到用户说话 → ESL uuid_break → 取消当前流

    Args (query params):
        call_id: 通话唯一标识（必填）
        biz_type: 业务类型，默认 "marketing"
        user_key: 用户标识（手机号等），默认 ""
    """
    handler = _streaming_handler or _ws_handler
    if handler is None:
        await websocket.close(code=503, reason="Service not initialized")
        return
    await handler.handle(websocket, call_id, biz_type, user_key)
