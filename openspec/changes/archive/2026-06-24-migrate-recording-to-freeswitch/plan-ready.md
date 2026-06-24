# 实现计划：migrate-recording-to-freeswitch

## 来源
- 提案：openspec/changes/migrate-recording-to-freeswitch/proposal.md
- 设计：openspec/changes/migrate-recording-to-freeswitch/design.md
- 规格：openspec/changes/migrate-recording-to-freeswitch/specs/call-recording/spec.md
- 任务：openspec/changes/migrate-recording-to-freeswitch/tasks.md

## 前置状态
验证补丁代码已在 working tree（带 `[验证补丁]` 标记）：
- `agent-flow/src/clients/esl.py` 的 `record_start` / `record_stop`
- `agent-flow/main.py::_on_channel_answer` 的 `record_start` 调用、`_on_channel_hangup` 的 `record_stop` 调用
- `agent-flow/src/ws/handler.py::_cleanup` 已注释的 app 写盘段

本计划在此基础上**转正式 + 清理 CallRecorder**，非从零实现。

---

## 实现步骤

### Task 1: ESL record 方法转正式
- **目标**：`record_start` / `record_stop` 从验证补丁转为正式代码
- **步骤**：
  1. `agent-flow/src/clients/esl.py`：去掉 `record_start` / `record_stop` 注释里的 `[验证补丁]` 标记，docstring 转正式（保留「必须在 audio_fork_start 之后」的 WHY 与 RECORD_STEREO 说明）
  2. `agent-flow/main.py::_on_channel_answer`：去掉 record_start 段的 `[验证补丁]` 标记，注释转正式
  3. `agent-flow/main.py::_on_channel_hangup`：去掉 record_stop 段的 `[验证补丁]` 标记，注释转正式
- **验证**：`cd agent-flow && python3 -m py_compile main.py src/clients/esl.py` 通过；`grep -rn "\[验证补丁\]" agent-flow/main.py agent-flow/src/clients/esl.py` 无残留

### Task 2: 删除 CallRecorder（依赖 Task 1）
- **目标**：移除应用层自录全套，录音完全由 FS 接管
- **步骤**：
  1. `agent-flow/src/ws/handler.py`：删除 `from ws.call_recorder import CallRecorder`
  2. `agent-flow/src/ws/handler.py`：删除 `recorder = CallRecorder(...)` 实例化
  3. `agent-flow/src/ws/handler.py`：删除 `recorder` 参数透传（`handle` → `_receive_during_streaming` / `_process_streaming_turn` / `_cleanup` 的签名与实参）
  4. `agent-flow/src/ws/handler.py`：删除所有 `recorder.feed_caller(...)` 与 `recorder.feed_ai(...)` 调用
  5. `agent-flow/src/ws/handler.py`：删除 `_cleanup` 中已注释的 app 写盘段（含其上方注释）
  6. `agent-flow/src/ws/call_recorder.py`：删除整个文件
  7. `agent-flow/src/storage/minio_storage.py`：确认 `wrap_wav_header` 是否还有 CallRecorder 之外的引用，若否则一并删除，否则保留
- **验证**：`cd agent-flow && python3 -m py_compile src/ws/handler.py` 通过；`grep -rn "CallRecorder\|call_recorder\|\.recorder\|feed_caller\|feed_ai" src/ main.py` 无残留

### Task 3: dialplan 注释更新（独立）
- **目标**：注释反映 FS uuid_record 录音机制
- **步骤**：
  1. `freeswitch/dialplan/public/00_biz_type.xml`：更新注释段 —— FS 现由 agent-flow 通过 `uuid_record`（audio_fork_start 之后发起，RECORD_STEREO=true）录双声道（L=caller / R=AI），不再「FS 录不到 AI」；dialplan 保持 `silence_stream://-1` 保活，不加 record_session
- **验证**：`fs_cli -x "reloadxml"` 无报错

### Task 4: 文档更新（独立）
- **目标**：CLAUDE.md 录音描述与新实现同步
- **步骤**：
  1. `CLAUDE.md`：架构「Call recording」段从 `CallRecorder 累加双声道 PCM ... finalize_stereo_wav()` 改为 `FreeSWITCH uuid_record（audio_fork_start 之后发起，RECORD_STEREO=true）录双声道 {uuid}.wav，media_bug 排在 WRITE_REPLACE 之后以 tap 到 AI 下行`
  2. `CLAUDE.md`：「Key Orchestrator Modules」表删除 `call_recorder.py` 行
  3. `CLAUDE.md`：数据流描述里录音相关文字（CallRecorder 喂帧）更新
- **验证**：`grep -n "CallRecorder\|call_recorder" CLAUDE.md` 无过时引用（或已更新为新机制）

### Task 5: 端到端验证（依赖 Task 1-4）
- **目标**：功能回归 + 规格校验 + 索引更新
- **步骤**：
  1. 重启 flow：`./scripts/local.sh stop flow && ./scripts/local.sh flow`；启动日志无 import error
  2. 真实呼入一通（caller 说话 + AI 回复 + 挂断）；flow 日志见 `uuid_audio_fork start` → `uuid_record start → .../recordings/{uuid}.wav` → `CHANNEL_HANGUP`
  3. `ffprobe recordings/{uuid}.wav` 显示 2 channels；提取右声道（`ffmpeg -af "pan=mono|c0=c1"`）含 AI 人声
  4. 挂断 ~5s 后 `_archive_recording` 上传成功；console 通话详情录音可播放（L=caller / R=AI 与历史归档一致）
  5. 流式通话功能回归：多轮对话 + barge-in 正常（无 CallRecorder 相关报错）
  6. `openspec validate migrate-recording-to-freeswitch --strict` 通过
  7. `codegraph sync`（main.py / handler.py 改动纳入索引）
