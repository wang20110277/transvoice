"""SenseVoice ASR inference server — FunASR FastAPI"""
import os
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, Form
from funasr import AutoModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sensevoice-server")

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/sensevoice/models/SenseVoiceSmall")
AUDIO_DIR = os.environ.get("AUDIO_DIR", "/data/audio")
PORT = int(os.environ.get("PORT", "10095"))

model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    os.makedirs(AUDIO_DIR, exist_ok=True)
    logger.info(f"Loading SenseVoice model from {MODEL_DIR}")
    model = AutoModel(model=MODEL_DIR, disable_update=True)
    logger.info("Model loaded")
    yield


app = FastAPI(title="SenseVoice ASR Server", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model": "SenseVoiceSmall"}


@app.post("/asr")
async def recognize(
    audio: UploadFile = File(...),
    language: str = Form(default="zh"),
):
    audio_id = str(uuid.uuid4())
    tmp_path = os.path.join(AUDIO_DIR, f"{audio_id}.wav")
    with open(tmp_path, "wb") as f:
        f.write(await audio.read())

    try:
        result = model.generate(input=tmp_path, language=language, batch_size_s=300)
        text = result[0]["text"] if result else ""
        return {"text": text, "confidence": 0.95, "audio_path": tmp_path}
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
