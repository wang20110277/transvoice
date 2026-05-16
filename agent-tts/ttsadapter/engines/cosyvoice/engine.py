import asyncio
import hashlib
import io
import logging
import os

from ttsadapter.base import TTSEngine, TTSResult
from cosyvoice.cli.cosyvoice import CosyVoice2

logger = logging.getLogger(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/cosyvoice/pretrained_models/CosyVoice2-0.5B")
COSYVOICE_MAX_CONCURRENT = int(os.environ.get("COSYVOICE_MAX_CONCURRENT", "30"))

BIZ_TYPE_PROFILES = {
    "customer_service": {"voice_id": "中文女", "speed": 0, "volume": 0, "pitch": 0},
    "collection": {"voice_id": "中文男", "speed": -1, "volume": 1, "pitch": -1},
    "marketing": {"voice_id": "中文女", "speed": 1, "volume": 0, "pitch": 1},
}

DEFAULT_PROFILE = BIZ_TYPE_PROFILES["customer_service"]


class CosyVoiceTTSEngine(TTSEngine):
    def __init__(self):
        self._cache_dir = "/data/tts_cache"
        self._semaphore = asyncio.Semaphore(COSYVOICE_MAX_CONCURRENT)
        self._model = None

    async def load_model(self):
        import sys
        runtime_path = os.environ.get("COSYVOICE_RUNTIME", "/opt/cosyvoice/runtime")
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
        logger.info("Loading CosyVoice2 model from %s", MODEL_DIR)
        self._model = CosyVoice2(MODEL_DIR)
        logger.info("CosyVoice2 model loaded")

    def _get_profile(self, params: dict) -> dict:
        biz_type = params.get("biz_type", "customer_service")
        return BIZ_TYPE_PROFILES.get(biz_type, DEFAULT_PROFILE)

    def _cache_key(self, text: str, profile: dict) -> str:
        raw = f"{profile['voice_id']}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, biz_type: str, key: str) -> str:
        return os.path.join(self._cache_dir, biz_type, f"{key}.wav")

    async def synthesize(self, text: str, params: dict) -> TTSResult:
        if self._model is None:
            raise RuntimeError("CosyVoice model not loaded")

        async with self._semaphore:
            profile = self._get_profile(params)
            biz_type = params.get("biz_type", "customer_service")
            cache_path = self._cache_path(biz_type, self._cache_key(text, profile))

            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return TTSResult(audio=f.read())

            try:
                import soundfile as sf
                buffer = io.BytesIO()
                for chunk in self._model.inference_sft(text, profile["voice_id"], stream=False):
                    sf.write(buffer, chunk["tts_speech"].numpy().flatten(), 22050, format="WAV")
                    break

                buffer.seek(0)
                audio = buffer.read()

                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "wb") as f:
                    f.write(audio)

                return TTSResult(audio=audio)
            except Exception as e:
                logger.error("CosyVoice synthesis failed: %s", e)
                raise RuntimeError(f"CosyVoice synthesis failed: {e}") from e

    async def health_check(self) -> bool:
        return self._model is not None


Engine = CosyVoiceTTSEngine
