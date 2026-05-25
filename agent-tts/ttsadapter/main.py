import asyncio
import os
import json
import base64
import yaml
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form
from fastapi.responses import Response, JSONResponse
from ttsadapter.config import load_tts_engine
from ttsadapter.store import storage
from ttsadapter.grpc_server import serve_grpc

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
    call_id = params_dict.get("call_id", "")
    result = await engine.synthesize(text, params_dict)
    minio_key = storage.build_object_key(prefix="tts", call_id=call_id)
    if minio_key:
        asyncio.create_task(storage.upload_audio_async(result.audio, minio_key))
    headers = {}
    if minio_key:
        headers["X-Minio-Key"] = minio_key
    return Response(content=result.audio, media_type=result.content_type, headers=headers)


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
        {"audio": str(base64), "content_type": str, "duration_ms": int, "minio_key": str|None}
    """
    params_dict = json.loads(params)
    call_id = params_dict.get("call_id", "")
    result = await engine.synthesize(text, params_dict)
    minio_key = storage.build_object_key(prefix="tts", call_id=call_id)
    if minio_key:
        asyncio.create_task(storage.upload_audio_async(result.audio, minio_key))
    audio_b64 = base64.b64encode(result.audio).decode("ascii") if result.audio else ""
    resp = {
        "audio": audio_b64,
        "content_type": result.content_type,
        "duration_ms": result.duration_ms,
    }
    if minio_key:
        resp["minio_key"] = minio_key
    return JSONResponse(content=resp)
