# WebRTC AEC + NS + AGC 架构改造设计

- **日期**: 2026-06-12
- **状态**: 设计已确认，待实现
- **影响组件**: `agent-flow`（`src/ws/`、`src/config.py`、`main.py`）
- **关联**: 替换现有 `denoise.py` 降噪链 + 固定 `AUDIO_GAIN`

---

## 1. 背景与目标

智能外呼系统在 AI 播放 TTS 时，电话线路会把 AI 自己的声音回采进麦克风（回声）。当前没有回声消除（AEC），导致两个症状：

1. **Barge-in 误触发**：VAD 把 AI 回声当成"用户说话"，误触打断（`handler.py:163-164` 注释明确写"barge-in 音频混入 AI 回声，ASR 无法识别"）。
2. **ASR 串话**：AI 的话被 ASR 识别成用户输入。

现状靠 `rms_threshold` + `vad_cooldown_after_bargein` + grace period 硬扛，治标不治本。

**目标**：引入 WebRTC APM（Acoustic Echo Cancellation + Noise Suppression + Automatic Gain Control），用「远端参考帧」抵消 near 端的 TTS 回声，从根上消除 barge-in 误触发与串话。同时用 WebRTC 的 NS/AGC 替换现有零散的降噪与固定增益。

---

## 2. 现状与问题

| 能力 | 当前实现 | 位置 | 问题 |
|------|---------|------|------|
| VAD | `webrtcvad`（仅 VAD） | `vad.py:63` | 被回声误导 → 误触发 |
| 降噪 (NS) | highpass / noisereduce / pyrnnoise | `denoise.py` | 零散多套，noisereduce 有 300ms 延迟 |
| 增益 (AGC) | 固定 `CALLBOT_AUDIO_GAIN` | `config.py` / `handler._apply_gain` | 非自适应 |
| 回声消除 (AEC) | **无** | — | 核心缺口 |

**两条独立音频循环、时钟域不同**（决定 reference 怎么接）：

- **near（麦克风回采）**：`websocket.receive()` → `JitterBuffer.drain()` 出 960B/30ms → `denoiser.process()` → VAD/ASR
- **reverse（TTS 远端参考）**：`audio_callback` → `TTSOutputBuffer.write()` → `_send_loop` 以 30ms 匀速 `popleft()` → `send_bytes` 发往 FreeSWITCH

两条路都是 16kHz/16-bit/单声道/960B，在同一 WS 连接上，物理上可汇合。

---

## 3. 库选型与依据

选用 **`python-webrtc-audio-processing`**（xiongyihui 维护，pip 包名 `webrtc_audio_processing`）。

以下 API 经源码 `src/audio_processing_module.cpp` 与 `setup.py` 确认（非 README 摘要，README 的 usage 示例仅展示了 `process_stream`，实际能力更全）：

### 构造

```cpp
AudioProcessingModule(int aec_type, bool enable_ns, int agc_type, bool enable_vad)
```

- `aec_type`:
  - `0` = 关闭 AEC
  - `1` = AECM（`echo_control_mobile()`，`kLoudSpeakerphone`，移动端轻量）
  - `2` = 老频域 AEC（`echo_cancellation()`，`kLowSuppression`）
  - `3` = AEC3（**代码被注释，不可用**）
- `agc_type`: `0`=关, `1`=AdaptiveDigital（target -30dBFS）, `2`=AdaptiveAnalog
- `enable_ns`: 开启噪声抑制
- `enable_vad`: 开启 APM 内置 VAD（**本设计不启用**，沿用现有 webrtcvad/silero）

### 关键方法

| 方法 | 作用 |
|------|------|
| `set_stream_format(rate, channels, out_rate, out_channels)` | 配置 near 端格式 |
| `set_reverse_stream_format(rate, channels)` | 配置 reverse 端格式 |
| `process_reverse_stream(farend)` | **喂远端参考帧**；内部调用 `ProcessReverseStream()` + `set_stream_delay_ms(system_delay)` |
| `process_stream(nearend) -> bytes` | 处理 near 端，返回 HPF+AEC+NS+AGC 后的音频 |
| `set_system_delay(int)` | 设置回声延迟先验（毫秒），供 `process_reverse_stream` 内部使用 |
| `has_echo() -> bool` | 查询当前帧是否有残留回声（监控用） |
| `set_ns_level(0-3)` / `set_aec_level(0-2)` | NS / AEC 抑制等级 |

### 调用顺序铁律（源码确认）

`process_reverse_stream` 内部调 `set_stream_delay_ms`，因此**每个 10ms 配对必须先 `process_reverse_stream(reverse)` 再 `process_stream(near)`**。颠倒顺序会导致 delay 与参考帧错配，AEC 失效。

### 帧格式约束

`process_stream` / `process_reverse_stream` **每次只处理 10ms**。16kHz/16-bit/单声道下 10ms = 160 samples = 320 bytes。当前管线是 30ms/960B，需拆成 3×320B 子帧逐个处理。

### 安装

- pip 包名 `webrtc_audio_processing`，版本 0.1.3
- import: `from webrtc_audio_processing import AudioProcessingModule as AP`
- 构建依赖：`swig` + C++ 工具链 + git submodule（自带 `webrtc-audio-processing` C++ 源码，autotools 构建）
- `setup.py` classifiers 仅声明 `POSIX :: Linux`，但 `process_arch` 含 `arm64`/`x86` 分支，macOS（含 Apple Silicon）理论上可编译，需实测
- **无预编译 wheel**，需 `pip install .` 从源码编译

---

## 4. 架构设计

### 4.1 数据流（现状 vs 目标）

```
【现状】
WS in → JitterBuffer → denoiser(NS) → VAD → ASR
                                      AUDIO_GAIN(固定, 仅终点 apply)
TTS: audio_callback → TTSOutputBuffer → WS out     ← 无 AEC 参考

【目标】
WS in → JitterBuffer → WebRTCAPM.process(near, reverse) → VAD → ASR
                              │  HPF + AEC + NS + AGC 一次过
                              └ reverse ← TTSOutputBuffer.recent_reverse（镜像最近播放帧）
TTS: audio_callback → TTSOutputBuffer ──┬→ WS out
                                        └→ 镜像最近播放帧 → WebRTCAPM（AEC 远端参考）
废弃: denoise.py 具体 denoiser、handler._apply_gain / AUDIO_GAIN
不动: VAD、JitterBuffer、barge-in 逻辑骨架
```

### 4.2 reference 接入（方案 A：镜像最近播放帧）

`TTSOutputBuffer._send_loop` 每次向 FreeSWITCH 发送一帧时，把该帧的引用拷贝到 `self.recent_reverse: bytes`（最近一帧的快照）。AI 沉默时 `_send_loop` 发送的是 `SILENCE_FRAME`（RMS=0），`recent_reverse` 自然为静音 → AEC 收敛为"无回声"状态。

handler 在 near 端处理点（`JitterBuffer.drain()` 之后）读取 `tts_buffer.recent_reverse`，配对喂入 APM。

**不做精确时间戳对齐**。靠 AEC 内部维护的远端参考历史 buffer + `set_system_delay` 先验吸收"最近帧 vs 真实回声时刻"之间的错位。这是 YAGNI 取舍：先简单跑通，效果不足再升级到方案 B（near/reverse 打 monotonic 时间戳精确对齐）。

> 注意：`recent_reverse` 是"最近发送帧"，与 near 端此刻采集到的回声之间存在一个传播延迟差（FreeSWITCH 编码 + 网络 + 解码）。这个差就是 `set_system_delay` 要补偿的对象。

### 4.3 新模块 `src/ws/audio_processing.py`

```python
class WebRTCAPM:
    """WebRTC AudioProcessingModule 封装 —— HPF + AEC + NS + AGC 一次过。

    near 端: 麦克风回采（含 TTS 回声 + 用户语音 + 噪声）
    reverse 端: 正在播放的 TTS 帧（AEC 远端参考）
    """
    # pipeline 30ms 帧 = 3 个 10ms 子帧
    _SUBFRAME_BYTES = 320  # 10ms @ 16kHz 16-bit mono

    def __init__(self, aec_type, ns_level, agc_type, system_delay_ms): ...

    def process(self, near_frame: bytes, reverse_frame: bytes | None) -> bytes:
        """处理一个 960B near 帧，返回去回声+降噪+增益后的 960B。

        步骤（顺序不可颠倒）:
        1. 若 reverse_frame 非空:
           - set_system_delay(system_delay_ms)
           - 拆 reverse 为 3×320B，逐个 process_reverse_stream（喂参考 + 设 delay）
        2. 拆 near 为 3×320B，逐个 process_stream，拼回 960B 返回
        """
```

工厂 `create_audio_processing(settings) -> WebRTCAPM | None`：`CALLBOT_AEC_ENABLED=true` 时创建，否则返回 `None`（走旧 denoise 路径）。

### 4.4 delay 策略

- 固定值起步：`CALLBOT_AEC_SYSTEM_DELAY_MS`（默认 80ms）
- 通过 `has_echo()` 在日志中监控残留回声比例，作为后续标定依据
- **本次不做自适应 delay 估计**（YAGNI）。跑通后若残留回声明显，再评估是否引入 delay 扫描或换 AEC3 库（届时另开 proposal）

### 4.5 替换范围与 handler 改动

| 模块 | 改动 |
|------|------|
| `src/ws/audio_processing.py` | **新增** `WebRTCAPM` + `create_audio_processing()` 工厂 |
| `src/ws/jitter_buffer.py` `TTSOutputBuffer` | `_send_loop` 发送每帧后更新 `self.recent_reverse`；初始化为 `SILENCE_FRAME` |
| `src/ws/handler.py` | `handle()` 创建 `apm`；正常接收路径与 `_receive_during_streaming`（barge-in）路径在 `jitter.drain()` 后插入 `apm.process(near, tts_buffer.recent_reverse)` 替代原 `denoiser.process()`；`_apply_gain` 在 AEC 开启时跳过（AGC 交 APM） |
| `src/ws/denoise.py` | AEC 开启时 `create_denoiser()` 返回 `PassThroughDenoiser`（NS 交 APM）；保留为 fallback |
| `src/config.py` | 新增 `CALLBOT_AEC_*` 配置项（见 4.6） |
| `requirements.txt` | 新增 `webrtc-audio-processing`（源码编译，标注 swig 依赖） |
| `main.py` | 构造 `StreamingCallHandler` 时注入 apm 工厂（或 settings 驱动） |

**两条 near 路径都过 APM**，尤其 barge-in 路径（`_receive_during_streaming`）——这正是 AEC 消回声、让 barge-in 不再被 AI 自己声音误触发的价值点。验证通过后可考虑放宽 `_BARGE_IN_RMS_THRESHOLD` 与 `vad_cooldown_after_bargein`（本次仅记录，不在范围内调参）。

### 4.6 配置项（`CALLBOT_` 前缀，pydantic-settings）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CALLBOT_AEC_ENABLED` | `false` | 总开关。关时走原 denoise + gain 路径 |
| `CALLBOT_AEC_TYPE` | `2` | AEC 类型：`1`=AECM, `2`=老 AEC（AEC3 不可用） |
| `CALLBOT_AEC_NS_LEVEL` | `2` | NS 抑制等级 0-3 |
| `CALLBOT_AEC_AGC_TYPE` | `1` | AGC 类型：`0`=关, `1`=AdaptiveDigital, `2`=AdaptiveAnalog |
| `CALLBOT_AEC_SYSTEM_DELAY_MS` | `80` | 回声延迟先验（毫秒） |

---

## 5. 错误处理与 fallback

- **库未安装 / import 失败**：`create_audio_processing()` 捕获 `ImportError`，记 warning，返回 `None` → handler 走原 denoise + gain 路径。系统不阻断启动。
- **`process` 抛异常**：`WebRTCAPM.process` 内部 try/except，失败时返回原始 `near_frame`（降级为无处理），记 error。单帧失败不影响通话。
- **reverse 帧缺失**（`recent_reverse` 为空或 None）：跳过 `process_reverse_stream`，仅做 `process_stream`（NS+AGC 仍生效，AEC 本帧不更新参考）。
- **AEC 效果不足**：通过 `has_echo()` 监控 + 主观听感判断。残留明显时，调整 `CALLBOT_AEC_SYSTEM_DELAY_MS`；仍不足则后续 proposal 评估 AEC3 库。

---

## 6. 测试策略

### 单元测试 `tests/ws/test_audio_processing.py`

1. **拆帧/拼帧正确性**：`process()` 输入 960B，输出 960B，长度不变（无论 reverse 是否提供）。
2. **调用顺序**：mock `AudioProcessingModule`，验证每对子帧先 `process_reverse_stream` 后 `process_stream`。
3. **system_delay 传递**：reverse 提供时 `set_system_delay` 被调用，值为配置值。
4. **reverse 缺失**：reverse=None 时不调 `process_reverse_stream`，仍调 `process_stream`。

### 集成测试（合成 PCM）

5. **AEC 有效性**：构造 `near = user_speech + echo(reverse 的衰减延迟拷贝)`，`reverse = tts_frame`，验证 `process()` 输出中 reverse 成分能量显著下降（对比输入输出在 reverse 频段的能量，或 `has_echo()` 由 True 转 False）。
6. **静音 reverse**：reverse 为静音帧时，near 中纯语音不被破坏（能量保留）。

测试不依赖真实电话链路，全部用合成 PCM + mock。

---

## 7. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| 老 AEC（非 AEC3）+ 手动 delay，延迟漂动时效果不稳 | 中 | 固定值起步 + `has_echo` 监控；不足再换 AEC3（另开 proposal） |
| macOS conda 编译失败（swig/autotools/submodule） | 中 | plan 第一步先验证安装；失败则 `CALLBOT_AEC_ENABLED=false` fallback，不阻塞开发 |
| `recent_reverse` 与真实回声时刻错位过大 | 中 | delay 先验 + AEC 内部 buffer 吸收；可调 `CALLBOT_AEC_SYSTEM_DELAY_MS` |
| 10ms 拆帧增加 CPU 开销 | 低 | 16kHz 3 子帧/30ms，计算量可忽略 |
| `process_reverse_stream` 与 `process_stream` 顺序误用 | 低 | 单元测试断言顺序；封装在 `WebRTCAPM.process` 内部，外部无法误调 |

---

## 8. 不在本次范围（out of scope）

- **不换 VAD**：保留 `webrtcvad` / `silero`。
- **不做 delay 自适应估计**：仅固定 `CALLBOT_AEC_SYSTEM_DELAY_MS`。
- **不做 reference 精确时间戳对齐**：仅方案 A（镜像最近帧）。
- **不引入 AEC3**：除非老 AEC 验证后效果不足，届时另开 proposal。
- **不调 barge-in 阈值**：AEC 上线后 `_BARGE_IN_RMS_THRESHOLD` / `vad_cooldown_after_bargein` 的放宽仅记录为后续调参项，本次不动。

---

## 9. 验收标准

1. `CALLBOT_AEC_ENABLED=true` 时，`WebRTCAPM` 正常加载，near 端音频经 AEC+NS+AGC 处理后送 VAD/ASR。
2. 单元测试全部通过（拆帧、顺序、delay、reverse 缺失）。
3. 集成测试：合成回声场景下，AEC 输出的 reverse 成分能量显著下降。
4. `CALLBOT_AEC_ENABLED=false` 时，系统行为与改造前完全一致（fallback 路径无回归）。
5. 库 import 失败时系统正常启动，记 warning，走 fallback。
