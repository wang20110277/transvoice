# 实现计划：add-call-records-and-recording

## 来源

- 提案：`openspec/changes/add-call-records-and-recording/proposal.md`
- 设计：`openspec/changes/add-call-records-and-recording/design.md`
- 规格：
  - `openspec/changes/add-call-records-and-recording/specs/call-records-persistence/spec.md`
  - `openspec/changes/add-call-records-and-recording/specs/call-recording/spec.md`
  - `openspec/changes/add-call-records-and-recording/specs/call-records-console/spec.md`
- 任务：`openspec/changes/add-call-records-and-recording/tasks.md`

> 执行顺序严格按依赖：① 配置+MinIO 方法 → ② dialplan 录音 → ③ session 接线 → ④ turn/event 接线 → ⑤ console schema/权限 → ⑥ console API → ⑦ console UI → ⑧ 收尾。
>
> **最高优先级不变量**：所有 PG repository 写入 fire-and-forget（`asyncio.create_task` + `add_done_callback` 或外层 try/except 记日志），任何 DB 异常**绝不阻断**音频流/LLM/TTS/ESL。容错等级与现有 Redis `save_turn`（`chat_history.py:80 except Exception: logger.warning`）一致。
>
> 工作目录分两处：agent-flow 改动在仓库根 `agent-flow/`，console 改动在 `console/server/`。服务用 pm2 管理（`./scripts/local.sh flow`、`pm2 restart console`）。

---

## Task 1：agent-flow 配置 + MinIO 录音方法

> 地基：配置项和 MinIO 方法先就绪，Task 3/4 接线点依赖它们。

### Step 1.1 — config.py 新增录音配置项
- **改动文件**：`agent-flow/src/config.py`
- **做什么**：Settings 类新增 4 字段（pydantic-settings，`CALLBOT_` 前缀自动从 env 读）：`recordings_dir: str = "/Users/lindaw/freeswitch/var/lib/freeswitch/recordings"`、`recording_notice_enabled: bool = True`、`recording_archive_timeout: int = 30`、`recording_notice_sound: str = "ivr/recording_notice.wav"`。命名风格参照现有 `media_sample_rate`/`jitter_target_depth`
- **验证**：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.config import settings; print(settings.recordings_dir, settings.recording_notice_enabled)"` 输出默认值无错

### Step 1.2 — minio_storage.upload_recording
- **改动文件**：`agent-flow/src/storage/minio_storage.py`
- **做什么**：新增 `async def upload_recording(call_id: str, wav_bytes: bytes, biz_type: str, tenant_id: str) -> str | None`：object key = `build_object_key(prefix="recordings", call_id=call_id)`（复用现有 `build_object_key`，已含 `{prefix}/{date}/{call_id}.wav` 格式）；`MINIO_ENDPOINT` 空时 `return None`；否则 `await upload_audio_async(wav_bytes, key)` 后 `return key`。与现有 `save_turn_audio` 的 `if not MINIO_ENDPOINT: return` 模式一致
- **验证**：`PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.storage.minio_storage import upload_recording; print(upload_recording)"` 导入无错

### Step 1.3 — minio_storage.presigned_get_url
- **改动文件**：`agent-flow/src/storage/minio_storage.py`
- **做什么**：新增 `def presigned_get_url(object_key: str, expiry: int = 3600) -> str | None`：`client = _client()`；`client is None` 或异常 → `return None`；否则 `from datetime import timedelta; return client.presigned_get_object(MINIO_BUCKET, object_key, expires=timedelta(seconds=expiry))`。MinIO SDK `presigned_get_object` 是同步方法
- **验证**：导入无错；MinIO 未配置时 `presigned_get_url("x")` 返回 None

### Step 1.4 — Task 1 整体验证
- **改动文件**：无
- **验证**：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.storage.minio_storage import upload_recording, presigned_get_url; from src.config import settings; print('recordings_dir=', settings.recordings_dir); print('ok')"` 全部导入成功

---

## Task 2：FreeSWITCH dialplan 整通录音

> dialplan 先于接线，真实呼入才能产出 wav 供 Task 3 归档验证。

### Step 2.1 — dialplan 加 record_session + 提示音
- **改动文件**：`freeswitch/dialplan/public/00_biz_type.xml`
- **做什么**：在现有 `<action application="answer"/>` 之后、`<action application="playback" data="silence_stream://-1"/>` 之前插入：(1) 录音提示音开关（用 FS set 变量 + conditional，或直接 `<action application="playback" data="${recording_notice_sound}" condition="${recording_notice_enabled}==true"/>`，本地测试可改 FS 全局变量关闭）；(2) `<action application="set" data="RECORD_STEREO=false"/>`（双向混音单声道，与 MinIO 上传一致）；(3) `<action application="record_session" data="${recordings_dir}/${uuid}.wav"/>`。`${uuid}` = Channel Unique-ID（FS 内置变量），`${recordings_dir}` = FS 内置默认 `$${base_dir}/recordings`
- **验证**：`./scripts/local.sh stop fs && ./scripts/local.sh fs`；`/Users/lindaw/freeswitch/bin/fs_cli -x "reloadxml"` 应返回 `+OK`；检查 `/Users/lindaw/freeswitch/var/log/freeswitch/freeswitch.log` 无 XML 解析错误

### Step 2.2 — 真实呼入验证录音产出
- **改动文件**：无
- **验证**：真实 SIP 呼入一通 → 通话几秒后挂断 → `ls -la /Users/lindaw/freeswitch/var/lib/freeswitch/recordings/`（或 `${base_dir}/recordings/`）见 `${uuid}.wav` 文件 → `afplay <file>` 或 `ffprobe` 确认含双向音频、可播放。若 FS `${recordings_dir}` 与 `CALLBOT_RECORDINGS_DIR` 路径不一致，记下实际路径并在 Task 3 Step 3.4 校正

---

## Task 3：agent-flow session 生命周期接线

> 依赖 Task 1（config + minio）+ Task 2（录音文件就绪）。接线点：`main._on_channel_answer`（main.py:175-226）/ `main._on_channel_hangup`（main.py:163-173）。

### Step 3.1 — 脱敏辅助函数
- **改动文件**：`agent-flow/main.py`
- **做什么**：模块级新增两个函数：`def _mask_phone(s: str) -> str:`（长度≥7 时返回 `f"{s[:3]}****{s[-4:]}"`，否则返回原串）；`def _phone_hash(s: str) -> str:`（`import hashlib; return hashlib.sha256(s.encode()).hexdigest()`）
- **验证**：`PYTHONPATH=$(pwd):$(pwd)/src python -c "import main; print(main._mask_phone('13812345678')); print(main._phone_hash('13812345678')[:16])"` 输出 `138****5678` + sha256 前16位

### Step 3.2 — CHANNEL_ANSWER 写 session start
- **改动文件**：`agent-flow/main.py::_on_channel_answer`
- **做什么**：顶部 `from src.storage import repository, minio_storage`；在 `_call_registry.register(uuid, ...)`（main.py:215）之后插入 session 写入：用 try/except 包裹 `await repository.insert_call_session({...})`，dict keys：`call_id=uuid, fs_uuid=uuid, user_id=user_key, biz_type, tenant_id, scenario, phone_hash=_phone_hash(user_key), user_key, phone_masked=_mask_phone(user_key), start_ts=datetime.now(), recording_notice_played=settings.recording_notice_enabled`。keys 必须匹配 `CallSession` 列名（models.py:33-54）。except 只 `logger.error("[%s] insert_call_session failed: %s", uuid, e)`，不 re-raise
- **验证**：真实呼入 → `docker exec callbot-postgres psql -U postgres -d callbot -c "SELECT call_id,fs_uuid,user_id,phone_masked,biz_type,tenant_id,start_ts,recording_notice_played FROM callbot.call_session ORDER BY id DESC LIMIT 1"` 见新行，call_id=fs_uuid，phone_masked 形如 `138****5678`；通话流正常（audio_fork_start 日志照常）

### Step 3.3 — CHANNEL_HANGUP 更新 session end
- **改动文件**：`agent-flow/main.py::_on_channel_hangup`
- **做什么**：当前函数（main.py:163-173）只做 `audio_fork_stop` + `cancel_call`。扩展：从 event headers 取 `hangup_cause = event.headers.get("Hangup-Cause", "")`、`result_code = event.headers.get("Variable-Hangup-Cause", "")`；`active = _call_registry.get(uuid)`；若 `active` 存在则 try/except `await repository.update_call_session_end(uuid, datetime.now(), hangup_cause, result_code)`，except 仅记日志；registry 无记录则跳过 PG（仍执行 audio_fork_stop + cancel）
- **验证**：真实呼入挂断后 → `SELECT end_ts,hangup_cause FROM callbot.call_session WHERE fs_uuid='<uuid>'` 见 end_ts 已填 + hangup_cause 非空

### Step 3.4 — _archive_recording 归档协程
- **改动文件**：`agent-flow/main.py`
- **做什么**：新增 `async def _archive_recording(fs_uuid: str, biz_type: str, tenant_id: str, user_key: str) -> None:`：(1) 构造 `path = os.path.join(settings.recordings_dir, f"{fs_uuid}.wav")`；(2) 文件就绪重试循环 `for i in range(3): if os.path.exists(path): break; await asyncio.sleep(0.5)`，仍未找到则 `logger.warning` + return；(3) `wav_bytes = open(path,'rb').read()`；(4) `key = await minio_storage.upload_recording(fs_uuid, wav_bytes, biz_type, tenant_id)`；key 为 None（MinIO 未配置）→ return；(5) try/except `await repository.insert_artifact(call_id=fs_uuid, fs_uuid=fs_uuid, biz_type=biz_type, user_id=user_key, user_key=user_key, kind='recording', storage='minio', uri=key, size_bytes=len(wav_bytes), content_type='audio/wav')`，except 仅记日志
- **验证**：`PYTHONPATH=$(pwd):$(pwd)/src python -c "import main; print(main._archive_recording)"` 导入无错

### Step 3.5 — hangup 触发归档（fire-and-forget）
- **改动文件**：`agent-flow/main.py::_on_channel_hangup`
- **做什么**：在 `update_call_session_end` 之后、`_call_registry.cancel_call(uuid)` 之前，若 `active` 存在：`task = asyncio.create_task(_archive_recording(uuid, active.biz_type, active.tenant_id, active.user_key)); task.add_done_callback(lambda t: t.exception() and logger.error("[%s] _archive_recording failed: %s", uuid, t.exception()))`。注意：`cancel_call` 在归档 task 之前调用会清掉 registry，但归档已捕获参数，无依赖问题——顺序上先 `create_task`（快）再 `cancel_call`
- **验证**：真实呼入挂断后 → `SELECT kind,storage,uri,size_bytes FROM callbot.call_artifact WHERE call_id='<uuid>'` 见 recording 行，uri 形如 `recordings/{date}/{uuid}.wav`；MinIO 控制台见对应 object；通话挂断流程无延迟（归档异步）

### Step 3.6 — Task 3 整体验证
- **改动文件**：无
- **验证**：完整一通呼入（answer → 几轮对话 → 挂断）：call_session 有 start+end 行、call_artifact 有 recording 行、`flow.log` 与 `freeswitch.log` 无异常；通话过程音频/LLM/TTS 全部正常。**人为制造 PG 故障**（`docker stop callbot-postgres`）再呼入一通，通话仍正常完成（接线点报 error 日志但不阻断），`docker start callbot-postgres` 恢复

---

## Task 4：每轮 turn + 事件接线

> 依赖 Task 3。接线点：`flow.run_streaming_pipeline`（flow.py:496 save_turn 旁）、`handler._execute_terminal_action`（handler.py:628-641）、handler 主循环 barge-in 分支（handler.py:174-189）。

### Step 4.1 — flow.py 每轮 turn PG 双写
- **改动文件**：`agent-flow/src/graph/flow.py::run_streaming_pipeline`
- **做什么**：顶部 `from storage import repository`；在 `await save_turn(call_id, biz_type, state.get("user_input", ""), full_text)`（flow.py:496）之后加 PG 双写（仅 `if full_text.strip():` 块内）。写一个 helper `_fire_turn(call_id, biz_type, user_key, role, text):` 内部 `task = asyncio.create_task(repository.insert_turn(...)); task.add_done_callback(lambda t: t.exception() and logger.error(...))`，调两次（role='user'/text=user_input, role='assistant'/text=full_text）。call_id=fs_uuid=state['call_id']，user_id=user_key=state.get('user_key','')。空轮（user_input 与 full_text 均空）跳过，与 save_turn 一致
- **验证**：真实呼入多轮对话 → `SELECT role,text FROM callbot.call_turn WHERE call_id='<uuid>' ORDER BY ts` 见 user/assistant 交替行；Redis `cb:chat:<biz_type>:<uuid>` 仍正常（双写不冲突）；通话无阻断

### Step 4.2 — handler.py terminal action 事件
- **改动文件**：`agent-flow/src/ws/handler.py::_execute_terminal_action`
- **做什么**：顶部 `from storage import repository`（handler.py 已 `from storage import minio_storage`，同一包）；`_execute_terminal_action(self, action, call_id)` 内：action='handoff' 分支（handler.py:637-639）在 `self._esl.transfer` 前加 fire-and-forget `repository.insert_event(call_id=call_id, fs_uuid=call_id, biz_type=?, ...)`——注意此函数无 biz_type/user_key 参数，需从 `self._registry.get(call_id)` 取 ActiveCall 填充，或扩展函数签名传入。action='end' 分支同理写 `event_type='hangup_by_bot'`。用 helper `_fire_event(call_id, event_type, payload)` 封装 fire-and-forget。biz_type/user_key 取不到时用空串/None（事件记录不阻断）
- **验证**：触发 handoff（LLM action=handoff）→ `SELECT event_type FROM callbot.call_event WHERE call_id='<uuid>'` 见 handoff 行；触发 end → 见 hangup_by_bot 行

### Step 4.3 — handler.py barge-in 事件
- **改动文件**：`agent-flow/src/ws/handler.py` 主循环
- **做什么**：在 `if barge_detected:` 分支内（handler.py:174-189，`tts_buffer.clear()` 之后、`_cancel_asr_stream` 附近）加 `self._fire_event(call_id, 'barge_in', {'turn': turn_count})`（fire-and-forget）。call_id/biz_type/user_key 此时在 handle() 作用域可用
- **验证**：通话中人为打断 AI（说话）→ `SELECT event_type,payload FROM callbot.call_event WHERE call_id='<uuid>'` 见 barge_in 行，payload.turn 为当前轮号；打断流程无延迟

### Step 4.4 — _fire_event / _fire_turn helper 抽取
- **改动文件**：`agent-flow/src/ws/handler.py` + `agent-flow/src/graph/flow.py`
- **做什么**：若 Step 4.1/4.2/4.3 的 fire-and-forget 模式重复，抽成共享 helper（如 `storage/persistence_helpers.py` 的 `fire_insert_turn(...)` / `fire_insert_event(...)`），避免三处重复 add_done_callback 样板。helper 内统一 `asyncio.create_task(repository.xxx(...))` + `add_done_callback` 记日志。**原则：三行相似代码可容忍，超过则抽**（CLAUDE.md）
- **验证**：`PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.ws.handler import StreamingCallHandler; from src.graph.flow import run_streaming_pipeline; print('ok')"` 导入无错

### Step 4.5 — Task 4 整体验证
- **改动文件**：无
- **验证**：真实呼入完整流程（多轮 + 一次 barge-in + 最终挂断）：call_turn 见全部轮次 user/assistant 行、call_event 见 barge_in 行、（若触发）handoff/hangup_by_bot 行；通话全程音频/LLM/TTS/barge-in 无异常；`docker stop callbot-postgres` 期间通话仍正常（fire-and-forget 报错不阻断）

---

## Task 5：console 权限码 + schema 只读映射

> console 读侧地基，Task 6/7 依赖。无 DDL 变更（agent-flow alembic 已建四表）。

### Step 5.1 — permissions.ts 加 call:view
- **改动文件**：`console/server/src/lib/permissions.ts`
- **做什么**：`PermissionCode` 联合类型加 `| 'call:view'`；`ROLE_PERMISSIONS` 的 `admin`/`editor`/`viewer` 数组各加 `'call:view'`（platform_admin 经 `hasPermission` 短路自动通过，无需列）
- **验证**：`cd console/server && npm run lint`（tsc --noEmit）无错；`hasPermission('admin','call:view')===true`、`hasPermission('viewer','call:view')===true`

### Step 5.2 — schema.ts callSession 映射
- **改动文件**：`console/server/src/db/schema.ts`
- **做什么**：`callbot.table('call_session', {...})`，TS 属性 camelCase → DB 列 snake_case，与 models.py CallSession（models.py:16-54）严格对齐：`id: bigserial('id').primaryKey()`、`userId: text('user_id').notNull()`、`callId: text('call_id').notNull()`（注：models 是 UUID 类型，Drizzle 用 text 即可，alembic 已建表不改）、`fsUuid: text('fs_uuid').notNull()`、`tenantId: text('tenant_id')`、`bizType: text('biz_type').notNull()`、`scenario: text('scenario')`、`taskId: text('task_id')`、`phoneHash: text('phone_hash').notNull()`、`userKey: text('user_key').notNull()`、`phoneMasked: text('phone_masked')`、`startTs: timestamp('start_ts',{withTimezone:true}).notNull()`、`endTs: timestamp('end_ts',{withTimezone:true})`、`resultCode: text('result_code')`、`hangupCause: text('hangup_cause')`、`identityVerified: boolean('identity_verified').notNull().default(false)`、`verifyAttempts: integer('verify_attempts').notNull().default(0)`、`recordingNoticePlayed: boolean('recording_notice_played').notNull().default(false)` + 审计列。**不声明 PrimaryKeyConstraint/Index**（DDL 由 alembic 维护）。导出 `type CallSession`
- **验证**：`npm run lint` 无错

### Step 5.3 — schema.ts callTurn 映射
- **改动文件**：`console/server/src/db/schema.ts`
- **做什么**：`callbot.table('call_turn', {...})` 对齐 models.py CallTurn（57-87）：`id`、`userId`、`callId`、`fsUuid`、`bizType`、`userKey`、`role`、`text: text('text')`、`asrConf: real('asr_conf')`（用 `real` 或 `float8`，Drizzle 无 float 用 `real`）、`startMs: integer('start_ms')`、`endMs: integer('end_ms')`、`ts`、审计列。导出 type
- **验证**：`npm run lint` 无错

### Step 5.4 — schema.ts callEvent 映射
- **改动文件**：`console/server/src/db/schema.ts`
- **做什么**：`callbot.table('call_event', {...})` 对齐 models.py CallEvent（90-114）：`id`、`userId`、`callId`、`fsUuid`、`bizType`、`userKey`、`eventType: text('event_type').notNull()`、`payload: jsonb('payload').notNull()`、`ts`、审计列。导出 type
- **验证**：`npm run lint` 无错

### Step 5.5 — schema.ts callArtifact 映射
- **改动文件**：`console/server/src/db/schema.ts`
- **做什么**：`callbot.table('call_artifact', {...})` 对齐 models.py CallArtifact（117-145）：`id`、`userId`、`callId`、`fsUuid`、`bizType`、`userKey`、`kind: text('kind').notNull()`、`storage: text('storage').notNull()`、`uri: text('uri').notNull()`、`sha256: text('sha256')`、`sizeBytes: bigserial('size_bytes')`（用 bigint，`bigserial({mode:'number'})` 读已存行可用 bigserial 但更准确用 `bigint`；参照已有用法）、`contentType: text('content_type')`、`ts`、审计列。导出 type
- **验证**：`npm run lint` 无错

### Step 5.6 — 列名对齐校验
- **改动文件**：无
- **验证**：`docker exec callbot-postgres psql -U postgres -d callbot -c '\d callbot.call_session'`、`\d callbot.call_turn`、`\d callbot.call_event`、`\d callbot.call_artifact` 输出与 schema.ts 映射列名逐一比对，确保 snake_case DB 列完全一致（杜绝双词汇表）

---

## Task 6：console 通话记录 API

> 依赖 Task 5。新增 lib/calls-service.ts + 3 route。参照 `routes-service.ts` + `api/inbound-routes/route.ts` 模式。

### Step 6.1 — calls-service.ts 数据层
- **改动文件**：`console/server/src/lib/calls-service.ts`（新建）
- **做什么**：参照 `routes-service.ts` 结构。导出：(1) `listCalls(opts: {tenantId, bizType?, phoneMasked?, startFrom?, startTo?, page, pageSize})`：`SELECT * FROM callSession WHERE tenantId=? [+ 筛选] ORDER BY startTs DESC LIMIT pageSize OFFSET (page-1)*pageSize` + `db.select({count}).from(callSession).where(...)` 算 total；(2) `getCallDetail(id, tenantId)`：先 `SELECT session WHERE id=? AND tenantId=?`（无则 null，含跨租户），再并发 `SELECT turns ORDER BY ts ASC`、`SELECT events ORDER BY ts ASC`、`SELECT artifacts`；(3) `getRecordingArtifact(id, tenantId)`：`SELECT artifact WHERE callId=(session.callId) AND kind='recording'`（用 session 查 artifact，因 artifact 按 call_id 关联）；(4) toDTO 函数脱敏（phone_hash 不透出前端，仅 phone_masked）。用 drizzle `and()/eq()/gte()/lte()/like()/desc()/asc()` + `sql/template` 计算 total
- **验证**：`npm run lint` 无错；逻辑用 vitest 单测覆盖 listCalls 筛选 + getCallDetail 跨租户 null（参考 `tests/lib/` 现有风格，可选）

### Step 6.2 — /api/calls 列表
- **改动文件**：`console/server/src/app/api/calls/route.ts`（新建）
- **做什么**：参照 `api/inbound-routes/route.ts`。`export async function GET(req: Request)`：`const auth = await requirePermission('call:view'); if (isDenial(auth)) return auth;`；解析 query：`bizType/phoneMasked/startFrom/startTo/page(默认1)/pageSize(默认20)`；`const {calls, total} = await listCalls({tenantId: auth.tenantId, ...})`；`return NextResponse.json({calls, total, page, pageSize})`
- **验证**：`curl -b cookie.txt localhost:3001/api/calls` 返回当前租户通话列表；带 `?bizType=marketing&page=1` 筛选生效；未登录 401、无权限 403

### Step 6.3 — /api/calls/:id 详情
- **改动文件**：`console/server/src/app/api/calls/[id]/route.ts`（新建）
- **做什么**：参照 `api/inbound-routes/[id]/route.ts`。`GET(_req, {params})`：`requirePermission('call:view')` → `const {id} = await params` → `const detail = await getCallDetail(Number(id), auth.tenantId)` → `if (!detail) return 404` → `return NextResponse.json(detail)`
- **验证**：`curl localhost:3001/api/calls/<id>` 返回 `{session, turns, events, artifacts}`；跨租户 id 返回 404；turns/events 按 ts 升序

### Step 6.4 — /api/calls/:id/recording-url
- **改动文件**：`console/server/src/app/api/calls/[id]/recording-url/route.ts`（新建）
- **做什么**：`GET`：`requirePermission('call:view')` → 查 session（跨租户 404）→ 查 recording artifact → 无则 `return NextResponse.json({error:'no recording'},{status:404})`；有则需调 agent-flow 生成 presigned URL——**方案**：console 不直连 MinIO（凭证隔离），改由 agent-flow 暴露 `GET /recording-url?call_id=&artifact_id=` 内部端点（鉴权用共享 secret 或内网信任），console 转发；**或**：console 直连 MinIO（与 agent-flow 共享 `MINIO_*` env），直接 `presigned_get_url(artifact.uri)`。选**后者**（简单，console 已有 DB 连接，加 MinIO client 符合现有架构），用 `@minio/client` 或 agent-flow 同款 `minio` npm 包生成 presigned。返回 `{url, expiresIn: 3600}`
- **验证**：有录音的通话 → 返回 `{url}` 可在浏览器播放；无录音 → 404；URL 1h 后过期

### Step 6.5 — Task 6 整体验证
- **改动文件**：无
- **验证**：`npm run lint`；登录 console，用浏览器/curl 测三个端点；多租户：platform_admin 切租户后 `/api/calls` 返回不同租户数据，跨租户详情 id 404

---

## Task 7：console 通话记录 UI

> 依赖 Task 6 API + Task 5.1 菜单。

### Step 7.1 — ConsoleShell 菜单启用
- **改动文件**：`console/server/src/components/ConsoleShell.tsx`
- **做什么**：MENUS 数组 `records` 项（ConsoleShell.tsx:35）改 `{ key:'records', label:'通话记录', icon:FileSpreadsheet, href:'/calls', enabled:true }`（移除 `enabled:false`，去掉"下期"标记）。图标已 import（FileSpreadsheet）
- **验证**：登录后侧栏见「通话记录」可点击（无"下期"灰标）；点击导航 /calls

### Step 7.2 — /calls 列表页
- **改动文件**：`console/server/src/app/calls/page.tsx`（新建）
- **做什么**：参照 `app/inbound-routes/page.tsx`。server component：`const session = await getSession(); if (!session) redirect('/login');` 取 tenantId/userEmail/userName/role → `<ConsoleShell ...><CallRecordsList /></ConsoleShell>`
- **验证**：访问 /calls 渲染列表组件 + 侧栏/顶栏

### Step 7.3 — CallRecordsList 组件
- **改动文件**：`console/server/src/components/CallRecordsList.tsx`（新建）
- **做什么**：client component；参照 `InboundRoutesManager.tsx` 的 fetch + state + flash toast 模式。筛选区（biz_type select、phone_masked input、时间范围 date inputs、查询按钮）；表格列（开始时间 startTs、biz_type、phone_masked、时长 = endTs-startTs 计算、hangupCause）；分页（上一页/下一页 + total）；点击行 `router.push('/calls/<id>')`；空态"暂无通话记录"。`GET /api/calls?...`；样式 slate/indigo 与现有一致
- **验证**：列表展示通话 + 筛选生效 + 分页工作 + 点击进详情；空租户显示空态

### Step 7.4 — /calls/[id] 详情页
- **改动文件**：`console/server/src/app/calls/[id]/page.tsx`（新建）
- **做什么**：参照 inbound-routes 详情模式。server component：`getSession` + `<ConsoleShell><CallDetail id={params.id} /></ConsoleShell>`
- **验证**：访问 /calls/<id> 渲染详情组件

### Step 7.5 — CallDetail 组件
- **改动文件**：`console/server/src/components/CallDetail.tsx`（新建）
- **做什么**：client component；(1) `GET /api/calls/<id>` 加载聚合数据；(2) 顶部录音播放器：`GET /api/calls/<id>/recording-url` → 200 则 `<audio controls src={url}>`，404 则显示"录音未归档"占位；(3) 逐轮对话回放：turns 按 ts ASC，user 右侧气泡（slate）、assistant 左侧气泡（indigo），展示 text；(4) 事件时间线：events 按 ts ASC，列 eventType + payload（barge_in 显示轮号、handoff 显示分机号）。样式与现有详情一致
- **验证**：详情页对话气泡按序 + 事件时间线 + 录音可播放；无录音显示占位

### Step 7.6 — Task 7 整体验证
- **改动文件**：无
- **验证**：`npm run lint`；`pm2 restart console`；登录后完整走查：侧栏→通话记录→列表筛选→详情逐轮回放→录音播放→事件时间线；platform_admin 切租户后数据隔离

---

## Task 8：收尾验证

> 全链路 + 不变量 + 归档。

### Step 8.1 — 端到端真实通话
- **改动文件**：无
- **验证**：真实 SIP 呼入：answer（录音提示音）→ 多轮对话 → 人为 barge-in 一次 → 再几轮 → 挂断。检查：`call_session` start+end 行、`call_turn` 全部轮次 user/assistant 行、`call_event` 含 barge_in 行、`call_artifact` 含 recording 行；console /calls 见该通话；详情页对话回放与实际一致；录音可播放；事件时间线含 barge_in

### Step 8.2 — 不阻断不变量（最高优先级）
- **改动文件**：无
- **验证**：`docker stop callbot-postgres` → 真实呼入一通完整通话（多轮 + barge-in + 挂断）→ 音频/LLM/TTS/barge-in 全部正常 → `flow.log` 仅见 repository insert error 日志（非 traceback 中断）→ `docker start callbot-postgres` → 下一通通话正常落库。**此验证失败则接线点阻断，必须修复**

### Step 8.3 — 多租户隔离
- **改动文件**：无
- **验证**：platform_admin 切到 default → /calls 仅显示 default 租户通话；切到 galaxy_fin → 仅显示 galaxy_fin；用 default 的 call_session.id 在 galaxy_fin 会话下请求 → 404；普通 admin 仅见自己租户

### Step 8.4 — OpenSpec 校验
- **改动文件**：无
- **验证**：`openspec validate add-call-records-and-recording --strict` 通过（Task 5 已通过，改动后复核）

### Step 8.5 — 索引与测试
- **改动文件**：`console/server/tests/lib/`（新增 calls-service 单测，可选）
- **做什么**：`codegraph sync`（main.py/handler.py/flow.py/minio_storage.py/schema.ts 纳入索引）；CRG `build_or_update_graph`；为 calls-service listCalls 筛选 + getCallDetail 跨租户补 vitest 单测（参考 `tests/lib/` 风格）
- **验证**：`cd console/server && npm test` 通过；`codegraph_status` 健康
