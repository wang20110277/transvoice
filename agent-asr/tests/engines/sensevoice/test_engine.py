import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from asradapter.base import ASREngine, ASRResult
from asradapter.engines.sensevoice.engine import SenseVoiceASREngine


@pytest.fixture
def engine():
    return SenseVoiceASREngine()


def test_engine_inherits_base():
    eng = SenseVoiceASREngine()
    assert isinstance(eng, ASREngine)


def test_semaphore_default_concurrency():
    eng = SenseVoiceASREngine()
    assert eng._semaphore._value == 50


def test_semaphore_custom_concurrency():
    with patch("asradapter.engines.sensevoice.engine.SENSEVOICE_MAX_CONCURRENT", 10):
        eng = SenseVoiceASREngine()
        assert eng._semaphore._value == 10


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
async def test_recognize_success(engine):
    mock_model = MagicMock()
    mock_model.generate.return_value = [{"text": "你好世界"}]
    engine._model = mock_model

    result = await engine.recognize(b"fake-audio-bytes", {})
    assert isinstance(result, ASRResult)
    assert result.text == "你好世界"
    assert result.confidence == 0.95
    assert result.is_final is True


@pytest.mark.asyncio
async def test_recognize_empty_result(engine):
    mock_model = MagicMock()
    mock_model.generate.return_value = []
    engine._model = mock_model

    result = await engine.recognize(b"fake-audio-bytes", {})
    assert result.text == ""


@pytest.mark.asyncio
async def test_recognize_model_not_loaded(engine):
    with pytest.raises(RuntimeError, match="SenseVoice model not loaded"):
        await engine.recognize(b"fake-audio-bytes", {})


@pytest.mark.asyncio
async def test_recognize_generate_failure(engine):
    mock_model = MagicMock()
    mock_model.generate.side_effect = RuntimeError("inference error")
    engine._model = mock_model

    with pytest.raises(RuntimeError, match="SenseVoice recognition failed"):
        await engine.recognize(b"fake-audio-bytes", {})
