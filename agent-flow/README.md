# agent-flow

LangGraph 7-node 通话编排管线 — FastAPI WebSocket service (uvloop event loop).

## 功能

- **事件驱动音频 fork**: ESL 订阅 `CHANNEL_ANSWER` + `CHANNEL_HANGUP`，动态 `uuid_audio_fork` 启停
- **流式 LLM + TTS**: LLM token 流 → `SentenceSplitter` 句级切分 → 并行 TTS → `TTSOutputBuffer` 稳态 30ms 帧输出
- **Barge-in 打断**: AI 说话时并发接收用户音频，VAD 检测打断 → 清空 TTS buffer → 冷却期防误触发
- **提示词管理**: Redis 缓存（5min TTL）→ PostgreSQL `prompt_config` 表两级降级，每轮日志打印提示词内容
- **多传输**: ASR/TTS 支持 HTTP / gRPC / WebSocket 三种传输方式
- **Pluggable VAD**: WebRTC（RMS 能量门控）和 Silero（神经网络）两种 VAD 引擎
- **降噪**: 可配置前置降噪（highpass / noisereduce / rnnoise）
- **ESL 自动重连**: 读异常自动重连 + heartbeat 检测，`break_media` fire-and-forget 绕过锁争用

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

提示词存储在数据库 `callbot.prompt_config` 表中，按 `biz_system` + `biz_type` 维度管理。

- **Redis 缓存**: `cb:prompt:{biz_system}:{biz_type}`，TTL 5 分钟
- **数据库降级**: Redis miss 时查询 `callbot.prompt_config` WHERE `is_active=true`
- **初始化数据**: `alembic/versions/0002_prompt_config.py` 包含三种业务类型的默认提示词

修改提示词后调用 `invalidate_prompt_cache()` 清除 Redis 缓存。

## 数据库迁移

```bash
cd agent-flow && PYTHONPATH=$(pwd)/src alembic upgrade head
```

## 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/healthz` | GET | 健康检查 |
| `/media/{uuid}` | WS | 双向音频 WebSocket（16kHz PCM） |
