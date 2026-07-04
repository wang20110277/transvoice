# VAD 迁移至 agent-asr(FSMN-VAD)+ agent-flow RMS 门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 VAD(端点检测)从 agent-flow 迁移到 agent-asr(FSMN-VAD 流式分段层),agent-flow 仅保留 RMS+SNR 自适应门禁做 barge-in,WS 成主传输。

**Architecture:** agent-asr WS handler 加 FSMN-VAD 分段层:每收音频帧 → 喂 segmenter → 得已完成语音段 → 段级 `engine.recognize` → 主动推 `{type:result,is_final:true}`,单连接多次返回。agent-flow 删 VAD 引擎(`webrtcvad`/`silero`),抽 RMS+SNR 门禁为独立 `RMSGate`;端点改由 ASR final 回调驱动(`TurnController` + `asyncio.Lock` 保护 `streaming_task` 生命周期),barge-in 仍由本地 RMS 门禁低延迟检测 + 发 `{type:reset}` 丢服务端进行中段。

**Tech Stack:** Python 3.11 / asyncio / FastAPI / WebSocket / FunASR 1.2.3(FSMN-VAD,已装)/ pydantic-settings / pytest

## Global Constraints

- **全链路音频**:16kHz / 16-bit / mono;30ms 帧 = 960 字节;ms→字节 = 32 字节/ms(`16000 * 2 / 1000`)。
- **零新依赖**:`funasr==1.2.3` 已在 `agent-asr/asradapter/requirements.txt`;FSMN 模型已在 `agent-asr/models/speech_fsmn_vad_zh-cn-16k-common-pytorch/`(含 `model.pt`)。
- **测试命令**:
  - agent-asr:`cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v`
  - agent-flow:`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`
- **配置规约**:agent-flow 配置走 `CALLBOT_` 前缀 + pydantic-settings(`src/config.py`);`.env` 实际位置 `agent-flow/.env`。
- **`.env` 调优值必须原样保留,不得重置**:`CALLBOT_BARGE_IN_MIN_AUDIO_BYTES=3200`、`CALLBOT_BARGE_IN_RMS_THRESHOLD=1500`(非代码默认 1600/300)。
- **命名见名知意,注释只写 WHY**(隐藏约束/不变量/workaround),不写 WHAT。
- **commit 规约**:中文 + conventional 前缀(`feat`/`refactor`/`chore`/`test`/`docs`),结尾 `Co-Authored-By: Claude <noreply@anthropic.com>`。不跳 git hooks。
- **每个 task 结尾必须独立可测 + 可 commit**(应用能启动、相关测试绿)。

## File Structure

**agent-asr(新增分段层)**
- Create `agent-asr/asradapter/vad_segmenter.py` — `FsmnVadSegmenter`(流式 feed→段 PCM,内部 FunASR AutoModel + 缓冲/偏移簿记)
- Create `agent-asr/tests/__init__.py` + `agent-asr/tests/test_vad_segmenter.py` — 单测(mock AutoModel.generate)
- Create `agent-asr/tests/test_ws_server.py` — WS handler 多 final / reset / 降级 / sample_rate(mock segmenter+engine)
- Modify `agent-asr/asradapter/ws_server.py` — 接 segmenter + 多 final + `{type:reset}` + sample_rate resample + 降级 + 删 `stream_ctx` 死路径
- Modify `agent-asr/asradapter/main.py` — lifespan 加载 segmenter,注入 `ASRWebSocketHandler`

**agent-flow(瘦身 + 控制流反转)**
- Create `agent-flow/src/ws/rms_gate.py` — `RMSGate`(从 `WebRTCVAD._has_speech_energy` + `_update_noise_floor` 抽出,纯 RMS+SNR)
- Create `agent-flow/tests/ws/test_rms_gate.py` — 8 用例从 `test_vad.py` 平移
- Create `agent-flow/tests/ws/test_handler.py` — `TurnController` 并发安全 + 短文本丢弃
- Modify `agent-flow/src/clients/asr_ws_client.py` — receiver_loop 多 final + `on_final` 回调 + `send_reset`
- Modify `agent-flow/src/ws/asr_streaming.py` — 通透 `on_final`、WS 模式 per-call 生命周期、`reset_server_segment`
- Modify `agent-flow/src/ws/handler.py` — `TurnController` + `_on_asr_final` 驱动轮次、删 VAD 门控/端点、barge-in 用 `rms_gate`、barge 发 reset
- Modify `agent-flow/src/config.py` — 删 6 项 WebRTC/Silero 配置、改名 5 项 `vad_*`→`rms_gate_*`/`cooldown_after_bargein`、`asr_use_ws` 默认 true
- Modify `agent-flow/main.py` — `rms_gate_factory` 注入、启动日志改 RMS gate
- Modify `agent-flow/.env` — 删 3 项待删 VAD 配置、保留 barge_in 调优值
- Delete `agent-flow/src/ws/vad.py` + `agent-flow/tests/ws/test_vad.py`(handler 不再引用后)

---

## Task 1: agent-asr — FsmnVadSegmenter 分段器 + 单测

**Files:**
- Create: `agent-asr/asradapter/vad_segmenter.py`
- Create: `agent-asr/tests/__init__.py`
- Test: `agent-asr/tests/test_vad_segmenter.py`

**Interfaces:**
- Produces: `FsmnVadSegmenter(model_dir: str = DEFAULT_MODEL_DIR, chunk_size=CHUNK_SIZE)` with `.feed(pcm: bytes) -> list[bytes]`、`.force_flush() -> list[bytes]`、`.reset() -> None`。模块常量 `BYTES_PER_MS=32`、`SAMPLE_RATE=16000`。`__init__` 内 lazy `from funasr import AutoModel`(模块导入零模型依赖,可 mock)。

- [ ] **Step 1: 创建 tests 目录骨架**

```bash
mkdir -p agent-asr/tests
```

Create `agent-asr/tests/__init__.py`(空文件,使 tests 成包):

```python
```

- [ ] **Step 2: 写失败测试(mock AutoModel)**

Create `agent-asr/tests/test_vad_segmenter.py`:

```python
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
    _patch_funasr(monkeypatch, value_seq=[[], [[200, 350]]])
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
```

- [ ] **Step 3: 跑测试确认失败(模块不存在)**

Run: `cd agent-asr && PYTHONPATH=$(pwd) pytest tests/test_vad_segmenter.py -v`
Expected: FAIL — `ModuleNotFoundError: asradapter.vad_segmenter`

- [ ] **Step 4: 实现 FsmnVadSegmenter**

Create `agent-asr/asradapter/vad_segmenter.py`:

```python
"""FSMN-VAD 流式分段器 — 把连续 PCM 流切成已完成语音段,供 SenseVoice 段级 recognize。

基于 FunASR AutoModel(FSMN-VAD)流式 chunk 推理:feed 增量喂音频,内部累积到
chunk_size 后 generate(is_final=False)取已完成段;force_flush 用 is_final=True
冲刷尾部。FunASR 返回的段时间戳是绝对 ms(跨 chunk 经 cache 累积),故需保留音频
并按绝对 ms 切片,已吐段不重复。

ms→字节:16kHz×16bit mono = 32 字节/ms。
"""
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # 32 bytes/ms (16-bit mono)

DEFAULT_MODEL_DIR = os.environ.get(
    "FSMN_VAD_MODEL_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "models", "speech_fsmn_vad_zh-cn-16k-common-pytorch",
    ),
)

# FunASR FSMN-VAD 流式 chunk 约定(60ms 单位 [0,10,5] = 600ms net chunk)
CHUNK_SIZE = [0, 10, 5]
ENCODER_CHUNK_LOOK_BACK = 4
DECODER_CHUNK_LOOK_BACK = 1
CHUNK_MS = CHUNK_SIZE[1] * 60  # 600ms 累积阈值


class FsmnVadSegmenter:
    """流式 VAD 分段器。线程不安全 —— 单 WS 连接独占一个实例。"""

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR, chunk_size=CHUNK_SIZE):
        from funasr import AutoModel

        self._chunk_size = chunk_size
        self._model = AutoModel(model=model_dir, disable_update=True)
        self._feed_buf = bytearray()    # 累积到 chunk_bytes 才喂模型
        self._audio = bytearray()       # 保留音频供段切片(绝对 ms 索引)
        self._audio_offset_ms = 0       # _audio[0] 对应的绝对 ms
        self._cache: dict[str, Any] = {}
        self._consumed_ms = 0           # 已吐段的最大 end_ms

    def feed(self, pcm: bytes) -> list[bytes]:
        """累积 pcm,每达 chunk 阈值跑一次 generate,返回新完成的语音段 PCM 列表。"""
        self._audio.extend(pcm)
        self._feed_buf.extend(pcm)
        chunk_bytes = CHUNK_MS * BYTES_PER_MS
        segments: list[bytes] = []
        while len(self._feed_buf) >= chunk_bytes:
            chunk = bytes(self._feed_buf[:chunk_bytes])
            del self._feed_buf[:chunk_bytes]
            segments.extend(self._run(chunk, is_final=False))
        return segments

    def force_flush(self) -> list[bytes]:
        """is_final=True 冲刷尾部残余音频,返回最后一段(若 VAD 判定为语音)。"""
        if not self._feed_buf:
            return []
        chunk = bytes(self._feed_buf)
        self._feed_buf.clear()
        return self._run(chunk, is_final=True)

    def reset(self) -> None:
        """重置所有内部状态(新一轮/barge-in 后)。"""
        self._feed_buf.clear()
        self._audio.clear()
        self._cache = {}
        self._audio_offset_ms = 0
        self._consumed_ms = 0

    def _run(self, chunk: bytes, is_final: bool) -> list[bytes]:
        try:
            res = self._model.generate(
                input=chunk,
                is_final=is_final,
                chunk_size=self._chunk_size,
                encoder_chunk_look_back=ENCODER_CHUNK_LOOK_BACK,
                decoder_chunk_look_back=DECODER_CHUNK_LOOK_BACK,
                cache=self._cache,
            )
        except Exception as e:
            logger.error("FSMN-VAD generate failed (is_final=%s): %s", is_final, e)
            raise
        emitted = self._extract_segments(res)
        self._drop_consumed_audio()
        return emitted

    def _extract_segments(self, res) -> list[bytes]:
        if not res or not isinstance(res[0], dict):
            return []
        value = res[0].get("value", [])
        out: list[bytes] = []
        for seg in value:
            start_ms, end_ms = int(seg[0]), int(seg[1])
            if end_ms <= self._consumed_ms:
                continue  # 已吐过
            start_ms = max(start_ms, self._consumed_ms)
            b0 = (start_ms - self._audio_offset_ms) * BYTES_PER_MS
            b1 = (end_ms - self._audio_offset_ms) * BYTES_PER_MS
            if b1 <= b0 or b1 > len(self._audio):
                continue
            out.append(bytes(self._audio[b0:b1]))
            self._consumed_ms = end_ms
        return out

    def _drop_consumed_audio(self) -> None:
        """丢弃已吐段之前的音频,控制 _audio 内存占用;保持偏移簿记一致。"""
        drop_ms = min(self._consumed_ms - self._audio_offset_ms,
                      len(self._audio) // BYTES_PER_MS)
        if drop_ms <= 0:
            return
        del self._audio[:drop_ms * BYTES_PER_MS]
        self._audio_offset_ms += drop_ms
```

- [ ] **Step 5: 跑测试确认全绿**

Run: `cd agent-asr && PYTHONPATH=$(pwd) pytest tests/test_vad_segmenter.py -v`
Expected: PASS(8 个用例全过)

- [ ] **Step 6: Commit**

```bash
git add agent-asr/asradapter/vad_segmenter.py agent-asr/tests/__init__.py agent-asr/tests/test_vad_segmenter.py
git commit -m "feat(asr): FSMN-VAD 流式分段器 FsmnVadSegmenter + mock 单测

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: agent-asr — WS handler 接入 segmenter + 多 final + reset + 降级

**Files:**
- Modify: `agent-asr/asradapter/ws_server.py`(整体重写 `ASRWebSocketHandler`)
- Modify: `agent-asr/asradapter/main.py`(lifespan 加载 segmenter,route 注入)
- Test: `agent-asr/tests/test_ws_server.py`

**Interfaces:**
- Consumes: `FsmnVadSegmenter`(Task 1)、`ASREngine.recognize(audio: bytes, params: dict) -> ASRResult`(现状不变)
- Produces: `ASRWebSocketHandler(engine, segmenter)`;协议 `config` 新增 `sample_rate`;服务端多次主动推 `{type:result,is_final:true}`;新增 `{type:reset}` 处理。

- [ ] **Step 1: 写失败测试(mock segmenter + engine)**

Create `agent-asr/tests/test_ws_server.py`:

```python
"""ASRWebSocketHandler 单测 — 验证多 final 主动推、reset、sample_rate resample、降级。

mock segmenter(返回固定段)+ mock engine(返回固定文本),不依赖真实模型。
"""
import asyncio
import json

import pytest

from asradapter.base import ASRResult
from asradapter.ws_server import ASRWebSocketHandler


class _FakeSegmenter:
    def __init__(self, segments_by_feed):
        self._seq = list(segments_by_feed)
        self._i = 0
        self.resets = 0
        self.flushed = False

    def feed(self, pcm):
        if self._i < len(self._seq):
            segs = self._seq[self._i]
            self._i += 1
            return segs
        return []

    def force_flush(self):
        self.flushed = True
        return []

    def reset(self):
        self.resets += 1


class _FakeEngine:
    def __init__(self, text="你好"):
        self._text = text

    async def recognize(self, audio, params):
        return ASRResult(text=self._text, confidence=0.95, is_final=True)


class _FakeWS:
    """最小 WebSocket 替身 —— 收消息队列 + 发送记录。"""

    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []

    async def accept(self):
        pass

    async def receive(self):
        if not self._incoming:
            await asyncio.sleep(0.01)
            raise asyncio.TimeoutError
        item = self._incoming.pop(0)
        if isinstance(item, str):
            return {"text": item}
        return {"bytes": item}

    async def send_json(self, obj):
        self.sent.append(obj)


def _config_msg(**over):
    msg = {"type": "config", "call_id": "c1", "language": "zh", "sample_rate": 16000}
    msg.update(over)
    return json.dumps(msg)


@pytest.mark.asyncio
async def test_segment_drives_proactive_final(monkeypatch):
    seg = _FakeSegmenter([[b"seg-a"], [b"seg-b"]])
    handler = ASRWebSocketHandler(_FakeEngine("hi"), seg)
    ws = _FakeWS([
        _config_msg(),
        b"frame1",  # feed 返回 [seg-a] → recognize → 推一个 final
        b"frame2",  # feed 返回 [seg-b] → 再推一个 final
        json.dumps({"type": "end"}),
    ])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert len(results) >= 2  # 单连接多 final
    assert all(m["is_final"] for m in results)


@pytest.mark.asyncio
async def test_reset_calls_segmenter_reset():
    seg = _FakeSegmenter([])
    handler = ASRWebSocketHandler(_FakeEngine(), seg)
    ws = _FakeWS([_config_msg(), json.dumps({"type": "reset"}), json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    assert seg.resets == 1


@pytest.mark.asyncio
async def test_sample_rate_8000_triggers_resample(monkeypatch):
    """declared 8k → 16k resample 后再喂 segmenter。"""
    called_sr = []

    class _Seg(_FakeSegmenter):
        def feed(self, pcm):
            called_sr.append(len(pcm))
            return super().feed(pcm)

    seg = _Seg([])
    handler = ASRWebSocketHandler(_FakeEngine(), seg)
    ws = _FakeWS([_config_msg(sample_rate=8000), b"\x01\x00" * 160, json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    # 160 samples @ 8k = 160ms;resample 到 16k = 320 samples = 640 bytes
    assert any(n == 640 for n in called_sr)


@pytest.mark.asyncio
async def test_degrade_falls_back_to_end_batch(monkeypatch):
    """segmenter.feed 抛异常 → 标记 degraded → 后续收 end 整段 recognize 兜底。"""

    class _BoomSeg(_FakeSegmenter):
        def feed(self, pcm):
            raise RuntimeError("vad boom")

    seg = _BoomSeg([])
    handler = ASRWebSocketHandler(_FakeEngine("fallback"), seg)
    ws = _FakeWS([_config_msg(), b"audiochunk", json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert len(results) == 1
    assert results[0]["text"] == "fallback"
```

> 注:异步测试用 `pytest-asyncio`(`@pytest.mark.asyncio` + auto 模式)。

- [ ] **Step 2: 确保异步测试依赖**

Run: `grep -i "pytest-asyncio" agent-asr/asradapter/requirements.txt || echo "MISSING"`
若 MISSING,在 `agent-asr/asradapter/requirements.txt` 追加 `pytest-asyncio==0.23.7`。

Create `agent-asr/pytest.ini`(启用 asyncio auto 模式,`@pytest.mark.asyncio` 自动生效):

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd agent-asr && PYTHONPATH=$(pwd) pytest tests/test_ws_server.py -v`
Expected: FAIL — `ASRWebSocketHandler.__init__() takes 2 positional arguments but 3 were given`(segmenter 参数还不存在)

- [ ] **Step 4: 重写 ws_server.py**

Replace the entire contents of `agent-asr/asradapter/ws_server.py`:

```python
"""WebSocket ASR 服务 — FSMN-VAD 流式分段 + 段级 recognize + 主动多次推 final。

协议:
    客户端 → 服务端:
        Text JSON: {"type":"config","call_id":"...","language":"zh","sample_rate":16000}
        Binary:    PCM 16-bit 16kHz mono 音频帧(全量喂)
        Text JSON: {"type":"end"}    (兜底:force_flush 冲刷尾部 / 降级整段识别)
        Text JSON: {"type":"reset"}  (barge-in:丢服务端进行中段)
    服务端 → 客户端:
        Text JSON: {"type":"result","text":"...","confidence":0.95,"is_final":true}
                    ↑ VAD 切段识别完主动推,单连接可多次(每语音段一个)
        Text JSON: {"type":"error","message":"..."}
"""
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from asradapter.base import ASREngine
from asradapter.vad_segmenter import FsmnVadSegmenter, SAMPLE_RATE as VAD_SAMPLE_RATE

logger = logging.getLogger(__name__)


def _resample_to_16k(pcm: bytes, declared_sr: int) -> bytes:
    """declared_sr → 16kHz 重采样(线性插值,够用;整数倍关系直接重采样)。"""
    if declared_sr == VAD_SAMPLE_RATE or declared_sr <= 0:
        return pcm
    # 16-bit mono samples;用 numpy 线性插值(已在依赖链:funasr 依赖 numpy)
    import numpy as np
    n_in = len(pcm) // 2
    if n_in == 0:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(round(n_in * VAD_SAMPLE_RATE / declared_sr))
    idx = np.linspace(0, n_in - 1, n_out)
    resampled = np.interp(idx, np.arange(n_in), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


class ASRWebSocketHandler:
    """WS handler — FSMN-VAD 分段层 + 段级 batch recognize。

    服务端 VAD 自检端点:每收帧喂 segmenter,得已完成段即 recognize 并主动推 final
    (不等 end)。segmenter 推理异常时降级:后续收 end 走整段 batch recognize 保可用。
    """

    def __init__(self, engine: ASREngine, segmenter: FsmnVadSegmenter):
        self._engine = engine
        self._segmenter = segmenter

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        call_id = ""
        language = "zh"
        declared_sr = VAD_SAMPLE_RATE
        degraded = False
        pending_audio: list[bytes] = []  # 降级路径累积用

        try:
            while True:
                data = await websocket.receive()

                if "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")

                    if msg_type == "config":
                        call_id = msg.get("call_id", "")
                        language = msg.get("language", "zh")
                        declared_sr = int(msg.get("sample_rate", VAD_SAMPLE_RATE))
                        logger.info("[WS-ASR] config call_id=%s sr=%d", call_id, declared_sr)

                    elif msg_type == "reset":
                        self._segmenter.reset()
                        pending_audio.clear()

                    elif msg_type == "end":
                        if degraded:
                            await self._batch_recognize(
                                websocket, b"".join(pending_audio), call_id, language)
                        else:
                            for seg in self._segmenter.force_flush():
                                await self._recognize_and_push(
                                    websocket, seg, call_id, language)
                        return

                elif "bytes" in data and data["bytes"]:
                    pcm16k = _resample_to_16k(data["bytes"], declared_sr)
                    pending_audio.append(pcm16k)
                    try:
                        segments = self._segmenter.feed(pcm16k)
                    except Exception as e:
                        logger.warning("[WS-ASR] VAD degraded call_id=%s: %s — fallback to end-batch", call_id, e)
                        degraded = True
                        segments = []
                    for seg in segments:
                        await self._recognize_and_push(websocket, seg, call_id, language)

        except WebSocketDisconnect:
            logger.info("[WS-ASR] client disconnected call_id=%s", call_id)
        except Exception as e:
            logger.error("[WS-ASR] error call_id=%s: %s", call_id, e)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass

    async def _recognize_and_push(
        self, websocket: WebSocket, audio: bytes, call_id: str, language: str,
    ) -> None:
        params = {"call_id": call_id, "language": language}
        try:
            result = await self._engine.recognize(audio, params)
        except Exception as e:
            logger.error("[WS-ASR] segment recognize error call_id=%s: %s", call_id, e)
            await websocket.send_json({"type": "error", "message": str(e)})
            return
        await websocket.send_json({
            "type": "result", "text": result.text,
            "confidence": result.confidence, "is_final": True,
        })

    async def _batch_recognize(
        self, websocket: WebSocket, audio_bytes: bytes, call_id: str, language: str,
    ) -> None:
        if not audio_bytes:
            await websocket.send_json({
                "type": "result", "text": "", "confidence": 0.0, "is_final": True})
            return
        await self._recognize_and_push(websocket, audio_bytes, call_id, language)
```

- [ ] **Step 5: 改 main.py — lifespan 加载 segmenter + 注入**

In `agent-asr/asradapter/main.py`:

5a. 找到 lifespan 内 `engine = load_asr_engine(...)` 后的 `if hasattr(engine, "load_model"): await engine.load_model()` 块,在其后追加 segmenter 加载。同时 `global` 声明加 `_segmenter`:

```python
_segmenter = None  # 模块级,与 engine/_grpc_server 并列
```

```python
async def lifespan(app: FastAPI):
    global engine, _grpc_server, _segmenter
    ...
    engine = load_asr_engine(config["engine"]["asr"])
    if hasattr(engine, "load_model"):
        await engine.load_model()
    logger.info(f"ASR engine loaded: {config['engine']['asr']}")

    # ── FSMN-VAD 分段器(WS 路径用,模型全局单例)──
    from asradapter.vad_segmenter import FsmnVadSegmenter
    _segmenter = FsmnVadSegmenter()
    logger.info("FSMN-VAD segmenter loaded")
    ...
```

5b. WS route 构造 handler 处注入 segmenter(原来 `ASRWebSocketHandler(engine)`):

```python
    handler = ASRWebSocketHandler(engine, _segmenter)
```

- [ ] **Step 6: 跑测试确认全绿**

Run: `cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v`
Expected: PASS(test_vad_segmenter + test_ws_server 全过)

- [ ] **Step 7: Commit**

```bash
git add agent-asr/asradapter/ws_server.py agent-asr/asradapter/main.py agent-asr/tests/test_ws_server.py agent-asr/pytest.ini agent-asr/asradapter/requirements.txt
git commit -m "feat(asr): WS handler 接入 FSMN-VAD 分段层 + 多 final + reset + 降级

Co-Authored-By: Claude <noreply@anthropic.com>"
```

> ⚠️ **真实 FunASR API 验证(Task 7 必做)**:本 task 的 mock 用 `res[0]["value"]=[[s,e]]` 结构。真实 FunASR FSMN-VAD 流式返回的时间戳语义(绝对 vs 相对)与 cache 用法,需 Task 7 用真实模型 smoke 验证。若返回相对时间戳,`_extract_segments` 的偏移逻辑需相应调整 —— 这是设计文档点名的"实现时验证"项。

---

## Task 3: agent-flow — RMSGate 门禁 + 平移测试

**Files:**
- Create: `agent-flow/src/ws/rms_gate.py`
- Create: `agent-flow/tests/ws/test_rms_gate.py`
- (保留 `vad.py` —— handler 仍在用,Task 5 后才删)

**Interfaces:**
- Produces: `RMSGate(threshold: float, snr_factor: float, noise_floor_init: float, noise_adapt_rate: float)`;`.is_speech(frame: bytes) -> bool`(RMS+SNR,非语音帧内部 EMA 更新底噪);`.reset() -> None`(不清底噪)。常量 `FRAME_BYTES=960`。

- [ ] **Step 1: 写失败测试(从 test_vad.py 平移)**

Create `agent-flow/tests/ws/test_rms_gate.py`:

```python
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
    """reset 不清底噪(一通通话内环境底噪稳定,跨轮复用更准)。"""
    gate = RMSGate(threshold=0.0, snr_factor=3.0, noise_floor_init=100.0, noise_adapt_rate=0.1)
    for _ in range(40):
        gate.is_speech(_frame(500.0))
    converged = gate._noise_floor
    gate.reset()
    assert gate._noise_floor == pytest.approx(converged)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ws/test_rms_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: ws.rms_gate`

- [ ] **Step 3: 实现 RMSGate**

Create `agent-flow/src/ws/rms_gate.py`:

```python
"""RMS + SNR 自适应门禁 — barge-in 低延迟语音检测。

从 WebRTCVAD._has_speech_energy + _update_noise_floor 抽出(去 WebRTC 频谱判定):
- is_speech(frame):RMS + SNR 自适应门限判语音;返回 False(非语音)时内部 EMA 更新底噪
- reset():清语音状态但保留底噪(一通通话内环境底噪稳定,跨轮复用)

门限两种模式:
- 自适应 SNR(snr_factor > 0):门限 = max(noise_floor * snr_factor, threshold)
- 固定(snr_factor <= 0):门限 = threshold(threshold<=0 时不过滤)
"""
import logging

import numpy as np

logger = logging.getLogger(__name__)

# 16kHz 16-bit mono PCM: 30ms frame = 960 bytes
SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000) * 2  # 960


class RMSGate:
    """RMS + SNR 自适应语音门禁。线程不安全 —— 单通话独占。"""

    def __init__(
        self,
        threshold: float = 300.0,
        snr_factor: float = 3.0,
        noise_floor_init: float = 300.0,
        noise_adapt_rate: float = 0.1,
    ) -> None:
        self._threshold = threshold
        self._snr_factor = snr_factor
        self._noise_floor = noise_floor_init
        self._noise_adapt_rate = noise_adapt_rate

    def is_speech(self, frame: bytes) -> bool:
        """判单帧是否语音。非语音帧用 EMA 更新底噪(双向,随环境浮动)。"""
        if self._threshold <= 0 and self._snr_factor <= 0:
            return True
        _f32 = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(_f32 ** 2)))
        if self._snr_factor > 0:
            threshold = max(self._noise_floor * self._snr_factor, self._threshold)
            is_speech = rms > threshold
        else:
            is_speech = rms > self._threshold
        if not is_speech:
            self._update_noise_floor(rms)
        return is_speech

    def _update_noise_floor(self, rms: float) -> None:
        """非语音帧 EMA 平滑更新底噪(仅 snr_factor>0 时有意义)。"""
        if self._snr_factor <= 0:
            return
        a = self._noise_adapt_rate
        self._noise_floor = self._noise_floor * (1 - a) + rms * a

    def reset(self) -> None:
        """重置一轮状态;底噪不重置(跨轮复用,跨通话由新实例归零)。"""
        # 当前无语音状态字段;底噪显式保留。预留扩展点。
        return
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ws/test_rms_gate.py -v`
Expected: PASS(8 用例全过)

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/ws/rms_gate.py agent-flow/tests/ws/test_rms_gate.py
git commit -m "feat(flow): RMSGate(RMS+SNR 自适应门禁)从 WebRTCVAD 抽出 + 平移 8 用例

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: agent-flow — asr_ws_client on_final + AsrStreamingManager per-call

**Files:**
- Modify: `agent-flow/src/clients/asr_ws_client.py`(receiver_loop 多 final + on_final + send_reset)
- Modify: `agent-flow/src/ws/asr_streaming.py`(通透 on_final + reset_server_segment)
- Test: `agent-flow/tests/clients/test_asr_ws_client.py`、`agent-flow/tests/ws/test_asr_streaming_on_final.py`

**Interfaces:**
- Consumes: 现状 `ASRWebSocketClient.create_stream`、`ASRWsStream` 接口
- Produces:
  - `ASRWsStream(..., on_final: Callable[[dict], None] | None = None)`;`on_final` 收到每个 `result` 即触发(receiver_loop 不再首结果 break);`.send_reset() -> None`(排队 `{type:reset}`)
  - `AsrStreamingManager(..., on_final=None)`;WS 模式 stream per-call(feed 首帧创建,finalize/cancel **不**销毁,call 结束才销毁);`.reset_server_segment(call_id) -> None`(WS 模式发 reset,gRPC/无 provider 时 no-op)
- **向后兼容**:`on_final=None` 时 receiver_loop 保持旧行为(首 result break),finalize 仍可用 → gRPC/HTTP 路径不受影响。

- [ ] **Step 1: 写 asr_ws_client 失败测试**

Create `agent-flow/tests/clients/__init__.py`(空) + `agent-flow/tests/clients/test_asr_ws_client.py`:

```python
"""ASRWsStream 多 final + on_final + send_reset 单测。

直接驱动 _receiver_loop(注入假 _ws),不连真实 socket。
"""
import asyncio
import json

import pytest

from clients.asr_ws_client import ASRWsStream


class _FakeWS:
    """recv 依次返回 msg 列表,之后永久阻塞(模拟连接保持)。"""
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []
        self.closed = False

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(10)  # 模拟连接保持,不返回

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_on_final_fires_per_result_and_loops():
    finals = []
    stream = ASRWsStream("ws://x", "c1", streaming=True,
                         on_final=lambda r: finals.append(r["text"]))
    ws = _FakeWS([
        json.dumps({"type": "result", "text": "你好", "confidence": 0.9}),
        json.dumps({"type": "result", "text": "第二句", "confidence": 0.9}),
    ])
    stream._ws = ws
    task = asyncio.create_task(stream._receiver_loop())
    # 给 receiver 时间处理两条
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert finals == ["你好", "第二句"]  # 多 final,不首条 break


@pytest.mark.asyncio
async def test_send_reset_enqueues_reset_message():
    stream = ASRWsStream("ws://x", "c1", streaming=True, on_final=lambda r: None)
    stream._queue = asyncio.Queue()
    stream.send_reset()
    item = stream._queue.get_nowait()
    assert json.loads(item) == {"type": "reset"}


@pytest.mark.asyncio
async def test_no_on_final_keeps_legacy_single_result_break():
    """on_final=None → 旧行为:首 result 后 break(receiver_loop 退出)。"""
    stream = ASRWsStream("ws://x", "c1", streaming=True, on_final=None)
    ws = _FakeWS([json.dumps({"type": "result", "text": "只此一句", "confidence": 0.9})])
    stream._ws = ws
    await stream._receiver_loop()  # 应正常返回(不抛 CancelledError)
    assert stream._result is not None
    assert stream._result["text"] == "只此一句"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/clients/test_asr_ws_client.py -v`
Expected: FAIL — `AttributeError: 'ASRWsStream' object has no attribute 'send_reset'`(及 on_final 多 final 行为未实现)

- [ ] **Step 3: 改 asr_ws_client.py**

3a. `ASRWsStream.__init__` 加 `on_final` 参数(在 `on_partial` 后):

```python
    def __init__(
        self,
        base_url: str,
        call_id: str,
        streaming: bool = False,
        on_partial: Callable[[str, float], None] | None = None,
        on_final: Callable[[dict], None] | None = None,
    ):
        self._base_url = base_url
        self._call_id = call_id
        self._streaming = streaming
        self._on_partial = on_partial
        self._on_final = on_final
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._queue: asyncio.Queue | None = None
        self._sender_task: asyncio.Task | None = None
        self._receiver_task: asyncio.Task | None = None
        self._partial_text = ""
        self._result: dict | None = None
        self._result_event = asyncio.Event()
```

3b. 重写 `_receiver_loop` 的 `result` 分支 —— `on_final` 模式下不 break,每次触发回调;无 `on_final` 保持旧行为:

```python
                elif msg_type == "result":
                    result_dict = {
                        "text": msg.get("text", ""),
                        "confidence": msg.get("confidence", 0.0),
                        "is_final": True,
                        "minio_key": msg.get("minio_key") or None,
                    }
                    logger.info(
                        "[WS-ASR] result call_id=%s text=%s confidence=%.2f",
                        self._call_id, result_dict["text"], result_dict["confidence"],
                    )
                    if self._on_final:
                        # 服务端驱动多 final:每次 result 触发回调,继续接收
                        try:
                            self._on_final(result_dict)
                        except Exception as e:
                            logger.error("[WS-ASR] on_final callback error call_id=%s: %s", self._call_id, e)
                        continue
                    # 旧行为(无 on_final):首结果落地 + break
                    self._result = result_dict
                    self._result_event.set()
                    break
```

3c. 新增 `send_reset` 方法(在 `send_audio` 后):

```python
    def send_reset(self) -> None:
        """发 {type:reset} —— barge-in 时丢服务端进行中段(连接保持)。"""
        if self._queue is None:
            return
        self._queue.put_nowait(json.dumps({"type": "reset"}))
```

3d. `create_stream` 透传 `on_final`:

```python
    def create_stream(self, call_id: str, streaming: bool = False,
                      on_partial=None, on_final=None) -> "ASRWsStream | None":
        if not self._started:
            return None
        return ASRWsStream(self._base_url, call_id, streaming=streaming,
                           on_partial=on_partial, on_final=on_final)
```

- [ ] **Step 4: 跑 asr_ws_client 测试确认绿**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/clients/test_asr_ws_client.py -v`
Expected: PASS(3 用例)

- [ ] **Step 5: 写 AsrStreamingManager on_final / reset 测试**

Create `agent-flow/tests/ws/test_asr_streaming_on_final.py`:

```python
"""AsrStreamingManager on_final 通透 + reset_server_segment 单测。"""
import asyncio

import pytest

from ws.asr_streaming import AsrStreamingManager


class _FakeStream:
    def __init__(self):
        self.reset_calls = 0
        self.audio = []

    async def start(self): pass

    def send_audio(self, chunk): self.audio.append(chunk)

    def send_reset(self): self.reset_calls += 1

    async def finish(self): return {"text": "x", "confidence": 0.9, "is_final": True}

    async def cancel(self): pass


class _FakeClient:
    def __init__(self):
        self.last_on_final = None

    def create_stream(self, call_id, streaming=False, on_partial=None, on_final=None):
        self.last_on_final = on_final
        return _FakeStream()


@pytest.mark.asyncio
async def test_feed_threads_on_final_to_stream():
    client = _FakeClient()
    mgr = AsrStreamingManager(asr_ws_client=client, use_ws_streaming=True, on_final=lambda r: None)
    await mgr.feed(b"\x00" * 960, "c1")
    assert client.last_on_final is not None


@pytest.mark.asyncio
async def test_reset_server_segment_sends_reset():
    client = _FakeClient()
    mgr = AsrStreamingManager(asr_ws_client=client, use_ws_streaming=True, on_final=lambda r: None)
    await mgr.feed(b"\x00" * 960, "c1")  # 建 stream
    await mgr.reset_server_segment("c1")
    assert mgr._stream.reset_calls == 1


@pytest.mark.asyncio
async def test_reset_no_provider_is_noop():
    mgr = AsrStreamingManager()  # 无 provider
    await mgr.reset_server_segment("c1")  # 不抛
```

- [ ] **Step 6: 改 asr_streaming.py**

6a. `__init__` 加 `on_final` 参数:

```python
    def __init__(
        self,
        asr_grpc_client=None,
        asr_ws_client=None,
        use_grpc_streaming: bool = False,
        use_ws_streaming: bool = False,
        use_streaming_asr: bool = False,
        on_final=None,
    ) -> None:
        self._asr_grpc_client = asr_grpc_client
        self._asr_ws_client = asr_ws_client
        self._use_grpc_streaming = use_grpc_streaming
        self._use_ws_streaming = use_ws_streaming
        self._use_streaming_asr = use_streaming_asr
        self._on_final = on_final  # WS 服务端驱动 final 回调(per-call)
        self._stream = None
        self._speech_started = False
        self._partial_text = ""
```

6b. `feed` 内 `create_stream` 调用透传 `on_final`:

```python
            self._stream = provider.create_stream(
                call_id, streaming=self._use_streaming_asr,
                on_partial=_on_partial if self._use_streaming_asr else None,
                on_final=self._on_final,
            )
```

6c. 新增 `reset_server_segment`(在 `finalize` 后、`cancel` 前):

```python
    async def reset_server_segment(self, call_id: str) -> None:
        """WS 模式:发 {type:reset} 丢服务端进行中段(barge-in 用,连接保持)。

        无 stream / 非 WS 模式 → no-op。
        """
        if self._stream is not None and hasattr(self._stream, "send_reset"):
            try:
                self._stream.send_reset()
            except Exception as e:
                logger.warning("[%s] reset_server_segment failed: %s", call_id, e)
```

> 注:WS 模式下 `finalize` 不再被新 handler 调用(端点改 on_final),但保留供 gRPC/HTTP 兜底 —— 无需改动其逻辑。

- [ ] **Step 7: 跑全部新增测试确认绿**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/clients/test_asr_ws_client.py tests/ws/test_asr_streaming_on_final.py -v`
Expected: PASS(6 用例)

- [ ] **Step 8: Commit**

```bash
git add agent-flow/src/clients/asr_ws_client.py agent-flow/src/ws/asr_streaming.py agent-flow/tests/clients/ agent-flow/tests/ws/test_asr_streaming_on_final.py
git commit -m "feat(flow): asr_ws_client on_final 多 final 回调 + send_reset, manager per-call 通透

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: agent-flow — handler 控制流反转(TurnController + on_final + barge-in RMS)

**Files:**
- Modify: `agent-flow/src/ws/handler.py`(新增 `TurnController` 类 + 重写 `handle` 接收循环 / barge-in / `_reset_audio_state` / `_receive_during_streaming` / 构造函数)
- Modify: `agent-flow/main.py`(`rms_gate_factory` 注入,暂用现有 settings 名)
- Delete: `agent-flow/src/ws/vad.py`、`agent-flow/tests/ws/test_vad.py`
- Test: `agent-flow/tests/ws/test_handler.py`

**Interfaces:**
- Consumes: `RMSGate`(Task 3)、`AsrStreamingManager.on_final` / `.reset_server_segment`(Task 4)
- Produces: `TurnController(launch_fn, reset_fn, min_text_len=2)`;`.on_final(result: dict)`、`.cancel_for_barge() -> Task|None`、`.lock`、`.streaming_task`、`.turn_count`。`StreamingCallHandler.__init__` 参数 `vad_factory` → `rms_gate_factory`。

> **并发模型说明(实现依据)**:端点从主循环同步触发 → ASR receiver task 异步回调。`TurnController.lock` 串行化 `streaming_task` 的 check-and-set(`on_final` launch vs `cancel_for_barge` 取消)。`audio_buffer` 不入锁 —— asyncio 协作式调度下 `on_final` 只在 await 点运行,主循环 `audio_buffer.extend()` 在 await 间原子完成,launch 时快照 `bytes(audio_buffer)` 后 reset,无竞态损坏。AI 说话期主循环走 barge-in 分支、不喂 asr → 服务端无新音频 → 不产新 final,轮次天然串行;lock 内 `.done()` 检查是残余段/竞态兜底。

- [ ] **Step 1: 写 TurnController 失败测试**

Create `agent-flow/tests/ws/test_handler.py`:

```python
"""TurnController 并发安全 + 短文本丢弃(handler 控制流反转核心)。"""
import asyncio

import pytest

from ws.handler import TurnController


async def _never_complete():
    await asyncio.Event().wait()  # 永不完成


def _make_launch(record):
    def launch(result, turn):
        task = asyncio.create_task(_never_complete())
        record.append((turn, result.get("text")))
        return task
    return launch


@pytest.mark.asyncio
async def test_on_final_launches_turn():
    launched = []
    tc = TurnController(_make_launch(launched), lambda: None)
    await tc.on_final({"text": "你好"})
    assert launched == [(1, "你好")]
    assert tc.turn_count == 1
    assert tc.streaming_task is not None
    tc.streaming_task.cancel()
    try:
        await tc.streaming_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_on_final_drops_second_while_turn_active():
    """轮次进行中,第二个 final 丢弃(不并发起第二个轮次)。"""
    launched = []
    tc = TurnController(_make_launch(launched), lambda: None)
    await tc.on_final({"text": "第一句"})
    await tc.on_final({"text": "第二句"})  # streaming_task 仍 active → 丢弃
    assert launched == [(1, "第一句")]
    tc.streaming_task.cancel()
    try:
        await tc.streaming_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_on_final_drops_short_text():
    launched = []
    tc = TurnController(_make_launch(launched), lambda: None)
    await tc.on_final({"text": "啊"})  # < 2 字
    await tc.on_final({"text": ""})    # 空
    assert launched == []
    assert tc.streaming_task is None


@pytest.mark.asyncio
async def test_cancel_for_barge_clears_task_and_calls_reset():
    reset_calls = []
    tc = TurnController(_make_launch([]), lambda: reset_calls.append(1))
    await tc.on_final({"text": "你好"})
    old = await tc.cancel_for_barge()
    assert old is tc.streaming_task or old is not None
    assert tc.streaming_task is None
    assert len(reset_calls) == 1
    old.cancel()
    try:
        await old
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ws/test_handler.py -v`
Expected: FAIL — `ImportError: cannot import name 'TurnController' from 'ws.handler'`

- [ ] **Step 3: 在 handler.py 加 TurnController 类**

在 `agent-flow/src/ws/handler.py` 顶部 import 区改 `ws.vad` 引用为 `rms_gate`:

```python
from ws.rms_gate import RMSGate
```
(删 `from ws.vad import BaseVAD, SimpleVAD`)

在 `class StreamingCallHandler:` **之前**插入 `TurnController`:

```python
class TurnController:
    """streaming_task 生命周期 + turn_lock —— 解耦 on_final/barge-in 并发,便于单测。

    lock 串行化 streaming_task 的 check-and-set:on_final launch vs cancel_for_barge 取消。
    reset_fn(清 audio_buffer/jitter/rms_gate)在锁内调用,确保 launch 快照后再清。
    """

    def __init__(self, launch_fn, reset_fn, min_text_len: int = 2):
        self._launch_fn = launch_fn   # (result_dict, turn:int) -> asyncio.Task
        self._reset_fn = reset_fn     # () -> None
        self._min_text_len = min_text_len
        self.lock = asyncio.Lock()
        self.streaming_task: asyncio.Task | None = None
        self.turn_count = 0

    async def on_final(self, result: dict) -> None:
        async with self.lock:
            if self.streaming_task and not self.streaming_task.done():
                logger.info("final while turn active, drop")
                return
            text = (result.get("text") or "").strip()
            if len(text) < self._min_text_len:
                logger.info("ASR final too short ('%s'), skip", text)
                return
            self.turn_count += 1
            self.streaming_task = self._launch_fn(result, self.turn_count)
            self._reset_fn()

    async def cancel_for_barge(self) -> "asyncio.Task | None":
        async with self.lock:
            task = self.streaming_task
            self.streaming_task = None
            self._reset_fn()
            return task
```

- [ ] **Step 4: 改 StreamingCallHandler 构造函数**

`agent-flow/src/ws/handler.py` 的 `__init__`:把 `vad_factory` 参数改名 `rms_gate_factory`,实例字段同步:

```python
    def __init__(
        self,
        pre_llm_fn,
        streaming_fn,
        esl: "ESLClient | None" = None,
        handoff_extension: str = "1001",
        registry: ActiveCallRegistry | None = None,
        rms_gate_factory: "callable | None" = None,
        barge_in_min_audio_bytes: int = 1600,
        jitter_target_depth: int = 3,
        jitter_max_depth: int = 10,
        denoiser: BaseDenoiser | None = None,
        apm: "WebRTCAPM | None" = None,
        asr_grpc_client: "ASRGrpcClient | None" = None,
        use_grpc_streaming: bool = False,
        asr_ws_client: "ASRWebSocketClient | None" = None,
        use_ws_streaming: bool = False,
        use_streaming_asr: bool = False,
        tts_prebuffer_frames: int = 0,
    ) -> None:
        self._pre_llm_fn = pre_llm_fn
        self._streaming_fn = streaming_fn
        self._esl = esl
        self._handoff_extension = handoff_extension
        self._registry = registry
        self._rms_gate_factory = rms_gate_factory
        self._barge_in_min_audio_bytes = barge_in_min_audio_bytes
        self._BARGE_IN_RMS_THRESHOLD = _settings.barge_in_rms_threshold
        self._jitter_target_depth = jitter_target_depth
        self._jitter_max_depth = jitter_max_depth
        self._denoiser = denoiser or PassThroughDenoiser()
        self._apm = apm
        self._asr_grpc_client = asr_grpc_client
        self._use_grpc_streaming = use_grpc_streaming
        self._asr_ws_client = asr_ws_client
        self._use_ws_streaming = use_ws_streaming
        self._use_streaming_asr = use_streaming_asr
        self._tts_prebuffer_frames = tts_prebuffer_frames
```

- [ ] **Step 5: 重写 handle() 关键段落**

5a. `handle()` 开头:把 `vad = self._vad_factory() ...` 改为 `rms_gate`,加 `TurnController` + 带 `on_final` 的 manager。找到原:

```python
        active_call = self._resolve_active_call(call_id, biz_type, user_key, tenant_id, scenario)
        vad = self._vad_factory() if self._vad_factory else SimpleVAD()
        jitter = JitterBuffer(target_depth=self._jitter_target_depth, max_depth=self._jitter_max_depth)
        audio_buffer = bytearray()
```

替换为:

```python
        active_call = self._resolve_active_call(call_id, biz_type, user_key, tenant_id, scenario)
        rms_gate = self._rms_gate_factory() if self._rms_gate_factory else RMSGate()
        jitter = JitterBuffer(target_depth=self._jitter_target_depth, max_depth=self._jitter_max_depth)
        audio_buffer = bytearray()
```

5b. 找到原 ASR streaming state 块:

```python
        # ASR streaming state
        asr = AsrStreamingManager(
            asr_grpc_client=self._asr_grpc_client,
            asr_ws_client=self._asr_ws_client,
            use_grpc_streaming=self._use_grpc_streaming,
            use_ws_streaming=self._use_ws_streaming,
            use_streaming_asr=self._use_streaming_asr,
        )
        precomputed_asr_result: dict | None = None
```

替换为(在 manager 之前定义 TurnController + launch/reset 闭包,manager 带 on_final):

```python
        # TurnController:端点由 ASR final 回调驱动(控制流反转)
        def _launch_turn(result: dict, turn: int):
            raw_audio = self._gain_audio(audio_buffer, audio_gain)
            return asyncio.create_task(
                self._process_streaming_turn(
                    websocket, call_id, biz_type, user_key,
                    raw_audio, turn, active_call,
                    barge_in_event=barge_in_event,
                    precomputed_asr_result=result,
                    ai_spoken_event=ai_has_spoken,
                    tts_buffer=tts_buffer,
                    tenant_id=tenant_id,
                    scenario=scenario,
                ),
                name=f"stream-{call_id}-{turn}",
            )

        turn_ctrl = TurnController(
            launch_fn=_launch_turn,
            reset_fn=lambda: self._reset_audio_state(audio_buffer, rms_gate, jitter),
        )

        # ASR streaming state —— on_final=turn_ctrl.on_final 驱动轮次(WS 服务端分段)
        asr = AsrStreamingManager(
            asr_grpc_client=self._asr_grpc_client,
            asr_ws_client=self._asr_ws_client,
            use_grpc_streaming=self._use_grpc_streaming,
            use_ws_streaming=self._use_ws_streaming,
            use_streaming_asr=self._use_streaming_asr,
            on_final=turn_ctrl.on_final if self._use_ws_streaming else None,
        )
```

(删 `precomputed_asr_result: dict | None = None` —— 不再需要,结果经回调流入)

5c. 删原 AI 说话分支对 `streaming_task` 的本地赋值会与 TurnController 冲突 —— 改为读写 `turn_ctrl.streaming_task`。找到原 barge-in 检测块开头:

```python
                if streaming_task and not streaming_task.done():
```

及内部所有 `streaming_task` 引用,改为 `turn_ctrl.streaming_task`。具体:
- `if streaming_task and not streaming_task.done():` → `if turn_ctrl.streaming_task and not turn_ctrl.streaming_task.done():`
- barge_detected 后的清理块(原 `tts_buffer.clear()` ... `streaming_task = None` ... `continue`)整段替换为:

```python
                    if barge_detected:
                        tts_buffer.clear()
                        logger.info("[%s] barge-in: TTS buffer cleared", call_id)
                        fire_insert_event(
                            call_id=call_id, fs_uuid=call_id,
                            biz_type=biz_type, user_id=user_key, user_key=user_key,
                            event_type="barge_in", payload={"turn": turn_ctrl.turn_count},
                        )
                        await asr.cancel()
                        await asr.reset_server_segment(call_id)  # 丢服务端进行中段
                        old_task = await turn_ctrl.cancel_for_barge()
                        if old_task and not old_task.done():
                            old_task.cancel()
                        # cooldown:本块在 handle() 方法体内,vad_cooldown_until 是其局部变量,直接赋值即可
                        # (无需 nonlocal —— nonlocal 仅用于嵌套函数;此处直接 rebind 局部)
                        vad_cooldown_until = time.monotonic() + _settings.vad_cooldown_after_bargein
                        barge_in_event.clear()
                        ai_has_spoken.clear()
                        ai_spoken_buffer_cleared = False
                        continue
```

- `elif streaming_task.done():` → `elif turn_ctrl.streaming_task.done():`,块内 `streaming_task = None` → 用 `turn_ctrl` 重置。原:

```python
                    elif streaming_task.done():
                        exc = streaming_task.exception()
                        if exc:
                            logger.error("[%s] streaming task error: %s", call_id, exc)
                        else:
                            logger.info("[%s] streaming turn completed", call_id)
                        streaming_task = None
                        self._reset_audio_state(audio_buffer, vad, jitter)
                        barge_in_event.clear()
                        continue
```

替换为:

```python
                    elif turn_ctrl.streaming_task.done():
                        exc = turn_ctrl.streaming_task.exception()
                        if exc:
                            logger.error("[%s] streaming task error: %s", call_id, exc)
                        else:
                            logger.info("[%s] streaming turn completed", call_id)
                        turn_ctrl.streaming_task = None
                        barge_in_event.clear()
                        continue
```

5d. 正常接收分支:删 VAD 门控 + 端点检测,音频全量喂 asr。原:

```python
                if "bytes" in data and data["bytes"]:
                    # barge-in 冷却期：丢弃残余音频，防止 VAD 误触发
                    if time.monotonic() < vad_cooldown_until:
                        continue

                    frame = data["bytes"]
                    jitter.insert(frame)

                    while True:
                        smooth_frame = jitter.drain()
                        if not smooth_frame:
                            break
                        denoised_frame = self._process_near_frame(smooth_frame, tts_buffer)

                        # VAD 门控：确认语音后才缓冲音频、创建 ASR 流
                        if not vad.speech_detected:
                            # 仅更新 VAD 状态机，不缓冲、不喂 ASR
                            vad.is_end_of_speech(denoised_frame, 0)
                            continue

                        audio_buffer.extend(denoised_frame)

                        await asr.feed(denoised_frame, call_id)

                        # VAD 端点检测
                        if vad.is_end_of_speech(denoised_frame, len(audio_buffer)):
                            if active_call and active_call.cancel.is_set():
                                break

                            turn_count += 1
                            ... (整段 _process_streaming_turn 启动逻辑) ...
                            break  # 回到外层循环进入 barge-in 检测模式
```

替换为(全量喂、无门控、无端点 —— 端点由 on_final 触发):

```python
                if "bytes" in data and data["bytes"]:
                    # barge-in 冷却期:丢弃残余音频,防止 RMS 误触发
                    if time.monotonic() < vad_cooldown_until:
                        continue

                    frame = data["bytes"]
                    jitter.insert(frame)

                    while True:
                        smooth_frame = jitter.drain()
                        if not smooth_frame:
                            break
                        denoised_frame = self._process_near_frame(smooth_frame, tts_buffer)
                        audio_buffer.extend(denoised_frame)
                        # 全量喂 ASR —— 服务端 FSMN-VAD 分段,final 经 on_final 触发轮次
                        await asr.feed(denoised_frame, call_id)
```

(删 `turn_count` 局部变量声明 —— 由 `turn_ctrl.turn_count` 接管。若 `turn_count` 在循环外仍被引用,改为 `turn_ctrl.turn_count`)

5e. 删原 `streaming_task: asyncio.Task | None = None` 局部声明(在 barge-in state 块),改由 `turn_ctrl` 管理。但 `_cleanup` 仍需访问当前 task —— 见 5f。

5f. `_cleanup` 签名改为收 `turn_ctrl`。原:

```python
    async def _cleanup(
        self, streaming_task: asyncio.Task | None, asr: AsrStreamingManager,
        tts_buffer: TTSOutputBuffer, call_id: str, turn_count: int,
    ) -> None:
        ...
        if streaming_task and not streaming_task.done():
            streaming_task.cancel()
            try:
                await streaming_task
```

替换为:

```python
    async def _cleanup(
        self, turn_ctrl: "TurnController", asr: AsrStreamingManager,
        tts_buffer: TTSOutputBuffer, call_id: str,
    ) -> None:
        streaming_task = turn_ctrl.streaming_task
        if streaming_task and not streaming_task.done():
            streaming_task.cancel()
            try:
                await streaming_task
            except (asyncio.CancelledError, Exception):
                pass
        if asr.stream is not None:
            try:
                await asr.cancel()
            except Exception:
                pass
        await tts_buffer.stop()
        if self._registry:
            self._registry.unregister(call_id)
        logger.info("[%s] WS closed, total turns=%d", call_id, turn_ctrl.turn_count)
```

`handle()` finally 块的 `_cleanup(...)` 调用同步改:

```python
        finally:
            await self._cleanup(turn_ctrl, asr, tts_buffer, call_id)
```

5g. `_receive_during_streaming`:参数 `vad: BaseVAD` → `rms_gate: RMSGate`,签名 + 内部 `vad.is_speech` 改 `rms_gate.is_speech`。原 barge-in 判定行:

```python
                    is_speech = frame_rms > self._BARGE_IN_RMS_THRESHOLD and vad.is_speech(denoised_frame)
```

替换为:

```python
                    is_speech = frame_rms > self._BARGE_IN_RMS_THRESHOLD and rms_gate.is_speech(denoised_frame)
```

方法签名 `vad: BaseVAD,` 改 `rms_gate: RMSGate,`;调用处(`_receive_during_streaming` 在主循环的调用)把传参 `vad` → `rms_gate`。

5h. `_reset_audio_state`:`vad: BaseVAD` → `rms_gate: RMSGate`,`vad.reset()` → `rms_gate.reset()`:

```python
    def _reset_audio_state(self, audio_buffer: bytearray, rms_gate: RMSGate, jitter: JitterBuffer) -> None:
        """重置所有音频处理状态,准备下一轮。"""
        audio_buffer.clear()
        rms_gate.reset()
        jitter.reset()
        self._denoiser.reset()
```

- [ ] **Step 6: 改 main.py 注入 rms_gate_factory(暂用现有 settings 名)**

`agent-flow/main.py`:

```python
from src.ws.rms_gate import RMSGate
```
(替换 `from src.ws.vad import create_vad`)

```python
    rms_gate_factory = lambda: RMSGate(
        threshold=settings.vad_rms_threshold,
        snr_factor=settings.vad_snr_factor,
        noise_floor_init=settings.vad_noise_floor_init,
        noise_adapt_rate=settings.vad_noise_adapt_rate,
    )
```
(替换 `vad_factory = lambda: create_vad(settings)`,注意:Task 6 会把 `vad_rms_*` 改名 `rms_gate_*`,届时此处同步改)

构造 handler 时 `vad_factory=vad_factory` → `rms_gate_factory=rms_gate_factory`。

启动日志行 `logger.info("  VAD: %s", settings.vad_type)` 改:

```python
    logger.info("  RMS gate: threshold=%.0f snr=%.1f", settings.vad_rms_threshold, settings.vad_snr_factor)
```

- [ ] **Step 7: 删 vad.py + test_vad.py**

```bash
git rm agent-flow/src/ws/vad.py agent-flow/tests/ws/test_vad.py
```

- [ ] **Step 8: 跑全量 agent-flow 测试确认绿**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`
Expected: PASS(test_handler 4 用例 + test_rms_gate 8 用例 + test_asr_streaming_on_final 3 用例 + 现有 test_asr_streaming/test_audio_processing/test_registry/test_tts_output_buffer_reverse)

> 若 `test_asr_streaming.py`(旧)因 manager 签名变化失败:检查是否因 `on_final` 新参数 —— 旧测试不传 `on_final` 应默认 None 兼容;若旧测试 mock 了 `create_stream` 签名,补 `on_final=None` kwarg。

- [ ] **Step 9: 冒烟:agent-flow 能 import + 启动到 lifespan 就绪(不连真实服务)**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.ws.handler import StreamingCallHandler, TurnController; from ws.rms_gate import RMSGate; print('import ok')"`
Expected: `import ok`(无 ImportError)

- [ ] **Step 10: Commit**

```bash
git add agent-flow/src/ws/handler.py agent-flow/main.py agent-flow/tests/ws/test_handler.py
git commit -m "refactor(flow): handler 控制流反转 TurnController+on_final,删 VAD 引擎,barge-in 用 RMSGate

Co-Authored-By: Claude <noreply@anthropic.com>"
git rm agent-flow/src/ws/vad.py agent-flow/tests/ws/test_vad.py 2>/dev/null; git commit -m "chore(flow): 删除已废弃的 vad.py + test_vad.py

Co-Authored-By: Claude <noreply@anthropic.com>" || true
```

(若 Step 7 已 `git rm`,Step 10 第二个 commit 跳过)

---

## Task 6: agent-flow — config 精简/改名 + .env 清理 + main 同步

**Files:**
- Modify: `agent-flow/src/config.py`
- Modify: `agent-flow/main.py`(rms_gate_factory 读新字段名)
- Modify: `agent-flow/.env`

**Interfaces:**
- Produces:`Settings` 删 6 项(`vad_type`/`vad_aggressiveness`/`vad_silence_frames`/`vad_silero_threshold`/`vad_silero_min_silence_ms`/`vad_min_audio_bytes`),改名 5 项(`vad_rms_threshold`→`rms_gate_threshold` 等、`vad_cooldown_after_bargein`→`cooldown_after_bargein`),`asr_use_ws` 默认 `True`。

- [ ] **Step 1: 改 config.py**

在 `agent-flow/src/config.py` 找到 VAD 配置块(约 84-116 行),替换为:

```python
    # RMS 门禁(barge-in 低延迟语音检测,RMS+SNR 自适应底噪)
    # 帧能量低于 threshold 视为静音(过滤 SIP 底噪);snr_factor>0 时门限=noise_floor*snr_factor
    rms_gate_threshold: float = 300.0
    # 自适应噪声底噪:门限随环境底噪浮动(安静时低、嘈杂时抬高),解决固定门限在嘈杂环境失效
    rms_gate_snr_factor: float = 3.0
    # 初始噪声底噪估计(启动/换通话的 warm-up 基线)
    rms_gate_noise_floor_init: float = 300.0
    # 底噪 EMA 更新率(0-1,越大越快收敛);0.1 ≈ 1s 收敛
    rms_gate_noise_adapt_rate: float = 0.1

    # Barge-in
    barge_in_min_audio_bytes: int = 1600
    # Barge-in RMS 阈值:AEC 场景调高(过滤残留回声尖峰),默认 300;.env 实测调优 1500
    barge_in_rms_threshold: int = 300

    # Barge-in 后冷却(秒):丢弃残余音频防 RMS 误触发
    cooldown_after_bargein: float = 0.5
```

(即删除 `vad_type`/`vad_aggressiveness`/`vad_silence_frames`/`vad_silero_threshold`/`vad_silero_min_silence_ms`/`vad_min_audio_bytes`/`vad_rms_threshold`/`vad_snr_factor`/`vad_noise_floor_init`/`vad_noise_adapt_rate`/`vad_cooldown_after_bargein` 共 11 行,替换为上述 8 行)

`asr_use_ws` 默认改 true(约 148 行):

```python
    # ASR WebSocket streaming(主传输)
    asr_use_ws: bool = True
    asr_ws_url: str = "ws://127.0.0.1:8080/ws/asr/streaming-recognize"
```

- [ ] **Step 2: 改 main.py 读新字段名**

`agent-flow/main.py` 的 `rms_gate_factory`:

```python
    rms_gate_factory = lambda: RMSGate(
        threshold=settings.rms_gate_threshold,
        snr_factor=settings.rms_gate_snr_factor,
        noise_floor_init=settings.rms_gate_noise_floor_init,
        noise_adapt_rate=settings.rms_gate_noise_adapt_rate,
    )
```

handler.py Step 5c 里 barge-in 的 cooldown 引用 `_settings.vad_cooldown_after_bargein` 改 `_settings.cooldown_after_bargein`(若 Task 5 留了旧名)。

启动日志:

```python
    logger.info("  RMS gate: threshold=%.0f snr=%.1f", settings.rms_gate_threshold, settings.rms_gate_snr_factor)
```

- [ ] **Step 3: 改 .env —— 删 3 项,保留 barge_in 调优值**

编辑 `agent-flow/.env`:删除以下 3 行:

```
CALLBOT_VAD_AGGRESSIVENESS=3
CALLBOT_VAD_SILENCE_FRAMES=40
CALLBOT_VAD_MIN_AUDIO_BYTES=12800
```

**保留**(原样,勿改值):

```
CALLBOT_BARGE_IN_MIN_AUDIO_BYTES=3200
CALLBOT_BARGE_IN_RMS_THRESHOLD=1500
CALLBOT_ASR_USE_WS=true
CALLBOT_ASR_WS_URL=ws://127.0.0.1:8080/ws/asr/streaming-recognize
```

(可选)按需新增 RMS gate 调优(默认值已合理,通常无需):

```
# CALLBOT_RMS_GATE_THRESHOLD=300
# CALLBOT_RMS_GATE_SNR_FACTOR=3
```

- [ ] **Step 4: 验证 config 加载 + 全量测试**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.config import settings; print('rms_gate_threshold=', settings.rms_gate_threshold, 'asr_use_ws=', settings.asr_use_ws, 'barge_in_rms=', settings.barge_in_rms_threshold)"`
Expected: `rms_gate_threshold= 300.0 asr_use_ws= True barge_in_rms= 1500`(barge_in 来自 .env=1500,证明 .env 保留生效)

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`
Expected: PASS(全绿)

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/config.py agent-flow/main.py agent-flow/.env
git commit -m "chore(flow): config 精简删 6 项 VAD 配置、改名 rms_gate_*/cooldown,asr_use_ws 默认 true

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: 端到端联调 + 真实 FSMN 模型 smoke

**Files:** 无代码改动(验证 task)

- [ ] **Step 1: 双仓库全量测试**

Run: `cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v`
Expected: PASS

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`
Expected: PASS

- [ ] **Step 2: 真实 FSMN-VAD API smoke(验证 Task 2 留的"实现时验证"项)**

Create 临时脚本 `/tmp/fsmn_smoke.py`(不进仓库):

```python
"""验证真实 FunASR FSMN-VAD 流式 generate 返回结构 —— 确认 value/时间戳语义。"""
import sys
sys.path.insert(0, "agent-asr")
from asradapter.vad_segmenter import FsmnVadSegmenter, BYTES_PER_MS

seg = FsmnVadSegmenter()
# 喂 1.2s 非静音 PCM(模拟语音)+ 0.6s 静音
audio = (b"\x10\x00" * (1200 * BYTES_PER_MS // 2)) + (b"\x00\x00" * (600 * BYTES_PER_MS // 2))
out = seg.feed(audio)
print("feed segments:", [(len(s), len(s) // BYTES_PER_MS, "ms") for s in out])
tail = seg.force_flush()
print("flush tail:", [(len(s), len(s) // BYTES_PER_MS, "ms") for s in tail])
```

Run: `cd agent-asr && PYTHONPATH=$(pwd) python /tmp/fsmn_smoke.py`
Expected: 打印非空段列表(证明 `value` 结构 + 时间戳切片正确)。

> **若输出空或报错**:FunASR 流式返回结构与 mock 假设不符。检查 `res[0]` 实际 key(可能非 `value` 而是 `text`/`timestamp`)、时间戳是否相对。修正 `vad_segmenter.py:_extract_segments` 后重跑本步,并相应更新 Task 1 mock 的 value 结构注释。

- [ ] **Step 3: 服务联调(按 CLAUDE.md 启动顺序)**

```bash
docker compose up -d                 # pg/redis/minio
./scripts/local.sh stop flow asr     # 确保这两个先停
./scripts/local.sh asr               # ASR(含新 segmenter,看日志 "FSMN-VAD segmenter loaded")
./scripts/local.sh flow              # flow(看日志 "RMS gate: threshold=300 snr=3.0")
```

期望日志:
- agent-asr:`FSMN-VAD segmenter loaded`
- agent-flow:`RMS gate: threshold=300 snr=3.0`、`ASR transport: grpc=False ws=True`

- [ ] **Step 4: 真实通话 smoke(可选,需软电话/外呼环境)**

发起一通测试通话,观察:
- 用户说话 → 停顿 → agent-flow 日志出现 `stream-{call_id}-N` task launch(证明 on_final 触发轮次)
- 说话期间 agent 回复被打断 → 日志 `barge-in: TTS buffer cleared`(证明 RMS 门禁 + reset_server_segment)
- agent-asr 日志每段一个 `[WS-ASR] result ...`(证明多 final)

若 barge-in 误触发频繁(§4.3 回退风险):调高 `.env` `CALLBOT_BARGE_IN_RMS_THRESHOLD`(当前 1500,可试 2000)+ `CALLBOT_BARGE_IN_MIN_AUDIO_BYTES`(当前 3200),重测。

- [ ] **Step 5: 收尾 commit(若有 smoke 中发现的 hotfix)**

```bash
# 仅当 Step 2/4 触发了 vad_segmenter 或阈值调整
git add -A
git commit -m "fix(asr/flow): e2e 联调 hotfix(FSMN API 适配 / barge-in 阈值)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Deferred(设计 §7 中明确推迟项,记录备查)

以下 §7 行本期**不实现**,理由 + 触发条件记录如下(YAGNI:无实际观测证据前不提前设计):

| §7 行 | 状态 | 理由 / 触发条件 |
|---|---|---|
| **flow 侧无 final 超时兜底**(N 秒无 final → 主动 end 冲刷 + 告警) | ⏸ 推迟 | FSMN-VAD 自身 `max_single_segment_time`(默认 60s)已强切段;纯静音无音频→无 final→无轮次是正确行为。**触发**:若实测出现"音频持续流入但长期无 final"(VAD 卡在某段不切),再在 `handle()` 加一个 `last_final_at` 时间戳 + watchdog task。提前实现需考虑与 `turn_lock` 的交互,无观测数据前易引入 bug。 |
| **asr WS 连接失败/中途断开 → flow 重连** | ⏸ 推迟 | 现状 `asr_ws_client` 仅 log 错误不重连(既有行为,非本次引入)。**触发**:若生产观测到 WS 断开后通话卡死,再加重连 + segmenter 随新连接 reset。本期 Task 7 Step 4 通话 smoke 应观测此项。 |

---

## Self-Review 核对(实现者每完成一个 task 自查)

- **Spec 覆盖**:§9 七步 → Task 1-7 一一对应;§4.3 控制流反转 → Task 5 TurnController;§6 协议(partial 退役 / reset 新增)→ Task 2+4;§4.4 配置 → Task 6;§7 降级 → Task 2 `_BoomSeg` 测试 + degraded 路径。
- **类型一致**:`RMSGate.is_speech(frame)->bool`、`TurnController.on_final(dict)`、`AsrStreamingManager.reset_server_segment(call_id)`、`ASRWsStream.send_reset()` 跨 task 名称一致。
- **向后兼容**:Task 4 `on_final=None` 保持 gRPC/HTTP 旧路径;Task 5 `rms_gate_factory=None` 走默认 `RMSGate()`。
- **每 task 可独立 commit**:Task 1/2(agent-asr 自洽)、Task 3(rms_gate 独立)、Task 4(client+manager 向后兼容)、Task 5(handler 切换 + 删 vad)、Task 6(config 改名)、Task 7(验证)。
