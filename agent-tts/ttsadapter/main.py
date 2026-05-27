import os
import json
import base64
import yaml
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, WebSocket
from fastapi.responses import Response, JSONResponse
from ttsadapter.config import load_tts_engine
from ttsadapter.grpc_server import serve_grpc
from ttsadapter.ws_server import TTSWebSocketHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

engine = None
_grpc_server = None


def _load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, _grpc_server
    config = _load_config()
    engine = load_tts_engine(config["engine"]["tts"])
    if hasattr(engine, "load_model"):
        await engine.load_model()
    logger.info(f"TTS engine loaded: {config['engine']['tts']}")

    # Start gRPC server alongside FastAPI
    _grpc_server = await serve_grpc(engine)

    yield

    if _grpc_server:
        await _grpc_server.stop(grace=2)
        logger.info("gRPC TTS server stopped")


app = FastAPI(title="TTS Adapter Service", lifespan=lifespan)


# ── HTTP 接口 ────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    """健康检查 — 判断 TTS 引擎是否可用。

    Returns:
        {"status": "ok" | "degraded"}
    """
    healthy = await engine.health_check() if engine else False
    return {"status": "ok" if healthy else "degraded"}


@app.post("/tts/synthesize-binary")
async def synthesize_binary(text: str = Form(...), params: str = Form("{}")):
    """语音合成（二进制响应）— 文本转音频，直接返回音频二进制。

    Args (application/x-www-form-urlencoded):
        text: 待合成文本
        params: JSON 字符串，可选字段：
            - call_id (str): 通话ID，用于 MinIO 音频归档
            - biz_type (str): 业务类型，决定音色/语速等参数
            - voice_id (str): 指定音色ID

    Returns:
        二进制音频流（WAV/PCM），Content-Type 由引擎决定。
        若启用 MinIO，响应头包含 X-Minio-Key。
    """
    params_dict = json.loads(params)
    result = await engine.synthesize(text, params_dict)
    return Response(content=result.audio, media_type=result.content_type)


@app.post("/tts/synthesize-json")
async def synthesize_json(text: str = Form(...), params: str = Form("{}")):
    """语音合成（JSON 响应）— 文本转音频，返回 base64 编码的 JSON。

    Args (application/x-www-form-urlencoded):
        text: 待合成文本
        params: JSON 字符串，可选字段：
            - call_id (str): 通话ID，用于 MinIO 音频归档
            - biz_type (str): 业务类型，决定音色/语速等参数
            - voice_id (str): 指定音色ID

    Returns:
        {"audio": str(base64), "content_type": str, "duration_ms": int}
    """
    params_dict = json.loads(params)
    result = await engine.synthesize(text, params_dict)
    audio_b64 = base64.b64encode(result.audio).decode("ascii") if result.audio else ""
    return JSONResponse(content={
        "audio": audio_b64,
        "content_type": result.content_type,
        "duration_ms": result.duration_ms,
    })


# ── WebSocket 接口 ───────────────────────────────────────────

@app.websocket("/ws/tts/streaming-synthesize")
async def ws_streaming_synthesize(websocket: WebSocket):
    """语音合成（WebSocket）— 文本转音频，支持连接复用。

    协议:
        发送 (客户端 → 服务端):
            - Text JSON 帧: {"type": "synthesize", "text": "...", "call_id": "...",
                             "biz_type": "...", "request_id": "..."}
        接收 (服务端 → 客户端):
            - Binary 帧: WAV 音频数据
            - Text JSON 帧: {"type": "result", "duration_ms": ..., "minio_key": "...", "request_id": "..."}
            - Text JSON 帧: {"type": "error", "message": "...", "request_id": "..."}
    """
    handler = TTSWebSocketHandler(engine)
    await handler.handle(websocket)
