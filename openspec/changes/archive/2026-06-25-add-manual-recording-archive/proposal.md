# Proposal: 新增手动录音归档兜底

## Why

自动录音归档（`main.py` `_archive_recording`，CHANNEL_HANGUP 后 fire-and-forget）在 MinIO 未配置 / 上传失败时会静默跳过（日志 `upload_recording returned None ... 跳过归档`），**无兜底机制**。一旦跳过，该通话在 console 详情页就听不到录音，且无法补救。

但 FreeSWITCH 本地 `recordings_dir/{uuid}.wav`（`config.py:recordings_dir`，由 `uuid_record` 写入）**无定时清理逻辑**——文件一直保留在磁盘上。也就是说，自动归档失败的通话，其录音源文件大概率还在，完全有条件事后补归档，只是目前没有一个触发入口。

## What Changes

1. **agent-flow 新增手动归档 HTTP 接口**：按 call_id（= fs_uuid）读取 `recordings_dir/{uuid}.wav` → `upload_recording` 上传 MinIO → `insert_artifact(kind='recording')`。**复用** `_archive_recording` / `upload_recording` / `insert_artifact` 现有能力，不重复实现上传与落库逻辑。接口路径遵循 CLAUDE.md「见名知意」规范。
2. **错误区分（可重试）**：接口按失败原因返回明确状态——录音文件不存在 / MinIO 未配置或上传失败 / 该通话已存在 recording artifact（幂等，不重复上传）。前端按原因给出对应提示，按钮保留可重试。
3. **console 通话详情页**：`CallDetail`（`console/server/src/components/CallDetail.tsx`）现有「无 recording artifact → 显示未归档」分支增加「手动归档」按钮；点击调用 agent-flow 接口，成功后刷新 detail 自动加载播放器。
4. **触发范围**：仅未归档（无 recording artifact）时显示按钮；已归档通话正常显示播放器，不出现按钮。

## 成功标准

- [ ] 自动归档失败的通话，在 console 详情页点「手动归档」→ 录音出现在播放器可正常回放。
- [ ] 录音文件已不存在时，按钮给出「录音文件已清理」提示，且可重试。
- [ ] MinIO 仍不可用时，按钮给出「归档服务暂不可用」提示，且可重试。
- [ ] 已存在 recording artifact 的通话重复触发不产生重复 artifact（幂等）。
- [ ] 已归档通话详情页不出现「手动归档」按钮（仅未归档时出现）。
- [ ] 自动归档逻辑（`_archive_recording`）本身未被改动。
- [ ] 流式通话路径与现有录音回放功能不受影响。

## 边界（不在范围内）

- 不做列表页批量归档（仅单个通话详情页）。
- 不支持对已归档通话「重新归档/覆盖」（409 幂等仅作并发与重复点击兜底，非面向用户的功能）。
- 不改自动归档逻辑、不改录音声道布局、不改 MinIO 上传/`insert_artifact` 实现。
- agent-flow 接口**不加鉴权**（内网信任，与 `/media/{uuid}` 一致）；console→agent-flow 的具体调用方式（前端直连 vs console 后端转发）在 spec 阶段确定。

## 约束

- 复用现有 `_archive_recording` 的读文件 / 上传 / 落库链路，保持与自动归档一致的 object key 格式与 artifact 字段（`storage='minio'`, `kind='recording'`, `uri=<object key>`）。
- 接口需处理三类失败：文件不存在（FS 已清理）、MinIO 不可用、已归档（并发/重复），分别映射到可区分的 HTTP 状态码。
- 手动归档为同步操作（录音通常 MB 级，秒级完成），接口返回最终结果而非异步任务句柄。
- 多租户隔离：归档时 `tenant_id` / `biz_type` 需从 `call_session` 取并透传给 `upload_recording` / `insert_artifact`（与自动归档一致）。
