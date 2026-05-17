import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from ttsadapter.base import TTSEngine, TTSResult
from ttsadapter.engines.cosyvoice.engine import CosyVoiceTTSEngine


@pytest.fixture
def engine():
    return CosyVoiceTTSEngine()


def test_engine_inherits_base():
    eng = CosyVoiceTTSEngine()
    assert isinstance(eng, TTSEngine)


def test_biz_type_profiles():
    from ttsadapter.engines.cosyvoice.engine import BIZ_TYPE_PROFILES
    assert "customer_service" in BIZ_TYPE_PROFILES
    assert "collection" in BIZ_TYPE_PROFILES
    assert "marketing" in BIZ_TYPE_PROFILES
    for profile in BIZ_TYPE_PROFILES.values():
        assert "voice" in profile
        assert "speed" in profile


def test_get_profile_default(engine):
    profile = engine._get_profile({})
    assert profile["voice"] == "default_female.wav"
    assert profile["speed"] == 1.0


def test_get_profile_collection(engine):
    profile = engine._get_profile({"biz_type": "collection"})
    assert profile["speed"] == 0.9


def test_get_profile_marketing(engine):
    profile = engine._get_profile({"biz_type": "marketing"})
    assert profile["speed"] == 1.1


def test_get_profile_unknown_biz_type(engine):
    profile = engine._get_profile({"biz_type": "unknown"})
    assert profile["voice"] == "default_female.wav"


def test_semaphore_default_concurrency():
    eng = CosyVoiceTTSEngine()
    assert eng._semaphore._value == 5


@pytest.mark.asyncio
async def test_health_check_model_loaded(engine):
    engine._model = MagicMock()
    result = await engine.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_model_not_loaded(engine):
    result = await engine.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_synthesize_cache_hit(engine, tmp_path):
    engine._cache_dir = str(tmp_path)
    engine._model = MagicMock()

    profile = engine._get_profile({"biz_type": "customer_service"})
    cache_key = engine._cache_key("你好", profile)
    cache_path = engine._cache_path("customer_service", cache_key)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "wb") as f:
        f.write(b"cached_audio_bytes")

    result = await engine.synthesize("你好", {"biz_type": "customer_service"})
    assert isinstance(result, TTSResult)
    assert result.audio == b"cached_audio_bytes"
    engine._model.inference_cross_lingual.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_model_not_loaded(engine):
    with pytest.raises(RuntimeError, match="CosyVoice model not loaded"):
        await engine.synthesize("你好", {"biz_type": "customer_service"})


@pytest.mark.asyncio
async def test_synthesize_inference_failure(engine, tmp_path):
    engine._cache_dir = str(tmp_path)
    mock_model = MagicMock()
    mock_model.inference_cross_lingual.side_effect = RuntimeError("inference error")
    engine._model = mock_model

    with patch.dict("os.environ", {"VOICES_DIR": str(tmp_path)}):
        os.makedirs(os.path.join(str(tmp_path)), exist_ok=True)
        voice_path = os.path.join(str(tmp_path), "default_female.wav")
        with open(voice_path, "wb") as f:
            f.write(b"fake_voice")

        with pytest.raises(RuntimeError, match="CosyVoice synthesis failed"):
            await engine.synthesize("你好", {"biz_type": "customer_service"})
