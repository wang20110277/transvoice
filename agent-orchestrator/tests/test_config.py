from config import Settings


def test_default_settings():
    s = Settings()
    assert s.fs_esl_host == "127.0.0.1"
    assert s.fs_esl_port == 8021
    assert s.redis_url.startswith("redis://")
    assert s.pg_dsn.startswith("postgresql://")
