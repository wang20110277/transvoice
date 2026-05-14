import os
import json
import yaml
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Form
from adapter.config import load_asr_engine
from adapter import storage

logger = logging.getLogger(__name__)

engine = None
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
    global engine
    config = _load_config()
    engine = load_asr_engine(config["engine"]["asr"])
    if hasattr(engine, "load_model"):
        await engine.load_model()
    logger.info(f"ASR engine loaded: {config['engine']['asr']}")
    yield


app = FastAPI(title="ASR Adapter Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    healthy = await engine.health_check() if engine else False
    return {"status": "ok" if healthy else "degraded"}


@app.post("/asr/recognize")
async def recognize(audio: UploadFile, params: str = Form("{}")):
    audio_bytes = await audio.read()
    params_dict = json.loads(params)
    minio_key = storage.upload_audio(audio_bytes, prefix="asr", call_id=params_dict.get("call_id", ""))
    result = await engine.recognize(audio_bytes, params_dict)
    call_id = params_dict.get("call_id", "")
    _save_audio_meta(call_id, minio_key, result.text)
    resp = {"text": result.text, "confidence": result.confidence, "is_final": result.is_final}
    if minio_key:
        resp["minio_key"] = minio_key
    return resp


@app.get("/asr/audio/{call_id}")
async def get_audio_meta(call_id: str):
    meta = _audio_cache.get(call_id)
    if not meta:
        return {"error": "not found", "call_id": call_id}
    return {"call_id": call_id, "minio_key": meta["minio_key"], "text": meta["text"]}
