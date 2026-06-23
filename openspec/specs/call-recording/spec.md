# call-recording Specification

## Purpose
TBD - created by archiving change add-call-records-and-recording. Update Purpose after archive.
## Requirements
### Requirement: agent-flow 自录双声道（CallRecorder）

系统 SHALL 在 `agent-flow/src/ws/call_recorder.py` 提供 `CallRecorder`，在每通通话生命周期内（`handler.handle()` 创建一个实例）累加两路原始 PCM：
- **caller（upstream）**：`feed_caller(frame)`，喂接收循环每帧（mod_audio_fork 实时转发的 caller 原始 PCM，VAD 门控之前，全部帧不丢音）
- **AI（downstream）**：`feed_ai(pcm)`，喂 streaming 管线 `audio_callback` 的每段 TTS PCM

系统 SHALL 在 `_receive_during_streaming`（barge-in 路径）也喂 caller 帧，覆盖 AI 说话期间的 caller 音频。`recorder` 作为参数在 `handle()` → `_receive_during_streaming` / `_process_streaming_turn` / `_cleanup` 间传递（非 handle 局部跨方法访问）。

#### Scenario: 双声道均有声
- **WHEN** 一通含 caller 说话 + AI 回复的通话结束
- **THEN** 录音 wav 的 L 声道（caller）SHALL 含清晰人声（max 接近 0dB）
- **AND** R 声道（AI）SHALL 含 TTS 人声
- **AND** L/R 相关系数 SHALL 接近 0（两路独立，非回声）

#### Scenario: 短边补静音
- **WHEN** caller 与 AI PCM 长度不等
- **THEN** `finalize_stereo_wav` SHALL 把短边以 int16 静音（0）补齐到长边长度
- **AND** 输出 16kHz 16-bit 立体声 wav（L=caller R=AI 交错）

#### Scenario: 无音频跳过
- **WHEN** 通话全程无 caller 帧也无 AI PCM（`has_audio=False`）
- **THEN** `finalize_stereo_wav` SHALL 返回 None，不写空 wav

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

系统 SHALL 在 CHANNEL_HANGUP 时，通过 `main._on_channel_hangup` 异步执行 `_archive_recording`（fire-and-forget，强引用持有 task 防 GC）：间隔 3 秒 → 读 CallRecorder 写入的 wav → `upload_recording` → `repository.insert_artifact(call_id=uuid, fs_uuid=uuid, kind='recording', storage='minio', uri=<object_key>, size_bytes, content_type='audio/wav')`。

`call_artifact.uri` SHALL 存 **object key**（`recordings/{date}/{uuid}.wav`，永久稳定、endpoint 无关、不过期），**非 presigned URL**（presigned 1h 过期，由 console 播放时现生成）。

#### Scenario: 挂断后录音归档
- **WHEN** CHANNEL_HANGUP 触发，`_archive_recording` 找到 CallRecorder 写入的 `${uuid}.wav`
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

### Requirement: CallRecorder 写入 + 录音目录配置

系统 SHALL 在 `handler._cleanup`（WS 关闭/挂断）时，若 `recorder.has_audio`，调用 `recorder.write_to(path)` 合成立体声 wav 写入 `CALLBOT_RECORDINGS_DIR/{call_id}.wav`，供 `_archive_recording`（同 uuid）读取上传。`CALLBOT_RECORDINGS_DIR`（pydantic-settings，`CALLBOT_` 前缀）SHALL 指向 agent-flow 可写的目录。

> dialplan 不再 `record_session`（FS 录不到 AI 侧，agent-flow 接管）。该目录现为 agent-flow 写入点 + _archive_recording 读取点。

#### Scenario: 挂断写 wav
- **WHEN** WS 关闭且 recorder 含音频
- **THEN** `_cleanup` SHALL 调 recorder.write_to 写立体声 wav 到 `CALLBOT_RECORDINGS_DIR/{call_id}.wav`

#### Scenario: agent-flow 读取录音文件
- **WHEN** `_archive_recording` 需读取 wav
- **THEN** 系统 SHALL 从 `CALLBOT_RECORDINGS_DIR/{call_id}.wav` 读取
- **AND** 该路径 SHALL 与 `_cleanup` 写入路径一致

