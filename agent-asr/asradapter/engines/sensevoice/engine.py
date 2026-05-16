import asyncio
import logging
import os
import tempfile
import uuid

from asradapter.base import ASREngine, ASRResult

logger = logging.getLogger(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/sensevoice/models/SenseVoiceSmall")
SENSEVOICE_LANGUAGE = os.environ.get("SENSEVOICE_LANGUAGE", "zh")
SENSEVOICE_MAX_CONCURRENT = int(os.environ.get("SENSEVOICE_MAX_CONCURRENT", "50"))


class SenseVoiceASREngine(ASREngine):
    def __init__(self):
        self._language = SENSEVOICE_LANGUAGE
        self._semaphore = asyncio.Semaphore(SENSEVOICE_MAX_CONCURRENT)
        self._model = None

    async def load_model(self):
        from funasr import AutoModel
        logger.info("Loading SenseVoice model from %s", MODEL_DIR)
        self._model = AutoModel(model=MODEL_DIR, disable_update=True)
        logger.info("SenseVoice model loaded")

    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        if self._model is None:
            raise RuntimeError("SenseVoice model not loaded")

        async with self._semaphore:
            tmp_path = os.path.join(tempfile.gettempdir(), f"asr_{uuid.uuid4().hex}.wav")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(audio_stream)

                result = self._model.generate(
                    input=tmp_path,
                    language=params.get("language", self._language),
                    batch_size_s=300,
                )
                text = result[0]["text"] if result else ""
                return ASRResult(text=text, confidence=0.95, is_final=True)
            except Exception as e:
                logger.error("SenseVoice recognition failed: %s", e)
                raise RuntimeError(f"SenseVoice recognition failed: {e}") from e
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    async def health_check(self) -> bool:
        return self._model is not None


Engine = SenseVoiceASREngine
