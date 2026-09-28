## WebSocket 整段识别协议（/ws/asr/streaming-recognize）

无状态服务：切段职责在客户端（agent-flow silero VAD + StreamAdapter），
服务端只做「config → binary 累积（逐帧重采样到 16kHz）→ end → 整段识别 →
单 result → 关连接」。

- 发送: `{"type":"config","call_id":...,"language":"zh","sample_rate":16000}`
  → 若干 binary PCM 帧 → `{"type":"end"}`
- 接收: `{"type":"result","text":...,"confidence":...,"is_final":true}`（单条）
  或 `{"type":"error","message":...}`
- 无 VAD 模型、无 reset 消息、无服务端语音状态机

## 模型下载
modelscope download --model iic/SenseVoiceSmall --local_dir ./models/SenseVoiceSmall
