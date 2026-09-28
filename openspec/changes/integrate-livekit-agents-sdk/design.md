# Design: livekit-agents SDK 集成改造

> 本文档记录 spec 阶段确认的架构方向，三项核心决策已经用户拍板（2026-09-28）：① LangGraph 经 `llm_node` 覆盖嵌入；② VAD + `"stt"` 轮次模式；③ 直接替换旧管线（无双轨开关）。
> 研究依据：`agents/`（livekit/agents @ v1.8.3）三路并行调研报告（AgentSession 无头运行 / STT-TTS-LLM 插件接口 / turn-VAD-打断-playout），关键结论均已 file:line 落证。

## 1. 核心架构：无头 AgentSession per call

```
FreeSWITCH (mod_audio_fork, 不变)
    │ WS /media/{uuid} (16kHz PCM16)
    ▼
agent-flow main.py ws_media_fork
    │ 收帧 → TelephonyAudioInput 内部队列
    ▼
┌─────────────────── AgentSession（每通话一个，进程内实例化，无 LiveKit Server / Worker / Job）───────────────────┐
│ TelephonyAudioInput          TransvoiceSTT 插件            silero VAD（inference.VAD，本地）                       │
│  JitterBuffer(保留) ──┬──→ STT RecognizeStream ──→ FSMN final → FINAL_TRANSCRIPT + END_OF_SPEECH                  │
│  WebRTCAPM(保留) ─────┴──→ VAD stream ──→ 打断检测（min_duration=0.5s，替代 RMSGate）                              │
│                                                                                                                    │
│ turn_handling: {"turn_detection": "stt"} —— STT EOS 主导轮次提交（FSMN 分段语义保留）                              │
│       │ commit user turn                                                                                           │
│       ▼                                                                                                             │
│ TransvoiceAgent.llm_node() ──→ LangGraph 7-node 管线（原样）──逐 token yield str──→ SDK tokenizer 分句             │
│       │                                                                                                            │
│       ▼                                                                                                            │
│ TransvoiceTTS 插件（SynthesizeStream；整句聚合→ agent-tts WS；22050Hz 直推）                                        │
│       │ AudioEmitter 切帧，generation 层自动重采样 → sink.sample_rate=16000（替代 _resample_pcm）                   │
│       ▼                                                                                                             │
│ TelephonyAudioOutput（TTSOutputBuffer 语义迁移：匀速 30ms + 静音保活 + clear_buffer=打断 + pause/resume + 播放事件）│
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
    │ WS 下行 PCM → FreeSWITCH → RTP
    ▼
ESL 生命周期（CHANNEL_ANSWER/HANGUP → audio_fork/record/registry/归档）全部不变
```

接线模式严格仿官方无头先例 `agents/tests/fake_session.py`：`session.input.audio = ...` / `session.output.audio = ...` 属性注入（start 前），`await session.start(agent)`（不传 room 即跳过 RoomIO，无 job_ctx 时 record 自动 False）。

## 2. 已拍板决策与依据

### 2.1 LangGraph 嵌入：override `Agent.llm_node`（路线 A'）

`Agent.llm_node(chat_ctx, tools, model_settings) -> AsyncIterable[str | ChatChunk | FlushSentinel]`（`voice/agent.py:400`）。generation 层接受纯 `str` 流（`generation.py:258-259`）——LangGraph 流式文本**零协议适配**直接 yield。

- 7-node 管线（MCP ②③/记忆 ④/RAG ⑤/LLM ⑥）原样保留，仅输出端从 `audio_callback(pcm, index)` 改为文本流
- `IncrementalJSONParser`（结构化输出解析）保留在管线内部；`SentenceSplitter` 退役（SDK tokenizer 接管分句 → TTS）
- 终端动作（end/handoff）：管线经回调暴露 → llm_node 捕获，stream 结束后 spawn 任务 `await session.wait_for_playout()` 再执行 ESL（对齐现状"等 TTS 播完再执行"，`_execute_terminal_action` 复用）
- 打断/排队/分句/TTS 并发全部由框架接管，免费获得

**放弃路线 B（OpenAI 兼容 LLM + 钩子）的原因**：用户约束"LangGraph 7-node 语义不变"，路线 B 需把编排退化为钩子 + ChatContext，7-node 结构性重写。

### 2.2 VAD/轮次分工：silero VAD + `turn_detection="stt"`（组合方案 C）

```python
AgentSession(
    stt=TransvoiceSTT(...),
    vad=inference.VAD(),                          # 本地原生库（livekit-local-inference，模型嵌入 .so，零下载离线可用）
    turn_handling={
        "turn_detection": "stt",                  # 轮次提交由 STT EOS 驱动（FSMN 分段语义）
        "endpointing": {"mode": "fixed", "min_delay": 0.1, "max_delay": 2.0},   # 见 2.2.1 调优
        "interruption": {"min_duration": 0.5, "resume_false_interruption": True},
    },
    aec_warmup_duration=None,                     # 关键：默认 3.0s 且自定义 IO 下 SIP 自动关闭逻辑不触发
)
```

- **轮次边界**：STT 插件把 FSMN final 映射为 `FINAL_TRANSCRIPT` + `END_OF_SPEECH`（顺序铁律 FINAL→EOS），`"stt"` 模式下 EOS 触发 EOU 提交（`audio_recognition.py:1333-1376`）——与现状"服务端分段驱动轮次"语义一致
- **打断检测**：silero VAD `INFERENCE_DONE` 32ms 粒度 + `min_duration=0.5s` 语音累计时长门（`agent_activity.py:2447-2462`）——替代 RMSGate，语义映射：`activation/deactivation_threshold` 迟滞 ≈ RMS 门限，`min_duration` ≈ `BARGE_IN_MIN_AUDIO_BYTES`
- **不使用默认 TurnDetector**（EOU 语义模型）：避免 108MB v1-mini 权重 + 中文支持未验证 + 与 FSMN 双重分段冲突；显式 `"stt"` 后缺省 TurnDetector 不装配
- **误打断恢复**：`resume_false_interruption=True`（打断后 2s 静默则恢复播放）→ 要求 AudioOutput `capabilities.pause=True`

#### 2.2.1 延迟调优（重要差异点）

现状 TurnController 收到 final **立即**启动轮次（零等待）；SDK 的 EOU 提交 = EOS 时刻 + `endpointing.min_delay`（通用默认 0.5s）。因 FSMN 分段本身已含端点静音判定，**min_delay 默认下调至 0.1s**（配置 `CALLBOT_ENDPOINTING_MIN_DELAY`），验收时实测校准。

### 2.3 切换策略：直接替换

本变更内删除 `StreamingCallHandler` / `TurnController` / `AsrStreamingManager` / `RMSGate` / graph 内 `SentenceSplitter` 及旧 `asr_ws_client.py` / `tts_ws_client.py`，重写受影响测试。回滚 = git revert。

### 2.4 派生决策（由上述决策自然推导）

| 张力 | 决策 |
|------|------|
| AEC/降噪挂载 | `WebRTCAPM`（livekit rtc AudioProcessingModule，与 RoomIO 内部同类）保留，在 `TelephonyAudioInput.__anext__` 内逐帧处理后再 yield；AEC 远端参考 = `TelephonyAudioOutput.recent_reverse`（对齐现状 `TTSOutputBuffer.recent_reverse`）。`Denoiser` 保留为 AEC 关闭时选项，`audio_gain` 保留 |
| JitterBuffer | 保留（SDK 自定义 IO 无输入缓冲概念），AudioInput 内部使用 |
| TTSOutputBuffer | 类逻辑迁移进 `TelephonyAudioOutput`（SDK playout 是薄层：匀速/静音保活/背压全归 sink——`io.py:297` capture_frame 可快于实时契约） |
| 插件归属 | `agent-flow/src/voice/` 新包（纯客户端，唯消费者是 agent-flow；不建独立仓库级插件包——不提前设计） |

## 3. 模块设计

### 3.1 新包 `agent-flow/src/voice/`

| 文件 | 内容 |
|------|------|
| `io.py` | `TelephonyAudioInput(AudioInput)`、`TelephonyAudioOutput(AudioOutput)` |
| `stt_plugin.py` | `TransvoiceSTT(stt.STT)` + `TransvoiceRecognizeStream(stt.RecognizeStream)` |
| `tts_plugin.py` | `TransvoiceTTS(tts.TTS)` + `TransvoiceSynthesizeStream(tts.SynthesizeStream)` + `TransvoiceChunkedStream(tts.ChunkedStream)` |
| `agent.py` | `TransvoiceAgent(Agent)`（llm_node 覆盖 + 终端动作 + barge-in 落库） |
| `session.py` | `build_agent_session(call_ctx) -> AgentSession` 工厂（集中全部 wiring/参数） |

### 3.2 TelephonyAudioInput（`voice/io.py`）

```python
class TelephonyAudioInput(AudioInput):
    """WS 帧 → 内部 Chan[bytes] → JitterBuffer → APM/denoise+gain → AudioFrame"""
    def push_bytes(self, pcm: bytes) -> None: ...   # main.py 收帧入口（send_nowait，背压由 Chan 满 blocking 处理）
    def close(self) -> None: ...                     # 投 StopAsyncIteration 哨兵
    async def __anext__(self) -> rtc.AudioFrame:     # 16kHz mono；帧长无强制约定（VAD 自攒窗口）
```

- 处理链与现状一致：jitter 平滑 → `_apm.process(frame, recent_reverse)` 或 denoiser → gain；**样本率恒定 16k**（VAD 变率丢帧，`inference/vad.py:299-301`）
- 采样率恒定性由 mod_audio_fork 保证，无需重采样

### 3.3 TelephonyAudioOutput（`voice/io.py`）

`AudioOutput` 硬契约（`io.py:147-330`）：`capture_frame/flush/clear_buffer` 必实现 + **播放事件回报**（漏报 `on_playback_finished` → `wait_for_playout` 永久挂死）。

- 内部即现 `TTSOutputBuffer` 逻辑整体迁移：960B/30ms 匀速发送循环、静音帧保活（`_SILENCE_TIMEOUT=120s`）、`prebuffer_frames`
- `capture_frame`（可快于实时）→ 拆帧入队；`flush()` → 段结束标记（排空当前段后上报 `playback_finished(interrupted=False)`）；`clear_buffer()` → 同步清空 + **立即** `playback_finished(interrupted=True)`
- `capabilities.pause=True` + pause/resume 实现（误打断恢复依赖，`agent_session.py:1192-1200` 会检查）
- `sample_rate=16000` → generation 层自动把 TTS 22050 重采样到 16k（`generation.py:628-650`，替代 `_resample_pcm`）
- `recent_reverse` 属性暴露给 AudioInput 作 AEC 远端参考（保留现状机制）
- 播放事件由匀速发送循环驱动：首帧实际发出 → `playback_started`；段尾/打断 → `playback_finished`

### 3.4 TransvoiceSTT 插件（`voice/stt_plugin.py`）

协议映射（`agent-asr/asradapter/ws_server.py` ↔ `stt.py`）：

| WS 协议 | SpeechEvent |
|---------|-------------|
| 连接后 `{"type":"config","call_id","language":"zh","streaming":true}` | `stream()` 建流时发送（call_id 用 shortuuid） |
| 二进制 PCM 帧 | `_run()` 内消费 `_input_ch` 的 AudioFrame → `frame.data` 直发（基类已按 `sample_rate=16000` 重采样，`stt.py:560-571`） |
| `{"type":"result","text","confidence"}`（多 final） | 每段 → `FINAL_TRANSCRIPT`（`SpeechData(text, confidence, language="zh")`）+ 紧随 `END_OF_SPEECH`（顺序铁律） |
| （分段开始推断） | 段首 → `START_OF_SPEECH`（`"stt"` 模式消费；首个二进制帧/上一 EOS 后新帧触发） |
| `{"type":"reset"}` | **不映射**（SDK 打断体系接管，插件忽略） |
| `{"type":"end"}` | `_FlushSentinel` 到达时发送（commit/close 时框架调 flush） |
| `{"type":"error","message"}` | `APIConnectionError`（连接类，可重试）/ `APIStatusError`（服务端拒绝） |

- `STTCapabilities(streaming=True, interim_results=False, aligned_transcript=False)`——服务端无 partial（客户端 partial 分支本就是死代码）、无词级时间戳（`start_time/end_time=0.0`，EOU 退化为到达时刻）
- **短 final 过滤**（替代 `TurnController.min_text_len=2`）：文本 `<2` 字符时**同时丢弃 FINAL 与配对 EOS**（防空转写轮次），SOS 保留（下一段新 SOS 自动取消挂起 EOU）
- **错误分类是通话生死线**：连续 3 次不可恢复错误熔断整通（`max_unrecoverable_errors=3`，`agent_session.py:1988-2021`）——上游瞬时故障一律 `APIConnectionError`（可重试标记）
- 请求级 `model`/`provider` property 覆盖（metrics 标签）

### 3.5 TransvoiceTTS 插件（`voice/tts_plugin.py`）

v1.8.3 事件模型：无 TTSEvent，经 `AudioEmitter` 推送（`tts.py:827-1358`）。

- `TransvoiceTTS(tts.TTS)`: `sample_rate=22050, num_channels=1`（服务端实际输出率，原样推字节，SDK 负责下游重采样）；`TTSCapabilities(streaming=True)`；构造参数 `biz_type`（`BIZ_TYPE_PROFILES` per-biz voice/speed/volume/pitch 原样迁移）
- `TransvoiceSynthesizeStream._run(output_emitter)`：
  - `output_emitter.initialize(request_id, sample_rate=22050, num_channels=1, mime_type="audio/pcm", stream=True)`
  - 缓冲 `push_text` token；`flush()` sentinel 处聚合**完整句** → 共享 WS 连接发 `{"type":"synthesize","text",...,"request_id"}`（服务端要求整句文本，无增量合成——协议约束，插件聚合）
  - 每句一个 segment：`start_segment(segment_id=request_id)` → 收二进制块 `push(chunk)` → 收 `{"type":"result",...}` → `end_segment()`；输入终止 → `end_input()`
  - **迟到音频过滤**（打断后旧句音频串台防线）：维护存活 request_id 集合；流被取消（打断）即从集合移除，receiver 丢弃其后续音频（对齐 cartesia 样板）
  - 每 call 一条 WS 连接（复用现状 tts_ws_client 连接复用 + request_id 解复用模式），连接层错误 → `APIConnectionError`
- `TransvoiceChunkedStream`（`say()` 预制音频路径）：单请求 `initialize(stream=False) → push* → flush()`

### 3.6 TransvoiceAgent + LangGraph 对接（`voice/agent.py` / `graph/flow.py`）

```python
class TransvoiceAgent(Agent):
    async def llm_node(self, chat_ctx, tools, model_settings):
        user_text = chat_ctx.latest_user_message()          # 或经 userdata 传 turn 文本
        state = await run_pre_llm_phase(...)                 # 节点① + ②③④⑤ fan-out（原样）
        async for token in astream_reply_text(state):        # ⑥ LLM + IncrementalJSONParser，逐 token
            yield token                                       # SDK tokenizer 分句 → tts_node → ⑦
```

- `graph/flow.py` 重构：`run_streaming_pipeline(state, audio_callback, action_callback)` → **`astream_reply_text(state) -> AsyncIterator[str]` + `on_action(action)` 回调**；节点 ⑦ tts_synthesize 拆除（SDK TTS 链路替代）；对话历史持久化（fire_insert_turn/save_turn）保留在管线尾部（stream 迭代完毕处）
- call 上下文（`call_id/biz_type/user_key/tenant_id/scenario/call_task_vars/esl`）经 `AgentSession(userdata={...})` 携带，llm_node 从 userdata 取
- prompt 三维加载不变（管线 ① 内部 `get_system_prompt`）；`Agent(instructions=...)` 仅放兜底短语
- **终端动作**：`on_action("end"/"handoff")` → 记录 pending；llm_node 流结束后 spawn 任务 `await session.wait_for_playout()` → `_execute_terminal_action`（ESL 挂断/转接，逻辑从现 handler 迁移复用）
- **barge-in 落库**：`session.on("conversation_item_added", ...)` 检测 agent 消息 `interrupted=True` → `fire_insert_event(event_type="barge_in")`（对齐现状）
- ASR 结果 `minio_key`（音频归档）随 FINAL_TRANSCRIPT 事件 → 挂 userdata/graph state，管线消费方式不变

### 3.7 main.py 接线

```python
# ws_media_fork（替代 StreamingCallHandler.handle）
async def ws_media_fork(websocket, uuid):
    await websocket.accept()
    session, agent = build_agent_session(call_ctx)          # voice/session.py 工厂
    audio_in, audio_out = session.input.audio, session.output.audio
    await session.start(agent)
    try:
        while True:
            if hangup_event.is_set(): break
            data = await websocket.receive()
            if bytes → audio_in.push_bytes(data)
            elif text == {"type":"stop"} → break
    finally:
        await session.aclose()                              # force-interrupt + detach IO；先 drain 再挂的需求见 3.6 终端动作
```

- ESL `_on_channel_answer/_on_channel_hangup`、`ActiveCallRegistry`、`insert_call_session`、录音/归档、OutboundExecutor **零改动**
- `lifespan`：`StreamingCallHandler(...)` 构造替换为插件实例工厂（STT/TTS 客户端随每 call 创建）；`loop=asyncio.get_running_loop()` 显式传（uvloop 兼容）

### 3.8 配置映射

| 现配置 | 去向 |
|--------|------|
| `CALLBOT_ASR_WS_URL` / `CALLBOT_TTS_WS_URL` | 保留（插件构造） |
| `CALLBOT_JITTER_*` | 保留（AudioInput） |
| `CALLBOT_AEC_*` / `CALLBOT_DENOISE_*` / `CALLBOT_AUDIO_GAIN` | 保留（AudioInput，语义不变） |
| `CALLBOT_TTS_PREBUFFER_FRAMES` | 保留（AudioOutput sink） |
| `CALLBOT_RMS_GATE_*`（4 项） | **删除**（silero VAD 替代） |
| `CALLBOT_BARGE_IN_MIN_AUDIO_BYTES` | **删除**（≈`interruption.min_duration` 替代） |
| `CALLBOT_COOLDOWN_AFTER_BARGEIN` | **删除**（VAD 迟滞 + min_duration + SDK 轮次状态机替代） |
| `CALLBOT_SPLITTER_*`（3 项） | **删除**（SDK tokenizer 替代） |
| `CALLBOT_ASR_STREAMING_ENABLED` | **删除**（streaming 恒 true） |
| 新增 `CALLBOT_ENDPOINTING_MIN_DELAY`（默认 0.1）/ `CALLBOT_ENDPOINTING_MAX_DELAY`（默认 2.0）/ `CALLBOT_INTERRUPTION_MIN_DURATION`（默认 0.5） | SDK turn_handling 映射（pydantic-settings，`CALLBOT_` 前缀规范） |

## 4. 风险与对策

| 风险 | 对策 |
|------|------|
| **轮次提交延迟回归**（EOS + min_delay vs 现状 final 即启） | min_delay 默认 0.1s；验收项显式对比改造前后首轮响应时延 |
| **silero 电话噪声误打断**（无 RMSGate SNR 自适应） | `activation_threshold` 调优 + `min_duration=0.5` 门 + 误打断恢复（resume_false_interruption）；验收含安静环境/嘈杂环境两档 |
| **熔断拆通话**（3 次不可恢复错误） | 插件错误全分类为可重试连接错误；集成测试模拟上游抖动 |
| **AudioOutput 事件漏报挂死** | 契约单测：flush/clear_buffer/pause 路径全部断言 playback 事件；close 兜底上报 |
| **TTS 迟到音频串台** | request_id 存活集合过滤 + 打断场景单测 |
| **中文 tokenizer 分句质量** | SDK tokenizer 中文按标点切分，集成测试覆盖（长句/无标点尾句） |
| mod_audio_fork 帧 20ms 与 APM 10ms 帧长适配 | 现状 `WebRTCAPM.process` 已处理，迁移不改帧逻辑 |

## 5. 测试策略

- **单元**（新）：`tests/voice/test_stt_plugin.py`（mock WS server：多 final→事件序列、短 final 过滤、错误分类、FINAL→EOS 顺序）、`test_tts_plugin.py`（整句聚合、segment 生命周期、迟到音频丢弃）、`test_io.py`（AudioOutput 播放事件契约/静音保活/clear_buffer、AudioInput 处理链与恒定采样率）、`test_agent.py`（llm_node yield 流、终端动作时序）
- **无头集成**（新，仿 `agents/tests/fake_session.py`）：FakeIO + TransvoiceSTT/TTS（mock 上游 WS）跑完整 session——多轮对话、打断（推语音帧触发 VAD→ 需 fake VAD，仿 `agents/tests/fake_vad.py`）、终端动作
- **重写**：`tests/ws/test_handler.py`（TurnController 测试随组件删除）、`test_asr_streaming*.py`（随 AsrStreamingManager 删除）
- **端到端验收**（真实 SIP）：呼入→对话→barge-in→挂断→录音归档；外呼任务摘机复用管线；首轮响应时延对比

## 6. 明确不做

- 不引入 LiveKit Server / RTC / Room / Worker job 模型
- 不动 agent-asr / agent-tts 服务与其 WS 契约（`reset` 消息保留服务端实现，agent-flow 侧不再使用）
- 不动 java-mcp-server / console / OutboundExecutor 调度 / 录音归档链路
- 不接 AMD / filler / background_audio / adaptive interruption（云端凭证依赖；能力已记录，后续变更）
- 不接云引擎插件（Deepgram/Cartesia 等）
