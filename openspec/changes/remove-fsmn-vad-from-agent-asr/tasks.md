# Tasks: remove-fsmn-vad-from-agent-asr

按执行依赖排序：agent-asr 协议先行（独立可测）→ agent-flow 配置 → STT 插件（依赖新协议语义）→ session 装配（依赖插件）→ 文档 → 全量回归。

验证环境：agent-flow 测试跑 conda `base`（livekit-agents 1.8.3 安装于 base python3.12）；agent-asr 测试为 mock 型（不依赖 funasr 模型），同一 base 环境可跑（若无 fastapi/websockets/pytest 则在 agent-asr 既有环境跑）。

## Task 1: agent-asr 改无状态整段识别服务

- [ ] 1.1 重写 `agent-asr/asradapter/ws_server.py`：`ASRWebSocketHandler(engine)` 去掉 segmenter 注入；协议改为 config → binary 累积（逐帧 `_resample_to_16k`）→ end → 整段 `engine.recognize` → 单 result → return；删除 reset 分支与 degraded 状态机；空音频回空文本 result；同步模块 docstring
- [ ] 1.2 修改 `agent-asr/asradapter/main.py`：删 `vad_segmenter` import 与 lifespan 的 `load_fsmn_vad_model()`；handler 构造改为 `ASRWebSocketHandler(engine)`
- [ ] 1.3 重写 `agent-asr/tests/test_ws_server.py`：多帧累积→end→单 result→终结；8k resample 断言；空音频→空文本；删除 reset/degraded 用例（沿用现有 `_FakeWS`/mock engine 结构）
- [ ] 1.4 删除 `agent-asr/asradapter/vad_segmenter.py`、`agent-asr/tests/test_vad_segmenter.py`；确认 `agent-asr/models/` 下无 FSMN 目录（本地已不存在，验证性检查）

验证：`cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v` 全绿；`grep -rn "fsmn\|FsmnVad\|vad_segmenter" agent-asr/asradapter agent-asr/tests` 无残留
回滚：git checkout 单任务粒度 revert

## Task 2: agent-flow 新增 VAD 配置

- [ ] 2.1 `agent-flow/src/config.py` 新增 `vad_min_silence_duration: float = 0.25`（`CALLBOT_VAD_MIN_SILENCE_DURATION`），置于 endpointing/interruption 配置组附近并带注释说明承接 FSMN 尾静音角色
- [ ] 2.2 `agent-flow/tests/voice/test_config_livekit.py` 新增默认值断言 `settings.vad_min_silence_duration == 0.25`

验证：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_config_livekit.py -v`

## Task 3: TransvoiceSTT 重写为非流式批量插件

- [ ] 3.1 重写 `agent-flow/src/voice/stt_plugin.py`：`TransvoiceSTT(STT)` 声明 `capabilities(streaming=False, interim_results=False)`；实现 `_recognize_impl`（每段一连接：connect → config → ≤64KB 分帧发音频 → end → 收单 result → close → `SpeechEvent(FINAL_TRANSCRIPT)`）；短文本（< `min_final_len`）返回空 alternatives；WS/协议错误 → `APIConnectionError`；`model="sensevoice"`；删除 `TransvoiceRecognizeStream` 与 SOS/FINAL/EOS 映射
- [ ] 3.2 重写 `agent-flow/tests/voice/test_stt_plugin.py`：正常文本 → FINAL 事件含 alternatives 与 confidence；短文本 → 空 alternatives；发帧序列断言（config 首、音频分帧 ≤64KB、end 末）；上游断连/error → `APIConnectionError`；`recognize()` 直调路径错误即抛（单次尝试约定）

验证：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/test_stt_plugin.py -v`

## Task 4: session 装配切换到 vad 轮次检测

- [ ] 4.1 `agent-flow/src/voice/session.py`：`_turn_handling_options()` 的 `turn_detection` 改 `"vad"`；VAD 构造改 `inference.VAD(min_silence_duration=settings.vad_min_silence_duration)`；`build_agent_session` 新增 `vad=None` 参数（缺省构造 silero，测试注入 FakeVAD），注释注明不手动包 StreamAdapter（默认 stt_node 自动包装）
- [ ] 4.2 改造 `agent-flow/tests/voice/test_session_integration.py`：STT fake 改为非流式（仅 `_recognize_impl` 的 FakeSTT）+ 注入 FakeVAD（START/END_OF_SPEECH 事件可控），走 SDK 真 StreamAdapter 路径；新增断言：轮次提交时 `llm_node` 收到完整转写（守护 EOS/FINAL 竞态，R1）；原 FINAL→EOS 顺序铁律注释删除
- [ ] 4.3 检查 `tests/voice/test_agent.py` 是否受 stt 形态影响，按需适配

验证：`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/voice/ -v` 全绿

## Task 5: 文档同步

- [ ] 5.1 根 `CLAUDE.md`：架构图/数据流中「FSMN-VAD 服务端分段」表述改为「silero VAD + StreamAdapter 切段」；模块表 `stt_plugin.py`/`vad_segmenter.py` 条目更新；配置表新增 `CALLBOT_VAD_MIN_SILENCE_DURATION`、`CALLBOT_ENDPOINTING_*` 语义注释（语音结束后窗口，不变）；Five Components 与 Project Structure 中 FSMN 引用清理
- [ ] 5.2 `agent-asr/README.md`：WS 协议描述改为整段识别协议，删除 VAD 相关段落
- [ ] 5.3 `agent-flow/README.md`：STT 插件描述改为非流式批量 + StreamAdapter；turn_detection 表述同步

验证：`grep -rni "fsmn" CLAUDE.md agent-asr/README.md agent-flow/README.md agent-asr/asradapter agent-flow/src` 仅允许出现于本变更目录 openspec/changes/ 的历史文档

## Task 6: 全量回归

- [ ] 6.1 `cd agent-asr && PYTHONPATH=$(pwd) pytest tests/ -v` 全绿
- [ ] 6.2 `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/ -v` 全绿
- [ ] 6.3 启动冒烟（可选，需本地 GPU 环境）：`./scripts/local.sh asr` 启动日志无 "FSMN-VAD model loaded"；agent-flow 起 session 后呼入一通，验证轮次提交与打断

回滚：整变更为 agent-asr/agent-flow 协议两侧原子变更，无中间兼容态——`git revert` 合并提交即整体回滚
