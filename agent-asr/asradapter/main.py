import os
import json
import yaml
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Form, WebSocket
from asradapter.config import load_asr_engine
from asradapter.grpc_server import serve_grpc
from asradapter.ws_server import ASRWebSocketHandler

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
    engine = load_asr_engine(config["engine"]["asr"])
    if hasattr(engine, "load_model"):
        await engine.load_model()
    logger.info(f"ASR engine loaded: {config['engine']['asr']}")

    # Start gRPC server alongside FastAPI
    _grpc_server = await serve_grpc(engine)

    yield

    if _grpc_server:
        await _grpc_server.stop(grace=2)
        logger.info("gRPC ASR server stopped")


app = FastAPI(title="ASR Adapter Service", lifespan=lifespan)


# ── HTTP 接口 ────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    """健康检查 — 判断 ASR 引擎是否可用。

    Returns:
        {"status": "ok" | "degraded"}
    """
    healthy = await engine.health_check() if engine else False
    return {"status": "ok" if healthy else "degraded"}


@app.post("/asr/recognize-speech")
async def recognize_speech(audio: UploadFile, params: str = Form("{}")):
    """语音识别 — 上传音频文件，返回识别文本。

    Args (multipart/form-data):
        audio: 音频文件（PCM / WAV，8kHz/16kHz 16-bit mono）
        params: JSON 字符串，可选字段：
            - call_id (str): 通话ID，用于 MinIO 音频归档
            - language (str): 语言代码，默认 "zh"

    Returns:
        {"text": str, "confidence": float, "is_final": bool}
    """
    audio_bytes = await audio.read()
    params_dict = json.loads(params)
    result = await engine.recognize(audio_bytes, params_dict)
    return {"text": result.text, "confidence": result.confidence, "is_final": result.is_final}


# ── WebSocket 接口 ───────────────────────────────────────────

@app.websocket("/ws/asr/streaming-recognize")
async def ws_streaming_recognize(websocket: WebSocket):
    """流式语音识别（WebSocket）— 客户端逐帧发送音频，服务端在流结束时返回识别结果。

    协议:
        发送 (客户端 → 服务端):
            - Text JSON 帧: {"type": "config", "call_id": "xxx", "language": "zh"}
            - Binary 帧: PCM 16-bit 8kHz mono 音频数据
            - Text JSON 帧: {"type": "end"} 标记流结束
        接收 (服务端 → 客户端):
            - Text JSON 帧: {"type": "result", "text": "...", "confidence": 0.95, ...}
            - Text JSON 帧: {"type": "error", "message": "..."}
    """
    handler = ASRWebSocketHandler(engine)
    await handler.handle(websocket)
