import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from adapter.base import TTSEngine, TTSResult
from adapter.engines.vibevoice.engine import VibeVoiceTTSEngine


@pytest.fixture
def engine():
    return VibeVoiceTTSEngine()


def test_engine_inherits_base():
    eng = VibeVoiceTTSEngine()
    assert isinstance(eng, TTSEngine)


def test_biz_type_profiles():
    from adapter.engines.vibevoice.engine import BIZ_TYPE_PROFILES
    assert "customer_service" in BIZ_TYPE_PROFILES
    assert "collection" in BIZ_TYPE_PROFILES
    assert "marketing" in BIZ_TYPE_PROFILES


def test_get_profile_default(engine):
    profile = engine._get_profile({})
    assert profile["voice_id"] == "cs_female_soft_01"


def test_get_profile_collection(engine):
    profile = engine._get_profile({"biz_type": "collection"})
    assert profile["voice_id"] == "col_male_serious_01"


@pytest.mark.asyncio
async def test_health_check_success(engine):
    with patch("adapter.engines.vibevoice.engine.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await engine.health_check()
        assert result is True


@pytest.mark.asyncio
async def test_health_check_failure(engine):
    with patch("adapter.engines.vibevoice.engine.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await engine.health_check()
        assert result is False


@pytest.mark.asyncio
async def test_synthesize_cache_miss(engine, tmp_path):
    engine._cache_dir = str(tmp_path)

    with patch("adapter.engines.vibevoice.engine.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_wav_audio"
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await engine.synthesize("你好", {"biz_type": "customer_service"})
        assert isinstance(result, TTSResult)
        assert result.audio == b"fake_wav_audio"


@pytest.mark.asyncio
async def test_synthesize_cache_hit(engine, tmp_path):
    engine._cache_dir = str(tmp_path)

    with patch("adapter.engines.vibevoice.engine.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        profile = engine._get_profile({"biz_type": "customer_service"})
        cache_key = engine._cache_key("你好", profile)
        cache_path = engine._cache_path("customer_service", cache_key)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(b"cached_audio")

        result = await engine.synthesize("你好", {"biz_type": "customer_service"})
        assert result.audio == b"cached_audio"
        mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_server_error(engine, tmp_path):
    engine._cache_dir = str(tmp_path)

    with patch("adapter.engines.vibevoice.engine.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("server error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="VibeVoice TTS synthesis failed"):
            await engine.synthesize("你好", {"biz_type": "customer_service"})
