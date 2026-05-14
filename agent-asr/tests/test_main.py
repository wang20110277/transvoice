import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from adapter.main import app, _load_config, engine as _engine_ref, _audio_cache
from adapter.config import load_asr_engine


@pytest.mark.asyncio
async def test_healthz():
    import adapter.main as main_mod
    config = _load_config()
    main_mod.engine = load_asr_engine(config["engine"]["asr"])

    with patch.object(main_mod.engine, "health_check", new=AsyncMock(return_value=True)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_get_audio_meta_found():
    import adapter.main as main_mod
    _audio_cache["test-call-123"] = {"minio_key": "asr/20260514/test-call-123.wav", "text": "你好"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/asr/audio/test-call-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["call_id"] == "test-call-123"
        assert data["minio_key"] == "asr/20260514/test-call-123.wav"
        assert data["text"] == "你好"

    del _audio_cache["test-call-123"]


@pytest.mark.asyncio
async def test_get_audio_meta_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/asr/audio/nonexistent")
        assert resp.status_code == 200
        assert "error" in resp.json()
