"""手动录音归档接口测试 — 覆盖 5 个状态码分支（404/409/410/502/200）。

直接 await 路由函数（不经 FastAPI TestClient，避开 lifespan 重依赖）；repository /
minio_storage / 文件系统用 monkeypatch 替换，隔离 DB / MinIO / 磁盘。
"""
import io
import json
from types import SimpleNamespace

import pytest

import main


def _body(resp) -> dict:
    return json.loads(resp.body)


def _session():
    return SimpleNamespace(
        biz_type="marketing", tenant_id="t1", user_id="u1", user_key="138****5678",
    )


@pytest.mark.asyncio
async def test_404_when_session_missing(monkeypatch):
    """fs_uuid 在 call_session 无记录 → 404。"""
    async def fake_session(fs):
        return None
    monkeypatch.setattr(main.repository, "get_call_session_by_fs_uuid", fake_session)

    resp = await main.archive_recording("missing-uuid")

    assert resp.status_code == 404
    assert _body(resp)["error"] == "call session not found"


@pytest.mark.asyncio
async def test_409_when_already_archived(monkeypatch):
    """该通话已存在 recording artifact → 409 幂等，不重复上传。"""
    monkeypatch.setattr(
        main.repository, "get_call_session_by_fs_uuid",
        lambda fs: _session_done(_session()),
    )
    async def fake_artifact(cid, kind):
        return SimpleNamespace(uri="recordings/20260101/uuid.wav")
    monkeypatch.setattr(main.repository, "get_artifact_by_call_kind", fake_artifact)

    resp = await main.archive_recording("uuid")

    assert resp.status_code == 409
    assert _body(resp)["error"] == "already archived"
    assert _body(resp)["objectKey"] == "recordings/20260101/uuid.wav"


@pytest.mark.asyncio
async def test_410_when_file_missing(monkeypatch):
    """FS 本地 wav 已清理 → 410。"""
    monkeypatch.setattr(
        main.repository, "get_call_session_by_fs_uuid",
        lambda fs: _session_done(_session()),
    )
    async def no_artifact(cid, kind):
        return None
    monkeypatch.setattr(main.repository, "get_artifact_by_call_kind", no_artifact)
    monkeypatch.setattr(main.os.path, "exists", lambda p: False)

    resp = await main.archive_recording("uuid")

    assert resp.status_code == 410
    assert _body(resp)["error"] == "recording file not found"


@pytest.mark.asyncio
async def test_502_when_minio_unavailable(monkeypatch):
    """upload_recording 返回 None（MinIO 未配置/失败）→ 502。"""
    monkeypatch.setattr(
        main.repository, "get_call_session_by_fs_uuid",
        lambda fs: _session_done(_session()),
    )
    async def no_artifact(cid, kind):
        return None
    monkeypatch.setattr(main.repository, "get_artifact_by_call_kind", no_artifact)
    monkeypatch.setattr(main.os.path, "exists", lambda p: True)
    monkeypatch.setattr("builtins.open", lambda *a, **k: io.BytesIO(b"fake-wav"))
    async def upload_returns_none(*a, **k):
        return None
    monkeypatch.setattr(main.minio_storage, "upload_recording", upload_returns_none)

    resp = await main.archive_recording("uuid")

    assert resp.status_code == 502
    assert _body(resp)["error"] == "minio unavailable"


@pytest.mark.asyncio
async def test_200_success(monkeypatch):
    """全链路成功 → 200 + objectKey，且 insert_artifact 被正确调用。"""
    monkeypatch.setattr(
        main.repository, "get_call_session_by_fs_uuid",
        lambda fs: _session_done(_session()),
    )
    async def no_artifact(cid, kind):
        return None
    monkeypatch.setattr(main.repository, "get_artifact_by_call_kind", no_artifact)
    monkeypatch.setattr(main.os.path, "exists", lambda p: True)
    monkeypatch.setattr("builtins.open", lambda *a, **k: io.BytesIO(b"fake-wav"))
    async def upload_ok(cid, wav, biz, tenant):
        return "recordings/20260101/uuid.wav"
    monkeypatch.setattr(main.minio_storage, "upload_recording", upload_ok)
    inserted = []
    async def fake_insert(**kw):
        inserted.append(kw)
    monkeypatch.setattr(main.repository, "insert_artifact", fake_insert)

    resp = await main.archive_recording("uuid")

    assert resp.status_code == 200
    assert _body(resp)["objectKey"] == "recordings/20260101/uuid.wav"
    assert len(inserted) == 1
    art = inserted[0]
    assert art["kind"] == "recording"
    assert art["storage"] == "minio"
    assert art["uri"] == "recordings/20260101/uuid.wav"
    assert art["biz_type"] == "marketing"
    assert art["user_id"] == "u1"  # user_id 从 session 取（非 _archive_recording 的 user_key fallback）


async def _session_done(s):
    """async wrapper: get_call_session_by_fs_uuid 是协程，mock 须返回 awaitable。"""
    return s
