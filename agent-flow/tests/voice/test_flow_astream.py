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
    import graph.prompt_config as prompt_config

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
    # get_system_prompt 是 astream_reply_text 函数内 import，patch 源模块而非 flow 命名空间
    monkeypatch.setattr(prompt_config, "get_system_prompt", fake_prompt)

    state = {"call_id": "c1", "tenant_id": "default", "biz_type": "collection",
             "scenario": "default", "user_key": "u1", "user_input": "在吗",
             "chat_history": [], "call_task_vars": {}}
    actions: list[str] = []
    tokens = [t async for t in flow.astream_reply_text(
        state, on_action=actions.append)]

    assert tokens == ["您好，", "请问。"]
    assert actions == ["end"]
    assert saved, "流末必须持久化对话历史"
    # save_turn 实际签名 save_turn(call_id, biz_type, user_text, ai_text) 为位置传参
    assert saved[0][0] == ("c1", "collection", "在吗", "您好，请问。")


@pytest.mark.asyncio
async def test_action_default_say_when_missing(monkeypatch):
    import graph.flow as flow
    import graph.prompt_config as prompt_config

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
    monkeypatch.setattr(prompt_config, "get_system_prompt", fake_prompt)

    state = {"call_id": "c2", "tenant_id": "default", "biz_type": "collection",
             "scenario": "default", "user_key": "u1", "user_input": "嗯",
             "chat_history": [], "call_task_vars": {}}
    actions: list[str] = []
    _ = [t async for t in flow.astream_reply_text(state, on_action=actions.append)]
    assert actions == ["say"]  # 兜底：LLM 未产出 action 时默认 say
