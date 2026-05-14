from config import Settings


def test_default_settings():
    s = Settings()
    assert s.redis_url.startswith("redis://")
    assert s.pg_dsn.startswith("postgresql")
    assert s.tts_adapter_url.startswith("http://")
