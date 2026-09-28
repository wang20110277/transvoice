# Tasks: livekit-agents SDK 集成改造

> 按执行依赖排序：基础设施 → IO 层 → 插件层 → 管线对接 → 接线 → 退役 → 验收。
> 验证命令统一：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`（单文件运行去掉路径尾部）。

## 1. 依赖与配置基础

- [ ] 1.1 `agent-flow/requirements.txt` 增加 `livekit-agents>=1.8.3,<1.9`；`pip install` 后 `python -c "from livekit.agents import AgentSession; print('ok')"` 验证
- [ ] 1.2 `agent-flow/src/config.py` 新增 `endpointing_min_delay`（默认 0.1）/`endpointing_max_delay`（默认 2.0）/`interruption_min_duration`（默认 0.5）字段；`.env.example` 同步；验证：pydantic 加载单测或启动日志确认
- [ ] 1.3 冒烟脚本 `python -c`：无头构造 `AgentSession(stt=..., tts=...)` + fake IO `session.start()`（仿 `agents/tests/fake_session.py`）在 uvloop 下可启停，确认依赖闭环

## 2. Telephony IO 适配层（`src/voice/io.py`）

- [ ] 2.1 `TelephonyAudioInput`：`push_bytes/close` + `__anext__`（Chan 队列 → JitterBuffer → WebRTCAPM/denoise → gain → `rtc.AudioFrame` 16k mono）；AEC 远端参考接 `TelephonyAudioOutput.recent_reverse`
- [ ] 2.2 `TelephonyAudioOutput`：迁移 `TTSOutputBuffer` 语义（30ms 匀速/静音保活 120s/prebuffer）为 `capture_frame/flush/clear_buffer/pause/resume` + 播放事件回报（`playback_started/progressed/finished`，clear_buffer 同步 interrupted=True）；`sample_rate=16000`、`capabilities.pause=True`
- [ ] 2.3 单测 `tests/voice/test_io.py`：输入处理链（jitter/APM 顺序/恒定采样率）、输出契约（flush/clear/pause 路径必报 finished、静音保活、clear 后转静音帧）
- [ ] 2.4 `src/ws/jitter_buffer.py` 保留 `JitterBuffer`，`TTSOutputBuffer` 标记迁移（2.2 完成后在退役阶段删除）

## 3. STT/TTS 远程插件（`src/voice/stt_plugin.py` / `tts_plugin.py`）

- [ ] 3.1 `TransvoiceSTT` + `TransvoiceRecognizeStream`：config 握手、帧直发、final→FINAL+EOS（顺序）、段首 SOS、`_FlushSentinel`→end、错误分类（连接类可重试）、短 final（<2 字符）连 EOS 丢弃、`model/provider` property
- [ ] 3.2 `TransvoiceTTS` + `TransvoiceSynthesizeStream` + `TransvoiceChunkedStream`：biz_type profile 构造、flush 聚合整句、segment 生命周期（initialize→start_segment→push→end_segment→end_input）、request_id 存活集合迟到音频过滤、22050 声明
- [ ] 3.3 单测 `tests/voice/test_stt_plugin.py`（mock WS server 驱动：多 final 事件序列断言、短 final 丢弃、断连抛可重试异常）
- [ ] 3.4 单测 `tests/voice/test_tts_plugin.py`（两 flush 两请求、打断后迟到 chunk 丢弃、result→end_segment）

## 4. LangGraph 管线流式接口重构（`src/graph/flow.py`）

- [ ] 4.1 `run_streaming_pipeline(state, audio_callback, action_callback)` → `astream_reply_text(state) -> AsyncIterator[str]` + `on_action` 回调：节点 ①-⑥ 与 prompt 加载不动，拆掉节点 ⑦ TTS 段与 SentenceSplitter 接线；历史持久化（fire_insert_turn/save_turn）移至流迭代完毕处
- [ ] 4.2 单测：`astream_reply_text` yield 文本流与 action 回调（mock LLM 流），历史持久化调用断言

## 5. Agent 与会话工厂（`src/voice/agent.py` / `session.py`）

- [ ] 5.1 `TransvoiceAgent(Agent)`：`llm_node` 覆盖（userdata 取 call 上下文 → `run_pre_llm_phase` → `astream_reply_text` yield）；终端动作捕获 + 流结束 spawn `wait_for_playout` 后执行 `_execute_terminal_action`（自 handler 迁移）；`conversation_item_added` interrupted 检测 → `fire_insert_event("barge_in")`
- [ ] 5.2 `build_agent_session(call_ctx)`：AgentSession 装配（插件实例/silero VAD/turn_handling 映射 config 字段/`aec_warmup_duration=None`/userdata/`loop=get_running_loop()`）
- [ ] 5.3 单测 `tests/voice/test_agent.py`：llm_node 流式 yield、end/handoff 时序（playout 排空后执行）

## 6. main.py 接线

- [ ] 6.1 `ws_media_fork` 重写：accept → build_agent_session → `session.start` → 收帧循环 `push_bytes` / `{"type":"stop"}` / hangup 检查 → finally `aclose`；ESL answer/hangup、registry、录音、归档零改动确认
- [ ] 6.2 `lifespan`：移除 `StreamingCallHandler` 构造及其依赖注入
- [ ] 6.3 无头集成测试 `tests/voice/test_session_integration.py`（FakeIO + mock 上游 WS + fake VAD，仿 `agents/tests/fake_session.py`/`fake_vad.py`）：两轮对话、打断恢复、终端动作

## 7. 旧组件退役

- [ ] 7.1 删除文件：`src/ws/handler.py`、`src/ws/asr_streaming.py`、`src/ws/rms_gate.py`、`src/clients/asr_ws_client.py`、`src/clients/tts_ws_client.py`；`src/ws/jitter_buffer.py` 删 `TTSOutputBuffer`（保留 `JitterBuffer`）
- [ ] 7.2 删除 `src/llm/sentence_splitter.py` 及 graph 内引用；config 删 `rms_gate_*` 4 项、`cooldown_after_bargein`、`barge_in_min_audio_bytes`、`splitter_*` 3 项、`asr_streaming_enabled`、`barge_in_rms_threshold`；`.env.example` 同步
- [ ] 7.3 删除/重写测试：`tests/ws/test_handler.py`、`tests/ws/test_asr_streaming.py`、`tests/ws/test_asr_streaming_on_final.py`（TurnController/AsrStreamingManager 随组件消失）；`grep -rn "StreamingCallHandler\|TurnController\|AsrStreamingManager\|RMSGate\|asr_ws_client\|tts_ws_client\|SentenceSplitter" agent-flow/src agent-flow/main.py` 零残留
- [ ] 7.4 确认 `agent-asr`/`agent-tts` 代码与契约未动（git diff 为空）

## 8. 端到端验收与文档

- [ ] 8.1 全量测试：三组件 `pytest` 全绿（agent-asr / agent-tts 套件不受影响）
- [ ] 8.2 真实 SIP 呼入验收：`./scripts/local.sh` 全链路启动 → 软电话呼入 → ≥3 轮对话（含一次 barge-in）→ 挂断 → `recordings/{uuid}.wav` 双声道 + MinIO 归档 + console 可回放
- [ ] 8.3 外呼验收：console 建任务 → originate 摘机 → 同管线对话（call_target_vars 渲染生效）
- [ ] 8.4 时延记录：首轮响应时延改造前后对比（`turn %d done in %.0fms` 等价日志）写入变更目录
- [ ] 8.5 文档同步：`CLAUDE.md`（架构图/模块表/配置表/命令）、`agent-flow/README.md`
- [ ] 8.6 回滚说明：git revert 本变更整体 commit 即可恢复旧管线（无数据迁移，DB schema 零变更）
