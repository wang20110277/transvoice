"""fire-and-forget PG 写入封装。

所有 call_turn / call_event 写入经此模块：asyncio.create_task + add_done_callback 记日志，
确保 DB 异常绝不阻断通话流（与 Redis save_turn 同级容错），且不静默吞错。

注意：helper 只收 **kwargs（透传 repository.insert_turn/insert_event 的字段），
日志标签从 kwargs['call_id'] 取——避免位置参 call_id 与 repository 的 call_id 字段撞名
（TypeError: multiple values for argument 'call_id'）。
"""
import asyncio
import logging

from storage import repository

logger = logging.getLogger(__name__)


def _fire(coro, call_id: str, what: str) -> None:
    """create_task 起 coro，异常通过 done_callback 记 error（不静默吞）。"""
    task = asyncio.create_task(coro)
    task.add_done_callback(
        lambda t: t.exception() and logger.error(
            "[%s] %s failed: %s", call_id, what, t.exception(),
        )
    )


def fire_insert_turn(**turn_kwargs) -> None:
    """fire-and-forget 写一行 call_turn。参数透传 repository.insert_turn（call_id 为必填字段）。"""
    call_id = turn_kwargs.get("call_id", "?")
    _fire(repository.insert_turn(**turn_kwargs), call_id, "insert_turn")


def fire_insert_event(**event_kwargs) -> None:
    """fire-and-forget 写一行 call_event。参数透传 repository.insert_event（call_id 为必填字段）。"""
    call_id = event_kwargs.get("call_id", "?")
    _fire(repository.insert_event(**event_kwargs), call_id, "insert_event")
