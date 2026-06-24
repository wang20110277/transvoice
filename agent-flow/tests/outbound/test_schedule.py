"""is_within_allowed_hours 单测 — 时段调度判定（纯函数，时间注入）。"""
from datetime import datetime

from outbound.schedule import is_within_allowed_hours


def _t(hhmm: str) -> datetime:
    """构造一个固定日期、指定 HH:MM 的时间点。"""
    h, m = hhmm.split(":")
    return datetime(2026, 6, 24, int(h), int(m))


def test_none_means_always_allowed():
    """空/None = 不限制时段，任意时间都允许。"""
    assert is_within_allowed_hours(None, _t("03:00")) is True
    assert is_within_allowed_hours("", _t("23:59")) is True


def test_inside_window():
    assert is_within_allowed_hours("09:00-21:00", _t("14:30")) is True
    assert is_within_allowed_hours("09:00-21:00", _t("09:00")) is True  # 左闭
    assert is_within_allowed_hours("09:00-21:00", _t("20:59")) is True


def test_outside_window():
    assert is_within_allowed_hours("09:00-21:00", _t("08:59")) is False
    assert is_within_allowed_hours("09:00-21:00", _t("21:00")) is False  # 右开
    assert is_within_allowed_hours("09:00-21:00", _t("03:00")) is False


def test_invalid_format_treated_as_unrestricted():
    """格式不符（非 HH:MM-HH:MM）视为不限制，避免误锁死任务。"""
    assert is_within_allowed_hours("garbage", _t("14:00")) is True
    assert is_within_allowed_hours("9-21", _t("14:00")) is True
