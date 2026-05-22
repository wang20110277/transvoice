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

    client = TTSClient(base_url="http://tts:8081")
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    client._client = mock_client

    result = await client.synthesize("你好", "call123", "marketing")

    assert result["audio"] == "dGVzdA=="
    assert result["minio_key"] == "tts/20260514/call123.wav"


@pytest.mark.asyncio
async def test_synthesize_failure_returns_none():
    client = TTSClient(base_url="http://tts:8081")
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("connection refused")
    client._client = mock_client

    result = await client.synthesize("你好", "call123", "marketing")

    assert result is None


@pytest.mark.asyncio
async def test_synthesize_raw_returns_bytes():
    wav_data = b'RIFF' + b'\x00' * 40 + b'\x01\x00\x02\x00'
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = wav_data
    mock_resp.raise_for_status = MagicMock()

    client = TTSClient(base_url="http://tts:8081")
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    client._client = mock_client

    result = await client.synthesize_raw("你好", "call123", "marketing")

    assert result == wav_data


@pytest.mark.asyncio
async def test_synthesize_without_start_returns_none():
    client = TTSClient(base_url="http://tts:8081")
    result = await client.synthesize("你好", "call123", "marketing")
    assert result is None
