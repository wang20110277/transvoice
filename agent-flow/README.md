# agent-flow

LangGraph 通话编排管线 + livekit-agents AgentSession 流式对话 — FastAPI WebSocket service (uvloop event loop).

## 功能

- **AgentSession 无头管线**: `WS /media/{uuid}` → `build_agent_session()` → `session.start(agent)`；`TelephonyAudioInput`/`TelephonyAudioOutput`（`src/voice/io.py`）实现 SDK AudioInput/AudioOutput 契约，桥接 mod_audio_fork 双向音频
- **流式 LLM + TTS**: `TransvoiceAgent.llm_node` 复用 LangGraph 节点①-⑤（`run_pre_llm_phase`）+ 节点⑥（`astream_reply_text` LLM 流式 token）；节点⑦由 SDK tts_node 分句 → `TransvoiceTTS` 插件（flush 边界聚合整句）→ `TelephonyAudioOutput` 30ms 匀速帧输出
- **Barge-in 打断**: silero VAD（`inference.VAD` 本地）+ `interruption.min_duration` 检测 → SDK 中断体系清空输出缓冲 → 新一轮对话（PG `call_event` 记 `barge_in`）
- **提示词管理**: Redis 缓存（5min TTL）→ PostgreSQL `prompt_config` 表两级降级，每轮日志打印提示词内容
- **WebSocket 传输**: ASR/TTS 均走 WebSocket（`src/voice/stt_plugin.py` / `tts_plugin.py`，唯一传输）——ASR 批量 recognize 每语音段自建一条连接，TTS 逐 call 自建共享连接 + request_id 解复用
- **STT 非流式切段**: TransvoiceSTT 非流式批量（每语音段一条 WS 连接，agent-asr 整段识别），SDK 默认 stt_node 自动以 session silero VAD 包 StreamAdapter 切段；`turn_detection="vad"` 提交轮次，`CALLBOT_VAD_MIN_SILENCE_DURATION` 控制端点静音阈值
- **降噪**: 可配置前置降噪（highpass / noisereduce / rnnoise）
- **ESL 自动重连**: 读异常自动重连 + heartbeat 检测，`break_media` fire-and-forget 绕过锁争用
- **事件驱动音频 fork**: ESL 订阅 `CHANNEL_ANSWER` + `CHANNEL_HANGUP`，动态 `uuid_audio_fork` 启停

## 快速启动

```bash
# 启动（依赖 FreeSWITCH、ASR、TTS 先就绪）
cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src uvicorn main:app --host 0.0.0.0 --port 8000

# 或使用脚本
./scripts/local.sh flow
```

## 配置

通过 `.env` 文件配置，所有配置项使用 `CALLBOT_` 前缀，由 `pydantic-settings` 管理。详见 `src/config.py`。

### 提示词配置

提示词存储在数据库 `callbot.prompt_config` 表中，按 `(tenant_id, biz_type, scenario)` 三元组维度管理。

- **Redis 缓存**: `cb:prompt:{tenant_id}:{biz_type}:{scenario}`，TTL 5 分钟
- **数据库降级**: Redis miss 时查询 `callbot.prompt_config`
- **初始化数据**: `alembic/versions/0001_init_full_schema.py` 内置三种业务类型默认提示词（seed）

修改提示词后调用 `invalidate_prompt_cache(tenant_id, biz_type, scenario)` 清除 Redis 缓存。

## 数据库迁移

单个全量初始化 migration `0001_init_full_schema.py`（13 表 + 全字段中文 COMMENT + seed）。

```bash
# 全新库：直接 upgrade
cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head

# 已有库（alembic_version 记着旧版本）：须先手动清库再 upgrade（数据全丢）
docker exec callbot-postgres psql -U postgres -d callbot -c 'DROP SCHEMA IF EXISTS callbot CASCADE;'
cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head
```

## 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/healthz` | GET | 健康检查 |
| `/media/{uuid}` | WS | 双向音频 WebSocket（16kHz PCM，FreeSWITCH mod_audio_fork 连接 → AgentSession） |
| `/calls/{uuid}/archive-recording` | POST | 手动录音归档兜底（自动归档 MinIO 不可用时静默跳过，事后补归档） |
