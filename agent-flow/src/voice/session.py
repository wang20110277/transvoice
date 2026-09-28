"""build_agent_session —— AgentSession per-call 装配（design.md §2.2 决策落点）。

Task 8 的 main.py 消费：`session, agent = build_agent_session(...)`
→ `await session.start(agent)`。
"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket
from livekit.agents import AgentSession, inference, llm

from config import settings
from voice.agent import CallContext, TransvoiceAgent
from voice.io import TelephonyAudioInput, TelephonyAudioOutput
from voice.stt_plugin import TransvoiceSTT
from voice.tts_plugin import TransvoiceTTS
from ws.jitter_buffer import JitterBuffer


class _PipelineLLM(llm.LLM):
    """占位 LLM：推理由 TransvoiceAgent.llm_node（LangGraph 管线）全覆盖。

    SDK 门禁要求 session llm 非 None 才会调度回复（agent_activity
    `_user_turn_completed_impl` 对 llm is None 直接 return，且不报错），
    默认 llm_node 又被覆盖，chat() 永不被调用。
    """

    def chat(self, *, chat_ctx, tools=None, conn_options=None, **kwargs):
        raise NotImplementedError("llm_node 已覆盖默认推理，chat 不应被调用")

    @property
    def model(self) -> str:
        return "transvoice-pipeline"

    @property
    def provider(self) -> str:
        return "transvoice"


def _turn_handling_options() -> dict:
    return {
        # FSMN 服务端分段主导轮次提交，STT final 即终点（design.md §2.2）
        "turn_detection": "stt",
        "endpointing": {
            "mode": "fixed",
            "min_delay": settings.endpointing_min_delay,
            "max_delay": settings.endpointing_max_delay,
        },
        "interruption": {
            "min_duration": settings.interruption_min_duration,
            "resume_false_interruption": True,
        },
    }


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
        llm=_PipelineLLM(),  # SDK 回复调度门禁需要非 None；真实推理在 llm_node
        vad=inference.VAD(),  # 本地 silero，仅辅助打断（design.md §2.2）
        turn_handling=_turn_handling_options(),
        aec_warmup_duration=None,  # 关键：默认 3s 会屏蔽首轮 barge-in
        userdata={"call_ctx": ctx},
        loop=asyncio.get_running_loop(),
    )
    session.input.audio = audio_input
    session.output.audio = output
    session.on("conversation_item_added", agent.on_conversation_item_added)
    return session, agent
