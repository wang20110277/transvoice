# 实现计划：add-manual-recording-archive

## 来源
- 提案：openspec/changes/add-manual-recording-archive/proposal.md
- 设计：openspec/changes/add-manual-recording-archive/design.md
- 规格：openspec/changes/add-manual-recording-archive/specs/（call-recording, call-records-console）
- 任务：openspec/changes/add-manual-recording-archive/tasks.md

## 不变量（全程不得违反）

- 自动归档 `_archive_recording` 逻辑与 fire-and-forget 行为不变
- 流式通话路径（WS → JitterBuffer → APM → VAD → ASR → LLM → TTS）不受影响
- agent-flow 不新增鉴权机制（内网信任）；租户隔离由 console 转发前保证

## 实现步骤

### Task 1: agent-flow 手动归档接口

> **目标**：新增 `POST /calls/{fs_uuid}/archive-recording` 同步接口，5 步流程 + 四类错误码。
> **改动文件**：`agent-flow/main.py`、`agent-flow/src/storage/repository.py`
> **验证**：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -m pytest tests/ -v` 全绿；重启 flow 后 curl 不存在 uuid 返回 404。

- 步骤 1.1 `src/storage/repository.py`：确认 `get_call_session_by_fs_uuid(uuid)` 返回 row 含 `biz_type/tenant_id/user_key/user_id`；新增 `get_artifact_by_call_kind(call_id, kind)` 用于幂等检查（无则新增）
- 步骤 1.2 `main.py`：新增 `@app.post("/calls/{fs_uuid}/archive-recording")`，实现：查 session(→404) → 幂等查 artifact(→409) → 读 `recordings_dir/{fs_uuid}.wav`(→410) → `upload_recording`(→502) → `insert_artifact`(→200)。失败用 `JSONResponse(status_code=, content={"error":})`
- 步骤 1.3 `main.py`：（可选）抽 helper `_archive_to_minio(fs_uuid, biz_type, tenant_id, user_id, user_key)` 供 `_archive_recording` 与新接口共享；若现状清晰可内联
- 步骤 1.4 跑测试 + `python -m py_compile main.py src/storage/repository.py`
- 步骤 1.5 重启 flow + `curl -i -X POST http://127.0.0.1:8000/calls/00000000-0000-0000-0000-000000000000/archive-recording` → 404

### Task 2: console 后端转发路由

> **目标**：`POST /api/calls/{id}/archive-recording`，租户隔离 + 透传 agent-flow 状态。
> **依赖**：Task 1（agent-flow 接口存在）。
> **改动文件**：`console/server/src/lib/calls-service.ts`、`console/server/src/app/api/calls/[id]/archive-recording/route.ts`、`console/server/.env.example`
> **验证**：`cd console/server && npx tsc --noEmit` 无错；重启 console 无报错。

- 步骤 2.1 `calls-service.ts`：新增 `archiveRecording(id, tenantId)` —— 复用 session 归属校验 → 取 `fsUuid` → `fetch` POST `${flowBaseUrl}/calls/${fsUuid}/archive-recording` → 返回 `{ok, status, body}`
- 步骤 2.2 `calls-service.ts`：新增 `flowBaseUrl = process.env.CALLBOT_FLOW_URL ?? 'http://127.0.0.1:8000'`
- 步骤 2.3 新建 `api/calls/[id]/archive-recording/route.ts`（参照 `recording-url/route.ts`）：Better Auth session + `activeTenantId` → 调 `archiveRecording` → 透传状态码与 JSON
- 步骤 2.4 `.env.example`：新增 `CALLBOT_FLOW_URL=http://127.0.0.1:8000`
- 步骤 2.5 `npx tsc --noEmit` + 重启 console

### Task 3: console 前端按钮

> **目标**：`CallDetail` 未归档时展示按钮，成功刷新、失败按码提示可重试。
> **依赖**：Task 2（后端路由）。
> **改动文件**：`console/server/src/lib/calls-api.ts`、`console/server/src/components/CallDetail.tsx`
> **验证**：`npx tsc --noEmit`；浏览器打开未归档通话详情，按钮出现。

- 步骤 3.1 `calls-api.ts`：新增 `archiveRecording(id)`；扩展 `req` 抛出的错误携带 `status` 码（`HttpError extends Error { status }`），供按码分支
- 步骤 3.2 `CallDetail.tsx`：新增 `archiving` loading 态；录音区 `recordingChecked && !recording && !recordingUrl` 分支渲染按钮（loading 禁用）
- 步骤 3.3 `CallDetail.tsx`：`handleArchive` —— 200→`flash ok`+`load()`；409→`flash ok 录音已归档`+`load()`；410→文件已清理；502→服务不可用可重试；404→通话不存在。按钮除 loading 始终保留
- 步骤 3.4 `npx tsc --noEmit` + 浏览器确认按钮

### Task 4: 端到端验证

> **目标**：覆盖 200/404/409/410/502 + 跨租户 + 自动归档回归。
> **依赖**：Task 1-3。

- 步骤 4.1 制造失败通话（`CALLBOT_MINIO_ENDPOINT` 设不可达 → 重启 flow → 呼入一通挂断）→ 详情页显示按钮
- 步骤 4.2 恢复正确 endpoint 重启 flow → 点按钮 → 200 → 播放器出现可回放（L=caller / R=AI）
- 步骤 4.3 删除该 uuid 本地 wav → 点按钮 → 410 提示
- 步骤 4.4 `docker compose stop minio` → 点按钮 → 502 提示可重试
- 步骤 4.5 curl 直调 agent-flow 对已归档通话 → 409
- 步骤 4.6 切换 tenant 访问非本租户归档路由 → 404
- 步骤 4.7 再呼入一通 → 自动归档仍正常（`_archive_recording` 未受影响）
- 步骤 4.8 `openspec validate add-manual-recording-archive --strict` 通过
- 步骤 4.9 `codegraph sync`
