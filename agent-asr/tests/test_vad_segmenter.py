"""FsmnVadSegmenter 单测 — mock funasr.AutoModel,验证累积切段/尾部冲刷/重置/偏移簿记。

不依赖真实模型权重。AutoModel.generate 返回 FunASR FSMN-VAD 文档结构:
res[0]["value"] = [[start_ms, end_ms], ...](绝对时间戳,跨 chunk 累积)。
"""
import sys
import types

import pytest

from asradapter.vad_segmenter import BYTES_PER_MS, FsmnVadSegmenter


class _FakeGenerate:
    """按调用序列返回预设 value 列表(模拟流式 generate)。"""

    def __init__(self, value_seq):
        self._seq = list(value_seq)
        self.calls = []  # [(chunk_bytes, is_final)]

    def __call__(self, input, is_final, **kw):
        self.calls.append((len(input), is_final))
        idx = min(len(self.calls) - 1, len(self._seq) - 1)
        return [{"value": self._seq[idx]}]


def _patch_funasr(monkeypatch, value_seq):
    """注入假 funasr.AutoModel,generate 行为由 value_seq 控制。"""
    fake = types.ModuleType("funasr")
    captured = {}

    class _FakeAutoModel:
        def __init__(self, model=None, disable_update=False, **kw):
            captured["model"] = model
            self._gen = _FakeGenerate(value_seq)

        def generate(self, input, is_final=False, **kw):
            return self._gen(input, is_final, **kw)

    fake.AutoModel = _FakeAutoModel
    monkeypatch.setitem(sys.modules, "funasr", fake)
    return captured


def _pcm(ms: int) -> bytes:
    """造 ms 毫秒的 PCM(非零,避免被误判静音)。"""
    return b"\x01\x00" * (ms * BYTES_PER_MS // 2)


def test_bytes_per_ms_constant():
    assert BYTES_PER_MS == 32  # 16kHz * 16bit / 8 / 1000


def test_feed_accumulates_until_chunk_then_emits_segment(monkeypatch):
    # 一个 600ms chunk 后,generate 报告 [100,500]ms 段已结束
    _patch_funasr(monkeypatch, value_seq=[[[100, 500]]])
    seg = FsmnVadSegmenter(model_dir="fake")
    out = seg.feed(_pcm(600))  # 600ms = CHUNK_MS,触发一次 generate
    assert len(out) == 1
    assert len(out[0]) == (500 - 100) * BYTES_PER_MS  # 400ms 段


def test_feed_below_chunk_threshold_emits_nothing(monkeypatch):
    _patch_funasr(monkeypatch, value_seq=[[[0, 100]]])
    seg = FsmnVadSegmenter(model_dir="fake")
    assert seg.feed(_pcm(300)) == []  # 不足 600ms,不 generate


def test_segment_offsets_track_absolute_ms_across_chunks(monkeypatch):
    """第二 chunk 的段用绝对 ms 切片,不重复已吐段。"""
    _patch_funasr(monkeypatch, value_seq=[
        [[100, 400]],        # chunk1:吐 [100,400]
        [[100, 400], [400, 900]],  # chunk2:第一段已吐(跳过),吐 [400,900]
    ])
    seg = FsmnVadSegmenter(model_dir="fake")
    first = seg.feed(_pcm(600))
    second = seg.feed(_pcm(600))
    assert len(first) == 1 and len(first[0]) == 300 * BYTES_PER_MS
    assert len(second) == 1 and len(second[0]) == 500 * BYTES_PER_MS  # [400,900]


def test_force_flush_emits_trailing_segment(monkeypatch):
    # feed 不足 chunk 时不 generate(test_feed_below_chunk_threshold_emits_nothing
    # 已验证),故 force_flush 是首次也是唯一的 generate 调用,value_seq 只需 1 项。
    _patch_funasr(monkeypatch, value_seq=[[[200, 350]]])
    seg = FsmnVadSegmenter(model_dir="fake")
    seg.feed(_pcm(400))  # 不足 chunk,无输出
    tail = seg.force_flush()
    assert len(tail) == 1
    assert len(tail[0]) == 150 * BYTES_PER_MS


def test_force_flush_empty_buffer_returns_empty(monkeypatch):
    _patch_funasr(monkeypatch, value_seq=[[[10, 20]]])
    seg = FsmnVadSegmenter(model_dir="fake")
    assert seg.force_flush() == []


def test_reset_clears_state(monkeypatch):
    _patch_funasr(monkeypatch, value_seq=[[[100, 400]], []])
    seg = FsmnVadSegmenter(model_dir="fake")
    seg.feed(_pcm(600))
    seg.reset()
    # reset 后缓冲清空,force_flush 无尾部
    assert seg.force_flush() == []


def test_generate_exception_propagates(monkeypatch):
    fake = types.ModuleType("funasr")

    class _Boom:
        def __init__(self, *a, **k): pass
        def generate(self, *a, **k): raise RuntimeError("model boom")

    fake.AutoModel = _Boom
    monkeypatch.setitem(sys.modules, "funasr", fake)
    seg = FsmnVadSegmenter(model_dir="fake")
    with pytest.raises(RuntimeError, match="model boom"):
        seg.feed(_pcm(600))
