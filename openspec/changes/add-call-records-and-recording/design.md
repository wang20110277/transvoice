# Design: 通话记录查看与录音回放 — repository 接线 + FS 整通录音 + console 查看页

> 本文件记录 spec 阶段确认的技术方案。proposal.md 的 8 项决策为本设计输入；探查代码库后收敛出 1 项**新决策**（call_id = fs_uuid 同值填两列）与 1 项**已知约束**（MCP identity 当前禁用）。

## 1. 核心决策（6 条）

| # | 决策 | 取舍 |
|---|------|------|
| 1 | `call_id` = `fs_uuid` = FreeSWITCH `Unique-ID`，**同值填 schema 两列**（UUID 列） | 全链路同一个 uuid：dialplan `record_session ${uuid}`、`_call_registry.register(uuid, ...)`、WS `/media/{uuid}`、`update_call_session_end(fs_uuid, ...)` 全用同一值。无独立"业务 call_id 生成器"——表内两列是 Citus 分布键预留，本期同值填即可，YAGNI 不为假设需求造生成器 |
| 2 | repository 接线埋点：`main._on_channel_answer`（session start）/ `main._on_channel_hangup`（session end + recording artifact + barge-in 事件回填）/ `flow.run_streaming_pipeline`（每轮 user+assistant turn 双写）/ `handler` 关键节点（identity_verified / handoff event） | 与通话生命周期天然对齐，零新状态机；PG 写入 fire-and-forget `asyncio.create_task`，异常只记日志**不阻断通话**（与 Redis `save_turn` 同等级容错） |
| 3 | Redis `save_turn` 与 PG `insert_turn` **双写并存** | 职责不同：Redis 给下一轮 LLM 跨轮热上下文（1h TTL），PG 给 console 审查（永久）。不互相替代 |
| 4 | FS `record_session` 录整通双向混音到 `${recordings_dir}/${uuid}.wav` | FS 原生后台录音，agent-flow / ESL 断了甚至重启照样录完整通，最稳、代码最少（vs ESL `uuid_record` 依赖连接稳定性） |
| 5 | 录音 `call_id`/`fs_uuid` = `${uuid}`（决策 1），CHANNEL_HANGUP 时读文件 → `upload_recording` 上传 MinIO → `insert_artifact(kind='recording', storage='minio', uri=key)` 回写 | `record_session` 执行时业务 call_id 即 uuid，无映射问题；`call_artifact` 表即为此设计（一通话可多 artifact），`insert_artifact` 首次接线 |
| 6 | console 通话记录读侧：`schema.ts` 加 4 表 Drizzle 只读映射（**不改 DDL**，agent-flow alembic 已建表）；`/api/calls` 按 `activeTenantId` 隔离；详情页 presigned URL 1h 播放 | 与 tenants / inbound-routes 隔离一致；列名与 SQLAlchemy 严格对齐杜绝双词汇表 |

## 2. call_id / fs_uuid 关系澄清（决策 1 展开）

**现状**（探查确认）：

```
dialplan CHANNEL_ANSWER
  └─ FS 生成 uuid (Unique-ID)
       └─ ESL CHANNEL_ANSWER event → main._on_channel_answer(event)
            ├─ uuid = event.headers["Unique-ID"]
            ├─ _call_registry.register(uuid, ...)      ← registry 键 = uuid
            └─ esl.audio_fork_start(uuid, ws_url)      ← WS path = /media/{uuid}
                 └─ main.ws_media_fork(ws, call_id=uuid)
                      └─ handler.handle(call_id=uuid, ...)
                           └─ run_pre_llm_phase(call_id=uuid, ...)
```

**全链路 call_id 就是 fs_uuid 就是 uuid**。`call_session.call_id`（UUID 列）与 `call_session.fs_uuid`（UUID 列）在本期**填同一个 uuid 值**。两列并存是为 Citus 水平扩展预留分布键选项，本期不做分片（CLAUDE.md：单表起步）。

> **不做的事**：不造一个独立的"业务 call_id"（如 UUIDv7 时间序）。record_session 用 `${uuid}` 录音，回写时业务 call_id 就是这个 uuid，天然闭环。强行引入第二个标识符会增加 registry 双键映射复杂度，违反 YAGNI。

## 3. 接线埋点位置（精确到文件 + 行）

### 3.1 session 开始 — `main.py::_on_channel_answer`

在 `_call_registry.register(uuid, ...)` 之后（main.py:215 之后）插入：

```python
await repository.insert_call_session({
    "call_id": uuid, "fs_uuid": uuid,          # 决策 1：同值
    "user_id": user_key,                        # 本期 fallback（见 §7）
    "biz_type": biz_type, "tenant_id": tenant_id, "scenario": scenario,
    "phone_hash": sha256(user_key), "user_key": user_key,
    "phone_masked": mask(user_key),             # 138****1234
    "start_ts": datetime.now(),
})
```

> `insert_call_session` 接受 `state_dict`，keys 必须匹配 `CallSession` 列名（models.py:33-54）。未提供的列走 server_default / nullable。

### 3.2 session 结束 + 录音回写 — `main.py::_on_channel_hangup`

当前 `_on_channel_hangup` 只做 `audio_fork_stop` + `cancel_call`。扩展：

```python
async def _on_channel_hangup(event):
    uuid = event.headers.get("Unique-ID", "")
    ...
    hangup_cause = event.headers.get("Hangup-Cause", "")
    result_code = event.headers.get("variable_hangup_cause", "")  
    end_ts = datetime.now()

    # 1. session end
    await repository.update_call_session_end(uuid, end_ts, hangup_cause, result_code)
    # 2. recording 上传 + artifact 回写（fire-and-forget，不阻塞 hangup 清理）
    asyncio.create_task(_archive_recording(uuid, biz_type, tenant_id, user_key))

    _call_registry.cancel_call(uuid)
```

`_archive_recording` 读 `${recordings_dir}/${uuid}.wav`（路径配置 §6）→ `upload_recording` → `insert_artifact`。**registry 在 hangup 时取 biz_type/tenant_id/user_key**——当前 `_on_channel_hangup` 只从 event 取 uuid，需从 `_call_registry.get(uuid)` 读出 `ActiveCall` 的 biz_type/user_key/tenant_id（这些在 answer 时已注册）。

### 3.3 每轮对话 — `flow.py::run_streaming_pipeline`

在 `await save_turn(...)`（flow.py:496）旁加 PG 双写：

```python
# Redis：下一轮 LLM 热上下文
await save_turn(call_id, biz_type, state.get("user_input", ""), full_text)

# PG：console 审查（fire-and-forget 双写）
asyncio.create_task(repository.insert_turn(
    call_id=call_id, fs_uuid=call_id,         # 决策 1
    biz_type=biz_type, user_id=state.get("user_key", ""),
    user_key=state.get("user_key", ""),
    role="user", text=state.get("user_input", ""),
    asr_conf=state.get("asr_conf"),
))
asyncio.create_task(repository.insert_turn(
    call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
    user_id=user_key, user_key=user_key,
    role="assistant", text=full_text,
))
```

> `asr_conf` 当前 `run_pre_llm_phase` 未透传到 state——本期可选填 None；canonical 透传留待 align-prompt-config 同期或后续。`insert_turn` 签名已含 `asr_conf: float | None`。

### 3.4 关键事件 — `handler.py`

barge-in / handoff / end 等 terminal action 触发时写 `call_event`：

- **handoff**：`_execute_terminal_action(action='handoff')` 内 → `insert_event(event_type='handoff', payload={extension})`
- **end**：`_execute_terminal_action(action='end')` 内 → `insert_event(event_type='hangup_by_bot', payload={})`
- **barge-in**：handler 主循环 barge_detected 分支（handler.py:174）→ `insert_event(event_type='barge_in', payload={turn})`
- **identity_verified**：identity 核验逻辑（当前 MCP 禁用，本期 event 占位，canonical 待 MCP 重启）

> event 写入**不阻塞通话流**，全部 fire-and-forget。事件 payload 是 JSONB，存上下文（turn 号、extension、hangup cause 等）。

## 4. 录音链路（完整时序）

```
① dialplan CHANNEL_ANSWER
   ├─ answer
   ├─ playback 录音提示音（recording_notice_played 标志）
   ├─ record_session ${recordings_dir}/${uuid}.wav   ← FS 原生后台录音
   └─ playback silence_stream://-1                    ← 保活

② 通话进行中：FS 持续写 ${uuid}.wav（双向混音），agent-flow 存活无关

③ CHANNEL_HANGUP
   ├─ FS flush 关闭 ${uuid}.wav 文件
   └─ ESL event → main._on_channel_hangup(uuid)
        ├─ repository.update_call_session_end(uuid, end_ts, hangup_cause, result_code)
        ├─ asyncio.create_task(_archive_recording(uuid, ...))
        │     ├─ 读 ${recordings_dir}/${uuid}.wav（路径 §6）
        │     ├─ minio_storage.upload_recording(wav_bytes, call_id=uuid, biz_type, tenant_id)
        │     │     └─ object key = recordings/{date}/{uuid}.wav
        │     └─ repository.insert_artifact(
        │           call_id=uuid, fs_uuid=uuid, kind='recording',
        │           storage='minio', uri=key, size_bytes, content_type='audio/wav',
        │       )
        ├─ esl.audio_fork_stop(uuid)
        └─ _call_registry.cancel_call(uuid)
```

> **时序注意**：`record_session` 文件在 CHANNEL_HANGUP 后才 flush 完成。`_archive_recording` 用 `asyncio.create_task` 异步执行，自然延后于 hangup 清理；若文件尚未就绪，加短重试（最多 3 次 × 0.5s）。MinIO 未配置时跳过上传（与 `save_turn_audio` 同样 `if not MINIO_ENDPOINT: return`）。

## 5. console 读侧（Drizzle 只读映射 + API + UI）

### 5.1 schema.ts 加 4 表映射（不改 DDL，agent-flow alembic 已建表）

```typescript
export const callSession = callbot.table('call_session', { ...列名与 models.py CallSession 严格一致... });
export const callTurn     = callbot.table('call_turn', { ... });
export const callEvent    = callbot.table('call_event', { ... });
export const callArtifact = callbot.table('call_artifact', { ... });
```

列名映射约定（camelCase TS 属性 → snake_case DB 列，与 promptConfig/inboundRoute 一致）：
- `callId: text('call_id')`、`fsUuid: text('fs_uuid')`、`bizType: text('biz_type')`
- `tenantId: text('tenant_id')`、`phoneMasked: text('phone_masked')`、`startTs: timestamp('start_ts')`
- 不复制 PrimaryKeyConstraint / Index（Drizzle 映射只读，索引由 alembic DDL 维护）

### 5.2 API（按 activeTenantId 隔离）

| Method | Path | 权限码 | 行为 |
|--------|------|--------|------|
| GET | `/api/calls` | `call:view` | 列表：`WHERE tenant_id = activeTenantId`，支持 biz_type / 时间范围 / phone_masked 模糊筛选 + 分页（LIMIT/OFFSET），ORDER BY start_ts DESC |
| GET | `/api/calls/:id` | `call:view` | 详情聚合：session + turns（按 ts ASC）+ events（按 ts ASC）+ artifacts（kind='recording'） |
| GET | `/api/calls/:id/recording-url` | `call:view` | 返回 MinIO presigned URL（1h），供前端 `<audio>` 播放；无录音返回 404 |

> **`:id` 是 call_session.id（bigserial）**，前端列表项带 id；详情聚合内 call_id/fs_uuid 透出供调试。隔离键用 `tenant_id`（CallSession.tenant_id 在 answer 时已写入）。

### 5.3 UI

- **菜单**：`ConsoleShell.MENUS` 的 `records`（通话记录）从 `enabled: false`（下期）改为 `enabled: true`，`href: '/calls'`
- **/calls 列表页**：表格（开始时间 / biz_type / 手机号 / 时长 / 状态）+ 筛选（biz_type 下拉、时间范围、手机号搜索）+ 分页 + 点击进详情
- **/calls/[id] 详情页**：上半逐轮对话回放（call_turn 按 ts 排序，user/assistant 交替气泡）+ 下半事件流（call_event 时间线）+ 顶部录音播放器（`<audio src={presignedUrl}>`，加载调 `/api/calls/:id/recording-url`）

## 6. 新增配置项（CALLBOT_ 前缀，pydantic-settings）

| 配置 | 默认 | 说明 |
|------|------|------|
| `CALLBOT_RECORDINGS_DIR` | `/Users/lindaw/freeswitch/var/lib/freeswitch/recordings` | FS 录音文件目录（`record_session` 写入路径，agent-flow 读取路径） |
| `CALLBOT_RECORDING_NOTICE_ENABLED` | `true` | dialplan 是否播放录音提示音；本地测试可关 |
| `CALLBOT_RECORDING_ARCHIVE_TIMEOUT` | `30` | `_archive_recording` 上传超时（秒） |
| `CALLBOT_RECORDING_NOTICE_SOUND` | `ivr/recording_notice.wav` | 提示音文件路径 |

> dialplan 侧 `${recordings_dir}` 是 FS 内置变量（默认 `$${base_dir}/recordings`），与 agent-flow `CALLBOT_RECORDINGS_DIR` 必须指向**同一物理路径**（部署文档注明）。

## 7. 已知约束：MCP identity 当前禁用

**现状**：`flow.py:334-344` 的 MCP 身份查询 + 征信查询 + 记忆召回 + RAG 检索的 `asyncio.gather` 扇出**整块被注释禁用**（TODO: re-enable after fixing MCP phone format + RedisSearch + Ollama structured_output）。`run_pre_llm_phase` 当前只做 ASR + 加载 Redis chat history。

**影响**：CHANNEL_ANSWER 时**无法得到 canonical `user_id`**（需 MCP `query_user_identity(user_key, biz_type).user_id`）。

**本期处理**（YAGNI，不修复 MCP）：
- `user_id` 字段 fallback = `user_key`（主叫手机号明文，与 callbot schema `user_id TEXT` 类型兼容）
- `phone_hash` = `sha256(user_key)`（脱敏存储，供跨通话关联同一用户）
- `phone_masked` = 中间 4 位掩码（138****1234）
- **call_session.user_id 暂非 canonical**——MCP 重启后续变更（独立于本变更）会把 CHANNEL_ANSWER → MCP identity 串起来，回填 canonical user_id。本变更的接线点（§3.1）预留 user_key→user_id 升级位置，不阻塞。

> 此约束**不阻塞通话记录功能**：console 按 phone_masked / user_key 筛选、逐轮回放、录音播放全部可用。canonical user_id 关联是增量优化。

## 8. 录音告知合规

dialplan `answer` 后、`record_session` 前播放提示音：

```xml
<action application="answer"/>
<action application="playback" data="${CALLBOT_RECORDING_NOTICE_SOUND}" cond="${recording_notice_enabled}"/>
<action application="record_session" data="${recordings_dir}/${uuid}.wav"/>
<action application="playback" data="silence_stream://-1"/>
```

`recording_notice_played` 字段（models.py:50 已预留）在 `insert_call_session` 时按 `CALLBOT_RECORDING_NOTICE_ENABLED` 写入。

## 9. 边界与风险

- **不阻断通话（最高优先级不变量）**：所有 PG 写入 fire-and-forget（`asyncio.create_task`），异常只记日志。接线点的 DB 异常**绝不能**影响音频流 / LLM / TTS。与 Redis `save_turn` 的 `except Exception: logger.warning` 同等级容错。
- **DB 写入失败可见性**：fire-and-forget 的 task 异常默认被吞。需在 `create_task` 后挂 `task.add_done_callback` 记日志（或包一层 try/except），避免静默丢数据。
- **录音时序竞态**：CHANNEL_HANGUP 后 FS flush wav 文件有延迟。`_archive_recording` 需短重试（§4）。
- **MinIO 未配置**：`upload_recording` 在 `MINIO_ENDPOINT` 为空时跳过（与 `save_turn_audio` 一致），artifact 不写。console 详情页显示"录音未归档"。
- **dialplan 变更需 reload**：FS `reloadxml` 或重启。`scripts/local.sh fs` 覆盖。
- **破坏性低**：纯新增接线 + 新增读侧，不改现有音频流 / LLM / TTS / ESL 逻辑。PG 写入是新行为，对现有通话零语义变化。
- **MCP 禁用约束**（§7）：user_id 非 canonical，本期可接受；不阻塞 console 功能。

## 10. 不包含（重申 proposal 范围）

- 逐句 ASR/TTS 音频归档（现有 `save_turn_audio` 不增强）
- 实时通话监听（live tapping）
- 录音转写 / 质检评分 / 敏感词检测（后续特性）
- 呼出 outbound 录音（本期聚焦 inbound catch-all）
- MCP identity 重启 / canonical user_id 回填（独立后续变更）
