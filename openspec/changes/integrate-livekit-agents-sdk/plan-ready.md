# 实现计划：integrate-livekit-agents-sdk

## 来源
- 提案：openspec/changes/integrate-livekit-agents-sdk/proposal.md
- 设计：openspec/changes/integrate-livekit-agents-sdk/design.md
- 规格：openspec/changes/integrate-livekit-agents-sdk/specs/conversation-pipeline/spec.md
- 任务：openspec/changes/integrate-livekit-agents-sdk/tasks.md

> 参考代码库：`agents/`（livekit/agents @ v1.8.3）。关键先例：`agents/tests/fake_session.py`（无头接线）、`agents/tests/fake_io.py`（自定义 IO）、`agents/tests/fake_vad.py`（测试 VAD）、`agents/livekit-plugins/livekit-plugins-cartesia/.../tts.py`（TTS 流样板）、`agents/livekit-plugins/livekit-plugins-sarvam/.../stt.py`（STT 流样板）。
> 全量测试命令：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`

## 实现步骤

### Task 1: 依赖引入与配置扩展
- 目标：livekit-agents 依赖可用，新配置项就位
- 来源：tasks.md §1
- 改动文件：`agent-flow/requirements.txt`、`agent-flow/src/config.py`、`agent-flow/.env.example`
- 验证方式：依赖冒烟 + pydantic 加载
- 步骤：
  1. `requirements.txt` 加 `livekit-agents>=1.8.3,<1.9`，pip 安装后 `python -c "from livekit.agents import AgentSession, Agent; from livekit.agents.voice.io import AudioInput, AudioOutput; print('ok')"`
  2. `config.py` 加 `endpointing_min_delay: float = 0.1`、`endpointing_max_delay: float = 2.0`、`interruption_min_duration: float = 0.5`（CALLBOT_ 前缀自动）；`.env.example` 同步注释
  3. 冒烟：uvloop 下无头构造 AgentSession + Fake IO 启停（仿 fake_session.py 最小版），确认依赖闭环
  4. 跑既有测试确认无回归

### Task 2: TelephonyAudioInput
- 目标：WS 上行帧 → SDK AudioFrame 的处理链桥接
- 来源：tasks.md §2.1
- 改动文件：新建 `agent-flow/src/voice/__init__.py`、`agent-flow/src/voice/io.py`
- 验证方式：`pytest tests/voice/test_io.py -v`
- 步骤：
  1. `TelephonyAudioInput(AudioInput)`：内部 `utils.aio.Chan[bytes]` + `push_bytes()/close()`；`__anext__` 拉帧 → JitterBuffer.insert/drain → APM（`process(frame, output.recent_reverse)`）或 denoiser → gain → 构造 `rtc.AudioFrame(sample_rate=16000, num_channels=1)`
  2. close 哨兵后 `__anext__` 抛 StopAsyncIteration（session teardown 依赖）
  3. 单测：处理链顺序断言、采样率恒定、close 语义

### Task 3: TelephonyAudioOutput
- 目标：TTSOutputBuffer 语义迁移为 SDK AudioOutput（含播放事件硬契约）
- 来源：tasks.md §2.2
- 改动文件：`agent-flow/src/voice/io.py`
- 验证方式：`pytest tests/voice/test_io.py -v`
- 步骤：
  1. 迁移 `TTSOutputBuffer` 匀速发送循环/静音保活（120s）/prebuffer 为内部实现；`capture_frame`（快于实时入队）、`flush`（段尾排空后 `playback_finished(interrupted=False)`）、`clear_buffer`（立即清空 + 同步 `playback_finished(interrupted=True)`）
  2. 实现 `pause/resume` + `AudioOutput(label=..., capabilities=AudioOutputCapabilities(pause=True), sample_rate=16000)`；匀速循环驱动 `playback_started/progressed`
  3. 保留 `recent_reverse` 属性（AEC 远端参考，对齐现 `TTSOutputBuffer.recent_reverse`）
  4. 单测：flush/clear/pause 三路径必报 finished（防 wait_for_playout 挂死）、clear 后转静音帧、send_fn 即 WS send_bytes mock

### Task 4: TransvoiceSTT 插件
- 目标：agent-asr WS 协议 → livekit STT 流式事件
- 来源：tasks.md §3.1、§3.3
- 改动文件：新建 `agent-flow/src/voice/stt_plugin.py`、`agent-flow/tests/voice/test_stt_plugin.py`
- 验证方式：`pytest tests/voice/test_stt_plugin.py -v`
- 步骤：
  1. `TransvoiceSTT(stt.STT)`：`capabilities=STTCapabilities(streaming=True, interim_results=False)`、`stream()` 返回 RecognizeStream、`_recognize_impl` raise NotImplementedError、`model/provider` property
  2. `TransvoiceRecognizeStream(stt.RecognizeStream)`（`sample_rate=16000`）：`_run()` 建 WS → 发 config → 消费 `_input_ch`（AudioFrame→二进制；`_FlushSentinel`→`{"type":"end"}`）→ receiver：`result` → `FINAL_TRANSCRIPT` + 紧随 `END_OF_SPEECH`；段首/首帧 → `START_OF_SPEECH`；文本 <2 字符 → FINAL 与 EOS 一并丢弃；`error`/断连 → `APIConnectionError`（可重试）
  3. `reset` 消息忽略（不发送）
  4. 单测（mock WS server）：多 final 事件序列断言（顺序 FINAL→EOS）、短 final 丢弃、断连异常类型

### Task 5: TransvoiceTTS 插件
- 目标：agent-tts WS 协议 → livekit TTS 流式合成
- 来源：tasks.md §3.2、§3.4
- 改动文件：新建 `agent-flow/src/voice/tts_plugin.py`、`agent-flow/tests/voice/test_tts_plugin.py`
- 验证方式：`pytest tests/voice/test_tts_plugin.py -v`
- 步骤：
  1. `TransvoiceTTS(tts.TTS)`：`sample_rate=22050, num_channels=1`、`TTSCapabilities(streaming=True)`、构造参数 `biz_type`（BIZ_TYPE_PROFILES 迁移）、`stream()`/`synthesize()`
  2. `TransvoiceSynthesizeStream._run(output_emitter)`：`initialize(mime_type="audio/pcm", sample_rate=22050, stream=True)`；缓冲 push_text，flush sentinel 处聚合整句发 `{"type":"synthesize",...,"request_id"}`；`start_segment(segment_id)` → 二进制块 `push` → `result` 消息 → `end_segment()`；`end_input()` 收尾；每 call 共享一条 WS 连接
  3. 迟到音频过滤：存活 request_id 集合，流取消（打断）即移除，receiver 丢弃其后续块
  4. `TransvoiceChunkedStream`（say() 预制音频）：单请求 `initialize(stream=False)→push*→flush()`
  5. 单测：两 flush 两 synthesize 请求、打断后迟到 chunk 丢弃、result→end_segment

### Task 6: LangGraph 流式接口重构
- 目标：管线输出从回调式改为文本流（节点 ①-⑥ 语义不动）
- 来源：tasks.md §4.1、§4.2
- 改动文件：`agent-flow/src/graph/flow.py`、`agent-flow/tests/graph/test_flow_stream.py`（或就近测试文件）
- 验证方式：`pytest tests/graph/ -v`
- 步骤：
  1. `run_streaming_pipeline(state, audio_callback, action_callback)` → `astream_reply_text(state) -> AsyncIterator[str]` + action 回调参数：保留 run_pre_llm_phase 与 ①-⑥；LLM 流经 IncrementalJSONParser 后逐 token yield；拆掉节点 ⑦ TTS 与 SentenceSplitter 接线
  2. 历史/轮次持久化（fire_insert_turn/save_turn）移至流迭代完毕处执行
  3. 单测：mock LLM 流 → yield 序列断言、action 回调触发、持久化调用断言

### Task 7: TransvoiceAgent 与会话工厂
- 目标：llm_node 嵌入 + 终端动作 + barge-in 落库 + session 装配
- 来源：tasks.md §5.1-5.3
- 改动文件：新建 `agent-flow/src/voice/agent.py`、`agent-flow/src/voice/session.py`、`agent-flow/tests/voice/test_agent.py`
- 验证方式：`pytest tests/voice/test_agent.py -v`
- 步骤：
  1. `TransvoiceAgent(Agent).llm_node`：userdata 取 call 上下文 → `run_pre_llm_phase`（空文本跳过对齐现状）→ `astream_reply_text` yield；ASR minio_key 经 userdata→graph state 透传
  2. 终端动作：on_action 捕获 end/handoff → llm_node 流结束 spawn `await session.wait_for_playout()` → `_execute_terminal_action`（自 handler.py 迁移 ESL 挂断/转接逻辑）
  3. `session.on("conversation_item_added")` 检测 agent 消息 interrupted=True → `fire_insert_event("barge_in")`
  4. `build_agent_session(call_ctx)`：STT/TTS 插件实例（biz_type）、`vad=inference.VAD()`、`turn_handling={"turn_detection":"stt","endpointing":{...from config},"interruption":{"min_duration":cfg}}`、`aec_warmup_duration=None`、`userdata=call_ctx`、`loop=asyncio.get_running_loop()`
  5. 单测：llm_node yield 流、end/handoff 在 playout 排空后执行（时序断言）

### Task 8: main.py 接线
- 目标：ws_media_fork 驱动 AgentSession；lifespan 清理
- 来源：tasks.md §6.1-6.3
- 改动文件：`agent-flow/main.py`、新建 `agent-flow/tests/voice/test_session_integration.py`
- 验证方式：`pytest tests/voice/test_session_integration.py -v`
- 步骤：
  1. `ws_media_fork` 重写：accept → build_agent_session → session.start(agent) → 收帧循环（bytes→`audio_in.push_bytes`；text stop→break；active_call.cancel 检查）→ finally `await session.aclose()`
  2. `lifespan` 移除 StreamingCallHandler 构造与注入（ESL/registry/录音/归档/OutboundExecutor 保持零改动）
  3. 无头集成测试（FakeIO + mock 上游 WS + fake VAD，仿 agents/tests 先例）：两轮对话、打断（推语音帧）恢复、终端动作触达 mock ESL

### Task 9: 旧组件退役
- 目标：删除被替换组件与配置，零残留
- 来源：tasks.md §7.1-7.4
- 改动文件：删 `agent-flow/src/ws/handler.py`、`src/ws/asr_streaming.py`、`src/ws/rms_gate.py`、`src/clients/asr_ws_client.py`、`src/clients/tts_ws_client.py`、`src/llm/sentence_splitter.py`；改 `src/ws/jitter_buffer.py`（删 TTSOutputBuffer）、`src/config.py`、`.env.example`；删旧测试
- 验证方式：grep 零残留 + 全量测试绿 + agent-asr/agent-tts git diff 为空
- 步骤：
  1. 删 6 个组件文件 + `jitter_buffer.py` 内 TTSOutputBuffer + 相关 import 清理
  2. config 删 `rms_gate_threshold/rms_gate_snr_factor/rms_gate_noise_floor_init/rms_gate_noise_adapt_rate`、`cooldown_after_bargein`、`barge_in_min_audio_bytes`、`barge_in_rms_threshold`、`splitter_min_length/splitter_flush_timeout/splitter_eager_first`、`asr_streaming_enabled`；`.env.example` 同步
  3. 删 `tests/ws/test_handler.py`、`tests/ws/test_asr_streaming.py`、`tests/ws/test_asr_streaming_on_final.py`
  4. `grep -rn "StreamingCallHandler\|TurnController\|AsrStreamingManager\|RMSGate\|asr_ws_client\|tts_ws_client\|SentenceSplitter\|TTSOutputBuffer" agent-flow/src agent-flow/main.py` 零命中；`git diff --stat agent-asr agent-tts` 为空

### Task 10: 端到端验收与文档同步
- 目标：真实通话验收 + 时延记录 + 文档一致
- 来源：tasks.md §8.1-8.6
- 改动文件：`CLAUDE.md`、`agent-flow/README.md`、变更目录内验收记录
- 验证方式：真实 SIP 通话清单逐项通过
- 步骤：
  1. 三组件全量 pytest 绿（agent-asr / agent-tts 套件零改动通过）
  2. `./scripts/local.sh` 全链路 → 软电话呼入 → ≥3 轮对话含一次 barge-in → 挂断 → 双声道 wav + MinIO 归档 + console 回放
  3. 外呼：console 建任务 → originate 摘机 → 同管线对话（call_target_vars 渲染）
  4. 首轮响应时延改造前后对比记录（写入变更目录 notes）
  5. CLAUDE.md（架构图/模块表/配置表）与 agent-flow/README.md 同步；确认回滚路径 = git revert 整体 commit
