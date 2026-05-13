import pytest
from compliance import compliance_check, contains_sensitive_fields
from llm.service import LLMAction


def test_contains_sensitive_fields():
    assert contains_sensitive_fields("您的欠款金额为50000元") is True
    assert contains_sensitive_fields("请问您方便通话吗") is False


def test_collection_blocks_sensitive_when_not_verified():
    action = LLMAction(action="say", text="您欠款50000元", intent="inform")
    result = compliance_check(action, "collection", identity_verified=False)
    assert "50000" not in result.text


def test_collection_allows_sensitive_when_verified():
    action = LLMAction(action="say", text="您欠款50000元", intent="inform")
    result = compliance_check(action, "collection", identity_verified=True)
    assert "50000" in result.text


def test_marketing_do_not_call():
    action = LLMAction(action="say", text="我们有优惠活动", intent="pitch")
    result = compliance_check(action, "marketing", identity_verified=False, do_not_call=True)
    assert result.action == "end"
