import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from adapter.base import ASREngine
from adapter.engines.vibevoice.engine import VibeVoiceASREngine


@pytest.fixture
def engine():
    return VibeVoiceASREngine()


def test_engine_inherits_base():
    from adapter.base import ASREngine
    eng = VibeVoiceASREngine()
    assert isinstance(eng, ASREngine)


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
async def test_recognize_success(engine):
    with patch("adapter.engines.vibevoice.engine.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"text": "你好", "confidence": 0.9}
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await engine.recognize(b"fake_audio", {"language": "zh"})
        assert result.text == "你好"
        assert result.confidence == 0.9
        assert result.is_final is True


@pytest.mark.asyncio
async def test_recognize_server_error(engine):
    with patch("adapter.engines.vibevoice.engine.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("server error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="VibeVoice ASR recognition failed"):
            await engine.recognize(b"fake_audio", {})
