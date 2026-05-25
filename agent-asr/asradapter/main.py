import asyncio
import os
import json
import yaml
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Form
from asradapter.config import load_asr_engine
from asradapter.store import storage
from asradapter.grpc_server import serve_grpc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

engine = None
_grpc_server = None
_audio_cache: OrderedDict[str, dict] = OrderedDict()
_CACHE_MAX = 10000


def _save_audio_meta(call_id: str, minio_key: str | None, text: str):
    if not call_id:
        return
    _audio_cache[call_id] = {"minio_key": minio_key, "text": text}
    if len(_audio_cache) > _CACHE_MAX:
        _audio_cache.popitem(last=False)


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
        {"text": str, "confidence": float, "is_final": bool, "minio_key": str|None}
    """
    audio_bytes = await audio.read()
    params_dict = json.loads(params)
    call_id = params_dict.get("call_id", "")
    minio_key = storage.build_object_key(prefix="asr", call_id=call_id)
    result = await engine.recognize(audio_bytes, params_dict)
    if minio_key:
        asyncio.create_task(storage.upload_audio_async(audio_bytes, minio_key))
    _save_audio_meta(call_id, minio_key, result.text)
    resp = {"text": result.text, "confidence": result.confidence, "is_final": result.is_final}
    if minio_key:
        resp["minio_key"] = minio_key
    return resp


@app.get("/asr/audio-meta/{call_id}")
async def get_audio_meta(call_id: str):
    """音频元数据查询 — 按通话ID查询音频存储信息（非音频本身，为 MinIO key + 识别文本）。

    Args:
        call_id: 通话唯一标识

    Returns:
        {"call_id": str, "minio_key": str|None, "text": str}
        或 {"error": "not found", "call_id": str}
    """
    meta = _audio_cache.get(call_id)
    if not meta:
        return {"error": "not found", "call_id": call_id}
    return {"call_id": call_id, "minio_key": meta["minio_key"], "text": meta["text"]}
