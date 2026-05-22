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


@app.get("/healthz")
async def healthz():
    healthy = await engine.health_check() if engine else False
    return {"status": "ok" if healthy else "degraded"}


@app.post("/tts/synthesize")
async def synthesize(text: str = Form(...), params: str = Form("{}")):
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


@app.post("/tts/synthesize_json")
async def synthesize_json(text: str = Form(...), params: str = Form("{}")):
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
