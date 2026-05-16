"""ASR adapter HTTP client — 调用 agent-asr 的 /asr/recognize 端点"""
import json
import logging
import httpx

logger = logging.getLogger(__name__)


class ASRClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def recognize(self, audio_bytes: bytes, call_id: str) -> dict | None:
        """调用 agent-asr 识别音频，返回 {text, confidence, is_final, minio_key} 或 None。"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/asr/recognize",
                    files={"audio": ("audio.wav", audio_bytes, "audio/wav")},
                    data={"params": json.dumps({"call_id": call_id})},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error("ASR 识别失败 call_id=%s: %s", call_id, e)
            return None
