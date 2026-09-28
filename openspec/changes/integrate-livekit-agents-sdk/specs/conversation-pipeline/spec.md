# conversation-pipeline Specification (delta)

## ADDED Requirements

### Requirement: livekit AgentSession 无头会话管线

agent-flow SHALL 以 livekit-agents SDK（v1.8.3，pip 依赖）的 `AgentSession` 驱动每通话对话：在 FastAPI 进程内 per-call 实例化，经 `session.input.audio` / `session.output.audio` 属性注入自定义 IO，`session.start(agent)` 不传 room。系统 SHALL NOT 引入 LiveKit Server / RTC / Worker job 模型；ESL 生命周期（CHANNEL_ANSWER/HANGUP → audio_fork/record → registry → 归档）SHALL 保持不变。

#### Scenario: 呼入触发会话建立
- **WHEN** FreeSWITCH CHANNEL_ANSWER 触发 `audio_fork_start`，FS 连接 `WS /media/{uuid}`
- **THEN** agent-flow SHALL 构造 AgentSession（含 STT/TTS 插件、silero VAD、turn_handling 配置）并注入 Telephony IO
- **AND** SHALL `session.start(agent)` 进入对话循环，无任何 LiveKit Server 连接尝试

#### Scenario: 挂断清理
- **WHEN** WS 断开或 CHANNEL_HANGUP 置 cancel
- **THEN** 系统 SHALL `await session.aclose()`（force-interrupt + detach IO）
- **AND** 录音归档/registry 取消等后续流程 SHALL 与改造前一致

### Requirement: Telephony 音频 IO 适配层

系统 SHALL 提供自定义 `AudioInput`/`AudioOutput` 桥接 mod_audio_fork WebSocket：

- **AudioInput**：WS 帧入队 → JitterBuffer 平滑 → WebRTCAPM（AEC 开启时，远端参考取 AudioOutput 最近下发帧）或 denoiser → 增益 → 产出 16kHz mono `rtc.AudioFrame`；采样率 SHALL 恒定 16kHz
- **AudioOutput**：SHALL 声明 `sample_rate=16000`（SDK 自动重采样 TTS 输出）与 `capabilities.pause=True`；内部 SHALL 保留 TTSOutputBuffer 语义——960B/30ms 匀速发送、无数据静音帧保活（120s 超时）、prebuffer
- **AudioOutput 播放事件契约**：`capture_frame/flush/clear_buffer` SHALL 正确回报 `playback_started/progressed/finished`；`clear_buffer`（打断）SHALL 同步上报 `playback_finished(interrupted=True)`；漏报导致挂死 SHALL 由测试防线覆盖

#### Scenario: 上行音频处理链
- **WHEN** mod_audio_fork 推送一帧上行 PCM
- **THEN** 该帧 SHALL 经 JitterBuffer → AEC/降噪 → 增益处理后以 AudioFrame 形式被 session 消费
- **AND** AEC 开启时远端参考 SHALL 为最近实际发往 FS 的下行帧（TTS 帧或静音帧）

#### Scenario: 打断清空下行
- **WHEN** SDK 检测打断调用 `clear_buffer`
- **THEN** 待播缓冲 SHALL 立即清空并切换为静音帧
- **AND** SHALL 上报 `playback_finished(interrupted=True)`，不终止 dialplan playback 保活

### Requirement: TransvoiceSTT 远程插件

系统 SHALL 提供实现 livekit `stt.STT` 流式接口的远程插件对接 agent-asr WebSocket：`capabilities=(streaming=True, interim_results=False)`；每通话一条连接。协议映射 SHALL 满足：

- 服务端多 `result`（FSMN-VAD 分段）→ 每段发 `FINAL_TRANSCRIPT` 后**紧随** `END_OF_SPEECH`（顺序不可颠倒）；段首发 `START_OF_SPEECH`
- `turn_detection="stt"` 模式下轮次提交 SHALL 由上述 EOS 驱动（保留服务端分段语义）
- 文本长度 < 2 字符的 final SHALL 连同配对 EOS 一并丢弃（防噪声伪轮次）
- `reset` 协议消息 SHALL 被插件忽略（打断由 SDK 体系接管）
- 上游连接/服务错误 SHALL 分类为可重试的连接类异常（避免 SDK 3 次熔断拆通话）

#### Scenario: 多 final 轮次
- **WHEN** 用户一句话内服务端推送 2 个分段 result 后停顿
- **THEN** 插件 SHALL 依次发 2 组 FINAL_TRANSCRIPT+END_OF_SPEECH
- **AND** session SHALL 在 endpointing min_delay 后提交包含两段合并转写的用户轮次并触发 LLM 回复

#### Scenario: 短 final 过滤
- **WHEN** 服务端推送文本为单字符的 result（如"嗯"）
- **THEN** 插件 SHALL 丢弃该 FINAL_TRANSCRIPT 与其 END_OF_SPEECH
- **AND** SHALL NOT 产生空转写轮次

#### Scenario: 上游断连
- **WHEN** agent-asr WS 连接中断
- **THEN** 插件 SHALL 抛可重试连接异常由 SDK 基类重连
- **AND** 通话 SHALL NOT 因瞬时断连被熔断关闭

### Requirement: TransvoiceTTS 远程插件

系统 SHALL 提供实现 livekit `tts.TTS` 接口的远程插件对接 agent-tts WebSocket：声明 `sample_rate=22050`（服务端实际输出率，字节原样推送）；`biz_type` voice profile（voice/speed/volume/pitch）SHALL 经插件构造参数注入；每通话一条共享连接。SHALL 满足：

- 流式合成：flush 边界聚合完整句 → 单次 synthesize 请求（服务端要求整句）；每句一个 segment（`start_segment → push* → end_segment`），`request_id` 即 segment 标识
- **迟到音频过滤**：流被取消（打断）后，该 request_id 的后续音频 SHALL 被丢弃
- 结果消息（`{"type":"result"}`）SHALL 映射为 segment 结束

#### Scenario: 整句合成
- **WHEN** LLM 流式输出"你好。请问是本人吗？"经 SDK tokenizer 分成两句
- **THEN** 插件 SHALL 在各自 flush 边界发出 2 次 synthesize 请求并按 segment 推送音频

#### Scenario: 打断后迟到音频
- **WHEN** 第 2 句合成中用户打断，该 segment 流被取消
- **THEN** 该 request_id 后续到达的音频块 SHALL 被丢弃
- **AND** 下一轮回复 SHALL NOT 串入被打断句的残留音频

### Requirement: LangGraph 管线经 llm_node 嵌入

系统 SHALL 通过覆盖 `Agent.llm_node` 驱动既有 LangGraph 7-node 管线：节点 ①-⑥（ASR 接收/MCP 身份/征信/记忆/RAG/LLM）语义与多租户 `(tenant_id, biz_type, scenario)` prompt 加载 SHALL 保持不变；管线输出 SHALL 从回调式改为 `AsyncIterator[str]` 文本流逐 token yield（`IncrementalJSONParser` 保留管线内部）。call 上下文（call_id/三元组/user_key/call_task_vars）SHALL 经 `AgentSession(userdata=...)` 传递。

终端动作（end/handoff）SHALL 由管线回调暴露，llm_node 捕获后 SHALL 在流结束且 `wait_for_playout()` 播放排空后执行 ESL 动作。barge-in 事件 SHALL 经 session 事件（agent 消息 interrupted 标记）落 PG（`fire_insert_event`）。

#### Scenario: 多租户回复生成
- **WHEN** 租户 A（collection）与租户 B（marketing）各自通话产生用户轮次
- **THEN** 两通话 SHALL 各自加载 `(tenant_id, biz_type, scenario)` 对应 prompt 并产生独立回复流
- **AND** marketing 通话 SHALL 照常触发征信查询节点

#### Scenario: 终端动作时序
- **WHEN** LLM 结构化输出含 action=end 且话术文本已流式播报
- **THEN** 系统 SHALL 等当前回复播放排空后再经 ESL 挂断
- **AND** SHALL NOT 出现话术未播完即挂断

### Requirement: 打断与轮次参数配置

AgentSession SHALL 配置 `vad=inference.VAD()`（本地 silero）、`turn_handling={"turn_detection":"stt"}`、`aec_warmup_duration=None`。endpointing 与打断参数 SHALL 经 `CALLBOT_` 前缀配置暴露：`CALLBOT_ENDPOINTING_MIN_DELAY`（默认 0.1s）、`CALLBOT_ENDPOINTING_MAX_DELAY`（默认 2.0s）、`CALLBOT_INTERRUPTION_MIN_DURATION`（默认 0.5s）。

#### Scenario: barge-in
- **WHEN** AI 播报中用户说话累计 ≥ min_duration
- **THEN** VAD SHALL 触发打断：清空下行缓冲、取消当前回复任务
- **AND** 用户后续语音 SHALL 正常进入新一轮识别

#### Scenario: 首轮响应时延
- **WHEN** 用户说完一段话（服务端推 final）
- **THEN** 轮次提交时延 SHALL 不显著劣于改造前（min_delay 默认 0.1s；验收实测对比记录）

### Requirement: 旧管线组件退役

本变更 SHALL 删除：`StreamingCallHandler`、`TurnController`、`AsrStreamingManager`、`RMSGate`（含 `CALLBOT_RMS_GATE_*` 配置）、graph 内 `SentenceSplitter` 接线（含 `CALLBOT_SPLITTER_*` 配置）、`asr_ws_client.py`、`tts_ws_client.py`、`CALLBOT_COOLDOWN_AFTER_BARGEIN` / `CALLBOT_BARGE_IN_MIN_AUDIO_BYTES` / `CALLBOT_ASR_STREAMING_ENABLED` 配置及对应测试。`JitterBuffer`、`WebRTCAPM`/denoiser、`ActiveCallRegistry`、ESL client、录音归档、OutboundExecutor SHALL 保留。

#### Scenario: 旧组件无残留
- **WHEN** 变更完成
- **THEN** 代码库 SHALL 无上述组件的 import/调用/配置引用
- **AND** `agent-asr`/`agent-tts` 服务与其 WS 契约 SHALL 未被修改

#### Scenario: 外呼复用
- **WHEN** 外呼任务 originate 摘机触发 CHANNEL_ANSWER（ai_outbound=true）
- **THEN** 该通话 SHALL 进入同一 AgentSession 管线（含 call_target_vars 渲染）
