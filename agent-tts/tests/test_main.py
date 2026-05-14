import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from adapter.main import app
from adapter.base import TTSResult


@pytest.mark.asyncio
async def test_healthz():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_synthesize_json():
    import adapter.main as main_mod

    mock_engine = MagicMock()
    mock_engine.synthesize = AsyncMock(return_value=TTSResult(audio=b"fake_audio_bytes", content_type="audio/wav"))
    main_mod.engine = mock_engine

    with patch("adapter.main.storage.upload_audio", return_value="tts/20260514/call123.wav"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/tts/synthesize_json", data={"text": "你好", "params": '{"call_id":"call123"}'})
            assert resp.status_code == 200
            data = resp.json()
            assert "audio" in data
            assert data["minio_key"] == "tts/20260514/call123.wav"
            assert data["content_type"] == "audio/wav"
