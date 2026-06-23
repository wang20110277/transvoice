"""脱敏 + 录音归档辅助函数单测。

测试 main.py 的 _mask_phone / _phone_hash（pure 函数，TDD）。
CHANNEL_ANSWER/HANGUP 接线属集成层，由真实呼入验证（Task 3 Step 8 / Task 8）。
"""
import hashlib


def test_mask_phone_long():
    from main import _mask_phone
    assert _mask_phone("13812345678") == "138****5678"


def test_mask_phone_short_passthrough():
    from main import _mask_phone
    assert _mask_phone("123") == "123"
    assert _mask_phone("") == ""


def test_phone_hash_is_sha256_hex():
    from main import _phone_hash
    assert _phone_hash("13812345678") == hashlib.sha256(b"13812345678").hexdigest()
    assert len(_phone_hash("13812345678")) == 64
