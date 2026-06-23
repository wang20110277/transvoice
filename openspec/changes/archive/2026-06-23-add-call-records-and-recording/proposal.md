# Proposal: 通话记录查看与录音回放 — repository 接线 + FS 整通录音 + console 查看页

## 背景

智能外呼系统的通话数据链路存在三处断裂,运营无法在 console 审查或回放任何一通真实通话:

- **flow 端 repository 写好但零接线**:`agent-flow/src/storage/repository.py` 定义了 `insert_call_session` / `insert_turn` / `insert_event` / `insert_artifact` / `update_call_session_end` 等写入方法,对应 `callbot.call_session` / `call_turn` / `call_event` / `call_artifact` 四张表(alembic 已建表)。但全仓 grep 显示这些方法**零调用、零导入** — `handler.py` 通话生命周期与 `flow.py` 每轮管线都从未写入 PG,四表永远为空。通话内容目前只短暂存在于 Redis(`cb:chat:{biz_type}:{call_id}`,TTL 1h,`chat_history.save_turn`)和 `flow.log` 文本日志里。
- **无整通录音归档**:FreeSWITCH dialplan(`freeswitch/dialplan/public/00_biz_type.xml`)只有 `answer` + `playback silence_stream://-1`,**无 `record_session`**;`$HOME/freeswitch/.../recordings/` 目录为空。`minio_storage.py` 只有逐句的 `save_turn_audio`(upstream/downstream 分片),无整通录音上传。逐句 TTS 拼接无法还原真实通话(缺用户侧音频 + 句间静音间隙)。
- **console 无通话记录页**:`db/schema.ts` 无 call 表 drizzle 映射;`app/api/` 无 `/api/calls`;`app/` 无通话记录页面(已实现租户 / DID 路由 / 提示词管理)。

## 需求

1. **flow repository 首次接线**:在 `main.py` CHANNEL_ANSWER / CHANNEL_HANGUP 与 `handler.py` 通话生命周期埋点,把 `call_session`(开始/结束)、`call_turn`(每轮 user+assistant)、`call_event`(barge-in / handoff / 身份核验等关键节点)写入 PG。Redis `save_turn` 保留作 LLM 跨轮热上下文,与 PG `insert_turn` **双写**(职责不同,前者给下一轮 LLM,后者给 console 审查)。
2. **FS 整通录音归档**:dialplan 加 `record_session` 录双向混音到 `${recordings_dir}/${uuid}.wav`(FS 原生后台录音,agent-flow 存活无关);CHANNEL_HANGUP 时 agent-flow 读文件 → `upload_recording` 上传 MinIO → `insert_artifact(call_id, kind='recording', storage='minio', uri=key)` 回写。
3. **console 通话记录页**:`schema.ts` 加 `callSession` / `callTurn` / `callEvent` / `callArtifact` 映射(列名与 agent-flow SQLAlchemy 严格一致);`/api/calls`(列表,按 tenant / biz_type / 时间 / 手机号筛选,按 `activeTenantId` 隔离) + `/api/calls/[id]`(详情聚合);`app/calls` 列表页 + 详情页(逐轮对话回放 + 录音 `<audio>` 播放器,MinIO presigned URL)。

## 范围

### 包含

- **flow 接线**:`repository.py` 接到 `main.py:_on_channel_hangup`(session end + artifact + event)与 `handler.py` 通话生命周期(session start + 每轮 turn + event);首次让 PG 四表有数据。
- **录音**:dialplan `record_session`;`minio_storage.upload_recording(call_id, wav_bytes, biz_type, tenant_id)` + `presigned_get_url(key, expiry=1h)`;CHANNEL_HANGUP 异步上传 + 回写 `call_artifact(kind='recording')`;文件名用 FS `${uuid}`(= fs_uuid),回写时关联业务 `call_id`(ActiveCallRegistry 映射,二者是表内两个独立列)。
- **console**:`schema.ts` call 表映射;`/api/calls` 列表 + `/api/calls/[id]` 详情;`app/calls` 列表页(筛选/分页) + 详情页(逐轮 + 录音播放器);`layout` 加「通话记录」导航项。
- **录音告知**:dialplan answer 后播放录音提示音再 `record_session`,`call_session.recording_notice_played=true`(字段已预留)。

### 不包含

- **逐句 ASR/TTS 音频归档**:现有 `save_turn_audio` 非本特性目标,不增强。
- **实时通话监听**:只做事后回放,不做 live tapping。
- **录音转写 / 质检评分 / 敏感词检测**:后续特性。
- **呼出(outbound)录音**:本期聚焦呼入 inbound(dialplan catch-all),呼出 originate 链路后续补。

## 决策记录(proposal 阶段确认)

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 录音触发方式 | dialplan `record_session` | FS 原生后台录音,agent-flow / ESL 断了甚至重启照样录完整通;最稳、代码最少(vs ESL `uuid_record` 依赖连接稳定性) |
| 录音地址回写 | `call_artifact` 新增 `kind='recording'` 行 | 表即为此设计,一通话可多 artifact;`repository.insert_artifact` 首次接线(vs `call_session` 加 recording_uri 列) |
| 录音文件命名 | FS `${uuid}`(= fs_uuid)录音,业务 `call_id` 通过 registry 映射回写 | `record_session` 执行时业务 `call_id` 尚未生成;`call_id` / `fs_uuid` 是表内两个独立列,天然支持 |
| 录音格式 | wav(FS 默认,无损) | 本地开发够用;生产后续可切 mp3(需 FS lame 编译) |
| presigned URL | 默认 1h 有效 | 详情页播放够用 |
| 列表权限隔离 | 按 `activeTenantId` 过滤 | 与 tenants / inbound-routes 一致 |
| 录音告知 | dialplan answer 后播提示音,`recording_notice_played=true` | 上线合规;本地测试可用 dialplan 变量关 |
| 变更结构 | 单 change,3 个 capability specs | `call-records-persistence` / `call-recording` / `call-records-console` 逻辑上一体,不拆分为多个 change |
