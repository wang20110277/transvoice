"""ASR adapter HTTP client — 调用 agent-asr 的 /asr/recognize-speech 端点"""
import json
import logging
import httpx

logger = logging.getLogger(__name__)


class ASRClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """初始化持久 HTTP 连接池"""
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        """关闭连接池"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def recognize(self, audio_bytes: bytes, call_id: str) -> dict | None:
        """调用 agent-asr 识别音频，返回 {text, confidence, is_final, minio_key} 或 None。"""
        if self._client is None:
            return None
        try:
            resp = await self._client.post(
                f"{self._base_url}/asr/recognize-speech",
                files={"audio": ("audio.wav", audio_bytes, "audio/wav")},
                data={"params": json.dumps({"call_id": call_id})},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("ASR 识别失败 call_id=%s: %s", call_id, e)
            return None
