"""FsmnVadSegmenter 单测 — mock funasr.AutoModel,验证累积切段/尾部冲刷/重置/偏移簿记。

不依赖真实模型权重。AutoModel.generate 返回 FunASR FSMN-VAD 文档结构:
res[0]["value"] = [[start_ms, end_ms], ...](绝对时间戳,跨 chunk 累积)。
"""
import sys
import types

import pytest

from asradapter.vad_segmenter import (
    BYTES_PER_MS,
    MAX_RETAINED_MS,
    FsmnVadSegmenter,
    load_fsmn_vad_model,
)


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
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    out = seg.feed(_pcm(600))  # 600ms = CHUNK_MS,触发一次 generate
    assert len(out) == 1
    assert len(out[0]) == (500 - 100) * BYTES_PER_MS  # 400ms 段


def test_feed_below_chunk_threshold_emits_nothing(monkeypatch):
    _patch_funasr(monkeypatch, value_seq=[[[0, 100]]])
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    assert seg.feed(_pcm(300)) == []  # 不足 600ms,不 generate


def test_segment_offsets_track_absolute_ms_across_chunks(monkeypatch):
    """第二 chunk 的段用绝对 ms 切片,不重复已吐段。"""
    _patch_funasr(monkeypatch, value_seq=[
        [[100, 400]],        # chunk1:吐 [100,400]
        [[100, 400], [400, 900]],  # chunk2:第一段已吐(跳过),吐 [400,900]
    ])
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    first = seg.feed(_pcm(600))
    second = seg.feed(_pcm(600))
    assert len(first) == 1 and len(first[0]) == 300 * BYTES_PER_MS
    assert len(second) == 1 and len(second[0]) == 500 * BYTES_PER_MS  # [400,900]


def test_force_flush_emits_trailing_segment(monkeypatch):
    # feed 不足 chunk 时不 generate(test_feed_below_chunk_threshold_emits_nothing
    # 已验证),故 force_flush 是首次也是唯一的 generate 调用,value_seq 只需 1 项。
    _patch_funasr(monkeypatch, value_seq=[[[200, 350]]])
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    seg.feed(_pcm(400))  # 不足 chunk,无输出
    tail = seg.force_flush()
    assert len(tail) == 1
    assert len(tail[0]) == 150 * BYTES_PER_MS


def test_force_flush_empty_buffer_returns_empty(monkeypatch):
    _patch_funasr(monkeypatch, value_seq=[[[10, 20]]])
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    assert seg.force_flush() == []


def test_reset_clears_state(monkeypatch):
    _patch_funasr(monkeypatch, value_seq=[[[100, 400]], []])
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
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
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    with pytest.raises(RuntimeError, match="model boom"):
        seg.feed(_pcm(600))


def test_real_api_minus1_sentinel_assembles_segment_across_chunks(monkeypatch):
    """真实 funasr FSMN-VAD 用 -1 sentinel 表示跨 chunk 起止(verified vs funasr 1.2.7):
       [start, -1] = 段在 start ms 起、未结束;[-1, end] = 段在 end ms 结束(起已在先前 chunk 报)。
    segmenter 的 max(start,consumed) clamp 应正确组装:跳过未结束的、吐出已结束的,不丢不重复。"""
    _patch_funasr(monkeypatch, value_seq=[
        [[70, -1]],     # chunk0: 段 70ms 起,未终 → skip
        [],             # chunk1: 段持续,无边界
        [[-1, 1800]],   # chunk2: 段 1800ms 终 → emit [consumed=0, 1800]
    ])
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    assert seg.feed(_pcm(600)) == []    # chunk0: [70,-1] skipped(end=-1 ≤ consumed=0)
    assert seg.feed(_pcm(600)) == []    # chunk1: 空
    out = seg.feed(_pcm(600))           # chunk2: [-1,1800] → emit [0,1800]
    assert len(out) == 1
    assert len(out[0]) == 1800 * BYTES_PER_MS
    # 再喂无新段,确认不重复吐
    assert seg.feed(_pcm(600)) == []


def test_audio_capped_during_long_silence(monkeypatch):
    """用户长静音时 consumed 停滞,_audio 持续累积;超过 MAX_RETAINED_MS 后应截断,
    偏移簿记保持一致(_consumed_ms >= _audio_offset_ms,不越界)。"""
    _patch_funasr(monkeypatch, value_seq=[[]])  # 始终不报段 → consumed 停滞
    seg = FsmnVadSegmenter(load_fsmn_vad_model("fake"))
    # 喂超过 MAX_RETAINED_MS 的音频(每个 chunk 600ms)
    chunks = MAX_RETAINED_MS // 600 + 5  # 确保超过上限
    for _ in range(chunks):
        seg.feed(_pcm(600))
    # _audio 应被截断到不超过 MAX_RETAINED_MS
    assert len(seg._audio) // BYTES_PER_MS <= MAX_RETAINED_MS
    # 偏移簿记一致:consumed 不落后于 offset(被丢音频不可切片,consumed 随之推进)
    assert seg._consumed_ms >= seg._audio_offset_ms
