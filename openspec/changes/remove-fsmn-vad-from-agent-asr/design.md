# Design: 去掉 agent-asr FSMN-VAD，端点检测移交 livekit silero VAD

## 决策总览（对应 proposal 三个设计张力）

| # | 张力 | 决策 |
|---|------|------|
| D1 | STT 形态 | 非流式批量 STT，依赖 SDK 默认 `stt_node` **自动** `StreamAdapter` 包装（不手动包） |
| D2 | 轮次端点 | `turn_detection="vad"`（SDK 有 VAD 时的自动选择优先级，见下） |
| D3 | endpointing 配置映射 | `CALLBOT_ENDPOINTING_MIN/MAX_DELAY` **语义不变**（语音结束后提交窗口，stt/vad 模式通用）；FSMN 尾静音确认角色由**新增** `CALLBOT_VAD_MIN_SILENCE_DURATION`（默认 0.25s）承担 |
| D4 | 连接策略 | `_recognize_impl` 每语音段一条 WS 连接（connect → config → 音频 → end → 单 result → close） |
| D5 | 短段过滤 | 保留在插件内：文本长度 < `min_final_len`（默认 2）→ 返回**空 alternatives**，由 StreamAdapter 跳过 FINAL 发射（源码 `len(t_event.alternatives) == 0: continue`） |

## 已验证的 SDK 事实（安装版 1.8.3，conda base env 实测 + vendored `agents/` 参考源码）

1. **StreamAdapter 存在且语义匹配**：`stt.StreamAdapter(stt=, vad=)`；`StreamAdapterWrapper._run()` 转发输入帧到 `vad_stream`，在 `VADEventType.END_OF_SPEECH` 时 `merge_frames(event.frames)` → `wrapped_stt.recognize(buffer)` → 发射 `FINAL_TRANSCRIPT`；空文本跳过。flush 哨兵透传 `vad_stream.flush()`。
2. **自动包装**：`voice/agent.py` `Agent.default.stt_node`——`not activity.stt.capabilities.streaming` 且 `activity.vad` 存在时自动 `StreamAdapter(stt=wrapped_stt, vad=activity.vad)`。`TransvoiceAgent` 只覆盖 `llm_node`（agent.py:97），**未覆盖 stt_node** → 自动包装路径对本项目生效。
3. **TurnDetectionMode**：`Literal["stt","vad","realtime_llm","manual"] | _TurnDetector | _StreamingTurnDetector`；缺省自动选择优先级 `realtime_llm → vad → stt`。**"vad" 是有 VAD 时的 SDK 默认**——本项目 session 已有 `vad=inference.VAD()`。
4. **inference.VAD 构造器直接吃 VAD 参数**：`min_speech_duration=0.05, min_silence_duration=0.25, prefix_padding_duration=0.5, activation_threshold=0.5, ...`（1.8.3 安装版默认 `min_silence_duration=0.25`）。
5. **EndpointingOptions 语义**：`min_delay` =「最后一次语音到判定轮次完成的最小等待」、`max_delay` = 上限；对 stt/vad 两模式通用；SDK 默认 0.5/3.0，本项目已配 0.1/2.0。
6. **重试**：`STT.recognize()` 基类按 `conn_options`（AgentSession `stt_conn_options`，默认 max_retry=3）重试；`_recognize_impl` 抛 `APIConnectionError` 即可重试。StreamAdapter 自身连接参数 `max_retry=0`（不与内层重试叠加）。
7. **EOU 等 final 机制存在**：`audio_recognition.py` 跟踪 `last_final_transcript_time` / `transcription_delay`（`_EndOfTurnInfo`），`_eou_wait_not_committed` 计数器存在——vad 模式提交轮次时有等待转写的机器。

## 组件设计

### agent-flow `src/voice/stt_plugin.py`（重写）

```python
class TransvoiceSTT(STT):
    # capabilities=(streaming=False, interim_results=False)
    # _recognize_impl(buffer, language, conn_options):
    #   websockets.connect(ws_url) → send config{call_id, language, sample_rate=16000}
    #   → 音频按 ≤64KB 分帧发送（websockets 服务端单帧上限规避）
    #   → send {"type":"end"} → recv 单条 result → close
    #   → SpeechEvent(FINAL_TRANSCRIPT, [SpeechData(text, confidence)])
    #   → 文本 strip 后 < min_final_len → 返回空 alternatives（StreamAdapter 跳过）
    #   WS/协议错误 → APIConnectionError（基类按 conn_options 重试）
```

- 删除 `TransvoiceRecognizeStream` 与 SOS→FINAL→EOS 三连映射（StreamAdapter 原生产出 SOS/EOS/FINAL）。
- `model` 属性 `"sensevoice-fsmn"` → `"sensevoice"`。
- 保留 `_SINGLE_ATTEMPT_CONN_OPTIONS` 约定（AgentSession 建流显式传 conn_options；直接调用时单次尝试）。

### agent-flow `src/voice/session.py`

- `_turn_handling_options()`：`turn_detection` `"stt"` → `"vad"`；`endpointing`（fixed/min/max）与 `interruption` 不动。
- `vad=inference.VAD(min_silence_duration=settings.vad_min_silence_duration)`。
- `build_agent_session` 新增 `vad=None` 参数：缺省构造上述 silero VAD，测试可注入 FakeVAD（对齐现有 `apm`/`denoiser` 注入模式）。
- **不手动构造 StreamAdapter**——默认 stt_node 自动包装（事实 #2）。

### agent-flow `src/config.py`

- 新增 `vad_min_silence_duration: float = 0.25`（env `CALLBOT_VAD_MIN_SILENCE_DURATION`）。0.25 = 1.8.3 安装版现行为（当前 `inference.VAD()` 裸构造默认），端点体感调优交给运维。

### agent-asr `asradapter/ws_server.py`（重写为无状态）

- `ASRWebSocketHandler(engine)`——去掉 segmenter 注入。
- 协议：`config`（call_id/language/sample_rate）→ 后续 binary 帧累积（每帧过 `_resample_to_16k`）→ `{"type":"end"}` → 整段 `engine.recognize` → 回单条 `result` → return（FastAPI 关闭连接）。
- 删除：`reset` 分支、degraded 状态机、VAD 降级批量路径（新主路径即其形态）、空音频时回空 result 的兜底保留。
- 模块 docstring 协议说明同步。

### agent-asr `asradapter/main.py`

- 删 `from asradapter.vad_segmenter import ...` 与 lifespan 中 `_vad_model = load_fsmn_vad_model()`；handler 构造 `ASRWebSocketHandler(engine)`。

### 删除清单

- `agent-asr/asradapter/vad_segmenter.py`
- `agent-asr/tests/test_vad_segmenter.py`
- `agent-asr/models/speech_fsmn_vad_zh-cn-16k-common-pytorch/`（本地已不存在，任务中验证性清理即可；Dockerfile/deploy/compose/scripts/.env 已确认无 FSMN 引用）
- funasr 依赖**保留**（SenseVoice 引擎仍依赖）

## 测试设计

| 文件 | 改造 |
|------|------|
| `agent-asr/tests/test_ws_server.py` | 重写：多 binary 帧累积 → end → 单 result → 连接终结；8k resample 断言喂 engine 的是 16k；无 reset/degraded 用例 |
| `agent-asr/tests/test_vad_segmenter.py` | 删除 |
| `agent-flow/tests/voice/test_stt_plugin.py` | 重写：FakeUpstreamWs 模拟单 result 协议——正常文本 → FINAL + alternatives；短文本 → 空 alternatives；config/end 帧序列与 ≤64KB 分帧断言；上游断连 → `APIConnectionError` |
| `agent-flow/tests/voice/test_session_integration.py` | STT fake 从流式 `ScriptedRecognizeStream` 改为**非流式 FakeSTT**（仅 `_recognize_impl`）+ 注入 FakeVAD → 走 SDK 真 StreamAdapter 路径；断言「VAD end-of-speech → recognize → 轮次提交时转写完整」（守护 D2 竞态风险） |
| `agent-flow/tests/voice/test_config_livekit.py` | 新增 `vad_min_silence_duration == 0.25` 默认断言 |

## 风险与回退

- **R1（核心风险）vad 模式提交与 recognize 完成的竞态**：StreamAdapter 的 EOS 先于 FINAL（EOS 在 VAD end-of-speech 即发，FINAL 等 recognize 返回）。缓解：事实 #7 的 EOU 机制跟踪 final 时间；集成测试守护不变量「轮次提交时 final 转写已就绪」。若实测提交早于 final（转写缺失进入 llm_node），回退方案 `turn_detection="stt"`——StreamAdapter 的 EOS 同样源自 VAD，语义等价，一行改动，同一测试覆盖。
- **R2 端点体感变化**：FSMN 尾静音确认（约数百 ms）→ silero `min_silence_duration`（默认 0.25s）+ endpointing 窗口（0.1~2.0s）。可经 `CALLBOT_VAD_MIN_SILENCE_DURATION` 调优，无需改码。
- **R3 大帧**：长语音段（60s ≈ 1.9MB）单帧发送可能触发 websockets 接收上限——客户端 ≤64KB 分帧规避；服务端逐帧累积无上限（单段由 VAD `max_buffered_speech=60s` 天然封顶）。
- **回滚**：本变更为 agent-asr/agent-flow 协议两侧原子变更，无中间兼容态；整体 `git revert` 即回滚。

## 架构一致性

- 无新增依赖：StreamAdapter/`inference.VAD` 均来自已 pin 的 livekit-agents 1.8.3。
- 多租户三元组、LangGraph 管线、TTS 插件、录音归档、ESL 生命周期零改动。
- 消除双 VAD（FSMN@agent-asr + silero@agent-flow）→ 单一 silero 实例双流复用（StreamAdapter 一条、turn detection 一条，`vad.stream()` 天然支持多流）。
