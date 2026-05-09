# Orchestrator 事件处理规范

## 目录
1. [事件订阅配置](#1-事件订阅配置)
2. [CHANNEL_CREATE 处理](#2-channel_create-处理)
3. [CHANNEL_ANSWER 处理](#3-channel_answer-处理)
4. [DETECTED_SPEECH 处理](#4-detected_speech-处理)
5. [PLAYBACK_* 处理](#5-playback_-处理)
6. [静默检测与超时](#6-静默检测与超时)
7. [CHANNEL_HANGUP 处理](#7-channel_hangup-处理)
8. [转人工逻辑](#8-转人工逻辑)
9. [错误处理与告警](#9-错误处理与告警)

---

## 1. 事件订阅配置

### 订阅的事件列表
```python
ESL_EVENTS = [
    "CHANNEL_CREATE",         # 新通话创建
    "CHANNEL_ANSWER",         # 接通
    "CHANNEL_HANGUP",         # 挂断信号
    "CHANNEL_HANGUP_COMPLETE", # 挂断完成
    "DETECTED_SPEECH",        # ASR 识别结果
    "PLAYBACK_START",         # TTS 开始播放
    "PLAYBACK_STOP",          # TTS 播放结束
    "RECORD_START",           # 录音开始
    "RECORD_STOP",            # 录音结束
    "RECORD_COMPLETE",        # 录音完成
    "DTMF",                   # 按键
    "CALL_UPDATE",            # 通话状态更新
]
```

### 事件格式
- 推荐使用 JSON 格式，便于解析
- 必带字段：`Unique-ID`, `Call-Direction`, `Channel-Name`

---

## 2. CHANNEL_CREATE 处理

### 触发时机
- 通话创建时（主叫拨打或网关接入）

### 处理流程
```python
def handle_channel_create(event: dict):
    fs_uuid = event["Unique-ID"]
    call_direction = event.get("Call-Direction", "")

    # 1. 初始化会话状态
    call_state[fs_uuid] = {
        "fs_uuid": fs_uuid,
        "call_direction": call_direction,
        "status": "created",
        "biz_type": None,
        "task_id": None,
        "user_key": None,
        "recording_notice_played": False,
        "identity_verified": False,
        "silence_count": 0,
        "turn_count": 0,
        "start_time": time.time(),
    }

    # 2. 记录日志
    log.info(f"[{fs_uuid}] CHANNEL_CREATE: {call_direction}")
```

### 关键变量
- `Unique-ID`: FreeSWITCH 会话唯一标识（fs_uuid）
- `Call-Direction`: `inbound` / `outbound`

---

## 3. CHANNEL_ANSWER 处理

### 触发时机
- 通话被接听（用户摘机）

### 处理流程
```python
def handle_channel_answer(event: dict):
    fs_uuid = event["Unique-ID"]
    state = call_state.get(fs_uuid)

    if not state:
        log.error(f"[{fs_uuid}] CHANNEL_ANSWER: state not found")
        return

    state["status"] = "answered"
    state["answer_time"] = time.time()

    # ============ 步骤 1: 设置业务变量 ============
    # 从 SIP Header 或变量获取 biz_type, task_id 等
    biz_type = event.get("biz_type", "marketing")
    task_id = event.get("task_id", "")
    core_user_id = event.get("core_user_id", "")
    phone_hash = event.get("phone_hash", "")

    # 生成 user_key
    user_key = f"{core_user_id}:{phone_hash}"

    state.update({
        "biz_type": biz_type,
        "task_id": task_id,
        "core_user_id": core_user_id,
        "phone_hash": phone_hash,
        "user_key": user_key,
    })

    # ============ 步骤 2: 播放固定录音告知 ============
    # 强制播放，失败告警
    try:
        playback_result = play_legal_notice(fs_uuid)
        if not playback_result:
            log.error(f"[{fs_uuid}] 录音告知播放失败，标记为异常")
            state["recording_notice_played"] = False
            trigger_alert("LEGAL_NOTICE_FAILED", fs_uuid)
            # 仍需继续流程，但标记状态
        else:
            state["recording_notice_played"] = True
            log.info(f"[{fs_uuid}] 录音告知播放成功")
    except Exception as e:
        log.exception(f"[{fs_uuid}] 录音告知异常: {e}")
        state["recording_notice_played"] = False

    # ============ 步骤 3: 启动录音 ============
    try:
        rec_path = start_recording(fs_uuid, state)
        state["recording_path"] = rec_path
        log.info(f"[{fs_uuid}] 录音启动: {rec_path}")
    except Exception as e:
        log.exception(f"[{fs_uuid}] 录音启动失败: {e}")
        trigger_alert("RECORD_START_FAILED", fs_uuid)

    # ============ 步骤 4: RocketMQ 请求身份包 ============
    # 异步请求，不阻塞后续流程
    try:
        request_identity(fs_uuid, biz_type, user_key)
    except Exception as e:
        log.warning(f"[{fs_uuid}] 身份请求异常: {e}")

    # ============ 步骤 5: 启动 detect_speech ============
    # 等待 1-2 秒后启动（让 TTS 播放完毕）
    time.sleep(1.5)
    try:
        asr_profile = get_asr_profile(biz_type)
        start_detect_speech(fs_uuid, asr_profile)
        state["status"] = "listening"
        log.info(f"[{fs_uuid}] detect_speech 已启动")
    except Exception as e:
        log.exception(f"[{fs_uuid}] detect_speech 启动失败: {e}")
        trigger_alert("DETECT_SPEECH_START_FAILED", fs_uuid)

    # ============ 步骤 6: 写入 PG 会话表 ============
    insert_call_session(state)
```

### 关键函数
```python
def play_legal_notice(uuid: str, file: str = None) -> bool:
    """播放固定录音告知"""
    # file 默认从 vars.xml 的 legal_notice_file 获取
    result = fs.api("uuid_playback", f"{uuid} {file}")
    return result == "+OK"

def start_recording(uuid: str, state: dict) -> str:
    """启动分轨录音"""
    # 路径: /nas/rec/{biz_type}/YYYY/MM/DD/{call_id}/
    biz_type = state["biz_type"]
    date_str = datetime.now().strftime("%Y/%m/%d")
    rec_dir = f"/nas/rec/{biz_type}/{date_str}/{uuid}"
    os.makedirs(rec_dir, exist_ok=True)

    # 双声道录音（主叫 / 被叫）
    fs.api("uuid_record", f"{uuid} start {rec_dir}/caller.wav 48000 16")
    fs.api("uuid_record", f"{uuid} start {rec_dir}/bot.wav 48000 16")

    return rec_dir

def start_detect_speech(uuid: str, profile: str):
    """启动语音识别"""
    # grammar 可用 builtin 或自定义
    fs.api("uuid_detect_speech", f"{uuid} unimrcp://{profile} grammar:builtin:grammar:digits")
```

---

## 4. DETECTED_SPEECH 处理

### 触发时机
- ASR 识别到用户语音（实时回调）

### 处理流程
```python
def handle_detected_speech(event: dict):
    fs_uuid = event["Unique-ID"]
    state = call_state.get(fs_uuid)

    if not state or state.get("status") != "listening":
        return

    # ============ 步骤 1: 提取识别结果 ============
    speech_text = event.get("speech", "")
    asr_confidence = float(event.get("confidence", "0.0"))

    if not speech_text:
        log.debug(f"[{fs_uuid}] DETECTED_SPEECH: empty text")
        return

    log.info(f"[{fs_uuid}] 用户发言: {speech_text[:50]}... (conf={asr_confidence})")

    # 重置静默计数
    state["silence_count"] = 0
    state["turn_count"] += 1

    # ============ 步骤 2: 写入 Redis 滑窗 ============
    add_to_turn_window(fs_uuid, {
        "role": "user",
        "text": speech_text,
        "asr_conf": asr_confidence,
        "ts": time.time(),
        "turn_id": state["turn_count"],
    })

    # ============ 步骤 3: 记忆召回 ============
    # 3.1 Redis 热记忆
    redis_facts = get_redis_hot_facts(state["biz_type"], state["user_key"])

    # 3.2 PG facts（最近 90 天）
    pg_facts = get_pg_facts(state["biz_type"], state["user_key"], days=90, top_k=5)

    # 3.3 pgvector 相似召回（最近 180 天）
    vector_recall = get_vector_recall(
        state["biz_type"], state["user_key"],
        query=speech_text, top_k=3, days=180
    )

    # 组装 Memory Block
    memory_block = assemble_memory_block(redis_facts, pg_facts, vector_recall)

    # ============ 步骤 4: LLM 决策 ============
    try:
        # 构造 Prompt（业务 + 记忆 + 当前输入）
        prompt = build_prompt(
            biz_type=state["biz_type"],
            task_id=state["task_id"],
            user_key=state["user_key"],
            memory=memory_block,
            user_input=speech_text,
        )

        # 调用 Qwen3.6
        llm_response = qwen36.invoke(prompt)
        action = parse_llm_response(llm_response)

    except Exception as e:
        log.exception(f"[{fs_uuid}] LLM 调用失败: {e}")
        action = {"type": "say", "text": "抱歉，请您稍后再说一遍好吗？"}
        trigger_alert("LLM_FAILED", fs_uuid)

    # ============ 步骤 5: 二次合规校验 ============
    # 催收敏感字段仅核验后允许
    if state["biz_type"] == "collection" and not state.get("identity_verified"):
        if contains_sensitive_fields(action.get("text", "")):
            log.warning(f"[{fs_uuid}] 尝试播敏感字段但未核验，替换为脱敏版本")
            action["text"] = sanitize_sensitive_text(action["text"])
            trigger_alert("SENSITIVE_FIELD_BLOCKED", fs_uuid)

    # ============ 步骤 6: 执行动作 ============
    action_type = action.get("type")

    if action_type in ("say", "ask"):
        # 停止 detect_speech（避免回声触发）
        stop_detect_speech(fs_uuid)

        # TTS 播报（或命中缓存）
        tts_profile = get_tts_profile(state["biz_type"])
        tts_result = tts_speak(fs_uuid, tts_profile, action["text"])

        # 记录 bot 回复
        add_to_turn_window(fs_uuid, {
            "role": "assistant",
            "text": action["text"],
            "intent": action.get("intent", ""),
            "ts": time.time(),
        })

        # 写入 PG turn 表
        insert_turn(fs_uuid, "assistant", action["text"], intent=action.get("intent"))

        # 恢复 detect_speech
        time.sleep(0.5)  # 等待 TTS 播放结束
        start_detect_speech(fs_uuid, get_asr_profile(state["biz_type"]))

    elif action_type == "handoff":
        # 转人工
        log.info(f"[{fs_uuid}] 转接人工: loopback/1001")
        transfer_to_agent(fs_uuid, "1001")

    elif action_type == "end":
        # 结束通话
        log.info(f"[{fs_uuid}] 通话结束: {action.get('reason', 'unknown')}")
        terminate_call(fs_uuid, action.get("reason", ""))

    # ============ 步骤 7: 更新会话状态 ============
    state["last_action"] = action
    update_call_state(fs_uuid, state)
```

### Memory Block 组装示例
```text
## 用户记忆
- 偏好联系时间: 周末上午（Redis 缓存）
- 上次意图: 咨询产品功能（2025-12-01）
- 历史异议: 价格偏高（向量召回相似案例）
- 身份核验: 已通过（姓名: 张*，身份证后四位: 1234）

## 当前对话
用户: 我想了解一下你们的产品
```

---

## 5. PLAYBACK_* 处理

### 5.1 PLAYBACK_START
- TTS 开始播放时触发
- 记录日志，用于监控延迟
- 可关联 turn_id 追踪

### 5.2 PLAYBACK_STOP
- TTS 播放结束时触发
- 用于判断是否可以恢复 detect_speech

```python
def handle_playback_stop(event: dict):
    fs_uuid = event["Unique-ID"]
    state = call_state.get(fs_uuid)

    if not state:
        return

    log.debug(f"[{fs_uuid}] PLAYBACK_STOP")

    # 如果处于 TTS 播报状态，恢复 detect_speech
    if state.get("status") == "speaking":
        state["status"] = "listening"
        time.sleep(0.3)
        try:
            start_detect_speech(fs_uuid, get_asr_profile(state["biz_type"]))
        except Exception as e:
            log.exception(f"恢复 detect_speech 失败: {e}")
```

---

## 6. 静默检测与超时

### 6.1 静默检测机制
```python
# Orchestrator 定时任务（每 3 秒检查一次）
def check_silence():
    for fs_uuid, state in call_state.items():
        if state.get("status") != "listening":
            continue

        # 检测是否超过静默阈值
        # 方案 A: 通过 DETECTED_SPEECH 事件间隔判断
        # 方案 B: 通过 FreeSWITCH 的 no-answer-timeout

        # 这里使用方案 A：每次收到 DETECTED_SPEECH 时重置 silence_count
        # 如果长时间未收到，则累加

        # 或者在每轮结束后启动一个计时器，超过 N 秒未收到用户输入则触发

# 更简单的方案：利用 detect_speech 的 recognition-timeout
# UniMRCP 配置的 recognition-timeout=5000ms
# 如果用户 5 秒未说话，会触发一个"空"的 DETECTED_SPEECH 或超时事件
```

### 6.2 推荐的静默处理方案
```python
def handle_silence_timeout(fs_uuid: str):
    """静默超时处理"""
    state = call_state.get(fs_uuid)
    if not state:
        return

    state["silence_count"] += 1

    if state["silence_count"] == 1:
        # 首次静默，提示用户
        prompt_text = get_biz_prompt(state["biz_type"], "silence_prompt_1")
        tts_speak(fs_uuid, get_tts_profile(state["biz_type"]), prompt_text)

    elif state["silence_count"] == 2:
        # 第二次静默，再次提示
        prompt_text = get_biz_prompt(state["biz_type"], "silence_prompt_2")
        tts_speak(fs_uuid, get_tts_profile(state["biz_type"]), prompt_text)

    else:
        # 第三次静默，结束通话或转人工
        log.info(f"[{fs_uuid}] 多次静默，转接人工")
        transfer_to_agent(fs_uuid, "1001")
```

### 6.3 配置建议
- `detection_speech` 的 `recognition-timeout` 设置为 5 秒
- 配合 Orchestrator 计时器兜底（双重保障）
- 静默次数阈值可通过业务配置调整

---

## 7. CHANNEL_HANGUP 处理

### 触发时机
- 通话挂断信号（主叫挂断、被叫挂断、异常断开）

### 处理流程
```python
def handle_channel_hangup(event: dict):
    fs_uuid = event["Unique-ID"]
    state = call_state.pop(fs_uuid, None)  # 移除状态

    if not state:
        log.warning(f"[{fs_uuid}] CHANNEL_HANGUP: state not found")
        return

    hangup_cause = event.get("Hangup-Cause", "")
    duration = time.time() - state.get("start_time", time.time())

    log.info(f"[{fs_uuid}] 通话结束: cause={hangup_cause}, duration={duration:.1f}s")

    # ============ 步骤 1: 停止 detect_speech ============
    try:
        stop_detect_speech(fs_uuid)
    except Exception as e:
        log.warning(f"停止 detect_speech 失败: {e}")

    # ============ 步骤 2: 停止录音 ============
    try:
        fs.api("uuid_record", f"{fs_uuid} stop")
    except Exception as e:
        log.warning(f"停止录音失败: {e}")

    # ============ 步骤 3: 更新 PG 会话表 ============
    update_call_session_end(
        fs_uuid,
        end_ts=datetime.now(),
        hangup_cause=hangup_cause,
        result_code=map_hangup_code(hangup_cause),
    )

    # ============ 步骤 4: 清理 Redis 状态 ============
    clean_redis_state(fs_uuid)

    # ============ 步骤 5: 记忆写入（异步） ============
    # 通话结束后抽取 facts 和向量记忆
    try:
        finalize_memory(state)
    except Exception as e:
        log.exception(f"记忆写入失败: {e}")

    # ============ 步骤 6: 录音文件处理 ============
    # NAS -> MinIO 归档
    try:
        archive_recording(state)
    except Exception as e:
        log.exception(f"录音归档失败: {e}")

    log.info(f"[{fs_uuid}] 会话清理完成")
```

### 挂机原因映射
```python
def map_hangup_code(cause: str) -> str:
    mapping = {
        "NORMAL_CLEARING": "normal_end",
        "USER_BUSY": "user_busy",
        "NO_ANSWER": "no_answer",
        "CALL_REJECTED": "rejected",
        "NETWORK_TIMEOUT": "network_timeout",
        "LOSE_RTP": "audio_lost",
        "ORIGINATOR_CANCEL": "caller_cancel",
    }
    return mapping.get(cause, "unknown")
```

---

## 8. 转人工逻辑

### 转接条件（可配置）
```python
# 转接条件示例
handoff_conditions = {
    "max_turns": 10,              # 超过 10 轮未解决
    "max_silence": 3,             # 连续 3 次静默
    "max_duration": 600,           # 通话超过 10 分钟
    "intent_handoff": ["投诉", "退款", "复杂问题"],  # 特定意图转人工
    "explicit_request": "转人工",  # 用户明确要求
    "asr_failure_threshold": 3,   # ASR 连续失败 3 次
    "llm_failure_threshold": 2,   # LLM 连续失败 2 次
    "sensitive_field_blocked": True,  # 敏感字段被拦截
}
```

### 转接执行
```python
def transfer_to_agent(fs_uuid: str, extension: str = "1001"):
    """转接人工"""
    state = call_state.get(fs_uuid)
    if not state:
        return

    log.info(f"[{fs_uuid}] 转接人工: {extension}")

    # 记录转接事件
    insert_event(fs_uuid, "handoff", {
        "extension": extension,
        "turn_count": state.get("turn_count"),
        "reason": state.get("handoff_reason", "unknown"),
    })

    # 停止 detect_speech
    stop_detect_speech(fs_uuid)

    # 转接命令
    fs.api("uuid_transfer", f"{fs_uuid} loopback/{extension}")

    # 更新状态
    state["status"] = "handoff"
```

---

## 9. 错误处理与告警

### 9.1 告警级别
```python
ALERT_LEVELS = {
    "CRITICAL": 0,  # 立即处理
    "ERROR": 1,     # 影响通话
    "WARNING": 2,   # 需要关注
    "INFO": 3,      # 仅记录
}
```

### 9.2 告警事件类型
```python
ALERT_EVENTS = {
    # 合规类（CRITICAL）
    "LEGAL_NOTICE_FAILED": {"level": "CRITICAL", "msg": "录音告知未播放"},

    # 服务类（ERROR）
    "DETECT_SPEECH_START_FAILED": {"level": "ERROR", "msg": "语音识别启动失败"},
    "TTS_FAILED": {"level": "ERROR", "msg": "TTS 播报失败"},
    "LLM_FAILED": {"level": "ERROR", "msg": "LLM 调用失败"},
    "ASR_CONSECUTIVE_FAILURES": {"level": "ERROR", "msg": "ASR 连续失败"},

    # 监控类（WARNING）
    "SENSITIVE_FIELD_BLOCKED": {"level": "WARNING", "msg": "敏感字段被拦截"},
    "RECORD_START_FAILED": {"level": "WARNING", "msg": "录音启动失败"},
    "MEMORY_WRITE_FAILED": {"level": "WARNING", "msg": "记忆写入失败"},
}
```

### 9.3 告警触发示例
```python
def trigger_alert(alert_type: str, fs_uuid: str, extra: dict = None):
    alert_config = ALERT_EVENTS.get(alert_type, {})
    level = alert_config.get("level", "INFO")
    msg = alert_config.get("msg", alert_type)

    # 发送告警（Prometheus / 钉钉 / 短信）
    send_alert(
        level=level,
        alert_type=alert_type,
        fs_uuid=fs_uuid,
        message=msg,
        extra=extra,
    )

    # 记录日志
    log.log(
        {"CRITICAL": log.critical, "ERROR": log.error, "WARNING": log.warning, "INFO": log.info}[level],
        f"[ALERT:{level}] {alert_type} - {fs_uuid}: {msg}"
    )
```

### 9.4 熔断回滚
```python
# 如果某个服务连续失败达到阈值，触发熔断
def check_circuit_breaker(service: str, failure_count: int, threshold: int = 5):
    if failure_count >= threshold:
        log.critical(f"熔断触发: {service}，连续失败 {failure_count} 次")
        # 暂停该服务的新请求
        # 切换到降级策略
        # 发送告警
        send_alert(level="CRITICAL", message=f"服务熔断: {service}")
        return True
    return False
```

---

## 10. 附录：配置文件路径

| 配置项 | 默认路径 | 说明 |
|--------|----------|------|
| 录音告知文件 | `/usr/local/freeswitch/sounds/legal_notice.wav` | 法务固定录音 |
| 录音存储根目录 | `/nas/rec` | 按 biz_type 隔离 |
| ESL 监听配置 | `event_socket.conf.xml` | 127.0.0.1:8021 |
| MRCP 配置 | `unimrcp.conf.xml` | ASR/TTS profile |
| 日志目录 | `/var/log/freeswitch/` | 运行时日志 |
| 录音文件格式 | `caller.wav`, `bot.wav` | 双声道分轨 |

---

## 11. 验收检查点

- [ ] CHANNEL_ANSWER 后 2 秒内播放录音告知
- [ ] 录音文件按 biz_type 隔离存储
- [ ] DETECTED_SPEECH 事件正常回传
- [ ] TTS 播放使用正确的业务音色
- [ ] 静默 3 次后自动转人工
- [ ] 通话结束后录音归档到 MinIO
- [ ] 记忆写入 PG（facts + 向量）
- [ ] 告警事件正常触发