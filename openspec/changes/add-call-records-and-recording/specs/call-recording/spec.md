# Spec: 整通通话录音归档（FS record_session + MinIO + artifact 回写）

> 能力：通过 FreeSWITCH dialplan `record_session` 录制每通呼入的双向混音到 `${recordings_dir}/${uuid}.wav`（FS 原生后台录音，agent-flow 存活无关）；CHANNEL_HANGUP 时 agent-flow 异步读取文件、上传 MinIO、回写 `call_artifact(kind='recording')`。录音提示音在 answer 后播放以满足合规，`call_session.recording_notice_played` 标志记录是否播放。

## ADDED Requirements

### Requirement: dialplan record_session 整通录音

系统 SHALL 在 `freeswitch/dialplan/public/00_biz_type.xml` 的呼入 extension 中，于 `answer` 之后、`silence_stream://-1` 保活之前，加入 `record_session ${recordings_dir}/${uuid}.wav` 动作，录制双向混音。文件名 SHALL 使用 FreeSWITCH Channel Unique-ID（`${uuid}`）。

#### Scenario: 呼入触发整通录音
- **WHEN** 一通呼入到达 catch-all dialplan 并 answer
- **THEN** FreeSWITCH SHALL 执行 record_session，将双向混音写入 `${recordings_dir}/${uuid}.wav`
- **AND** 录音 SHALL 在整个通话期间持续，不受 agent-flow / ESL 连接状态影响

#### Scenario: agent-flow 重启录音不中断
- **WHEN** 通话进行中 agent-flow 进程重启或 ESL 断连
- **THEN** FreeSWITCH SHALL 继续录制直到 CHANNEL_HANGUP
- **AND** 录音文件 SHALL 完整（FS 原生后台录音，与 agent-flow 解耦）

### Requirement: 录音提示音合规

系统 SHALL 在 dialplan `answer` 之后、`record_session` 之前播放录音提示音（路径由 `CALLBOT_RECORDING_NOTICE_SOUND` 配置），受 `CALLBOT_RECORDING_NOTICE_ENABLED` 开关控制（本地测试可关）。提示音播放后 `call_session.recording_notice_played` SHALL 置为 true。

#### Scenario: 默认播放录音提示
- **WHEN** `CALLBOT_RECORDING_NOTICE_ENABLED=true`（默认）且呼入 answer
- **THEN** dialplan SHALL 在 record_session 前播放提示音
- **AND** call_session.recording_notice_played SHALL 为 true

#### Scenario: 本地测试关闭提示音
- **WHEN** `CALLBOT_RECORDING_NOTICE_ENABLED=false`
- **THEN** dialplan SHALL 跳过提示音播放
- **AND** call_session.recording_notice_played SHALL 为 false

### Requirement: MinIO 整通录音上传

系统 SHALL 在 `minio_storage.py` 新增 `upload_recording(call_id, wav_bytes, biz_type, tenant_id)` 方法，将整通录音 wav 字节上传到 MinIO，object key 格式为 `recordings/{YYYYMMDD}/{call_id}.wav`。`MINIO_ENDPOINT` 为空时 SHALL 静默跳过（与现有 `save_turn_audio` 一致）。

#### Scenario: 上传整通录音
- **WHEN** CHANNEL_HANGUP 后 `_archive_recording` 读取到 `${recordings_dir}/${uuid}.wav` 文件字节，且 MinIO 已配置
- **THEN** 系统 SHALL 调用 upload_recording 上传 wav 字节
- **AND** object key SHALL 为 `recordings/{YYYYMMDD}/{uuid}.wav`
- **AND** content_type SHALL 为 `audio/wav`

#### Scenario: MinIO 未配置跳过上传
- **WHEN** `MINIO_ENDPOINT` 为空
- **THEN** upload_recording SHALL 静默返回（不抛异常）
- **AND** SHALL NOT 写入 call_artifact

### Requirement: MinIO presigned 下载 URL

系统 SHALL 在 `minio_storage.py` 新增 `presigned_get_url(object_key, expiry=3600)` 方法，生成有效期为 1 小时的 presigned GET URL，供 console 详情页 `<audio>` 播放。MinIO 未配置或 key 不存在时 SHALL 返回 None。

#### Scenario: 生成录音播放 URL
- **WHEN** console 详情页请求某通话录音的播放 URL
- **THEN** 系统 SHALL 调用 presigned_get_url 返回 1h 有效的 MinIO presigned URL
- **AND** URL 过期后 SHALL 不可访问

#### Scenario: 无录音返回 None
- **WHEN** 该通话无 kind='recording' 的 call_artifact 行
- **THEN** presigned URL 接口 SHALL 返回 404（console 显示"录音未归档"）

### Requirement: 录音 artifact 回写

系统 SHALL 在 CHANNEL_HANGUP 时，通过 `main._on_channel_hangup` 异步执行 `_archive_recording`：读取 wav 文件 → upload_recording → `repository.insert_artifact(call_id=uuid, fs_uuid=uuid, kind='recording', storage='minio', uri=<object_key>, size_bytes, content_type='audio/wav')`。整个归档 SHALL 为 fire-and-forget（`asyncio.create_task`），不阻塞 hangup 清理。

#### Scenario: 挂断后录音归档
- **WHEN** CHANNEL_HANGUP 触发，`_archive_recording` 找到 `${uuid}.wav` 文件
- **THEN** 系统 SHALL 上传 MinIO 并写入一行 call_artifact(kind='recording', storage='minio')
- **AND** artifact 行的 call_id/fs_uuid SHALL 等于 uuid
- **AND** uri SHALL 为 MinIO object key（非完整 URL）

#### Scenario: 挂断后 3 秒延时上传
- **WHEN** CHANNEL_HANGUP 触发
- **THEN** `_archive_recording` SHALL 先 `await asyncio.sleep(recording_archive_delay_sec)`（默认 3 秒，等 FS flush 完 wav）
- **AND** 3 秒后 `${uuid}.wav` 仍不存在时 SHALL 记录 warning 日志，不写 artifact

#### Scenario: 归档失败不阻断 hangup
- **WHEN** `_archive_recording`（upload 或 insert_artifact）抛出异常
- **THEN** 系统 SHALL 仅记录日志
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

### Requirement: 录音目录配置

系统 SHALL 新增 `CALLBOT_RECORDINGS_DIR` 配置项（pydantic-settings，`CALLBOT_` 前缀），指向 FreeSWITCH `record_session` 写入的物理目录。该路径 SHALL 与 dialplan `${recordings_dir}` 指向同一物理位置（部署约束）。

#### Scenario: agent-flow 读取录音文件
- **WHEN** `_archive_recording` 需要读取 `${uuid}.wav`
- **THEN** 系统 SHALL 从 `CALLBOT_RECORDINGS_DIR/{uuid}.wav` 读取
- **AND** 该路径 SHALL 与 FS record_session 写入路径一致
