# WebRTC AEC + NS + AGC 接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 `python-webrtc-audio-processing` 的 `AudioProcessingModule` 在 agent-flow 的 near 端音频路径上一次性完成 HPF + AEC + NS + AGC，替换现有 `denoise.py` 降噪链与固定 `AUDIO_GAIN`，从根上消除 barge-in 回声误触发。

**Architecture:** `TTSOutputBuffer` 在发往 FreeSWITCH 时镜像「最近播放帧」作为 AEC 远端参考；handler 在 `JitterBuffer.drain()` 后将 (near, reverse) 配对喂入新模块 `WebRTCAPM`，后者按 10ms 拆帧、严格「先 `process_reverse_stream` 后 `process_stream`」调用 APM。两条 near 路径（正常接收 + barge-in）都过 APM。VAD 不动。`CALLBOT_AEC_ENABLED=false` 或库缺失时走原路径无回归。

**Tech Stack:** Python 3 / asyncio / pydantic-settings / `webrtc_audio_processing`（xiongyihui 绑定，pip 名 `webrtc-audio-processing`）/ pytest

**Spec:** `docs/superpowers/specs/2026-06-12-webrtc-aec-design.md`

---

## 关键约束（来自源码确认，实现时不可违背）

1. **APM 每次 `process_stream` / `process_reverse_stream` 只吃 10ms** = 320 bytes（16kHz/16-bit/mono）。管线是 30ms/960B，须拆 3×320B。
2. **调用顺序铁律**：每对子帧必须先 `process_reverse_stream(reverse)`（内部会 `set_stream_delay_ms`）再 `process_stream(near)`。
3. **`aec_type=3`（AEC3）源码被注释，不可用**；只能 `1`(AECM) 或 `2`(老 AEC)。
4. **库需源码编译**（swig+autotools+git submodule），无预编译 wheel；因此 `requirements.txt` 中**注释掉**（避免 `pip install -r` 阻塞），运行时延迟 import + ImportError fallback。

---

## File Structure

| 文件 | 责任 | 动作 |
|------|------|------|
| `agent-flow/src/ws/audio_processing.py` | `WebRTCAPM` 封装（拆帧/调用顺序/delay）+ `create_audio_processing()` 工厂 | **Create** |
| `agent-flow/tests/ws/test_audio_processing.py` | WebRTCAPM 单元测试（注入 fake AP，不依赖真实库） | **Create** |
| `agent-flow/src/ws/jitter_buffer.py` | `TTSOutputBuffer.recent_reverse`（镜像最近播放帧） | **Modify** |
| `agent-flow/src/ws/handler.py` | 构造参数加 `apm`；两条 near 路径插 `apm.process()`；AEC 开时跳过 `_apply_gain`；`_receive_during_streaming` 加 `tts_buffer` 参数 | **Modify** |
| `agent-flow/src/config.py` | `CALLBOT_AEC_*` 配置字段 | **Modify** |
| `agent-flow/main.py` | `create_audio_processing()` 注入 handler；`_log_startup_summary` 加 AEC 行 | **Modify** |
| `agent-flow/requirements.txt` | `webrtc-audio-processing`（注释掉，附编译说明） | **Modify** |
| `agent-flow/tests/ws/__init__.py` | 测试包 | **Create**（空） |

---

## Task 1: 前置 — 验证库可安装 + 声明依赖

**Files:**
- Modify: `agent-flow/requirements.txt`
- Verify: 本地 conda 环境 + Dockerfile 编译可行性

- [ ] **Step 1: 本地 conda 验证安装**

```bash
conda activate <env>  # 你的 agent-flow conda 环境
which swig || brew install swig   # macOS 需要 swig
pip install webrtc-audio-processing
python -c "from webrtc_audio_processing import AudioProcessingModule as AP; ap=AP(2,True,1,False); print('OK', ap)"
```

Expected: 打印 `OK <...AudioProcessingModule object...>`。若编译失败（swig/autotools 缺失），记录错误——后续任务仍可进行（单元测试用 fake AP，不依赖真实库），但 Task 7 端到端验证需先解决安装。

- [ ] **Step 2: 声明依赖（注释掉，避免阻塞 pip install -r）**

在 `agent-flow/requirements.txt` 的 `# Denoise` 段（约 line 44-45）下方追加：

```text
# AEC + NS + AGC (可选，需源码编译)
# webrtc-audio-processing>=0.1.3  # 需 swig+autotools+git submodule 编译，pip 名 webrtc-audio-processing，import 名 webrtc_audio_processing；AEC 用，按需启用
```

- [ ] **Step 3: Commit**

```bash
git add agent-flow/requirements.txt
git commit -m "chore: 声明 webrtc-audio-processing 可选依赖（AEC 用，注释默认关）"
```

---

## Task 2: config.py — 新增 AEC 配置字段

**Files:**
- Modify: `agent-flow/src/config.py:98`（`audio_gain` 字段之后）

- [ ] **Step 1: 新增配置字段**

在 `audio_gain: float = 1.0`（line 98）之后插入：

```python
    # Audio gain (pre-ASR amplification for quiet SIP audio)
    audio_gain: float = 1.0

    # WebRTC AEC + NS + AGC (audio_processing.py) — 替换 denoise + 固定增益
    aec_enabled: bool = False
    aec_type: int = 2  # 1=AECM(移动端), 2=老AEC (AEC3 源码注释不可用)
    aec_ns_level: int = 2  # NS 抑制等级 0-3
    aec_agc_type: int = 1  # 0=关, 1=AdaptiveDigital, 2=AdaptiveAnalog
    aec_system_delay_ms: int = 80  # 回声延迟先验(毫秒)，has_echo 监控后标定
```

- [ ] **Step 2: 验证 import 无误**

```bash
cd agent-flow && PYTHONPATH=$(pwd)/src python -c "from config import settings; print(settings.aec_enabled, settings.aec_type, settings.aec_system_delay_ms)"
```

Expected: `False 2 80`

- [ ] **Step 3: Commit**

```bash
git add agent-flow/src/config.py
git commit -m "feat(config): 新增 CALLBOT_AEC_* 配置（AEC/NS/AGC 接入）"
```

---

## Task 3: audio_processing.py — WebRTCAPM 封装 + 工厂（TDD）

**Files:**
- Create: `agent-flow/src/ws/audio_processing.py`
- Create: `agent-flow/tests/ws/__init__.py`（空文件）
- Create: `agent-flow/tests/ws/test_audio_processing.py`
- Create: `agent-flow/tests/__init__.py`（若不存在，空文件）

- [ ] **Step 1: 写失败测试（拆帧 + 输出长度 + 调用顺序 + delay + reverse 缺失）**

`agent-flow/tests/ws/test_audio_processing.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ws/test_audio_processing.py -v
```

Expected: FAIL / collection error（`ModuleNotFoundError: No module named 'ws.audio_processing'`）

- [ ] **Step 3: 创建空 `__init__.py`**

```bash
mkdir -p agent-flow/tests/ws && touch agent-flow/tests/ws/__init__.py
[ -f agent-flow/tests/__init__.py ] || touch agent-flow/tests/__init__.py
```

- [ ] **Step 4: 写实现**

`agent-flow/src/ws/audio_processing.py`：

```python
"""WebRTC APM 封装 — HPF + AEC + NS + AGC 一次过。

替换 denoise.py 的具体降噪器 + 固定 AUDIO_GAIN。near 端每 30ms 帧拆成 3×10ms
子帧喂入 AudioProcessingModule；reverse（TTS 远端参考）成对先喂，启用回声消除。

调用顺序铁律（源码 audio_processing_module.cpp 确认）：每对子帧必须先
process_reverse_stream（内部 set_stream_delay_ms）再 process_stream。
"""
import logging

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
# 10ms @ 16kHz 16-bit mono = 160 samples = 320 bytes；pipeline 30ms = 3×320
SUBFRAME_BYTES = 320


class WebRTCAPM:
    """AudioProcessingModule 的帧级封装。

    near: 麦克风回采（含 TTS 回声 + 语音 + 噪声）
    reverse: 正在播放的 TTS 帧（AEC 远端参考）；AI 沉默时为静音帧
    """

    def __init__(
        self,
        aec_type: int,
        ns_level: int,
        agc_type: int,
        system_delay_ms: int,
        _ap_cls=None,
    ) -> None:
        # _ap_cls=None 时延迟 import 真实库；测试注入 fake
        if _ap_cls is None:
            from webrtc_audio_processing import AudioProcessingModule as _AP
            _ap_cls = _AP
        self._ap = _ap_cls(aec_type=aec_type, enable_ns=True,
                           agc_type=agc_type, enable_vad=False)
        self._ap.set_stream_format(SAMPLE_RATE, CHANNELS, SAMPLE_RATE, CHANNELS)
        self._ap.set_reverse_stream_format(SAMPLE_RATE, CHANNELS)
        self._ap.set_ns_level(ns_level)
        self._system_delay_ms = system_delay_ms
        logger.info("WebRTCAPM init: aec_type=%d ns=%d agc=%d delay=%dms",
                    aec_type, ns_level, agc_type, system_delay_ms)

    def process(self, near_frame: bytes, reverse_frame: bytes | None) -> bytes:
        """处理一个 30ms near 帧，返回去回声+降噪+增益后的等长帧。

        失败时降级返回原始 near_frame（单帧错误不影响通话）。
        """
        try:
            if reverse_frame:
                self._ap.set_system_delay(self._system_delay_ms)
                for i in range(0, len(reverse_frame), SUBFRAME_BYTES):
                    sub = reverse_frame[i:i + SUBFRAME_BYTES]
                    if len(sub) == SUBFRAME_BYTES:
                        self._ap.process_reverse_stream(sub)
            out = bytearray()
            for i in range(0, len(near_frame), SUBFRAME_BYTES):
                sub = near_frame[i:i + SUBFRAME_BYTES]
                if len(sub) == SUBFRAME_BYTES:
                    out.extend(self._ap.process_stream(sub))
                else:
                    out.extend(sub)  # 尾部不足 10ms 透传
            return bytes(out)
        except Exception as e:
            logger.error("WebRTCAPM process failed, passthrough: %s", e)
            return near_frame

    def has_echo(self) -> bool:
        try:
            return self._ap.has_echo()
        except Exception:
            return False


def create_audio_processing(settings) -> "WebRTCAPM | None":
    """工厂：CALLBOT_AEC_ENABLED=true 时创建 WebRTCAPM，否则/库缺失返回 None。"""
    if not settings.aec_enabled:
        return None
    try:
        return WebRTCAPM(
            aec_type=settings.aec_type,
            ns_level=settings.aec_ns_level,
            agc_type=settings.aec_agc_type,
            system_delay_ms=settings.aec_system_delay_ms,
        )
    except ImportError as e:
        logger.warning("webrtc_audio_processing 未安装，AEC 关闭（走原 denoise 路径）: %s", e)
        return None
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ws/test_audio_processing.py -v
```

Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add agent-flow/src/ws/audio_processing.py agent-flow/tests/
git commit -m "feat(ws): 新增 WebRTCAPM 封装 WebRTC APM（AEC+NS+AGC）+ 单元测试"
```

---

## Task 4: jitter_buffer.py — TTSOutputBuffer.recent_reverse（TDD）

**Files:**
- Modify: `agent-flow/src/ws/jitter_buffer.py`（`TTSOutputBuffer.__init__` 与 `_send_loop`）
- Create: `agent-flow/tests/ws/test_tts_output_buffer_reverse.py`

- [ ] **Step 1: 写失败测试**

`agent-flow/tests/ws/test_tts_output_buffer_reverse.py`：

```python
"""TTSOutputBuffer.recent_reverse — 镜像最近发往 FreeSWITCH 的帧，供 AEC 做远端参考。"""
import asyncio
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))

from ws.jitter_buffer import TTSOutputBuffer, SILENCE_FRAME  # noqa: E402


def test_recent_reverse_defaults_to_silence():
    sent = []
    buf = TTSOutputBuffer(send_fn=lambda b: sent.append(b))
    assert buf.recent_reverse == SILENCE_FRAME


def test_recent_reverse_updates_to_sent_frame():
    async def main():
        sent = []
        buf = TTSOutputBuffer(send_fn=lambda b: sent.append(b))
        await buf.start()
        payload = b"\x01" * 960  # 一个 30ms 帧
        buf.write(payload)
        await asyncio.sleep(0.12)  # > 30ms，让 _send_loop 发出
        await buf.stop()
        assert sent, "no frame sent"
        assert buf.recent_reverse == payload

    asyncio.run(main())


def test_recent_reverse_becomes_silence_during_gap():
    async def main():
        sent = []
        buf = TTSOutputBuffer(send_fn=lambda b: sent.append(b))
        await buf.start()
        buf.write(b"\x01" * 960)
        await asyncio.sleep(0.12)
        # 不再 write，进入静音填充窗口 → 发静音帧 → recent_reverse 归零
        await asyncio.sleep(0.12)
        await buf.stop()
        assert buf.recent_reverse == SILENCE_FRAME

    asyncio.run(main())
```

- [ ] **Step 2: 运行确认失败**

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ws/test_tts_output_buffer_reverse.py -v
```

Expected: FAIL（`AttributeError: 'TTSOutputBuffer' object has no attribute 'recent_reverse'`）

- [ ] **Step 3: 加 `recent_reverse` 初始值**

在 `TTSOutputBuffer.__init__` 末尾（`self._last_write_time: float = 0.0` 之后，约 `jitter_buffer.py:198`）追加：

```python
        self._last_write_time: float = 0.0
        # AEC 远端参考：镜像最近发往 FreeSWITCH 的帧（TTS 帧或静音帧）
        self.recent_reverse: bytes = SILENCE_FRAME
```

- [ ] **Step 4: `_send_loop` 发送后更新镜像**

在 `_send_loop` 中，找到发送 TTS 帧的位置（`await self._send_fn(frame)` 之后、`frames_sent += 1` 之前，约 `jitter_buffer.py:300-307`），改为：

```python
                    frame = self._buffer.popleft()
                    try:
                        await self._send_fn(frame)
                    except Exception as e:
                        logger.error(
                            "TTSOutputBuffer send error (type=%s, repr=%r, frames_sent=%d): %s",
                            type(e).__name__, e, frames_sent, e,
                        )
                        return
                    self.recent_reverse = frame  # AEC 远端参考 = 此刻发往线路的 TTS 帧
                    frames_sent += 1
                    await asyncio.sleep(self._frame_interval)
```

找到发送静音帧的位置（`await self._send_fn(SILENCE_FRAME)` 之后、`silence_sent += 1` 之前，约 `jitter_buffer.py:333-338`），改为：

```python
                            try:
                                await self._send_fn(SILENCE_FRAME)
                            except Exception as e:
                                logger.error("TTSOutputBuffer silence send error: %s", e)
                                return
                            self.recent_reverse = SILENCE_FRAME  # AI 沉默 → AEC 参考归零
                            silence_sent += 1
```

- [ ] **Step 5: 运行确认通过**

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ws/test_tts_output_buffer_reverse.py -v
```

Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add agent-flow/src/ws/jitter_buffer.py agent-flow/tests/ws/test_tts_output_buffer_reverse.py
git commit -m "feat(ws): TTSOutputBuffer 镜像最近播放帧供 AEC 远端参考"
```

---

## Task 5: handler.py — 接入 APM

**Files:**
- Modify: `agent-flow/src/ws/handler.py`（构造、`handle`、`_receive_during_streaming`、`_finalize_asr_and_gain`）

- [ ] **Step 1: 构造函数加 `apm` 参数**

在 `StreamingCallHandler.__init__` 签名中（`handler.py:55-73`），在 `denoiser: BaseDenoiser | None = None,` 之后加一行参数；并在方法体内 `self._denoiser = denoiser or PassThroughDenoiser()` 之后存 apm。

签名改为（在 `denoiser=...` 之后插入 `apm` 行）：

```python
        denoiser: BaseDenoiser | None = None,
        apm: "WebRTCAPM | None" = None,
        asr_grpc_client: "ASRGrpcClient | None" = None,
```

方法体内（`self._denoiser = denoiser or PassThroughDenoiser()` 之后）追加：

```python
        self._denoiser = denoiser or PassThroughDenoiser()
        self._apm = apm
```

并在文件顶部 import 区（`from ws.denoise import ...` 之后，约 `handler.py:27`）追加：

```python
from ws.audio_processing import WebRTCAPM
```

- [ ] **Step 2: 抽取「near 帧处理」为统一逻辑**

为避免正常路径与 barge-in 路径重复改两遍，在 `StreamingCallHandler` 内新增一个方法（放在 `_apply_gain` 之后，约 `handler.py:296`）：

```python
    def _process_near_frame(self, smooth_frame: bytes, tts_buffer: "TTSOutputBuffer") -> bytes:
        """near 端帧处理：AEC 开启时走 WebRTCAPM（near + reverse），否则走原 denoiser。"""
        if self._apm is not None:
            return self._apm.process(smooth_frame, tts_buffer.recent_reverse)
        return self._denoiser.process(smooth_frame)
```

- [ ] **Step 3: 正常接收路径调用新逻辑**

`handle()` 中（`handler.py:205`），把：

```python
                        denoised_frame = self._denoiser.process(smooth_frame)
```

改为：

```python
                        denoised_frame = self._process_near_frame(smooth_frame, tts_buffer)
```

- [ ] **Step 4: `_receive_during_streaming` 加 `tts_buffer` 参数**

`_receive_during_streaming` 签名（`handler.py:402-416`）末尾加参数：

```python
        tolerance_counter: list[int],
        tts_buffer: "TTSOutputBuffer",
    ) -> bool:
```

方法体内（`handler.py:442`）把：

```python
                denoised_frame = self._denoiser.process(smooth_frame)
```

改为：

```python
                denoised_frame = self._process_near_frame(smooth_frame, tts_buffer)
```

调用点（`handle()` 中 `_receive_during_streaming(...)` 调用，`handler.py:151-156`）末尾加 `tts_buffer`：

```python
                    barge_detected = await self._receive_during_streaming(
                        websocket, call_id, vad, jitter, audio_buffer,
                        streaming_task, barge_in_event, active_call,
                        barge_grace_until, ai_has_spoken, barge_speech_counter,
                        barge_tolerance_counter, tts_buffer,
                    )
```

- [ ] **Step 5: AEC 开启时跳过固定增益（AGC 已由 APM 处理）**

`_finalize_asr_and_gain` 中（`handler.py:356`），把：

```python
        raw_audio = self._apply_gain(bytes(audio_buffer), audio_gain)
```

改为：

```python
        # AEC 开启时 AGC 已由 WebRTCAPM 逐帧处理，不再叠加固定增益
        if self._apm is not None:
            raw_audio = bytes(audio_buffer)
        else:
            raw_audio = self._apply_gain(bytes(audio_buffer), audio_gain)
```

- [ ] **Step 6: 验证 import 与语法**

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "from ws.handler import StreamingCallHandler; print('OK')"
```

Expected: `OK`（无语法/import 错误）

- [ ] **Step 7: 运行全部已有测试确认无回归**

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v
```

Expected: 既有测试全 pass（Task 3、Task 4 的 9 个）

- [ ] **Step 8: Commit**

```bash
git add agent-flow/src/ws/handler.py
git commit -m "feat(ws): handler 接入 WebRTCAPM，两条 near 路径过 AEC，跳过固定增益"
```

---

## Task 6: main.py — 工厂注入 + startup log

**Files:**
- Modify: `agent-flow/main.py`（lifespan ⑥ 与 `_log_startup_summary`）

- [ ] **Step 1: import 工厂**

在 `main.py` import 区（`from src.ws.denoise import create_denoiser` 之后，约 `line 37`）追加：

```python
from src.ws.audio_processing import create_audio_processing
```

- [ ] **Step 2: lifespan ⑥ 创建 apm 并注入**

在 `denoiser = create_denoiser()`（`main.py:212`）之后追加：

```python
    denoiser = create_denoiser()
    apm = create_audio_processing(settings)
```

在 `StreamingCallHandler(...)` 构造（`main.py:215-232`）的 `denoiser=denoiser,` 之后加一行：

```python
        denoiser=denoiser,
        apm=apm,
```

- [ ] **Step 3: startup log 打印 AEC 状态**

在 `_log_startup_summary`（`main.py:248` 的 `Denoise` 行之后）追加：

```python
    logger.info("  Denoise: %s", settings.denoise_enabled or "disabled")
    logger.info("  AEC/APM: enabled=%s type=%d ns=%d agc=%d delay=%dms",
                settings.aec_enabled, settings.aec_type,
                settings.aec_ns_level, settings.aec_agc_type, settings.aec_system_delay_ms)
```

- [ ] **Step 4: 验证应用可启动（AEC 关闭默认路径）**

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "
import main
print('import OK')
from src.config import settings
print('aec_enabled=', settings.aec_enabled)
"
```

Expected: 打印 `import OK` 与 `aec_enabled= False`

- [ ] **Step 5: Commit**

```bash
git add agent-flow/main.py
git commit -m "feat(main): 注入 create_audio_processing 工厂 + startup log 输出 AEC 状态"
```

---

## Task 7: 端到端验证（AEC on/off 回归）

**Files:** 无代码改动，仅运行验证

- [ ] **Step 1: AEC 关闭 — 行为零回归**

确认 `.env` 无 `CALLBOT_AEC_ENABLED` 或为 `false`：

```bash
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v
```

Expected: 全 pass；`startup log` 显示 `AEC/APM: enabled=False`

- [ ] **Step 2: AEC 开启 — 模块加载（需库已安装）**

设置环境变量启动（需 Task 1 已装好 `webrtc_audio_processing`）：

```bash
cd agent-flow && CALLBOT_AEC_ENABLED=true PYTHONPATH=$(pwd):$(pwd)/src python -c "
import main
from src.config import settings
from src.ws.audio_processing import create_audio_processing
apm = create_audio_processing(settings)
print('apm=', apm)
out = apm.process(b'\x00'*960, b'\x01'*960) if apm else None
print('process ok, len=', len(out) if out else 'N/A')
"
```

Expected: `apm= <WebRTCAPM object>`；`process ok, len= 960`；日志含 `WebRTCAPM init: aec_type=2 ...`

- [ ] **Step 3: AEC 开启但库缺失 — fallback 无阻断**

模拟库未安装（临时改名）：

```bash
cd agent-flow && CALLBOT_AEC_ENABLED=true PYTHONPATH=$(pwd):$(pwd)/src python -c "
import main
from src.config import settings
from src.ws.audio_processing import create_audio_processing
# 强制触发 ImportError 路径
import sys
sys.modules['webrtc_audio_processing'] = None  # 让 import 抛 ImportError
apm = create_audio_processing(settings)
print('apm=', apm)
"
```

Expected: `apm= None`；日志 warning 含 `webrtc_audio_processing 未安装`；应用不崩溃

- [ ] **Step 4: 真实通话链路验证（手动，需 FreeSWITCH + ASR/TTS 全栈）**

按 `CLAUDE.md` 启动顺序 `./scripts/local.sh fs asr tts flow`（flow 需 `.env` 设 `CALLBOT_AEC_ENABLED=true`）。发起一通测试外呼，AI 说话时观察：

- 日志中 barge-in 是否仍被 AI 回声误触发（应减少）
- `WebRTCAPM` 日志正常初始化
- ASR 是否不再把 AI 自己的话识别成用户输入

记录 `aec_system_delay_ms` 不同取值下 barge-in 误触发次数，作为后续标定依据（写入 PR 描述）。

- [ ] **Step 5: 最终 commit（若有日志/调参微调）**

```bash
git add -A
git commit -m "test: AEC 端到端验证通过（on/off 回归 + fallback）"
git log --oneline -8
```

---

## Self-Review

**1. Spec coverage**
- §4.3 WebRTCAPM 模块 → Task 3 ✓
- §4.2 reference 镜像（方案 A）→ Task 4 ✓
- §4.5 handler 两条 near 路径 + gain 跳过 → Task 5 ✓
- §4.6 配置项 → Task 2 ✓
- §4.5 main.py 注入 + startup log → Task 6 ✓
- §3 库选型/安装 → Task 1 ✓
- §5 fallback（import 失败 → None）→ Task 3 工厂 + Task 7 Step 3 验证 ✓
- §6 测试（拆帧/顺序/delay/reverse 缺失/AEC 有效性）→ Task 3（单元）+ Task 7（端到端）✓
- §9 验收标准 → Task 7 ✓

**2. Placeholder scan** — 无 TBD/TODO；每步含完整代码与确切命令。

**3. Type consistency**
- `WebRTCAPM.process(near_frame: bytes, reverse_frame: bytes | None) -> bytes` — Task 3 定义，Task 5 `_process_near_frame` 调用签名一致 ✓
- `create_audio_processing(settings) -> WebRTCAPM | None` — Task 3 定义，Task 6 调用一致 ✓
- `TTSOutputBuffer.recent_reverse: bytes` — Task 4 定义，Task 5 `_process_near_frame` 读取一致 ✓
- handler `apm` 参数类型 `"WebRTCAPM | None"` — Task 5 定义，Task 6 `apm=apm` 注入一致 ✓
- 配置字段名 `aec_enabled/aec_type/aec_ns_level/aec_agc_type/aec_system_delay_ms` — Task 2 定义，Task 3 工厂读取、Task 6 日志输出一致 ✓
