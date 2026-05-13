"""合规检查 - 催收敏感字段拦截 + 营销 do_not_call"""
import re
import logging
from llm.service import LLMAction

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = [
    r'\d{4,}',
    r'欠款',
    r'逾期',
    r'剩余本金',
]


def contains_sensitive_fields(text: str) -> bool:
    return any(re.search(p, text) for p in SENSITIVE_PATTERNS)


def sanitize_sensitive_text(text: str) -> str:
    sanitized = text
    for pattern in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, '****', sanitized)
    return sanitized


def compliance_check(
    action: LLMAction,
    biz_type: str,
    identity_verified: bool = False,
    do_not_call: bool = False,
) -> LLMAction:
    if biz_type == "marketing" and do_not_call:
        logger.warning("营销 do_not_call 拦截")
        return LLMAction(action="end", text="抱歉打扰了，再见")

    if biz_type == "collection" and not identity_verified:
        if contains_sensitive_fields(action.text):
            logger.warning(f"催收敏感字段拦截: {action.text[:30]}")
            action.text = sanitize_sensitive_text(action.text)

    return action
