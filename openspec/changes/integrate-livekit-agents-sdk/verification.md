# 端到端验收记录 — integrate-livekit-agents-sdk

- 分支：`worktree-integrate-livekit-agents-sdk`（base f526d69，本变更 12 commit）
- 验收环境：macOS (Darwin 22.6.0)，conda base Python 3.12.9，livekit-agents 1.8.3
- 记录日期：2026-09-28
- 状态标记：✅ 已验证 ｜ PENDING 需真机通话环境（软电话 / FreeSWITCH / GPU 推理全链路），代码级验收无法替代

## Step 1: 三组件全量测试 — ✅

命令约定：`cd <component> && python -m pytest tests/ -q`（agent-flow 的 `tests/conftest.py` 注入 `src/` 到 sys.path，无需 PYTHONPATH 前缀）。

| 组件 | 结果 | 说明 |
|------|------|------|
| agent-flow | ✅ **97 passed** in 48.11s | 含 `tests/voice/`（io/stt_plugin/tts_plugin/agent/session_integration/flow_astream/config_livekit）、`tests/ws/`（audio_processing/registry）、`tests/graph/`、`tests/outbound/` 及归档/MinIO/persistence 顶层用例 |
| agent-asr | ✅ **15 passed** in 4.34s | `test_vad_segmenter.py` + `test_ws_server.py`，mock 模型权重，无需 GPU |
| agent-tts | ⚠️ 无测试套件 | `tests/` 目录不存在（`ERROR: file or directory not found: tests/`）。main 分支同样没有 agent-tts 测试 —— 属既有空白，非本次改造回归；本变更对 agent-asr/agent-tts 零代码改动（`git diff f526d69..HEAD -- agent-asr agent-tts` 为空） |

附加代码级验证（均 ✅）：

- 依赖：`agent-flow/requirements.txt` 含 `livekit-agents>=1.8.3,<1.9`（tasks.md §1.1）
- 配置：`.env.example` 已含 `CALLBOT_ENDPOINTING_MIN_DELAY=0.1` / `CALLBOT_ENDPOINTING_MAX_DELAY=2.0` / `CALLBOT_INTERRUPTION_MIN_DURATION=0.5`（tasks.md §1.2）
- 退役零残留：`grep -rn "StreamingCallHandler\|TurnController\|AsrStreamingManager\|RMSGate\|asr_ws_client\|tts_ws_client\|SentenceSplitter\|rms_gate\|cooldown_after_bargein\|barge_in_min_audio_bytes\|splitter_\|asr_streaming_enabled\|tts_streaming_enabled\|tts_skip" agent-flow/src agent-flow/main.py` 零命中（tasks.md §7.3 扩展）
- 录音/归档/外呼/console/ESL 生命周期路径未被本变更触碰（`main.py` 仅 `ws_media_fork` 重写，ESL handler 与 `_archive_recording` 原样）

## Step 2: 真实 SIP 呼入验收 — PENDING

前置：`./scripts/local.sh stop && ./scripts/local.sh`（Docker pg/redis/minio → fs → asr → tts → mcp → flow → console 按序就绪），软电话呼入。

| # | 验收项 | 判定锚点（可操作） | 状态 |
|---|--------|--------------------|------|
| 2.1 | ≥3 轮对话正常 | FreeSWITCH 日志（`~/freeswitch/var/log/freeswitch/freeswitch.log`）无 mod_audio_fork 错误；agent-flow 日志每轮出现 `AgentSession started` 后跟 `pre-llm phase done in %.0fms` + `LLM complete: action=... text=...`（flow.py 两行时延日志为轮次完成等价锚点，改造中保留未动） | PENDING |
| 2.2 | AI 播报中说话可打断，打断后新轮次正常 | PG 查询 `SELECT * FROM callbot.call_event WHERE event_type='barge_in' AND call_id='{uuid}'` 有行（`TransvoiceAgent.on_conversation_item_added` 检测 interrupted → `fire_insert_event("barge_in")`）；打断后下一轮 `pre-llm phase done` 正常出现 | PENDING |
| 2.3 | 挂断后双声道录音 | `CALLBOT_RECORDINGS_DIR/{uuid}.wav` 存在，ffprobe 双声道（L=caller / R=AI） | PENDING |
| 2.4 | MinIO 归档 + console 回放 | agent-flow 日志录音归档成功（或 `POST /calls/{uuid}/archive-recording` 兜底后 200）；console 通话详情页 presigned URL 可回放，左右声道可分辨人声/AI 声 | PENDING |
| 2.5 | 首轮响应时延记录 | 见下方"时延记录方法"，改造前后对比填入 | PENDING |

时延记录方法（2.5）：

- 轮次处理时延：agent-flow 日志 `pre-llm phase done in %.0fms`（该行代码在改造中未改动，可与改造前基线直接对比）。
- 首轮端到端时延（用户说完 → AI 首音播出）：取同一通电话录音（双声道）回放，R 声道（AI）相对 L 声道（caller）末次语音结束的首帧起点；或对比 agent-flow 日志 `LLM complete` 时间戳与 TTS 首帧发送。基线 = 改造前（f526d69）同一场景录制。
- 记录格式：`首轮: pre-llm ___ms / 端到端 ___s（基线 ___ms / ___s）`，追加到本表下方。

## Step 3: 外呼验收 — PENDING

前置：console 建外呼任务（CSV 含 `call_target.vars` 占位符，如 `name:张三`，话术含 `{name}`）→ 启动任务 → `OutboundExecutor` originate → 软电话摘机。

| # | 验收项 | 判定锚点 | 状态 |
|---|--------|----------|------|
| 3.1 | 摘机复用 AgentSession 管线 | agent-flow 日志 `AgentSession started (tenant=... biz_type=... scenario=...)`（与呼入同路径 `ws_media_fork`） | PENDING |
| 3.2 | vars 渲染进话术 | agent-flow 日志 `rendered system_prompt (vars=[...])` 中含 call_target.vars 的 key，且渲染后文本含变量值（如"张三"） | PENDING |
| 3.3 | 对话/打断/挂断归档同 2.1-2.4 | 同上 | PENDING |

## Step 4: 文档同步 — ✅

本 commit：`CLAUDE.md`（Project Overview / 审查清单第 1 条 / Architecture 图 Node⑦ / Data flow per turn / Five Components agent-flow / LangGraph 管线段标题+节点⑦+Streaming mode / Configuration 删 RMS gate、VAD 本地、cooldown、barge-in bytes、splitter、streaming、tts_skip 行加 endpointing×2 + interruption 行，Media 行 _resample_pcm 改 SDK 重采样 / Key Orchestrator Modules 表 +`src/voice/{io,stt_plugin,tts_plugin,agent,session}.py` +`esl_events.py` −handler/rms_gate/旧WS客户端×2/sentence_splitter，flow.py 行改 run_pre_llm_phase/astream_reply_text（StateGraph 已不存在）/ Project Structure agent-flow 子树 / Infrastructure WebSocket 行）+ `agent-flow/README.md`（标题与功能段 AgentSession 化、提示词缓存 key 修正为 `(tenant_id, biz_type, scenario)` 三维、端点表补归档接口）。

## 回滚说明

本变更无数据迁移、DB schema 零变更，`git revert` 整组 commit 即可恢复旧管线。
