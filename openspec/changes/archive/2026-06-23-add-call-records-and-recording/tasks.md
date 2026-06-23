# Tasks: 通话记录查看与录音回放

> 按执行依赖排序（先依赖，后依赖方）。每个任务可独立验证。
> **最高优先级不变量**：所有 PG 写入 fire-and-forget，绝不阻断通话流（音频/LLM/TTS/ESL）。

## 1. agent-flow 配置与录音基础设施

- [ ] 1.1 `agent-flow/src/config.py`：新增 `recordings_dir`（默认 `/Users/lindaw/freeswitch/var/lib/freeswitch/recordings`）、`recording_notice_enabled`（默认 true）、`recording_archive_timeout`（默认 30）、`recording_notice_sound`（默认 `ivr/recording_notice.wav`）配置项，`CALLBOT_` 前缀
- [ ] 1.2 `agent-flow/src/storage/minio_storage.py`：新增 `upload_recording(call_id, wav_bytes, biz_type, tenant_id)`（object key = `recordings/{YYYYMMDD}/{call_id}.wav`，复用 `upload_audio` 底层；`MINIO_ENDPOINT` 空时静默返回）
- [ ] 1.3 `agent-flow/src/storage/minio_storage.py`：新增 `presigned_get_url(object_key, expiry=3600)`（`_client().presigned_get_object(MINIO_BUCKET, object_key, expires=timedelta(seconds=expiry))`；MinIO 未配置/异常返回 None）
- [ ] 1.4 验证：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.storage.minio_storage import upload_recording, presigned_get_url; print('ok')"` 导入无错；配置项在 settings 可见

## 2. FreeSWITCH dialplan 整通录音

- [ ] 2.1 `freeswitch/dialplan/public/00_biz_type.xml`：`answer` 后插入录音提示音 + `record_session ${recordings_dir}/${uuid}.wav`，再 `silence_stream://-1`；提示音受变量开关控制（`<action application="playback" data="..." cond="..."/>` 或 set 变量）
- [ ] 2.2 验证：`./scripts/local.sh stop fs && ./scripts/local.sh fs` 重启 FS；`fs_cli -x "reloadxml"`；真实呼入一通，检查 `${recordings_dir}/` 下生成 `${uuid}.wav` 文件且可播放（含双向混音）

## 3. agent-flow repository 接线（session 生命周期）

> 依赖 Task 1 配置项就绪。接线点：`main._on_channel_answer` / `main._on_channel_hangup`。

- [ ] 3.1 `agent-flow/main.py`：新增 `_mask_phone(user_key)`（首3末4掩码 `138****1234`）、`_phone_hash(user_key)`（sha256 hex）辅助函数
- [ ] 3.2 `agent-flow/main.py::_on_channel_answer`：`_call_registry.register` 后插入 `await repository.insert_call_session({...})`（call_id=uuid, fs_uuid=uuid, user_id=user_key, phone_hash, phone_masked, biz_type, tenant_id, scenario, start_ts, recording_notice_played）；外层 try/except 记日志不阻断
- [ ] 3.3 `agent-flow/main.py::_on_channel_hangup`：从 `_call_registry.get(uuid)` 读 ActiveCall 的 biz_type/user_key/tenant_id；调 `repository.update_call_session_end(uuid, end_ts, hangup_cause, result_code)`；registry 无记录时优雅跳过
- [ ] 3.4 `agent-flow/main.py::_on_channel_hangup`：新增 `_archive_recording(uuid, biz_type, tenant_id, user_key)` 协程（读 `CALLBOT_RECORDINGS_DIR/{uuid}.wav`，文件未就绪重试 3×0.5s → `upload_recording` → `repository.insert_artifact(kind='recording', storage='minio', uri=key, size_bytes, content_type='audio/wav')`）；`asyncio.create_task` fire-and-forget + `add_done_callback` 记日志
- [ ] 3.5 `agent-flow/main.py`：顶部 `from src.storage import repository, minio_storage`
- [ ] 3.6 验证：真实呼入 → CHANNEL_ANSWER 后 `SELECT * FROM callbot.call_session WHERE call_id='<uuid>'` 有行（call_id=fs_uuid，phone_masked 掩码正确）；挂断后 `end_ts/hangup_cause` 已填；录音 artifact 行存在（uri 为 object key）；通话过程无报错日志

## 4. agent-flow repository 接线（每轮 turn + 事件）

> 依赖 Task 3。接线点：`flow.run_streaming_pipeline`（turn 双写）、`handler._execute_terminal_action` 与 barge-in 分支（event）。

- [ ] 4.1 `agent-flow/src/graph/flow.py::run_streaming_pipeline`：在 `await save_turn(...)` 旁加 PG 双写——`asyncio.create_task(repository.insert_turn(role='user', text=user_input, ...))` + `asyncio.create_task(repository.insert_turn(role='assistant', text=full_text, ...))`；call_id=fs_uuid=state['call_id']，user_id=user_key；空轮跳过；task 加 done_callback 记日志
- [ ] 4.2 `agent-flow/src/ws/handler.py::_execute_terminal_action`：action='handoff' 分支加 `asyncio.create_task(repository.insert_event(event_type='handoff', payload={'extension': self._handoff_extension}, ...))`；action='end' 分支加 `event_type='hangup_by_bot'`
- [ ] 4.3 `agent-flow/src/ws/handler.py` 主循环 barge-in 分支（约 handler.py:174 `if barge_detected:`）：加 `asyncio.create_task(repository.insert_event(event_type='barge_in', payload={'turn': turn_count}, ...))`
- [ ] 4.4 `agent-flow/src/ws/handler.py` 与 `flow.py`：顶部 `from storage import repository`（注意 PYTHONPATH，handler 已 import `storage.minio_storage`）
- [ ] 4.5 验证：真实呼入多轮对话 → `SELECT role,text FROM callbot.call_turn WHERE call_id='<uuid>' ORDER BY ts` 见 user/assistant 交替行；人为打断 → `SELECT event_type FROM callbot.call_event WHERE call_id='<uuid>'` 见 barge_in 行；通话全程无阻断/无异常日志

## 5. console 权限码与 schema 映射

> 依赖无（console 读侧地基，先于 API）。Task 6/7 依赖此。

- [ ] 5.1 `console/server/src/lib/permissions.ts`：`PermissionCode` 联合类型加 `'call:view'`；`ROLE_PERMISSIONS` 的 admin/editor/viewer 数组各加 `'call:view'`（platform_admin 超集自动通过）
- [ ] 5.2 `console/server/src/db/schema.ts`：新增 `callSession` 映射（callbot schema，列名与 models.py CallSession 严格对齐：id/callId/fsUuid/userId/bizType/tenantId/scenario/phoneHash/userKey/phoneMasked/startTs/endTs/hangupCause/resultCode/identityVerified/recordingNoticePlayeded 等；TS camelCase → DB snake_case）；导出 type
- [ ] 5.3 `console/server/src/db/schema.ts`：新增 `callTurn` 映射（id/callId/fsUuid/bizType/userId/userKey/role/text/asrConf/ts 等）
- [ ] 5.4 `console/server/src/db/schema.ts`：新增 `callEvent` 映射（id/callId/fsUuid/bizType/userId/userKey/eventType/payload/ts）
- [ ] 5.5 `console/server/src/db/schema.ts`：新增 `callArtifact` 映射（id/callId/fsUuid/bizType/userId/userKey/kind/storage/uri/sha256/sizeBytes/contentType/ts）
- [ ] 5.6 验证：`cd console/server && npm run lint`（tsc --noEmit）无类型错误；Drizzle 映射列名与 agent-flow alembic DDL 一致（人工比对或 `docker exec callbot-postgres psql -U postgres -d callbot -c '\d callbot.call_session'`）

## 6. console 通话记录 API

> 依赖 Task 5 schema + 权限码。新增 lib/calls-service.ts + 3 个 route。

- [ ] 6.1 `console/server/src/lib/calls-service.ts`（新建）：`listCalls({tenantId, bizType?, phoneMasked?, startFrom?, startTo?, page, pageSize})`（SELECT callSession WHERE tenant_id + 筛选 + LIMIT/OFFSET + COUNT）；`getCallDetail(id, tenantId)`（session + turns ASC + events ASC + artifacts，聚合）；`getRecordingUrl(id, tenantId)`（查 artifact kind='recording' → MinIO presigned；无 artifact 返回 null）。参考 `routes-service.ts` 的 toDTO + tenantId 隔离模式
- [ ] 6.2 `console/server/src/app/api/calls/route.ts`（新建）：GET → `requirePermission('call:view')` → 解析 query 参数 → `listCalls(auth.tenantId, ...)` → 返回 `{calls, total, page, pageSize}`
- [ ] 6.3 `console/server/src/app/api/calls/[id]/route.ts`（新建）：GET → `requirePermission('call:view')` → `getCallDetail(id, auth.tenantId)` → 404 if null（含跨租户）→ 返回聚合 JSON
- [ ] 6.4 `console/server/src/app/api/calls/[id]/recording-url/route.ts`（新建）：GET → `requirePermission('call:view')` → `getRecordingUrl(id, auth.tenantId)` → 有则 `{url, expiresIn:3600}`，无则 404
- [ ] 6.5 验证：`npm run lint`；登录后 `curl localhost:3001/api/calls` 返回当前租户通话列表；`curl localhost:3001/api/calls/<id>` 返回聚合详情；跨租户 id 返回 404；未登录 401

## 7. console 通话记录 UI

> 依赖 Task 6 API + Task 5 菜单。

- [ ] 7.1 `console/server/src/components/ConsoleShell.tsx`：MENUS 的 `records` 项改 `enabled: true, href: '/calls'`（移除"下期"标记）
- [ ] 7.2 `console/server/src/app/calls/page.tsx`（新建）：`requireAuth` + `<ConsoleShell>` 包裹 + `<CallRecordsList>` 组件
- [ ] 7.3 `console/server/src/components/CallRecordsList.tsx`（新建）：client component；筛选区（biz_type 下拉、phone_masked 搜索、时间范围）+ 表格（开始时间/biz_type/phone_masked/时长/hangup_cause）+ 分页；`GET /api/calls?...`；点击行 `router.push('/calls/<id>')`；空态"暂无通话记录"。参考 `InboundRoutesManager.tsx` 的 fetch + flash toast 模式
- [ ] 7.4 `console/server/src/app/calls/[id]/page.tsx`（新建）：`requireAuth` + `<ConsoleShell>` + `<CallDetail>` 组件
- [ ] 7.5 `console/server/src/components/CallDetail.tsx`（新建）：client component；顶部录音播放器（`GET /api/calls/<id>/recording-url` → `<audio controls src={url}>`；404 显示"录音未归档"）；逐轮对话回放（turns 按 ts ASC，user 右/assistant 左气泡）；事件时间线（events 按 ts ASC）。参考现有详情/列表组件样式（slate/indigo）
- [ ] 7.6 验证：`npm run lint`；`pm2 restart console`；登录后侧栏见「通话记录」可点击；列表页展示通话 + 筛选生效；详情页逐轮回放 + 事件时间线 + 录音可播放；无录音显示占位

## 8. 收尾验证

- [ ] 8.1 端到端：真实呼入 → 多轮对话 → barge-in → 挂断；console /calls 见该通话；详情页对话回放与实际一致；录音可播放；事件时间线含 barge_in
- [ ] 8.2 不阻断保证：人为停 PostgreSQL（`docker stop callbot-postgres`）后真实呼入，通话音频/LLM/TTS 全部正常（PG 写入报错但不阻断）；恢复 PG 后新通话正常落库
- [ ] 8.3 多租户隔离：platform_admin 切到不同租户，/calls 列表仅显示该租户通话；跨租户详情 id 返回 404
- [ ] 8.4 `openspec validate add-call-records-and-recording --strict` 通过
- [ ] 8.5 `codegraph sync` + CRG `build_or_update_graph`（main.py/handler.py/flow.py/minio_storage.py/schema.ts 改动纳入索引）；通话记录 API 补 vitest 单测（参考 `tests/lib/` 现有风格）
