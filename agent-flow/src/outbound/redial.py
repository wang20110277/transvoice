"""重拨判定 — 根据 hangup_cause + redial_strategy 决定重拨 or 终态。

纯函数（无副作用），便于单测。被 _on_channel_hangup 调用：
  接通成功 → 终态 done，不重拨
  失败原因 ∈ retry_on_causes 且 attempt_count < max_attempts → 重拨（置 pending + 退避）
  否则 → 终态 failed

max_attempts = redial_strategy.max_retries + 1（执行器录入时算好）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedialDecision:
    redial: bool           # True=重拨（调用方置 pending + next_attempt_ts 退避）
    final_status: str      # 不重拨时的终态（done/failed）；redial=True 时无意义


def decide_redial(
    hangup_cause: str,
    attempt_count: int,
    max_attempts: int,
    retry_on_causes: list[str],
) -> RedialDecision:
    """判定挂断后是否重拨。"""
    # 接通成功 → 终态 done
    if hangup_cause == "NORMAL_CLEARING":
        return RedialDecision(redial=False, final_status="done")

    # 失败：原因可重拨且未达上限 → 重拨；否则终态 failed
    retryable = hangup_cause in (retry_on_causes or [])
    if retryable and attempt_count < max_attempts:
        return RedialDecision(redial=True, final_status="failed")
    return RedialDecision(redial=False, final_status="failed")
