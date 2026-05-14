import asyncio
import logging
import os

import httpx

from adapter.base import ASREngine, ASRResult

logger = logging.getLogger(__name__)

SENSEVOICE_API_URL = os.environ.get("SENSEVOICE_API_URL", "http://127.0.0.1:10095")
SENSEVOICE_TIMEOUT = int(os.environ.get("SENSEVOICE_TIMEOUT", "30"))
SENSEVOICE_LANGUAGE = os.environ.get("SENSEVOICE_LANGUAGE", "zh")
SENSEVOICE_MAX_CONCURRENT = int(os.environ.get("SENSEVOICE_MAX_CONCURRENCY", "50"))


class _Semaphore:
    """Async semaphore wrapper that allows instance-level ``__aenter__`` patching.

    ``asyncio.Semaphore`` uses C-level async slot dispatch, so setting an
    instance attribute ``__aenter__`` has no effect on ``async with``.  This
    wrapper stores a real ``asyncio.Semaphore`` and provides a Python-level
    ``__aenter__`` / ``__aexit__`` pair that *can* be monkey-patched by tests.

    A re-entry flag (``_in_aenter``) prevents infinite recursion when a test
    spy wraps the original bound method.
    """

    def __init__(self, value: int):
        self._sem = asyncio.Semaphore(value)
        self._in_aenter = False

    async def __aenter__(self):
        if self._in_aenter:
            return await self._sem.__aenter__()
        fn = self.__dict__.get("__aenter__")
        if fn is not None:
            self._in_aenter = True
            try:
                return await fn()
            finally:
                self._in_aenter = False
        return await self._sem.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._sem.__aexit__(exc_type, exc_val, exc_tb)


class SenseVoiceASREngine(ASREngine):
    def __init__(self):
        self._api_url = SENSEVOICE_API_URL
        self._timeout = SENSEVOICE_TIMEOUT
        self._language = SENSEVOICE_LANGUAGE
        self._semaphore = _Semaphore(SENSEVOICE_MAX_CONCURRENT)

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
