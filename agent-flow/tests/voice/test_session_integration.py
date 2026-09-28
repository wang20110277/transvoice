"""无头集成：真实 build_agent_session 装配的 AgentSession 全链路两轮对话。

仿 agents/tests/fake_session.py + fake_io.py + fake_stt.py + fake_tts.py 的结构
（测试内自含 Fake 类）：monkeypatch voice.session 命名空间里的
TransvoiceSTT/TransvoiceTTS/TransvoiceAgent，绕开真实 agent-asr/agent-tts WS
与 LangGraph 管线，其余全部真实——AgentSession 默认对话循环、turn_handling
（stt 轮次提交）、真实 inference.VAD()（本地 silero）、TelephonyAudioInput
（jitter→denoise→gain 链）、TelephonyAudioOutput（30ms 匀速 + playback_finished
契约）、事件接线均被覆盖。

Task 4 观察项（STT flush 重连）在此不适用：ScriptedStream 永不主动结束，
flush 哨兵仅被消费，不存在服务端关连接路径。
"""
import asyncio
from types import SimpleNamespace

import pytest

from livekit.agents import APIConnectOptions
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents import utils

from ws.jitter_buffer import FRAME_BYTES

_TTS_SAMPLE_RATE = 22050  # 与 TransvoiceTTS 一致，走真实 22050→16000 重采样路径
# 每段合成 0.24s 非零音频（12 × 10ms），非零字节用于与静音保活帧区分
_TTS_CHUNKS = 12
_TTS_CHUNK_SAMPLES = _TTS_SAMPLE_RATE // 100


class ScriptedRecognizeStream(lk_stt.RecognizeStream):
    def __init__(self, *, stt, conn_options):
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=16000)

    def push_turn(self, text: str) -> None:
        rid = utils.shortuuid()
        self._event_ch.send_nowait(lk_stt.SpeechEvent(
            type=lk_stt.SpeechEventType.START_OF_SPEECH, request_id=rid))
        self._event_ch.send_nowait(lk_stt.SpeechEvent(
            type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT, request_id=rid,
            alternatives=[lk_stt.SpeechData(language="zh", text=text)]))
        # 铁律：FINAL 之后才能 EOS（turn_detection="stt" 由 EOS 驱动轮次提交）
        self._event_ch.send_nowait(lk_stt.SpeechEvent(
            type=lk_stt.SpeechEventType.END_OF_SPEECH, request_id=rid))

    async def _run(self) -> None:
        # 仅消费输入通道（含 flush 哨兵），事件由测试经 push_turn 外部注入
        async for _ in self._input_ch:
            pass


class ScriptedSTT(lk_stt.STT):
    """build_agent_session 经 ws_url 参数构造；latest 暴露实例给测试驱动。"""

    latest: "ScriptedSTT | None" = None

    def __init__(self, *, ws_url: str = "", min_final_len: int = 2) -> None:
        super().__init__(capabilities=lk_stt.STTCapabilities(
            streaming=True, interim_results=False))
        self._stream: ScriptedRecognizeStream | None = None
        ScriptedSTT.latest = self

    def stream(self, *, language=None, conn_options=None, **kwargs):
        self._stream = ScriptedRecognizeStream(
            stt=self, conn_options=conn_options or APIConnectOptions())
        return self._stream

    async def _recognize_impl(self, buffer, *, language, conn_options):
        raise NotImplementedError("batch 模式未启用")

    @property
    def model(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "test"

    def push_turn(self, text: str) -> None:
        assert self._stream is not None, "STT stream not created yet"
        self._stream.push_turn(text)


class FakeSynthesizeStream(lk_tts.SynthesizeStream):
    def __init__(self, *, tts, conn_options, texts: list, done_evt: asyncio.Event):
        super().__init__(tts=tts, conn_options=conn_options)
        self._texts = texts
        self._done_evt = done_evt

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
        for _ in range(_TTS_CHUNKS):
            output_emitter.push(chunk)
            await asyncio.sleep(0)
        output_emitter.end_segment()
        self._done_evt.set()


class FakeTTS(lk_tts.TTS):
    latest: "FakeTTS | None" = None

    def __init__(self, *, ws_url: str = "", biz_type: str = "", call_id: str = "") -> None:
        super().__init__(capabilities=lk_tts.TTSCapabilities(streaming=True),
                         sample_rate=_TTS_SAMPLE_RATE, num_channels=1)
        self.segments: list[str] = []
        self.segment_done = asyncio.Event()
        FakeTTS.latest = self

    def stream(self, *, conn_options=None, **kwargs):
        return FakeSynthesizeStream(
            tts=self, conn_options=conn_options or APIConnectOptions(),
            texts=self.segments, done_evt=self.segment_done)

    def synthesize(self, text, *, conn_options=None, **kwargs):
        raise NotImplementedError("batch 模式未启用（SDK 走 streaming）")

    @property
    def model(self) -> str:
        return "fake"

    @property
    def provider(self) -> str:
        return "test"


class FakeAgentMixin:
    """绕开 LangGraph 管线：按用户输入回固定文案，其余 TransvoiceAgent 行为保留。"""

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


@pytest.mark.asyncio
async def test_session_processes_two_turns(monkeypatch):
    """两轮用户输入 → 两段 TTS 文本 + 两段非静音音频下行；aclose 不挂死。"""
    import voice.session as voice_session
    from voice.agent import CallContext, TransvoiceAgent
    from ws.registry import ActiveCallRegistry

    class FakeAgent(FakeAgentMixin, TransvoiceAgent):
        pass

    monkeypatch.setattr(voice_session, "TransvoiceSTT", ScriptedSTT)
    monkeypatch.setattr(voice_session, "TransvoiceTTS", FakeTTS)
    monkeypatch.setattr(voice_session, "TransvoiceAgent", FakeAgent)

    ScriptedSTT.latest = None
    FakeTTS.latest = None

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
    )
    audio_input = session.input.audio
    await session.start(agent)

    # 背景推上行静音帧：驱动真实 jitter→denoise→gain→VAD 链
    async def push_loop() -> None:
        while True:
            audio_input.push_bytes(b"\x00" * FRAME_BYTES)
            await asyncio.sleep(0.03)

    pusher = asyncio.create_task(push_loop())
    try:
        await _wait_for(
            lambda: ScriptedSTT.latest is not None
            and ScriptedSTT.latest._stream is not None,
            what="STT stream creation")

        ScriptedSTT.latest.push_turn("你好")
        await _wait_for(lambda: len(FakeTTS.latest.segments) >= 1,
                        what="turn-1 reply synthesized")
        await asyncio.sleep(0.8)  # 覆盖 0.24s 匀速 playout，确保段关闭后再开下一轮

        ScriptedSTT.latest.push_turn("在吗")
        await _wait_for(lambda: len(FakeTTS.latest.segments) >= 2,
                        what="turn-2 reply synthesized")
        await asyncio.sleep(0.8)
    finally:
        pusher.cancel()
        audio_input.close()
        # Task 3 约束验证点：aclose 不挂死（playback_finished 契约成立）
        await asyncio.wait_for(session.aclose(), timeout=10.0)

    assert FakeTTS.latest.segments == ["回复:你好", "回复:在吗"]

    non_silent = [f for f in sent if any(f)]
    # 两轮 × 0.24s ≈ 16 帧（30ms/帧）；放宽下限容忍重采样切帧差异
    assert len(non_silent) >= 8, (
        f"expected >=8 non-silent output frames, got {len(non_silent)}")

    history_text = " ".join(
        item.text_content or "" for item in session.history.items
        if isinstance(item, lk_llm.ChatMessage))
    assert "回复:你好" in history_text
    assert "回复:在吗" in history_text
