# Tasks: 录音下沉到 FreeSWITCH

> 按执行依赖排序。验证补丁代码已在 working tree（带 `[验证补丁]` 标记），本变更主要为清理 + 转正式。
> **不变量**：流式通话路径（WS → JitterBuffer → APM → VAD → ASR → LLM → TTS）不受影响；归档 fire-and-forget 不阻断通话。

## 1. ESL record 方法转正式

> 验证补丁已引入 `record_start`/`record_stop`，去标记即可。

- [ ] 1.1 `agent-flow/src/clients/esl.py`：去掉 `record_start`/`record_stop` 注释里的 `[验证补丁]` 标记，docstring 转正式（保留「必须在 audio_fork_start 之后」的 WHY 与 RECORD_STEREO 说明）
- [ ] 1.2 `agent-flow/main.py::_on_channel_answer`：去掉 record_start 段的 `[验证补丁]` 标记，注释转正式
- [ ] 1.3 `agent-flow/main.py::_on_channel_hangup`：去掉 record_stop 段的 `[验证补丁]` 标记，注释转正式
- [ ] 1.4 验证：`cd agent-flow && python3 -m py_compile main.py src/clients/esl.py`；`grep -rn "\[验证补丁\]" agent-flow/main.py agent-flow/src/clients/esl.py` 无残留

## 2. 删除 CallRecorder

> 依赖 Task 1（FS 录制已正式接管，app 自录可移除）。

- [ ] 2.1 `agent-flow/src/ws/handler.py`：删除 `from ws.call_recorder import CallRecorder`
- [ ] 2.2 `agent-flow/src/ws/handler.py`：删除 `recorder = CallRecorder(...)` 实例化
- [ ] 2.3 `agent-flow/src/ws/handler.py`：删除 `recorder` 参数透传（`handle` → `_receive_during_streaming` / `_process_streaming_turn` / `_cleanup` 的签名与实参）
- [ ] 2.4 `agent-flow/src/ws/handler.py`：删除所有 `recorder.feed_caller(...)` 与 `recorder.feed_ai(...)` 调用
- [ ] 2.5 `agent-flow/src/ws/handler.py`：删除 `_cleanup` 中已注释的 app 写盘段（含其上方注释）
- [ ] 2.6 `agent-flow/src/ws/call_recorder.py`：删除整个文件
- [ ] 2.7 `agent-flow/src/storage/minio_storage.py`：确认 `wrap_wav_header` 是否还有 CallRecorder 之外的引用；若仅 CallRecorder 用则一并删除，否则保留为公共工具
- [ ] 2.8 验证：`cd agent-flow && python3 -m py_compile src/ws/handler.py`；`grep -rn "CallRecorder\|call_recorder\|\.recorder\|feed_caller\|feed_ai" src/ main.py` 无残留

## 3. dialplan 注释更新

- [ ] 3.1 `freeswitch/dialplan/public/00_biz_type.xml`：更新注释段 —— FS 现由 agent-flow 通过 `uuid_record`（audio_fork_start 之后发起，RECORD_STEREO=true）录双声道（L=caller / R=AI），不再「FS 录不到 AI」；dialplan 保持 `silence_stream://-1` 保活，不加 record_session
- [ ] 3.2 验证：`fs_cli -x "reloadxml"` 无报错

## 4. 文档更新

- [ ] 4.1 `CLAUDE.md`：架构「Call recording」段从 `CallRecorder 累加双声道 PCM ... finalize_stereo_wav()` 改为 `FreeSWITCH uuid_record（audio_fork_start 之后发起，RECORD_STEREO=true）录双声道 {uuid}.wav，media_bug 排在 WRITE_REPLACE 之后以 tap 到 AI 下行`
- [ ] 4.2 `CLAUDE.md`：「Key Orchestrator Modules」表删除 `call_recorder.py` 行
- [ ] 4.3 `CLAUDE.md`：数据流描述里录音相关文字（CallRecorder 喂帧）更新
- [ ] 4.4 验证：`grep -n "CallRecorder\|call_recorder" CLAUDE.md` 无过时引用（或已更新为新机制）

## 5. 端到端验证

- [ ] 5.1 重启 flow：`./scripts/local.sh stop flow && ./scripts/local.sh flow`；启动日志无 import error
- [ ] 5.2 真实呼入一通（caller 说话 + AI 回复 + 挂断）；flow 日志见 `uuid_audio_fork start` → `uuid_record start → .../recordings/{uuid}.wav` → `CHANNEL_HANGUP`
- [ ] 5.3 `ffprobe recordings/{uuid}.wav` 显示 2 channels；提取右声道（`ffmpeg -af "pan=mono|c0=c1"`）含 AI 人声
- [ ] 5.4 挂断 ~5s 后 `_archive_recording` 上传成功；console 通话详情录音可播放（L=caller / R=AI 与历史归档一致）
- [ ] 5.5 流式通话功能回归：多轮对话 + barge-in 正常（无 CallRecorder 相关报错）
- [ ] 5.6 `openspec validate migrate-recording-to-freeswitch --strict` 通过
- [ ] 5.7 `codegraph sync`（main.py / handler.py 改动纳入索引）
