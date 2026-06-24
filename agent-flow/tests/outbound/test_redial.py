"""重拨判定单测 — hangup_cause + redial_strategy → 重拨 or 终态。"""
from outbound.redial import decide_redial, RedialDecision


RETRYABLE = ["NO_ANSWER", "RECOVERY_ON_TIMER_EXPIRE", "USER_NOT_REGISTERED"]


def test_normal_clearing_is_final_done():
    """接通成功（NORMAL_CLEARING）不重拨，终态 done。"""
    d = decide_redial("NORMAL_CLEARING", attempt_count=0, max_attempts=3, retry_on_causes=RETRYABLE)
    assert d == RedialDecision(redial=False, final_status="done")


def test_retryable_cause_within_limit_redials():
    """NO_ANSWER 在可重拨原因集且未达上限 → 重拨。"""
    d = decide_redial("NO_ANSWER", attempt_count=0, max_attempts=3, retry_on_causes=RETRYABLE)
    assert d.redial is True


def test_non_retryable_cause_is_final_failed():
    """不在 retry_on_causes 的失败原因 → 终态 failed。"""
    d = decide_redial("BUSY", attempt_count=0, max_attempts=3, retry_on_causes=RETRYABLE)
    assert d == RedialDecision(redial=False, final_status="failed")


def test_reached_max_attempts_is_final_failed():
    """已达 max_attempts → 终态 failed，不再重拨。"""
    d = decide_redial("NO_ANSWER", attempt_count=3, max_attempts=3, retry_on_causes=RETRYABLE)
    assert d == RedialDecision(redial=False, final_status="failed")


def test_empty_retry_on_causes_means_no_redial():
    """retry_on_causes 空 → 任何失败都不重拨（终态 failed）。"""
    d = decide_redial("NO_ANSWER", attempt_count=0, max_attempts=3, retry_on_causes=[])
    assert d == RedialDecision(redial=False, final_status="failed")


def test_unknown_cause_treated_as_non_retryable():
    """未知 hangup_cause 不在可重拨集 → 终态 failed。"""
    d = decide_redial("SOME_WEIRD_CAUSE", attempt_count=0, max_attempts=3, retry_on_causes=RETRYABLE)
    assert d == RedialDecision(redial=False, final_status="failed")
