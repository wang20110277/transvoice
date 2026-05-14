import asyncio
import logging
import os

import httpx

from asradapter.base import ASREngine, ASRResult

logger = logging.getLogger(__name__)

SENSEVOICE_API_URL = os.environ.get("SENSEVOICE_API_URL", "http://127.0.0.1:10095")
SENSEVOICE_TIMEOUT = int(os.environ.get("SENSEVOICE_TIMEOUT", "30"))
SENSEVOICE_LANGUAGE = os.environ.get("SENSEVOICE_LANGUAGE", "zh")
SENSEVOICE_MAX_CONCURRENT = int(os.environ.get("SENSEVOICE_MAX_CONCURRENT", "50"))


class SenseVoiceASREngine(ASREngine):
    def __init__(self):
        self._api_url = SENSEVOICE_API_URL
        self._timeout = SENSEVOICE_TIMEOUT
        self._language = SENSEVOICE_LANGUAGE
        self._semaphore = asyncio.Semaphore(SENSEVOICE_MAX_CONCURRENT)

    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        async with self._semaphore:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._api_url}/asr",
                        files={"audio": ("audio.wav", audio_stream, "audio/wav")},
                        data={"language": params.get("language", self._language)},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return ASRResult(
                        text=data.get("text", ""),
                        confidence=data.get("confidence", 0.0),
                        is_final=True,
                    )
            except Exception as e:
                logger.error(f"SenseVoice recognition failed: {e}")
                raise RuntimeError(f"SenseVoice recognition failed: {e}") from e

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._api_url}/health")
                return resp.status_code == 200
        except Exception as e:
            logger.warning(f"SenseVoice health check failed: {e}")
            return False


Engine = SenseVoiceASREngine
