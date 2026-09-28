# Proposal: agent-flow/asr/tts 集成 livekit-agents SDK

## Why

agent-flow 当前自研整条流式通话管线：JitterBuffer → WebRTCAPM/降噪 → ASR WS 客户端（服务端 FSMN-VAD 分段驱动轮次）→ TurnController → SentenceSplitter → TTS WS 客户端 → TTSOutputBuffer（30ms 静音保活）→ RMSGate 自适应门禁 barge-in。这些组件持续暴露维护负担（打断误触发、轮次边界、playout 节奏），且与业界标准 Agent 框架的能力重复。

livekit-agents SDK（参考仓库 `agents/`，upstream [livekit/agents](https://github.com/livekit/agents) @ v1.8.3）在 `livekit.agents.voice` 中提供了经过大规模生产验证的等价原语：AgentSession 对话循环、turn detection / endpointing、playout（SpeechHandle）、基于 VAD 的原生 interruption（barge-in）、token accumulate + sentence tokenize、STT/TTS/LLM/VAD 插件接口体系。

迁移到 SDK 框架可获得：维护过的 turn-taking 与打断逻辑、插件生态扩展点（后续可换 Deepgram/Cartesia 等云引擎对照）、结构化的 STT/TTS 抽象（SenseVoice/CosyVoice 以插件形式标准化）。

**可行性已验证**：`voice/io.py` 提供 `AudioInput`/`AudioOutput`/`AgentInput`/`AgentOutput` 抽象接口；SDK 自身测试套件（`tests/fake_io.py`）即以自定义 IO 脱离 LiveKit Server 运行 AgentSession，`voice/recorder_io` 是另一个非 Room IO 先例。因此「AgentSession 框架 + 自定义电话 IO 桥接 mod_audio_fork WebSocket」的组合成立，**无需引入 LiveKit Server / RTC**。

## What Changes

1. **agent-flow 管线迁移**：自研 `StreamingCallHandler` 流式管线替换为 livekit-agents **AgentSession** + 自定义 TelephonyIO 适配层 —— `AudioInput` 消费 `/media/{uuid}` WebSocket 上行音频，`AudioOutput` 回写下行 PCM。turn detection、playout、打断（interruption）交给 SDK；AgentSession 在 agent-flow 进程内 per-call 实例化（不用 Worker/job 模型）。
2. **STT 远程插件**：实现 livekit `stt.STT` 流式接口的远程客户端插件，对接现有 agent-asr WebSocket（`ws://*:8080/ws/asr/streaming-recognize`，FSMN-VAD 分段多 final 语义映射为 SDK SpeechEvent）。
3. **TTS 远程插件**：实现 livekit `tts.TTS` 流式接口的远程客户端插件，对接现有 agent-tts WebSocket（`ws://*:8081/ws/tts/streaming-synthesize`）。
4. **自研组件退役**（以设计阶段确认为准）：RMSGate → SDK 原生 interruption；TTSOutputBuffer → SDK playout；SentenceSplitter → SDK tokenize；TurnController → SDK turn detection。JitterBuffer / WebRTCAPM（AEC/NS/AGC）的去留与挂载位置为设计决策。
5. **业务层保持**：LangGraph 7-node 管线（MCP/记忆/RAG/多租户 prompt）语义保留，通过适配层嵌入 AgentSession 对话循环（嵌入方式为 brainstorming 核心设计点）；录音归档、OutboundExecutor 外呼、console 兼容不变。
6. **agent-asr / agent-tts 服务本身不动**：GPU 推理进程、端口、WS 接口契约不破坏；插件是 agent-flow 侧（或新适配模块）的纯客户端。

## 成功标准

- [ ] SIP 真实呼入 → ASR → LLM → TTS → 挂断全链路真实跑通（端到端通话可用）。
- [ ] 用户说话可打断 AI 播报（barge-in），无误触发回归。
- [ ] FS `uuid_record` 双声道录音 + `_archive_recording` MinIO 归档不受影响。
- [ ] 多租户 `(tenant_id, biz_type, scenario)` 三元组 prompt 加载与变量渲染不受影响。
- [ ] agent-asr(:8080) / agent-tts(:8081) 独立部署不动，WS 接口契约不破坏（现有测试通过）。
- [ ] 外呼 originate 摘机后复用同一 AgentSession 管线。
- [ ] agent-flow 现有测试 + 新增插件/适配层测试全部通过。

## 边界（不在范围内）

- 不引入 LiveKit Server / RTC / Room —— 媒体传输保持 FreeSWITCH + mod_audio_fork WebSocket。
- 不改 agent-asr / agent-tts 内部推理与引擎插件结构（SenseVoice/CosyVoice 进程不动）。
- 不改 java-mcp-server 与 console。
- 不改 OutboundExecutor 调度/重拨逻辑（仅保证摘机后管线复用）。
- LangGraph 7-node 的业务语义不变（内部适配、节点实现允许调整接线）。
- 不做云引擎（Deepgram/Cartesia 等）接入 —— 仅打通本地引擎插件化。

## 约束

- 参考实现以 `agents/` 仓库为准（livekit-agents 1.8.3）；依赖以 pip 安装 `livekit-agents`（版本对齐），不 vendor 源码。
- 全链路 16kHz 单声道；TTS 22050Hz 输出的重采样位置随 playout 迁移到适配层。
- ESL 生命周期（CHANNEL_ANSWER/HANGUP → audio_fork start/stop → registry → 归档）保持现有事件驱动结构。
- uvloop 事件循环保留。
- 打断语义映射（RMSGate 冷却期 → SDK interruption 配置）需在设计阶段对齐，防误触发标准不低于现状。

## 验证依据

- `agents/` 为 livekit/agents upstream monorepo（git describe: `livekit-agents@1.8.3-9-g57b3227a7`），含 `livekit-agents` 主包、全部 plugins、examples（含 telephony SIP 示例）、tests。
- `livekit-agents/livekit/agents/voice/io.py`：`AudioInput`/`AudioOutput` 抽象 + `AgentInput`/`AgentOutput` 组合接口。
- `agents/tests/fake_io.py`：AgentSession 以自定义 FakeAudioInput/FakeAudioOutput 运行（无 LiveKit Server、无 Room/RTC）——headless 运行模式的官方先例。
- `agents/livekit-agents/livekit/agents/voice/` 模块清单：agent_session、speech_handle（playout）、turn（turn detection）、endpointing、transcription、audio_recognition 等。

## 设计张力（brainstorming 待决）

1. **LangGraph 7-node 嵌入方式**：自定义 ChatModel 适配器包裹 LangGraph 管线流式输出 vs 拆用 SDK 组件不走默认对话循环。
2. **VAD / turn detection 分工**：FSMN-VAD 在 agent-asr 服务端分段 vs SDK 端 VAD plugin（silero）+ endpointing —— STT 插件如何呈现分段 final。
3. **AEC/降噪挂载位置**：WebRTCAPM 保留在 AudioInput 侧逐帧处理 vs 换用 SDK 生态 audio processor。
4. **自研组件退役清单**：JitterBuffer 是否仍必要（SDK 侧缓冲语义）、静音保活由谁负责（dialplan silence_stream vs playout）。
