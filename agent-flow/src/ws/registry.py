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
    user_key: str = ""
    tenant_id: str = "default"
    scenario: str = "default"
    # 外呼每号码 render 变量（call_target.vars，摘机加载）→ 透传进 graph state call_task_vars。
    # 呼入恒 {}（无 call_target_id），零影响。
    call_target_vars: dict = field(default_factory=dict)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


class ActiveCallRegistry:
    """活跃通话注册表 — CHANNEL_HANGUP 事件可取消正在处理的通话。"""

    def __init__(self) -> None:
        self._calls: dict[str, ActiveCall] = {}

    def register(
        self,
        call_id: str,
        biz_type: str,
        user_key: str = "",
        tenant_id: str = "default",
        scenario: str = "default",
        call_target_vars: dict | None = None,
    ) -> ActiveCall:
        """注册一个新通话。"""
        call = ActiveCall(
            call_id=call_id,
            biz_type=biz_type,
            user_key=user_key,
            tenant_id=tenant_id,
            scenario=scenario,
            call_target_vars=call_target_vars or {},
        )
        self._calls[call_id] = call
        logger.debug(
            "[%s] call registered tenant=%s biz_type=%s scenario=%s user_key=%s vars=%d",
            call_id, tenant_id, biz_type, scenario, user_key, len(call.call_target_vars),
        )
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
