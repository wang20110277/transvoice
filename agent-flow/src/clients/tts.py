"""TTS adapter HTTP client — 调用 TTS adapter 的合成端点"""
import json
import logging
import httpx

logger = logging.getLogger(__name__)


class TTSClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
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

    async def synthesize(self, text: str, call_id: str, biz_type: str) -> dict | None:
        """调用 TTS adapter 合成语音，返回 {audio, minio_key, content_type} 或 None"""
        if self._client is None:
            return None
        try:
            resp = await self._client.post(
                f"{self._base_url}/tts/synthesize_json",
                data={"text": text, "params": json.dumps({"call_id": call_id, "biz_type": biz_type})},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("TTS 合成失败 call_id=%s: %s", call_id, e)
            return None

    async def synthesize_raw(self, text: str, call_id: str, biz_type: str) -> bytes | None:
        """调用 TTS adapter 合成语音，返回原始 WAV 字节（无 base64 编解码）。"""
        if self._client is None:
            return None
        try:
            resp = await self._client.post(
                f"{self._base_url}/tts/synthesize",
                data={"text": text, "params": json.dumps({"call_id": call_id, "biz_type": biz_type})},
            )
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.error("TTS 原始合成失败 call_id=%s: %s", call_id, e)
            return None
