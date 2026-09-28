# Proposal: 去掉 agent-asr 的 FSMN-VAD，端点检测移交 livekit silero VAD

## Why

agent-flow 已集成 livekit-agents（`integrate-livekit-agents-sdk` 变更），AgentSession 进程内本就运行着 silero VAD（`inference.VAD()`）承担 barge-in 打断检测。当前架构因此**双 VAD 并行**：

- agent-asr 侧：FSMN-VAD 服务端流式分段（`vad_segmenter.py`：chunk 累积、绝对 ms 切片、滑窗截断、故障降级状态机）驱动 `turn_detection="stt"`
- agent-flow 侧：silero VAD 仅辅助打断，不参与轮次判定

SenseVoice 是离线模型，切段是它的前置条件——但切段者不必在服务端。切段移到 agent-flow 后：

1. **消除双 VAD 冗余**：单一切段/端点来源（silero），消除两套 VAD 端点标准不一致的隐患
2. **agent-asr 简化**：退化为无状态整段识别服务，删掉 VAD 状态机 + 降级路径 + 进程级 VAD 模型加载（内存/启动开销）
3. **对齐 livekit 原生模式**：非流式 STT + `StreamAdapter` 是 SDK 对离线识别引擎的标准接法（upstream Gnani 插件同款），删掉 agent-flow 侧自研的 SOS/FINAL/EOS 手工合成映射

## What Changes

1. **agent-flow STT 插件改造**：`TransvoiceSTT` 从自研流式插件（`RecognizeStream` 子类 + 事件三连映射）改为非流式批量 STT——实现 `_recognize_impl`（每语音段一次 WS 识别请求），由 `stt.StreamAdapter` + silero VAD 切段驱动；`session.py` `turn_detection` 从 `"stt"` 切到 VAD 驱动，删除 `_turn_handling_options` 中锚定 STT EOS 的 fixed endpointing 语义。
2. **agent-asr WS 协议简化**：`/ws/asr/streaming-recognize` 改为「`config` + 完整语音段 binary + `end` → 回单个 `result` 后关连接」——现有 VAD 故障降级路径（累积到 end 整段 batch 识别）转正为主路径。服务端无状态，`reset` 消息取消（barge-in 由 SDK 切段方处理）。
3. **删除 agent-asr VAD 代码**：`vad_segmenter.py`、`main.py` 的 FSMN 模型加载（`load_fsmn_vad_model`）、`tests/test_vad_segmenter.py`。
4. **删除 FSMN 模型资产**：`agent-asr/models/speech_fsmn_vad_zh-cn-16k-common-pytorch/` 权重目录（SenseVoice 权重不动）。
5. **配置语义迁移**：`CALLBOT_ENDPOINTING_MIN/MAX_DELAY` 保留环境变量名，语义映射到 silero VAD 静音参数（具体映射关系 spec 阶段确定），部署侧 `.env` 不需要改名。
6. **文档同步**：agent-asr / agent-flow README 与根 CLAUDE.md 中 FSMN-VAD 相关描述（架构图、数据流、配置表、模块表）。

## 成功标准

- [ ] agent-asr / agent-flow 测试全绿（`test_vad_segmenter.py` 删除，`ws_server` 用例改为整段识别语义；agent-flow `stt_plugin` / `session` 装配用例更新）。
- [ ] 端到端通话行为不回退：轮次提交、barge-in 打断、多段连续对话正常。
- [ ] agent-asr 启动不再加载 FSMN-VAD 模型（启动日志与内存占用验证）。
- [ ] 打断检测（本就在 agent-flow silero VAD）不受影响。
- [ ] `_resample_to_16k` 等仍被整段识别路径复用的逻辑不丢失。

## 边界（不在范围内）

- SenseVoice 引擎实现、TTS 侧（CosyVoice/插件）、MCP/LangGraph 管线、录音归档、OutboundExecutor 不动。
- 不做云引擎接入、不做 ASR 引擎更换。
- ASR WS 连接复用策略（每段一连接 vs 共享连接 + request_id 解复用）是设计决策，不属于协议兼容承诺。
- 不清理 funasr 依赖本身（SenseVoice 引擎仍依赖）。

## 约束

- livekit-agents 版本维持 `>=1.8.3,<1.9`（已 pin，`StreamAdapter` 在 vendored 参考仓库 `agents/` 中有源码与测试可对照）。
- 全链路 16kHz 单声道不变；客户端声明的 `sample_rate` 重采样逻辑保留。
- `.env` 兼容：`CALLBOT_ENDPOINTING_MIN/MAX_DELAY` 变量名不变（语义迁移）。

## 验证依据

- `agents/livekit-agents/livekit/agents/stt/stream_adapter.py`：SDK 原生 `StreamAdapter`（VAD 切段 → 批量 recognize 包装）。
- vendored Gnani 插件 docstring（`livekit-plugins-gnani/stt.py`）：非流式 STT + 本地 VAD 时 SDK 自动包 `StreamAdapter` 的官方先例。
- `agent-asr/asradapter/ws_server.py` 现有 degraded 路径（VAD 异常 → 累积 → end 整段 batch 识别）即目标协议形态，已在生产代码中存在。
- `agent-flow/src/voice/stt_plugin.py` 现有 `_recognize_impl` 抛 `NotImplementedError`（"batch 模式未启用"），本次即启用该路径。

## 设计张力（spec/brainstorming 待决）

1. **endpointing → VAD 参数映射**：`endpointing_min_delay`/`max_delay` 如何对应 silero `min_silence_duration` / `prefix_padding_duration` / `min_speech_duration`；`turn_handling` dict 里 fixed endpointing 配置的去留。
2. **连接策略**：`_recognize_impl` 每段新建 WS 连接（简单，localhost 延迟可忽略）vs 共享连接 + request_id 解复用（TTS 插件已有先例，但引入复杂度）。
3. **短段过滤迁移**：现 `min_final_len`（文本 <2 字符整组丢弃）由谁承担——silero `min_speech_duration`（音频侧过滤）vs 保留插件内文本长度判断。
