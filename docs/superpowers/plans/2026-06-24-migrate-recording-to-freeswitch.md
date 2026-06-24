# 实现计划：录音下沉到 FreeSWITCH

> 来源：openspec/changes/migrate-recording-to-freeswitch/plan-ready.md
> 类型：清理 + 转正式（验证补丁已在 working tree），无新功能逻辑 → 不套 TDD，用 py_compile + grep + 真实通话验证
> 提交策略：不自动 commit，实现完由用户决定

## Task 1: ESL record 方法转正式
- [x] 1.1 `agent-flow/src/clients/esl.py`：`record_start`/`record_stop` 注释去「验证用」措辞，转正式
- [x] 1.2 `agent-flow/main.py::_on_channel_answer`：record_start 段去 `[验证补丁]` 标记，注释转正式（含 WRITE_REPLACE 顺序 WHY、失败 non-fatal）
- [x] 1.3 `agent-flow/main.py::_on_channel_hangup`：record_stop 段去 `[验证补丁]` 标记
- [x] 1.4 验证：`py_compile main.py src/clients/esl.py`；`grep -rn "\[验证补丁\]" agent-flow/main.py agent-flow/src/clients/esl.py` 无残留

## Task 2: 删除 CallRecorder（依赖 Task 1）
- [x] 2.1 `agent-flow/src/ws/handler.py`：删 `from ws.call_recorder import CallRecorder`
- [x] 2.2 `agent-flow/src/ws/handler.py`：删 `recorder = CallRecorder(...)` 实例化
- [x] 2.3 `agent-flow/src/ws/handler.py`：删 `recorder` 参数透传（handle → _receive_during_streaming / _process_streaming_turn / _cleanup）
- [x] 2.4 `agent-flow/src/ws/handler.py`：删所有 `recorder.feed_caller(...)` / `recorder.feed_ai(...)` 调用
- [x] 2.5 `agent-flow/src/ws/handler.py`：删 `_cleanup` 已注释写盘段（含注释）
- [x] 2.6 删除 `agent-flow/src/ws/call_recorder.py` 整文件
- [x] 2.7 `agent-flow/src/storage/minio_storage.py`：确认 `wrap_wav_header` 无其他引用后决定是否删
- [x] 2.8 验证：`py_compile src/ws/handler.py`；`grep -rn "CallRecorder\|call_recorder\|\.recorder\|feed_caller\|feed_ai" src/ main.py` 无残留

## Task 3: dialplan 注释更新（独立）
- [x] 3.1 `freeswitch/dialplan/public/00_biz_type.xml`：注释更新为 FS uuid_record 录双声道
- [x] 3.2 验证：`fs_cli -x "reloadxml"` 无报错

## Task 4: CLAUDE.md 文档更新（独立）
- [x] 4.1 `CLAUDE.md`：架构「Call recording」段 CallRecorder → FS uuid_record
- [x] 4.2 `CLAUDE.md`：「Key Orchestrator Modules」表删 call_recorder.py 行
- [x] 4.3 `CLAUDE.md`：数据流描述录音文字更新
- [x] 4.4 验证：`grep -n "CallRecorder\|call_recorder" CLAUDE.md` 无过时引用

## Task 5: 端到端验证（依赖 Task 1-4）
- [x] 5.1 重启 flow 无 import error
- [x] 5.2 真实呼入：flow 日志见 uuid_record start；ffprobe {uuid}.wav 2 channels；右声道含 AI
- [x] 5.3 挂断后 archive 上传成功 + console 可播放
- [x] 5.4 多轮 + barge-in 回归
- [x] 5.5 `openspec validate migrate-recording-to-freeswitch --strict` 通过
- [x] 5.6 `codegraph sync`
