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
