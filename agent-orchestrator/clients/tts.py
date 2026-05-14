"""TTS adapter HTTP client — 调用 TTS adapter 的 /tts/synthesize_json 端点"""
import logging
import httpx

logger = logging.getLogger(__name__)


class TTSClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def synthesize(self, text: str, call_id: str, biz_type: str) -> dict | None:
        """调用 TTS adapter 合成语音，返回 {audio, minio_key, content_type} 或 None"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/tts/synthesize_json",
                    data={"text": text, "params": f'{{"call_id":"{call_id}","biz_type":"{biz_type}"}}'},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"TTS 合成失败 call_id={call_id}: {e}")
            return None
