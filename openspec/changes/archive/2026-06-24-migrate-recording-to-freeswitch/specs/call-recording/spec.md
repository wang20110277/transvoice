# call-recording Specification

## REMOVED Requirements

### Requirement: agent-flow 自录双声道（CallRecorder）

> 录音下沉到 FreeSWITCH：`CallRecorder` 已删除，双声道录制改由 `uuid_record` 接管（见 ADDED「FreeSWITCH uuid_record 双声道录制」）。

### Requirement: CallRecorder 写入 + 录音目录配置

> 录音写入方从 `CallRecorder` 变为 FreeSWITCH `uuid_record`（见 ADDED「FreeSWITCH 录音写入 + 录音目录配置」）。`CALLBOT_RECORDINGS_DIR` 语义不变（仍是写入点 + `_archive_recording` 读取点）。

## ADDED Requirements

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

## MODIFIED Requirements

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
