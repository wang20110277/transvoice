"""Agent Orchestrator — FastAPI WebSocket 入口。

事件驱动架构：
  FreeSWITCH CHANNEL_ANSWER → ESL handler → uuid_audio_fork → WS /media/{uuid}
  → StreamingCallHandler → JitterBuffer → VAD → ASR → LLM 流式 → TTS → 回传

服务启动顺序（lifespan）：
  ① 核心服务 (MCP, TTS, ASR, Memory)
  ② 可选 gRPC 客户端
  ③ 可选 WebSocket 客户端
  ④ 注入 flow.py 服务单例
  ⑤ ESL 连接 + 事件订阅
  ⑥ 创建 StreamingCallHandler
"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中，兼容 Docker 挂载和本地开发
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# 把整个 .env 灌进 os.environ —— pydantic-settings(env_prefix=CALLBOT_) 只加载
# CALLBOT_ 前缀字段，无前缀的 MINIO_* 不会进 os.environ，而 minio_storage 在 import 时
# 用 os.environ.get 读 MINIO_*。load_dotenv 必须在任何 src.storage import 之前执行。
from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from src.config import settings
from src.storage import minio_storage, repository
from src.graph.flow import set_services, run_pre_llm_phase, run_streaming_pipeline
from src.memory.assembler import MemoryAssembler
from src.clients.mcp import MCPClient
from src.clients.tts import TTSClient
from src.clients.asr import ASRClient
from src.clients.esl import ESLClient
from src.ws.registry import ActiveCallRegistry
from src.ws.denoise import create_denoiser
from src.ws.audio_processing import create_audio_processing
from src.ws.vad import create_vad
from src.clients.asr_grpc_client import ASRGrpcClient
from src.clients.tts_grpc_client import TTSGrpcClient
from src.clients.asr_ws_client import ASRWebSocketClient
from src.clients.tts_ws_client import TTSWebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 模块级状态 — 由 lifespan 管理 ──

_initialized = False
_streaming_handler = None
_call_registry = ActiveCallRegistry()
_outbound_executor = None  # OutboundExecutor 单例，lifespan 启停


# ═══════════════════════════════════════════════════════════════════
# 服务初始化
# ═══════════════════════════════════════════════════════════════════

async def _init_core_services() -> tuple[MemoryAssembler, MCPClient, TTSClient, ASRClient]:
    """初始化核心服务：Memory、MCP、TTS、ASR。"""
    assembler = MemoryAssembler()
    logger.info("MemoryAssembler initialized")

    mcp = MCPClient(settings.mcp_server_url, settings.mcp_transport)
    try:
        await asyncio.wait_for(mcp.initialize(), timeout=10)
        logger.info("MCP client connected to %s", settings.mcp_server_url)
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("MCP init failed (identity/credit queries will be skipped): %s", e)

    tts = TTSClient(settings.tts_adapter_url)
    await tts.start()
    logger.info("TTS client started → %s", settings.tts_adapter_url)

    asr = ASRClient(settings.asr_adapter_url)
    await asr.start()
    logger.info("ASR client started → %s", settings.asr_adapter_url)

    return assembler, mcp, tts, asr


async def _init_grpc_clients() -> tuple[ASRGrpcClient | None, TTSGrpcClient | None]:
    """初始化可选 gRPC 客户端（ASR + TTS）。"""
    asr_grpc = None
    if settings.asr_use_grpc:
        asr_grpc = ASRGrpcClient(settings.asr_grpc_target)
        await asr_grpc.start()
        logger.info("ASR gRPC client → %s", settings.asr_grpc_target)

    tts_grpc = None
    if settings.tts_use_grpc:
        tts_grpc = TTSGrpcClient(settings.tts_grpc_target)
        await tts_grpc.start()
        logger.info("TTS gRPC client → %s", settings.tts_grpc_target)

    return asr_grpc, tts_grpc


async def _init_ws_clients() -> tuple[ASRWebSocketClient | None, TTSWebSocketClient | None]:
    """初始化可选 WebSocket 客户端（ASR + TTS）。"""
    asr_ws = None
    if settings.asr_use_ws:
        asr_ws = ASRWebSocketClient(settings.asr_ws_url)
        await asr_ws.start()
        logger.info("ASR WS client → %s", settings.asr_ws_url)

    tts_ws = None
    if settings.tts_use_ws:
        tts_ws = TTSWebSocketClient(settings.tts_ws_url)
        await tts_ws.start()
        logger.info("TTS WS client → %s", settings.tts_ws_url)

    return asr_ws, tts_ws


# ═══════════════════════════════════════════════════════════════════
# 生命周期
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期：按顺序初始化所有服务，yield 后清理。"""
    global _initialized, _streaming_handler

    logger.info("══════════════════════════════════════")
    logger.info("  Agent Orchestrator starting up")
    logger.info("══════════════════════════════════════")

    # ── ① 核心服务 ──
    assembler, mcp, tts, asr = await _init_core_services()

    # ── ② 可选 gRPC 客户端 ──
    asr_grpc, tts_grpc = await _init_grpc_clients()

    # ── ③ 可选 WebSocket 客户端 ──
    asr_ws, tts_ws = await _init_ws_clients()

    # ── ④ 注入 flow.py 服务单例 ──
    set_services(assembler, mcp, tts, asr, tts_grpc=tts_grpc, asr_grpc=asr_grpc,
                 tts_ws=tts_ws, asr_ws=asr_ws)

    # ── ⑤ ESL 连接 + 事件订阅 ──
    esl = ESLClient(host=settings.esl_host, port=settings.esl_port, password=settings.esl_password)
    from src.ws.esl_events import register_esl_event_handlers
    register_esl_event_handlers(esl, _call_registry)

    subscribed_events = ["CHANNEL_HANGUP", "CHANNEL_ANSWER"]
    try:
        await esl.start()
        await esl.subscribe(subscribed_events)
        logger.info("ESL connected to %s:%d, subscribed to %s",
                     settings.esl_host, settings.esl_port, ", ".join(subscribed_events))
    except Exception as e:
        logger.warning("ESL connection failed (background reconnect started): %s", e)

    # ── ⑥ 创建 StreamingCallHandler ──
    from src.ws.handler import StreamingCallHandler

    denoiser = create_denoiser()
    apm = create_audio_processing(settings)
    vad_factory = lambda: create_vad(settings)

    _streaming_handler = StreamingCallHandler(
        pre_llm_fn=run_pre_llm_phase,
        streaming_fn=run_streaming_pipeline,
        esl=esl,
        handoff_extension=settings.handoff_extension,
        registry=_call_registry,
        vad_factory=vad_factory,
        barge_in_min_audio_bytes=settings.barge_in_min_audio_bytes,
        jitter_target_depth=settings.jitter_target_depth,
        jitter_max_depth=settings.jitter_max_depth,
        denoiser=denoiser,
        apm=apm,
        asr_grpc_client=asr_grpc,
        use_grpc_streaming=settings.asr_use_grpc,
        asr_ws_client=asr_ws,
        use_ws_streaming=settings.asr_use_ws,
        use_streaming_asr=settings.asr_streaming_enabled,
        tts_prebuffer_frames=settings.tts_prebuffer_frames,
    )

    # ── ⑦ 外呼执行器（进程内 asyncio，tick 调度）──
    global _outbound_executor
    from src.outbound.executor import OutboundExecutor
    _outbound_executor = OutboundExecutor(esl, settings)
    _outbound_executor.start()

    _initialized = True
    _log_startup_summary()

    yield

    # ── 关闭 ──
    if _outbound_executor is not None:
        await _outbound_executor.stop()
        _outbound_executor = None
    await _shutdown(mcp, asr_grpc, tts_grpc, asr_ws, tts_ws, asr, tts, esl)
    _initialized = False


def _log_startup_summary() -> None:
    """输出启动配置摘要。"""
    logger.info("──────────────────────────────────────")
    logger.info("  VAD: %s", settings.vad_type)
    logger.info("  Denoise: %s", settings.denoise_enabled or "disabled")
    logger.info("  AEC/APM: enabled=%s type=%d ns=%d agc=%d delay=%dms",
                settings.aec_enabled, settings.aec_type,
                settings.aec_ns_level, settings.aec_agc_type, settings.aec_system_delay_ms)
    logger.info("  ASR transport: grpc=%s ws=%s streaming=%s",
                settings.asr_use_grpc, settings.asr_use_ws, settings.asr_streaming_enabled)
    logger.info("  TTS transport: grpc=%s ws=%s streaming=%s",
                settings.tts_use_grpc, settings.tts_use_ws, settings.tts_streaming_enabled)
    logger.info("  Splitter: min=%d timeout=%.1fs eager_first=%s",
                settings.splitter_min_length, settings.splitter_flush_timeout, settings.splitter_eager_first)
    logger.info("  Audio: sample_rate=%d gain=%.1fx jitter=%d-%d",
                settings.media_sample_rate, settings.audio_gain,
                settings.jitter_target_depth, settings.jitter_max_depth)
    logger.info("  Barge-in: min_bytes=%d", settings.barge_in_min_audio_bytes)
    logger.info("──────────────────────────────────────")
    logger.info("  Agent Orchestrator ready (port %d)", settings.media_ws_port)
    logger.info("══════════════════════════════════════")


async def _shutdown(
    mcp: MCPClient,
    asr_grpc: ASRGrpcClient | None,
    tts_grpc: TTSGrpcClient | None,
    asr_ws: ASRWebSocketClient | None,
    tts_ws: TTSWebSocketClient | None,
    asr: ASRClient,
    tts: TTSClient,
    esl: ESLClient,
) -> None:
    """按逆序关闭所有服务。"""
    logger.info("Shutting down...")

    # 关闭 ESL
    try:
        await esl.close()
        logger.info("ESL closed")
    except Exception:
        pass

    # 关闭可选客户端
    for name, client in [("ASR gRPC", asr_grpc), ("TTS gRPC", tts_grpc),
                          ("ASR WS", asr_ws), ("TTS WS", tts_ws)]:
        if client:
            try:
                await client.close()
                logger.info("%s client closed", name)
            except Exception:
                pass

    # 关闭核心客户端
    for name, client in [("MCP", mcp), ("ASR", asr), ("TTS", tts)]:
        try:
            await client.close()
            logger.info("%s client closed", name)
        except Exception:
            pass

    logger.info("══════════════════════════════════════")
    logger.info("  Agent Orchestrator shut down")
    logger.info("══════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════════════

app = FastAPI(title="Agent Orchestrator", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok" if _initialized else "initializing"}


@app.post("/calls/{fs_uuid}/archive-recording")
async def archive_recording(fs_uuid: str):
    """手动归档整通录音（自动归档失败的兜底入口）。

    自动归档 `_archive_recording`（CHANNEL_HANGUP 后 fire-and-forget）在 MinIO 不可用时静默
    跳过；本接口提供事后补归档。链路与 _archive_recording 一致（读 FS 本地 wav → upload_recording
    → insert_artifact），区别仅三元组来源：自动=ActiveCallRegistry（挂断后清空），手动=DB 反查
    call_session。无鉴权（内网信任，与 /media 一致），租户隔离由调用方 console 转发前保证。
    """
    session = await repository.get_call_session_by_fs_uuid(fs_uuid)
    if session is None:
        return JSONResponse(status_code=404, content={"error": "call session not found"})

    existing = await repository.get_artifact_by_call_kind(fs_uuid, "recording")
    if existing is not None:
        return JSONResponse(
            status_code=409, content={"error": "already archived", "objectKey": existing.uri})

    path = os.path.join(settings.recordings_dir, f"{fs_uuid}.wav")
    if not os.path.exists(path):
        return JSONResponse(status_code=410, content={"error": "recording file not found"})
    try:
        with open(path, "rb") as f:
            wav_bytes = f.read()
    except OSError as e:
        logger.warning("[%s] manual archive read failed: %s", fs_uuid, e)
        return JSONResponse(status_code=410, content={"error": "recording file not found"})

    key = await minio_storage.upload_recording(
        fs_uuid, wav_bytes, session.biz_type, session.tenant_id)
    if key is None:
        return JSONResponse(status_code=502, content={"error": "minio unavailable"})

    try:
        await repository.insert_artifact(
            call_id=fs_uuid, fs_uuid=fs_uuid, biz_type=session.biz_type,
            user_id=session.user_id, user_key=session.user_key,
            kind="recording", storage="minio", uri=key,
            size_bytes=len(wav_bytes), content_type="audio/wav",
        )
    except Exception as e:
        logger.error("[%s] manual archive insert_artifact failed: %s", fs_uuid, e)
        return JSONResponse(status_code=500, content={"error": "failed to persist artifact"})

    logger.info("[%s] manual recording archived: %s (%d bytes)", fs_uuid, key, len(wav_bytes))
    return JSONResponse(status_code=200, content={"objectKey": key})


@app.websocket("/media/{call_id}")
async def ws_media_fork(websocket: WebSocket, call_id: str):
    """uuid_audio_fork 端点 — FreeSWITCH 作为 WS 客户端连接。

    流程:
      1. FreeSWITCH CHANNEL_ANSWER → ESL handler → uuid_audio_fork start → FS 连接本端点
      2. 双向音频流: JitterBuffer → VAD → ASR → LLM 流式 → 句级 TTS → 回传
      3. CHANNEL_HANGUP → uuid_audio_fork stop → 清理
    """
    if _streaming_handler is None:
        await websocket.close(code=503, reason="Service not initialized")
        return

    call = _call_registry.get(call_id)
    biz_type = call.biz_type if call else "marketing"
    user_key = call.user_key if call else ""
    tenant_id = call.tenant_id if call else "default"
    scenario = call.scenario if call else "default"

    await _streaming_handler.handle(websocket, call_id, biz_type, user_key, tenant_id, scenario)
