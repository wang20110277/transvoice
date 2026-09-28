# Remove FSMN-VAD from agent-asr Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把语音端点检测（切段）职责从 agent-asr 的 FSMN-VAD 服务端分段迁移到 agent-flow 已集成的 livekit silero VAD（StreamAdapter + `turn_detection="vad"`），agent-asr 退化为无状态整段识别服务。

**Architecture:** `TransvoiceSTT` 改为非流式批量 STT（`_recognize_impl` 每语音段一条 WS 连接），由 SDK 默认 `stt_node` 自动包装 `stt.StreamAdapter(vad=session_vad)` 切段；agent-asr WS 协议改为「config → binary 累积 → end → 整段识别 → 单 result → 关连接」；轮次提交 `turn_detection` 从 `"stt"` 切 `"vad"`。

**Tech Stack:** Python 3.12 / FastAPI + WebSocket（agent-asr）、livekit-agents 1.8.3（`stt.STT`/`stt.StreamAdapter`/`inference.VAD`）、pytest。

**Spec:** `openspec/changes/remove-fsmn-vad-from-agent-asr/`（proposal.md / design.md / specs/conversation-pipeline/spec.md / tasks.md / plan-ready.md）

## Global Constraints

- livekit-agents 版本 `>=1.8.3,<1.9`（conda base 已装 1.8.3）；**不引入新依赖**。
- 测试命令统一用 base 环境：`/usr/local/Caskroom/miniconda/base/bin/python -m pytest`（下文 `$PY` 代称）。
- 基线（2026-09-28 实测）：agent-asr 15 passed；agent-flow 102 passed。任何 task 完成后不得低于此。
- 范围外（不得改动）：TransvoiceTTS/tts_plugin、MCP、LangGraph 管线、录音归档、OutboundExecutor、ESL；funasr 依赖保留（SenseVoice 引擎仍用）。
- `CALLBOT_ENDPOINTING_MIN/MAX_DELAY` 变量名与语义（语音结束后提交窗口）不变；新增 `CALLBOT_VAD_MIN_SILENCE_DURATION`（默认 0.25）。
- 每 task 一个 commit；测试不通过不许提交。
- build 阶段不改 openspec 规格文档；发现偏差记入本文件「偏差记录」节，留 close 处理。
- **已验证 SDK 事实（勿重推导）**：① 默认 `stt_node` 对 `capabilities.streaming=False` 且 session 有 vad 的 STT 自动包 `StreamAdapter`；② StreamAdapter 对空 alternatives 或空 text 的 recognize 结果直接跳过（不发 FINAL）；③ `APIConnectionError` 从 `livekit.agents` 顶层导入（`livekit.agents.stt` 未 re-export）；④ `STT.recognize()` 基类按 conn_options 重试；⑤ `inference.VAD(min_silence_duration=...)` 构造器直吃 VAD 参数。

## 偏差记录（close 阶段回填规格）

- design.md 曾写「保留 `_SINGLE_ATTEMPT_CONN_OPTIONS` 约定」——该常量服务于已删除的 `stream()` 路径，重写后为死代码，本计划删除（遵循「不写死代码」项目规范）。

---

### Task 1: agent-asr 无状态整段识别服务

**Files:**
- Modify: `agent-asr/asradapter/ws_server.py`（全文重写）
- Modify: `agent-asr/asradapter/main.py`（删 VAD 加载）
- Test: `agent-asr/tests/test_ws_server.py`（全文重写）
- Delete: `agent-asr/asradapter/vad_segmenter.py`、`agent-asr/tests/test_vad_segmenter.py`

**Interfaces:**
- Consumes: `ASREngine.recognize(audio: bytes, params: dict) -> ASRResult(text, confidence, is_final)`（`asradapter/base.py`，不变）
- Produces: `ASRWebSocketHandler(engine)`（单参数构造，原 `(engine, segmenter)` 废除）；WS 协议 `config → binary* → end → 单 result → 服务端关连接`

- [ ] **Step 1: 重写失败测试**

用以下内容**全文替换** `agent-asr/tests/test_ws_server.py`：

```python
"""ASRWebSocketHandler 单测 — 无状态整段识别协议：config → binary 累积 → end → 单 result → 终结。

mock engine（返回固定文本），不依赖真实模型。
"""
import asyncio
import json

import pytest

from asradapter.base import ASRResult
from asradapter.ws_server import ASRWebSocketHandler


class _FakeEngine:
    def __init__(self, text="你好"):
        self._text = text
        self.audio: list[bytes] = []

    async def recognize(self, audio, params):
        self.audio.append(audio)
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
async def test_frames_accumulate_then_end_single_result():
    engine = _FakeEngine("整段文本")
    handler = ASRWebSocketHandler(engine)
    ws = _FakeWS([_config_msg(), b"frame1", b"frame2", json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    assert engine.audio == [b"frame1frame2"]  # 拼接后整段识别，恰好一次
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert results == [{"type": "result", "text": "整段文本",
                        "confidence": 0.95, "is_final": True}]


@pytest.mark.asyncio
async def test_sample_rate_8000_triggers_resample():
    engine = _FakeEngine()
    handler = ASRWebSocketHandler(engine)
    ws = _FakeWS([_config_msg(sample_rate=8000),
                  b"\x01\x00" * 160, json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    # 160 samples @ 8k = 20ms；常量幅度线性插值不变 → 320 samples @ 16k = 640B
    assert engine.audio == [b"\x01\x00" * 320]


@pytest.mark.asyncio
async def test_end_without_audio_returns_empty_result():
    engine = _FakeEngine()
    handler = ASRWebSocketHandler(engine)
    ws = _FakeWS([_config_msg(), json.dumps({"type": "end"})])
    await asyncio.wait_for(handler.handle(ws), timeout=2.0)
    assert engine.audio == []  # 空音频不调 engine
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert results == [{"type": "result", "text": "",
                        "confidence": 0.0, "is_final": True}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd agent-asr && PYTHONPATH=$(pwd) $PY -m pytest tests/test_ws_server.py -v`
Expected: FAIL（`TypeError: ASRWebSocketHandler.__init__() missing 1 required positional argument: 'segmenter'`）

- [ ] **Step 3: 重写 ws_server.py 与 main.py**

用以下内容**全文替换** `agent-asr/asradapter/ws_server.py`：

```python
"""WebSocket ASR 服务 — 无状态整段识别（切段职责在客户端）。

协议:
    客户端 → 服务端:
        Text JSON: {"type":"config","call_id":"...","language":"zh","sample_rate":16000}
        Binary:    PCM 16-bit mono 音频帧（逐帧重采样到 16kHz 后累积）
        Text JSON: {"type":"end"}  整段识别触发
    服务端 → 客户端:
        Text JSON: {"type":"result","text":"...","confidence":0.95,"is_final":true}
                    ↑ end 后回单条 result 并关闭连接
        Text JSON: {"type":"error","message":"..."}
"""
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from asradapter.base import ASREngine

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def _resample_to_16k(pcm: bytes, declared_sr: int) -> bytes:
    """declared_sr → 16kHz 重采样(线性插值,够用;整数倍关系直接重采样)。"""
    if declared_sr == SAMPLE_RATE or declared_sr <= 0:
        return pcm
    # 16-bit mono samples;用 numpy 线性插值(已在依赖链:funasr 依赖 numpy)
    import numpy as np
    n_in = len(pcm) // 2
    if n_in == 0:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(round(n_in * SAMPLE_RATE / declared_sr))
    idx = np.linspace(0, n_in - 1, n_out)
    resampled = np.interp(idx, np.arange(n_in), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


class ASRWebSocketHandler:
    """WS handler — 无状态整段识别：客户端切段（VAD 在客户端），服务端只累积识别。"""

    def __init__(self, engine: ASREngine):
        self._engine = engine

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        call_id = ""
        language = "zh"
        declared_sr = SAMPLE_RATE
        audio = bytearray()

        try:
            while True:
                data = await websocket.receive()

                if "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")

                    if msg_type == "config":
                        call_id = msg.get("call_id", "")
                        language = msg.get("language", "zh")
                        declared_sr = int(msg.get("sample_rate", SAMPLE_RATE))
                        logger.info("[WS-ASR] config call_id=%s sr=%d", call_id, declared_sr)

                    elif msg_type == "end":
                        if not audio:
                            await websocket.send_json({
                                "type": "result", "text": "",
                                "confidence": 0.0, "is_final": True})
                            return
                        await self._recognize_and_push(
                            websocket, bytes(audio), call_id, language)
                        return

                elif "bytes" in data and data["bytes"]:
                    audio.extend(_resample_to_16k(data["bytes"], declared_sr))

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
            logger.error("[WS-ASR] recognize error call_id=%s: %s", call_id, e)
            await websocket.send_json({"type": "error", "message": str(e)})
            return
        await websocket.send_json({
            "type": "result", "text": result.text,
            "confidence": result.confidence, "is_final": True,
        })
```

`agent-asr/asradapter/main.py` 三处修改——删除 import 行：

```python
from asradapter.vad_segmenter import FsmnVadSegmenter, load_fsmn_vad_model
```

删除全局变量行：

```python
_vad_model = None
```

lifespan 改为（去掉 `global engine, _vad_model` 的 `_vad_model`、VAD 加载块与日志）：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    config = _load_config()
    engine = load_asr_engine(config["engine"]["asr"])
    if hasattr(engine, "load_model"):
        await engine.load_model()
    logger.info(f"ASR engine loaded: {config['engine']['asr']}")

    yield
```

`ws_streaming_recognize` 末尾两行改为：

```python
    handler = ASRWebSocketHandler(engine)
    await handler.handle(websocket)
```

其 docstring 的协议描述同步替换为：

```python
    """整段语音识别（WebSocket）— 客户端切段，服务端无状态识别。

    协议:
        发送 (客户端 → 服务端):
            - Text JSON 帧: {"type": "config", "call_id": "xxx", "language": "zh", "sample_rate": 16000}
            - Binary 帧: PCM 16-bit mono 音频数据（可多帧，逐帧重采样到 16kHz 累积）
            - Text JSON 帧: {"type": "end"} 触发整段识别
        接收 (服务端 → 客户端):
            - Text JSON 帧: {"type": "result", "text": "...", "confidence": 0.95, "is_final": true}
            - Text JSON 帧: {"type": "error", "message": "..."}
        end 处理完服务端关闭连接。
    """
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd agent-asr && PYTHONPATH=$(pwd) $PY -m pytest tests/test_ws_server.py -v`
Expected: 3 passed

- [ ] **Step 5: 删除 VAD 模块与测试**

```bash
git rm agent-asr/asradapter/vad_segmenter.py agent-asr/tests/test_vad_segmenter.py
ls agent-asr/models/   # 确认只有 SenseVoiceSmall，无 FSMN 目录（本地已不存在，若发现则一并 git rm -r）
```

- [ ] **Step 6: 跑全量 + grep 残留**

Run: `cd agent-asr && PYTHONPATH=$(pwd) $PY -m pytest tests/ -v`（Expected: 3 passed——旧 15 个中 12 个 VAD 用例已删）
Run: `grep -rn "fsmn\|FsmnVad\|vad_segmenter" agent-asr/asradapter agent-asr/tests`
Expected: 无输出

- [ ] **Step 7: Commit**

```bash
git add agent-asr/asradapter/ws_server.py agent-asr/asradapter/main.py agent-asr/tests/test_ws_server.py
git commit -m "refactor(asr): 退役 FSMN-VAD——ws_server 改无状态整段识别（config→累积→end→单 result）"
```

---

### Task 2: agent-flow 新增 VAD 端点配置

**Files:**
- Modify: `agent-flow/src/config.py:82-85`（livekit 参数组）
- Test: `agent-flow/tests/voice/test_config_livekit.py`

**Interfaces:**
- Produces: `settings.vad_min_silence_duration: float`（env `CALLBOT_VAD_MIN_SILENCE_DURATION`，默认 0.25）——Task 4 的 `session.py` 消费

- [ ] **Step 1: 写失败测试**

在 `agent-flow/tests/voice/test_config_livekit.py` 追加：

```python
def test_vad_min_silence_default():
    from config import settings
    assert settings.vad_min_silence_duration == 0.25
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/voice/test_config_livekit.py -v`
Expected: FAIL（`AttributeError: ... no attribute 'vad_min_silence_duration'`）

- [ ] **Step 3: 加配置字段**

`agent-flow/src/config.py` 将：

```python
    # livekit AgentSession 轮次/打断参数（design.md §2.2：FSMN 分段已含端点判定，min_delay 远小于 SDK 默认 0.5）
    endpointing_min_delay: float = 0.1
    endpointing_max_delay: float = 2.0
    interruption_min_duration: float = 0.5
```

替换为：

```python
    # livekit AgentSession 轮次/打断参数
    endpointing_min_delay: float = 0.1
    endpointing_max_delay: float = 2.0
    interruption_min_duration: float = 0.5
    # silero 判定语音结束的静音时长（原 agent-asr FSMN-VAD 尾静音确认角色的承接配置；
    # 0.25 = livekit-agents 1.8.3 inference.VAD 默认）
    vad_min_silence_duration: float = 0.25
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/voice/test_config_livekit.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/config.py agent-flow/tests/voice/test_config_livekit.py
git commit -m "feat(livekit): 新增 CALLBOT_VAD_MIN_SILENCE_DURATION（承接 FSMN 尾静音端点角色）"
```

---

### Task 3: TransvoiceSTT 重写为非流式批量插件

**Files:**
- Modify: `agent-flow/src/voice/stt_plugin.py`（全文重写）
- Test: `agent-flow/tests/voice/test_stt_plugin.py`（全文重写）

**Interfaces:**
- Consumes: Task 1 的 agent-asr 新协议（config → binary* → end → 单 result → 关连接）
- Produces: `TransvoiceSTT(ws_url=..., min_final_len=2)`，`capabilities=(streaming=False, interim_results=False)`，`await stt.recognize(buffer, language=..., conn_options=...) -> SpeechEvent`；短文本 → `alternatives=[]`（StreamAdapter 跳过 FINAL）

- [ ] **Step 1: 重写失败测试**

用以下内容**全文替换** `agent-flow/tests/voice/test_stt_plugin.py`：

```python
"""TransvoiceSTT：非流式批量 recognize —— 协议交互 + 短文本过滤 + 分帧 + 错误分类。

与 design.md 的差异记录：
- `_SINGLE_ATTEMPT_CONN_OPTIONS` 已随 stream() 路径删除（计划「偏差记录」）；
- 直调 recognize 时传 `APIConnectOptions(max_retry=0)` 保证错误即抛（基类不重试）。
"""
import asyncio
import json

import pytest
import websockets
from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions


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


def _buffer(nbytes: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(data=b"\x01\x02" * (nbytes // 2), sample_rate=16000,
                          num_channels=1, samples_per_channel=nbytes // 2)


def _patch(monkeypatch, fake):
    import voice.stt_plugin as sp

    async def fake_connect(*a, **kw):
        return fake

    monkeypatch.setattr(sp.websockets, "connect", fake_connect)
    return sp


_NO_RETRY = {"conn_options": APIConnectOptions(max_retry=0)}


@pytest.mark.asyncio
async def test_recognize_returns_final_event(monkeypatch):
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "你好请问", "confidence": 0.9})])
    sp = _patch(monkeypatch, fake)

    ev = await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
        _buffer(960), language="zh", **_NO_RETRY)

    assert ev.type == sp.SpeechEventType.FINAL_TRANSCRIPT
    assert ev.alternatives[0].text == "你好请问"
    assert ev.alternatives[0].confidence == 0.9
    # 发帧序列：config → 音频 → end → 关连接
    cfg = json.loads(fake.sent[0])
    assert cfg["type"] == "config" and cfg["sample_rate"] == 16000
    assert fake.sent[1] == b"\x01\x02" * 480
    assert json.loads(fake.sent[-1])["type"] == "end"
    assert fake.closed


@pytest.mark.asyncio
async def test_short_text_returns_empty_alternatives(monkeypatch):
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "嗯", "confidence": 0.5})])
    sp = _patch(monkeypatch, fake)

    ev = await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
        _buffer(960), language="zh", **_NO_RETRY)

    assert ev.type == sp.SpeechEventType.FINAL_TRANSCRIPT
    assert ev.alternatives == []  # StreamAdapter 据此跳过 FINAL


@pytest.mark.asyncio
async def test_large_audio_chunked_to_64k(monkeypatch):
    fake = FakeUpstreamWs([
        json.dumps({"type": "result", "text": "长段文本", "confidence": 0.9})])
    sp = _patch(monkeypatch, fake)

    total = 200 * 1024
    await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
        _buffer(total), language="zh", **_NO_RETRY)

    audio_frames = fake.sent[1:-1]
    assert all(isinstance(f, bytes) and len(f) <= 64 * 1024 for f in audio_frames)
    assert b"".join(audio_frames) == b"\x01\x02" * (total // 2)


@pytest.mark.asyncio
async def test_error_message_raises_connection_error(monkeypatch):
    fake = FakeUpstreamWs([json.dumps({"type": "error", "message": "boom"})])
    sp = _patch(monkeypatch, fake)

    with pytest.raises(APIConnectionError):
        await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
            _buffer(960), language="zh", **_NO_RETRY)


@pytest.mark.asyncio
async def test_disconnect_raises_connection_error(monkeypatch):
    fake = FakeUpstreamWs([websockets.ConnectionClosed(None, None)])
    sp = _patch(monkeypatch, fake)

    with pytest.raises(APIConnectionError):
        await sp.TransvoiceSTT(ws_url="ws://fake").recognize(
            _buffer(960), language="zh", **_NO_RETRY)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/voice/test_stt_plugin.py -v`
Expected: FAIL（现插件无 `recognize` 实现——`NotImplementedError: batch 模式未启用`）

- [ ] **Step 3: 重写 stt_plugin.py**

用以下内容**全文替换** `agent-flow/src/voice/stt_plugin.py`：

```python
"""TransvoiceSTT —— agent-asr WS（无状态整段识别）→ livekit 非流式批量 STT。

插件仅实现 _recognize_impl（每语音段一条连接：config → 分帧音频 → end → 单 result）；
流式能力由 SDK 默认 stt_node 自动包装的 stt.StreamAdapter 提供（silero VAD 切段 →
段级 recognize → SOS/FINAL/EOS 事件），插件不手工合成事件序列。要点（design.md D1/D4/D5）：
- 文本 strip 后 < min_final_len → 返回空 alternatives（StreamAdapter 跳过 FINAL 发射，
  过滤无意义短促噪声段）
- 上游故障一律 APIConnectionError（SDK 默认 retryable）→ STT.recognize 基类
  按 conn_options 重试，防 AgentSession 连续错误计数熔断拆通话
"""
from __future__ import annotations

import contextlib
import json
import logging

import websockets
from livekit import rtc
from livekit.agents import APIConnectionError, utils
from livekit.agents.stt import (
    STT,
    STTCapabilities,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
)

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_MAX_WS_CHUNK_BYTES = 64 * 1024  # 单 binary 帧上限，规避 websockets 接收缓冲限制


class TransvoiceSTT(STT):
    def __init__(self, *, ws_url: str, min_final_len: int = 2) -> None:
        super().__init__(
            capabilities=STTCapabilities(streaming=False, interim_results=False))
        self._ws_url = ws_url
        self._min_final_len = min_final_len

    @property
    def model(self) -> str:
        return "sensevoice"

    @property
    def provider(self) -> str:
        return "transvoice"

    async def _recognize_impl(self, buffer, *, language, conn_options):
        call_id = utils.shortuuid()[:8]
        pcm = bytes(rtc.combine_audio_frames(buffer).data)
        try:
            ws = await websockets.connect(
                self._ws_url, ping_interval=120, ping_timeout=180)
        except Exception as e:
            raise APIConnectionError(f"ASR connect failed: {e}") from e
        try:
            await ws.send(json.dumps({
                "type": "config", "call_id": call_id,
                "language": language or "zh", "sample_rate": _SAMPLE_RATE,
            }))
            for i in range(0, len(pcm), _MAX_WS_CHUNK_BYTES):
                await ws.send(pcm[i:i + _MAX_WS_CHUNK_BYTES])
            await ws.send(json.dumps({"type": "end"}))
            try:
                raw = await ws.recv()
            except websockets.ConnectionClosedOK:
                # 服务端回 result 后才关连接；未回即关属协议异常
                raise APIConnectionError("ASR closed without result")
            msg = json.loads(raw)
            if msg.get("type") == "error":
                raise APIConnectionError(
                    f"ASR server error: {msg.get('message', '')}")
            text = (msg.get("text") or "").strip()
        except APIConnectionError:
            raise
        except Exception as e:
            raise APIConnectionError(f"ASR recognize failed: {e}") from e
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

        if len(text) < self._min_final_len:
            logger.info("ASR final too short ('%s'), drop", text)
            return SpeechEvent(
                type=SpeechEventType.FINAL_TRANSCRIPT, alternatives=[])
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[SpeechData(
                language=language or "zh", text=text,
                confidence=msg.get("confidence", 0.0))],
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/voice/test_stt_plugin.py -v`
Expected: 5 passed

- [ ] **Step 5: 确认无其他模块引用已删符号**

Run: `grep -rn "TransvoiceRecognizeStream\|_SINGLE_ATTEMPT" agent-flow/src agent-flow/tests`
Expected: 无输出

- [ ] **Step 6: Commit**

```bash
git add agent-flow/src/voice/stt_plugin.py agent-flow/tests/voice/test_stt_plugin.py
git commit -m "refactor(livekit): TransvoiceSTT 改非流式批量——StreamAdapter 自动包装，每段一连接"
```

---

### Task 4: session 装配切换 vad 轮次检测 + 集成测试改造

**Files:**
- Modify: `agent-flow/src/voice/session.py:41-54, 57-95`
- Test: `agent-flow/tests/voice/test_session_integration.py`（全文重写）

**Interfaces:**
- Consumes: Task 2 的 `settings.vad_min_silence_duration`；SDK `inference.VAD(min_silence_duration=...)`、`turn_handling={"turn_detection": "vad"}`
- Produces: `build_agent_session(*, ctx, websocket, registry, esl, apm=None, denoiser=None, vad=None)`——`vad` 参数供测试注入 FakeVAD（对齐 apm/denoiser 注入模式）

- [ ] **Step 1: 重写失败集成测试**

用以下内容**全文替换** `agent-flow/tests/voice/test_session_integration.py`：

```python
"""无头集成：真实 build_agent_session 装配的 AgentSession 全链路两轮对话 + fake-VAD 打断。

仿 agents/tests/fake_session.py + fake_io.py + fake_stt.py + fake_tts.py 的结构
（测试内自含 Fake 类）：monkeypatch voice.session 命名空间里的
TransvoiceSTT/TransvoiceTTS/TransvoiceAgent，绕开真实 agent-asr/agent-tts WS
与 LangGraph 管线，其余全部真实——AgentSession 默认对话循环、turn_handling
（vad 轮次提交）、非流式 STT 经默认 stt_node 自动 StreamAdapter 包装
（TriggerFakeVAD END_OF_SPEECH 带 frames → recognize → FINAL）、
TelephonyAudioInput（jitter→denoise→gain 链）、TelephonyAudioOutput（30ms 匀速 +
playback_finished 契约）、事件接线均被覆盖。
TriggerFakeVAD 经 build_agent_session(vad=...) 注入：StreamAdapter 分段与
audio_recognition 轮次端点共用同一触发源（对齐真实 VAD 双流消费同一音频）。
"""
import asyncio
import time
from types import SimpleNamespace

import pytest
from livekit import rtc
from livekit.agents import APIConnectOptions
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents import utils
from livekit.agents.vad import VAD as LK_VAD, VADCapabilities, VADEvent, VADEventType, VADStream

from ws.jitter_buffer import FRAME_BYTES

_TTS_SAMPLE_RATE = 22050  # 与 TransvoiceTTS 一致，走真实 22050→16000 重采样路径
# 每段合成 0.24s 非零音频（12 × 10ms），非零字节用于与静音保活帧区分
_TTS_CHUNKS = 12
_TTS_CHUNK_SAMPLES = _TTS_SAMPLE_RATE // 100
# VAD 段内帧：单帧 960B（30ms @16kHz），供 StreamAdapter merge 后喂 recognize
_VAD_FRAME = rtc.AudioFrame(data=b"\x01\x02" * 480, sample_rate=16000,
                            num_channels=1, samples_per_channel=480)


class FakeBatchSTT(lk_stt.STT):
    """非流式 STT fake：_recognize_impl 弹出 texts 队列脚本，记录收到音频字节数。

    经默认 stt_node 的 StreamAdapter 自动包装（streaming=False + session vad），
    覆盖「VAD 切段 → recognize → FINAL」的真实链路。
    """

    latest: "FakeBatchSTT | None" = None

    def __init__(self, *, ws_url: str = "", min_final_len: int = 2) -> None:
        super().__init__(capabilities=lk_stt.STTCapabilities(
            streaming=False, interim_results=False))
        self.texts: list[str] = []
        self.recv_bytes: list[int] = []
        FakeBatchSTT.latest = self

    async def _recognize_impl(self, buffer, *, language, conn_options):
        frames = buffer if isinstance(buffer, list) else [buffer]
        self.recv_bytes.append(sum(len(bytes(f.data)) for f in frames))
        text = self.texts.pop(0) if self.texts else ""
        alternatives = ([lk_stt.SpeechData(language="zh", text=text)]
                        if text else [])
        return lk_stt.SpeechEvent(
            type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=alternatives)

    @property
    def model(self) -> str:
        return "fake-batch"

    @property
    def provider(self) -> str:
        return "test"


class FakeSynthesizeStream(lk_tts.SynthesizeStream):
    def __init__(self, *, tts, conn_options, texts: list, done_evt: asyncio.Event,
                 chunks: int = _TTS_CHUNKS):
        super().__init__(tts=tts, conn_options=conn_options)
        self._texts = texts
        self._done_evt = done_evt
        self._chunks = chunks

    async def _run(self, output_emitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=_TTS_SAMPLE_RATE,
            num_channels=1, mime_type="audio/pcm", stream=True,
        )
        buf: list[str] = []
        async for item in self._input_ch:
            if isinstance(item, self._FlushSentinel):
                text = "".join(buf).strip()
                buf = []
                if text:
                    await self._synthesize(output_emitter, text)
            else:
                buf.append(item)
        text = "".join(buf).strip()
        if text:  # 末句未 flush 兜底
            await self._synthesize(output_emitter, text)

    async def _synthesize(self, output_emitter, text: str) -> None:
        self._texts.append(text)
        output_emitter.start_segment(segment_id=f"seg{len(self._texts)}")
        chunk = b"\x00\x10" * _TTS_CHUNK_SAMPLES  # 非零小幅度，区别于静音保活帧
        for _ in range(self._chunks):
            output_emitter.push(chunk)
            await asyncio.sleep(0)
        output_emitter.end_segment()
        self._done_evt.set()


class FakeTTS(lk_tts.TTS):
    latest: "FakeTTS | None" = None
    # 每段音频块数（10ms/块）：子类调大以拉长 playout，覆盖 barge-in 打断窗口
    chunks_per_segment: int = _TTS_CHUNKS

    def __init__(self, *, ws_url: str = "", biz_type: str = "", call_id: str = "") -> None:
        super().__init__(capabilities=lk_tts.TTSCapabilities(streaming=True),
                         sample_rate=_TTS_SAMPLE_RATE, num_channels=1)
        self.segments: list[str] = []
        self.segment_done = asyncio.Event()
        FakeTTS.latest = self

    def stream(self, *, conn_options=None, **kwargs):
        return FakeSynthesizeStream(
            tts=self, conn_options=conn_options or APIConnectOptions(),
            texts=self.segments, done_evt=self.segment_done,
            chunks=self.chunks_per_segment)

    def synthesize(self, text, *, conn_options=None, **kwargs):
        raise NotImplementedError("batch 模式未启用（SDK 走 streaming）")

    @property
    def model(self) -> str:
        return "fake"

    @property
    def provider(self) -> str:
        return "test"


class FakeAgentMixin:
    """绕开 LangGraph 管线：按用户输入回显固定文案，其余 TransvoiceAgent 行为保留。"""

    async def _llm_node_impl(self, user_text: str):
        if user_text:
            yield f"回复:{user_text}"


async def _wait_for(predicate, *, timeout: float = 10.0, what: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timeout waiting for {what}")


# ═══════════════════════════════════════════════════════════════════
# 可控 FakeVAD（仿 agents/tests/fake_vad.py 结构，改为显式触发）
# ═══════════════════════════════════════════════════════════════════

class TriggerFakeVAD(LK_VAD):
    """事件序列对齐真实 VAD：START_OF_SPEECH → 周期 INFERENCE_DONE（speech_duration
    递增，≥ interruption.min_duration 命中 audio-activity 打断）→ END_OF_SPEECH
    （带 frames，供 StreamAdapter merge 后调 recognize）。

    每个 stream() 独立流但共享同一 _trigger：轮次端点流（audio_recognition）与
    StreamAdapter 分段流同时收到事件，等价真实 VAD 双流消费同一音频。
    """

    latest: "TriggerFakeVAD | None" = None

    def __init__(self, *, speech_duration: float = 0.6,
                 inference_interval: float = 0.05, **_ignored) -> None:
        super().__init__(capabilities=VADCapabilities(update_interval=inference_interval))
        self._speech_duration = speech_duration
        self._inference_interval = inference_interval
        self._trigger = asyncio.Event()
        self.streams_created = 0
        TriggerFakeVAD.latest = self

    def trigger_speech(self) -> None:
        """测试显式注入一段用户语音（时长 = speech_duration）。"""
        self._trigger.set()

    def stream(self) -> "TriggerFakeVADStream":
        self.streams_created += 1
        return TriggerFakeVADStream(self)


class TriggerFakeVADStream(VADStream):
    def __init__(self, vad: TriggerFakeVAD) -> None:
        super().__init__(vad)

    async def _main_task(self) -> None:
        assert isinstance(self._vad, TriggerFakeVAD)
        # 输入帧不需要（fake 不做真推理），后台排空防 chan 无界增长
        drain = asyncio.create_task(self._drain_input())
        try:
            while True:
                await self._vad._trigger.wait()
                self._vad._trigger.clear()
                start = time.perf_counter()
                self._send(VADEventType.START_OF_SPEECH, speech_duration=0.0)
                while True:
                    await asyncio.sleep(self._vad._inference_interval)
                    elapsed = time.perf_counter() - start
                    if elapsed >= self._vad._speech_duration:
                        break
                    self._send(VADEventType.INFERENCE_DONE, speech_duration=elapsed,
                               speaking=True, probability=0.99)
                self._send(VADEventType.END_OF_SPEECH,
                           speech_duration=self._vad._speech_duration,
                           silence_duration=0.05, frames=[_VAD_FRAME])
        finally:
            drain.cancel()

    async def _drain_input(self) -> None:
        async for _ in self._input_ch:
            pass

    def _send(self, type: VADEventType, *, speech_duration: float,
              silence_duration: float = 0.0, speaking: bool = False,
              probability: float = 0.0, frames: list | None = None) -> None:
        now = time.perf_counter()
        self._event_ch.send_nowait(VADEvent(
            type=type, samples_index=0, timestamp=now,
            speech_duration=speech_duration, silence_duration=silence_duration,
            probability=probability, speaking=speaking,
            raw_accumulated_speech=speech_duration,
            raw_accumulated_silence=silence_duration,
            frames=frames or [],
        ))


@pytest.mark.asyncio
async def test_session_processes_two_turns(monkeypatch):
    """两段 VAD 切分语音 → 两段 TTS 文本 + 两段非静音音频下行；aclose 不挂死。

    R1 守护（design.md）：turn_detection="vad" 下轮次提交必须等已就绪 final——
    FakeAgent 仅在收到非空完整转写时回显，FakeTTS 段内容断言即「用户轮次带完整
    转写提交」的等价判据。
    """
    import voice.session as voice_session
    from voice.agent import CallContext, TransvoiceAgent
    from ws.registry import ActiveCallRegistry

    class FakeAgent(FakeAgentMixin, TransvoiceAgent):
        pass

    monkeypatch.setattr(voice_session, "TransvoiceSTT", FakeBatchSTT)
    monkeypatch.setattr(voice_session, "TransvoiceTTS", FakeTTS)
    monkeypatch.setattr(voice_session, "TransvoiceAgent", FakeAgent)

    FakeBatchSTT.latest = None
    FakeTTS.latest = None
    TriggerFakeVAD.latest = None

    ctx = CallContext(
        call_id="itest", biz_type="collection", user_key="u1",
        tenant_id="default", scenario="default",
        call_task_vars={}, handoff_extension="1001",
    )
    sent: list[bytes] = []

    async def send_bytes(frame: bytes) -> None:
        sent.append(frame)

    session, agent = voice_session.build_agent_session(
        ctx=ctx, websocket=SimpleNamespace(send_bytes=send_bytes),
        registry=ActiveCallRegistry(), esl=None,
        vad=TriggerFakeVAD(),
    )
    audio_input = session.input.audio
    await session.start(agent)

    # 背景推上行静音帧：驱动真实 jitter→denoise→gain 链
    async def push_loop() -> None:
        while True:
            audio_input.push_bytes(b"\x00" * FRAME_BYTES)
            await asyncio.sleep(0.03)

    pusher = asyncio.create_task(push_loop())
    try:
        # 双 VAD 流就位（audio_recognition 轮次端点 + StreamAdapter 分段）
        await _wait_for(lambda: TriggerFakeVAD.latest.streams_created >= 2,
                        what="VAD streams creation (turn detection + StreamAdapter)")

        FakeBatchSTT.latest.texts.append("你好")
        TriggerFakeVAD.latest.trigger_speech()
        await _wait_for(lambda: len(FakeTTS.latest.segments) >= 1,
                        what="turn-1 reply synthesized")
        await asyncio.sleep(0.8)  # 覆盖 0.24s 匀速 playout，段关闭后再开下一轮

        FakeBatchSTT.latest.texts.append("在吗")
        TriggerFakeVAD.latest.trigger_speech()
        await _wait_for(lambda: len(FakeTTS.latest.segments) >= 2,
                        what="turn-2 reply synthesized")
        await asyncio.sleep(0.8)
    finally:
        pusher.cancel()
        audio_input.close()
        # aclose 不挂死（playback_finished 契约成立）
        await asyncio.wait_for(session.aclose(), timeout=10.0)

    assert FakeTTS.latest.segments == ["回复:你好", "回复:在吗"]

    # StreamAdapter 把 VAD 段 frames 传给了 recognize（每段 960B 非空音频）
    assert FakeBatchSTT.latest.recv_bytes, "recognize should receive VAD frames"
    assert all(n > 0 for n in FakeBatchSTT.latest.recv_bytes)

    non_silent = [f for f in sent if any(f)]
    assert len(non_silent) >= 8, (
        f"expected >=8 non-silent output frames, got {len(non_silent)}")

    history_text = " ".join(
        item.text_content or "" for item in session.history.items
        if isinstance(item, lk_llm.ChatMessage))
    assert "回复:你好" in history_text
    assert "回复:在吗" in history_text


@pytest.mark.asyncio
async def test_barge_in_interrupts_playback_and_next_turn_proceeds(monkeypatch):
    """TTS 播放中用户说话（fake VAD）→ 播放暂停 → VAD 段 final 化为真打断
    （playback_finished(interrupted=True) + barge_in 落库 R2）→ 下一轮正常推进
    + aclose 不挂死。

    SDK 1.8.3 SOS-pause 语义（output.can_pause=True + resume_false_interruption）：
    ① VAD INFERENCE_DONE(speech_duration≥min_duration) 命中 → audio_output.pause()
    ② 段 FINAL transcript 到达（StreamAdapter recognize 完成即发）→
    _cancel_speech_pause(interrupt=True) → clear_buffer → interrupted 回报。
    """
    import voice.agent as voice_agent
    import voice.session as voice_session
    from voice.agent import CallContext, TransvoiceAgent
    from ws.registry import ActiveCallRegistry

    class FakeAgent(FakeAgentMixin, TransvoiceAgent):
        pass

    class LongPlayoutTTS(FakeTTS):
        chunks_per_segment = 100  # ~1.0s playout，保证打断触发时仍在播放

    monkeypatch.setattr(voice_session, "TransvoiceSTT", FakeBatchSTT)
    monkeypatch.setattr(voice_session, "TransvoiceTTS", LongPlayoutTTS)
    monkeypatch.setattr(voice_session, "TransvoiceAgent", FakeAgent)
    # 缩短打断门限（默认 0.5s），让暂停稳定落在 1.0s playout 窗口内
    monkeypatch.setattr(voice_session.settings, "interruption_min_duration", 0.15)

    # barge_in 落库走 fire-and-forget PG 写——替身为记录器（隔离 DB + 供断言）
    barge_in_events: list[dict] = []

    def record_event(**kwargs):
        if kwargs.get("event_type") == "barge_in":
            barge_in_events.append(kwargs)
    monkeypatch.setattr(voice_agent, "fire_insert_event", record_event)

    FakeBatchSTT.latest = None
    FakeTTS.latest = None
    TriggerFakeVAD.latest = None

    ctx = CallContext(
        call_id="itest-bargein", biz_type="collection", user_key="u1",
        tenant_id="default", scenario="default",
        call_task_vars={}, handoff_extension="1001",
    )

    async def send_bytes(frame: bytes) -> None:
        pass

    session, agent = voice_session.build_agent_session(
        ctx=ctx, websocket=SimpleNamespace(send_bytes=send_bytes),
        registry=ActiveCallRegistry(), esl=None,
        vad=TriggerFakeVAD(),
    )
    audio_input = session.input.audio
    playback_started: list = []
    playback_finished: list = []
    session.output.audio.on("playback_started", playback_started.append)
    session.output.audio.on("playback_finished", playback_finished.append)
    await session.start(agent)

    async def push_loop() -> None:
        while True:
            audio_input.push_bytes(b"\x00" * FRAME_BYTES)
            await asyncio.sleep(0.03)

    pusher = asyncio.create_task(push_loop())
    try:
        await _wait_for(lambda: TriggerFakeVAD.latest.streams_created >= 2,
                        what="VAD streams creation (turn detection + StreamAdapter)")

        # 轮次一：队列文本后触发语音（EOS → recognize → FINAL → 轮次提交 → 回复播放）
        FakeBatchSTT.latest.texts.append("第一句")
        TriggerFakeVAD.latest.trigger_speech()
        await _wait_for(lambda: len(FakeTTS.latest.segments) >= 1,
                        what="turn-1 reply synthesized")
        await _wait_for(lambda: len(playback_started) >= 1,
                        what="turn-1 playback started")

        # 用户开始说话（0.6s > 门限 0.15s）→ SOS 暂停；段 FINAL 随 EOS 到达升格打断
        FakeBatchSTT.latest.texts.append("第二句")
        TriggerFakeVAD.latest.trigger_speech()
        await _wait_for(
            lambda: session.agent_state == "listening" and not playback_finished,
            what="playback paused by user speech (SOS pause)")
        await _wait_for(
            lambda: any(ev.interrupted for ev in playback_finished),
            what="playback_finished(interrupted=True)")
        await _wait_for(lambda: len(FakeTTS.latest.segments) >= 2,
                        what="turn-2 reply synthesized after barge-in")
    finally:
        pusher.cancel()
        audio_input.close()
        await asyncio.wait_for(session.aclose(), timeout=10.0)

    assert FakeTTS.latest.segments[0] == "回复:第一句"
    assert FakeTTS.latest.segments[1] == "回复:第二句"
    interrupted_positions = [ev.playback_position for ev in playback_finished
                             if ev.interrupted]
    assert interrupted_positions, "应存在 interrupted=True 的 playback_finished"
    assert interrupted_positions[0] < 0.9
    assert barge_in_events, "interrupted assistant 消息应触发 barge_in 事件（R2）"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/voice/test_session_integration.py -v`
Expected: FAIL（`TypeError: build_agent_session() got an unexpected keyword argument 'vad'`）

- [ ] **Step 3: 修改 session.py**

三处 Edit。① `_turn_handling_options` 的 turn_detection 块：

```python
def _turn_handling_options() -> dict:
    return {
        # FSMN 服务端分段主导轮次提交，STT final 即终点（design.md §2.2）
        "turn_detection": "stt",
```

替换为：

```python
def _turn_handling_options() -> dict:
    return {
        # silero VAD 判定轮次起止（design.md D2）；转写经 StreamAdapter 包装的
        # 非流式 STT 供给，轮次提交等待已就绪 final
        "turn_detection": "vad",
```

② 函数签名加 `vad=None`：

```python
def build_agent_session(
    *,
    ctx: CallContext,
    websocket: WebSocket,
    registry,
    esl,
    apm=None,
    denoiser=None,
) -> tuple[AgentSession, TransvoiceAgent]:
```

替换为：

```python
def build_agent_session(
    *,
    ctx: CallContext,
    websocket: WebSocket,
    registry,
    esl,
    apm=None,
    denoiser=None,
    vad=None,
) -> tuple[AgentSession, TransvoiceAgent]:
```

③ AgentSession 的 vad 实参：

```python
        vad=inference.VAD(),  # 本地 silero，仅辅助打断（design.md §2.2）
```

替换为：

```python
        # 不手动包 StreamAdapter：默认 stt_node 对 streaming=False 的 STT 自动以
        # session vad 包装；min_silence_duration 承接退役 FSMN-VAD 的尾静音端点角色
        vad=vad or inference.VAD(
            min_silence_duration=settings.vad_min_silence_duration),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/voice/test_session_integration.py -v`
Expected: 2 passed（约 10-20s）

**若 `test_session_processes_two_turns` 超时（segments 不增长）**：即 R1 竞态实测命中——轮次在 final 前提交（转写空）。执行回退：把 Step 3-① 的 `"vad"` 改回 `"stt"`（StreamAdapter 的 EOS 同样源自 VAD，语义等价），重跑本测试必须通过；在「偏差记录」节追加回退原因与现象，继续后续步骤。

- [ ] **Step 5: 跑 voice 全量确认无回归**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/voice/ -v`
Expected: 全部 passed（test_agent.py 不依赖 STT 形态，应不受影响；若因 STT 形态失败则按同样 fake 化原则适配并在偏差记录说明）

- [ ] **Step 6: Commit**

```bash
git add agent-flow/src/voice/session.py agent-flow/tests/voice/test_session_integration.py
git commit -m "refactor(livekit): turn_detection 切 vad——非流式 STT 走 StreamAdapter 自动切段"
```

---

### Task 5: 文档同步

**Files:**
- Modify: `CLAUDE.md`（根目录）
- Modify: `agent-asr/README.md`、`agent-flow/README.md`

**Interfaces:**
- Consumes: Task 1-4 的最终行为

- [ ] **Step 1: 更新根 CLAUDE.md**

逐处替换（保持中英混排风格）：

1. Project Overview 首段：`FSMN-VAD server-side endpoint detection (agent-asr)` → `silero-VAD turn endpointing via livekit-agents StreamAdapter (agent-flow)`。
2. Architecture 数据流块：`识别: TransvoiceSTT 插件 → agent-asr WS（FSMN-VAD 服务端分段多 final）→ STT FINAL/EOS 事件（turn_detection="stt" 提交轮次）` → `识别: TransvoiceSTT 非流式插件（StreamAdapter + silero VAD 切段）→ agent-asr WS 整段识别 → STT FINAL（turn_detection="vad" 提交轮次）`。
3. 数据流块打断行 `打断: silero VAD（inference.VAD 本地）+ interruption.min_duration 检测，SDK 中断体系清空输出缓冲(clear_buffer) → 新一轮对话（PG call_event 记 barge_in）` 保留不动（本就正确）。
4. Five Components `agent-asr` 段：`WS /ws/asr/streaming-recognize via ws_server.py (FSMN-VAD 流式分段 → 段级 recognize → 多 final 主动推)` → `WS /ws/asr/streaming-recognize via ws_server.py (无状态整段识别: config → binary 累积 → end → 单 result；切段职责在 agent-flow)`。
5. Five Components `agent-flow` 段：`ASR 经 FSMN-VAD 分段后回推 final → STT EOS 驱动轮次；TTS flush 边界聚合整句合成` → `ASR 整段识别（StreamAdapter + silero VAD 切段，每段一连接）驱动 FINAL；轮次端点 turn_detection="vad"；TTS flush 边界聚合整句合成`；同段 `轮次提交 turn_detection="stt"` → `轮次提交 turn_detection="vad"`。
6. Key Modules 表 `src/voice/stt_plugin.py` 行 → `TransvoiceSTT 插件 — 非流式批量 recognize（agent-asr WS 整段识别，每段一连接），SDK 默认 stt_node 自动包 StreamAdapter 切段`。
7. Project Structure：删除 `│   │   ├── vad_segmenter.py  # FSMN-VAD 流式分段层` 行；`ws_server.py    # WebSocket ASR service (FSMN-VAD 分段 + 多 final)` → `ws_server.py    # WebSocket ASR service (无状态整段识别)`。
8. Configuration 节：`**VAD 端点检测/打断**` 条目 → `**VAD 端点检测/打断**: 轮次端点由 agent-flow 本地 silero VAD（`inference.VAD`，`turn_detection="vad"`）+ StreamAdapter 非流式 STT 切段驱动；打断检测同一 VAD（`interruption.min_duration`）`；紧跟新增一行 `**VAD 静音阈值**: `CALLBOT_VAD_MIN_SILENCE_DURATION` (default 0.25, silero 判定语音结束的静音时长——原 agent-asr FSMN-VAD 尾静音角色承接)`。
9. Infrastructure `**WebSocket**` 条目：`ASR 经 FSMN-VAD 分段 + 多 final 映射为 STT 事件驱动 AgentSession 轮次（turn_detection="stt"）` → `ASR 整段识别（客户端 VAD 切段，StreamAdapter 映射 STT 事件）驱动 AgentSession 轮次（turn_detection="vad"）`。

- [ ] **Step 2: 更新 agent-asr/README.md**

`grep -n "FSMN\|VAD\|分段\|多 final\|reset" agent-asr/README.md` 定位全部段落，替换协议描述为：

```markdown
## WebSocket 整段识别协议（/ws/asr/streaming-recognize）

无状态服务：切段职责在客户端（agent-flow silero VAD + StreamAdapter），
服务端只做「config → binary 累积（逐帧重采样到 16kHz）→ end → 整段识别 →
单 result → 关连接」。

- 发送: `{"type":"config","call_id":...,"language":"zh","sample_rate":16000}`
  → 若干 binary PCM 帧 → `{"type":"end"}`
- 接收: `{"type":"result","text":...,"confidence":...,"is_final":true}`（单条）
  或 `{"type":"error","message":...}`
- 无 VAD 模型、无 reset 消息、无服务端语音状态机
```

- [ ] **Step 3: 更新 agent-flow/README.md**

`grep -n "FSMN\|turn_detection\|stt\|多 final" agent-flow/README.md` 定位，将 STT 插件与轮次描述替换为：TransvoiceSTT 非流式批量（每语音段一条 WS 连接），SDK 默认 stt_node 自动以 session silero VAD 包 StreamAdapter 切段；`turn_detection="vad"`，`CALLBOT_VAD_MIN_SILENCE_DURATION` 控制端点静音阈值。

- [ ] **Step 4: grep 终检**

Run: `grep -rni "fsmn" CLAUDE.md agent-asr/README.md agent-flow/README.md agent-asr/asradapter agent-flow/src agent-asr/tests agent-flow/tests`
Expected: 无输出

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md agent-asr/README.md agent-flow/README.md
git commit -m "docs: 同步 FSMN-VAD 退役——StreamAdapter 切段 + vad 轮次检测 + 新配置"
```

---

### Task 6: 全量回归

**Files:** 无（纯验证）

- [ ] **Step 1: agent-asr 全量**

Run: `cd agent-asr && PYTHONPATH=$(pwd) $PY -m pytest tests/ -v`
Expected: 3 passed

- [ ] **Step 2: agent-flow 全量**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src $PY -m pytest tests/ -v`
Expected: ≥ 104 passed（基线 102 - 删 3 旧 stt 用例 + 5 新 stt 用例 + 1 新 config 用例 = 105；以实际计数为准，不得有 failed）

- [ ] **Step 3: 启动冒烟（可选——本机无 GPU/FS 运行环境则跳过并在收尾说明）**

```bash
./scripts/local.sh asr   # 启动日志确认无 "FSMN-VAD model loaded"
```

- [ ] **Step 4: 若 Step 1/2 有失败**：修复后重跑；修复属实现偏差时记入「偏差记录」节（不回改 openspec 规格文档）。

---

## Self-Review 结论

- **Spec 覆盖**：spec 三条 Requirement（TransvoiceSTT 非流式 / vad 轮次配置 / agent-asr 无状态服务）→ Task 3+4、Task 2+4、Task 1；10 个 Scenario 全部有对应测试断言（整段识别/多帧累积/8k resample/无 VAD 资产→Task 1；VAD 切段/短文本/断连/分帧→Task 3；barge-in/轮次等 final/端点可调→Task 2+4）。文档同步（proposal What-Changes #6）→ Task 5。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`ASRWebSocketHandler(engine)` 单参构造在 Task 1 定义、Task 1 测试消费；`settings.vad_min_silence_duration` Task 2 定义、Task 4 消费；`build_agent_session(vad=...)` Task 4 Step 3 定义、Step 1 测试消费；`FakeBatchSTT.capabilities(streaming=False)` 与 plan 头部事实 ① 的自动包装前提一致。
