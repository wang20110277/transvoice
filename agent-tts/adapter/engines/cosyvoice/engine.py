import asyncio
import hashlib
import logging
import os

import httpx

from adapter.base import TTSEngine, TTSResult

logger = logging.getLogger(__name__)

COSYVOICE_API_URL = os.environ.get("COSYVOICE_API_URL", "http://127.0.0.1:10096")
COSYVOICE_TIMEOUT = int(os.environ.get("COSYVOICE_TIMEOUT", "30"))
COSYVOICE_MAX_CONCURRENT = int(os.environ.get("COSYVOICE_MAX_CONCURRENT", "30"))

BIZ_TYPE_PROFILES = {
    "customer_service": {"voice_id": "中文女", "speed": 0, "volume": 0, "pitch": 0},
    "collection": {"voice_id": "中文男", "speed": -1, "volume": 1, "pitch": -1},
    "marketing": {"voice_id": "中文女", "speed": 1, "volume": 0, "pitch": 1},
}

DEFAULT_PROFILE = BIZ_TYPE_PROFILES["customer_service"]


class CosyVoiceTTSEngine(TTSEngine):
    def __init__(self):
        self._api_url = COSYVOICE_API_URL
        self._timeout = COSYVOICE_TIMEOUT
        self._cache_dir = "/data/tts_cache"
        self._semaphore = asyncio.Semaphore(COSYVOICE_MAX_CONCURRENT)

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
            profile = self._get_profile(params)
            biz_type = params.get("biz_type", "customer_service")
            cache_path = self._cache_path(biz_type, self._cache_key(text, profile))

            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return TTSResult(audio=f.read())

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._api_url}/tts",
                        json={
                            "text": text,
                            "speaker_id": profile["voice_id"],
                            "speed": profile["speed"],
                            "volume": profile["volume"],
                            "pitch": profile["pitch"],
                        },
                    )
                    resp.raise_for_status()
                    audio = resp.content

                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "wb") as f:
                    f.write(audio)

                return TTSResult(audio=audio)
            except Exception as e:
                logger.error(f"CosyVoice synthesis failed: {e}")
                raise RuntimeError(f"CosyVoice synthesis failed: {e}") from e

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._api_url}/health")
                return resp.status_code == 200
        except Exception as e:
            logger.warning(f"CosyVoice health check failed: {e}")
            return False


Engine = CosyVoiceTTSEngine
