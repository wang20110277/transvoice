"""外呼时段调度判定 — 解析 allowed_hours 字符串，判断给定时间是否在允许窗口内。

纯函数（时间由调用方注入），便于单测。生产调用传 datetime.now()。
"""
from __future__ import annotations

import re
from datetime import datetime

# "HH:MM-HH:MM"，如 "09:00-21:00"
_WINDOW_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


def is_within_allowed_hours(allowed_hours: str | None, now: datetime) -> bool:
    """now 是否落在 allowed_hours 窗口内（左闭右开 [start, end)）。

    - None / 空 / 格式不符 → True（不限制，避免误锁死任务）
    - 其余 → now 的 HH:MM 在 [start, end) 内为 True
    """
    if not allowed_hours:
        return True
    m = _WINDOW_RE.match(allowed_hours.strip())
    if not m:
        return True
    sh, sm, eh, em = (int(x) for x in m.groups())
    start = sh * 60 + sm
    end = eh * 60 + em
    cur = now.hour * 60 + now.minute
    return start <= cur < end
