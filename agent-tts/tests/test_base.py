import pytest
from ttsadapter.base import TTSEngine, TTSResult


def test_tts_result_creation():
    result = TTSResult(audio=b"fake_wav", content_type="audio/wav")
    assert result.audio == b"fake_wav"


def test_tts_engine_is_abstract():
    with pytest.raises(TypeError):
        TTSEngine()
