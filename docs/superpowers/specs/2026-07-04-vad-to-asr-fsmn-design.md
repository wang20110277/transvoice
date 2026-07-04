# VAD 迁移至 agent-asr(FSMN-VAD)+ agent-flow RMS 门禁

**日期**: 2026-07-04
**状态**: 设计已确认,待实现
**范围**: agent-flow(删 VAD + RMS 门禁)、agent-asr(FSMN-VAD 分段层)

---

## 1. 背景

### 1.1 现状

**agent-flow**(`src/ws/vad.py` + `src/ws/handler.py`)
- `BaseVAD` ABC + `WebRTCVAD`(含 RMS 门禁 + SNR 自适应底噪)+ `SileroVAD`,`create_vad()` 工厂
- VAD 三个用途:
  1. **门控 ASR 喂音频**(`handler.py:236-243`,`speech_detected=False` 时不喂 ASR)
  2. **端点检测**(`handler.py:246`,`is_end_of_speech` → 主动 `asr.finalize()` → 触发 LLM 轮次)
  3. **Barge-in**(`handler.py:415`,`RMS + vad.is_speech` ×5 → 清空 TTSOutputBuffer)
- 两套 RMS 阈值:`vad_rms_threshold`(vad.py 内)+ `barge_in_rms_threshold`(handler.py)
- 13 个 `CALLBOT_VAD_*` / `CALLBOT_BARGE_IN_*` 配置(`config.py:85-116`)
- `tests/ws/test_vad.py`:8 个 SNR 自适应用例

**agent-asr**
- 当前**零 VAD**;SenseVoice 整段喂模型、无分段(`engines/sensevoice/engine.py:62-66`)
- WS handler(`ws_server.py`):batch + streaming 双模,收 `{type:end}` 才整段 recognize
- gRPC(`grpc_server.py`):`Recognize` client-streaming,累积后批量返一次
- FSMN-VAD 模型**已下载本地** `models/speech_fsmn_vad_zh-cn-16k-common-pytorch/`,FunASR 已装(`funasr==1.2.3`),**零新依赖**
- 协议层标 8kHz vs 引擎层 16kHz 不一致,无 resample 代码(历史隐患)

### 1.2 动机

VAD 与 ASR 紧耦合(VAD 决定喂多少音频给 ASR、何时切段),VAD 结果天然属于 ASR 层。迁移到 agent-asr 后:
- agent-flow 瘦身(删 VAD 引擎依赖 webrtcvad/silero)
- ASR 分段质量提升(FSMN-VAD 神经网络端点检测,优于 WebRTC)
- agent-flow 仅保留轻量 RMS 门禁做低延迟 barge-in

---

## 2. 设计决策(已确认)

| # | 决策 | 选择 |
|---|------|------|
| 1 | 端点检测归属 | **ASR final 结果触发**(agent-asr FSMN-VAD 切段→识别→返 final;flow 收 final 触发 LLM) |
| 2 | flow RMS 门禁智能度 | **RMS + SNR 自适应底噪**(抽出现有逻辑做独立模块,8 测试用例可复用) |
| 3 | FSMN-VAD 接入层 | **服务端 VAD 分段层**(WS handler 加流式 VAD 中间层,引擎不变) |
| 4 | 主传输路径 | **WS**(全双工流式音频 + 实时 final);gRPC/HTTP 本期不改 |

**额外确认**:
- flow RMS 门禁**只用于 barge-in**,音频**全量喂 asr**(不门控 ASR,避免切断语音段)
- 配置 `vad_*` → `rms_gate_*` 改名 + .env 同步(见名知意)

---

## 3. 架构与数据流

### 3.1 职责划分

| 层 | 现状 | 新架构 |
|---|------|--------|
| agent-flow | VAD(精确分类)+ RMS + 端点 + barge-in | RMS 门禁(SNR 自适应)+ barge-in;端点由 ASR final 触发 |
| agent-asr | 无 VAD,SenseVoice 整段 | FSMN-VAD 流式分段层:端点检测→段识别→返 final |
| 传输 | HTTP 默认 | WS 默认 |

### 3.2 数据流

```
【agent-flow】
ws音频 → JitterBuffer → WebRTCAPM/Denoise → 全量喂 asr_ws_client
                                            ↓ RMS 门禁只用于 barge-in 监测
              RMS 连续N帧(AI说话时)→ barge-in(清 TTSOutputBuffer)  ← 本地低延迟
asr_ws_client ──收 final──→ _on_asr_final → 触发 LLM 轮次

【agent-asr WS handler】
ws收帧 → [resample→16k 若需] → FsmnVadSegmenter.feed(pcm)
                                   ↓ 返回已完成语音段
                              engine.recognize(segment_pcm)   ← SenseVoice batch
                                   ↓
                              ws.send({type:result, is_final:true})  ← 主动推,不等 end
```

---

## 4. agent-flow 改动

### 4.1 模块拆分

- **删** `src/ws/vad.py` 的 `BaseVAD`/`WebRTCVAD`/`SileroVAD`/`create_vad`/`SimpleVAD`
- **新建** `src/ws/rms_gate.py`,`RMSGate` 类(从 `WebRTCVAD._has_speech_energy` + `_update_noise_floor` 抽出):

```python
class RMSGate:
    def __init__(self, threshold, snr_factor, noise_floor_init, noise_adapt_rate): ...
    def is_speech(self, frame: bytes) -> bool: ...  # RMS + SNR 自适应
    def reset(self) -> None: ...                     # 保留底噪(test_reset_preserves_noise_floor 仍过)
```

- `tests/ws/test_vad.py` → `tests/ws/test_rms_gate.py`,8 用例机械改写(**非纯改名**):调用点 `is_end_of_speech(frame, 0)`→`is_speech(frame)`、`_has_speech_energy(x)`→`is_speech(x)`、删 fake webrtc `_vad.speech_result` setup(SNR 逻辑等价 —— RMSGate 无 WebRTC 共轭,`is_speech` 返回 False 即触发底噪 EMA 更新);`test_reset_preserves_noise_floor` 平移无改动

### 4.2 handler.py 改造点

| 现状 | 新架构 |
|------|--------|
| `vad.is_end_of_speech` → `asr.finalize()`(`handler.py:246-252`) | 删;端点改 ASR final 回调(4.3) |
| `RMS + vad.is_speech` ×5 barge-in(`handler.py:415`) | `rms_gate.is_speech` ×5(RMS+SNR,去 WebRTC 频谱确认 —— 见 §4.3 回退说明) |
| `vad.speech_detected` 门控 ASR(`handler.py:236`) | 删;音频全量喂 asr |
| `vad.reset()`(`handler.py:356`) | `rms_gate.reset()` |
| `vad_cooldown_until`(`handler.py:223`) | 保留(barge-in 冷却) |

> **⚠️ barge-in 精度回退(决策记录)**:现状 `handler.py:415` 是 `frame_rms > 阈值 AND vad.is_speech(...)`(WebRTC 频谱双重确认);新路径 `rms_gate.is_speech` 仅 RMS+SNR(能量域),去掉 WebRTC 频谱确认,AEC 残留/突发噪声下误打断概率可能上升。取舍已定(换取去 webrtcvad 依赖 + 更低延迟),缓解:`.env` 现有高阈值 `BARGE_IN_RMS_THRESHOLD=1500` 原样保留 + plan 含实测验证项。

### 4.3 端点触发:ASR final 回调链路(核心控制流改造)

**协议侧**:asr 服务端 FSMN-VAD 自检端点 → **主动推** `{type:result,is_final:true}`(不等 flow 发 end);单连接多次,每语音段一个。

**客户端链路**:`ASRWsStream`(receiver_loop)收 `result` → 调 `on_final(result)` → `AsrStreamingManager` 通透(见 §9 step 4)→ handler `_on_asr_final`。

**`_on_asr_final` 作为 `handle()` 内嵌协程**(闭包捕获 `streaming_task`/`turn_count`/`audio_buffer`,`nonlocal` 改写):

```python
async def _on_asr_final(result: dict):
    async with turn_lock:                       # 与 barge-in 取消路径互斥
        if streaming_task and not streaming_task.done():
            return                              # 轮次进行中(用户重叠 / 服务端残余段)→ 丢弃
        text = (result.get("text") or "").strip()
        if len(text) < 2:                       # 噪声/单字(原 handler.py:257-264 逻辑)
            return
        nonlocal streaming_task, turn_count
        turn_count += 1
        streaming_task = asyncio.create_task(
            self._process_streaming_turn(..., precomputed_asr_result=result, ...),
            name=f"stream-{call_id}-{turn_count}",
        )
        self._reset_audio_state(audio_buffer, rms_gate, jitter)  # 清下一轮缓冲
```

**主循环改造**(handle 正常接收分支):
- 删 `if not vad.speech_detected: ... continue` 门控(`handler.py:236-239`)—— 音频全量喂 asr(仅指用户轮次内不门控;AI 说话期主循环走 barge-in 分支,本就不喂 asr)
- 删 `if vad.is_end_of_speech(...):`(`handler.py:246-292`)—— 端点改由 `_on_asr_final` 触发
- 保留 `asr.feed(denoised_frame)` 全量转发
- **主循环如何感知新 streaming_task**:不需额外信号。`on_final` 在 ASR receiver task 直接 `create_task` 赋值 `streaming_task`;主循环当前阻塞在 `websocket.receive()`,下一帧(≤30ms)返回后回到 while 顶 `if streaming_task and not streaming_task.done():`(`handler.py:161`)自然进入 barge-in 分支。

**`turn_lock = asyncio.Lock()` 保护区间**(仅 `streaming_task` 的 check-and-set + `audio_buffer` reset,临界区内除 `create_task` 无 await):
- `_on_asr_final`:check 闲置 → launch → reset(上文)
- barge-in 取消路径(`handler.py:180-201`):`tts_buffer.clear()` → `asr.cancel()` + 发 `{type:reset}`(§6,丢服务端进行中段)→ `_reset_audio_state` → `streaming_task=None`,整段持锁
- 无嵌套加锁(`_process_streaming_turn` 本身不持 `turn_lock`)→ 无死锁

**轮次自然串行**:AI 说话期主循环走 barge-in 分支、**不喂 asr** → 服务端无新音频 → 不产新 final;新 final 仅在 barge-in 取消、回到正常分支后才可能出现。lock 内 `streaming_task.done()` 检查是残余段/竞态兜底。

> 注:`barge_in_event`/`barge_speech_counter`/`vad_cooldown_until` 等 barge 局部状态仅在主循环改写、不跨 task,不入锁。`precomputed_asr_result` 由回调 `result` 直接带入 `_process_streaming_turn`,不再经 `asr.finalize()` 返回值。

### 4.4 配置精简(`config.py` + `agent-flow/.env`)

> ⚠️ **.env 实际位置 `agent-flow/.env`**(非根目录)。当前 .env **未设**待改名的 `vad_rms_*`/`vad_snr_*`/`vad_noise_*`/`vad_cooldown_*`(走代码默认),只设了待删的 3 项 + 已调优的 barge_in 2 项。故 .env 工作量:**删 3 行**(`VAD_AGGRESSIVENESS`/`VAD_SILENCE_FRAMES`/`VAD_MIN_AUDIO_BYTES`)、**保留 barge_in 调优值**(`BARGE_IN_MIN_AUDIO_BYTES=3200`、`BARGE_IN_RMS_THRESHOLD=1500` —— 切勿重置回代码默认 1600/300)、`ASR_USE_WS=true` 已是目标无需动;新 `rms_gate_*`/`cooldown_after_bargein` 按需新增调优。

- **删**(WebRTC/Silero/终点累积归 asr):`vad_type`、`vad_aggressiveness`、`vad_silence_frames`、`vad_silero_threshold`、`vad_silero_min_silence_ms`、`vad_min_audio_bytes`
- **改名**(`vad_`→`rms_gate_`,改 config.py 字段名;rename 后的 key 按需写入 .env 调优,当前 .env 无这些 key):
  - `vad_rms_threshold`→`rms_gate_threshold`
  - `vad_snr_factor`→`rms_gate_snr_factor`
  - `vad_noise_floor_init`→`rms_gate_noise_floor_init`
  - `vad_noise_adapt_rate`→`rms_gate_noise_adapt_rate`
  - `vad_cooldown_after_bargein`→`cooldown_after_bargein`
- **保留**:`barge_in_min_audio_bytes`、`barge_in_rms_threshold`(.env 现有调优值原样保留)
- **新默认**:`asr_use_ws = True`(代码默认 false→true;.env 已 true)

### 4.5 main.py

删 `create_vad` import + 工厂注入;`RMSGate` 在 handler 构造(或工厂注入,保持可测)。

---

## 5. agent-asr 改动

### 5.1 FSMN-VAD 分段器 `asradapter/vad_segmenter.py`

```python
class FsmnVadSegmenter:
    def __init__(self, model_dir: str): ...       # AutoModel(model=model_dir, disable_update=True), _buf, _cache, _consumed_ms
    def feed(self, pcm: bytes) -> list[bytes]:    # 累积→generate(is_final=False)→切已完成段
    def force_flush(self) -> list[bytes]:         # generate(is_final=True) 冲刷尾部
    def reset(self) -> None: ...
```

- ms→字节换算:16k×16bit = 32 字节/ms
- 实现时按 FunASR 实际返回结构验证(`value` key、`[[start_ms, end_ms], ...]`)
- 流式 chunk_size 沿用 FunASR 约定(默认 600ms,实现时验证)

### 5.2 WS handler 改造(`ws_server.py`)

```
每收 binary 帧:
  pcm16k = resample_if_needed(chunk, declared_sr)     # 5.3
  for segment_pcm in segmenter.feed(pcm16k):
      result = await engine.recognize(segment_pcm)    # SenseVoice batch(段级)
      await ws.send({type:result, is_final:true})     # 主动推,不等 end
收 {type:end}:    segmenter.force_flush() → 同上(兼容 batch / 兜底冲刷)
收 {type:reset}:  segmenter.reset()                   # barge-in 时 flow 主动丢进行中段
```

- **删现有 `streaming=True` + `stream_ctx` 分支**(`ws_server.py:57-61`、`84-92`):engine 级 streaming(`start_stream`/`get_partial`)对 SenseVoice 本就是死代码(未覆写 `supports_streaming`,仅 `engines/streaming/` 覆写),新 segmenter 层取代之,不再产 `partial`。
- `config.streaming` 字段保留作协议开关但语义弱化(segmenter 主路径与该标志无关)。

### 5.3 采样率处理(修历史不一致)

- WS config 消息加 `sample_rate`(默认 16000)
- 服务端 `sample_rate != 16000` → resample(`librosa`/`scipy.signal.resample_poly`)→ 喂 VAD
- 修正协议层注释 8k→16k(与引擎/VAD/`CALLBOT_MEDIA_SAMPLE_RATE=16000` 全链路一致)
- agent-flow 全链路本就是 16k,实际不触发 resample;字段作防御 + 修历史标注

### 5.4 引擎零改动

`SenseVoiceASREngine.recognize` 不变(整段 batch recognize)。唯一变化:**调用粒度从"整段通话音频"→"VAD 切出的单语音段"**,由 5.2 handler 驱动。

### 5.5 本期范围(YAGNI)

- ✅ **WS**:VAD 分段层(主传输)
- ⏸ **gRPC**:本期**不改**(保留累积批量、无 VAD);后续按需补 VAD 分段 + server-streaming
- ⏸ **HTTP batch**(WS `streaming=false`):保留,`force_flush` 兜底

---

## 6. WS 协议规范

**客户端 → 服务端**

| 消息 | 字段 | 说明 |
|------|------|------|
| Text `config` | `call_id, language, sample_rate` | 新增 `sample_rate`(默认 16000);`streaming` 字段保留弱化(下文) |
| Binary | PCM 16k/16bit mono 帧 | 全量喂(4.2 决策) |
| Text `end` | — | 兼容 batch / VAD 降级兜底冲刷 |
| Text `reset` | — | **新增**:barge-in 时 flow 主动丢服务端进行中段(`segmenter.reset()`) |

**服务端 → 客户端**

| 消息 | 字段 | 触发 |
|------|------|------|
| `result` | `text, confidence, is_final:true` | **VAD 切段识别完主动推,单连接可多次**(每语音段一个) |
| `error` | `message` | 异常 |

**核心变化**:
- `result` 单连接**多次返回**(原仅一次)。flow `on_final` 每次触发一轮。
- **`partial` 退役**:旧 engine 级 streaming(`start_stream`/`get_partial`,SenseVoice 不支持)本就不产 partial;新 segmenter 走 batch `engine.recognize` 段级,仅发 `result`。`ASRWsStream.on_partial` 回调保留但 SenseVoice 下恒不触发。

---

## 7. 错误处理与降级

| 场景 | 处理 |
|------|------|
| **FSMN-VAD 推理异常** | segmenter `feed` 内捕获 → 标记 degraded → **回退"收 end 整段 recognize"**(原 batch 路径),保可用 |
| asr WS 连接失败/中途断开 | asr_ws_client 通知 flow;当前轮 abort、重连;segmenter 在新连接 reset |
| 用户长沉默 | RMS 全静音→无音频发 asr→无 final→无轮次(正常);FSMN-VAD `max_single_segment_time=60s` 强切段兜底 |
| barge-in 残余 | flow `asr.cancel()`(现状保留),segmenter 随连接销毁丢弃 |
| asr 长时间无 final | flow 侧超时兜底(N 秒无 final → 主动 `end` 冲刷 + log 告警) |

**降级原则**:VAD 失败不影响 ASR 可用性 —— 回退整段识别,通话不中断。

---

## 8. 测试策略

**agent-flow**
- `tests/ws/test_rms_gate.py`:8 个 SNR 用例从 `test_vad.py` 平移(逻辑不变,类/方法名改)+ `reset` 保留底噪
- `tests/ws/test_handler.py`(新增):`_on_asr_final` 回调触发轮次、`asyncio.Lock` 并发安全、barge-in 纯 RMS ×5
- 删 `tests/ws/test_vad.py`

**agent-asr**
- `tests/engines/test_vad_segmenter.py`(新增):`feed` 累积切段、`force_flush` 尾部、`reset`、ms→字节换算、`sample_rate` resample;**mock `AutoModel.generate`** 返回固定段,不依赖真实模型权重

**集成(本期可选)**
- flow ↔ asr WS 端到端:喂"语音+静音+语音"PCM → 收多个 final → 触发多轮

---

## 9. 实现顺序

1. agent-asr:`vad_segmenter.py` + 单测(mock 模型)
2. agent-asr:`ws_server.py` 接入 segmenter + 采样率 + 多 final + 降级
3. agent-flow:`rms_gate.py` + 平移测试
4. agent-flow:`asr_ws_client` 加 `on_final`(receiver_loop 收 `result` 即触发)+ `AsrStreamingManager` 通透 `on_final`(`create_stream(..., on_final=...)`、`feed` 全量转发)、`finalize` 降级为兜底(发 `end` 等单 result);新增 `{type:reset}` 客户端发送(barge-in 丢服务端段)
5. agent-flow:`handler.py`(端点回调 + barge-in 纯 RMS + 并发锁)
6. agent-flow:`config.py` + `.env` 配置精简/改名
7. 端到端联调

---

## 10. 配置变化清单

| 配置(env:`CALLBOT_*`) | 变化 | 默认 |
|--------------------------|------|------|
| `vad_type` | 删 | — |
| `vad_aggressiveness` | 删 | — |
| `vad_silence_frames` | 删 | — |
| `vad_silero_threshold` | 删 | — |
| `vad_silero_min_silence_ms` | 删 | — |
| `vad_min_audio_bytes` | 删 | — |
| `vad_rms_threshold` | 改名 → `rms_gate_threshold` | 300.0 |
| `vad_snr_factor` | 改名 → `rms_gate_snr_factor` | 3.0 |
| `vad_noise_floor_init` | 改名 → `rms_gate_noise_floor_init` | 300.0 |
| `vad_noise_adapt_rate` | 改名 → `rms_gate_noise_adapt_rate` | 0.1 |
| `vad_cooldown_after_bargein` | 改名 → `cooldown_after_bargein` | 0.5 |
| `barge_in_min_audio_bytes` | 保留 | 1600(代码默认) |
| `barge_in_rms_threshold` | 保留 | 300(代码默认) |
| `asr_use_ws` | 默认改 true | true |

> **.env 调优值(改名/精简时原样保留,勿重置)**:`agent-flow/.env` 实际 `BARGE_IN_MIN_AUDIO_BYTES=3200`、`BARGE_IN_RMS_THRESHOLD=1500`(非代码默认);待删 3 项(`VAD_AGGRESSIVENESS`/`VAD_SILENCE_FRAMES`/`VAD_MIN_AUDIO_BYTES`)从 .env 移除;`ASR_USE_WS=true` 已就位;待改名 `vad_rms_*` 等 .env 当前未设,按需新增 `RMS_GATE_*`。
