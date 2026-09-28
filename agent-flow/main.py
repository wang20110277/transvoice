"""Agent Orchestrator — FastAPI WebSocket 入口。

事件驱动架构：
  FreeSWITCH CHANNEL_ANSWER → ESL handler → uuid_audio_fork → WS /media/{uuid}
  → AgentSession 无头管线（livekit-agents）：TelephonyAudioInput（Jitter→APM/denoise→增益）
  → TransvoiceSTT（FSMN 多 final）→ TransvoiceAgent（LangGraph 节点 ①-⑥）
  → TransvoiceTTS → TelephonyAudioOutput（30ms 匀速）→ 回传

服务启动顺序（lifespan）：
  ① 核心服务 (MCP, Memory)
  ② 注入 flow.py 服务单例
  ③ ESL 连接 + 事件订阅
  ④ 外呼执行器
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
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from src.config import settings
from src.storage import minio_storage, repository
from src.graph.flow import set_services
from src.memory.assembler import MemoryAssembler
from src.clients.mcp import MCPClient
from src.clients.esl import ESLClient
from src.ws.registry import ActiveCallRegistry
from src.ws.denoise import create_denoiser
from src.ws.audio_processing import create_audio_processing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 模块级状态 — 由 lifespan 管理 ──

_initialized = False
_esl = None  # ESLClient 单例，ws_media_fork 供 TransvoiceAgent 终端动作使用
_call_registry = ActiveCallRegistry()
_outbound_executor = None  # OutboundExecutor 单例，lifespan 启停


# ═══════════════════════════════════════════════════════════════════
# 服务初始化
# ═══════════════════════════════════════════════════════════════════

async def _init_core_services() -> tuple[MemoryAssembler, MCPClient]:
    """初始化核心服务：Memory、MCP。ASR/TTS 由 voice 插件按 call 自建 WS 连接。"""
    assembler = MemoryAssembler()
    logger.info("MemoryAssembler initialized")

    mcp = MCPClient(settings.mcp_server_url, settings.mcp_transport)
    try:
        await asyncio.wait_for(mcp.initialize(), timeout=10)
        logger.info("MCP client connected to %s", settings.mcp_server_url)
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("MCP init failed (identity/credit queries will be skipped): %s", e)

    return assembler, mcp


# ═══════════════════════════════════════════════════════════════════
# 生命周期
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期：按顺序初始化所有服务，yield 后清理。"""
    global _initialized, _esl, _outbound_executor

    logger.info("══════════════════════════════════════")
    logger.info("  Agent Orchestrator starting up")
    logger.info("══════════════════════════════════════")

    # ── ① 核心服务 ──
    assembler, mcp = await _init_core_services()

    # ── ② 注入 flow.py 服务单例 ──
    set_services(assembler, mcp)
    logger.info("ASR/TTS WS clients: per-call, owned by voice plugins (%s / %s)",
                settings.asr_ws_url, settings.tts_ws_url)

    # ── ③ ESL 连接 + 事件订阅 ──
    _esl = ESLClient(host=settings.esl_host, port=settings.esl_port, password=settings.esl_password)
    from src.ws.esl_events import register_esl_event_handlers
    register_esl_event_handlers(_esl, _call_registry)

    subscribed_events = ["CHANNEL_HANGUP", "CHANNEL_ANSWER"]
    try:
        await _esl.start()
        await _esl.subscribe(subscribed_events)
        logger.info("ESL connected to %s:%d, subscribed to %s",
                     settings.esl_host, settings.esl_port, ", ".join(subscribed_events))
    except Exception as e:
        logger.warning("ESL connection failed (background reconnect started): %s", e)

    # ── ④ 外呼执行器（进程内 asyncio，tick 调度）──
    from src.outbound.executor import OutboundExecutor
    _outbound_executor = OutboundExecutor(_esl, settings)
    _outbound_executor.start()

    _initialized = True
    _log_startup_summary()

    yield

    # ── 关闭 ──
    if _outbound_executor is not None:
        await _outbound_executor.stop()
        _outbound_executor = None
    await _shutdown(mcp, _esl)
    _esl = None
    _initialized = False


def _log_startup_summary() -> None:
    """输出启动配置摘要。"""
    logger.info("──────────────────────────────────────")
    logger.info("  Pipeline: livekit AgentSession (TransvoiceSTT → Agent → TransvoiceTTS)")
    logger.info("  Endpointing: min=%.1fs max=%.1fs  Interruption: min_duration=%.1fs",
                settings.endpointing_min_delay, settings.endpointing_max_delay,
                settings.interruption_min_duration)
    logger.info("  Denoise: %s", settings.denoise_enabled or "disabled")
    logger.info("  AEC/APM: enabled=%s type=%d ns=%d agc=%d delay=%dms",
                settings.aec_enabled, settings.aec_type,
                settings.aec_ns_level, settings.aec_agc_type, settings.aec_system_delay_ms)
    logger.info("  Audio: sample_rate=%d gain=%.1fx jitter=%d-%d prebuffer=%d frames",
                settings.media_sample_rate, settings.audio_gain,
                settings.jitter_target_depth, settings.jitter_max_depth,
                settings.tts_prebuffer_frames)
    logger.info("──────────────────────────────────────")
    logger.info("  Agent Orchestrator ready (port %d)", settings.media_ws_port)
    logger.info("══════════════════════════════════════")


async def _shutdown(mcp: MCPClient, esl: ESLClient) -> None:
    """按逆序关闭所有服务。"""
    logger.info("Shutting down...")

    # 关闭 ESL
    try:
        await esl.close()
        logger.info("ESL closed")
    except Exception:
        pass

    # 关闭 MCP
    try:
        await mcp.close()
        logger.info("MCP client closed")
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
    """uuid_audio_fork 端点 — FreeSWITCH 作为 WS 客户端连接（AgentSession 无头管线）。

    1. CHANNEL_ANSWER → ESL handler → audio_fork start → FS 连接本端点
    2. 上行帧 push 进 TelephonyAudioInput；下行由 TelephonyAudioOutput 匀速回传
    3. CHANNEL_HANGUP / WS 断开 → session.aclose
    """
    if not _initialized:
        await websocket.close(code=503, reason="Service not initialized")
        return

    call = _call_registry.get(call_id)
    biz_type = call.biz_type if call else "marketing"
    user_key = call.user_key if call else ""
    tenant_id = call.tenant_id if call else "default"
    scenario = call.scenario if call else "default"

    from src.voice.agent import CallContext
    from src.voice.session import build_agent_session

    ctx = CallContext(
        call_id=call_id, biz_type=biz_type, user_key=user_key,
        tenant_id=tenant_id, scenario=scenario,
        call_task_vars=call.call_target_vars if call else {},
        handoff_extension=settings.handoff_extension,
    )
    # APM/denoiser 每 call 实例化（内部持有逐帧自适应状态，跨 call 复用会串扰）
    session, agent = build_agent_session(
        ctx=ctx, websocket=websocket, registry=_call_registry, esl=_esl,
        apm=create_audio_processing(settings), denoiser=create_denoiser(),
    )
    audio_input = session.input.audio
    await websocket.accept()
    try:
        # start 在 try 内：SDK start 失败时 aclose 对未完成启动的 session 是安全 no-op
        # （_aclose_locked 对 not _started 直接 return），清理路径仍完整执行
        await session.start(agent)
        logger.info("[%s] AgentSession started (tenant=%s biz_type=%s scenario=%s)",
                    call_id, tenant_id, biz_type, scenario)

        while True:
            if call and call.cancel.is_set():
                logger.info("[%s] CHANNEL_HANGUP, stopping", call_id)
                break
            data = await websocket.receive()
            if "bytes" in data and data["bytes"]:
                audio_input.push_bytes(data["bytes"])
            elif "text" in data and data["text"]:
                if json.loads(data["text"]).get("type") == "stop":
                    logger.info("[%s] WS stop received", call_id)
                    break
    except WebSocketDisconnect:
        logger.info("[%s] WS disconnected", call_id)
    except RuntimeError:
        logger.info("[%s] WS already disconnected", call_id)
    finally:
        audio_input.close()
        try:
            await asyncio.wait_for(session.aclose(), timeout=10.0)
        except Exception as e:
            logger.warning("[%s] session aclose: %s", call_id, e)
        # TransvoiceTTS 自管每 call 一条共享 WS（_SharedTtsConnection 跨流存活，
        # SDK aclose 只拆 activity/流，不关插件实例）——不显式关则每通话泄漏一条
        # agent-tts WS + 阻塞在 recv() 的 reader task。getattr 防御：session.tts
        # 可能是测试替身（lk_tts.TTS 基类 aclose 为 no-op，调用无害）。
        tts_plugin = getattr(session, "tts", None)
        tts_aclose = getattr(tts_plugin, "aclose", None)
        if tts_aclose is not None:
            try:
                await asyncio.wait_for(tts_aclose(), timeout=10.0)
            except Exception as e:
                logger.warning("[%s] tts plugin aclose: %s", call_id, e)
        # 主动收口：stop/cancel 路径下 FS 可能尚未断开 WS
        try:
            await websocket.close()
        except Exception:
            pass
        if _call_registry.get(call_id):
            _call_registry.unregister(call_id)
        logger.info("[%s] AgentSession closed", call_id)
