"""livekit 集成新配置项加载测试。"""


def test_endpointing_defaults():
    from config import settings
    assert settings.endpointing_min_delay == 0.1
    assert settings.endpointing_max_delay == 2.0
    assert settings.interruption_min_duration == 0.5
