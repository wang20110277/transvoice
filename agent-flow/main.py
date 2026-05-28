"""Agent Orchestrator — FastAPI WebSocket 服务（事件驱动 uuid_audio_fork）"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中，兼容 Docker 挂载、本地开发等场景
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket

from src.config import settings
from src.graph.flow import set_services, run_pre_llm_phase, run_streaming_pipeline
from src.memory.assembler import MemoryAssembler
from src.clients.mcp import MCPClient
from src.clients.tts import TTSClient
from src.clients.asr import ASRClient
from src.clients.esl import ESLClient
from src.ws.registry import ActiveCallRegistry
from src.ws.denoise import create_denoiser
from src.clients.asr_grpc_client import ASRGrpcClient
from src.clients.tts_grpc_client import TTSGrpcClient
from src.clients.asr_ws_client import ASRWebSocketClient
from src.clients.tts_ws_client import TTSWebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

_initialized = False
_streaming_handler = None
_call_registry = ActiveCallRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _initialized, _streaming_handler

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
    logger.info("ASR WS: enabled=%s url=%s", settings.asr_use_ws, settings.asr_ws_url)
    logger.info("TTS WS: enabled=%s url=%s", settings.tts_use_ws, settings.tts_ws_url)
    logger.info("Streaming ASR: enabled=%s", settings.asr_streaming_enabled)
    logger.info("Streaming TTS: enabled=%s", settings.tts_streaming_enabled)
    logger.info("Splitter: min=%d timeout=%.1fs eager_first=%s",
                settings.splitter_min_length, settings.splitter_flush_timeout, settings.splitter_eager_first)

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

    # WebSocket ASR client (optional — third transport)
    asr_ws = None
    if settings.asr_use_ws:
        asr_ws = ASRWebSocketClient(settings.asr_ws_url)
        await asr_ws.start()
        logger.info("ASR WS client ready, url=%s", settings.asr_ws_url)

    # WebSocket TTS client (optional — third transport)
    tts_ws = None
    if settings.tts_use_ws:
        tts_ws = TTSWebSocketClient(settings.tts_ws_url)
        await tts_ws.start()
        logger.info("TTS WS client connected to %s", settings.tts_ws_url)

    set_services(assembler, mcp, tts, asr, tts_grpc=tts_grpc, asr_grpc=asr_grpc,
                 tts_ws=tts_ws, asr_ws=asr_ws)

    # ESL client (optional — graceful degradation if FreeSWITCH not reachable)
    esl = ESLClient(host=settings.esl_host, port=settings.esl_port, password=settings.esl_password)
    try:
        await esl.start()

        # CHANNEL_HANGUP: stop audio fork + cancel active call
        async def _on_channel_hangup(event):
            hangup_uuid = event.headers.get("Unique-ID", "")
            if not hangup_uuid:
                return
            logger.info("[%s] CHANNEL_HANGUP received", hangup_uuid)
            try:
                await esl.audio_fork_stop(hangup_uuid)
            except Exception:
                pass
            _call_registry.cancel_call(hangup_uuid)

        # CHANNEL_ANSWER: register call + start dynamic audio fork
        async def _on_channel_answer(event):
            uuid = event.headers.get("Unique-ID", "")
            if not uuid:
                return
            biz_type = event.headers.get("variable_biz_type", "marketing")
            user_key = (
                event.headers.get("variable_user_key", "")
                or event.headers.get("Caller-Caller-ID-Number", "")
            )
            logger.info("[%s] CHANNEL_ANSWER biz_type=%s user_key=%s", uuid, biz_type, user_key)

            _call_registry.register(uuid, biz_type, user_key)

            ws_url = f"ws://{settings.media_ws_host}:{settings.media_ws_port}/media/{uuid}"
            try:
                result = await esl.audio_fork_start(
                    uuid, ws_url, sample_rate=settings.media_sample_rate,
                )
                logger.info("[%s] uuid_audio_fork start on CHANNEL_ANSWER: %s → %s", uuid, ws_url, result)
            except Exception as e:
                logger.error("[%s] uuid_audio_fork start failed: %s", uuid, e)
                _call_registry.unregister(uuid)

        esl.on_event("CHANNEL_HANGUP", _on_channel_hangup)
        esl.on_event("CHANNEL_ANSWER", _on_channel_answer)
        await esl.subscribe(["CHANNEL_HANGUP", "CHANNEL_ANSWER"])
        logger.info("ESL subscribed to CHANNEL_HANGUP + CHANNEL_ANSWER")
    except Exception as e:
        logger.warning("ESL connection failed (call control disabled): %s", e)
        esl = None

    _initialized = True

    from src.ws.handler import StreamingCallHandler
    denoiser = create_denoiser()
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
        asr_ws_client=asr_ws,
        use_ws_streaming=settings.asr_use_ws,
        use_streaming_asr=settings.asr_streaming_enabled,
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
    if asr_ws:
        await asr_ws.close()
    if tts_ws:
        await tts_ws.close()
    await asr.close()
    await tts.close()
    _initialized = False
    logger.info("=== Agent Orchestrator 关闭 ===")


app = FastAPI(title="Agent Orchestrator", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok" if _initialized else "initializing"}


@app.websocket("/media/{call_id}")
async def ws_media_fork(websocket: WebSocket, call_id: str):
    """uuid_audio_fork 专用端点 — FreeSWITCH 作为 WS 客户端连接。

    流程:
        1. FreeSWITCH 拨号计划 answer → park → 触发 CHANNEL_ANSWER 事件
        2. agent-flow ESL handler 注册通话 + uuid_audio_fork start → FS 连接本端点
        3. 双向音频流: JitterBuffer → VAD → ASR → LLM 流式 → 句级 TTS → 回传
        4. CHANNEL_HANGUP → uuid_audio_fork stop → 清理资源
    """
    if _streaming_handler is None:
        await websocket.close(code=503, reason="Service not initialized")
        return

    call = _call_registry.get(call_id)
    biz_type = call.biz_type if call else "marketing"
    user_key = call.user_key if call else ""

    await _streaming_handler.handle(websocket, call_id, biz_type, user_key)
