import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from asradapter.base import ASREngine, ASRResult
from asradapter.engines.sensevoice.engine import SenseVoiceASREngine


@pytest.fixture
def engine():
    with patch.dict("os.environ", {
        "SENSEVOICE_API_URL": "http://funasr:8000",
        "SENSEVOICE_TIMEOUT": "30",
        "SENSEVOICE_LANGUAGE": "zh",
        "SENSEVOICE_MAX_CONCURRENT": "10",
    }):
        return SenseVoiceASREngine()


def _mock_async_client(response_json=None, response_status=200, side_effect=None):
    """Build a mock httpx.AsyncClient that can be used as an async context manager."""
    mock_response = MagicMock()
    mock_response.status_code = response_status
    mock_response.json.return_value = response_json or {}

    mock_client = AsyncMock()
    if side_effect:
        mock_client.post = AsyncMock(side_effect=side_effect)
        mock_client.get = AsyncMock(side_effect=side_effect)
    else:
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)

    # Support `async with httpx.AsyncClient() as client:`
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    return mock_client


# ── Tests ──────────────────────────────────────────────────────────────


def test_engine_inherits_base():
    eng = SenseVoiceASREngine()
    assert isinstance(eng, ASREngine)


@pytest.mark.asyncio
async def test_health_check_success(engine):
    mock_client = _mock_async_client(response_status=200)

    with patch(
        "asradapter.engines.sensevoice.engine.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await engine.health_check()

    assert result is True
    mock_client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_failure(engine):
    mock_client = _mock_async_client(side_effect=Exception("connection refused"))

    with patch(
        "asradapter.engines.sensevoice.engine.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await engine.health_check()

    assert result is False


@pytest.mark.asyncio
async def test_recognize_success(engine):
    mock_client = _mock_async_client(
        response_json={"text": "你好世界", "confidence": 0.95},
        response_status=200,
    )

    with patch(
        "asradapter.engines.sensevoice.engine.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await engine.recognize(b"fake-audio-bytes", {})

    assert isinstance(result, ASRResult)
    assert result.text == "你好世界"
    assert result.confidence == 0.95
    assert result.is_final is True
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_recognize_server_error(engine):
    mock_client = _mock_async_client(
        side_effect=Exception("internal server error"),
    )

    with patch(
        "asradapter.engines.sensevoice.engine.httpx.AsyncClient",
        return_value=mock_client,
    ):
        with pytest.raises(RuntimeError, match="SenseVoice recognition failed"):
            await engine.recognize(b"fake-audio-bytes", {})


def test_semaphore_default_concurrency():
    eng = SenseVoiceASREngine()
    assert eng._semaphore._value == 50
