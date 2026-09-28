"""TransvoiceAgent：llm_node 流式 yield + 终端动作时序（playout 排空后执行）。

R2 裁定：barge-in 判定基于 SDK 真实事件结构 —— ConversationItemAddedEvent.item
为 ChatMessage，assistant 消息在 speech 被打断时带 interrupted=True。
"""
import asyncio
from types import SimpleNamespace

import pytest


def _make_agent(**ctx_overrides):
    from voice.agent import CallContext, TransvoiceAgent

    ctx = CallContext(
        call_id="c1", biz_type="collection", user_key="u1",
        tenant_id="default", scenario="default",
        call_task_vars={}, handoff_extension="1001",
        **ctx_overrides,
    )
    return TransvoiceAgent(
        instructions="fallback", ctx=ctx, registry=None, esl=None,
    )


def _async_ret(value):
    async def _r():
        return value
    return _r()


@pytest.mark.asyncio
async def test_execute_terminal_action_end(monkeypatch):
    from voice.agent import execute_terminal_action

    seq = []
    events = []

    async def fake_hangup(cid):
        seq.append(("hangup", cid))

    async def fake_transfer(cid, ext):
        seq.append(("transfer", cid, ext))

    esl = SimpleNamespace(hangup=fake_hangup, transfer=fake_transfer)
    monkeypatch.setattr("voice.agent.fire_insert_event",
                        lambda **kw: (seq.append(("event", kw["event_type"])),
                                      events.append(kw)))
    registry = SimpleNamespace(get=lambda cid: None)

    await execute_terminal_action(esl, registry, "end", "c1", "1001")

    # 关键事件先行，ESL 动作随后
    assert seq == [("event", "hangup_by_bot"), ("hangup", "c1")]
    assert len(events) == 1
    assert events[0]["call_id"] == "c1"


@pytest.mark.asyncio
async def test_execute_terminal_action_handoff(monkeypatch):
    from voice.agent import execute_terminal_action

    calls = []
    events = []

    async def fake_hangup(cid):
        calls.append(("hangup", cid))

    async def fake_transfer(cid, ext):
        calls.append(("transfer", cid, ext))

    esl = SimpleNamespace(hangup=fake_hangup, transfer=fake_transfer)
    monkeypatch.setattr("voice.agent.fire_insert_event",
                        lambda **kw: events.append(kw))
    registry = SimpleNamespace(get=lambda cid: None)

    await execute_terminal_action(esl, registry, "handoff", "c1", "1001")

    assert calls == [("transfer", "c1", "1001")]
    assert events[0]["event_type"] == "handoff"
    assert events[0]["payload"] == {"extension": "1001"}


@pytest.mark.asyncio
async def test_execute_terminal_action_without_esl(monkeypatch):
    from voice.agent import execute_terminal_action

    events = []
    monkeypatch.setattr("voice.agent.fire_insert_event",
                        lambda **kw: events.append(kw))
    # ESL 缺失：静默告警返回，不落事件、不抛异常
    await execute_terminal_action(None, None, "end", "c1", "1001")
    assert events == []


@pytest.mark.asyncio
async def test_llm_node_yields_tokens_and_defers_action(monkeypatch):
    agent = _make_agent()

    async def fake_pre_llm(*a, **kw):
        return {"user_input": "在吗", "biz_type": "collection", "call_id": "c1"}

    async def fake_astream(state, on_action=None):
        await on_action("end")
        yield "再见"
        yield "。"

    monkeypatch.setattr("voice.agent.run_pre_llm_phase", fake_pre_llm)
    monkeypatch.setattr("voice.agent.astream_reply_text", fake_astream)

    agent._test_session = SimpleNamespace(wait_for_playout=_async_ret(None))

    tokens = [t async for t in agent._llm_node_impl("在吗")]
    assert tokens == ["再见", "。"]
    await asyncio.sleep(0.05)  # spawn 的 post-playout 任务
    assert agent._executed_actions == ["end"]  # playout 排空后执行


@pytest.mark.asyncio
async def test_llm_node_skips_empty_input(monkeypatch):
    agent = _make_agent()

    async def fake_pre_llm(*a, **kw):
        return {"user_input": "  ", "biz_type": "collection", "call_id": "c1"}

    monkeypatch.setattr("voice.agent.run_pre_llm_phase", fake_pre_llm)

    tokens = [t async for t in agent._llm_node_impl("  ")]
    assert tokens == []
    assert agent._executed_actions == []


def test_barge_in_fires_only_for_interrupted_assistant_message(monkeypatch):
    from livekit.agents import llm as lk_llm

    events = []
    monkeypatch.setattr("voice.agent.fire_insert_event",
                        lambda **kw: events.append(kw))
    agent = _make_agent()

    agent.on_conversation_item_added(SimpleNamespace(
        item=lk_llm.ChatMessage(role="assistant", content=["再"], interrupted=True)))
    agent.on_conversation_item_added(SimpleNamespace(
        item=lk_llm.ChatMessage(role="assistant", content=["完整播完"], interrupted=False)))
    agent.on_conversation_item_added(SimpleNamespace(
        item=lk_llm.ChatMessage(role="user", content=["喂"], interrupted=True)))

    assert len(events) == 1
    assert events[0]["event_type"] == "barge_in"
    assert events[0]["call_id"] == "c1"


def test_turn_handling_options_keys_match_sdk():
    from livekit.agents.voice.turn import (
        EndpointingOptions,
        InterruptionOptions,
        TurnHandlingOptions,
    )

    from voice.session import _turn_handling_options

    th = _turn_handling_options()
    assert set(th) <= set(TurnHandlingOptions.__annotations__)
    assert set(th["endpointing"]) <= set(EndpointingOptions.__annotations__)
    assert set(th["interruption"]) <= set(InterruptionOptions.__annotations__)
    assert th["turn_detection"] == "stt"
    assert th["endpointing"]["mode"] == "fixed"


@pytest.mark.asyncio
async def test_build_agent_session_assembly():
    from voice.agent import CallContext
    from voice.io import TelephonyAudioInput, TelephonyAudioOutput
    from voice.session import build_agent_session

    async def send_bytes(frame):
        return None

    ctx = CallContext(call_id="c1", biz_type="collection", user_key="u1",
                      tenant_id="default", scenario="default",
                      call_task_vars={}, handoff_extension="1001")
    session, agent = build_agent_session(
        ctx=ctx, websocket=SimpleNamespace(send_bytes=send_bytes),
        registry=None, esl=None,
    )
    try:
        assert isinstance(session.output.audio, TelephonyAudioOutput)
        assert isinstance(session.input.audio, TelephonyAudioInput)
        assert session.userdata["call_ctx"] is ctx
        assert agent._ctx is ctx
    finally:
        await session.output.audio.aclose()
