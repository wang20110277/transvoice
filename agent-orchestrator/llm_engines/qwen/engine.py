import logging
import httpx

logger = logging.getLogger(__name__)


class QwenEngine:
    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    async def invoke(self, prompt: str, schema: dict | None = None) -> str:
        payload = {
            "model": "qwen3.5-9b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 256,
        }
        if schema:
            payload["response_format"] = {"type": "json_object"}

        resp = await self._client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/healthz")
            return resp.status_code == 200
        except Exception:
            return False

    async def embed(self, text: str) -> list[float]:
        """调用 Qwen Embedding 接口（或外部 Embedding 服务）"""
        resp = await self._client.post("/v1/embeddings", json={
            "model": "text-embedding-v3",
            "input": text,
        })
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
