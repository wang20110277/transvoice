"""CosyVoice TTS inference server — FastAPI wrapper"""
import io
import os
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cosyvoice-server")

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/cosyvoice/pretrained_models/CosyVoice2-0.5B")
AUDIO_DIR = os.environ.get("AUDIO_DIR", "/data/audio")
TTS_CACHE_DIR = os.environ.get("TTS_CACHE_DIR", "/data/tts_cache")
PORT = int(os.environ.get("PORT", "10096"))

cosyvoice_model = None


class TTSRequest(BaseModel):
    text: str
    speaker_id: str = "中文女"
    speed: float = 1.0
    volume: float = 0
    pitch: float = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global cosyvoice_model
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(TTS_CACHE_DIR, exist_ok=True)
    logger.info(f"Loading CosyVoice model from {MODEL_DIR}")
    sys.path.insert(0, "/opt/cosyvoice/runtime")
    from cosyvoice.cli.cosyvoice import CosyVoice2
    cosyvoice_model = CosyVoice2(MODEL_DIR)
    logger.info("Model loaded")
    yield


app = FastAPI(title="CosyVoice TTS Server", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model": "CosyVoice2-0.5B"}


@app.post("/tts")
async def synthesize(req: TTSRequest):
    if cosyvoice_model is None:
        return Response(content=b"", media_type="audio/wav", status_code=503)

    buffer = io.BytesIO()
    for chunk in cosyvoice_model.inference_sft(req.text, req.speaker_id, stream=False):
        import soundfile as sf
        sf.write(buffer, chunk["tts_speech"].numpy().flatten(), 22050, format="WAV")
        break  # first chunk contains full audio for non-streaming

    buffer.seek(0)
    return Response(content=buffer.read(), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
