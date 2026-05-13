import pytest
from unittest.mock import AsyncMock, patch
from adapter.engines.vibevoice.engine import VibeVoiceASREngine


@pytest.fixture
def engine():
    return VibeVoiceASREngine()


def test_engine_inherits_base():
    from adapter.base import ASREngine
    eng = VibeVoiceASREngine()
    assert isinstance(eng, ASREngine)


@pytest.mark.asyncio
async def test_health_check(engine):
    with patch.object(engine, "_model_loaded", True):
        result = await engine.health_check()
        assert result is True
