"""录音归档配置接线测试 — 防 commit 8cfdd9d 类回归。

.env 的 MinIO 配置已统一 CALLBOT_MINIO_ 前缀走 pydantic Settings；minio_storage 必须
从 settings.minio_* 读取，而非无前缀 MINIO_* env var（.env 改名后 os.environ 里已无该键，
旧读取方式会得到空串 → upload_recording 静默跳过归档）。
"""
from config import settings


def test_minio_storage_reads_endpoint_from_settings():
    """settings.minio_endpoint 非空时，build_object_key 必须返回非 None（接线未断）。"""
    import storage.minio_storage as minio_storage

    assert settings.minio_endpoint, "settings.minio_endpoint 未配置"
    key = minio_storage.build_object_key(prefix="recordings", call_id="test-call")
    assert key is not None
    assert key.startswith("recordings/")
    assert "test-call" in key


def test_minio_storage_client_built_when_configured():
    """settings 配好 endpoint 时 _client() 返回真实 Minio 客户端，而非 None。"""
    import storage.minio_storage as minio_storage

    assert settings.minio_endpoint, "settings.minio_endpoint 未配置"
    assert minio_storage._client() is not None
