# 实现计划：remove-fsmn-vad-from-agent-asr

## 来源
- 提案：openspec/changes/remove-fsmn-vad-from-agent-asr/proposal.md
- 设计：openspec/changes/remove-fsmn-vad-from-agent-asr/design.md
- 规格：openspec/changes/remove-fsmn-vad-from-agent-asr/specs/conversation-pipeline/spec.md
- 任务：openspec/changes/remove-fsmn-vad-from-agent-asr/tasks.md

> **已验证 SDK 事实（安装版 livekit-agents 1.8.3，conda base）——实现时直接引用，勿重推导**：
> 1. 默认 `stt_node`（`livekit/agents/voice/agent.py` `Agent.default.stt_node`）在 `capabilities.streaming=False` 且 session 有 VAD 时自动 `stt.StreamAdapter(stt=..., vad=activity.vad)`——**不要手动包装**。
> 2. `StreamAdapterWrapper`：VAD `END_OF_SPEECH` → `merge_frames(event.frames)` → `wrapped_stt.recognize()` → `FINAL_TRANSCRIPT`；`alternatives` 为空**或** text 为空 → `continue` 跳过（短文本过滤靠返回空 alternatives）。
> 3. `APIConnectionError` 从 `livekit.agents` 顶层导入（`livekit.agents.stt` 未 re-export，见现 test_stt_plugin.py 头注释）。
> 4. `STT.recognize()` 基类按 `conn_options` 重试（AgentSession 默认 max_retry=3）。
> 5. `inference.VAD(model='silero', min_speech_duration=0.05, min_silence_duration=0.25, ...)`——构造器直接吃 VAD 参数。
> 6. `TurnDetectionMode` 合法值含 `"vad"`。
>
> 测试命令：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v`（conda base）；`cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v`

## 实现步骤

### Task 1: agent-asr 无状态整段识别服务
- 目标：WS 协议改为「config → binary 累积 → end → 整段识别 → 单 result → 关连接」，删除全部 VAD 逻辑
- 来源：tasks.md §1（design.md「agent-asr 组件设计」）
- 改动文件：`agent-asr/asradapter/ws_server.py`、`agent-asr/asradapter/main.py`、`agent-asr/tests/test_ws_server.py`；删除 `agent-asr/asradapter/vad_segmenter.py`、`agent-asr/tests/test_vad_segmenter.py`
- 验证方式：`cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v` 全绿 + grep 无 fsmn 残留
- 步骤：
  1. 重写 `ws_server.py`：`ASRWebSocketHandler(engine)`（去 segmenter 参数）；`handle()` 循环——`config` 记录 call_id/language/declared_sr → binary 帧逐帧 `_resample_to_16k` 后累积 → `end` 时整段 `_recognize_and_push`（复用现方法）→ return（FastAPI 关连接）；空音频回 `{"type":"result","text":"","confidence":0.0,"is_final":true}`；删除 reset 分支、degraded 状态机、`pending_audio` 降级路径；`_resample_to_16k` 原样保留；模块 docstring 协议说明同步
  2. `main.py`：删 `from asradapter.vad_segmenter import FsmnVadSegmenter, load_fsmn_vad_model` 与 lifespan 的 `_vad_model = load_fsmn_vad_model()`（含日志行）；`ws_streaming_recognize` 中 handler 构造改 `ASRWebSocketHandler(engine)`，docstring 协议描述同步
  3. 重写 `tests/test_ws_server.py`（沿用 `_FakeWS`/mock engine 结构）：多 binary 帧累积后 `end` → engine 收到拼接音频、客户端收单 result、连接终结（handle 返回）；`sample_rate=8000` 声明 → engine 收到重采样后 16k 长度；无音频直接 `end` → 空文本 result；删除 reset/degraded/多 final 用例
  4. 删除 `vad_segmenter.py`、`test_vad_segmenter.py`；`ls agent-asr/models/` 确认无 FSMN 目录（预期只有 SenseVoiceSmall）
  5. `grep -rn "fsmn\|FsmnVad\|vad_segmenter" agent-asr/asradapter agent-asr/tests` 应无输出

### Task 2: agent-flow 新增 VAD 端点配置
- 目标：silero 静音判定阈值可配置（承接 FSMN 尾静音角色）
- 来源：tasks.md §2（design.md D3）
- 改动文件：`agent-flow/src/config.py`、`agent-flow/tests/voice/test_config_livekit.py`
- 验证方式：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_config_livekit.py -v`
- 步骤：
  1. `config.py` 在 `interruption_min_duration` 附近加 `vad_min_silence_duration: float = 0.25`，注释：silero 判定语音结束的静音时长（原 agent-asr FSMN-VAD 尾静音确认角色的承接配置；0.25 = SDK 1.8.3 默认）
  2. `test_config_livekit.py` 加断言 `settings.vad_min_silence_duration == 0.25`

### Task 3: TransvoiceSTT 重写为非流式批量插件
- 目标：插件仅实现 `_recognize_impl`（每段一连接），流式事件交给 SDK 自动 StreamAdapter
- 来源：tasks.md §3（design.md D1/D4/D5 +「agent-flow stt_plugin 设计」）
- 改动文件：`agent-flow/src/voice/stt_plugin.py`、`agent-flow/tests/voice/test_stt_plugin.py`
- 验证方式：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_stt_plugin.py -v`
- 步骤：
  1. 重写 `stt_plugin.py`：`TransvoiceSTT(STT)` 构造 `capabilities=STTCapabilities(streaming=False, interim_results=False)`，保留 `ws_url`/`min_final_len=2` 参数与 `_SINGLE_ATTEMPT_CONN_OPTIONS`；`model="sensevoice"`、`provider="transvoice"`；**删除** `TransvoiceRecognizeStream` 与 SOS/FINAL/EOS 映射
  2. `_recognize_impl(buffer, *, language, conn_options)`：`rtc` AudioBuffer → int16 bytes（`bytes(frame.data)` 或 combine）；`websockets.connect(ws_url, ping_interval=120, ping_timeout=180)`；发 `{"type":"config","call_id":shortuuid,"language":language,"sample_rate":16000}`；音频按 **≤64KB** 切片逐帧 `ws.send`；发 `{"type":"end"}`；`recv` 循环收单条 `result`（text/confidence）→ `ws.close()`；正常文本 → `SpeechEvent(type=FINAL_TRANSCRIPT, alternatives=[SpeechData(language=language, text, confidence)])`；`text.strip()` 长度 < `min_final_len` → 返回**空 alternatives** 的 SpeechEvent；连接失败/`error` 消息/断连/JSON 异常 → `raise APIConnectionError`（顶层导入）；`ConnectionClosedOK` 在收到 result 前发生视为异常
  3. 重写 `tests/test_stt_plugin.py`（沿用 `FakeUpstreamWs` + monkeypatch `websockets.connect`）：正常文本 → `await plugin.recognize(fake_frame_buffer)` 返回 FINAL 事件含 text/confidence；短文本（"嗯"）→ 空 alternatives；发帧序列断言（首帧 config JSON、音频帧 ≤64KB、末帧 end JSON）；`error` 消息/断连 → `pytest.raises(APIConnectionError)`（`conn_options=APIConnectOptions(max_retry=0)` 直调单次尝试）；多 AudioFrame buffer 合并断言

### Task 4: session 装配切换 vad 轮次检测
- 目标：`turn_detection="vad"` + VAD 参数化；集成测试走真 StreamAdapter 路径并守护「轮次提交等 final」
- 来源：tasks.md §4（design.md D2 + 风险 R1）
- 改动文件：`agent-flow/src/voice/session.py`、`agent-flow/tests/voice/test_session_integration.py`、（按需）`agent-flow/tests/voice/test_agent.py`
- 验证方式：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/ -v` 全绿
- 步骤：
  1. `session.py` `_turn_handling_options()`：`"turn_detection": "stt"` → `"vad"`，注释改为「轮次起止由 silero VAD 判定；识别转写经 StreamAdapter 非流式 STT 供给」；endpointing/interruption 块不动
  2. `build_agent_session` 加 `vad=None` 参数：`vad = vad or inference.VAD(min_silence_duration=settings.vad_min_silence_duration)`，传入 `AgentSession(vad=...)`；注释：不手动包 StreamAdapter（默认 stt_node 对 streaming=False 自动包装，见 plan 头部事实 1）；对齐现有 apm/denoiser 注入模式
  3. `test_session_integration.py`：删 `ScriptedRecognizeStream`（流式 fake）→ 新 `FakeBatchSTT(stt.STT)`（`streaming=False`，`_recognize_impl` 返回脚本文本，记录收到的 buffer）；VAD 用 fake（发 START_OF_SPEECH / 含帧的 END_OF_SPEECH，参考 `agents/tests/fake_vad.py` 的 `TriggerFakeVADStream` 做法）经 `build_agent_session(vad=fake_vad)` 注入；断言：END_OF_SPEECH 后轮次提交，`llm_node`（或 fake LLM 捕获的 user item）收到完整转写文本——**守护 EOS 先于 FINAL 的竞态（R1）**；删除原「FINAL 之后才能 EOS」注释与断言
  4. 跑 `tests/voice/test_agent.py`，如引用 streaming STT 形态则同步适配；无引用则不动
  5. 若步骤 3 出现「提交早于 final」（转写空进 llm_node）→ 执行 design.md R1 回退：`turn_detection` 改回 `"stt"`（StreamAdapter EOS 同样源自 VAD，语义等价），测试同构覆盖，并在 design.md 记录回退原因

### Task 5: 文档同步
- 目标：仓库文档与新架构一致，无 FSMN 残留表述
- 来源：tasks.md §5
- 改动文件：根 `CLAUDE.md`、`agent-asr/README.md`、`agent-flow/README.md`
- 验证方式：grep 仅 openspec/changes/ 历史文档允许含 fsmn
- 步骤：
  1. 根 `CLAUDE.md`：Project Overview 与 Architecture 数据流中「FSMN-VAD 服务端分段/多 final 主动推」→「silero VAD 切段 + StreamAdapter 非流式 STT」；「轮次端点由 agent-asr FSMN-VAD…（turn_detection="stt"）」→「轮次端点由 silero VAD（turn_detection="vad"）」；Five Components 的 agent-asr/agent-flow 段；Key Modules 表 `stt_plugin.py` 行、删 `vad_segmenter.py` 相关行（Project Structure 的 `vad_segmenter.py` / `ws_server.py` 注释）；Configuration 表：`CALLBOT_ENDPOINTING_*` 注释改为「语音结束后提交窗口（vad 模式同样适用）」、新增 `CALLBOT_VAD_MIN_SILENCE_DURATION`（默认 0.25）；「VAD 端点检测/打断」条目改写
  2. `agent-asr/README.md`：WS 协议章节改整段识别协议（config→binary→end→单 result→关连接）；删 FSMN-VAD/分段/降级描述与模型要求
  3. `agent-flow/README.md`：STT 插件描述改非流式批量 + StreamAdapter 自动包装；turn_detection 表述同步
  4. `grep -rni "fsmn" CLAUDE.md agent-asr/README.md agent-flow/README.md agent-asr/asradapter agent-flow/src` 确认无输出

### Task 6: 全量回归
- 目标：两侧测试全绿 + 启动冒烟验证无 VAD 加载
- 来源：tasks.md §6
- 改动文件：无（纯验证）
- 验证方式：双侧 pytest + 冒烟
- 步骤：
  1. `cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v` 全绿
  2. `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v` 全绿
  3. 冒烟（可选，需本地服务）：`./scripts/local.sh asr` 启动日志无 "FSMN-VAD model loaded"；起 flow 后 SIP 呼入一通验证轮次提交与打断（无 GPU 环境则跳过并在收尾说明）
  4. 回滚预案确认：协议两侧原子变更，`git revert` 整体回滚（tasks.md 回滚说明）
