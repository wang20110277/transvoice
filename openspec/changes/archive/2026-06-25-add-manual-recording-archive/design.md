# Design: 手动录音归档兜底

## 背景与动机

自动录音归档（`main.py::_archive_recording`，CHANNEL_HANGUP 后 fire-and-forget）在 MinIO 不可用 / 上传失败时静默跳过（日志 `upload_recording returned None ... 跳过归档`），**无兜底**。但 FreeSWITCH 本地 `CALLBOT_RECORDINGS_DIR/{uuid}.wav`（`uuid_record` 写入）**无定时清理**——文件一直保留。因此自动归档失败的通话完全具备事后补归档的条件，只缺一个触发入口。

## 方案总览

agent-flow 新增**同步** HTTP 接口，按 fs_uuid 重新归档；console 通话详情页在检测到无 recording artifact 时展示「手动归档」按钮，点击经 **console 后端转发**调 agent-flow（不直连，避免跨域与暴露内网地址）。

## 关键决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 调用链路 | console 后端 `/api` 转发 → agent-flow | 与现有 `recording-url` 架构一致；前端不直连 agent-flow，无跨域、不暴露内网地址；agent-flow 接口仍无鉴权（内网信任） |
| 三元组来源 | DB 反查 `call_session` | 通话已挂断，`ActiveCallRegistry` 已清空；`repository.get_call_session_by_fs_uuid` 可拿到 biz_type/tenant_id/user_key/user_id |
| 同步 vs 异步 | 同步 | 录音通常 MB 级，上传秒级完成；返回最终结果，前端无需轮询 |
| 幂等 | 已有 recording artifact → 409 不重复上传 | 防自动+手动并发、用户重复点击产生重复 artifact |
| 鉴权 | agent-flow 接口不校验；console 后端做 tenant 隔离 | 内网信任（与 `/media/{uuid}` 一致）；租户隔离由 console 转发前 `getCallDetail` 校验保证 |

## 1. agent-flow 接口

`POST /calls/{fs_uuid}/archive-recording`（无鉴权）

```
处理流程（同步，5 步）：
1. repository.get_call_session_by_fs_uuid(fs_uuid) → session
   None → 404 {"error": "call session not found"}
2. 幂等：查 call_artifact(call_id=fs_uuid, kind='recording') 已存在
   → 409 {"error": "already archived", "objectKey": <uri>}
3. 读 settings.recordings_dir/{fs_uuid}.wav，os.path.exists 为假
   → 410 {"error": "recording file not found"}
4. upload_recording(fs_uuid, wav, biz_type, tenant_id) 返回 None（MinIO 不可用）
   → 502 {"error": "minio unavailable"}
5. repository.insert_artifact(call_id=fs_uuid, fs_uuid=fs_uuid, biz_type,
   user_id, user_key, kind='recording', storage='minio', uri=key,
   size_bytes, content_type='audio/wav')
   → 200 {"objectKey": key}
```

**复用**：步骤 3-5 与 `_archive_recording` 完全一致（读 wav → upload → insert）。差异仅三元组来源：自动=registry、手动=DB 反查。实现时可抽取共享 helper `_archive_to_minio(fs_uuid, biz_type, tenant_id, user_id, user_key)` 供两者调用，消除重复（build 阶段视 `_archive_recording` 当前结构决定是否抽取，非强制）。

## 2. console 后端转发

`POST /api/calls/{id}/archive-recording`（Better Auth session + tenant 隔离）

- 复用 `getCallDetail(id, activeTenantId)` 同款 tenant 校验（跨租户 → 404，不泄漏存在性）
- 取 `session.fsUuid` → `fetch` POST `${CALLBOT_FLOW_URL}/calls/${fsUuid}/archive-recording`
- 透传 agent-flow 状态码与 error body

新增配置 `CALLBOT_FLOW_URL`（`console/server/.env`，默认 `http://127.0.0.1:8000`）。

实现位置：
- `console/server/src/lib/calls-service.ts`：新增 `archiveRecording(id, tenantId)`
- `console/server/src/app/api/calls/[id]/archive-recording/route.ts`：新路由（参照现有 `recording-url/route.ts`）

## 3. console 前端

- `calls-api.ts`：`callsApi.archiveRecording(id)` → `POST /api/calls/{id}/archive-recording`；`req` 需在非 2xx 时抛出**携带 status 码**的错误（扩展 `req` 或新建专用调用），供前端按码分支
- `CallDetail.tsx`：当 `recordingChecked && !recording && !recordingUrl`（无 recording artifact）时，在「录音未归档」处展示「手动归档」按钮 + `archiving` loading 态；点击 → 调接口；200 → `flash('ok')` + `load()` 重载（detail 现有 artifact → recordingUrl → 播放器）；非 200 → 按状态码 toast，按钮保留可重试

## 错误码 → 前端提示

| 状态 | 含义 | 前端提示 |
|------|------|---------|
| 200 | 归档成功 | 「归档成功」→ 自动刷新出播放器 |
| 404 | session 不存在 / 跨租户 | 「通话记录不存在」 |
| 409 | 已有 recording artifact | 「录音已归档」→ 自动刷新 |
| 410 | FS 本地 wav 已清理 | 「录音文件已被清理，无法补归档」 |
| 502 | MinIO 不可用 | 「归档服务暂不可用，请稍后重试」（可重试） |

## 不变

- `_archive_recording`（自动归档）逻辑不变
- 录音声道布局（L=caller / R=AI）、object key 格式、`insert_artifact` 字段不变
- agent-flow 无新增鉴权机制

## 多租户

agent-flow 接口本身不校验 tenant（内网信任）；隔离由 console 后端转发前的 `getCallDetail(tenantId)` 保证。agent-flow 信任来自 console 的请求（console 已做租户校验）。
