"""Agent Orchestrator — FastAPI HTTP 服务"""
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Form, WebSocket, Query
from pydantic import BaseModel

from fastapi import HTTPException

from src.config import settings
from src.graph.flow import create_call_graph, set_services, CallGraphState
from src.memory.assembler import MemoryAssembler
from src.clients.mcp import MCPClient
from src.clients.tts import TTSClient
from src.clients.asr import ASRClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

_graph = None
_initialized = False
_ws_handler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph, _initialized, _ws_handler

    assembler = MemoryAssembler()
    mcp = MCPClient(settings.mcp_server_url, settings.mcp_transport)
    await mcp.initialize()
    tts = TTSClient(settings.tts_adapter_url)
    asr = ASRClient(settings.asr_adapter_url)
    set_services(assembler, mcp, tts, asr)

    _graph = create_call_graph()
    _initialized = True

    from src.ws.handler import CallWebSocketHandler
    _ws_handler = CallWebSocketHandler(turn_fn=invoke_turn)

    logger.info("=== Agent Orchestrator 启动 ===")

    yield

    await mcp.close()
    _initialized = False
    logger.info("=== Agent Orchestrator 关闭 ===")


app = FastAPI(title="Agent Orchestrator", lifespan=lifespan)


class SpeechRequest(BaseModel):
    call_id: str
    biz_type: str
    user_key: str
    text: str
    minio_key: str | None = None


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


async def invoke_pipeline(
    call_id: str, biz_type: str, user_key: str, text: str, minio_key: str | None = None
) -> dict:
    """调用 LangGraph pipeline（文本输入），供 HTTP /call/speech 使用。"""
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


async def invoke_turn(
    call_id: str, biz_type: str, user_key: str, audio_bytes: bytes
) -> dict:
    """调用 LangGraph pipeline（音频输入），供 WebSocket 和 /call/turn 使用。"""
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


@app.get("/healthz")
async def healthz():
    return {"status": "ok" if _initialized else "initializing"}


@app.post("/call/speech")
async def handle_speech(request: SpeechRequest):
    """文本输入端点。"""
    if _graph is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return await invoke_pipeline(
        call_id=request.call_id, biz_type=request.biz_type,
        user_key=request.user_key, text=request.text, minio_key=request.minio_key,
    )


@app.post("/call/turn")
async def handle_turn(audio: UploadFile, params: str = Form("{}")):
    """音频输入端点 — 全流程 (ASR → pipeline → TTS)。"""
    if _graph is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    audio_bytes = await audio.read()
    params_dict = json.loads(params)
    call_id = params_dict.get("call_id", "")
    biz_type = params_dict.get("biz_type", "marketing")
    user_key = params_dict.get("user_key", "")

    turn_result = await invoke_turn(call_id, biz_type, user_key, audio_bytes)
    return {
        "text": turn_result.get("text", ""),
        "confidence": 0.95,
        "is_final": True,
    }


@app.websocket("/ws/call")
async def ws_call(
    websocket: WebSocket,
    call_id: str = Query(...),
    biz_type: str = Query(default="marketing"),
    user_key: str = Query(default=""),
):
    """WebSocket 双向音频流端点 — FreeSWITCH mod_audio_fork 直连。"""
    await _ws_handler.handle(websocket, call_id, biz_type, user_key)
