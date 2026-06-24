# Design: 录音下沉到 FreeSWITCH

## 背景

call-recording 现有实现（`src/ws/call_recorder.py`）在应用层累加 caller + AI PCM，挂断时合成立体声 wav。采用应用层的原始依据是注释认为「FS record_session 录不到 mod_audio_fork 注入的 TTS」。

## 根因分析（2026-06-24 已验证）

FreeSWITCH media_bug 按注册顺序串联执行。`record_session`（dialplan app）在 `answer` 后立即注册，必然早于 `uuid_audio_fork start`（CHANNEL_ANSWER 后 agent-flow 异步发起）。

mod_audio_fork 用 `SMBF_WRITE_REPLACE` flag（`mod_audio_fork.c:280`）注册 bug，在 write 帧即将送出网络前由 `dub_speech_frame`（`mod_audio_fork.c:51`）把 AI TTS PCM 替换掉 dialplan 的 silence 静音帧。

write 方向 media_bug 链：

```
原生帧(silence) → record bug[dub 前，录到静音] → audio_fork WRITE_REPLACE[dub 成 AI] → 网络
```

record bug 排在 WRITE_REPLACE **之前**，tap 到 dub 前的帧 → 录不到 AI。

**解法**：让 record bug 排在 audio_fork bug 之后注册。`uuid_record`（API）可在 answer 后任意时刻调用 —— 在 `audio_fork_start` 成功之后紧接着发起，record bug 即排在 WRITE_REPLACE 之后，tap 到 dub 后的 AI 帧。read（caller）方向 audio_fork 仅 fork 不替换，照常录到。

2026-06-24 实测：`audio_fork_start` 后 `uuid_record start`（RECORD_STEREO=true），`recordings/{uuid}.wav` 立体声右声道含 AI 语音。

## 关键决策

### D1: 录制触发点 — CHANNEL_ANSWER 的 audio_fork_start 之后
在 `_on_channel_answer` 的 `audio_fork_start` 成功分支内紧接着 `esl.record_start`。顺序由代码结构保证（同一 try 块，await 串行）。**不**在 dialplan 用 record_session（会抢在 audio_fork 前注册，失效）。

### D2: 双声道 — RECORD_STEREO=true
`record_start` 内先 `uuid_setvar RECORD_STEREO=true` 再 `uuid_record start`。FreeSWITCH record stereo：L=read（caller 上行）/ R=write（AI 下行），与原 CallRecorder 声道约定一致，console 播放与既有归档不受影响。

### D3: 停止 — CHANNEL_HANGUP 的 record_stop + FS 自动 flush
`_on_channel_hangup` 在 audio_fork_stop 后 `esl.record_stop`。channel 已释放时 stop 报 -ERR 属正常，不阻断；FS 挂断本会自动停止 record bug 并 flush wav。`_archive_recording` 的 3s delay（`recording_archive_delay_sec`）足以等 FS flush 完成。

### D4: 完全删除 CallRecorder，不做 app fallback
FS 单点录制。FS 录制失败为 non-fatal（仅日志，该通无录音）。理由：下沉初衷即简化；app fallback 双写违背初衷且引入两路写盘竞态；FS uuid_record 稳定（验证通过）。

### D5: _archive_recording 与 CALLBOT_RECORDINGS_DIR 不变
仍读 `CALLBOT_RECORDINGS_DIR/{uuid}.wav`（现由 FS 写入）→ upload_recording → insert_artifact。路径、上传、artifact 逻辑零改动，仅文件生产者从 CallRecorder 变 FS。

## 改动清单

| 文件 | 改动 |
|---|---|
| `agent-flow/src/clients/esl.py` | `record_start`/`record_stop` 去 `[验证补丁]` 标记转正式（验证补丁已引入） |
| `agent-flow/main.py::_on_channel_answer` | record_start 段去 `[验证补丁]` 标记转正式 |
| `agent-flow/main.py::_on_channel_hangup` | record_stop 段去 `[验证补丁]` 标记转正式 |
| `agent-flow/src/ws/call_recorder.py` | 删除整个文件 |
| `agent-flow/src/ws/handler.py` | 删 import / `feed_caller`/`feed_ai` 调用 / `recorder` 参数透传 / 已注释写盘段 |
| `freeswitch/dialplan/public/00_biz_type.xml` | 更新注释（FS 现由 uuid_record 录双声道） |
| `CLAUDE.md` | 「录音」段 CallRecorder → FS uuid_record；模块表移除 call_recorder.py |

## 风险与缓解

- **FS 挂断 flush 延迟**：`_archive_recording` 已有 3s delay 覆盖 FS flush；极端情况下 wav 未就绪则 archive 记 warning 跳过（已有逻辑），不阻断。
- **uuid_record 失败**：non-fatal，该通无录音但通话正常；error 日志可观测。
- **声道布局变化**：FS stereo 与原 CallRecorder 约定一致（L=caller/R=AI），console 既有播放与历史归档不受影响。
- **wrap_wav_header 残留**：`CallRecorder.finalize_stereo_wav` 用了 `minio_storage.wrap_wav_header`；删除前确认无其他引用，否则保留为公共工具。
