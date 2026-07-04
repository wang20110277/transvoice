"""RMSGate(RMS + SNR 自适应底噪)单元测试。

从 tests/ws/test_vad.py 平移 —— 逻辑等价,仅类/方法名改:
  WebRTCVAD._has_speech_energy(x) → RMSGate.is_speech(x)
  WebRTCVAD.is_end_of_speech(frame, 0)(驱动底噪 EMA)→ RMSGate.is_speech(frame)
  (RMSGate 无 WebRTC 共轭,is_speech 返回 False 即触发底噪更新)
"""
import struct

import numpy as np
import pytest

from ws.rms_gate import FRAME_BYTES, RMSGate

SAMPLES = FRAME_BYTES // 2  # 480 samples @ 16kHz 16-bit


def _frame(rms: float) -> bytes:
    sample = max(0, min(32767, int(round(rms))))
    return struct.pack(f"<{SAMPLES}h", *([sample] * SAMPLES))


def test_fixed_threshold_disabled_when_both_zero():
    """threshold=0 且 snr_factor=0 → 不做能量过滤(全判语音)。"""
    gate = RMSGate(threshold=0.0, snr_factor=0.0)
    assert gate.is_speech(_frame(1)) is True
    assert gate.is_speech(_frame(0)) is True


def test_fixed_threshold_backward_compat():
    """snr_factor=0 走固定门限:rms > threshold 才通过。"""
    gate = RMSGate(threshold=300.0, snr_factor=0.0)
    assert gate.is_speech(_frame(200)) is False
    assert gate.is_speech(_frame(400)) is True


def test_snr_noise_floor_converges_to_real_floor():
    """喂稳定噪声帧,底噪 EMA 应从初始值收敛到真实噪声 RMS。"""
    gate = RMSGate(threshold=0.0, snr_factor=3.0, noise_floor_init=900.0, noise_adapt_rate=0.1)
    noise = _frame(100.0)
    for _ in range(60):
        gate.is_speech(noise)  # 噪声帧 → False → 触发底噪更新
    assert gate._noise_floor == pytest.approx(100.0, abs=8.0)


def test_snr_speech_passes_above_adaptive_threshold():
    """底噪收敛后,明显高于门限的人声帧通过。"""
    gate = RMSGate(threshold=0.0, snr_factor=3.0, noise_floor_init=100.0, noise_adapt_rate=0.1)
    assert gate.is_speech(_frame(500.0)) is True
    assert gate.is_speech(_frame(200.0)) is False


def test_snr_rejects_noise_that_would_pass_fixed_threshold():
    """嘈杂环境底噪收敛后门限抬高,RMS=600 噪声帧被正确拒绝。"""
    gate = RMSGate(threshold=300.0, snr_factor=3.0, noise_floor_init=800.0, noise_adapt_rate=0.1)
    loud_noise = _frame(800.0)
    for _ in range(60):
        gate.is_speech(loud_noise)
    assert gate._noise_floor == pytest.approx(800.0, abs=15.0)
    assert gate.is_speech(_frame(600.0)) is False


def test_snr_loud_speech_still_detected_in_noisy_env():
    """嘈杂环境底噪收敛后,真正大声量人声仍能通过。"""
    gate = RMSGate(threshold=300.0, snr_factor=3.0, noise_floor_init=800.0, noise_adapt_rate=0.1)
    for _ in range(60):
        gate.is_speech(_frame(800.0))
    assert gate.is_speech(_frame(3000.0)) is True


def test_snr_speech_frames_do_not_raise_noise_floor():
    """语音帧(返回 True)不更新底噪,避免人声抬高门限误伤后续语音。"""
    gate = RMSGate(threshold=0.0, snr_factor=3.0, noise_floor_init=100.0, noise_adapt_rate=0.1)
    before = gate._noise_floor
    gate.is_speech(_frame(1500.0))  # 高 RMS → speech → 不更新
    assert gate._noise_floor == before


def test_reset_preserves_noise_floor():
    """reset 不清底噪:先喂噪声帧把底噪从 init=100 收敛到 ~80,再 reset,确认保留收敛值(非 init)。"""
    gate = RMSGate(threshold=0.0, snr_factor=3.0, noise_floor_init=100.0, noise_adapt_rate=0.1)
    # RMS=80 < 门限 max(100*3,0)=300 → 非语音 → EMA 把底噪从 100 拉向 80
    for _ in range(40):
        gate.is_speech(_frame(80.0))
    converged = gate._noise_floor
    assert converged == pytest.approx(80.0, abs=1.0)  # 真收敛了(证明不是 init=100 原值)
    gate.reset()
    assert gate._noise_floor == pytest.approx(converged)  # reset 保留收敛值
