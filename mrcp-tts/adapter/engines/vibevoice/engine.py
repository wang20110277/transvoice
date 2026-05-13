import asyncio
import hashlib
import os
import logging
from adapter.base import TTSEngine, TTSResult

logger = logging.getLogger(__name__)

BIZ_TYPE_PROFILES = {
    "customer_service": {"voice_id": "cs_female_soft_01", "speed": 0, "volume": 0, "pitch": 0},
    "collection": {"voice_id": "col_male_serious_01", "speed": -1, "volume": 1, "pitch": -1},
    "marketing": {"voice_id": "mkt_female_lively_01", "speed": 1, "volume": 0, "pitch": 1},
}

DEFAULT_PROFILE = BIZ_TYPE_PROFILES["customer_service"]


class VibeVoiceTTSEngine(TTSEngine):
    def __init__(self):
        self._model = None
        self._model_loaded = False
        self._cache_dir = "/data/tts_cache"
        self._semaphore = asyncio.Semaphore(30)

    async def load_model(self):
        logger.info("VibeVoice TTS model loading (stub)")
        self._model_loaded = True

    def _get_profile(self, params: dict) -> dict:
        biz_type = params.get("biz_type", "customer_service")
        return BIZ_TYPE_PROFILES.get(biz_type, DEFAULT_PROFILE)

    def _cache_key(self, text: str, profile: dict) -> str:
        raw = f"{profile['voice_id']}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, biz_type: str, key: str) -> str:
        return os.path.join(self._cache_dir, biz_type, f"{key}.wav")

    async def synthesize(self, text: str, params: dict) -> TTSResult:
        async with self._semaphore:
            if not self._model_loaded:
                raise RuntimeError("TTS model not loaded")

            profile = self._get_profile(params)
            biz_type = params.get("biz_type", "customer_service")
            cache_path = self._cache_path(biz_type, self._cache_key(text, profile))

            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return TTSResult(audio=f.read())

            return TTSResult(audio=b"")

    async def health_check(self) -> bool:
        return self._model_loaded
