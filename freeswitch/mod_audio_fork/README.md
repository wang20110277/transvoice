# mod_audio_fork

基于 [drachtio/mod_audio_fork](https://github.com/drachtio/mod_audio_fork) 的定制版本，为智能外呼系统 (Smart Outbound Call System) 提供双向实时音频流能力。通过 WebSocket 将 FreeSWITCH 通话音频以 L16 格式流式传输到远端服务，同时接收远端 PCM 音频进行实时播放，实现全双工 AI 语音对话。

> **上游仓库**: 本项目 fork 自 drachtio/mod_audio_fork，在此基础上进行了大量定制修改（见下方 [修改记录](#修改记录)）。

## Features

- **双向音频流** — 通过 WebSocket 同时上行通话音频、下行 AI 合成音频，实现全双工实时对话
- **二进制音频流** — 支持直接接收 raw binary PCM 帧（非 base64 JSON），更低延迟
- **Audio Markers** — 音频播放同步标记（`mark` / `clearMarks`），用于 TTS 句级边界对齐
- **多种混音模式** — Mono（仅来电方）、Mixed（来电方 + 去电方混合）、Stereo（双声道分离）
- **灵活采样率** — 8000 / 16000 / 24000 / 32000 / 48000 / 64000 Hz（8000 的整数倍）
- **内置重采样** — Speex resampler，服务端采样率与通道采样率不同时自动转换
- **TCP Keep-Alive** — 可配置的 TCP keep-alive，防止中间设备/防火墙断开空闲连接
- **TLS 支持** — wss:// 安全 WebSocket 连接
- **SIMD 优化** — AVX2/SSE2 向量化音频处理
- **优雅关闭** — drain 音频缓冲区后再关闭连接
- **ARM64 修复** — bit-field 类型修复，支持 macOS ARM64 / Apple Silicon

## 修改记录

基于上游 drachtio/mod_audio_fork 的定制修改：

### 1. 双向音频流 (Bidirectional Audio)

**核心新增功能**。上游仅支持单向音频上行（fork 到服务端），本版增加了完整的双向音频能力：

| 改动 | 说明 |
|------|------|
| `SWITCH_ABC_TYPE_WRITE_REPLACE` | media bug 回调新增 write_replace 处理，拦截出站音频帧替换为 WebSocket 下行的 AI 语音 |
| `dub_speech_frame()` | 新增函数，从 playout buffer 取 PCM 数据写入 FreeSWITCH 写帧 |
| `processIncomingBinary()` | 处理 WebSocket 接收的 raw binary PCM 帧，放入 pre-buffer → resample → playout buffer |
| `stop_play` 命令 | 新增 API 命令，清空 playout buffer 停止当前播放（用于 barge-in 打断） |
| 二进制帧路由 | `LWS_CALLBACK_CLIENT_RECEIVE` 区分 binary/text 帧，binary 帧交由 `BINARY` 回调处理 |
| `BINARY` 事件类型 | `AudioPipe::NotifyEvent_t` 新增 `BINARY` 枚举，携带 `(data, len)` 参数 |

**start 命令新增参数**：

```
uuid_audio_fork <uuid> start <wss-url> <mix-type> <sampling-rate> \
  [bugname] [metadata] \
  <bidirectionalAudio_enabled> <bidirectionalAudio_stream_enabled> <bidirectionalAudio_stream_samplerate>
```

| 参数 | 说明 |
|------|------|
| `bidirectionalAudio_enabled` | `true` / `false`，启用双向音频（默认 `true`） |
| `bidirectionalAudio_stream_enabled` | `true` / `false`，启用 binary 音频流接收 |
| `bidirectionalAudio_stream_samplerate` | 下行音频采样率（如 `8000`、`16000`） |

### 2. Audio Markers 同步机制

为 TTS 句级播放同步新增的 marker 机制：

- `mark` — 服务端发送命名标记，嵌入音频流
- `clearMarks` — 清除所有待处理标记
- 上限 30 个标记（`MAX_MARKS`）
- 使用 `boost::circular_buffer` 管理标记队列（inventory → in-use → cleared）

### 3. Pre-Buffer + Playout Buffer 架构

```
WebSocket binary frame
  → set-aside byte 对齐（处理奇数长度帧）
  → PreBuffer (boost::circular_buffer)
    → 等待达到阈值后批量 transfer
    → Speex resample（如果采样率不匹配）
    → PlayoutBuffer (boost::circular_buffer, mutex 保护)
      → dub_speech_frame() 在 WRITE_REPLACE 回调中消费
```

- **PreBuffer**: 网络抖动缓冲，积累足够数据后一次性转移
- **PlayoutBuffer**: 播放缓冲，被 `dub_speech_frame()` 以帧为单位消费
- **动态扩容**: `BUFFER_GROW_SIZE (16384)` 按需增长，避免初始分配过大

### 4. ARM64 Bit-Field 修复

`mod_audio_fork.h` 中三个 1-bit signed bit-field 在 ARM64 上赋值 1 时被截断为 -1，导致 SIGSEGV：

```c
// 修复前 — ARM64 上 int:1 范围 {-1, 0}，赋值 1 触发截断
int buffer_overrun_notified:1;
int audio_paused:1;
int graceful_shutdown:1;

// 修复后 — unsigned int:1 范围 {0, 1}，ARM64/x86 均正确
unsigned int buffer_overrun_notified:1;
unsigned int audio_paused:1;
unsigned int graceful_shutdown:1;
```

### 5. 日志系统迁移

所有 `lwsl_notice` / `lwsl_err` / `lwsl_info` 替换为 `switch_log_printf`：

- 统一使用 FreeSWITCH 日志框架，日志输出到 FreeSWITCH 日志系统
- 便于通过 `fs_cli` 按级别过滤查看

### 6. Text Frame 队列化

上游使用单个 `std::string m_metadata` 只能保存一条文本消息，改为 `std::list<std::string> m_metadata_list`：

- 支持多条文本消息排队发送（如 DTMF 事件 + 控制消息并发）
- 每次写事件只发一条，发送成功后 pop 并请求下一个 writable 事件

### 7. LWS v4.x 兼容性

- `lws_protocols` 结构体补全 7 个字段（上游只有 4 个）
- `LWS_CALLBACK_EVENT_WAIT_CANCELLED` 处理 wsi=NULL 情况（v4.x 行为变更）
- `LWS_CALLBACK_PROTOCOL_INIT` 在 vhd NULL 检查之前处理
- 新增 `LWS_CALLBACK_WS_CLIENT_DROP_PROTOCOL` 处理

### 8. Notify Callback 签名扩展

```c
// 上游
typedef void (*notifyHandler_t)(const char* sessionId, const char* bugname,
    NotifyEvent_t event, const char* message);

// 本版 — 新增 binary + len 参数
typedef void (*notifyHandler_t)(const char* sessionId, const char* bugname,
    NotifyEvent_t event, const char* message, const char* binary, size_t binary_len);
```

### 9. 资源管理修复

- `AudioPipe::~AudioPipe()` 中 `m_recv_buf` 使用 `free()` 释放（匹配 `malloc`），上游错误使用 `delete[]`

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `MOD_AUDIO_FORK_SUBPROTOCOL_NAME` | WebSocket sub-protocol 名称 | `audio.drachtio.org` |
| `MOD_AUDIO_FORK_SERVICE_THREADS` | libwebsocket 服务线程数 (1–5) | `1` |
| `MOD_AUDIO_FORK_BUFFER_SECS` | 音频缓冲区大小（秒，1–5） | `2` |
| `MOD_AUDIO_FORK_HTTP_AUTH_USER` | HTTP Basic Auth 用户名 | — |
| `MOD_AUDIO_FORK_HTTP_AUTH_PASSWORD` | HTTP Basic Auth 密码 | — |
| `MOD_AUDIO_FORK_TCP_KEEPALIVE_SECS` | TCP keep-alive 间隔（秒） | `55` |

## Channel Variables

| Variable | Description |
|---|---|
| `MOD_AUDIO_BASIC_AUTH_USERNAME` | WebSocket Basic Auth 用户名 |
| `MOD_AUDIO_BASIC_AUTH_PASSWORD` | WebSocket Basic Auth 密码 |
| `MOD_AUDIO_FORK_ALLOW_SELFSIGNED` | 允许自签名 TLS 证书 (`true`/`false`) |
| `MOD_AUDIO_FORK_SKIP_SERVER_CERT_HOSTNAME_CHECK` | 跳过 TLS 主机名验证 (`true`/`false`) |
| `MOD_AUDIO_FORK_ALLOW_EXPIRED` | 允许过期 TLS 证书 (`true`/`false`) |

## API

### Command Syntax

```
uuid_audio_fork <uuid> <command> [arguments...]
```

### Commands

#### start

```
uuid_audio_fork <uuid> start <wss-url> <mix-type> <sampling-rate> [bugname] [metadata] [bidirectionalAudio_enabled] [bidirectionalAudio_stream_enabled] [bidirectionalAudio_stream_samplerate]
```

| Parameter | Description |
|---|---|
| `uuid` | FreeSWITCH channel UUID |
| `wss-url` | WebSocket URL (`ws://`, `wss://`, `http://`, `https://`) |
| `mix-type` | `mono` (caller only), `mixed` (caller + callee), `stereo` (separate channels) |
| `sampling-rate` | `8k`, `16k`, 或任意 8000 整数倍 |
| `bugname` | 可选，bug 名称（默认 `audio_fork`） |
| `metadata` | 可选，连接后立即发送的 JSON 文本帧 |
| `bidirectionalAudio_enabled` | `true` / `false`（默认 `true`） |
| `bidirectionalAudio_stream_enabled` | `true` / `false`，启用 binary 音频流 |
| `bidirectionalAudio_stream_samplerate` | 下行音频采样率 |

#### stop

```
uuid_audio_fork <uuid> stop [bugname] [metadata]
```

关闭 WebSocket 连接并移除 media bug。可选发送最终文本帧。

#### stop_play

```
uuid_audio_fork <uuid> stop_play [bugname]
```

清空 playout buffer，停止当前音频播放。用于 barge-in（用户打断 AI 说话）场景。

#### send_text

```
uuid_audio_fork <uuid> send_text [bugname] <text>
```

向远端服务发送文本帧（如 DTMF 事件、控制消息）。

#### pause / resume

```
uuid_audio_fork <uuid> pause [bugname]
uuid_audio_fork <uuid> resume [bugname]
```

暂停/恢复音频上行（暂停期间帧被丢弃）。

#### graceful-shutdown

```
uuid_audio_fork <uuid> graceful-shutdown [bugname]
```

优雅关闭 — 停止发送新音频，等待缓冲区 drain 后关闭。

### Events

| Event | Description |
|---|---|
| `mod_audio_fork::connect` | WebSocket 连接成功 |
| `mod_audio_fork::connect_failed` | WebSocket 连接失败 |
| `mod_audio_fork::disconnect` | WebSocket 连接关闭 |
| `mod_audio_fork::buffer_overrun` | 音频缓冲区溢出，帧丢失 |
| `mod_audio_fork::transcription` | 服务端发送转录消息 |
| `mod_audio_fork::transfer` | 服务端发送转接请求 |
| `mod_audio_fork::play_audio` | 服务端发送 base64 音频播放 |
| `mod_audio_fork::kill_audio` | 服务端请求停止播放 |
| `mod_audio_fork::error` | 服务端报告错误 |
| `mod_audio_fork::json` | 服务端发送通用 JSON |

### Server-to-Module Messages

#### playAudio (base64 模式)

```json
{
  "type": "playAudio",
  "data": {
    "audioContentType": "raw",
    "sampleRate": 8000,
    "audioContent": "<base64-encoded raw audio>"
  }
}
```

#### Binary Audio (推荐)

当 `bidirectionalAudio_stream_enabled=true` 时，服务端直接发送 raw binary PCM 帧即可，无需 JSON/base64 封装。模块自动处理：
- 采样率重采样（如果与服务端不同）
- Pre-buffer 平滑网络抖动
- Audio marker 交织同步

#### killAudio

```json
{ "type": "killAudio" }
```

#### mark / clearMarks

```json
{ "type": "mark", "data": { "name": "marker-name" } }
```
```json
{ "type": "clearMarks" }
```

## Architecture

```
                         FreeSWITCH
                    ┌─────────────────────┐
 SIP/RTP ◄────────►│  mod_sofia          │
                    │    │                │
                    │    ▼                │
                    │  media_bug          │
                    │  ┌──────────────┐   │
    ┌───────────────│  │ capture_cb   │   │───────────────┐
    │  READ frames  │  │              │   │ WRITE_REPLACE │
    │  (uplink)     │  │  fork_frame  │   │ dub_speech    │
    │               │  │      │       │   │ _frame        │
    │               │  └──────┼───────┘   │      ▲        │
    │               └────────┼────────────┘      │        │
    │                        │                   │        │
    │                        ▼                   │        │
    │              ┌─────────────────┐           │        │
    │              │   AudioPipe     │           │        │
    │              │   (LWS client)  │           │        │
    │              │                 │           │        │
    │              │  ┌───────────┐  │           │        │
    │              │  │ tx: audio │──┼─── ws ───►│ agent  │
    │              │  │    buffer │  │           │ -flow  │
    │              │  └───────────┘  │           │        │
    │              │  ┌───────────┐  │           │        │
    │              │  │ rx: text  │◄─┼─── ws ────│ TTS    │
    │              │  │ rx: binary│  │  PCM/JSON │ output │
    │              │  └─────┬─────┘  │           │        │
    │              └────────┼────────┘           │        │
    │                       │                    │        │
    │                       ▼                    │        │
    │              processIncomingBinary          │        │
    │              / processIncomingMessage        │        │
    │                       │                    │        │
    │                       ▼                    │        │
    │              ┌────────────────┐             │        │
    │              │  PreBuffer     │             │        │
    │              │  (jitter smoothing)          │        │
    │              └───────┬────────┘             │        │
    │                      │ speex resample       │        │
    │                      ▼                      │        │
    │              ┌────────────────┐             │        │
    │              │  PlayoutBuffer │─────────────┘        │
    │              │  (mutex-guarded)│                     │
    │              └────────────────┘                      │
    └──────────────────────────────────────────────────────┘
```

## Building

### macOS (Apple Silicon)

```bash
chmod +x build.sh
./build.sh
```

依赖：FreeSWITCH headers + library（路径见 `build.sh` 顶部配置），boost，libwebsockets，cmake。

### Ubuntu / Linux (Production)

```bash
chmod +x build-ubantu.sh
sudo ./build-ubantu.sh all        # deps + build + install
sudo ./build-ubantu.sh build      # build only
sudo ./build-ubantu.sh install    # install only
```

> **注意**: ARM64 bit-field 修复需同时同步到 `build-ubantu.sh` 对应的源码。

详细构建说明见 [BUILD.md](BUILD.md)。

## Usage Example

```bash
# 启动双向音频流（16kHz，binary 模式，用于 AI 语音对话）
fs_cli -x "uuid_audio_fork <uuid> start ws://127.0.0.1:8000/media/<uuid> mixed 16k mybug {} true true 16000"

# Barge-in: 停止 AI 播放
fs_cli -x "uuid_audio_fork <uuid> stop_play mybug"

# 发送 DTMF 事件
fs_cli -x "uuid_audio_fork <uuid> send_text mybug {\"event\":\"dtmf\",\"digit\":\"1\"}"

# 暂停 / 恢复上行
fs_cli -x "uuid_audio_fork <uuid> pause mybug"
fs_cli -x "uuid_audio_fork <uuid> resume mybug"

# 优雅停止（带最终消息）
fs_cli -x "uuid_audio_fork <uuid> stop mybug {\"reason\":\"complete\"}"
```

## Integration with Smart Outbound Call System

在本项目中，mod_audio_fork 的使用方式：

1. **来电触发**: FreeSWITCH 拨号计划 answer → park → 触发 `CHANNEL_ANSWER` 事件
2. **ESL 启动**: agent-flow 通过 ESL 调用 `uuid_audio_fork start` → FreeSWITCH 建立 WebSocket 连接到 `ws://agent-flow:8000/media/{uuid}`
3. **上行音频**: 通话音频通过 WebSocket 流式传输 → JitterBuffer → VAD → ASR → LLM
4. **下行音频**: TTS 合成的 PCM 音频通过 WebSocket binary 帧回传 → PreBuffer → PlayoutBuffer → FreeSWITCH 播放
5. **Barge-in**: 用户说话时 ESL 调用 `uuid_audio_fork stop_play` 停止 AI 播放，开启新一轮对话
6. **挂断清理**: `CHANNEL_HANGUP` → `uuid_audio_fork stop` → 清理资源

## License

See [LICENSE](LICENSE) for details.
