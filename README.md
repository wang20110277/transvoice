# 智能外呼系统
采用分层流向架构，从底层通信层→语音交换层→语音识别层→流程编排层→业务服务层→大模型智能层→存储层→语音合成层→语音播放层，严格按业务数据流顺序绘制，**全程同步透传四维隔离键 `(tenant_id, biz_type, scenario, user_key)` 与通话标识 CallID/fs_uuid**，标注所有通信协议、调用方式、事件通知、数据传递逻辑，完整业务流转流程如下：

> **多租户四维隔离**：自 tenant-management 变更起，业务隔离键由「仅 3 biz_type」升级为 `(tenant_id, biz_type, scenario)` 三元组（加上 per-user 的 `user_key`）。DID 不再在拨号计划硬编码，由 agent-flow 查 `callbot.inbound_route` 解析三元组。详见「多租户四维隔离」章节。

1. **接入层**：SIP User发起外呼通话，通话媒体流、通话唯一标识 CallID/fs_uuid、主叫/被叫用户手机号同步上行传输
2. **语音交换层**：通话接入 FreeSWITCH 软交换，拨号计划为 **catch-all `^(\d+)$` → answer → playback silence_stream://-1**（无限静音保活）。`tenant_id/biz_type/scenario` **不在 dialplan 硬编码**，由 agent-flow 在 CHANNEL_ANSWER 读取被叫号(DID)后查 `callbot.inbound_route` 解析。全程携带 CallID+手机号
3. **WebSocket音频流层（事件驱动）**：CHANNEL_ANSWER 事件 → agent-flow ESL handler 调用 `uuid_audio_fork start` → FreeSWITCH 建立与 agent-flow 的 WebSocket 双向音频流，**上行传输用户语音 PCM 流，下行接收 TTS 合成音频**，全程携带四维隔离键。agent-flow 运行在 **uvloop 事件循环**上（libuv C 实现），减少高并发下 GC 停顿
4. **语音识别层**：agent-flow 首节点调用 agent-asr（内置 GPU 推理引擎），完成语音转文字。输入音频经 **WebRTCAPM（AEC+NS+AGC）或 Denoiser 降噪** 后全量喂入 agent-asr，由**服务端 FSMN-VAD 分段**做端点检测（无本地 VAD 引擎）。ASR 走 **WebSocket 流式传输**（`asr_ws_client.py`），服务端分段完成后主动回推 final 触发轮次
5. **LangGraph第一业务节点（用户身份核验）**
编排引擎内置 MCP Client，通过 HTTP Streamable 传输协议调用 java-mcp-server 用户中心 MCP 服务，**传入用户手机号**调用 `user_identity_query` 工具；查询获取用户ID、脱敏手机号、身份证后四位，流程全程保留 State 内 ASR 文本、四维隔离键
6. **LangGraph第二业务节点（征信合规核验）**
复用 MCP Client 调用 java-mcp-server，**使用用户ID**调用 `user_credit_query` 工具，获取用户征信档案数据，校验征信资质与风险等级（仅 marketing 业务类型触发），征信不合规直接触发风控预警，以上两个业务节点执行全程保留 LangGraph 状态内的 ASR 原始文本、四维隔离键
7. **LangGraph第三业务节点（LLM智能应答 + TTS语音合成）**
提示词按三维 `get_system_prompt(tenant_id, biz_type, scenario)` 加载（Redis 5min 缓存 → DB 降级）并经 `render.py` 渲染变量占位符；**从 LangGraph 全局 State 中提取完整 ASR 用户识别文本、四维隔离键、用户ID**，统一送入 LLM 大模型；LLM 解析用户语音文本语义，结合用户手机号绑定的历史用户数据，自主判定是否调取 RAG 知识库匹配业务标准话术，最终生成标准化外呼应答话术文本；**流式模式下 LLM 逐 token 输出，通过 IncrementalJSONParser 解析、SentenceSplitter 切分为完整句子，每句并行调用 agent-tts（内置 GPU 推理引擎，WebSocket 流式合成）合成语音音频，PCM 音频经 TTSOutputBuffer 以稳态 30ms 帧率通过 WebSocket 回传 FreeSWITCH**；依托 LangChain Memory 记忆体系做分层数据存储：Redis 存储短期会话记忆、PostgreSQL(PG) 存储长期业务会话数据，每轮对话 fire-and-forget 双写 PG（call_turn），整通录音经 CallRecorder 双声道合并后归档 MinIO
8. **终端播放层**：agent-flow 通过 WebSocket 将 TTS 音频回传 FreeSWITCH，FreeSWITCH 下行推送至 SIP User 通话终端，完成整通智能外呼语音交互闭环
9. **录音归档与打断/挂断控制层**：**录音**：CallRecorder 全程累加双声道 PCM（L=caller VAD前 / R=AI TTS），挂断时 `finalize_stereo_wav` 短边补静音，延迟 3s 读 FS 录音 wav → 上传 MinIO → insert_artifact(kind='recording')。**打断**：用户在 AI 说话过程中开口时，RMSGate（RMS+SNR 自适应门禁）实时检测 → **清空 TTSOutputBuffer（不调用 uuid_break，避免终止 dialplan playback）** → 冷却期防误触发 → 新一轮对话。**挂断**：ESL 订阅 CHANNEL_ANSWER（自动触发 uuid_audio_fork start）和 CHANNEL_HANGUP 事件（uuid_audio_fork stop → ActiveCallRegistry 取消活跃通话 → 录音归档 → 清理资源）

## 统一绘图强制规范
1. 整体布局：数据流从左至右分层排布，层级从上至下划分清晰
2. 明确标注所有协议：SIP、WebSocket、HTTP JSON、MCP
3. 重点高亮：**FreeSWITCH mod_audio_fork WebSocket 直连 agent-flow，双向音频流**
4. 区分交互模式：WebSocket双向音频流、HTTP接口推送、远程服务调用、状态内存取
5. 清晰标注三大风控预警点、三层记忆存储介质、音文件存储介质NAS/OSS
6. **强制标注：CallID、用户手机号双标识全链路全局透传**，业务查询优先依托手机号作为核心查询维度
7. 拆分独立模块：通信接入模块、WebSocket音频流模块、LangGraph编排模块、MCP用户中心模块、LLM+RAG智能话术模块、多级记忆存储模块、GPU推理模块(ASR/TTS内置)
8. 标注身份查询逻辑：手机号查用户中心获取用户ID和身份证，再用用户ID查征信

---

## 项目代码结构

```
aiphone/
├── agent-asr/              # ASR 服务 (FastAPI + WebSocket, 内置 GPU 推理)
│   ├── asradapter/         # 服务核心: main.py, base.py, config.py, requirements.txt
│   │   ├── engines/        # sensevoice/ (GPU), streaming/ (WebSocket), vibevoice/ (远程 HTTP)
│   │   ├── vad_segmenter.py  # FSMN-VAD 流式分段层
│   │   ├── ws_server.py    # WebSocket ASR 服务 (FSMN-VAD 分段 + 多 final 主动推)
│   │   └── models/             # SenseVoiceSmall/ (本地模型权重)
│   ├── deploy/             # systemd 部署单元
│   ├── Dockerfile          # PyTorch GPU 镜像, 模型下载
│   ├── README.md           # 组件文档
│   └── tests/              # test_base, test_main, test_storage, engines/*/
├── agent-tts/              # TTS 服务 (FastAPI + WebSocket, 内置 GPU 推理)
│   ├── CosyVoice/          # CosyVoice 源码 (推理运行时)
│   ├── ttsadapter/         # 服务核心: main.py, base.py, config.py, requirements.txt
│   │   ├── engines/        # cosyvoice/ (CosyVoice3 GPU), edgetts/ (Edge 在线, 无需 GPU), vibevoice/ (远程 HTTP)
│   │   └── ws_server.py    # WebSocket TTS 服务 (流式合成)
│   ├── models/             # CosyVoice3-0.5B/ (本地模型权重)
│   ├── deploy/             # systemd 部署单元
│   ├── Dockerfile          # PyTorch GPU 镜像, 模型下载
│   ├── README.md           # 组件文档
│   └── tests/              # test_base, test_main, test_storage, engines/*/
├── agent-flow/     # LangGraph 7 节点编排 (FastAPI HTTP + WebSocket)
│   ├── main.py             # FastAPI 入口: WS /media/{uuid} (事件驱动 audio_fork), GET /healthz, ESL生命周期
│   ├── src/                # 核心源码 (PYTHONPATH includes src/)
│   │   ├── config.py       # pydantic-settings, CALLBOT_ 环境变量前缀 (含ESL/JitterBuffer/Barge-in/Denoise/AEC/Recording配置)
│   │   ├── database.py     # SQLAlchemy 2.0 async engine
│   │   ├── clients/        # mcp.py (用户中心), esl.py (Event Socket, 自动重连+心跳)
│   │   │                    # asr_ws_client.py, tts_ws_client.py (ASR/TTS WebSocket 唯一传输)
│   │   ├── ws/             # handler.py (StreamingCallHandler 流式+打断+录音+APM), rms_gate.py (RMS 门禁), asr_streaming.py, jitter_buffer.py,
│   │   │                    # registry.py (ActiveCallRegistry 携带 tenant_id/scenario), denoise.py (前置降噪),
│   │   │                    # audio_processing.py (WebRTCAPM AEC/NS/AGC), call_recorder.py (双声道录音)
│   │   ├── graph/          # flow.py (7 节点 StateGraph + 流式管道), prompt.py, prompt_config.py (三维 Redis→DB 加载), render.py (变量渲染)
│   │   ├── llm/            # service.py, json_stream.py (增量JSON), sentence_splitter.py (句级切分)
│   │   ├── memory/         # assembler.py, chat_history.py, redis_memory.py, store.py
│   │   ├── rag/            # retriever.py (Agentic RAG: 自适应检索+文档评分+查询改写)
│   │   ├── db/             # models.py (SQLAlchemy ORM, callbot schema, 13 表; 含 prompt_config/prompt_version/inbound_route/call_task/call_target)
│   │   └── storage/        # repository.py (异步仓储层), minio_storage.py (upload_recording 录音归档), persistence_helpers.py (fire-and-forget 双写)
│   ├── llm/                # Qwen LLM 推理引擎 Dockerfile (vLLM)
│   ├── alembic/            # 数据库迁移 (0001_init_full_schema 合并旧 0001-0005 全量初始化, 0002_call_target_vars_customer_id, 0003_call_target_vars_text)
│   ├── alembic.ini         # Alembic 配置
│   ├── requirements.txt    # Python 依赖
│   ├── Dockerfile          # 应用镜像 (含 alembic 自动迁移)
│   ├── README.md           # 组件文档
│   └── tests/              # test suite + memory/ (含 test_jitter_buffer, test_config, test_mcp_client 等)
├── agent-mcp/               # MCP 服务器 (用户中心后端)
│   └── java-mcp-server/    # Spring Boot 4.0 + Spring AI 2.0 stateless MCP server
│       ├── src/main/java/com/trans/mcp/
│       │   ├── McpApplication.java     # 入口 (annotation-scanner 自动注册工具)
│       │   ├── model/                  # IdentityResult, CreditResult
│       │   └── service/                # UserService, CreditService (@McpTool + @McpToolParam)
│       ├── src/test/java/              # McpApplicationTests
│       ├── src/main/resources/
│       │   └── application.yaml        # MCP 配置 (STATELESS, /mcp, :9090)
│       ├── Dockerfile                  # MCP server 容器
│       └── pom.xml                     # Maven build
├── console/                # 管理控制台 (Next.js 15 + Drizzle + Better Auth, :3001)
│   └── server/
│       ├── src/app/        # App Router: pages (calls/call-tasks/inbound-routes/prompts/tenants/login) + route handlers (/api/prompts,/api/calls,/api/inbound-routes,/api/call-tasks,/api/tenants,/api/session)
│       ├── src/lib/        # services (prompts/calls/routes/call-tasks) + minio/redis/llm/perms/auth-client
│       ├── src/db/         # schema.ts (Drizzle 只读映射 callbot.*), migrations (0001_init_console 合并旧 console_auth + tenant_management), seed
│       ├── src/components/ # ConsoleShell, CallRecordsList, CallDetail, PromptManager, InboundRoutesManager, CallTasksManager, TenantsManager, TenantSwitcher
│       ├── auth.ts         # Better Auth (本地账密 + active_tenant_id)
│       ├── ecosystem.config.cjs  # PM2 部署
│       └── README.md       # Console 文档
├── freeswitch/             # FreeSWITCH 配置文件
│   ├── vars.xml            # 全局变量 (SIP, RTP, WebSocket URL)
│   ├── modules.conf        # 模块加载列表
│   ├── autoload_configs/   # modules.conf.xml (XML 模块配置)
│   ├── sip_profiles/       # internal.xml (SIP profile)
│   ├── event_socket.conf.xml  # ESL 监听配置
│   ├── dialplan/public/       # 00_biz_type.xml — catch-all 呼入 (answer + playback silence_stream://-1); DID/三元组由 agent-flow 查 inbound_route 解析
│   └── mrcp-plugin/          # UniMRCP 1.5.0 (MRCP/ASR 备选)
├── scripts/                # 启动脚本
│   ├── local.sh            # 本地开发 (conda): asr/tts/flow, stop, status
│   └── prod.sh             # 生产部署 (Docker Compose): GPU检查, 有序启动
├── voices/                 # TTS 音色样本
│   ├── default_female.wav
│   └── tts_test.wav
├── openspec/               # 稳定规格 (8 能力) + changes/archive/
├── docs/                   # superpowers plans/specs (设计记录)
├── docker-compose.yml      # 基础 Docker Compose (基础设施 + 服务)
├── docker-compose.prod.yml # 生产覆盖 (GPU固定, 健康检查)
└── .env                     # 环境变量 (CALLBOT_ 前缀, 不提交)
```

### 引擎插件模式 (ASR & TTS)

1. `asradapter/base.py` / `ttsadapter/base.py` 定义 ABC (`ASREngine` / `TTSEngine`)
2. `engines/{name}/engine.py` 实现抽象基类，导出 `Engine = ConcreteClass`
3. `config.yaml` 按名称选择活跃引擎
4. `config.py` 通过 `importlib.import_module` 动态加载

当前引擎: SenseVoice (ASR, 内置 FunASM GPU 推理), Streaming (ASR, WebSocket 流式), VibeVoice (ASR, 远程 HTTP), CosyVoice (TTS, 内置 CosyVoice3 GPU 推理), EdgeTTS (TTS, 微软 Edge 在线, 无需 GPU), VibeVoice (TTS, 远程 HTTP)

### LangGraph 7 节点流水线

```
① receive_asr    — 接收 ASR 文本，加载 Redis 对话历史
② mcp_identity   — 手机号查用户中心（用户ID/脱敏手机号/身份证后四位）
③ [条件] credit_query — 仅 marketing 查询征信
④ recall_memory  — Redis 热记忆 + PG 长期记忆
⑤ rag_retrieve   — Agentic RAG (自适应检索 → 文档评分 → 查询改写)
⑥ llm_decide     — LLM 结构化输出 (LLMAction)
⑦ tts_synthesize — 调用 TTS adapter，保存对话历史
```

**并行扇出**: ② mcp_identity、④ recall_memory、⑤ rag_retrieve 在 ① receive_asr 之后并发执行。

**提示词三维加载**：`get_system_prompt(tenant_id, biz_type, scenario)` 以 `(tenant_id, biz_type, scenario)` 为键，Redis 缓存 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`（TTL 5min）未命中回源 DB（`is_active=true`）回填；任一级失败降级返回空串 + 告警，不中断通话。加载后经 `render.py` 渲染 `extra.variables` 声明的占位符（vars_context = MCP 身份 + 记忆 + 外呼 call_task.vars）。`(tenant_id, biz_type, scenario, user_key)` 四元组全程透传：dialplan DID → ESL CHANNEL_ANSWER → ActiveCallRegistry → CallGraphState → run_streaming_pipeline。

**流式模式** (WebSocket 生产路径): `run_pre_llm_phase()` 执行 ① + 并行扇出，`run_streaming_pipeline()` 流式输出 LLM token → `IncrementalJSONParser` → `SentenceSplitter` → 每句并行 TTS → `TTSOutputBuffer` 稳态30ms帧回传 FreeSWITCH；每轮 fire-and-forget 双写 PG（`call_turn` role=user + assistant）。支持 **Barge-in 打断**：用户说话时 RMSGate（RMS+SNR 自适应门禁）检测 → **清空 TTSOutputBuffer（不调用 uuid_break，避免终止 dialplan playback）** → 冷却期防误触发 → 取消流式任务 → 新一轮。


## 全链路数据流（事件驱动 uuid_audio_fork）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  FreeSWITCH                                                                 │
│  dialplan/public/00_biz_type.xml  (catch-all 拨号计划)                      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ 来电 → condition ^(\d+)$ → set user_key → answer → playback silence_stream://-1 │   │
│  │ (DID/三元组不在 dialplan 硬编码；recording_notice 提示音由开关控制)  │   │
│  │          ↓ CHANNEL_ANSWER 事件                                       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│       │ ESL Event Socket (:8021)                                            │
│       ↓                                                                     │
│  agent-flow main.py::_on_channel_answer                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ 1. event.headers → variable_did (DID), variable_user_key             │   │
│  │ 2. _resolve_inbound_route(did) → 查 callbot.inbound_route           │   │
│  │      → (tenant_id, biz_type, scenario)  [精确 did 优先, 号段 pattern 兜底]│   │
│  │      (失败回落 dialplan 静态 variable_biz_type, tenant=default)       │   │
│  │ 3. ActiveCallRegistry.register(uuid, biz_type, user_key,             │   │
│  │                                   tenant_id, scenario)               │   │
│  │ 4. repository.insert_call_session(...)  ← 写 call_session 行          │   │
│  │      (recording_notice_played = settings.recording_notice_enabled)    │   │
│  │ 5. esl.audio_fork_start(uuid, "ws://.../media/{uuid}", 16000Hz)     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│       ↓ uuid_audio_fork start                                               │
│  FreeSWITCH 连接 WebSocket ws://agent-flow:8000/media/{uuid}               │
└─────────────────────────────────────────────────────────────────────────────┘
       ↓ WebSocket 双向 PCM (16kHz 16-bit)
┌─────────────────────────────────────────────────────────────────────────────┐
│  agent-flow StreamingCallHandler.handle()                                   │
│  每通通话创建 1 个 CallRecorder (双声道累加)                                │
│                                                                             │
│  ┌─ 接收循环 (FreeSWITCH → agent-flow) ──────────────────────────────────┐ │
│  │                                                                        │ │
│  │  websocket.receive() → binary PCM frame                                │ │
│  │  recorder.feed_caller(frame)  ← 录音 L 声道 (VAD 前原始帧, 不丢音)     │ │
│  │       ↓                                                                │ │
│  │  JitterBuffer.insert(frame)    ← 抖动平滑 (预填充3帧=90ms)           │ │
│  │       ↓                                                                │ │
│  │  JitterBuffer.drain() → smooth_frame                                   │ │
│  │       ↓                                                                │ │
│  │  WebRTCAPM.process(near, reverse) ← AEC+NS+AGC (CALLBOT_AEC_ENABLED)  │ │
│  │   或 Denoiser.process(frame)   ← 或降噪 (highpass/noisereduce/rnnoise) │ │
│  │       ↓                                                                │ │
│  │  audio_buffer.extend(processed)                                        │ │
│  │       ↓                                                                │ │
│  │  ASR WS 全量喂入 → 服务端 FSMN-VAD 分段 → on_final 回调触发轮次      │ │
│  │       ↓ (服务端静音端点判定, 主动回推 result/is_final)                  │ │
│  │  ┌──────────────────────────────────────────┐                          │ │
│  │  │ ASR 识别 (WebSocket 唯一传输)                                       │ │
│  │  │  → {"text": "我想咨询贷款", "confidence": 0.95}                     │ │
│  │  └──────────────────────────────────────────┘                          │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│       ↓ ASR text                                                            │
│  ┌─ Pre-LLM 阶段 (run_pre_llm_phase, flow.py) ─────────────────────────┐  │
│  │                                                                       │  │
│  │ get_system_prompt(tenant_id, biz_type, scenario)  ← 三维 Redis→DB    │  │
│  │ render.py 渲染 {变量} 占位符                                          │  │
│  │ ① receive_asr_node    → ASR文本 + Redis对话历史                      │  │
│  │ ② mcp_identity_node   ─┐                                             │  │
│  │ ④ recall_memory_node   ─┤ fan-out 并发 (asyncio.gather)              │  │
│  │ ⑤ rag_retrieve_node    ─┘                                             │  │
│  │ ③ credit_query_node    (仅 marketing)                                │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│       ↓ state (user_input + memory_block + rag_block + identity)            │
│  ┌─ 流式 LLM+TTS (run_streaming_pipeline, flow.py) ────────────────────┐  │
│  │                                                                       │  │
│  │ LLM astream_action() → token stream                                  │  │
│  │       ↓                                                               │  │
│  │ IncrementalJSONParser → 提取 action 字段                              │  │
│  │       ↓                                                               │  │
│  │ SentenceSplitter.feed(token_delta) → Sentence(text, index)           │  │
│  │       ↓ 每句独立 TTS 任务                                             │  │
│  │ ┌─────────────────────────────────────────────────────────┐           │  │
│  │ │ _tts_sentence(sentence)                                  │           │  │
│  │ │   TTS 传输: WebSocket (句级并发合成)                      │           │  │
│  │ │   → audio_callback(pcm, idx)  ← recorder.feed_ai(pcm)   │ ← 录音 R │  │
│  │ └─────────────────────────────────────────────────────────┘           │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│       ↓ audio_callback(pcm, sentence_index)                                  │
│  ┌─ TTS 输出缓冲 → 回传 FreeSWITCH ────────────────────────────────────┐  │
│  │                                                                       │  │
│  │ TTSOutputBuffer.write(pcm)    ← 拆帧 (960B = 30ms @ 16kHz)          │  │
│  │ TTSOutputBuffer._send_loop() ← 30ms 间隔匀速发送                     │  │
│  │ websocket.send_bytes(frame)  → FreeSWITCH 接收 PCM → 播放给用户      │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Barge-in 打断 (并发检测) ──────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │ AI 说话时，_receive_during_streaming() 并发接收用户音频               │  │
│  │       ↓ VAD 检测到用户说话                                            │  │
│  │ barge_in_event.set()                                                  │  │
│  │       ↓                                                               │  │
│  │ tts_buffer.clear()            ← 清空 TTS 输出缓冲 (不调 uuid_break!) │  │
│  │ streaming_task.cancel()        ← 取消当前 LLM+TTS 流                │  │
│  │ 冷却期 (CALLBOT_COOLDOWN_AFTER_BARGEIN) 防残留噪声误判             │  │
│  │       ↓ 开始新一轮对话                                                │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
       ↓ CHANNEL_HANGUP
┌─────────────────────────────────────────────────────────────────────────────┐
│  清理 + 录音归档                                                            │
│  recorder.finalize_stereo_wav() → ${recordings_dir}/{uuid}.wav (L=caller/R=AI, 短边补静音)│
│  esl.audio_fork_stop(uuid) → FS 停止音频分流                               │
│  _call_registry.cancel_call(uuid) → handler 循环退出                       │
│  repository.update_call_session_end(fs_uuid, end_ts, hangup_cause, result_code)│
│  _archive_recording(uuid,...)  ← fire-and-forget (延迟3s 读 FS wav)       │
│    → minio.upload_recording(fs_uuid, wav, biz_type, tenant_id)            │
│       → key = recordings/{YYYYMMDD}/{uuid}.wav                            │
│    → repository.insert_artifact(kind='recording', uri=key)  (MinIO 未配置静默跳过)│
│  WebSocket 关闭 → 资源释放                                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

> TTS (CosyVoice) 输出 22050Hz PCM，agent-flow 通过 `_resample_pcm()` 重采样到 16kHz 后写入 TTSOutputBuffer。FreeSWITCH 内部自动将 16kHz 下采样到电话编码 (G.711 8kHz) 播放。全链路统一 16kHz，帧大小 960 字节 (30ms @ 16kHz 16-bit)。

---

## 管理控制台 console

**console/server** 是 Next.js 15 (App Router) + Drizzle ORM + Better Auth 管理控制台（端口 3001）。与 agent-flow **共用同一物理 PostgreSQL `callbot` schema 和 Redis 实例**——发布/回滚动作直接删除 Redis key `cb:prompt:{tenant_id}:{biz_type}:{scenario}`，实现配置变更零延迟生效（无需重启 agent-flow）。

### 技术栈
- **后端**：Next.js Route Handlers（`/api/*`）+ Drizzle ORM（node-postgres）
- **认证**：Better Auth（本地账密 email/password；ADFS OAuth 走 `CONSOLE_ADFS_ENABLED` 预留）
- **多租户**：`session.user.tenantId` 隔离，跨租户返回 404（不泄漏存在性）；活跃租户取值 `session.active_tenant_id ?? user.tenant_id ?? 'default'`
- **缓存失效**：publish/rollback 直删 Redis key（与 agent-flow 共享同一 Redis）

### 数据模型（与 agent-flow SQLAlchemy 同表同列名）
- `callbot.prompt_config` — 主表，`UNIQUE(tenant_id, biz_type, scenario)`，单行即当前内容
- `callbot.prompt_version` — 版本快照（支撑回滚）
- `callbot.inbound_route` — DID/号段 → `(tenant_id, biz_type, scenario)`，呼入解析
- `callbot.call_task` — 外呼任务定义（prompt 绑定 + 策略参数）；`callbot.call_target` — 号码清单 + 每号码 render 变量（vars）；由 agent-flow `OutboundExecutor` 执行（tick 轮询 + 时段/并发 + CAS 认领 + originate + redial）
- `callbot.call_session/turn/event/artifact` — Drizzle 只读映射（Console 不写这四表）
- `console.*` — Better Auth 自带（user/session/account/verification）+ tenant/user_tenant（多租户）

> `prompt_config`/`prompt_version`/`inbound_route`/`call_task`/`call_target` 由 **agent-flow alembic** 建表（0001 全量初始化 + 0002/0003 扩 call_target.vars）；`console` schema 由 Console 自行建表（`src/db/migrations/`）。

### DID 呼入路由（与 FreeSWITCH dialplan 的关系）
三元组 `(tenant_id, biz_type, scenario)` **不在 dialplan 硬编码**：
```
呼入 → FS catch-all 拨号计划（仅 answer + user_key + 保活）
     → agent-flow CHANNEL_ANSWER 读 Caller-Destination-Number = DID
     → 查 callbot.inbound_route（精确 did 优先，号段 did_pattern 兜底）
     → (tenant_id, biz_type, scenario) → 命中 prompt_config
```
- **新增 DID / 租户 / scenario**：Console「DID 路由」页加一行 → 即时生效，不动 FreeSWITCH
- dialplan 改为 catch-all 后，需在 FS 执行一次 `fs_cli -x "reloadxml"`（仅首次切换时；之后增删路由无需再 reload）

### 页面与 API
| 页面 | 路由 | 能力 |
|------|------|------|
| 提示词管理 | `/prompts` | 草稿/发布/版本回滚/克隆/变量渲染测试 |
| DID 路由 | `/inbound-routes` | DID/号段 → 三元组 CRUD |
| 外呼任务 | `/call-tasks` | 任务定义 CRUD + 号码清单管理/结构化 CSV 导入（5 列：序号\|业务类型\|手机号\|客户id\|vars）+ 启停 + 进度查询 |
| 通话记录 | `/calls`, `/calls/[id]` | 只读列表+详情+录音回放（presigned URL） |
| 租户管理 | `/tenants` | 租户 CRUD、用户多租户归属 |

主要 API：`/api/prompts/{id}/{publish,rollback,clone,test,versions}`、`/api/inbound-routes`、`/api/call-tasks`、`/api/calls[/{id}][/{recording-url}]`、`/api/tenants`、`/api/session/{tenants,switch-tenant}`。受 Better Auth + RBAC 守护：`prompt:*` / `route:*` / `calltask:*` / `call:view` / `tenant:*`。

详见 `console/server/README.md`。

---

## 多租户四维隔离

自 tenant-management / align-prompt-config-pipeline 变更起，业务隔离键由「仅 3 biz_type」升级为 **`(tenant_id, biz_type, scenario)` 三元组**（加上 per-user 的 `user_key`）。各维度的隔离职责：

| 维度 | 隔离点 | 说明 |
|------|--------|------|
| `tenant_id` | PG 列 + Redis key 前缀 + Console RBAC | 多租户顶层隔离，default 兜底 |
| `biz_type` | TTS voice profile + PG 列 + Redis key 前缀 | customer_service/collection/marketing，TTS 音色/语速/音量按此分 |
| `scenario` | prompt 三维键 | 业务场景（如 activation/repayment/consult），DID 路由解析 |
| `user_key` | PG 分布键 + Redis hot memory | `{core_user_id}:{phone_hash_salted}`，per-user 数据分区 |

- **Redis**：`cb:{tenant_id}:{biz_type}:...`；prompt 缓存 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`（5min TTL）
- **PostgreSQL**：`tenant_id` + `biz_type` 列；分布键 `user_id`（非 biz_type / tenant_id）；Console 用 Drizzle 只读映射同表
- **Console RBAC**：`prompt:*` / `route:*` / `calltask:*` / `call:view` / `tenant:*`，按 `tenant_id` 隔离；`platform_admin` 可跨租户，普通用户切租户需 `user_tenant` 归属
- **DID 路由**：`callbot.inbound_route` 把 DID/号段映射到三元组，agent-flow CHANNEL_ANSWER 解析（dialplan 保持哑）
- **征信查询**：仅 marketing biz_type
- **旧「3 biz_type」**：仍作为 biz_type 维度生效（TTS profile / 征信），tenant 为新增的上层隔离维度

---

## 通话录音（CallRecorder 双声道）

录音方案为 **agent-flow 自录双声道**（Plan B），不依赖 FS `record_session`（FS 录不到 mod_audio_fork 注入的 AI 音频）。

### 双声道原理
- **L 声道（caller）**：`CallRecorder.feed_caller(frame)`，喂接收循环每帧——mod_audio_fork 实时转发的 caller 原始 PCM，**VAD 门控之前**，全部帧不丢音
- **R 声道（AI）**：`CallRecorder.feed_ai(pcm)`，喂 streaming 管线 `audio_callback` 的每段 TTS PCM；barge-in 路径 `_receive_during_streaming` 也喂 caller 帧，覆盖 AI 说话期间
- `finalize_stereo_wav()`：短边以 int16 静音(0)补齐到长边，输出 16kHz 16-bit 立体声 wav（L/R 交错）；无音频（全程无 caller 帧也无 AI PCM）返回 None 不写空 wav

### 归档流程（fire-and-forget）
```
CHANNEL_HANGUP
  → CallRecorder.finalize_stereo_wav() 写 ${CALLBOT_RECORDINGS_DIR}/{uuid}.wav
  → audio_fork_stop / cancel_call / WebSocket 关闭
  → _archive_recording(uuid,...)  ← fire-and-forget (asyncio.create_task, _ongoing_archives 强引用防 GC)
      → sleep(CALLBOT_RECORDING_ARCHIVE_DELAY_SEC=3) 等 FS 写盘
      → 读 ${recordings_dir}/{uuid}.wav
      → minio.upload_recording(uuid, wav, biz_type, tenant_id)
          → key = recordings/{YYYYMMDD}/{uuid}.wav, content_type=audio/wav
          → CALLBOT_MINIO_ENDPOINT 为空或上传失败时返回 None（不写 artifact）
      → repository.insert_artifact(kind='recording', storage='minio', uri=key)
```

### 录音提示音合规
- `CALLBOT_RECORDING_NOTICE_ENABLED=true`（默认）时 dialplan 在 answer 后播放提示音（`CALLBOT_RECORDING_NOTICE_SOUND`）
- `call_session.recording_notice_played` 置为 `settings.recording_notice_enabled`
- 本地测试可关开关或注释 dialplan 提示音行

### Console 回放
- `GET /api/calls/:id/recording-url` → MinIO presigned URL（按 tenant_id 隔离，跨租户 404）

### 关键约束
- MinIO 配置走 pydantic-settings（`CALLBOT_MINIO_*` 前缀），endpoint 为空时 `upload_recording` 静默跳过；上传失败返回 None（避免写指向空文件的 artifact）
- 自动归档失败可调 `POST /calls/{fs_uuid}/archive-recording` 手动补归档（404/409/410/502 区分未找到/已归档/文件丢失/MinIO 不可用），Console 详情页有手动归档按钮
- 录音写入 FS 的 `${recordings_dir}`（非 NAS 分轨），Console 读取 artifact.uri 的 MinIO 对象

---

## WebRTCAPM 音频处理（AEC + NS + AGC）

`agent-flow/src/ws/audio_processing.py` 封装 livekit 的 `AudioProcessingModule`，提供 **HPF + AEC + NS + AGC 一次过**，替代 `denoise.py` 的具体降噪器 + 固定 `CALLBOT_AUDIO_GAIN`。

### 帧语义（30ms @ 16kHz mono = 960 字节）
- **near 端**：麦克风回采（含 TTS 回声 + 语音 + 噪声），每 30ms 帧直接喂入 APM（livekit AudioFrame 无 10ms 限制，省掉拆帧）
- **reverse 端**：正在播放的 TTS 帧（AEC 远端参考）；AI 沉默时为静音帧

### 处理顺序（livekit in-place 语义）
每帧：`set_stream_delay_ms` → `process_reverse_stream`（喂远端参考 + 设延迟）→ `process_stream`（in-place 处理 capture）。单帧失败降级透传原帧（不影响通话）。

### 配置（CALLBOT_ 前缀）
| 配置项 | 默认 | 说明 |
|--------|------|------|
| `CALLBOT_AEC_ENABLED` | false | 开启 WebRTCAPM（与 denoise 互斥） |
| `CALLBOT_AEC_TYPE` | 2 | 1=AECM(移动端), 2=老AEC（AEC3 源码注释不可用） |
| `CALLBOT_AEC_NS_LEVEL` | 2 | NS 抑制等级 0-3 |
| `CALLBOT_AEC_AGC_TYPE` | 1 | 0=关, 1=AdaptiveDigital, 2=AdaptiveAnalog |
| `CALLBOT_AEC_SYSTEM_DELAY_MS` | 80 | 回声延迟先验（has_echo 监控后标定） |

### 互斥关系
- AEC 开启时：`WebRTCAPM.process()` 取代 denoiser，AGC 逐帧处理不再叠加固定 `CALLBOT_AUDIO_GAIN`
- AEC 关闭时：走原 `denoise.py`（highpass/noisereduce/rnnoise）+ 固定增益
- 库缺失：`create_audio_processing()` 返回 None，handler 走 passthrough

---

## 1. 系统整体架构图(文字拓扑) [1]

### 1.1 逻辑拓扑（数据流/控制流/媒体流）
**媒体流（WebSocket 双向音频）**
- 被叫用户 ⇄ FreeSWITCH（SIP/RTP）
- FreeSWITCH（`mod_audio_fork`）⇄ agent-flow（WebSocket :8000, uvloop）
  - 上行：用户语音 PCM → JitterBuffer → WebRTCAPM(AEC/NS/AGC) 或 Denoiser降噪 → agent-asr(:8080 WebSocket) 内置 GPU 推理（服务端 FSMN-VAD 分段 → 回推 final）
  - 下行：agent-tts(:8081 WebSocket) 内置 GPU 推理 → TTSOutputBuffer 稳态30ms帧 → FreeSWITCH → 用户
  - 录音：CallRecorder 双声道累加（L=caller / R=AI），挂断 finalize_stereo_wav → MinIO

**控制流（agent-flow 统一调度）**
- agent-flow 串联 ASR → LLM → TTS 完整链路（对客户无感），全程透传四维键 `(tenant_id, biz_type, scenario, user_key)`
  - Node ①：调用 agent-asr 识别音频
  - Node ②④⑤：MCP 查询 + 记忆 + RAG 并行扇出；提示词三维 `get_system_prompt(tenant_id, biz_type, scenario)` 加载 + `render.py` 渲染
  - Node ⑥：LLM 流式输出 → 句级切分 → 并行 TTS
  - Node ⑦：TTSOutputBuffer 稳态帧 WebSocket 回传；fire-and-forget 双写 PG call_turn

**事件控制流（ESL Event Socket）**
- agent-flow ESL Client → FreeSWITCH Event Socket(:8021)
  - CHANNEL_ANSWER 订阅 → `_resolve_inbound_route(DID)` 解析三元组 → register + insert_call_session → uuid_audio_fork start
  - CHANNEL_HANGUP 订阅 → uuid_audio_fork stop → update_call_session_end → `_archive_recording`(fire-and-forget) → ActiveCallRegistry 取消活跃通话 → 清理资源
  - uuid_transfer：转人工（handoff）
  - uuid_kill：挂断通话（end）
  - 打断：**清空 TTSOutputBuffer（不调 uuid_break，避免终止 dialplan playback）**

**决策流（本地LLM，流式输出）**
- Orchestrator → Qwen3.5-9B 推理服务（GPU2，独立部署）
  - 输入：业务域 Prompt（三维） + 会话状态 + 记忆召回块 + 用户最新文本
  - 输出：流式 token → IncrementalJSONParser → SentenceSplitter → 句级 TTS 并行
  - 结构化动作（say/ask/handoff/end）+ 文本 + 标签

**核心数据流（MCP 协议）**
- Orchestrator → java-mcp-server(:9090)：`user_identity_query`（phone + biz_type → user_id, phone_masked, id_card_last_four）
- Orchestrator → java-mcp-server(:9090)：`user_credit_query`（user_id → credit_qualified, risk_level，仅 marketing）

**运营流（console 控制台 :3001）**
- console ⇄ PostgreSQL `callbot` schema（prompt_config/prompt_version/inbound_route/call_task/call_target + 只读 call_session/turn/event/artifact）+ `console` schema（tenant/user/session，Better Auth）
- console ⇄ Redis（publish/rollback 直删 `cb:prompt:{tenant_id}:{biz_type}:{scenario}` 实现零延迟生效）
- console ⇄ MinIO（presigned URL 回放录音）

**数据/记忆/审计**
- Redis：会话态、短期记忆、TTS缓存索引、prompt 三维缓存（5min TTL），key 前缀 `cb:{tenant_id}:{biz_type}:...`
- PostgreSQL 17 + pgvector：13 张业务表（call_session/turn/event/artifact/config_snapshot/user_memory_fact/user_memory_vector/script_library/prompt_config/prompt_version/inbound_route/call_task/call_target）+ 向量召回；console schema（Better Auth + tenant/user_tenant/session）
- mem0：记忆抽取/更新/衰减，落地 PG/Redis
- MinIO：整通双声道录音归档（key `recordings/{YYYYMMDD}/{uuid}.wav`，生命周期 1–3 年）

**监控告警**
- Prometheus：采集 FS/ASR/TTS/LLM/Orchestrator/存储指标
- Grafana：面板与告警

### 1.2 物理拓扑（推荐生产）
- FS 节点×2（主备或水平扩容）
- agent-asr GPU 节点（GPU0）×1（内置推理引擎）
- agent-tts GPU 节点（GPU1）×1（内置推理引擎）
- agent-flow 节点×1（CPU，WebSocket + LangGraph 编排）
- LLM GPU 节点（GPU2）×1
- 数据节点：PG17、Redis、MinIO（可拆分）
- NAS（独立存储）

---

## 2. 服务器硬件配置推荐 [1]

### 2.1 FreeSWITCH 节点（每台）
- CPU：32C+
- 内存：64–128GB
- 磁盘：NVMe 1–2TB（临时音频/日志/缓存）
- 网卡：10GbE
- 说明：同时承担 50/100/200 并发时建议至少 2 台做扩容与容灾

### 2.2 ASR/TTS/LLM GPU 节点
- pip install modelscope
- ASR（GPU0）：GPU×1、CPU 16C、内存 64GB（agent-asr 内置推理）
- modelscope download --model iic/SenseVoiceSmall
- TTS（GPU1）：GPU×1、CPU 16C、内存 64GB（agent-tts 内置推理）
- modelscope download --model FunAudioLLM/Fun-CosyVoice3-0.5B-2512
- Qwen3.5-9B（GPU2）：GPU×1、CPU 32C、内存 128GB
- modelscope download --model Qwen/Qwen3.5-9B
- 推理引擎: `agent-flow/llm/Dockerfile`

### 2.4 数据与存储
- PostgreSQL 17：CPU 16–32C、内存 128GB、NVMe（高 IOPS）
- Redis：CPU 8–16C、内存 32–64GB
- MinIO：按 1–3 年保留期估算容量（建议纠删码/多盘）
- NAS：按近 N 天热存需求配置

---

## 3. ASR/TTS 引擎部署步骤 [1]

### 3.1 通用准备
1) 安装 NVIDIA Driver 与 CUDA（与模型要求匹配）
2) 为 ASR/TTS/LLM 分别准备独立运行环境（容器或 venv）
3) 固定 GPU：
- ASR：`CUDA_VISIBLE_DEVICES=0`
- TTS：`CUDA_VISIBLE_DEVICES=1`

### 3.2 ASR 服务部署 (GPU0)
当前支持引擎: **SenseVoice** (默认, 内置GPU推理), **VibeVoice** (远程HTTP)

**SenseVoice ASR** (内置 FunASR GPU 推理):
- 单服务部署: `agent-asr/` (Dockerfile 内置 PyTorch + 模型下载)
- 模型下载: `modelscope download --model iic/SenseVoiceSmall`
- 切换引擎: 修改 `asradapter/config.yaml` 中 `engine: sensevoice`

**VibeVoice ASR** (远程 HTTP):
- 需独立部署 VibeVoice ASR 推理服务
- 环境变量: `VIBEVOICE_ASR_API_URL`

验收:
- agent-flow → agent-asr: 识别链路端到端通

### 3.3 TTS 服务部署 (GPU1)
当前支持引擎: **CosyVoice** (默认, 内置GPU推理), **EdgeTTS** (微软 Edge 在线, 无需 GPU), **VibeVoice** (远程HTTP)

**CosyVoice TTS** (内置 CosyVoice3 GPU 推理):
- 单服务部署: `agent-tts/` (Dockerfile 内置 PyTorch + CosyVoice 运行时 + 模型下载)
- 模型下载: `modelscope download --model FunAudioLLM/Fun-CosyVoice3-0.5B-2512`
- 切换引擎: 修改 `ttsadapter/config.yaml` 中 `engine: cosyvoice`

**EdgeTTS** (微软 Edge 在线 TTS, 无需 GPU):
- 利用微软 Edge 在线 TTS 服务，无需本地 GPU
- 三业务 Profile 隔离（音色/语速/音量按 biz_type 配置）
- 磁盘缓存按 voice+text hash，按 biz_type 目录隔离
- 输出 22050Hz mono 16-bit WAV，与 CosyVoice 一致，下游统一重采样
- 切换引擎: 修改 `ttsadapter/config.yaml` 中 `engine: edgetts`
- 依赖: `edge-tts==7.2.8`, `pydub>=0.25.1`

**VibeVoice TTS** (远程 HTTP):
- 需独立部署 VibeVoice TTS 推理服务
- 环境变量: `VIBEVOICE_TTS_API_URL`

三业务 Profile 强隔离:
- TTS 引擎内置 `BIZ_TYPE_PROFILES` (voice_id/speed/volume/pitch 按 biz_type 隔离)

验收:
- agent-flow → agent-tts: 播报链路端到端通
- 不同 biz_type 播报音色/语速等严格符合配置隔离

---

## 4. mod_audio_fork WebSocket 配置 [1]

### 4.1 编译安装 mod_audio_fork
- 从 FreeSWITCH 源码编译 mod_audio_fork 模块
- 在 modules.conf 中添加 `mod_audio_fork`

### 4.2 WebSocket 连接配置
- vars.xml 中设置 `agent_flow_ws_url=ws://10.0.0.20:8000/media/{uuid}`
- dialplan 为 catch-all `^(\d+)$` + `answer` + `playback silence_stream://-1`（无限静音保活），三元组由 agent-flow 查 inbound_route 解析；由 agent-flow ESL 事件驱动 `uuid_audio_fork` 动态控制

监控要求：
- WebSocket 连接状态探测
- 音频流延迟 P95、断连率

---

## 5. FreeSWITCH全部配置文件(拨号计划、模块加载) [1]

> 给出”必须实现的配置要点 + 变量约定 + mod_audio_fork WebSocket 链路”。具体 XML 模板可由 Codex 按此清单生成并落盘。

### 5.1 modules（必须加载）
- `mod_sofia`（SIP）
- `mod_audio_fork`（WebSocket 双向音频流）
- `mod_dptools`（playback/record 等）
- `mod_sndfile`、`mod_native_file`（音频文件读写）

### 5.2 dialplan（关键，catch-all 呼入）
拨号计划为 **catch-all `^(\d+)$`**，三元组 `(tenant_id, biz_type, scenario)` **不在 dialplan 硬编码**——由 agent-flow 在 CHANNEL_ANSWER 读取被叫号(DID)后查 `callbot.inbound_route` 解析。新增/修改 DID 路由在 Console「DID 路由」页运营，无需改本文件、无需重启 FS。接通后必须具备：
1) **录音提示音合规**：`CALLBOT_RECORDING_NOTICE_ENABLED=true`（默认）时在 answer 后播放提示音（`CALLBOT_RECORDING_NOTICE_SOUND`），写 `call_session.recording_notice_played=true`；本地测试可关开关或注释 dialplan 提示音行
2) 设置 channel variables：`user_key = ${caller_id_number}`、DID = `destination_number`（贯穿 Orchestrator / 录音 / 审计）
3) `answer` + `playback silence_stream://-1` 保持通话（无限静音保活），agent-flow 通过 ESL `uuid_audio_fork` 动态控制音频分流：
   - CHANNEL_ANSWER 事件 → `_resolve_inbound_route(DID)` 解析三元组 → register + insert_call_session → `uuid_audio_fork start ws://...mono 16000`
   - CHANNEL_HANGUP 事件 → `uuid_audio_fork stop` → update_call_session_end → `_archive_recording`(fire-and-forget) → 清理资源

---

## 6. 三大业务TTS隔离详细参数表(音色、语速、语调) [1]

| 业务 | voice | speed | instruct | 说明 |
|---|---|---:|---|---|
| 客服 | default_female.wav | 1.0 | 温柔的客服语气 | 温柔平稳女声 |
| 催收 | default_female.wav | 0.9 | 严肃的催收语气 | 严肃稳重女声 |
| 营销 | default_female.wav | 1.1 | 活泼的营销语气 | 热情活力女声 |

隔离验收点：
- profile 配置文件、缓存目录、日志目录必须按 biz_type 分离；TTS 缓存 key 必含 `biz_type + tts_profile_version`。

---

## 7. Python AI对话完整源码(对接Qwen3.5-9B+ASR+TTS) [1]

这里给**可直接生成代码的实现规范**（模块、函数、状态机、DB/Redis/记忆写入、错误处理都明确）。

### 7.1 工程约束
- Python 3.12
- `langchain-mcp-adapters` MCP Client 对接 java-mcp-server 用户中心
- LangChain + LangGraph：图编排
- Redis + PG17(pgvector) + mem0：记忆
- ASR/TTS 为可插拔引擎，内置 GPU 推理（对客户无感）

### 7.2 必备模块与职责
- `main.py`：FastAPI 入口，接收 WebSocket 双向音频流（`WS /media/{uuid}`），ESL生命周期管理（CHANNEL_ANSWER/HANGUP 事件驱动 uuid_audio_fork）
- `graph/flow.py`：LangGraph 7 节点 StateGraph（强流程）+ `run_pre_llm_phase` / `run_streaming_pipeline` 流式管道
- `clients/mcp.py`：MCP Client 调用 java-mcp-server（身份查询 + 征信查询）
- `clients/esl.py`：ESL Client 调用 FreeSWITCH Event Socket（自动重连+心跳检测，挂断/转接/打断/事件订阅）
- `clients/asr_ws_client.py`：ASR WebSocket Client — 流式音频识别（唯一传输，服务端 FSMN-VAD 分段回推 final）
- `clients/tts_ws_client.py`：TTS WebSocket Client — 流式语音合成（唯一传输，句级并发）
- `llm/service.py`：Qwen3.5-9B 调用 + JSON schema 校验 + 流式输出 + 超时重试 + 降级
- `llm/json_stream.py`：IncrementalJSONParser 从 LLM token 流增量解析结构化字段
- `llm/sentence_splitter.py`：SentenceSplitter 将流式 token 切分为 TTS 就绪句子
- `ws/handler.py`：WebSocket 处理器（StreamingCallHandler 流式+打断）
- `ws/asr_streaming.py`：AsrStreamingManager — 单轮 ASR WS 流生命周期（feed/finalize/reset/cancel）
- `ws/rms_gate.py`：RMSGate — RMS+SNR 自适应门禁（barge-in 检测）
- `ws/denoise.py`：前置降噪（highpass/noisereduce/rnnoise），工厂函数读取 `CALLBOT_DENOISE_ENABLED`
- `ws/jitter_buffer.py`：JitterBuffer 输入平滑 + TTSOutputBuffer 稳态30ms帧输出
- `ws/registry.py`：ActiveCallRegistry 活跃通话注册（CHANNEL_HANGUP 取消）
- `rag/retriever.py`：Agentic RAG（自适应检索 + 文档评分 + 查询改写）
- `memory/`：assembler.py（三层记忆聚合）、chat_history.py（Redis对话历史）、redis_memory.py（热记忆）、store.py（PG记忆）
- `db/models.py`：PG17 DDL对应的 ORM 模型
- `storage/repository.py`：异步仓储层
- `storage/minio_storage.py`：MinIO 对象存储客户端（按 biz_type 隔离上传/下载）

### 7.3 每轮对话处理流程（agent-flow 统一调度，对客户无感）

**流式模式** (WebSocket /media/{uuid}，生产路径，事件驱动)：
- FreeSWITCH 通过 mod_audio_fork WebSocket 将用户音频流传至 agent-flow：
  1) JitterBuffer 平滑输入音频 → Denoiser 降噪 → 全量喂入 agent-asr WebSocket → 服务端 FSMN-VAD 分段，回推 final 触发本轮（识别文本随 final 到达）
  2) 落库 user turn（含置信度）
  3) 并行扇出：MCP 身份查询 ‖ 记忆召回 ‖ RAG 检索
  4) Qwen3.5-9B 流式输出 → IncrementalJSONParser → SentenceSplitter → 句级文本
  5) 每句并行调用 agent-tts 合成语音（WebSocket 唯一传输，句级并发） → WAV→PCM → TTSOutputBuffer 稳态30ms帧 → WebSocket 回传 FreeSWITCH
  6) Barge-in：用户说话时 RMSGate 检测 → **清空 TTSOutputBuffer（不调 uuid_break）** → 冷却期防误触发 → 取消流式任务 → 新一轮
  7) CHANNEL_HANGUP：ESL 事件订阅 → update_session_end → CallRecorder.finalize_stereo_wav → `_archive_recording`(fire-and-forget) → ActiveCallRegistry 取消 → 清理资源
- FreeSWITCH 播放 TTS 音频，完成一轮交互

### 7.4 多租户四维隔离“强制编码规则”
- 核心函数签名须透传四维键 `(tenant_id, biz_type, scenario, user_key)`，不得用 `default` 兜底替代 tenant_id/scenario
- `tts_profile` 只能由 `biz_type` 映射获取（不允许外部透传任意 profile）
- Redis key 必须含 `tenant_id`：`cb:{tenant_id}:{biz_type}:...`；prompt 缓存 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`
- Prompt 按 `(tenant_id, biz_type, scenario)` 三维加载，版本由 `prompt_config.version` + `prompt_version` 快照决定
- MinIO 对象 key 含 tenant_id/biz_type（录音 `recordings/{YYYYMMDD}/{uuid}.wav`）

---

## 8. 生产级systemd守护进程配置 [1]

必须服务化拆分：
- `freeswitch.service`
- `agent-asr.service`（GPU0, agent-asr 含内置推理引擎）
- `agent-tts.service`（GPU1, agent-tts 含内置推理引擎）
- `llm-engine.service`（GPU2, Qwen3.5-9B）
- `orchestrator.service`（agent-flow FastAPI + WebSocket）
- `mcp-server.service`（java-mcp-server Spring Boot 4.0, :9090）
- `postgresql.service` `redis.service` `minio.service`（如自建）

关键要求：
- `Restart=always`
- `LimitNOFILE` 增大
- GPU服务固定 `CUDA_VISIBLE_DEVICES`

---

## 9. 开机自启、目录规划、日志隔离、录音隔离方案 [1]

### 9.1 录音/语音目录（按 biz_type 强隔离）
- NAS：`/nas/rec/{biz_type}/YYYY/MM/DD/{call_id}/caller.wav bot.wav mix.wav meta.json`
- MinIO bucket：`rec-cs` / `rec-collection` / `rec-marketing`
- TTS片段：`/nas/rec/{biz_type}/.../tts/turn_{turn_id}.wav`（可选但推荐）

### 9.2 记忆分割与key
- `user_key = core_user_id + ":" + salted_hash(phone)`
- Redis：`mem:{biz_type}:{user_key}:{yyyymm}:...`
- PG：所有记忆/向量表带 `biz_type,user_key,ts`，并按月分区

### 9.3 日志隔离
- 结构化日志必须包含：`biz_type, fs_uuid, call_id, user_key`
- 下载/导出录音必须审计

---

## 10. 常见报错、坑点、优化调优方案 [1]

1) detect_speech 不出 DETECTED_SPEECH：
- 检查模块加载、ASR profile、事件订阅、UniMRCP/ASR健康
2) 营销并发导致TTS排队：
- TTS缓存+营销并发降级+短句兜底+监控队列长度
3) 合规失败（未播录音告知）：
- 强告警+通话标红+任务级统计
4) 催收越权播敏感字段：
- 二次校验门禁+事件审计+自动熔断回滚
5) PG17向量检索性能：
- HNSW索引+限制召回条数+限制时间窗（180天等）

---

## PG17 DDL（建议 schema=callbot）

### 10.1 扩展与schema
```sql
CREATE SCHEMA IF NOT EXISTS callbot;

-- pgvector
CREATE EXTENSION IF NOT EXISTS vector;
```

### 10.2 通话会话表（事实主表）
```sql
CREATE TABLE IF NOT EXISTS callbot.call_session (
  call_id            UUID PRIMARY KEY,
  fs_uuid            UUID UNIQUE NOT NULL,
  biz_type           TEXT NOT NULL CHECK (biz_type IN ('customer_service','collection','marketing')),
  tenant_id          TEXT NOT NULL DEFAULT 'default',  -- 多租户隔离键(0003迁移新增)
  scenario           TEXT NOT NULL DEFAULT 'default',  -- DID 路由解析出的场景(0003迁移新增)
  task_id            TEXT,
  core_user_id       TEXT NOT NULL,
  phone_hash         TEXT NOT NULL,
  user_key           TEXT NOT NULL, -- core_user_id:phone_hash
  phone_masked       TEXT,          -- 中间四位掩码, 形如 138****1234
  start_ts           TIMESTAMPTZ NOT NULL,
  end_ts             TIMESTAMPTZ,
  result_code        TEXT,
  hangup_cause       TEXT,
  identity_verified  BOOLEAN NOT NULL DEFAULT FALSE,
  verify_attempts    INT NOT NULL DEFAULT 0,
  recording_notice_played BOOLEAN NOT NULL DEFAULT FALSE,  -- = settings.recording_notice_enabled
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_call_session_tenant_biz_user_start
  ON callbot.call_session (tenant_id, biz_type, user_key, start_ts DESC);

CREATE INDEX IF NOT EXISTS idx_call_session_task_start
  ON callbot.call_session (tenant_id, biz_type, task_id, start_ts DESC);
```

### 10.2a 提示词配置 + 版本快照（0002/0003 迁移）
```sql
-- 主表: (tenant_id, biz_type, scenario) 三元组唯一键; is_active 标记当前生效版本
CREATE TABLE IF NOT EXISTS callbot.prompt_config (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     TEXT NOT NULL DEFAULT 'default',
  biz_type      TEXT NOT NULL,
  scenario      TEXT NOT NULL,
  title         TEXT,
  content       TEXT NOT NULL,       -- 含 {变量} 占位符
  category      TEXT,
  extra         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- variables[] 声明
  dept_id       TEXT,                 -- 映射 biz_type
  version       INT NOT NULL DEFAULT 1,
  is_active     BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, biz_type, scenario)
);

-- 版本快照: 支持回滚, 每次编辑保存 version 自增 + 写一条快照
CREATE TABLE IF NOT EXISTS callbot.prompt_version (
  id            BIGSERIAL PRIMARY KEY,
  prompt_id     BIGINT NOT NULL REFERENCES callbot.prompt_config(id),
  version       INT NOT NULL,
  title         TEXT,
  content       TEXT,
  category      TEXT,
  extra         JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_active     BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 10.2b DID 呼入路由（0003 迁移）
```sql
-- DID/号段 → (tenant_id, biz_type, scenario); agent-flow CHANNEL_ANSWER 查此表解析
CREATE TABLE IF NOT EXISTS callbot.inbound_route (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     TEXT NOT NULL DEFAULT 'default',
  did           TEXT UNIQUE,        -- 精确号(优先匹配)
  did_pattern   TEXT,               -- 号段正则(兜底匹配)
  biz_type      TEXT NOT NULL,
  scenario      TEXT NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  description   TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 10.2c 外呼任务 + 号码清单（0001 全量建表，0002/0003 扩 call_target.vars）
> ⚠️ 执行引擎**已实现**（agent-flow `OutboundExecutor`，进程内单例，lifespan 启停）：tick 轮询 `running` 任务 → `allowed_hours` 时段校验 → 并发槽位（per-task `concurrent_limit` / 全局 `concurrent_global`）→ CAS 认领 `call_target`（pending→dialing）→ `bgapi originate` → 摘机触发 CHANNEL_ANSWER 复用 inbound 管线 → 挂机按 Hangup-Cause + `redial_strategy` 重拨或终态（done/failed）。下方 DDL 与 alembic 0001 实际建表对齐（早期文档误标「仅定义层无执行器」，已纠正）。
```sql
-- call_task: 外呼任务定义。策略字段(concurrent_limit/allowed_hours/redial_strategy)
-- 被 OutboundExecutor 消费，不再是声明性死字段。
CREATE TABLE IF NOT EXISTS callbot.call_task (
  id                BIGSERIAL    PRIMARY KEY,
  tenant_id         TEXT         NOT NULL,
  name              TEXT         NOT NULL,
  prompt_id         BIGINT       NOT NULL REFERENCES callbot.prompt_config(id),
  kb_ids            JSONB        NOT NULL DEFAULT '[]'::jsonb,
  status            TEXT         NOT NULL DEFAULT 'idle',    -- idle/running/paused/completed/failed
  concurrent_limit  INTEGER      NOT NULL DEFAULT 1,
  allowed_hours     TEXT,                                     -- 如 "09:00-21:00"（TEXT 非 JSONB）
  redial_strategy   JSONB        NOT NULL DEFAULT '{}'::jsonb,
  dept_id           TEXT,                                     -- 映射 biz_type
  description       TEXT,
  create_time       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  create_user       TEXT         NOT NULL DEFAULT 'system',
  update_time       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  update_user       TEXT         NOT NULL DEFAULT 'system'
);
CREATE INDEX ix_call_task_tenant ON callbot.call_task (tenant_id);

-- call_target: 号码清单。task_id 裸列无外键（应用层关联删除）。
-- vars = 每号码 render 变量（TEXT，key:value|key:value，摘机 parse_call_target_vars 解析进 graph state call_task_vars）；
-- customer_id = 结构化 CSV 导入的客户id（仅展示/审计，不进渲染）。
CREATE TABLE IF NOT EXISTS callbot.call_target (
  id                    BIGSERIAL   PRIMARY KEY,
  task_id               BIGINT      NOT NULL,                  -- 关联 call_task.id（无 FK，应用层从属删除）
  tenant_id             TEXT        NOT NULL,
  phone_hash            TEXT        NOT NULL,                  -- 去重键（sha256，与 agent-flow _phone_hash 对齐）
  phone_masked         TEXT,                                  -- 138****1234
  user_key             TEXT        NOT NULL,                  -- 明文号码（originate 被叫 {phone}）
  status               TEXT        NOT NULL DEFAULT 'pending', -- pending/dialing/answered/no_answer/failed/done
  attempt_count        INTEGER     NOT NULL DEFAULT 0,
  max_attempts         INTEGER     NOT NULL DEFAULT 1,
  next_attempt_ts      TIMESTAMPTZ,                            -- 重拨退避
  last_call_session_id BIGINT,
  last_hangup_cause   TEXT,
  vars                 TEXT        NOT NULL DEFAULT '',        -- 每号码变量 key:value|key:value（0002 建 JSONB→0003 改 TEXT）
  customer_id          TEXT,                                   -- 结构化导入客户id（审计）
  create_time          TIMESTAMPTZ NOT NULL DEFAULT now(),
  create_user          TEXT        NOT NULL DEFAULT 'system',
  update_time          TIMESTAMPTZ NOT NULL DEFAULT now(),
  update_user          TEXT        NOT NULL DEFAULT 'system',
  CONSTRAINT uq_call_target_task_phone UNIQUE (task_id, phone_hash)
);
CREATE INDEX ix_call_target_task_status ON callbot.call_target (task_id, status);
```

### 10.3 逐轮对话表（按月分区）
```sql
CREATE TABLE IF NOT EXISTS callbot.call_turn (
  turn_id        BIGSERIAL,
  call_id        UUID NOT NULL,
  fs_uuid        UUID NOT NULL,
  tenant_id      TEXT NOT NULL DEFAULT 'default',
  biz_type       TEXT NOT NULL,
  user_key       TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  text           TEXT,
  asr_conf       REAL,
  start_ms       INT,
  end_ms         INT,
  ts             TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (turn_id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS idx_call_turn_call
  ON callbot.call_turn (call_id, ts);

CREATE INDEX IF NOT EXISTS idx_call_turn_tenant_biz_user_ts
  ON callbot.call_turn (tenant_id, biz_type, user_key, ts DESC);
```

**分区创建（示例：每月）**
```sql
-- 例如 2026-05
CREATE TABLE IF NOT EXISTS callbot.call_turn_202605
  PARTITION OF callbot.call_turn
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
```

### 10.4 事件流表（按月分区）
```sql
CREATE TABLE IF NOT EXISTS callbot.call_event (
  event_id      BIGSERIAL,
  call_id       UUID NOT NULL,
  fs_uuid       UUID NOT NULL,
  tenant_id     TEXT NOT NULL DEFAULT 'default',
  biz_type      TEXT NOT NULL,
  user_key      TEXT NOT NULL,
  event_type    TEXT NOT NULL,
  payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
  ts            TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS idx_call_event_call
  ON callbot.call_event (call_id, ts);

CREATE INDEX IF NOT EXISTS idx_call_event_tenant_biz_user_ts
  ON callbot.call_event (tenant_id, biz_type, user_key, ts DESC);

CREATE INDEX IF NOT EXISTS idx_call_event_type_ts
  ON callbot.call_event (tenant_id, biz_type, event_type, ts DESC);
```

### 10.5 录音/音频产物表（用于回放与审计）
```sql
CREATE TABLE IF NOT EXISTS callbot.call_artifact (
  artifact_id   BIGSERIAL PRIMARY KEY,
  call_id       UUID NOT NULL,
  fs_uuid       UUID NOT NULL,
  tenant_id     TEXT NOT NULL DEFAULT 'default',
  biz_type      TEXT NOT NULL,
  user_key      TEXT NOT NULL,
  kind          TEXT NOT NULL, -- recording(整通双声道)/caller_wav/bot_wav/mix_wav/tts_wav/meta_json
  storage       TEXT NOT NULL CHECK (storage IN ('nas','minio')),
  uri           TEXT NOT NULL, -- MinIO 对象 key: recordings/{YYYYMMDD}/{uuid}.wav
  sha256        TEXT,
  size_bytes    BIGINT,
  content_type  TEXT,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artifact_call
  ON callbot.call_artifact (call_id, kind);

CREATE INDEX IF NOT EXISTS idx_artifact_tenant_biz_ts
  ON callbot.call_artifact (tenant_id, biz_type, ts DESC);
```

### 10.6 配置快照表（Prompt/TTS/Flow版本可追溯）
```sql
CREATE TABLE IF NOT EXISTS callbot.config_snapshot (
  snapshot_id   BIGSERIAL PRIMARY KEY,
  call_id       UUID NOT NULL,
  fs_uuid       UUID NOT NULL,
  biz_type      TEXT NOT NULL,
  user_key      TEXT NOT NULL,
  prompt_version TEXT,
  flow_version   TEXT,
  tts_profile_version TEXT,
  dialplan_version TEXT,
  snapshot      JSONB NOT NULL,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snapshot_call
  ON callbot.config_snapshot (call_id, ts DESC);
```

### 10.7 结构化记忆（mem0 facts）
```sql
CREATE TABLE IF NOT EXISTS callbot.user_memory_fact (
  id            BIGSERIAL PRIMARY KEY,
  biz_type      TEXT NOT NULL,
  user_key      TEXT NOT NULL,
  fact_type     TEXT NOT NULL,
  fact_value    JSONB NOT NULL,
  confidence    REAL,
  first_seen_ts TIMESTAMPTZ NOT NULL,
  last_seen_ts  TIMESTAMPTZ NOT NULL,
  source_call_id UUID,
  expire_ts     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_mem_fact_user
  ON callbot.user_memory_fact (biz_type, user_key, fact_type);

CREATE INDEX IF NOT EXISTS idx_mem_fact_lastseen
  ON callbot.user_memory_fact (biz_type, user_key, last_seen_ts DESC);
```

### 10.8 向量记忆（pgvector，维度=1536，按月分区）
```sql
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector (
  id            BIGSERIAL,
  biz_type      TEXT NOT NULL,
  user_key      TEXT NOT NULL,
  content       TEXT NOT NULL,
  embedding     vector(1536) NOT NULL,
  tags          JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_call_id UUID,
  source_turn_id BIGINT,
  ts            TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS idx_mem_vec_user_ts
  ON callbot.user_memory_vector (biz_type, user_key, ts DESC);
```

**HNSW 向量索引（每个分区单独建）**  
（PG分区表上的向量索引通常需要在分区上创建）
```sql
-- 示例分区
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector_202605
  PARTITION OF callbot.user_memory_vector
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

-- 分区上建 HNSW 索引
CREATE INDEX IF NOT EXISTS idx_mem_vec_202605_hnsw
  ON callbot.user_memory_vector_202605
  USING hnsw (embedding vector_cosine_ops);
```

---

## 11. 开机自启、目录规划、日志隔离、录音隔离方案 [1]

### 11.1 Redis Key 规范（按 租户 + 业务 + 用户 + 时间 分割）

#### 11.1.1 命名约定（统一前缀）
- `tenant_id`：租户（kebab-case，迁移回填自历史值，default 兜底）
- `biz_type`：`customer_service|collection|marketing`
- `scenario`：业务场景（DID 路由解析，如 activation/repayment/consult）
- `user_key`：`{core_user_id}:{phone_hash_salted}`
- `yyyymm`：例如 `202605`
- `fs_uuid`：FreeSWITCH Unique-ID（uuid）

统一前缀（**含 tenant_id**）：
- `cb:{tenant_id}:{biz_type}:{user_key}:{yyyymm}:...`
- 提示词缓存：`cb:prompt:{tenant_id}:{biz_type}:{scenario}`（TTL 5min，Console publish/rollback 直删此 key 实现零延迟生效）
- 与会话相关的临时态可用 `cb:call:{fs_uuid}:...`（仍需在 value 中带 tenant_id/biz_type 以便审计与排障）

#### 11.1.2 通话会话态（强烈建议，TTL=24h）
- Key：`cb:call:state:{fs_uuid}`
- Type：Hash
- Fields（示例）：
  - `call_id`
  - `tenant_id`
  - `biz_type`
  - `scenario`
  - `user_key`
  - `task_id`
  - `recording_notice_played`（0/1）
  - `identity_verified`（0/1）
  - `verify_attempts`（int）
  - `silence_count`（int）
  - `asr_fail_count`（int）
  - `llm_fail_count`（int）
  - `tts_fail_count`（int）
  - `tts_profile_version`
  - `prompt_version`
  - `flow_version`
- TTL：24h（通话结束后可缩短为 1h，便于排查）

#### 11.1.3 最近对话滑窗（orchestrator 每轮处理后写入，TTL=24h）
- Key：`cb:call:window:{fs_uuid}`
- Type：List
- Value：每条为 JSON（role/text/ts/asr_conf/turn_id）
- 保留最近 N 条：`LTRIM` 到 50 或 100

用途：
- 给 LLM 组装短上下文（避免每轮都查 PG）

#### 11.1.4 用户“当月热记忆”缓存（TTL=90d，按业务可调）
- Key：`cb:mem:hot:{tenant_id}:{biz_type}:{user_key}:{yyyymm}`
- Type：Hash 或 JSON（建议 Hash：便于更新单个 fact）
- Fields（示例）：
  - `pref_contact_time`
  - `last_intent`
  - `do_not_call`（营销特别重要）
  - `verified_name_masked`
  - `gender_confirmed`
  - `collection_last_commitment_date`（仅催收且核验后）

用途：
- 开场/每轮决策前快速取用户偏好与关键标记

#### 11.1.5 TTS 缓存索引（跨通话复用，TTL=可长）
- Key：`cb:tts:cache:{biz_type}:{tts_profile_version}:{text_hash}`
- Type：String
- Value：MinIO 对象 key 或 NAS 路径（建议 MinIO 对象 key）
- 约束：
  - key 必须包含 biz_type + profile_version，确保严格隔离
  - 禁止缓存含敏感个资的动态句子（或必须加密+短TTL）

#### 11.1.6 MCP 身份查询缓存（TTL=10–30min）
- Key：`cb:core:id:{tenant_id}:{biz_type}:{user_key}`
- Type：String(JSON)
- 用途：降低 java-mcp-server 请求频次（注意与合规一致：仅缓存必要字段，脱敏/最小化）

---

### 11.2 mem0 记忆策略（写入、更新、衰减、召回）

#### 11.2.1 记忆分层（与 Redis/PG 配合）
- 短期（Redis hot memory）：
  - 频繁读取，TTL 90d（可按业务调整）
  - 保存稳定偏好/拒绝标记/已核验状态摘要（不存敏感原文）
- 长期（PG facts）：
  - 可审计、可解释、可追溯来源（source_call_id/source_turn_id）
- 相似召回（PG vector，embedding=1536）：
  - 存对话摘要、用户典型异议、成功处理片段等文本向量
  - 查询按 `biz_type + user_key + 时间窗` 限制，防止无界检索

#### 11.2.2 写入时机（强建议）
- **通话结束 finalize**：写“本通摘要 + 关键facts + 向量记忆”
- **关键节点写入**（可选）：
  - 用户明确拒绝营销/退订（立刻写 do_not_call）
  - 催收核验通过后首次确认关键还款计划（写 commitment）

#### 11.2.3 facts 抽取规则（建议一开始规则+LLM混合）
- 规则抽取（优先，确定性强）：
  - do_not_call
  - preferred_contact_time
  - identity_verified（只写布尔与脱敏）
- LLM 抽取（在合规模板下）：
  - 常见异议类型（太忙/不需要/已处理/非本人等）
  - 用户情绪标签（用于质检）
  - 话术有效性标签（用于运营优化）

#### 11.2.4 衰减与过期（按业务）
- 营销：拒绝/退订类记忆长期保留；“兴趣偏好”衰减更快（例如 90–180 天）
- 客服：偏好与常见问题可中等衰减（180–365 天）
- 催收：合规允许范围内保留更久（与1–3年录音保留期协调），但敏感字段必须脱敏存储

#### 11.2.5 召回组装为 LLM Memory Block（每轮）
按顺序拼接，严格控制长度：
1) Redis hot facts（最短、最关键）
2) PG facts（最近 90 天 topK）
3) pgvector 相似召回（最近 180 天 topK=3~5）
4) 输出一段简短结构化文本（不含敏感原文）

---

### 11.3 PG/Redis 与业务隔离的强制规则
- 任何 mem0/向量检索都必须带 `biz_type` 过滤
- 任何 key 都必须带 `biz_type` 或在 value 中携带并校验，防止跨业务串读
- 催收敏感字段 facts：仅核验通过写入，且存脱敏/区间，不存原始全量

---

### 11.4 目录与录音隔离（NAS/MinIO）
- NAS：`/nas/rec/{biz_type}/YYYY/MM/DD/{call_id}/`
- MinIO：bucket `rec-cs`、`rec-collection`、`rec-marketing`
- 对象命名建议：`{YYYY}/{MM}/{DD}/{call_id}/{kind}/{filename}`
- 生命周期：后管配置 1–3 年，统一落 `record_policy`，MinIO Lifecycle 应用到 bucket/prefix

---

## 12. 常见报错、坑点、优化调优方案 [1]

### 12.1 Redis/记忆相关
- 热记忆污染：必须按 `biz_type` 分 key；禁止共用 `user_key` 不带 biz 的 key
- TTL 失控：统一由配置中心下发 TTL 策略（营销/客服/催收不同）
- do_not_call 必须“立即生效”：写入 Redis hot + PG fact，并通知任务调度侧过滤

### 12.2 pgvector 相关（PG17）
- 向量分区表索引必须在分区上建（HNSW）
- 检索必须加条件：`biz_type, user_key, ts >= now()-interval '180 days'`，并限制 topK

### 12.3 WebSocket 双向音频链路 + 流式管道
- ASR 识别文本为空：检查 VAD 参数（WebRTC/Silero）、音频格式、agent-asr 健康状态
- orchestrator 超时：检查 LLM 响应时间、MCP Server 连通性、TTS 合成延迟
- WebSocket 断连：检查 agent-flow 进程状态、FreeSWITCH mod_audio_fork 日志
- Barge-in 失效：检查 ESL 连接、RMSGate 门限（CALLBOT_BARGE_IN_MIN_AUDIO_BYTES / CALLBOT_RMS_GATE_*）；打断靠清空 TTSOutputBuffer（**不调 uuid_break**），检查冷却期 CALLBOT_COOLDOWN_AFTER_BARGEIN 是否把残留噪声当说话
- TTS 音频断续：检查 JitterBuffer 深度配置（CALLBOT_JITTER_TARGET_DEPTH）、TTSOutputBuffer 帧率
- CHANNEL_HANGUP 未触发：检查 ESL Event Socket 连接、event_socket.conf.xml 配置

### 12.4 高并发 TTS
- 营销 200 路时 TTS 队列最容易爆：必须启用常用话术缓存，并对营销并发做动态降级
- Prometheus 告警：TTS P95、排队长度、缓存命中率下降


# 智能外呼配置文件说明

## 目录结构

```
智能外呼配置文件/
├── freeswitch/
│   ├── modules.conf              # 模块加载配置 (含 mod_event_socket)
│   ├── autoload_configs/         # modules.conf.xml (XML 模块配置)
│   ├── sip_profiles/             # internal.xml (SIP profile)
│   ├── vars.xml                   # 全局变量
│   ├── event_socket.conf.xml      # ESL 监听配置
│   ├── dialplan/
│   │   └── public/00_biz_type.xml  # 拨号计划
│   └── mrcp-plugin/              # UniMRCP 1.5.0 (MRCP/ASR 备选)

应用服务组件/
├── agent-asr/
│   ├── asradapter/    # ASR 服务 (FastAPI + WebSocket, 内置 GPU 推理, :8080)
│   ├── models/        # SenseVoiceSmall/ 本地模型权重
│   └── Dockerfile     # PyTorch GPU 镜像
├── agent-tts/
│   ├── CosyVoice/     # CosyVoice 源码 (推理运行时)
│   ├── ttsadapter/    # TTS 服务 (FastAPI + WebSocket, 内置 GPU 推理, :8081)
│   ├── models/        # CosyVoice3-0.5B/ 本地模型权重
│   └── Dockerfile     # PyTorch GPU 镜像
├── agent-flow/
│   ├── main.py        # FastAPI 入口
│   ├── src/           # 核心源码 (LangGraph 7 节点 + 流式管道 + WebSocket + ESL + ASR/TTS WS 客户端 + 降噪, port 8000, uvloop)
│   ├── llm/           # LLM 推理引擎 (Qwen3.5-9B, vLLM)
│   ├── alembic/       # 数据库迁移
│   └── Dockerfile
├── agent-mcp/
│   └── java-mcp-server/  # 用户中心 MCP Server (Spring Boot 4.0 + Spring AI 2.0, port 9090)
├── scripts/               # 启动脚本
│   ├── local.sh           # 本地开发 (conda)
│   └── prod.sh            # 生产部署 (Docker Compose)
├── docker-compose.yml     # 基础编排
├── docker-compose.prod.yml # 生产覆盖
└── .env                   # 环境变量 (CALLBOT_ 前缀, 不提交)
```

## 部署顺序

### 阶段 1: FreeSWITCH 基础配置

1. **vars.xml** - 设置全局变量
   - SIP 端口、RTP 范围、编码
   - agent-flow WebSocket URL
   - 业务变量默认值
   - 转接目标分机号

2. **modules.conf** - 加载必要模块
   - 核心模块：mod_sofia, mod_audio_fork, mod_dptools, mod_sndfile, mod_event_socket
   - 编码模块按需加载

### 阶段 2: 拨号计划

3. **dialplan/public/00_biz_type.xml** - catch-all 呼入路由
   - catch-all `^(\d+)$` → set user_key → answer + playback silence_stream://-1 保持通话
   - **三元组不在 dialplan 硬编码**，由 agent-flow CHANNEL_ANSWER 查 inbound_route 解析（Console 运营，即时生效）
   - 录音提示音由 `CALLBOT_RECORDING_NOTICE_ENABLED` 开关控制
   - 转人工：loopback/1001

### 阶段 3: 应用服务

4. **agent-asr** - ASR 服务（GPU0）
   - 内置 SenseVoice GPU 推理
   - WebSocket 流式传输 (:8080, FSMN-VAD 服务端分段)
   - PyTorch 基础镜像
   - 构建时下载模型

5. **agent-tts** - TTS 服务（GPU1）
   - 内置 CosyVoice3 GPU 推理
   - WebSocket 流式传输 (:8081)
   - PyTorch 基础镜像
   - 构建时下载模型

6. **agent-flow** - 编排服务
   - WebSocket 双向音频 + HTTP API + ESL 事件控制
   - 流式 LLM + 句级 TTS + Barge-in 打断
   - uvloop 事件循环 (高并发优化)
   - VAD 前置降噪 (可选: highpass/noisereduce/rnnoise)
   - ASR/TTS 均走 WebSocket 流式（唯一传输）
   - 连接 agent-asr, agent-tts, MCP, LLM, FreeSWITCH ESL

## 依赖关系

```
用户来电
    │
    └─→ FreeSWITCH (mod_sofia) → dialplan(catch-all ^(\d+)$ → set user_key → answer → playback silence_stream://-1) → CHANNEL_ANSWER 事件
            │
            ├─→ ESL Event Socket (:8021) ←── agent-flow
            │       │
            │       ├─→ CHANNEL_ANSWER 订阅 → _resolve_inbound_route(DID) → (tenant_id,biz_type,scenario) → register + insert_call_session → uuid_audio_fork start
            │       ├─→ CHANNEL_HANGUP 订阅 → uuid_audio_fork stop → update_session_end → _archive_recording(fire-and-forget) → ActiveCallRegistry 取消通话
            │       ├─→ 打断：清空 TTSOutputBuffer（不调 uuid_break，避免终止 dialplan playback）
            │       └─→ uuid_transfer 转人工 / uuid_kill 挂断
            │
            └─→ uuid_audio_fork ──→ agent-flow (:8000, uvloop) WebSocket /media/{uuid} 双向音频
                    │
                    ├─→ CallRecorder.feed_caller(frame)  ← 录音 L 声道 (VAD 前)
                    ├─→ JitterBuffer → WebRTCAPM(AEC/NS/AGC) 或 Denoiser → agent-asr (:8080 WS, 服务端 FSMN-VAD 分段) ──→ SenseVoice GPU0
                    │                                                       └─→ 返回识别文本
                    │
                    ├─→ get_system_prompt(tenant_id, biz_type, scenario) ← 三维 Redis→DB + render.py 渲染
                    ├─→ 决策 ──→ agent-flow LangGraph 7 节点 (流式管道)
                    │              │
                    │              ├─→ java-mcp-server (:9090) 用户中心（身份/征信查询）
                    │              ├─→ Qwen3.5-9B (GPU2) 流式输出
                    │              ├─→ Redis（热记忆/对话历史/prompt 三维缓存）
                    │              ├─→ PG17 pgvector（长期记忆/RAG话术库/call_turn fire-and-forget 双写）
                    │              └─→ agent-tts (:8081 WS) ──→ CosyVoice GPU1
                    │                       └─→ recorder.feed_ai(pcm)  ← 录音 R 声道
                    │
                    └─→ TTS 音频 ──→ TTSOutputBuffer ──→ WebSocket 回传 FreeSWITCH ──→ 播放给用户
```

FreeSWITCH 拨号计划为 catch-all，`answer` + `playback silence_stream://-1`（无限静音保活）后，agent-flow 通过 ESL 订阅 CHANNEL_ANSWER 事件，查 `inbound_route` 解析三元组，动态调用 `uuid_audio_fork` 启动 WebSocket 双向音频流。agent-flow 统一调度 ASR → 流式决策 → 句级 TTS 全链路。ESL Event Socket 实现接通/挂断/打断/转接全生命周期控制。**Console (:3001) 共用 callbot schema + Redis**，发布提示词即时失效缓存，新增 DID/租户/scenario 即时生效，无需改 FreeSWITCH。

**新增：console 服务**（依赖 PG/Redis，与 agent-flow 共库）
- 端口 3001，Next.js + Drizzle + Better Auth
- 部署后执行 console 自有迁移（`console/server/src/db/migrations/0001_init_console.sql`）建 `console` schema；`callbot` schema 表由 agent-flow alembic 维护

## 验收要点

### FreeSWITCH
- [ ] `fs_cli` 能正常连接
- [ ] `show modules` 显示已加载 mod_audio_fork, mod_event_socket
- [ ] SIP 通话能正常建立
- [ ] CHANNEL_ANSWER 事件触发 uuid_audio_fork start 成功
- [ ] uuid_audio_fork WebSocket 连接 agent-flow /media/{uuid} 正常

### Orchestrator
- [ ] WebSocket /media/{uuid} 双向音频流通 (事件驱动 audio_fork)
- [ ] ESL CHANNEL_ANSWER 事件触发 uuid_audio_fork start
- [ ] MCP Client 连接 java-mcp-server 成功
- [ ] 身份查询工具 (`user_identity_query`) 正常返回
- [ ] 征信查询工具 (`user_credit_query`) 正常返回 (仅 marketing)
- [ ] LLM 流式输出 + 句级 TTS 合成正常
- [ ] TTS 播报正常
- [ ] Barge-in 打断功能正常（用户说话时停止 AI 播放）
- [ ] ESL Event Socket 连接正常
- [ ] WebSocket ASR 流式识别正常（asr_ws_client.py，服务端 FSMN-VAD 分段回推 final）
- [ ] WebSocket TTS 流式合成正常（tts_ws_client.py）
- [ ] uvloop 事件循环生效（pip freeze | grep uvloop）
- [ ] 降噪模块工作正常（CALLBOT_DENOISE_ENABLED=highpass 时 VAD 误判减少）
- [ ] CHANNEL_HANGUP 事件触发通话取消
- [ ] 录音文件生成
- [ ] 记忆写入正常

### 业务隔离
- [ ] 三种 biz_type 音色不同
- [ ] Redis key 按 (tenant_id, biz_type) 隔离
- [ ] prompt 三维缓存 key `cb:prompt:{tenant_id}:{biz_type}:{scenario}`
- [ ] Console 跨租户返回 404（不泄漏存在性）
- [ ] 录音目录按 biz_type 隔离
- [ ] PG 数据按 (tenant_id, biz_type) 过滤

## 配置参数对照表

| 组件 | 配置项 | 示例值 |
|------|--------|--------|
| FS | SIP 端口 | 5060 |
| FS | RTP 范围 | 16384-32768 |
| FS | WebSocket URL | ws://agent-flow:8000/media/{uuid} (动态拼接) |
| FS | Event Socket 端口 | 8021 |
| ASR | 服务地址 | :8080 |
| ASR | 模型路径 | MODEL_DIR=/opt/sensevoice/models/SenseVoiceSmall |
| TTS | 服务地址 | :8081 |
| TTS | 模型路径 | MODEL_DIR=/opt/cosyvoice/models/CosyVoice3-0.5B |
| LLM | 推理地址 | :8083 (GPU2) |
| Orchestrator | 服务地址 | :8000 |
| Orchestrator | ESL 地址 | CALLBOT_ESL_HOST:CALLBOT_ESL_PORT |
| Orchestrator | 端点检测 | 服务端 FSMN-VAD（agent-asr 分段 → 回推 final → on_final 触发轮次，无本地 VAD 引擎） |
| Orchestrator | Barge-in 门禁 | CALLBOT_RMS_GATE_THRESHOLD=300, CALLBOT_RMS_GATE_SNR_FACTOR=3.0, CALLBOT_RMS_GATE_NOISE_FLOOR_INIT=300, CALLBOT_RMS_GATE_NOISE_ADAPT_RATE=0.1 |
| Orchestrator | Jitter Buffer | CALLBOT_JITTER_TARGET_DEPTH=3 |
| Orchestrator | 降噪模式 | CALLBOT_DENOISE_ENABLED="" (highpass/noisereduce/rnnoise; 与 AEC 互斥) |
| Orchestrator | WebRTC APM(AEC) | CALLBOT_AEC_ENABLED=false, CALLBOT_AEC_TYPE=2(1=AECM/2=老AEC), CALLBOT_AEC_NS_LEVEL=2, CALLBOT_AEC_AGC_TYPE=1, CALLBOT_AEC_SYSTEM_DELAY_MS=80 (开启时取代 denoise + 固定增益) |
| Orchestrator | 录音 | CALLBOT_RECORDINGS_DIR, CALLBOT_RECORDING_NOTICE_ENABLED=true, CALLBOT_RECORDING_NOTICE_SOUND, CALLBOT_RECORDING_ARCHIVE_DELAY_SEC=3, CALLBOT_RECORDING_ARCHIVE_TIMEOUT=30 |
| Orchestrator | ASR WebSocket | CALLBOT_ASR_WS_URL=ws://127.0.0.1:8080/ws/asr/streaming-recognize (唯一传输) |
| Orchestrator | TTS WebSocket | CALLBOT_TTS_WS_URL=ws://127.0.0.1:8081/ws/tts/streaming-synthesize (唯一传输) |
| Orchestrator | Audio gain | CALLBOT_AUDIO_GAIN=1.0 (放大安静 SIP 音频) |
| Orchestrator | TTS pre-buffer | CALLBOT_TTS_PREBUFFER_FRAMES=0 (预缓冲 N 帧) |
| Orchestrator | 事件循环 | uvloop (Dockerfile --loop uvloop) |
| TTS | CosyVoice device | COSYVOICE_DEVICE=auto (cpu/mps/auto, Mac本地建议cpu) |
| MCP Server | 用户中心 | :9090 |
| Console | 管理控制台 | :3001 (DATABASE_URL/REDIS_URL/MINIO_*/CONSOLE_ADFS_ENABLED, 详见 console/server/.env.example) |
| Redis | 地址 | 10.0.0.30:6379 |
| PG | 地址 | 10.0.0.31:5432 |
| MinIO | 地址 | 10.0.0.32:9000 (录音归档 + Console presigned 回放) |
| NAS | 挂载点 | /nas/rec |

## 常见问题

### 1. WebSocket 连接失败
- 检查 agent-flow 是否启动（:8000）
- 检查 vars.xml 中 agent_flow_ws_url 配置
- 检查 mod_audio_fork 是否加载

### 1.1 ESL 连接失败
- 检查 mod_event_socket 是否加载
- 检查 event_socket.conf.xml 中端口/密码配置
- 检查 CALLBOT_ESL_HOST/CALLBOT_ESL_PORT/CALLBOT_ESL_PASSWORD 环境变量

### 2. ASR 识别为空
- 检查 agent-asr 服务状态（:8080）
- 检查 GPU 可用性
- 检查模型是否加载（MODEL_DIR 路径）
- 检查 agent-asr 服务端 FSMN-VAD 分段是否正常（日志见 `[WS-ASR] result ... is_final`，无 final 则不触发轮次）

### 3. TTS 播放无声
- 检查 agent-tts 服务状态（:8081）
- 检查 CosyVoice 运行时环境（COSYVOICE_RUNTIME）
- Mac 本地开发: COSYVOICE_DEVICE=cpu 避免 MPS fallback 开销（local.sh 已默认）
- 检查 biz_type 对应的音色配置

### 4. 录音文件不存在 / 录音归档失败
- 检查 `CALLBOT_RECORDINGS_DIR` 下是否有 `${uuid}.wav`（CallRecorder 写入）
- 检查 MinIO 配置：`CALLBOT_MINIO_ENDPOINT` 为空时 `upload_recording` 静默跳过（不写 call_artifact）；MinIO 已统一走 `CALLBOT_MINIO_*` 前缀（pydantic-settings 加载）
- 自动归档漏归档时调 `POST /calls/{fs_uuid}/archive-recording` 手动补归档（Console 详情页有按钮）
- 检查 `_archive_recording` 是否被 GC（强引用集 `_ongoing_archives`），挂断后延迟 `CALLBOT_RECORDING_ARCHIVE_DELAY_SEC`（默认3s）才读 wav
- 检查 inbound_route 无 DID 匹配时回落 default（tenant_id/scenario = default）

### 4.1 WebRTCAPM 降级
- `CALLBOT_AEC_ENABLED=true` 但日志见 "WebRTCAPM process failed, passthrough"：livekit 库缺失或帧格式错误，单帧错误降级透传原帧（不影响通话）
- AEC 开启时不再叠加固定 `CALLBOT_AUDIO_GAIN`（AGC 由 WebRTCAPM 逐帧处理）

### 5. 环境噪音导致 barge-in 误判
- 启用降噪：设置 CALLBOT_DENOISE_ENABLED=highpass
- 调高 RMSGate 门限：CALLBOT_RMS_GATE_THRESHOLD / CALLBOT_RMS_GATE_SNR_FACTOR（自适应门限 = noise_floor × snr_factor）
- 增加最小音频阈值：调高 CALLBOT_BARGE_IN_MIN_AUDIO_BYTES
- barge-in 后冷却期 CALLBOT_COOLDOWN_AFTER_BARGEIN 内丢弃残余音频防误触发

## 文件版本

| 文件 | 版本 | 更新日期 |
|------|------|----------|
| modules.conf | 2.0 | 2026-05-16 |
| vars.xml | 2.0 | 2026-05-16 |
| dialplan/public/00_biz_type.xml | 2.0 | 2026-05-16 |
| agent-asr Dockerfile | 2.1 | 2026-05-22 |
| agent-tts Dockerfile | 2.1 | 2026-05-22 |
| agent-flow Dockerfile | 2.1 | 2026-05-22 |
| java-mcp-server | 0.0.1-SNAPSHOT | 2026-05-16 |
| docker-compose.yml | 1.0 | 2026-05-25 |
| docker-compose.prod.yml | 1.0 | 2026-05-25 |
| scripts/local.sh | 1.0 | 2026-05-25 |
| scripts/prod.sh | 1.0 | 2026-05-25 |

## 联系方式

如有配置问题，请参考各配置文件内的注释说明。