"""WebRTCAPM 单元测试 — 注入 fake AP + fake Frame，不依赖真实 livekit 库。"""
import sys
from pathlib import Path

# 让 tests/ 能 import src/ws
_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))

from ws.audio_processing import WebRTCAPM  # noqa: E402

FRAME_BYTES = 960  # 30ms @ 16kHz 16-bit mono


class FakeFrame:
    """模拟 livekit AudioFrame：data 可 in-place 修改。"""
    def __init__(self, data, sample_rate, num_channels, samples_per_channel):
        self.data = bytearray(data)
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.samples_per_channel = samples_per_channel


class FakeAP:
    """记录调用序列的假 AudioProcessingModule（livekit 签名）。"""
    def __init__(self, *, echo_cancellation=False, noise_suppression=False,
                 high_pass_filter=False, auto_gain_control=False):
        self.kwargs = dict(echo_cancellation=echo_cancellation,
                           noise_suppression=noise_suppression,
                           high_pass_filter=high_pass_filter,
                           auto_gain_control=auto_gain_control)
        self.calls = []

    def set_stream_delay_ms(self, d): self.calls.append(("set_delay", d))
    def process_reverse_stream(self, frame): self.calls.append(("reverse", len(frame.data)))
    def process_stream(self, frame):
        self.calls.append(("stream", len(frame.data)))
        # in-place：不改内容（测试只验顺序/长度）


def _make(ap_cls=FakeAP, system_delay_ms=80):
    return WebRTCAPM(
        aec_type=2, ns_level=2, agc_type=1,
        system_delay_ms=system_delay_ms,
        _ap_cls=ap_cls, _frame_cls=FakeFrame,
    )


def test_output_length_preserved_with_reverse():
    apm = _make()
    out = apm.process(b"\x00" * FRAME_BYTES, b"\x01" * FRAME_BYTES)
    assert len(out) == FRAME_BYTES


def test_output_length_preserved_without_reverse():
    apm = _make()
    out = apm.process(b"\x00" * FRAME_BYTES, None)
    assert len(out) == FRAME_BYTES


def test_order_delay_reverse_stream():
    """livekit 不拆帧：30ms 整帧一次。顺序 set_delay → reverse → stream。"""
    apm = _make(system_delay_ms=80)
    apm.process(b"\x00" * FRAME_BYTES, b"\x01" * FRAME_BYTES)
    kinds = [c[0] for c in apm._ap.calls]
    assert kinds == ["set_delay", "reverse", "stream"]
    assert all(c[1] == FRAME_BYTES for c in apm._ap.calls if c[0] in ("reverse", "stream"))


def test_system_delay_passed():
    apm = _make(system_delay_ms=120)
    apm.process(b"\x00" * FRAME_BYTES, b"\x01" * FRAME_BYTES)
    assert ("set_delay", 120) in apm._ap.calls


def test_no_reverse_skips_reverse_and_delay():
    apm = _make()
    apm.process(b"\x00" * FRAME_BYTES, None)
    kinds = [c[0] for c in apm._ap.calls]
    assert "reverse" not in kinds
    assert "set_delay" not in kinds
    assert kinds == ["stream"]


def test_exception_returns_input_unchanged():
    class BoomAP(FakeAP):
        def process_stream(self, frame):
            raise RuntimeError("boom")
    apm = _make(ap_cls=BoomAP)
    near = b"\xab" * FRAME_BYTES
    out = apm.process(near, None)
    assert out == near  # 降级透传


def test_aec_agc_enabled_from_config():
    """aec_type>0 → echo_cancellation=True；agc_type>0 → auto_gain_control=True。"""
    apm = _make()
    assert apm._ap.kwargs["echo_cancellation"] is True
    assert apm._ap.kwargs["auto_gain_control"] is True
    assert apm._ap.kwargs["noise_suppression"] is True
    assert apm._ap.kwargs["high_pass_filter"] is True
