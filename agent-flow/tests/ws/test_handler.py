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
    # on_final 已触发一次 reset(launch 后清 buffer);下面只验证 cancel_for_barge 自己的 reset
    reset_calls.clear()
    old = await tc.cancel_for_barge()
    assert old is tc.streaming_task or old is not None
    assert tc.streaming_task is None
    assert len(reset_calls) == 1
    old.cancel()
    try:
        await old
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_on_final_dropped_within_barge_cooldown_then_passes(monkeypatch):
    """barge 后 cooldown 窗口内的 final(残余段)丢弃,窗口外才启动 —— 防伪轮次。"""
    import ws.handler as handler_mod
    now = [1000.0]
    monkeypatch.setattr(handler_mod.time, "monotonic", lambda: now[0])

    launched = []
    tc = TurnController(_make_launch(launched), lambda: None, barge_cooldown_sec=0.5)
    await tc.cancel_for_barge()        # _barge_at = 1000.0

    now[0] = 1000.2                    # +0.2s, within 0.5s cooldown
    await tc.on_final({"text": "stale"})
    assert launched == []              # dropped

    now[0] = 1000.7                    # +0.7s, past cooldown
    await tc.on_final({"text": "fresh"})
    assert len(launched) == 1 and launched[0][1] == "fresh"

    # cleanup the launched never-complete task
    tc.streaming_task.cancel()
    try:
        await tc.streaming_task
    except asyncio.CancelledError:
        pass
