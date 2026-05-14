import asyncio
import logging
import os

import httpx

from adapter.base import ASREngine, ASRResult

logger = logging.getLogger(__name__)

VIBEVOICE_ASR_API_URL = os.environ.get("VIBEVOICE_ASR_API_URL", "http://127.0.0.1:10090")
VIBEVOICE_ASR_TIMEOUT = int(os.environ.get("VIBEVOICE_ASR_TIMEOUT", "30"))
VIBEVOICE_ASR_MAX_CONCURRENT = int(os.environ.get("VIBEVOICE_ASR_MAX_CONCURRENT", "50"))


class VibeVoiceASREngine(ASREngine):
    def __init__(self):
        self._api_url = VIBEVOICE_ASR_API_URL
        self._timeout = VIBEVOICE_ASR_TIMEOUT
        self._semaphore = asyncio.Semaphore(VIBEVOICE_ASR_MAX_CONCURRENT)

    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        async with self._semaphore:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._api_url}/asr",
                        files={"audio": ("audio.wav", audio_stream, "audio/wav")},
                        data={"language": params.get("language", "zh")},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return ASRResult(
                        text=data.get("text", ""),
                        confidence=data.get("confidence", 0.0),
                        is_final=True,
                    )
            except Exception as e:
                logger.error(f"VibeVoice ASR recognition failed: {e}")
                raise RuntimeError(f"VibeVoice ASR recognition failed: {e}") from e

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._api_url}/health")
                return resp.status_code == 200
        except Exception as e:
            logger.warning(f"VibeVoice ASR health check failed: {e}")
            return False


Engine = VibeVoiceASREngine
