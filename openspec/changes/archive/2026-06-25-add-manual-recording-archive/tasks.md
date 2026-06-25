# Tasks: 手动录音归档兜底

> 按执行依赖排序：agent-flow 接口先（被 console 依赖）→ console 后端转发 → 前端按钮 → 端到端验证。
> **不变量**：自动归档 `_archive_recording` 逻辑不变；流式通话路径（WS → JitterBuffer → APM → VAD → ASR → LLM → TTS）不受影响。

## 1. agent-flow 手动归档接口

> console 转发依赖此接口存在。

- [x] 1.1 `agent-flow/src/storage/repository.py`：确认 `get_call_session_by_fs_uuid(uuid)` 返回的 row 含 `biz_type/tenant_id/user_key/user_id`（手动归档取数入口）；新增（或复用）按 `call_id + kind` 查 `call_artifact` 的方法 `get_artifact_by_call_kind(call_id, kind)`（幂等检查用）
- [x] 1.2 `agent-flow/main.py`：新增 `POST /calls/{fs_uuid}/archive-recording` 路由（FastAPI `@app.post`），实现 5 步流程：查 session(→404) → 幂等查 artifact(→409) → 读 wav(→410) → upload(→502) → insert(→200)。失败用 `JSONResponse(status_code=..., content={"error": ...})`；入参 `fs_uuid: str` 路径参数
- [x] 1.3 `agent-flow/main.py`：（可选）抽取 `_archive_recording` 与新接口共享的「读 wav → upload_recording → insert_artifact」为 helper（如 `_archive_to_minio(fs_uuid, biz_type, tenant_id, user_id, user_key) -> str | None`），消除重复；若现有 `_archive_recording` 结构清晰也可在新接口内联，不强求抽取
- [x] 1.4 验证：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -m pytest tests/ -v` 全绿；`python -m py_compile main.py src/storage/repository.py`
- [x] 1.5 验证：重启 flow `./scripts/local.sh stop flow && ./scripts/local.sh flow`；`curl -i -X POST http://127.0.0.1:8000/calls/00000000-0000-0000-0000-000000000000/archive-recording` 返回 404

## 2. console 后端转发路由

> 依赖 Task 1（agent-flow 接口）。

- [x] 2.1 `console/server/src/lib/calls-service.ts`：新增 `archiveRecording(id, tenantId)` —— 复用现有 session 归属校验（跨租户返 null）→ 取 `fsUuid` → `fetch` POST `${flowBaseUrl}/calls/${fsUuid}/archive-recording` → 返回 `{ok, status, body}`
- [x] 2.2 `console/server/src/lib/calls-service.ts`（或 `minio-client.ts` 同层）：新增 `flowBaseUrl = process.env.CALLBOT_FLOW_URL ?? 'http://127.0.0.1:8000'`
- [x] 2.3 `console/server/src/app/api/calls/[id]/archive-recording/route.ts`：新建 POST 路由（参照 `recording-url/route.ts` 结构）—— Better Auth session 鉴权 + `activeTenantId` → 调 `archiveRecording` → 透传状态码与 JSON（agent-flow 4xx/5xx 原样透传）
- [x] 2.4 `console/server/.env.example`：新增 `CALLBOT_FLOW_URL=http://127.0.0.1:8000`
- [x] 2.5 验证：`cd console/server && npx tsc --noEmit` 无错；重启 console `./scripts/local.sh stop console && ./scripts/local.sh console`

## 3. console 前端按钮

> 依赖 Task 2（后端路由）。

- [x] 3.1 `console/server/src/lib/calls-api.ts`：`callsApi` 新增 `archiveRecording: (id) => req<{objectKey?: string}>(\`/api/calls/${id}/archive-recording\`, {method:'POST'})`；扩展 `req` 使其抛出的 Error 携带 `status` 码（如 `class HttpError extends Error { status }`），供前端按码分支
- [x] 3.2 `console/server/src/components/CallDetail.tsx`：新增 `archiving` loading 态；在录音区 `recordingChecked && !recording && !recordingUrl` 分支渲染「手动归档」按钮（loading 时禁用）
- [x] 3.3 `CallDetail.tsx`：点击 `handleArchive` —— 调 `callsApi.archiveRecording(id)`；200 → `flash('ok','归档成功')` + `load()`；409 → `flash('ok','录音已归档')` + `load()`；410 → `flash('err','录音文件已被清理，无法补归档')`；502 → `flash('err','归档服务暂不可用，请稍后重试')`；404 → `flash('err','通话记录不存在')`；按钮始终保留（除 loading）
- [x] 3.4 验证：`cd console/server && npx tsc --noEmit`；浏览器打开一通未归档通话详情，确认按钮出现

## 4. 端到端验证

> 依赖 Task 1-3。

- [x] 4.1 制造自动归档失败通话：临时把 `.env` 的 `CALLBOT_MINIO_ENDPOINT` 设为不可达 → 重启 flow → 真实呼入一通（caller 说话 + AI 回复 + 挂断）→ 确认 console 详情页显示「手动归档」按钮（无播放器）
- [x] 4.2 恢复正确 `CALLBOT_MINIO_ENDPOINT` 重启 flow → 点「手动归档」→ 200 → 播放器出现，录音可回放（L=caller / R=AI）
- [x] 4.3 文件已清理场景：删除该 uuid 的本地 wav → 重启 flow → 点按钮 → 410 提示「录音文件已被清理」
- [x] 4.4 MinIO 不可用场景：`docker compose stop minio` → 点按钮 → 502 提示「归档服务暂不可用」，按钮可重试
- [x] 4.5 幂等：对 4.2 已归档通话，用 curl 直调 agent-flow 接口 → 返回 409（前端因已不显示按钮，此分支由 curl 覆盖）
- [x] 4.6 多租户：切换 tenant 访问非本租户通话的归档路由 → 404（不泄漏存在性）
- [x] 4.7 流式通话回归：再呼入一通，确认自动归档仍正常（手动接口未影响 `_archive_recording`）
- [x] 4.8 `openspec validate add-manual-recording-archive --strict` 通过
- [x] 4.9 `codegraph sync`（main.py / calls-service.ts / CallDetail.tsx / calls-api.ts 改动纳入索引）
