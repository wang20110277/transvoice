"""通话注册表 — 跟踪活跃通话, 支持挂断取消"""
import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ActiveCall:
    """一个活跃通话的状态"""
    call_id: str
    biz_type: str
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


class ActiveCallRegistry:
    """活跃通话注册表 — CHANNEL_HANGUP 事件可取消正在处理的通话。"""

    def __init__(self) -> None:
        self._calls: dict[str, ActiveCall] = {}

    def register(self, call_id: str, biz_type: str) -> ActiveCall:
        """注册一个新通话。"""
        call = ActiveCall(call_id=call_id, biz_type=biz_type)
        self._calls[call_id] = call
        logger.debug("[%s] call registered", call_id)
        return call

    def unregister(self, call_id: str) -> None:
        """注销通话。"""
        self._calls.pop(call_id, None)
        logger.debug("[%s] call unregistered", call_id)

    def cancel_call(self, call_id: str) -> bool:
        """取消指定通话（由 CHANNEL_HANGUP 触发）。返回是否找到该通话。"""
        call = self._calls.get(call_id)
        if call:
            call.cancel.set()
            logger.info("[%s] call cancelled via CHANNEL_HANGUP", call_id)
            return True
        return False

    def get(self, call_id: str) -> ActiveCall | None:
        return self._calls.get(call_id)

    @property
    def active_count(self) -> int:
        return len(self._calls)
