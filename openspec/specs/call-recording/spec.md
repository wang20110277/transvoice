# call-recording Specification

## Purpose
TBD - created by archiving change add-call-records-and-recording. Update Purpose after archive.
## Requirements
### Requirement: 录音提示音合规

系统 SHALL 在 dialplan `answer` 之后播放录音提示音（路径由 `CALLBOT_RECORDING_NOTICE_SOUND` 配置），受 `CALLBOT_RECORDING_NOTICE_ENABLED` 开关控制（本地测试可注释 dialplan 行关闭）。提示音播放后 `call_session.recording_notice_played` SHALL 置为 `settings.recording_notice_enabled`。

#### Scenario: 默认播放录音提示
- **WHEN** `CALLBOT_RECORDING_NOTICE_ENABLED=true`（默认）且 dialplan 提示音行未注释
- **THEN** dialplan SHALL 在 answer 后播放提示音
- **AND** call_session.recording_notice_played SHALL 为 true

#### Scenario: 本地测试关闭提示音
- **WHEN** `CALLBOT_RECORDING_NOTICE_ENABLED=false` 或 dialplan 提示音行被注释
- **THEN** SHALL 跳过提示音播放
- **AND** call_session.recording_notice_played SHALL 为 false

### Requirement: MinIO 整通录音上传

系统 SHALL 在 `minio_storage.py` 提供 `upload_recording(call_id, wav_bytes, biz_type, tenant_id)` 方法，将整通录音 wav 字节上传到 MinIO，object key 格式为 `recordings/{YYYYMMDD}/{call_id}.wav`。`MINIO_ENDPOINT` 为空时 SHALL 静默跳过。

> MINIO_* env 经 `main.py` 顶部 `load_dotenv()` 加载到 os.environ（pydantic `CALLBOT_` 前缀不加载无前缀的 MINIO_*，故需 load_dotenv；否则 upload_recording 静默返回 None）。

#### Scenario: 上传整通录音
- **WHEN** CHANNEL_HANGUP 后 `_archive_recording` 读取到 CallRecorder 写入的 `${recordings_dir}/${uuid}.wav`，且 MinIO 已配置
- **THEN** 系统 SHALL 调用 upload_recording 上传 wav 字节
- **AND** object key SHALL 为 `recordings/{YYYYMMDD}/{uuid}.wav`
- **AND** content_type SHALL 为 `audio/wav`

#### Scenario: MinIO 未配置跳过上传
- **WHEN** `MINIO_ENDPOINT` 为空（env 未加载）
- **THEN** upload_recording SHALL 静默返回 None
- **AND** SHALL NOT 写入 call_artifact（_archive_recording 记 info 日志说明跳过）

### Requirement: console presigned 下载 URL

系统 SHALL 在 `console/server/src/lib/minio-client.ts` 提供 `presignedRecordingUrl(object_key, expirySec=3600)`，从 DB 的 artifact.uri（object key）生成 1h 有效的 MinIO presigned GET URL，供 console 详情页 `<audio>` 播放。console 经 `.env.local`（Next.js 自动加载）读取 MINIO_* env；未配置或异常返回 null。

#### Scenario: 生成录音播放 URL
- **WHEN** console 详情页请求某通话录音的播放 URL（`GET /api/calls/:id/recording-url`）
- **THEN** 系统 SHALL 读 call_artifact.uri（object key）→ presignedRecordingUrl 生成 1h 签名 URL
- **AND** 返回 `{url, expiresIn: 3600}`，URL 形如 `http://<endpoint>/<bucket>/<key>?X-Amz-Signature=...`

#### Scenario: 无录音 404
- **WHEN** 该通话无 kind='recording' 的 call_artifact 行，或 MinIO 未配置
- **THEN** recording-url 接口 SHALL 返回 404（console 显示"录音未归档"）

### Requirement: 录音 artifact 回写（DB 存 object key）

系统 SHALL 在 CHANNEL_HANGUP 时，通过 `main._on_channel_hangup` 异步执行 `_archive_recording`（fire-and-forget，强引用持有 task 防 GC）：间隔 3 秒 → 读 FreeSWITCH `uuid_record` 写入的 wav → `upload_recording` → `repository.insert_artifact(call_id=uuid, fs_uuid=uuid, kind='recording', storage='minio', uri=<object_key>, size_bytes, content_type='audio/wav')`。

`call_artifact.uri` SHALL 存 **object key**（`recordings/{date}/{uuid}.wav`，永久稳定、endpoint 无关、不过期），**非 presigned URL**（presigned 1h 过期，由 console 播放时现生成）。

#### Scenario: 挂断后录音归档
- **WHEN** CHANNEL_HANGUP 触发，`_archive_recording` 找到 FreeSWITCH 写入的 `${uuid}.wav`
- **THEN** 系统 SHALL 上传 MinIO 并写入一行 call_artifact(kind='recording', storage='minio')
- **AND** artifact 行的 call_id/fs_uuid SHALL 等于 uuid
- **AND** uri SHALL 为 MinIO object key（非完整 URL）

#### Scenario: 挂断后 3 秒延时
- **WHEN** CHANNEL_HANGUP 触发
- **THEN** `_archive_recording` SHALL 先 `await asyncio.sleep(recording_archive_delay_sec)`（默认 3）
- **AND** 3 秒后 wav 仍不存在时 SHALL 记 warning 日志，不写 artifact

#### Scenario: 归档失败不阻断 hangup
- **WHEN** `_archive_recording`（upload 或 insert_artifact）抛出异常
- **THEN** 系统 SHALL 仅记录日志（task 的 done_callback 记 error）
- **AND** SHALL NOT 阻塞 audio_fork_stop / cancel_call / 下一通通话

### Requirement: hangup 时读取 ActiveCall 上下文

系统 SHALL 在 `_on_channel_hangup` 中通过 `_call_registry.get(uuid)` 读取该通话的 `biz_type`/`user_key`/`tenant_id`（CHANNEL_ANSWER 时已注册），用于 session end 更新与 recording artifact 回写。registry 无该 uuid 时 SHALL 优雅降级（跳过 PG 写入，仅做 audio_fork_stop + cancel）。

#### Scenario: 正常挂断读取上下文
- **WHEN** CHANNEL_HANGUP 触发且 registry 存在该 uuid 的 ActiveCall
- **THEN** 系统 SHALL 从 ActiveCall 取得 biz_type/user_key/tenant_id 用于 recording 归档

#### Scenario: registry 无记录降级
- **WHEN** CHANNEL_HANGUP 触发但 registry 无该 uuid（如 answer 未到达 agent-flow）
- **THEN** 系统 SHALL 跳过 update_call_session_end 与 _archive_recording
- **AND** SHALL 仍执行 audio_fork_stop + cancel_call（幂等）

### Requirement: FreeSWITCH uuid_record 双声道录制

系统 SHALL 由 FreeSWITCH 完成整通双声道录音，替代应用层 `CallRecorder`。在 `_on_channel_answer` 中，`audio_fork_start` 成功后，系统 SHALL 紧接着调用 `esl.record_start(uuid, recordings/{uuid}.wav)`（内部先 `uuid_setvar RECORD_STEREO=true` 再 `uuid_record start`），使 record media_bug 排在 mod_audio_fork 的 `WRITE_REPLACE` bug **之后**注册，从而在 write 方向 tap 到被 `dub_speech_frame` 替换后的 AI 下行音频。

系统 SHALL 完全删除 `agent-flow/src/ws/call_recorder.py`（`CallRecorder` 类、`feed_caller`/`feed_ai`/`finalize_stereo_wav`/`has_audio`）及其在 `handler.py` 的所有引用（import、调用、`recorder` 参数透传），录音不再由应用层累加。

#### Scenario: 双声道均有声
- **WHEN** 一通含 caller 说话 + AI 回复的通话结束
- **THEN** FreeSWITCH 录制的 `recordings/{uuid}.wav` SHALL 为立体声（2 channels）
- **AND** L 声道（caller 上行）SHALL 含清晰人声
- **AND** R 声道（AI 下行）SHALL 含 TTS 人声
- **AND** 声道布局 SHALL 与既有归档一致（L=caller / R=AI），console 播放不受影响

#### Scenario: record bug 顺序保证
- **WHEN** CHANNEL_ANSWER 触发且 `audio_fork_start` 成功
- **THEN** 系统 SHALL 在同一处理流程内紧接着（await 串行）调用 `record_start`
- **AND** record bug SHALL 排在 audio_fork 的 WRITE_REPLACE bug 之后注册
- **AND** SHALL NOT 在 dialplan 中使用 record_session（会抢在 audio_fork 前注册，录不到 AI）

#### Scenario: FS 录制失败不阻断
- **WHEN** `uuid_record start` 抛出异常（FS 拒绝 / 通道异常）
- **THEN** 系统 SHALL 仅记 error 日志（non-fatal）
- **AND** SHALL NOT 阻断 audio_fork / WS / 通话音频流
- **AND** 该通 SHALL 无录音归档（接受单点，无 app fallback）

### Requirement: FreeSWITCH 录音写入 + 录音目录配置

`CALLBOT_RECORDINGS_DIR`（pydantic-settings，`CALLBOT_` 前缀）SHALL 为 FreeSWITCH 可写的目录，作为 `uuid_record` 写入点与 `_archive_recording` 读取点。系统 SHALL 在 `_on_channel_hangup` 的 `audio_fork_stop` 之后调用 `esl.record_stop(uuid)` 触发 flush；channel 已释放时 stop 失败 SHALL 被忽略（FS 挂断本会自动停止 record bug 并 flush）。

dialplan SHALL NOT 使用 `record_session`（录音由 agent-flow 通过 `uuid_record` API 在 `audio_fork_start` 之后触发，以确保 record bug 排在 WRITE_REPLACE 之后）。`handler._cleanup` SHALL NOT 再写 wav（`CallRecorder` 已删除）。

#### Scenario: FS 写入录音文件
- **WHEN** CHANNEL_ANSWER 且 record_start 成功
- **THEN** FreeSWITCH SHALL 通过 uuid_record 写立体声 wav 到 `CALLBOT_RECORDINGS_DIR/{uuid}.wav`

#### Scenario: 挂断停止录制
- **WHEN** CHANNEL_HANGUP 触发
- **THEN** 系统 SHALL 在 audio_fork_stop 后调用 record_stop
- **AND** record_stop 失败（channel 已释放）SHALL 被忽略，不阻断
- **AND** FreeSWITCH SHALL 已在挂断时自动 flush wav

#### Scenario: agent-flow 读取录音文件
- **WHEN** `_archive_recording` 需读取 wav
- **THEN** 系统 SHALL 从 `CALLBOT_RECORDINGS_DIR/{uuid}.wav` 读取（与 FS uuid_record 写入路径一致）

### Requirement: 手动录音归档接口

系统 SHALL 在 agent-flow 提供 `POST /calls/{fs_uuid}/archive-recording` 同步接口，作为自动归档（`_archive_recording`）失败时的手动兜底。接口 SHALL NOT 要求鉴权（内网信任，与 `/media/{uuid}` 一致）。接口 SHALL 按 fs_uuid 重新执行归档链路：反查 `call_session` 取 biz_type/tenant_id/user_key/user_id → 读取 `CALLBOT_RECORDINGS_DIR/{fs_uuid}.wav` → `upload_recording` → `repository.insert_artifact(kind='recording')`。

接口 SHALL 区分四类失败并返回相应 HTTP 状态码：session 不存在(404)、已存在 recording artifact(409 幂等)、录音文件不存在(410)、MinIO 不可用(502)；成功 SHALL 返回 200 + `{objectKey}`。

#### Scenario: 自动归档失败的通话手动补归档
- **WHEN** 一通自动归档失败（无 recording artifact）但 FreeSWITCH 本地 wav 仍存在的通话，收到 `POST /calls/{fs_uuid}/archive-recording`
- **THEN** 系统 SHALL 通过 `get_call_session_by_fs_uuid` 反查 call_session 取 biz_type/tenant_id/user_key/user_id
- **AND** SHALL 读取 `recordings_dir/{fs_uuid}.wav` 上传 MinIO
- **AND** SHALL 写入 call_artifact(kind='recording', storage='minio', uri=<object key>)
- **AND** SHALL 返回 200 `{"objectKey": <key>}`

#### Scenario: 已归档幂等返回 409
- **WHEN** 该 fs_uuid 已存在 kind='recording' 的 call_artifact 行
- **THEN** 系统 SHALL NOT 重复上传或重复写入 artifact
- **AND** SHALL 返回 409 `{"error": "already archived", "objectKey": <uri>}`

#### Scenario: 录音文件已清理返回 410
- **WHEN** `recordings_dir/{fs_uuid}.wav` 不存在（已被清理）
- **THEN** 系统 SHALL 返回 410 `{"error": "recording file not found"}`
- **AND** SHALL NOT 写入 artifact

#### Scenario: MinIO 不可用返回 502
- **WHEN** `upload_recording` 返回 None（MinIO 未配置或上传失败）
- **THEN** 系统 SHALL 返回 502 `{"error": "minio unavailable"}`
- **AND** SHALL NOT 写入 artifact

#### Scenario: session 不存在返回 404
- **WHEN** fs_uuid 在 call_session 中无记录
- **THEN** 系统 SHALL 返回 404 `{"error": "call session not found"}`

#### Scenario: 手动归档不依赖 ActiveCallRegistry
- **WHEN** 通话已挂断（`ActiveCallRegistry` 已清空该 uuid）
- **THEN** 手动归档 SHALL 从 call_session 反查三元组（不依赖 registry）
- **AND** SHALL NOT 与 `_archive_recording` 共享对 registry 的依赖

#### Scenario: 手动归档不影响自动归档
- **WHEN** 手动归档接口被调用
- **THEN** `_archive_recording`（自动归档）的逻辑与 fire-and-forget 行 SHALL 保持不变
- **AND** 手动归档 SHALL 复用与自动归档一致的 object key 格式（`recordings/{YYYYMMDD}/{uuid}.wav`）与 artifact 字段

