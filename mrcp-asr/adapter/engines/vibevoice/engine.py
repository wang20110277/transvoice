import asyncio
import logging
from adapter.base import ASREngine, ASRResult

logger = logging.getLogger(__name__)


class VibeVoiceASREngine(ASREngine):
    def __init__(self):
        self._model = None
        self._model_loaded = False
        self._semaphore = asyncio.Semaphore(50)

    async def load_model(self):
        """加载 VibeVoice ASR 模型"""
        logger.info("VibeVoice ASR model loading (stub)")
        self._model_loaded = True

    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        """识别音频流"""
        async with self._semaphore:
            if not self._model_loaded:
                raise RuntimeError("ASR model not loaded")
            return ASRResult(text="", confidence=0.0, is_final=True)

    async def health_check(self) -> bool:
        return self._model_loaded


# Alias for config.py's load_asr_engine which expects class named "Engine"
Engine = VibeVoiceASREngine
