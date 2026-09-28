"""ws_media_fork 拆除路径回归（终审 Critical：每通话 TTS 共享 WS + reader 泄漏）。

直接 await 路由函数（同 test_archive_recording.py 做法，避开 lifespan 重依赖）：
monkeypatch `src.voice.session.build_agent_session`（main.py 函数内 import 的模块
身份），session.tts 挂真 TransvoiceTTS 并记录 aclose 调用——断言拆除路径显式关
插件。另覆盖 start 失败路径（Minor：start 原在 try 之外，失败会跳过全部清理）。
"""
import json
from types import SimpleNamespace

import pytest

import main
from voice.tts_plugin import TransvoiceTTS


class FakeWebSocket:
    def __init__(self, frames: list):
        self._frames = list(frames)
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        if self._frames:
            return self._frames.pop(0)
        raise _Disconnect()

    async def send_bytes(self, data: bytes) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class _Disconnect(Exception):
    pass


def _install_fake_session(monkeypatch, *, start_exc: Exception | None = None):
    """替换 build_agent_session：返回携带真 TransvoiceTTS 的假 session，记录拆除动作。"""
    tts = TransvoiceTTS(ws_url="ws://unused", biz_type="collection", call_id="c1")
    calls = {"tts_aclose": 0, "session_aclose": 0, "audio_input_close": 0}
    orig_aclose = tts.aclose

    async def recording_aclose():
        calls["tts_aclose"] += 1
        await orig_aclose()

    monkeypatch.setattr(tts, "aclose", recording_aclose)

    class FakeAudioInput:
        def close(self) -> None:
            calls["audio_input_close"] += 1

    class FakeSession:
        def __init__(self):
            self.input = SimpleNamespace(audio=FakeAudioInput())
            self.tts = tts

        async def start(self, agent) -> None:
            if start_exc is not None:
                raise start_exc

        async def aclose(self) -> None:
            calls["session_aclose"] += 1

    def fake_build(**kwargs):
        return FakeSession(), SimpleNamespace()

    import src.voice.session as src_voice_session
    monkeypatch.setattr(src_voice_session, "build_agent_session", fake_build)
    monkeypatch.setattr(main, "_initialized", True)
    return calls


@pytest.mark.asyncio
async def test_teardown_closes_tts_plugin(monkeypatch):
    """WS stop → 拆除：session.aclose 后必须显式关 TTS 插件（aclose 共享 WS/reader）。"""
    calls = _install_fake_session(monkeypatch)
    ws = FakeWebSocket(frames=[
        {"type": "websocket.receive", "text": json.dumps({"type": "stop"})},
    ])

    await main.ws_media_fork(ws, "leak-check")

    assert ws.accepted and ws.closed
    assert calls["audio_input_close"] == 1
    assert calls["session_aclose"] == 1
    assert calls["tts_aclose"] == 1, (
        "拆除路径必须调用 session.tts 的 aclose——否则每通话泄漏一条 agent-tts "
        "WS + 阻塞在 recv() 的 reader task")


@pytest.mark.asyncio
async def test_start_failure_still_cleans_up(monkeypatch):
    """session.start 抛异常（start 在 try 内）→ finally 清理仍完整执行。"""
    calls = _install_fake_session(monkeypatch, start_exc=ValueError("start boom"))
    ws = FakeWebSocket(frames=[])

    with pytest.raises(ValueError, match="start boom"):
        await main.ws_media_fork(ws, "start-fail")

    assert calls["audio_input_close"] == 1
    assert calls["session_aclose"] == 1
    assert calls["tts_aclose"] == 1
    assert ws.closed
