# call-recording Specification

## ADDED Requirements

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
