import pytest
from adapter.base import ASREngine, ASRResult


def test_asr_result_creation():
    result = ASRResult(text="你好", confidence=0.95, is_final=True)
    assert result.text == "你好"
    assert result.confidence == 0.95
    assert result.is_final is True


def test_asr_engine_is_abstract():
    with pytest.raises(TypeError):
        ASREngine()


def test_load_unknown_engine_raises():
    from adapter.config import load_asr_engine
    with pytest.raises(ValueError, match="Unknown ASR engine"):
        load_asr_engine("nonexistent_engine")
