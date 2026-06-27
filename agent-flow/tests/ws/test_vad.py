"""WebRTCVAD 自适应噪声底噪（noise floor tracking）单元测试。

注入 fake webrtcvad，控制 is_speech 返回值，验证 SNR 门控与底噪收敛逻辑。
不依赖真实 webrtcvad 的硬件行为。
"""
import struct
import sys
import types

import numpy as np
import pytest

from ws.vad import FRAME_BYTES, WebRTCVAD  # noqa: E402

SAMPLES = FRAME_BYTES // 2  # 480 samples @ 16kHz 16-bit


class _FakeVad:
    """webrtcvad.Vad 替身 — is_speech 返回值由 speech_result 控制（默认噪声）。"""

    def __init__(self, aggressiveness: int) -> None:
        self.aggressiveness = aggressiveness
        self.speech_result = False

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        return self.speech_result


@pytest.fixture
def _fake_webrtcvad(monkeypatch):
    mod = types.ModuleType("webrtcvad")
    mod.Vad = _FakeVad
    monkeypatch.setitem(sys.modules, "webrtcvad", mod)
    return mod


def _frame(rms: float) -> bytes:
    sample = max(0, min(32767, int(round(rms))))
    return struct.pack(f"<{SAMPLES}h", *([sample] * SAMPLES))


def _rms_of(frame: bytes) -> float:
    f32 = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(f32**2)))


def test_fixed_threshold_disabled_when_both_zero(_fake_webrtcvad):
    """rms_threshold=0 且 snr_factor=0 → 不做能量过滤（纯 WebRTC，向后兼容）。"""
    vad = WebRTCVAD(rms_threshold=0.0, snr_factor=0.0)
    assert vad._has_speech_energy(_frame(1)) is True
    assert vad._has_speech_energy(_frame(0)) is True


def test_fixed_threshold_backward_compat(_fake_webrtcvad):
    """snr_factor=0 走原固定门限：rms > threshold 才通过。"""
    vad = WebRTCVAD(rms_threshold=300.0, snr_factor=0.0)
    assert vad._has_speech_energy(_frame(200)) is False
    assert vad._has_speech_energy(_frame(400)) is True


def test_snr_noise_floor_converges_to_real_floor(_fake_webrtcvad):
    """喂稳定噪声帧，底噪 EMA 应从初始值收敛到真实噪声 RMS。"""
    vad = WebRTCVAD(
        rms_threshold=0.0, snr_factor=3.0,
        noise_floor_init=900.0, noise_adapt_rate=0.1,
    )
    noise = _frame(100.0)
    for _ in range(60):
        vad.is_end_of_speech(noise, 0)
    assert vad._noise_floor == pytest.approx(100.0, abs=8.0)


def test_snr_speech_passes_above_adaptive_threshold(_fake_webrtcvad):
    """底噪收敛后，明显高于门限的人声帧通过能量门控。"""
    vad = WebRTCVAD(
        rms_threshold=0.0, snr_factor=3.0,
        noise_floor_init=100.0, noise_adapt_rate=0.1,
    )
    vad._vad.speech_result = True
    assert vad._has_speech_energy(_frame(500.0)) is True
    assert vad._has_speech_energy(_frame(200.0)) is False


def test_snr_rejects_noise_that_would_pass_fixed_threshold(_fake_webrtcvad):
    """嘈杂环境底噪收敛后门限抬高，RMS=600 噪声帧在固定 300 门限下会误触发，
    SNR 模式下因门限 = noise_floor*3 >> 600 被正确拒绝。"""
    vad = WebRTCVAD(
        rms_threshold=300.0, snr_factor=3.0,
        noise_floor_init=800.0, noise_adapt_rate=0.1,
    )
    loud_noise = _frame(800.0)
    for _ in range(60):
        vad.is_end_of_speech(loud_noise, 0)
    assert vad._noise_floor == pytest.approx(800.0, abs=15.0)
    assert vad._has_speech_energy(_frame(600.0)) is False


def test_snr_loud_speech_still_detected_in_noisy_env(_fake_webrtcvad):
    """嘈杂环境底噪收敛后，真正大声量人声（远超门限）仍能通过。"""
    vad = WebRTCVAD(
        rms_threshold=300.0, snr_factor=3.0,
        noise_floor_init=800.0, noise_adapt_rate=0.1,
    )
    for _ in range(60):
        vad.is_end_of_speech(_frame(800.0), 0)
    vad._vad.speech_result = True
    assert vad._has_speech_energy(_frame(3000.0)) is True


def test_snr_speech_frames_do_not_raise_noise_floor(_fake_webrtcvad):
    """语音帧不更新底噪，避免人声把门限抬高误伤后续语音。"""
    vad = WebRTCVAD(
        rms_threshold=0.0, snr_factor=3.0,
        noise_floor_init=100.0, noise_adapt_rate=0.1,
    )
    vad._vad.speech_result = True
    before = vad._noise_floor
    vad.is_end_of_speech(_frame(1500.0), 0)
    assert vad._noise_floor == before


def test_reset_preserves_noise_floor(_fake_webrtcvad):
    """reset 不清底噪（一通通话内环境底噪稳定，跨轮复用更准）。"""
    vad = WebRTCVAD(
        rms_threshold=0.0, snr_factor=3.0,
        noise_floor_init=100.0, noise_adapt_rate=0.1,
    )
    for _ in range(40):
        vad.is_end_of_speech(_frame(500.0), 0)
    converged = vad._noise_floor
    vad.reset()
    assert vad._noise_floor == pytest.approx(converged)
    assert vad._speech_detected is False
    assert vad._silent_count == 0
