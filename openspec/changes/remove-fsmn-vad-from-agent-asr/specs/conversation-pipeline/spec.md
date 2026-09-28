# conversation-pipeline Specification (delta)

## MODIFIED Requirements

### Requirement: TransvoiceSTT 远程插件

系统 SHALL 提供实现 livekit `stt.STT` **非流式批量**接口的远程插件对接 agent-asr WebSocket：`capabilities=(streaming=False, interim_results=False)`，仅实现 `_recognize_impl`。流式能力 SHALL 由 SDK 默认 `stt_node` 自动包装的 `stt.StreamAdapter(stt=plugin, vad=session_vad)` 提供（VAD 切段 → 段级 `recognize` → FINAL 事件）；插件 SHALL NOT 手工合成 STT 事件序列（SOS/EOS/FINAL 由 StreamAdapter 产出）。

协议交互 SHALL 满足：每语音段一条 WS 连接——connect → `{"type":"config","call_id":...,"language":...,"sample_rate":16000}` → 音频按 ≤64KB 分帧发送 → `{"type":"end"}` → 接收单条 `result` → 关闭连接。文本 strip 后长度 < 2（`min_final_len`，构造参数，默认 2）的识别结果 SHALL 返回**空 alternatives**（StreamAdapter 跳过 FINAL 发射，防噪声伪轮次）。上游连接/服务错误 SHALL 分类为可重试的 `APIConnectionError`（SDK `recognize` 基类按 `conn_options` 重试，避免连续错误熔断拆通话）。

#### Scenario: VAD 切段驱动整段识别
- **WHEN** 用户说完一段话，session silero VAD 触发 END_OF_SPEECH
- **THEN** StreamAdapter SHALL 以该段缓冲音频调用插件 `recognize`
- **AND** 插件 SHALL 经独立 WS 连接完成 config→音频→end→单 result 流程并返回 FINAL_TRANSCRIPT 事件

#### Scenario: 短文本过滤
- **WHEN** agent-asr 返回文本为单字符（如"嗯"）
- **THEN** 插件 SHALL 返回空 alternatives
- **AND** StreamAdapter SHALL 不发射该 FINAL_TRANSCRIPT，SHALL NOT 产生空转写轮次

#### Scenario: 上游断连
- **WHEN** agent-asr WS 连接中断或返回 error 消息
- **THEN** 插件 SHALL 抛可重试连接异常由 SDK 基类按 conn_options 重试
- **AND** 通话 SHALL NOT 因瞬时断连被熔断关闭

#### Scenario: 长段分帧
- **WHEN** 单语音段音频超过 64KB（约 2s 以上）
- **THEN** 插件 SHALL 拆分为多个 ≤64KB binary 帧发送
- **AND** 服务端 SHALL 累积全部帧于 `end` 后整段识别

### Requirement: 打断与轮次参数配置

AgentSession SHALL 配置 `vad=inference.VAD(min_silence_duration=settings.vad_min_silence_duration)`（本地 silero）、`turn_handling={"turn_detection":"vad"}`（轮次起止由 VAD 判定；识别转写经 StreamAdapter 的非流式 STT 供给，轮次提交 SHALL 等待已就绪的 final 转写）、`aec_warmup_duration=None`。endpointing 与打断参数 SHALL 经 `CALLBOT_` 前缀配置暴露：`CALLBOT_ENDPOINTING_MIN_DELAY`（默认 0.1s，语音结束后提交窗口下限，语义不变）、`CALLBOT_ENDPOINTING_MAX_DELAY`（默认 2.0s，窗口上限）、`CALLBOT_INTERRUPTION_MIN_DURATION`（默认 0.5s）、`CALLBOT_VAD_MIN_SILENCE_DURATION`（默认 0.25s，silero 判定语音结束的静音时长——原 FSMN 尾静音确认角色的承接配置）。

#### Scenario: barge-in
- **WHEN** AI 播报中用户说话累计 ≥ min_duration
- **THEN** VAD SHALL 触发打断：清空下行缓冲、取消当前回复任务
- **AND** 用户后续语音 SHALL 正常进入新一轮识别

#### Scenario: 轮次提交等 final
- **WHEN** VAD 判定用户说完（END_OF_SPEECH 后 endpointing 窗口期满）
- **THEN** 提交的用户轮次 SHALL 已包含该段 `recognize` 返回的完整转写
- **AND** SHALL NOT 出现转写缺失/空轮次进入 llm_node

#### Scenario: 端点时延可调
- **WHEN** 运维将 `CALLBOT_VAD_MIN_SILENCE_DURATION` 调大（如 0.5）
- **THEN** silero 判定语音结束 SHALL 相应变保守，无需改码或重启以外的操作

## ADDED Requirements

### Requirement: agent-asr 无状态整段识别 WS 服务

agent-asr 的 `WS /ws/asr/streaming-recognize` SHALL 为无状态整段识别服务：服务端 SHALL NOT 加载 VAD 模型、SHALL NOT 维护跨消息语音状态机。协议 SHALL 为：客户端发送 `{"type":"config","call_id":...,"language":...,"sample_rate":...}` → 若干 binary PCM 帧（每帧按声明采样率重采样到 16kHz 后累积）→ `{"type":"end"}` → 服务端整段 `engine.recognize` → 回单条 `{"type":"result","text":...,"confidence":...,"is_final":true}` → 服务端关闭连接。`reset` 消息与 VAD 降级路径 SHALL 不存在（切段职责在客户端，打断由客户端 SDK 处理）。空音频 `end` SHALL 返回空文本 result。

#### Scenario: 整段识别
- **WHEN** 客户端 config → 多个 binary 帧 → end
- **THEN** 服务端 SHALL 将累积音频（重采样后）整段送 engine.recognize 并回单条 result
- **AND** 回包后 SHALL 关闭连接（无 keep-alive 多段复用）

#### Scenario: 非 16kHz 声明
- **WHEN** config 声明 sample_rate=8000 且发送 8k PCM
- **THEN** 服务端 SHALL 线性重采样到 16kHz 后累积，engine 收到 16kHz 音频

#### Scenario: 无 VAD 资产
- **WHEN** agent-asr 服务启动
- **THEN** 进程 SHALL NOT 加载 FSMN-VAD 模型（无 `load_fsmn_vad_model` 调用、无 `vad_segmenter` 模块、models/ 下无 FSMN 权重目录）
