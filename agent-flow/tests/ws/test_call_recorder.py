"""CallRecorder 单测 — 验证双声道合成、补静音、wav 合法性。"""
import struct
import sys
from pathlib import Path

# 让 tests/ 能 import src/ws、src/storage
_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))


def _pcm(samples: list[int]) -> bytes:
    """int16 PCM bytes。"""
    return struct.pack(f"<{len(samples)}h", *samples)


def _parse_wav_channels(wav: bytes) -> int:
    """从 wav fmt chunk 取 channel 数。"""
    assert wav[:4] == b"RIFF"
    fmt = wav.index(b"fmt ")
    return struct.unpack("<H", wav[fmt + 10: fmt + 12])[0]


def test_stereo_two_channels():
    from ws.call_recorder import CallRecorder
    r = CallRecorder(sample_rate=16000)
    r.feed_caller(_pcm([100, 200, 300]))
    r.feed_ai(_pcm([400, 500]))
    wav = r.finalize_stereo_wav()
    assert wav is not None
    assert _parse_wav_channels(wav) == 2


def test_shorter_side_padded_with_silence():
    from ws.call_recorder import CallRecorder
    import numpy as np
    r = CallRecorder(sample_rate=16000)
    r.feed_caller(_pcm([100, 200, 300, 400]))   # 4 samples
    r.feed_ai(_pcm([500, 600]))                  # 2 samples → pad 2 zeros
    wav = r.finalize_stereo_wav()
    # 跳过 44 字节 wav 头，解交错 int16 立体声
    pcm = wav[44:]
    arr = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 2)
    assert arr.shape == (4, 2)
    assert list(arr[:, 0]) == [100, 200, 300, 400]      # L = caller
    assert list(arr[:, 1]) == [500, 600, 0, 0]          # R = ai + 静音补齐


def test_no_audio_returns_none():
    from ws.call_recorder import CallRecorder
    assert CallRecorder().finalize_stereo_wav() is None
