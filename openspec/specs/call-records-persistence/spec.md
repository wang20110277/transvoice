# call-records-persistence Specification

## Purpose
TBD - created by archiving change add-call-records-and-recording. Update Purpose after archive.
## Requirements
### Requirement: call_session 开始记录

系统 SHALL 在 `main._on_channel_answer` 注册 ActiveCall 之后、`audio_fork_start` 之前，调用 `repository.insert_call_session` 写入一行 `call_session`，包含 `call_id`/`fs_uuid`（同值 = FreeSWITCH Unique-ID）、`biz_type`、`tenant_id`、`scenario`、`user_id`（本期 = user_key）、`user_key`、`phone_hash`（sha256(user_key)）、`phone_masked`（中间四位掩码）、`start_ts`。

#### Scenario: CHANNEL_ANSWER 写入 session 行
- **WHEN** FreeSWITCH 触发 CHANNEL_ANSWER，agent-flow 解析出 uuid/biz_type/tenant_id/scenario/user_key
- **THEN** 系统 SHALL 在 `_call_registry.register` 后写入一行 call_session
- **AND** 该行 `call_id` 与 `fs_uuid` SHALL 填同一个 uuid 值
- **AND** `phone_masked` SHALL 形如 `138****1234`（首3末4，中间掩码）

#### Scenario: session 写入失败不阻断通话
- **WHEN** `insert_call_session` 抛出 DB 异常
- **THEN** 系统 SHALL 仅记录错误日志
- **AND** SHALL NOT 阻断 audio_fork_start 或后续 WebSocket 通话流

### Requirement: call_session 结束记录

系统 SHALL 在 `main._on_channel_hangup` 调用 `repository.update_call_session_end(fs_uuid, end_ts, hangup_cause, result_code)`，按 fs_uuid 更新该通话的结束时间与挂断原因。

#### Scenario: CHANNEL_HANGUP 更新 session 结束态
- **WHEN** FreeSWITCH 触发 CHANNEL_HANGUP
- **THEN** 系统 SHALL 按当前 uuid（= fs_uuid）更新 call_session.end_ts / hangup_cause / result_code
- **AND** 该更新 SHALL 在 audio_fork_stop 与 cancel_call 之前或之后均可，互不依赖

### Requirement: 每轮对话双写 PG

系统 SHALL 在 `flow.run_streaming_pipeline` 完成 LLM 流式 + TTS 后，与现有 Redis `save_turn` 并行地，写入两行 `call_turn`（role='user' + role='assistant'）。`insert_turn` 调用 SHALL 为 fire-and-forget（`asyncio.create_task`），不阻塞下一轮。

#### Scenario: 完成一轮对话写入 user + assistant 两行
- **WHEN** 一轮 LLM 流式完成，full_text 非空
- **THEN** 系统 SHALL 写入 call_turn(role='user', text=user_input) 与 call_turn(role='assistant', text=full_text)
- **AND** 两行 SHALL 携带相同的 call_id/fs_uuid/biz_type/user_key
- **AND** user_text 与 ai_text 均为空时 SHALL 跳过（与 Redis save_turn 的空轮跳过一致）

#### Scenario: turn 写入失败不阻断通话
- **WHEN** `insert_turn` 的 fire-and-forget task 抛出异常
- **THEN** 系统 SHALL 通过 add_done_callback 记录日志
- **AND** SHALL NOT 影响下一轮 VAD/ASR/LLM/TTS 处理

### Requirement: 关键事件记录

系统 SHALL 在通话关键节点调用 `repository.insert_event` 写入 `call_event`，event_type 与 payload 规范如下：
- `barge_in`：handler 检测到用户打断时，payload 含 turn 号
- `handoff`：LLM action='handoff' 转人工时，payload 含 extension
- `hangup_by_bot`：LLM action='end' 主动挂断时

#### Scenario: barge-in 写事件
- **WHEN** handler 主循环检测到 barge-in（speech_counter 达阈值）
- **THEN** 系统 SHALL 写入 call_event(event_type='barge_in', payload={turn: <turn_count>})
- **AND** 该写入 SHALL 为 fire-and-forget，不延迟 TTS buffer 清空

#### Scenario: handoff 转人工写事件
- **WHEN** `_execute_terminal_action('handoff')` 执行 ESL transfer
- **THEN** 系统 SHALL 写入 call_event(event_type='handoff', payload={extension: <handoff_extension>})

#### Scenario: 主动挂断写事件
- **WHEN** `_execute_terminal_action('end')` 执行 ESL hangup
- **THEN** 系统 SHALL 写入 call_event(event_type='hangup_by_bot', payload={})

### Requirement: user_id 与 phone_hash 本期 fallback

系统 SHALL 在 CHANNEL_ANSWER 写入 call_session 时，将 `user_id` 设为 `user_key`（主叫手机号明文），`phone_hash` 设为 sha256(user_key)。此为 MCP identity 当前禁用（flow.py:334 注释块）下的 fallback；canonical user_id 回填为独立后续变更，不阻塞本能力。

#### Scenario: MCP 禁用时 user_id fallback
- **WHEN** CHANNEL_ANSWER 触发且 MCP 身份查询未启用
- **THEN** call_session.user_id SHALL 等于 user_key
- **AND** call_session.phone_hash SHALL 等于 sha256(user_key)
- **AND** call_session.identity_verified SHALL 为 false（默认）

### Requirement: repository 接线零阻断保证

系统 SHALL 保证所有 PG repository 写入（session/turn/event）以 fire-and-forget 方式执行（直接 await 但外层包裹异常处理，或 `asyncio.create_task` + add_done_callback），任何 DB 异常 SHALL NOT 中断音频流、LLM、TTS、ESL 任一环节。容错等级 SHALL 与现有 Redis `save_turn` 的 `except Exception: logger.warning` 一致。

#### Scenario: DB 不可用通话仍正常
- **WHEN** PostgreSQL 不可达，所有 repository 写入失败
- **THEN** 通话的音频流 / ASR / LLM / TTS / barge-in SHALL 全部正常工作
- **AND** 每次 PG 写入失败 SHALL 记录一条 error 日志（不静默吞错）

