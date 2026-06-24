# Proposal: 录音下沉到 FreeSWITCH

## Why

当前通话双声道录音由 agent-flow 应用层 `CallRecorder`（`src/ws/call_recorder.py`）完成：WebSocket handler 内累加 caller 上行 PCM（mod_audio_fork 转发）+ AI 下行 TTS PCM（自生成），挂断时合成立体声 wav 写入 `recordings/{uuid}.wav`。带来两个问题：

- 每通约 **4MB/分钟** 的内存累加开销；
- 录音逻辑分散在应用层，与 FreeSWITCH 原生录音能力重复。

采用应用层自录的原始依据是 `call_recorder.py:3-6` 注释认为「FS record_session 录不到 mod_audio_fork 注入的 TTS」。

**验证（2026-06-24）推翻了该结论。** 根因是 FreeSWITCH media_bug 按注册顺序串联执行：dialplan 的 `record_session` 排在 mod_audio_fork 的 `SMBF_WRITE_REPLACE` bug **之前**，tap 到的是 `dub_speech_frame`（AI 音频注入）之前的静音帧。只要让 record bug 排在 audio_fork bug 之后注册即可。实测在 `audio_fork_start` 成功后紧接着 `uuid_record start`（设 `RECORD_STEREO=true`），录到的 `recordings/{uuid}.wav` 为立体声，**右声道（R=AI 下行）有 AI 语音**，透传成立。

## What Changes

将整通双声道录音从应用层下沉到 FreeSWITCH：

1. **`main.py` `_on_channel_answer`**：`audio_fork_start` 成功后，紧接着 `esl.record_start(uuid, recordings/{uuid}.wav)`（内部先 `uuid_setvar RECORD_STEREO=true` 再 `uuid_record start`）。顺序关键 —— record bug 必须排在 audio_fork 的 WRITE_REPLACE bug 之后。
2. **`main.py` `_on_channel_hangup`**：`audio_fork_stop` 后 `esl.record_stop(uuid)` 触发 flush（channel 已释放则失败忽略，FS 挂断本会自动落盘）。
3. **`esl.py`**：保留验证补丁引入的 `record_start` / `record_stop` 方法，去掉 `[验证补丁]` 标记转正式。
4. **删除 `CallRecorder`**：`src/ws/call_recorder.py` 整文件删除；`handler.py` 移除 import、`feed_caller`/`feed_ai` 调用、`recorder` 参数透传链路、已注释的写盘段。
5. **`_archive_recording`**：不变 —— 仍读 `recordings/{uuid}.wav`（现由 FS 写入）→ MinIO → `insert_artifact(kind='recording')`。
6. **`freeswitch/dialplan/public/00_biz_type.xml`**：更新注释（FS 现已通过 `uuid_record` 录双声道，不再是「FS 录不到 AI」）。
7. **`CLAUDE.md`**：架构描述「录音」段从 `CallRecorder` 改为 FS `uuid_record`。

## 成功标准

- [ ] FS 录的 `recordings/{uuid}.wav` 为立体声（2 channels），L=caller 上行、R=AI 下行，两声道均有正确音频。
- [ ] `CallRecorder` 完全删除，代码中无残留引用（import / 调用 / 参数）。
- [ ] `_archive_recording` 正常读取 FS 写入的 wav，上传 MinIO + `insert_artifact(kind='recording')` 成功。
- [ ] Console 通话详情可正常播放录音（声道布局与既有归档一致 L=caller / R=AI）。
- [ ] dialplan 与 CLAUDE.md 注释 / 描述与新实现一致。
- [ ] 流式通话路径（WS → JitterBuffer → APM → VAD → ASR → LLM → TTS）功能不受影响。

## 边界（不在范围内）

- 仅覆盖 inbound 呼入（已验证）。outbound `call_task` 当前为定义层（无 originate / 调度引擎），暂不涉及。
- 不改录音归档 / 上传 / MinIO / `insert_artifact` 逻辑。
- 不改 Console 录音播放（保持声道布局约定）。
- 不引入 app 层 fallback —— 完全删除 `CallRecorder`，FS 录制失败为 non-fatal（仅记日志，该通无录音）。

## 约束

- `uuid_record` 必须在 `audio_fork_start` 之后发起（media_bug 注册顺序）。
- `RECORD_STEREO=true` 保证双声道落盘。
- 依赖 FS CHANNEL_HANGUP 自动 flush；`record_stop` 在 channel 释放后失败属正常。
- 声道布局：FreeSWITCH record stereo 默认 L=read（caller 上行）/ R=write（AI 下行），与原 `CallRecorder` 约定一致。

## 验证依据

2026-06-24 验证补丁实测：`audio_fork_start` 后 `uuid_record start`（RECORD_STEREO=true），挂断 `uuid_record stop`，`recordings/{uuid}.wav` 立体声右声道含 AI 语音。补丁代码当前已在 working tree（带 `[验证补丁]` 标记），本变更高将其转正式并清理 `CallRecorder`。
