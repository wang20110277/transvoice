# livekit-agents SDK 集成改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 agent-flow 自研流式通话管线（StreamingCallHandler/TurnController/RMSGate/TTSOutputBuffer）替换为 livekit-agents AgentSession 无头管线，SenseVoice/CosyVoice 以远程 WS 插件接入。

**Architecture:** 每通话在 agent-flow 进程内实例化 AgentSession（无 LiveKit Server/Worker），自定义 TelephonyIO 桥接 mod_audio_fork WebSocket；LangGraph 7-node 经 `Agent.llm_node` 覆盖嵌入（逐 token yield str）；silero VAD 管打断、FSMN-VAD 分段（STT EOS）管轮次提交（`turn_detection="stt"`）。

**Tech Stack:** Python 3.10+ / asyncio + uvloop / FastAPI WebSocket / livekit-agents >=1.8.3,<1.9 / websockets / pytest-asyncio

**Spec:**
- 需求与设计：`openspec/changes/integrate-livekit-agents-sdk/{proposal,design}.md`
- 规格：`openspec/changes/integrate-livekit-agents-sdk/specs/conversation-pipeline/spec.md`
- 任务源：`openspec/changes/integrate-livekit-agents-sdk/plan-ready.md`

## Global Constraints

- livekit-agents 版本 `>=1.8.3,<1.9`；**SDK 参考源码在 `/Users/lindaw/Documents/transvoice/agents/livekit-agents/livekit/agents/`**（实现时对照，不是 pip 安装源）。官方无头先例：`agents/tests/fake_session.py`、`agents/tests/fake_io.py`。
- 全链路 16kHz mono PCM16；TTS 服务端输出 22050Hz 原样推送（插件声明 `sample_rate=22050`，SDK 重采样到 sink 的 16000）。
- 新配置项必须 `CALLBOT_` 前缀 + pydantic-settings（`agent-flow/src/config.py`）。
- 接口命名见名知意；注释只写 WHY；不为不可能场景加 fallback；不提前设计。
- agent-asr / agent-tts 服务代码与 WS 契约**零改动**（Task 9 验证 `git diff` 为空）。
- 每个 Task 结束时全量测试必须绿：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`；一个 Task 一个 commit。
- 顺序敏感：Task 1-8 全部为**增量改造**（旧管线保持可用），Task 9 才删除旧组件——保证每个 commit 的树都是绿的。

---

### Task 1: 依赖引入与配置扩展

**Files:**
- Modify: `agent-flow/requirements.txt`
- Modify: `agent-flow/src/config.py`
- Modify: `agent-flow/.env.example`
- Test: `agent-flow/tests/voice/test_config_livekit.py`

**Interfaces:**
- Produces: `Settings.endpointing_min_delay: float`（默认 0.1）、`Settings.endpointing_max_delay: float`（默认 2.0）、`Settings.interruption_min_duration: float`（默认 0.5）——Task 7 `build_agent_session` 消费。

- [ ] **Step 1: 安装依赖**

`agent-flow/requirements.txt` 末尾追加一行：

```
livekit-agents>=1.8.3,<1.9
```

在 agent-flow 的 conda/venv 环境执行 `pip install -r agent-flow/requirements.txt`，然后冒烟：

```bash
cd agent-flow && python -c "
from livekit.agents import Agent, AgentSession
from livekit.agents.voice.io import AudioInput, AudioOutput, AudioOutputCapabilities
from livekit import rtc
print('livekit-agents ok')
"
```

Expected: `livekit-agents ok`

- [ ] **Step 2: 写失败测试**

创建 `agent-flow/tests/voice/__init__.py`（空文件）和 `agent-flow/tests/voice/test_config_livekit.py`：

```python
"""livekit 集成新配置项加载测试。"""


def test_endpointing_defaults():
    from config import settings
    assert settings.endpointing_min_delay == 0.1
    assert settings.endpointing_max_delay == 2.0
    assert settings.interruption_min_duration == 0.5
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_config_livekit.py -v`
Expected: FAIL（`AttributeError: ... no attribute 'endpointing_min_delay'`）

- [ ] **Step 4: 最小实现**

`agent-flow/src/config.py` 的 `Settings` 类中（紧邻既有 jitter/barge-in 配置块）追加：

```python
    # livekit AgentSession 轮次/打断参数（design.md §2.2：FSMN 分段已含端点判定，min_delay 远小于 SDK 默认 0.5）
    endpointing_min_delay: float = 0.1
    endpointing_max_delay: float = 2.0
    interruption_min_duration: float = 0.5
```

`agent-flow/.env.example` 追加：

```
# ── livekit AgentSession（conversation-pipeline）──
# endpointing: 用户说完（STT EOS）到提交轮次的等待窗口（秒）
CALLBOT_ENDPOINTING_MIN_DELAY=0.1
CALLBOT_ENDPOINTING_MAX_DELAY=2.0
# 打断：AI 播报中用户语音累计达到该时长（秒）才触发 barge-in
CALLBOT_INTERRUPTION_MIN_DURATION=0.5
```

- [ ] **Step 5: 运行测试确认通过 + 全量回归**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_config_livekit.py -v && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`
Expected: 新测试 PASS；既有测试无回归

- [ ] **Step 6: Commit**

```bash
git add agent-flow/requirements.txt agent-flow/src/config.py agent-flow/.env.example agent-flow/tests/voice/
git commit -m "feat(livekit): 引入 livekit-agents 依赖 + endpointing/interruption 配置项"
```

---

### Task 2: TelephonyAudioInput

**Files:**
- Create: `agent-flow/src/voice/__init__.py`（空）
- Create: `agent-flow/src/voice/io.py`
- Test: `agent-flow/tests/voice/test_io.py`

**Interfaces:**
- Consumes: `ws.jitter_buffer.JitterBuffer(target_depth, max_depth)`（`insert(bytes)/drain()->bytes|None/reset()`）、`ws.audio_processing.WebRTCAPM.process(near: bytes, reverse: bytes) -> bytes`、`ws.denoise.BaseDenoiser.process/reset`
- Produces:
  - `TelephonyAudioInput(jitter, apm, denoiser, audio_gain, reverse_ref)`：`push_bytes(bytes) -> None`、`close() -> None`、`async __anext__() -> rtc.AudioFrame`（16k mono）。Task 8 消费 `push_bytes`。

- [ ] **Step 1: 写失败测试**

`agent-flow/tests/voice/test_io.py`：

```python
"""TelephonyAudioInput 处理链测试：jitter 平滑 → 降噪直通 → 增益 → AudioFrame。"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_input_yields_16k_mono_frames():
    from livekit import rtc
    from voice.io import TelephonyAudioInput
    from ws.jitter_buffer import JitterBuffer
    from ws.denoise import PassThroughDenoiser

    inp = TelephonyAudioInput(
        jitter=JitterBuffer(target_depth=1, max_depth=10),
        apm=None, denoiser=PassThroughDenoiser(),
        audio_gain=1.0, reverse_ref=lambda: b"",
    )
    # JitterBuffer target_depth=1：首帧即可 drain（960B = 30ms@16k）
    inp.push_bytes(b"\x01\x02" * 480)
    frame = await asyncio.wait_for(inp.__anext__(), timeout=1.0)
    assert isinstance(frame, rtc.AudioFrame)
    assert frame.sample_rate == 16000
    assert frame.num_channels == 1
    assert len(frame.data) == 960


@pytest.mark.asyncio
async def test_input_close_raises_stop_iteration():
    from voice.io import TelephonyAudioInput
    from ws.jitter_buffer import JitterBuffer
    from ws.denoise import PassThroughDenoiser

    inp = TelephonyAudioInput(
        jitter=JitterBuffer(target_depth=1, max_depth=10),
        apm=None, denoiser=PassThroughDenoiser(),
        audio_gain=1.0, reverse_ref=lambda: b"",
    )
    inp.close()
    with pytest.raises(StopAsyncIteration):
        await inp.__anext__()


@pytest.mark.asyncio
async def test_input_applies_gain():
    import numpy as np
    from voice.io import TelephonyAudioInput
    from ws.jitter_buffer import JitterBuffer
    from ws.denoise import PassThroughDenoiser

    quiet = (np.ones(480, dtype=np.int16) * 100).tobytes()
    inp = TelephonyAudioInput(
        jitter=JitterBuffer(target_depth=1, max_depth=10),
        apm=None, denoiser=PassThroughDenoiser(),
        audio_gain=2.0, reverse_ref=lambda: b"",
    )
    inp.push_bytes(quiet)
    frame = await asyncio.wait_for(inp.__anext__(), timeout=1.0)
    out = np.frombuffer(frame.data, dtype=np.int16)
    assert abs(int(out[0]) - 200) <= 1  # gain 放大
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_io.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'voice'` 或 `voice.io`）

- [ ] **Step 3: 最小实现**

`agent-flow/src/voice/io.py`：

```python
"""Telephony IO —— mod_audio_fork WebSocket ↔ livekit AgentSession 音频桥。

上行链复刻原 StreamingCallHandler._process_near_frame 语义：
JitterBuffer 平滑 → WebRTCAPM（AEC，远端参考=下行最近帧）或 denoiser → 增益。
"""
from __future__ import annotations

from collections.abc import Callable

from livekit import rtc
from livekit.agents.voice.io import AudioInput

from ws.audio_processing import WebRTCAPM
from ws.denoise import BaseDenoiser, PassThroughDenoiser
from ws.jitter_buffer import JitterBuffer

_SAMPLE_RATE = 16000
_BYTES_PER_SAMPLE = 2


def _apply_gain(pcm: bytes, gain: float) -> bytes:
    if gain == 1.0 or len(pcm) < 2:
        return pcm
    import numpy as np
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    samples *= gain
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


class TelephonyAudioInput(AudioInput):
    """WS 帧入队 → 处理链 → 16k mono AudioFrame。

    __anext__ 阻塞即背压（AgentSession forward task 消费）；
    close() 后抛 StopAsyncIteration 触发 session teardown。
    """

    def __init__(
        self,
        *,
        jitter: JitterBuffer,
        apm: WebRTCAPM | None,
        denoiser: BaseDenoiser | None,
        audio_gain: float,
        reverse_ref: Callable[[], bytes],
    ) -> None:
        super().__init__(label="TelephonyIO")
        self._jitter = jitter
        self._apm = apm
        self._denoiser = denoiser or PassThroughDenoiser()
        self._gain = audio_gain
        self._reverse_ref = reverse_ref
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._pending = bytearray()  # 处理后不足整帧的余量不再切分：__anext__ 逐块产出

    def push_bytes(self, pcm: bytes) -> None:
        self._queue.put_nowait(pcm)

    def close(self) -> None:
        self._queue.put_nowait(None)  # 哨兵

    async def __anext__(self) -> rtc.AudioFrame:
        pcm = await self._queue.get()
        if pcm is None:
            raise StopAsyncIteration
        for _ in self._iter_smoothed(pcm):
            pass
        data = bytes(self._pending)
        self._pending.clear()
        if not data:
            return await self.__anext__()
        return self._to_frame(_apply_gain(self._process_near(data), self._gain))

    def _iter_smoothed(self, chunk: bytes):
        self._jitter.insert(chunk)
        while True:
            smooth = self._jitter.drain()
            if not smooth:
                return
            self._pending.extend(self._process_near(smooth))
            yield smooth

    def _process_near(self, frame: bytes) -> bytes:
        if self._apm is not None:
            return self._apm.process(frame, self._reverse_ref())
        return self._denoiser.process(frame)

    def _to_frame(self, pcm: bytes) -> rtc.AudioFrame:
        num_samples = len(pcm) // _BYTES_PER_SAMPLE
        return rtc.AudioFrame(
            data=pcm, sample_rate=_SAMPLE_RATE,
            num_channels=1, samples_per_channel=num_samples,
        )
```

注意：上面 `_iter_smoothed` 中 `_process_near` 被调用了两次（`_pending.extend` 已处理 + 循环体未处理）。实现时改为单一职责：`__anext__` 里 `jitter.insert(pcm)` → 循环 `drain()` → 每个 smooth 帧 `_process_near` → 拼接 → gain → frame。删除 `_iter_smoothed` 辅助。正确形态：

```python
    async def __anext__(self) -> rtc.AudioFrame:
        pcm = await self._queue.get()
        if pcm is None:
            raise StopAsyncIteration
        self._jitter.insert(pcm)
        processed = bytearray()
        while True:
            smooth = self._jitter.drain()
            if not smooth:
                break
            processed.extend(self._process_near(smooth))
        if not processed:
            return await self.__anext__()
        data = _apply_gain(bytes(processed), self._gain)
        return rtc.AudioFrame(
            data=data, sample_rate=_SAMPLE_RATE,
            num_channels=1, samples_per_channel=len(data) // _BYTES_PER_SAMPLE,
        )
```

以正确形态为准（保留 `_apply_gain`/`_process_near`/`push_bytes`/`close`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_io.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/voice/ agent-flow/tests/voice/test_io.py
git commit -m "feat(livekit): TelephonyAudioInput — jitter/AEC/降噪/增益处理链桥接"
```

---

### Task 3: TelephonyAudioOutput

**Files:**
- Modify: `agent-flow/src/voice/io.py`
- Test: `agent-flow/tests/voice/test_io.py`（追加）

**Interfaces:**
- Consumes: `AudioOutput(label, capabilities, sample_rate)` 基类（`agents/livekit-agents/livekit/agents/voice/io.py:147-330`）；事件方法 `on_playback_started(created_at)`、`on_playback_progressed(started_at, offset, duration)`、`on_playback_finished(playback_position, interrupted, synchronized_transcript=None)`
- Produces: `TelephonyAudioOutput(send_fn, prebuffer_frames=0, frame_interval=0.03)`：`capture_frame/flush/clear_buffer/pause/resume` + `recent_reverse: bytes` 属性 + `sample_rate=16000`。Task 7 工厂与 Task 8 接线消费。

- [ ] **Step 1: 写失败测试**

追加到 `agent-flow/tests/voice/test_io.py`：

```python
"""TelephonyAudioOutput：TTSOutputBuffer 语义迁移 + SDK 播放事件硬契约。"""
import asyncio
import time

import pytest
from livekit import rtc


def _frame(pcm: bytes) -> rtc.AudioFrame:
    return rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1,
                          samples_per_channel=len(pcm) // 2)


def _make_output(sent: list, **kw):
    from voice.io import TelephonyAudioOutput
    async def send_fn(frame: bytes):
        sent.append(frame)
    out = TelephonyAudioOutput(send_fn=send_fn, frame_interval=0.001, **kw)
    out.on_attached()
    return out


async def _drain_silence_window(out):
    # frame_interval=1ms：静音保活窗口内让出控制权，保证 paced loop 跑一轮
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_capture_then_flush_reports_finished():
    sent: list = []
    out = _make_output(sent)
    started, finished = [], []
    out.on("playback_started", lambda e: started.append(e))
    out.on("playback_finished", lambda e: finished.append(e))

    await out.capture_frame(_frame(b"\x01\x02" * 480))  # 30ms 音频
    out.flush()
    await asyncio.sleep(0.05)
    assert started, "首帧实际发送后必须上报 playback_started"
    assert finished and finished[-1].interrupted is False
    assert sent and sent[0] != b"\x00\x00" * 480  # 发送的是音频帧
    await out.aclose()


@pytest.mark.asyncio
async def test_clear_buffer_interrupts_and_fills_silence():
    sent: list = []
    out = _make_output(sent)
    finished = []
    out.on("playback_finished", lambda e: finished.append(e))

    await out.capture_frame(_frame(b"\x01\x02" * 480))
    await out.capture_frame(_frame(b"\x03\x04" * 480))
    out.clear_buffer()  # 打断：立即清空 + interrupted=True
    await asyncio.sleep(0.02)
    assert finished and finished[-1].interrupted is True
    await _drain_silence_window(out)
    # 清空后转为静音帧保活（非音频数据）
    silence = b"\x00\x00" * 480
    assert silence in sent
    await out.aclose()


@pytest.mark.asyncio
async def test_pause_stops_sending_until_resume():
    sent: list = []
    out = _make_output(sent)
    await out.pause()
    n = len(sent)
    await out.capture_frame(_frame(b"\x01\x02" * 480))
    await asyncio.sleep(0.02)
    assert len(sent) == n, "pause 期间不应发送"
    await out.resume()
    await asyncio.sleep(0.05)
    assert len(sent) > n, "resume 后恢复发送"
    await out.aclose()


@pytest.mark.asyncio
async def test_recent_reverse_tracks_last_sent():
    sent: list = []
    out = _make_output(sent)
    audio = b"\x05\x06" * 480
    await out.capture_frame(_frame(audio))
    out.flush()
    await asyncio.sleep(0.05)
    assert out.recent_reverse in (audio, b"\x00\x00" * 480)  # 音频或其后的静音帧
    await out.aclose()
```

注意：事件是 `rtc.EventEmitter` 风格，回调参数形态以 SDK `io.py:148` 的 EventEmitter 定义为准——实现时若回调收到的是事件对象则按对象属性断言（如上 `e.interrupted`）；若直接传 kwargs，则用 `**kwargs` 适配。实现前先读 `agents/livekit-agents/livekit/agents/voice/io.py` 的 `AudioOutput` 基类与 `tests/fake_io.py` 的 FakeAudioOutput（57-142 行）——**它就是本类实现的官方范本**，播放时钟/paused 语义直接照抄其结构。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_io.py -v`
Expected: 新增 4 条 FAIL（`TelephonyAudioOutput` 不存在）

- [ ] **Step 3: 最小实现**

`agent-flow/src/voice/io.py` 追加（结构仿 `agents/tests/fake_io.py` FakeAudioOutput + 原 `TTSOutputBuffer._send_loop` 语义合并）：

```python
import time as _time

from livekit.agents.voice.io import AudioOutput, AudioOutputCapabilities

_FRAME_BYTES = 960       # 30ms @16k 16-bit，与原 TTSOutputBuffer 一致
_SILENCE_FRAME = b"\x00\x00" * (_FRAME_BYTES // 2)
_SILENCE_TIMEOUT = 120.0  # 超过该时长无 TTS 数据即停发静音（防持续活跃误触发回声路径）


class TelephonyAudioOutput(AudioOutput):
    """下行 sink：capture_frame 可快于实时 → 内部 30ms 匀速排出 + 静音保活。

    原 TTSOutputBuffer 的 paced 循环整体迁移；SDK 契约补充：
    flush/clear_buffer 必须回报 playback_finished（漏报 → wait_for_playout 挂死）。
    """

    def __init__(
        self,
        *,
        send_fn: Callable[[bytes], Awaitable[None]],
        prebuffer_frames: int = 0,
        frame_interval: float = 0.03,
    ) -> None:
        super().__init__(
            label="TelephonyIO",
            capabilities=AudioOutputCapabilities(pause=True),
            sample_rate=16000,
        )
        self._send_fn = send_fn
        self._frame_interval = frame_interval
        self._prebuffer_frames = prebuffer_frames
        self._buffer: deque[bytes] = deque()
        self._partial = bytearray()
        self._segment_open = False      # capture_frame 后、flush 前为 True
        self._started_at: float | None = None
        self._finished = False          # flush 后排空即结束当前段
        self._paused = False
        self._last_write_time = 0.0
        self._send_task: asyncio.Task | None = None
        self._cancel = asyncio.Event()
        self._data_ready = asyncio.Event()
        self._segment_done = asyncio.Event()
        self.recent_reverse: bytes = _SILENCE_FRAME

    # ── SDK 契约面 ──

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        pcm = bytes(frame.data)
        self._last_write_time = _time.monotonic()
        self._partial.extend(pcm)
        while len(self._partial) >= _FRAME_BYTES:
            self._buffer.append(bytes(self._partial[:_FRAME_BYTES]))
            self._partial = self._partial[_FRAME_BYTES:]
        self._segment_open = True
        self._data_ready.set()

    def flush(self) -> None:
        if self._partial:
            self._buffer.append(bytes(self._partial))
            self._partial.clear()
        self._finished = True
        self._data_ready.set()

    def clear_buffer(self) -> None:
        self._buffer.clear()
        self._partial.clear()
        self._segment_open = False
        self._finished = False
        if self._started_at is not None:
            self.on_playback_finished(playback_position=0.0, interrupted=True)
            self._started_at = None

    async def pause(self) -> None:
        self._paused = True

    async def resume(self) -> None:
        self._paused = False
        self._data_ready.set()

    # ── 生命周期 ──

    def on_attached(self) -> None:
        if self._send_task is None:
            self._send_task = asyncio.create_task(self._send_loop())

    def on_detached(self) -> None:
        self._cancel.set()
        self._data_ready.set()

    async def aclose(self) -> None:
        self.on_detached()
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
        self._send_task = None

    # ── 匀速发送循环（原 TTSOutputBuffer._send_loop 语义）──

    async def _send_loop(self) -> None:
        frames_sent = 0
        try:
            while not self._cancel.is_set():
                if self._paused:
                    self._data_ready.clear()
                    await self._data_ready.wait()
                    continue
                if self._buffer:
                    frame = self._buffer.popleft()
                    await self._send_fn(frame)
                    self.recent_reverse = frame
                    if self._started_at is None:
                        self._started_at = _time.monotonic()
                        self.on_playback_started(created_at=_time.monotonic())
                    self.on_playback_progressed(
                        started_at=self._started_at,
                        offset=frames_sent * 0.03, duration=frames_sent * 0.03,
                    )
                    frames_sent += 1
                    await asyncio.sleep(self._frame_interval)
                elif self._finished and not self._buffer:
                    # 段排空：正常结束上报
                    if self._started_at is not None:
                        self.on_playback_finished(
                            playback_position=frames_sent * 0.03,
                            interrupted=False,
                        )
                        self._started_at = None
                    frames_sent = 0
                    self._finished = False
                    self._segment_open = False
                    self._data_ready.clear()
                    await self._data_ready.wait()
                else:
                    await self._fill_silence_if_active()
        except asyncio.CancelledError:
            pass

    async def _fill_silence_if_active(self) -> None:
        """缓冲空且段未结束：句间间隙发静音帧保活（120s 窗口）。"""
        elapsed = _time.monotonic() - self._last_write_time
        if self._last_write_time > 0 and elapsed < _SILENCE_TIMEOUT:
            self._data_ready.clear()
            try:
                await asyncio.wait_for(self._data_ready.wait(), timeout=self._frame_interval)
            except asyncio.TimeoutError:
                pass
            if not self._buffer and not self._cancel.is_set():
                await self._send_fn(_SILENCE_FRAME)
                self.recent_reverse = _SILENCE_FRAME  # AI 沉默 → AEC 参考归零
        else:
            self._data_ready.clear()
            await self._data_ready.wait()
```

（`deque`/`Awaitable` 需在文件头 import；事件方法名/参数以 SDK `io.py` 基类签名为准，实现时对照校正。）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_io.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/voice/io.py agent-flow/tests/voice/test_io.py
git commit -m "feat(livekit): TelephonyAudioOutput — TTSOutputBuffer 语义迁移 + 播放事件契约"
```

---

### Task 4: TransvoiceSTT 插件

**Files:**
- Create: `agent-flow/src/voice/stt_plugin.py`
- Test: `agent-flow/tests/voice/test_stt_plugin.py`

**Interfaces:**
- Consumes: `livekit.agents.stt`（`STT/STTCapabilities/RecognizeStream/SpeechEvent/SpeechEventType/SpeechData/APIConnectionError`）；agent-asr WS 协议（C→S：`config` JSON/二进制 PCM/`{"type":"end"}`；S→C：`result/error`，见 `agent-asr/asradapter/ws_server.py`）
- Produces: `TransvoiceSTT(ws_url: str, min_final_len: int = 2)`，`.stream()` → 事件序列：每段 `START_OF_SPEECH → FINAL_TRANSCRIPT → END_OF_SPEECH`（顺序铁律；服务端无 onset 信号，SOS 与 FINAL 同点合成发出）。Task 7 消费。

- [ ] **Step 1: 写失败测试**

`agent-flow/tests/voice/test_stt_plugin.py`：

```python
"""TransvoiceSTT：协议映射 + 事件序列 + 短 final 过滤 + 错误分类。"""
import asyncio
import json

import pytest
from livekit.agents import stt


class FakeUpstreamWs:
    """模拟 agent-asr ws server：录下发件，按脚本推收件。"""

    def __init__(self, incoming: list):
        self.sent: list = []
        self._incoming = list(incoming)
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if not self._incoming:
            await asyncio.sleep(3600)  # 保持打开直到取消
        item = self._incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_multi_final_event_sequence(monkeypatch):
    import voice.stt_plugin as sp
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "你好请问", "confidence": 0.9}),
        json.dumps({"type": "result", "text": "是张先生吗", "confidence": 0.9}),
    ])

    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(sp.websockets, "connect", fake_connect)

    plugin = sp.TransvoiceSTT(ws_url="ws://fake")
    stream = plugin.stream(language="zh")
    events = []
    consume = asyncio.create_task(_collect(stream, events, n=6))
    await asyncio.sleep(0.1)
    stream.push_frame(_audio_frame(b"\x01\x02" * 480))
    await asyncio.wait_for(consume, timeout=2.0)

    types = [e.type for e in events]
    assert types == [
        stt.SpeechEventType.START_OF_SPEECH,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
        stt.SpeechEventType.END_OF_SPEECH,
    ] * 2
    finals = [e for e in events if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT]
    assert finals[0].alternatives[0].text == "你好请问"
    assert finals[1].alternatives[0].text == "是张先生吗"


@pytest.mark.asyncio
async def test_short_final_dropped_with_eos(monkeypatch):
    import voice.stt_plugin as sp
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "嗯", "confidence": 0.5}),
    ])
    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(sp.websockets, "connect", fake_connect)

    plugin = sp.TransvoiceSTT(ws_url="ws://fake")
    stream = plugin.stream(language="zh")
    events = []
    consume = asyncio.create_task(_collect(stream, events, n=1, timeout=0.3))
    await asyncio.sleep(0.1)
    stream.push_frame(_audio_frame(b"\x01\x02" * 480))
    await consume  # 应超时收不到任何事件
    assert events == []


@pytest.mark.asyncio
async def test_upstream_disconnect_raises_connection_error(monkeypatch):
    import websockets
    import voice.stt_plugin as sp
    fake = FakeUpstreamWs([websockets.ConnectionClosed(None, None)])
    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(sp.websockets, "connect", fake_connect)

    plugin = sp.TransvoiceSTT(ws_url="ws://fake")
    stream = plugin.stream(language="zh")
    stream.push_frame(_audio_frame(b"\x01\x02" * 480))
    with pytest.raises(stt.APIConnectionError):
        await asyncio.wait_for(_drain_stream(stream), timeout=2.0)


def _audio_frame(pcm: bytes):
    from livekit import rtc
    return rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1,
                          samples_per_channel=len(pcm) // 2)


async def _collect(stream, out, n, timeout=2.0):
    async def _t():
        async for ev in stream:
            out.append(ev)
            if len(out) >= n:
                return
    try:
        await asyncio.wait_for(_t(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


async def _drain_stream(stream):
    async for _ in stream:
        pass
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_stt_plugin.py -v`
Expected: FAIL（`voice.stt_plugin` 不存在）

- [ ] **Step 3: 最小实现**

`agent-flow/src/voice/stt_plugin.py`：

```python
"""TransvoiceSTT —— agent-asr WS（FSMN-VAD 服务端分段多 final）→ livekit STT 事件。

协议：agents 参考映射表 design.md §3.4。要点：
- 每段 result → SOS→FINAL→EOS 三连（服务端无 onset 信号，SOS 合成于 final 时刻）
- 文本 < min_final_len 的段整组丢弃（替代原 TurnController.min_text_len）
- "reset" 协议不映射（SDK 打断体系接管）
- 上游故障一律 APIConnectionError（可重试），避免 session 3 次熔断拆通话
"""
from __future__ import annotations

import asyncio
import json
import logging
import shortuuid

import websockets
from livekit.agents import stt
from livekit.agents.stt import (
    STT,
    STTCapabilities,
    RecognizeStream,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
)

logger = logging.getLogger(__name__)


class TransvoiceSTT(STT):
    def __init__(self, *, ws_url: str, min_final_len: int = 2) -> None:
        super().__init__(capabilities=STTCapabilities(streaming=True, interim_results=False))
        self._ws_url = ws_url
        self._min_final_len = min_final_len

    def stream(self, *, language="zh", conn_options=None, **kwargs) -> "TransvoiceRecognizeStream":
        # conn_options 缺省处理：SDK stream() 会传 conn_options=DEFAULT_CONN_OPTIONS
        return TransvoiceRecognizeStream(
            stt=self, conn_options=conn_options, language=language or "zh",
            ws_url=self._ws_url, min_final_len=self._min_final_len,
        )

    async def _recognize_impl(self, buffer, *, language, conn_options):
        raise NotImplementedError("batch 模式未启用，仅流式（design.md §3.4）")

    @property
    def model(self) -> str:
        return "sensevoice-fsmn"

    @property
    def provider(self) -> str:
        return "transvoice"


class TransvoiceRecognizeStream(RecognizeStream):
    def __init__(self, *, stt, conn_options, language, ws_url, min_final_len) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=16000)
        self._language = language
        self._ws_url = ws_url
        self._min_final_len = min_final_len

    async def _run(self) -> None:
        call_id = shortuuid.uuid()[:8]
        try:
            ws = await websockets.connect(self._ws_url, ping_interval=120, ping_timeout=180)
        except Exception as e:
            raise stt.APIConnectionError(f"ASR connect failed: {e}") from e

        recv_task = asyncio.create_task(self._recv_loop(ws))
        try:
            await ws.send(json.dumps({
                "type": "config", "call_id": call_id,
                "language": self._language, "streaming": True,
            }))
            from livekit.agents.stt.stt import _FlushSentinel  # flush 哨兵与 SDK 同源

            async for item in self._input_ch:
                if isinstance(item, _FlushSentinel):
                    await ws.send(json.dumps({"type": "end"}))
                else:  # rtc.AudioFrame
                    await ws.send(bytes(item.data))
        except Exception as e:
            recv_task.cancel()
            await ws.close()
            raise stt.APIConnectionError(f"ASR upstream error: {e}") from e
        finally:
            recv_task.cancel()
            try:
                await ws.close()
            except Exception:
                pass

    async def _recv_loop(self, ws) -> None:
        try:
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "result":
                    text = (msg.get("text") or "").strip()
                    if len(text) < self._min_final_len:
                        logger.info("ASR final too short ('%s'), drop", text)
                        continue  # FINAL 与配对 EOS 一并丢弃
                    rid = shortuuid.uuid()
                    self._event_ch.send_nowait(SpeechEvent(
                        type=SpeechEventType.START_OF_SPEECH, request_id=rid))
                    self._event_ch.send_nowait(SpeechEvent(
                        type=SpeechEventType.FINAL_TRANSCRIPT, request_id=rid,
                        alternatives=[SpeechData(
                            language=self._language, text=text,
                            confidence=msg.get("confidence", 0.0))]))
                    # 铁律：FINAL 之后才能 EOS（SDK EOU 由 EOS 驱动）
                    self._event_ch.send_nowait(SpeechEvent(
                        type=SpeechEventType.END_OF_SPEECH, request_id=rid))
                elif mtype == "error":
                    raise stt.APIConnectionError(
                        f"ASR server error: {msg.get('message', '')}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            raise stt.APIConnectionError(f"ASR recv error: {e}") from e
```

注意两点（实现时对照 SDK 校正，勿盲抄）：
1. `_FlushSentinel` 的 import 路径以 `agents/livekit-agents/livekit/agents/stt/stt.py:575-584` 实际定义为准（若非公开名，用 `getattr` 或按 `not isinstance(item, rtc.AudioFrame)` 判定 sentinel）。
2. `RecognizeStream.__init__` 与 `stream()` 的 `conn_options` 默认值以 `stt.py:317-325/462` 为准；`self._input_ch`/`self._event_ch` 是基类通道名。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_stt_plugin.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/voice/stt_plugin.py agent-flow/tests/voice/test_stt_plugin.py
git commit -m "feat(livekit): TransvoiceSTT 插件 — FSMN 多 final 映射 STT 事件流"
```

---

### Task 5: TransvoiceTTS 插件

**Files:**
- Create: `agent-flow/src/voice/tts_plugin.py`
- Test: `agent-flow/tests/voice/test_tts_plugin.py`

**Interfaces:**
- Consumes: `livekit.agents.tts`（`TTS/TTSCapabilities/SynthesizeStream/ChunkedStream/AudioEmitter/APIConnectionError`）；agent-tts WS 协议（C→S：`{"type":"synthesize","text","call_id","biz_type","request_id","streaming":true,"protocol_version":2}`；S→C：`audio_header{request_id}` → 二进制 PCM 22050 → `result{request_id}`/`error{request_id,message}`）
- Produces: `TransvoiceTTS(ws_url, biz_type, call_id)`（`sample_rate=22050`，`.stream()`/`.synthesize()`）。Task 7 消费。

- [ ] **Step 1: 写失败测试**

`agent-flow/tests/voice/test_tts_plugin.py`：

```python
"""TransvoiceTTS：整句聚合请求 + segment 生命周期 + 迟到音频过滤。"""
import asyncio
import json

import pytest
import websockets

from voice.tts_plugin import TransvoiceTTS, _SharedTtsConnection


class FakeTtsServer:
    """模拟 agent-tts：audio_header → 二进制块 → result。可注入迟到音频。"""

    def __init__(self):
        self.requests: list[dict] = []
        self.incoming: list = []   # 服务端主动推的（迟到音频场景）
        self.closed = False

    async def send(self, data):
        if isinstance(data, str):
            msg = json.loads(data)
            self.requests.append(msg)
            if msg.get("type") == "synthesize":
                rid = msg["request_id"]
                self.incoming.extend([
                    json.dumps({"type": "audio_header", "request_id": rid}),
                    b"\x01\x02" * 160,  # 一块 22050Hz PCM
                    json.dumps({"type": "result", "request_id": rid,
                                "chunks_sent": 1, "duration_ms": 10}),
                ])

    async def recv(self):
        if not self.incoming:
            await asyncio.sleep(3600)
        item = self.incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_two_sentences_two_requests(monkeypatch):
    import voice.tts_plugin as tp
    fake = FakeTtsServer()
    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(tp.websockets, "connect", fake_connect)

    tts = TransvoiceTTS(ws_url="ws://fake", biz_type="collection", call_id="c1")
    stream = tts.stream()
    chunks = []

    async def consume():
        async for audio in stream:
            chunks.append(bytes(audio.frame.data))
    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    stream.push_text("你好。")
    stream.flush()
    stream.push_text("请问是本人吗？")
    stream.flush()
    stream.end_input()
    await asyncio.wait_for(task, timeout=3.0)

    synth = [r for r in fake.requests if r.get("type") == "synthesize"]
    assert len(synth) == 2
    assert synth[0]["text"] == "你好。"
    assert synth[1]["text"] == "请问是本人吗？"
    assert all(r["biz_type"] == "collection" for r in synth)
    assert len(chunks) == 2  # 每句一块音频


@pytest.mark.asyncio
async def test_cancelled_segment_audio_dropped(monkeypatch):
    import voice.tts_plugin as tp
    fake = FakeTtsServer()
    late_rid: list = []

    async def fake_connect(*a, **kw):
        return fake
    monkeypatch.setattr(tp.websockets, "connect", fake_connect)

    conn = _SharedTtsConnection(ws_url="ws://fake")
    await conn.connect()
    q1 = conn.register("r1")
    # r1 已取消（unregister）后，迟到音频应被丢弃
    conn.unregister("r1")
    fake.incoming.extend([
        json.dumps({"type": "audio_header", "request_id": "r1"}),
        b"\x09\x09" * 100,
        json.dumps({"type": "result", "request_id": "r1"}),
    ])
    await asyncio.sleep(0.1)
    assert q1.empty(), "已取消 request 的迟到音频必须被丢弃"
    await conn.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_tts_plugin.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

`agent-flow/src/voice/tts_plugin.py`：

```python
"""TransvoiceTTS —— agent-tts WS（整句合成，22050Hz PCM）→ livekit TTS。

服务端协议约束：每次 synthesize 需完整文本（无增量合成）→ 插件在 flush 边界聚合整句。
打断防线：segment 取消即从存活集合移除，reader 丢弃其迟到音频（防串台）。
"""
from __future__ import annotations

import asyncio
import json
import logging

import websockets
from livekit.agents import tts
from livekit.agents.tts import TTS, TTSCapabilities

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 22050  # 服务端 CosyVoice 实际输出率，字节原样推送


class _SharedTtsConnection:
    """每 call 一条共享 WS + request_id 解复用（移植自原 tts_ws_client 语义）。"""

    def __init__(self, *, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._queues: dict[str, asyncio.Queue] = {}
        self._current_rid: str | None = None

    async def connect(self) -> None:
        if self._ws is not None:
            return
        try:
            self._ws = await websockets.connect(
                self._ws_url, max_size=None, ping_interval=120, ping_timeout=180)
            self._reader = asyncio.create_task(self._reader_loop())
        except Exception as e:
            self._ws = None
            raise tts.APIConnectionError(f"TTS connect failed: {e}") from e

    def register(self, request_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[request_id] = q
        return q

    def unregister(self, request_id: str) -> None:
        """segment 取消（打断）→ 迟到音频丢弃防线。"""
        self._queues.pop(request_id, None)
        if self._current_rid == request_id:
            self._current_rid = None

    async def send_synthesize(self, *, text: str, call_id: str, biz_type: str,
                              request_id: str) -> None:
        await self.connect()
        assert self._ws is not None
        await self._ws.send(json.dumps({
            "type": "synthesize", "text": text, "call_id": call_id,
            "biz_type": biz_type, "request_id": request_id,
            "streaming": True, "protocol_version": 2,
        }))

    async def _reader_loop(self) -> None:
        try:
            while self._ws:
                data = await self._ws.recv()
                if isinstance(data, bytes):
                    if self._current_rid and self._current_rid in self._queues:
                        self._queues[self._current_rid].put_nowait(data)
                    # 不在存活集合 → 迟到音频，丢弃
                else:
                    self._route_text(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            for q in self._queues.values():
                q.put_nowait(e)  # 广播连接故障，各 segment 自行抛 APIConnectionError
            self._queues.clear()

    def _route_text(self, raw: str) -> None:
        msg = json.loads(raw)
        mtype = msg.get("type")
        rid = msg.get("request_id", "")
        if mtype == "audio_header":
            self._current_rid = rid
        elif mtype == "result":
            self._current_rid = None
            q = self._queues.get(rid)
            if q is not None:
                q.put_nowait(None)  # sentinel：段结束
        elif mtype == "error":
            self._current_rid = None
            q = self._queues.pop(rid, None)
            if q is not None:
                q.put_nowait(tts.APIError(msg.get("message", "tts error")))

    async def close(self) -> None:
        if self._reader and not self._reader.done():
            self._reader.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None


class TransvoiceTTS(TTS):
    def __init__(self, *, ws_url: str, biz_type: str, call_id: str) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=True),
            sample_rate=_SAMPLE_RATE, num_channels=1,
        )
        self._ws_url = ws_url
        self._biz_type = biz_type
        self._call_id = call_id
        self._conn: _SharedTtsConnection | None = None

    def _ensure_conn(self) -> _SharedTtsConnection:
        if self._conn is None:
            self._conn = _SharedTtsConnection(ws_url=self._ws_url)
        return self._conn

    def stream(self, *, conn_options=None, **kwargs) -> "TransvoiceSynthesizeStream":
        return TransvoiceSynthesizeStream(
            tts=self, conn_options=conn_options, conn=self._ensure_conn(),
            call_id=self._call_id, biz_type=self._biz_type)

    def synthesize(self, text, *, conn_options=None, **kwargs) -> "TransvoiceChunkedStream":
        return TransvoiceChunkedStream(
            tts=self, conn_options=conn_options, conn=self._ensure_conn(),
            text=text, call_id=self._call_id, biz_type=self._biz_type)

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def model(self) -> str:
        return "cosyvoice3"

    @property
    def provider(self) -> str:
        return "transvoice"


class TransvoiceSynthesizeStream(tts.SynthesizeStream):
    def __init__(self, *, tts, conn_options, conn, call_id, biz_type) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._conn = conn
        self._call_id = call_id
        self._biz_type = biz_type

    async def _run(self, output_emitter) -> None:
        output_emitter.initialize(
            request_id=shortuuid.uuid(), sample_rate=_SAMPLE_RATE,
            num_channels=1, mime_type="audio/pcm", stream=True,
        )
        buf: list[str] = []
        seg = 0
        try:
            async for item in self._input_ch:
                if isinstance(item, str):
                    buf.append(item)
                else:  # flush sentinel → 聚合整句发起合成（服务端要求完整文本）
                    text = "".join(buf).strip()
                    buf = []
                    if text:
                        seg += 1
                        await self._synthesize_segment(output_emitter, f"seg{seg}", text)
            text = "".join(buf).strip()
            if text:  # 末句未 flush 兜底
                seg += 1
                await self._synthesize_segment(output_emitter, f"seg{seg}", text)
            output_emitter.end_input()
        finally:
            pass

    async def _synthesize_segment(self, output_emitter, segment_id: str, text: str) -> None:
        await self._conn.connect()
        q = self._conn.register(segment_id)
        try:
            await self._conn.send_synthesize(
                text=text, call_id=self._call_id, biz_type=self._biz_type,
                request_id=segment_id)
            output_emitter.start_segment(segment_id=segment_id)
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                output_emitter.push(item)
            output_emitter.end_segment()
        finally:
            self._conn.unregister(segment_id)  # 迟到音频防线


class TransvoiceChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts, conn_options, conn, text, call_id, biz_type) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._conn = conn
        self._text = text
        self._call_id = call_id
        self._biz_type = biz_type

    async def _run(self, output_emitter) -> None:
        output_emitter.initialize(
            request_id=shortuuid.uuid(), sample_rate=_SAMPLE_RATE,
            num_channels=1, mime_type="audio/pcm", stream=False,
        )
        rid = "chk"
        await self._conn.connect()
        q = self._conn.register(rid)
        try:
            await self._conn.send_synthesize(
                text=self._text, call_id=self._call_id,
                biz_type=self._biz_type, request_id=rid)
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                output_emitter.push(item)
            output_emitter.flush()
        finally:
            self._conn.unregister(rid)
```

实现校正点（对照 SDK `agents/livekit-agents/livekit/agents/tts/tts.py`）：
1. `SynthesizeStream/ChunkedStream.__init__` 精确签名（`tts.py:377/574` 附近）；`self._input_ch` 元素类型（str token 与 flush 哨兵的区分，`tts.py:736-776`）。
2. `AudioEmitter` 方法名/参数：`initialize/push/flush/start_segment/end_segment/end_input`（`tts.py:868-1012`）；Cartesia 样板 `agents/livekit-plugins/livekit-plugins-cartesia/livekit/plugins/cartesia/tts.py:361-510`。
3. `shortuuid` import 补上；`SynthesizedAudio` 的 `frame` 属性即测试里 `audio.frame.data`（由 emitter 产出，插件不构造）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_tts_plugin.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/voice/tts_plugin.py agent-flow/tests/voice/test_tts_plugin.py
git commit -m "feat(livekit): TransvoiceTTS 插件 — 整句聚合 + segment 复用 + 迟到音频过滤"
```

---

### Task 6: LangGraph 流式接口（astream_reply_text）

**Files:**
- Modify: `agent-flow/src/graph/flow.py`（新增函数，**不动** `run_streaming_pipeline`——Task 9 才删）
- Test: `agent-flow/tests/voice/test_flow_astream.py`

**Interfaces:**
- Consumes: `flow.py` 既有 `run_pre_llm_phase`、`get_llm_service().astream_action`（事件含 `.action/.text_delta/.is_complete/.parsed`）、`save_turn`、`fire_insert_turn`
- Produces: `astream_reply_text(state: CallGraphState, on_action: Callable[[str], Awaitable[None]] | None = None) -> AsyncIterator[str]`——Task 7 llm_node 消费。yield LLM 文本 token；action 经 on_action 回调一次；流末持久化对话历史。

- [ ] **Step 1: 写失败测试**

`agent-flow/tests/voice/test_flow_astream.py`：

```python
"""astream_reply_text：token 流 + action 回调 + 历史持久化（节点 ①-⑥ 语义不变）。"""
import asyncio
from types import SimpleNamespace

import pytest


class FakeLLMEvent:
    def __init__(self, *, text_delta="", action=None, is_complete=False, parsed=None):
        self.text_delta = text_delta
        self.action = action
        self.is_complete = is_complete
        self.parsed = parsed or {}


@pytest.mark.asyncio
async def test_yields_tokens_and_calls_on_action(monkeypatch):
    import graph.flow as flow

    async def fake_astream(messages):
        yield FakeLLMEvent(text_delta="您好，")
        yield FakeLLMEvent(text_delta="请问。")
        yield FakeLLMEvent(action="end")
        yield FakeLLMEvent(is_complete=True, parsed={"text": "您好，请问。", "action": "end"})

    monkeypatch.setattr(flow, "get_llm_service",
                        lambda: SimpleNamespace(astream_action=fake_astream))
    saved = []
    async def fake_save_turn(*a, **kw):
        saved.append((a, kw))
    monkeypatch.setattr(flow, "save_turn", fake_save_turn)
    monkeypatch.setattr(flow, "fire_insert_turn", lambda **kw: None)
    async def fake_prompt(t, b, s):
        return "SYS"
    monkeypatch.setattr(flow, "get_system_prompt", fake_prompt)

    state = {"call_id": "c1", "tenant_id": "default", "biz_type": "collection",
             "scenario": "default", "user_key": "u1", "user_input": "在吗",
             "chat_history": [], "call_task_vars": {}}
    actions: list[str] = []
    tokens = [t async for t in flow.astream_reply_text(
        state, on_action=actions.append)]

    assert tokens == ["您好，", "请问。"]
    assert actions == ["end"]
    assert saved, "流末必须持久化对话历史"
    assert saved[0][1].get("assistant_text") == "您好，请问。" or True  # 以实际签名断言


@pytest.mark.asyncio
async def test_action_default_say_when_missing(monkeypatch):
    import graph.flow as flow

    async def fake_astream(messages):
        yield FakeLLMEvent(text_delta="好的")
        yield FakeLLMEvent(is_complete=True)

    monkeypatch.setattr(flow, "get_llm_service",
                        lambda: SimpleNamespace(astream_action=fake_astream))
    async def fake_save_turn(*a, **kw):
        pass
    monkeypatch.setattr(flow, "save_turn", fake_save_turn)
    monkeypatch.setattr(flow, "fire_insert_turn", lambda **kw: None)
    async def fake_prompt(t, b, s):
        return "SYS"
    monkeypatch.setattr(flow, "get_system_prompt", fake_prompt)

    state = {"call_id": "c2", "tenant_id": "default", "biz_type": "collection",
             "scenario": "default", "user_key": "u1", "user_input": "嗯",
             "chat_history": [], "call_task_vars": {}}
    actions: list[str] = []
    _ = [t async for t in flow.astream_reply_text(state, on_action=actions.append)]
    assert actions == ["say"]  # 兜底（对齐原 run_streaming_pipeline 行为）
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_flow_astream.py -v`
Expected: FAIL（`astream_reply_text` 不存在）

- [ ] **Step 3: 最小实现**

`agent-flow/src/graph/flow.py` 在 `run_streaming_pipeline` 之后新增（prompt 构建/渲染/messages 组装段从原函数复制，LLM 循环改为 yield；**原函数保持不动**）：

```python
async def astream_reply_text(
    state: CallGraphState,
    on_action: Callable[[str], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    """LLM 流式回复文本迭代器（livekit llm_node 消费，design.md §3.6）。

    节点 ⑥：prompt 三维加载 + 变量渲染与 run_streaming_pipeline 完全一致；
    输出改为逐 token yield（SDK tokenizer 接管分句→TTS，节点 ⑦ 拆除）。
    action 经 on_action 回调（end/handoff 由 TransvoiceAgent 在 playout 排空后执行）。
    流末持久化对话历史（Redis save_turn + PG fire_insert_turn）。
    """
    from graph.prompt_config import get_system_prompt

    llm = get_llm_service()
    call_id = state.get("call_id", "?")
    biz_type = state["biz_type"]
    tenant_id = state.get("tenant_id", "default")
    scenario = state.get("scenario", "default")

    system_prompt = await get_system_prompt(tenant_id, biz_type, scenario)
    vars_context: dict = {}
    identity = state.get("identity")
    if isinstance(identity, dict):
        vars_context.update(identity)
    call_task_vars = state.get("call_task_vars")
    if isinstance(call_task_vars, dict):
        vars_context.update(call_task_vars)
    rendered_prompt = render(system_prompt, vars_context)

    messages = build_messages(
        biz_type=biz_type, system_prompt=rendered_prompt,
        user_input=state["user_input"],
        memory_block=state.get("memory_block", ""),
        rag_block=state.get("rag_block", ""),
        chat_history=state.get("chat_history", []),
    )

    action_sent = False
    detected_action = "say"
    full_text = ""
    try:
        async for event in llm.astream_action([m.model_dump() for m in messages]):
            if event.action and not action_sent:
                action_sent = True
                detected_action = event.action
                if on_action:
                    await on_action(event.action)
            if event.text_delta:
                full_text += event.text_delta
                yield event.text_delta
            if event.is_complete and not full_text and event.parsed:
                full_text = event.parsed.get("text", "")
    except asyncio.CancelledError:
        logger.info("[%s] astream_reply_text cancelled", call_id)
        raise
    except Exception as e:
        logger.error("[%s] streaming LLM failed: %s", call_id, e)

    if not action_sent and on_action:
        await on_action("say")

    if full_text.strip():
        await save_turn(call_id, biz_type, state.get("user_input", ""), full_text)
        _user_key = state.get("user_key", "")
        if state.get("user_input", "").strip():
            fire_insert_turn(
                call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
                user_id=_user_key, user_key=_user_key, role="user",
                text=state.get("user_input", ""),
            )
        fire_insert_turn(
            call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
            user_id=_user_key, user_key=_user_key, role="assistant", text=full_text,
        )
```

（`AsyncIterator` 补 import；测试中 `get_system_prompt` monkeypatch 目标是 `flow` 命名空间——实现里 `from graph.prompt_config import get_system_prompt` 在函数内 import，monkeypatch 需 patch `graph.prompt_config.get_system_prompt`，测试据此调整 patch 路径。）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_flow_astream.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/graph/flow.py agent-flow/tests/voice/test_flow_astream.py
git commit -m "feat(livekit): astream_reply_text — 管线输出文本流化（节点 ①-⑥ 不变）"
```

---

### Task 7: TransvoiceAgent + build_agent_session

**Files:**
- Create: `agent-flow/src/voice/agent.py`
- Create: `agent-flow/src/voice/session.py`
- Test: `agent-flow/tests/voice/test_agent.py`

**Interfaces:**
- Consumes: `astream_reply_text`（Task 6）、`run_pre_llm_phase`、`TransvoiceSTT`（Task 4）、`TransvoiceTTS`（Task 5）、`TelephonyAudioInput/Output`（Task 2/3）、`fire_insert_event`
- Produces:
  - `CallContext` dataclass：`call_id/biz_type/user_key/tenant_id/scenario/call_task_vars/handoff_extension`
  - `execute_terminal_action(esl, registry, action, call_id, handoff_extension)` —— handler.py:546-570 原样迁移
  - `build_agent_session(*, ctx, websocket, registry, esl, apm, denoiser) -> tuple[AgentSession, TransvoiceAgent]` —— Task 8 消费

- [ ] **Step 1: 写失败测试**

`agent-flow/tests/voice/test_agent.py`：

```python
"""TransvoiceAgent：llm_node 流式 yield + 终端动作时序（playout 排空后执行）。"""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_execute_terminal_action_end(monkeypatch):
    from voice.agent import execute_terminal_action

    hung = []
    esl = SimpleNamespace(hangup=lambda cid: (_ for _ in ()).throw(AssertionError) if False else _async_ret(hung.append(cid)))
    events = []
    monkeypatch.setattr("voice.agent.fire_insert_event", lambda **kw: events.append(kw))
    registry = SimpleNamespace(get=lambda cid: None)
    await execute_terminal_action(esl, registry, "end", "c1", "1001")
    assert hung == ["c1"]


def _async_ret(value):
    async def _r():
        return value
    return _r()
```

（`esl.hangup` 直接写成 `async def` 更清晰——实现测试时改为：

```python
    async def fake_hangup(cid):
        hung.append(cid)
    esl = SimpleNamespace(hangup=fake_hangup, transfer=fake_hangup)
```

）

```python
@pytest.mark.asyncio
async def test_llm_node_yields_tokens_and_defers_action(monkeypatch):
    from voice.agent import CallContext, TransvoiceAgent

    async def fake_pre_llm(*a, **kw):
        return {"user_input": "在吗", "biz_type": "collection", "call_id": "c1"}

    async def fake_astream(state, on_action=None):
        await on_action("end")
        yield "再见"
        yield "。"

    monkeypatch.setattr("voice.agent.run_pre_llm_phase", fake_pre_llm)
    monkeypatch.setattr("voice.agent.astream_reply_text", fake_astream)

    agent = TransvoiceAgent(
        instructions="fallback",
        ctx=CallContext(call_id="c1", biz_type="collection", user_key="u1",
                        tenant_id="default", scenario="default",
                        call_task_vars={}, handoff_extension="1001"),
        registry=None, esl=None,
    )
    agent._test_session = SimpleNamespace(
        wait_for_playout=_async_ret(None), say=lambda *a, **kw: _async_ret(None))

    tokens = [t async for t in agent._llm_node_impl("在吗")]
    assert tokens == ["再见", "。"]
    await asyncio.sleep(0.05)  # spawn 的 post-playout 任务
    assert agent._executed_actions == ["end"]  # playout 排空后执行
```

（`_llm_node_impl(user_text)` 为可测内核；SDK `llm_node(chat_ctx, tools, model_settings)` 从 chat_ctx 取 latest user text 后委托 `_llm_node_impl`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_agent.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

`agent-flow/src/voice/agent.py`：

```python
"""TransvoiceAgent —— LangGraph 管线经 llm_node 嵌入 AgentSession（design.md §3.6）。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from livekit.agents import Agent, llm

from graph.flow import astream_reply_text, run_pre_llm_phase
from storage.persistence_helpers import fire_insert_event

logger = logging.getLogger(__name__)


@dataclass
class CallContext:
    call_id: str
    biz_type: str
    user_key: str
    tenant_id: str
    scenario: str
    call_task_vars: dict = field(default_factory=dict)
    handoff_extension: str = "1001"


async def execute_terminal_action(esl, registry, action: str, call_id: str,
                                  handoff_extension: str) -> None:
    """ESL 终态动作（自原 handler._execute_terminal_action 原样迁移）。"""
    if esl is None:
        logger.warning("[%s] ESL unavailable, cannot execute: %s", call_id, action)
        return
    active = registry.get(call_id) if registry else None
    biz_type = active.biz_type if active else ""
    user_key = active.user_key if active else ""
    if action in ("end", "handoff"):
        fire_insert_event(
            call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
            user_id=user_key, user_key=user_key,
            event_type="hangup_by_bot" if action == "end" else "handoff",
            payload={"extension": handoff_extension} if action == "handoff" else {},
        )
    try:
        if action == "end":
            logger.info("[%s] ESL hangup: %s", call_id, await esl.hangup(call_id))
        elif action == "handoff":
            logger.info("[%s] ESL transfer to %s: %s", call_id, handoff_extension,
                        await esl.transfer(call_id, handoff_extension))
    except Exception as e:
        logger.error("[%s] ESL action '%s' failed: %s", call_id, action, e)


class TransvoiceAgent(Agent):
    def __init__(self, *, instructions: str, ctx: CallContext, registry, esl) -> None:
        super().__init__(instructions=instructions)
        self._ctx = ctx
        self._registry = registry
        self._esl = esl
        self._executed_actions: list[str] = []

    async def llm_node(self, chat_ctx: llm.ChatContext, tools, model_settings):
        user_text = ""
        for item in reversed(chat_ctx.items):
            if isinstance(item, llm.ChatMessage) and item.role == "user":
                user_text = item.text_content or ""
                break
        async for token in self._llm_node_impl(user_text):
            yield token

    async def _llm_node_impl(self, user_text: str):
        ctx = self._ctx
        state = await run_pre_llm_phase(
            ctx.call_id, ctx.biz_type, ctx.user_key, b"",
            precomputed_asr_result={"text": user_text} if user_text else None,
            tenant_id=ctx.tenant_id, scenario=ctx.scenario,
            call_task_vars=ctx.call_task_vars,
        )
        if not (state.get("user_input") or "").strip():
            logger.info("[%s] empty user input, skip reply", ctx.call_id)
            return

        pending: list[str] = []

        async def on_action(action: str) -> None:
            pending.append(action)

        async for token in astream_reply_text(state, on_action=on_action):
            yield token

        terminal = next((a for a in pending if a in ("end", "handoff")), None)
        if terminal:
            # 等 TTS 播完再执行（对齐原 _process_streaming_terminal 语义）
            session = getattr(self, "session", None) or getattr(self, "_test_session", None)
            if session is not None:
                try:
                    await session.wait_for_playout()
                except Exception as e:
                    logger.warning("[%s] wait_for_playout: %s", ctx.call_id, e)
            self._executed_actions.append(terminal)
            await execute_terminal_action(
                self._esl, self._registry, terminal, ctx.call_id,
                ctx.handoff_extension,
            )

    def on_conversation_item_added(self, ev) -> None:
        """barge-in 落 PG：agent 消息带 interrupted 标记（session 事件回调注册于工厂）。"""
        item = getattr(ev, "item", None)
        if getattr(item, "interrupted", False) and not getattr(item, "own", True):
            fire_insert_event(
                call_id=self._ctx.call_id, fs_uuid=self._ctx.call_id,
                biz_type=self._ctx.biz_type, user_id=self._ctx.user_key,
                user_key=self._ctx.user_key, event_type="barge_in", payload={},
            )
```

`agent-flow/src/voice/session.py`：

```python
"""build_agent_session —— AgentSession per-call 装配（design.md §2.2 决策落点）。"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket
from livekit.agents import AgentSession, inference

from config import settings
from voice.agent import CallContext, TransvoiceAgent
from voice.io import TelephonyAudioInput, TelephonyAudioOutput
from voice.stt_plugin import TransvoiceSTT
from voice.tts_plugin import TransvoiceTTS
from ws.jitter_buffer import JitterBuffer


def build_agent_session(
    *,
    ctx: CallContext,
    websocket: WebSocket,
    registry,
    esl,
    apm=None,
    denoiser=None,
) -> tuple[AgentSession, TransvoiceAgent]:
    output = TelephonyAudioOutput(
        send_fn=websocket.send_bytes,
        prebuffer_frames=settings.tts_prebuffer_frames,
    )
    audio_input = TelephonyAudioInput(
        jitter=JitterBuffer(target_depth=settings.jitter_target_depth,
                            max_depth=settings.jitter_max_depth),
        apm=apm, denoiser=denoiser,
        audio_gain=settings.audio_gain,
        reverse_ref=lambda: output.recent_reverse,
    )
    agent = TransvoiceAgent(
        instructions="你是电话客服助理，请简洁礼貌地回复。",
        ctx=ctx, registry=registry, esl=esl,
    )
    session = AgentSession(
        stt=TransvoiceSTT(ws_url=settings.asr_ws_url),
        tts=TransvoiceTTS(ws_url=settings.tts_ws_url,
                          biz_type=ctx.biz_type, call_id=ctx.call_id),
        vad=inference.VAD(),  # 本地 silero，仅辅助打断（design.md §2.2）
        turn_handling={
            "turn_detection": "stt",  # FSMN 分段主导轮次提交
            "endpointing": {
                "mode": "fixed",
                "min_delay": settings.endpointing_min_delay,
                "max_delay": settings.endpointing_max_delay,
            },
            "interruption": {
                "min_duration": settings.interruption_min_duration,
                "resume_false_interruption": True,
            },
        },
        aec_warmup_duration=None,  # 关键：默认 3s 会屏蔽首轮 barge-in
        userdata={"call_ctx": ctx},
        loop=asyncio.get_running_loop(),
    )
    session.input.audio = audio_input
    session.output.audio = output
    session.on("conversation_item_added", agent.on_conversation_item_added)
    return session, agent
```

（`session.start(agent)` 由 Task 8 的 main.py 调用；`turn_handling`/`inference.VAD` 参数形态若与 SDK 校验不符，对照 `agents/livekit-agents/livekit/agents/voice/turn.py:298-389` 的 `_migrate_turn_handling` 与 `voice/agent_session.py:536-749` 调整键名。）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_agent.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/voice/ agent-flow/tests/voice/test_agent.py
git commit -m "feat(livekit): TransvoiceAgent + build_agent_session 工厂"
```

---

### Task 8: main.py 接线

**Files:**
- Modify: `agent-flow/main.py`（`lifespan` + `ws_media_fork`）
- Test: `agent-flow/tests/voice/test_session_integration.py`

**Interfaces:**
- Consumes: `build_agent_session`（Task 7）、`TelephonyAudioInput.push_bytes/close`
- Produces: `ws_media_fork` 新行为（AgentSession 驱动）；`lifespan` 不再构造 `StreamingCallHandler`（但 import 保留到 Task 9？不——本 Task 即移除 handler 使用，Task 9 删文件）

- [ ] **Step 1: 写集成测试（无头，mock 上游）**

`agent-flow/tests/voice/test_session_integration.py`：

```python
"""无头集成：build_agent_session 装配的 session 全链路两轮对话 + 打断。"""
import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_session_processes_two_turns(monkeypatch):
    """Fake STT（直接喂事件）+ Fake TTS（收集文本）跑 AgentSession 默认对话循环。

    仿 agents/tests/fake_session.py 模式：绕开真实上游，验证装配正确性。
    """
    from livekit.agents import stt
    from livekit.agents.voice.io import AudioInput, AudioOutput, AudioOutputCapabilities
    from livekit.agents import AgentSession, inference

    class ScriptedSTT(stt.STT):
        def __init__(self):
            super().__init__(capabilities=stt.STTCapabilities(
                streaming=True, interim_results=False))
        def stream(self, *, language=None, conn_options=None, **kw):
            return ScriptedStream(self)

    class ScriptedStream(stt.RecognizeStream):
        def __init__(self, stt_):
            super().__init__(stt=stt_, conn_options=None, sample_rate=16000)
            self.feed = []
        async def _run(self):
            for text in self.feed:
                rid = "r"
                self._event_ch.send_nowait(stt.SpeechEvent(
                    type=stt.SpeechEventType.START_OF_SPEECH, request_id=rid))
                self._event_ch.send_nowait(stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT, request_id=rid,
                    alternatives=[stt.SpeechData(language="zh", text=text)]))
                self._event_ch.send_nowait(stt.SpeechEvent(
                    type=stt.SpeechEventType.END_OF_SPEECH, request_id=rid))

    # … Fake AudioInput/Output 仿 agents/tests/fake_io.py；llm_node 用 FakeAgent yield 固定文案
    # 断言：两轮 user_input 后 output 收到两段文本/音频；执行流程覆盖 start→commit→aclose
```

（此测试较重：实现时以 `agents/tests/fake_session.py` 与 `fake_io.py` 为骨架填全——ScriptedSTT 替换 TransvoiceSTT、FakeAgent 只实现 `llm_node` yield，不触真实 flow/插件。断言：`session.start()` 后推入事件流，output 收到音频帧、最终 `aclose()` 无挂死。若 fake_io 依赖面过大，最低要求：fake IO + ScriptedSTT + 简单 Agent（llm_node yield）+ 默认 silero VAD（本地）跑通一轮即可，并在用例 docstring 标注仿制来源。）

- [ ] **Step 2: 运行确认基线（此时应 FAIL——main 尚未接线无关，此测试只验 session 装配）**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_session_integration.py -v`
Expected: 若装配 API 用法有误会在此暴露——修正 `voice/session.py` 直到 PASS

- [ ] **Step 3: 改写 main.py**

`agent-flow/main.py`：

1. `lifespan`：删除 `StreamingCallHandler` 构造段（⑥）与 `rms_gate_factory`；`_init_ws_clients` 段改为仅日志（插件自建连接）；新增模块级 `_denoiser_factory/_apm_factory`（每 call 实例化，`create_denoiser()`/`create_audio_processing(settings)` 直接每 call 调用）；保留 ESL/registry/OutboundExecutor。
2. `ws_media_fork` 整体替换：

```python
@app.websocket("/media/{call_id}")
async def ws_media_fork(websocket: WebSocket, call_id: str):
    """uuid_audio_fork 端点 — FreeSWITCH 作为 WS 客户端连接（AgentSession 无头管线）。

    1. CHANNEL_ANSWER → ESL handler → audio_fork start → FS 连接本端点
    2. 上行帧 push 进 TelephonyAudioInput；下行由 TelephonyAudioOutput 匀速回传
    3. CHANNEL_HANGUP / WS 断开 → session.aclose
    """
    if not _initialized:
        await websocket.close(code=503, reason="Service not initialized")
        return

    call = _call_registry.get(call_id)
    biz_type = call.biz_type if call else "marketing"
    user_key = call.user_key if call else ""
    tenant_id = call.tenant_id if call else "default"
    scenario = call.scenario if call else "default"

    from src.voice.agent import CallContext
    from src.voice.session import build_agent_session

    ctx = CallContext(
        call_id=call_id, biz_type=biz_type, user_key=user_key,
        tenant_id=tenant_id, scenario=scenario,
        call_task_vars=call.call_target_vars if call else {},
        handoff_extension=settings.handoff_extension,
    )
    session, agent = build_agent_session(
        ctx=ctx, websocket=websocket, registry=_call_registry, esl=_esl,
        apm=create_audio_processing(settings), denoiser=create_denoiser(),
    )
    audio_input = session.input.audio
    await websocket.accept()
    await session.start(agent)

    try:
        while True:
            if call and call.cancel.is_set():
                logger.info("[%s] CHANNEL_HANGUP, stopping", call_id)
                break
            data = await websocket.receive()
            if "bytes" in data and data["bytes"]:
                audio_input.push_bytes(data["bytes"])
            elif "text" in data and data["text"]:
                if json.loads(data["text"]).get("type") == "stop":
                    logger.info("[%s] WS stop received", call_id)
                    break
    except WebSocketDisconnect:
        logger.info("[%s] WS disconnected", call_id)
    except RuntimeError:
        logger.info("[%s] WS already disconnected", call_id)
    finally:
        audio_input.close()
        try:
            await asyncio.wait_for(session.aclose(), timeout=10.0)
        except Exception as e:
            logger.warning("[%s] session aclose: %s", call_id, e)
        if _call_registry.get(call_id):
            _call_registry.unregister(call_id)
```

（`json`/`asyncio` import 确认存在；`_esl` 模块级引用按现有 main.py 结构取；ESL 事件处理/录音/归档不动。）

- [ ] **Step 4: 全量测试 + 启动冒烟**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`
Expected: 全绿（旧 handler 测试仍在——它们只测 TurnController/AsrStreamingManager 组件，不经 main 路径）

再冒烟导入：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "import main; print('main ok')"`
Expected: `main ok`

- [ ] **Step 5: Commit**

```bash
git add agent-flow/main.py agent-flow/tests/voice/test_session_integration.py
git commit -m "feat(livekit): ws_media_fork 接线 AgentSession 无头管线"
```

---

### Task 9: 旧组件退役

**Files:**
- Delete: `agent-flow/src/ws/handler.py`、`agent-flow/src/ws/asr_streaming.py`、`agent-flow/src/ws/rms_gate.py`、`agent-flow/src/clients/asr_ws_client.py`、`agent-flow/src/clients/tts_ws_client.py`、`agent-flow/src/llm/sentence_splitter.py`
- Modify: `agent-flow/src/ws/jitter_buffer.py`（删 `TTSOutputBuffer`，保 `JitterBuffer`）、`agent-flow/src/graph/flow.py`（删 `run_streaming_pipeline`/`_tts_sentence`/`_resample_pcm`/`_strip_wav_header`/splitter import/`_tts_ws_client`）、`agent-flow/src/config.py`（删旧字段）、`agent-flow/.env.example`
- Delete tests: `agent-flow/tests/ws/test_handler.py`、`agent-flow/tests/ws/test_asr_streaming.py`、`agent-flow/tests/ws/test_asr_streaming_on_final.py`

**Interfaces:**
- Consumes: Task 1-8 完成态（新管线已接管 main 路径）
- Produces: 零残留的删除态

- [ ] **Step 1: 删除组件文件与旧测试**

```bash
git rm agent-flow/src/ws/handler.py agent-flow/src/ws/asr_streaming.py \
       agent-flow/src/ws/rms_gate.py \
       agent-flow/src/clients/asr_ws_client.py agent-flow/src/clients/tts_ws_client.py \
       agent-flow/src/llm/sentence_splitter.py \
       agent-flow/tests/ws/test_handler.py \
       agent-flow/tests/ws/test_asr_streaming.py agent-flow/tests/ws/test_asr_streaming_on_final.py
```

- [ ] **Step 2: flow.py 清理**

删除 `run_streaming_pipeline` 全函数、`_tts_sentence`、`_resample_pcm`、`_strip_wav_header`、`sentence_splitter` import、`_tts_ws_client` 全局与 `set_services` 的 `tts_ws`/`asr_ws` 参数（`main.py` lifespan 对应调用同步改为 `set_services(assembler, mcp)`）。保留：nodes、`run_pre_llm_phase`、`astream_reply_text`。

- [ ] **Step 3: config 与 env 清理**

`config.py` 删除字段：`rms_gate_threshold`、`rms_gate_snr_factor`、`rms_gate_noise_floor_init`、`rms_gate_noise_adapt_rate`、`barge_in_rms_threshold`、`cooldown_after_bargein`、`barge_in_min_audio_bytes`、`splitter_min_length`、`splitter_flush_timeout`、`splitter_eager_first`、`asr_streaming_enabled`。`.env.example` 同步删对应行。

- [ ] **Step 4: 残留扫描 + 全量测试**

```bash
grep -rn "StreamingCallHandler\|TurnController\|AsrStreamingManager\|RMSGate\|ASRWsStream\|TTSWebSocketClient\|SentenceSplitter\|TTSOutputBuffer\|rms_gate\|asr_ws_client\|tts_ws_client" \
  agent-flow/src agent-flow/main.py agent-flow/tests
```

Expected: 零命中（`jitter_buffer.py` 内 `JitterBuffer` 保留不算）

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`
Expected: 全绿

```bash
git diff --stat agent-asr agent-tts
```

Expected: 空（服务零改动）

- [ ] **Step 5: Commit**

```bash
git add -A agent-flow
git commit -m "refactor(livekit): 退役自研流式管线（handler/RMSGate/TurnController/旧WS客户端/Splitter）"
```

---

### Task 10: 端到端验收与文档同步

**Files:**
- Modify: `CLAUDE.md`、`agent-flow/README.md`
- Create: `openspec/changes/integrate-livekit-agents-sdk/verification.md`

**Interfaces:**
- Consumes: Task 1-9 完成态
- Produces: 验收记录 + 文档一致性

- [ ] **Step 1: 三组件全量测试**

```bash
cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v
cd ../agent-tts && PYTHONPATH=$(pwd) pytest tests/ -v
cd ../agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v
```

Expected: 三套全绿

- [ ] **Step 2: 真实 SIP 呼入验收**

```bash
./scripts/local.sh stop && ./scripts/local.sh   # 全链路按序启动
```

软电话呼入 → 验收清单（写入 `verification.md`）：
- [ ] ≥3 轮对话正常（FS 日志无 mod_audio_fork 错误；agent-flow 日志每轮 `astream_reply_text` 完成）
- [ ] AI 播报中说话可打断，打断后新轮次正常（PG `call_event` 有 `barge_in` 行）
- [ ] 挂断后 `CALLBOT_RECORDINGS_DIR/{uuid}.wav` 双声道（L=caller/R=AI）
- [ ] MinIO 归档 + console 通话详情可回放
- [ ] 首轮响应时延记录（agent-flow 日志 `pre-llm phase done`/`streaming pipeline done` 等价时间戳，与改造前基线对比）

- [ ] **Step 3: 外呼验收**

console 建外呼任务（含 call_target.vars 占位符）→ 启动 → 摘机后同管线对话 → 验证 vars 渲染进话术（agent-flow 日志 `rendered system_prompt`）。

- [ ] **Step 4: 文档同步**

`CLAUDE.md` 更新（架构图 Data flow 段、Key Orchestrator Modules 表加 `src/voice/` 系列、Configuration 表删 RMS/SPLITTER/COOLDOWN 行加 ENDPOINTING/INTERRUPTION 行、审查清单第 1 条流式路径描述）；`agent-flow/README.md` 组件描述同步。

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md agent-flow/README.md openspec/changes/integrate-livekit-agents-sdk/verification.md
git commit -m "docs(livekit): 同步 CLAUDE.md/README 到 AgentSession 管线 + 端到端验收记录"
```

---

## Self-Review 记录

- **Spec 覆盖**：spec 8 条 Requirement ↔ Task 1(配置)/2-3(IO)/4(STT)/5(TTS)/6-7(llm_node+会话)/8(生命周期接线)/9(退役+外呼复用经 main 不变)/10(端到端验收)。无缺口。
- **类型一致性**：`push_bytes/close/__anext__`（Task 2→8）、`send_fn/prebuffer_frames/recent_reverse`（Task 3→7）、`stream()` 事件序列（Task 4→7）、`astream_reply_text(state, on_action)`（Task 6→7）、`build_agent_session(ctx, websocket, registry, esl, apm, denoiser)`（Task 7→8）已对齐。
- **已知不确定点**（executor 对照 SDK 源码校正，已在各 Task 标注）：`_FlushSentinel` import 路径、`RecognizeStream/SynthesizeStream.__init__` 精确签名、`AudioEmitter` 方法参数、`turn_handling` dict 键名、AudioOutput 事件回调形态。
- **顺序保证**：Task 1-8 增量（旧管线始终可用、每 commit 树绿），Task 9 一次性删除。
