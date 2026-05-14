import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from clients.tts import TTSClient


@pytest.mark.asyncio
async def test_synthesize_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "audio": "dGVzdA==",
        "minio_key": "tts/20260514/call123.wav",
        "content_type": "audio/wav",
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("clients.tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = TTSClient(base_url="http://tts:8081")
        result = await client.synthesize("你好", "call123", "marketing")

    assert result["audio"] == "dGVzdA=="
    assert result["minio_key"] == "tts/20260514/call123.wav"


@pytest.mark.asyncio
async def test_synthesize_failure_returns_none():
    with patch("clients.tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        client = TTSClient(base_url="http://tts:8081")
        result = await client.synthesize("你好", "call123", "marketing")

    assert result is None
