"""WebRTCAPM 单元测试 — 注入 fake AP，不依赖真实 webrtc_audio_processing 库。"""
import sys
from pathlib import Path

# 让 tests/ 能 import src/ws
_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))

from ws.audio_processing import WebRTCAPM  # noqa: E402

SUB = 320  # 10ms @ 16kHz 16-bit


class FakeAP:
    """记录调用序列的假 AudioProcessingModule。process_stream 透传输入。"""
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def set_stream_format(self, *a, **kw): pass
    def set_reverse_stream_format(self, *a, **kw): pass
    def set_ns_level(self, l): self.ns_level = l
    def set_system_delay(self, d): self.calls.append(("set_system_delay", d))
    def process_reverse_stream(self, frame): self.calls.append(("reverse", len(frame)))
    def process_stream(self, frame):
        self.calls.append(("stream", len(frame)))
        return frame  # 透传，便于断言输出长度


def _make(ap_cls=FakeAP, system_delay_ms=80):
    return WebRTCAPM(
        aec_type=2, ns_level=2, agc_type=1,
        system_delay_ms=system_delay_ms, _ap_cls=ap_cls,
    )


def test_output_length_preserved_with_reverse():
    apm = _make()
    out = apm.process(b"\x00" * 960, b"\x01" * 960)
    assert len(out) == 960


def test_output_length_preserved_without_reverse():
    apm = _make()
    out = apm.process(b"\x00" * 960, None)
    assert len(out) == 960


def test_subframe_split_and_order_reverse_before_stream():
    apm = _make(system_delay_ms=80)
    apm.process(b"\x00" * 960, b"\x01" * 960)
    # 期望: set_system_delay → reverse×3 (320) → stream×3 (320)
    kinds = [c[0] for c in apm._ap.calls]
    assert kinds == ["set_system_delay", "reverse", "reverse", "reverse",
                     "stream", "stream", "stream"]
    assert all(c[1] == SUB for c in apm._ap.calls if c[0] in ("reverse", "stream"))


def test_system_delay_passed():
    apm = _make(system_delay_ms=120)
    apm.process(b"\x00" * 960, b"\x01" * 960)
    delay_calls = [c for c in apm._ap.calls if c[0] == "set_system_delay"]
    assert delay_calls == [("set_system_delay", 120)]


def test_no_reverse_skips_reverse_calls():
    apm = _make()
    apm.process(b"\x00" * 960, None)
    kinds = [c[0] for c in apm._ap.calls]
    assert "reverse" not in kinds
    assert "set_system_delay" not in kinds
    assert kinds == ["stream", "stream", "stream"]


def test_exception_returns_input_unchanged():
    class BoomAP(FakeAP):
        def process_stream(self, frame):
            raise RuntimeError("boom")
    apm = _make(ap_cls=BoomAP)
    near = b"\xab" * 960
    out = apm.process(near, None)
    assert out == near  # 降级透传
