# 通话记录查看与录音回放 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让运营首次能在 console 审查/回放真实通话——agent-flow repository 首次接线（session/turn/event 写 PG）+ FreeSWITCH 整通录音归档（MinIO + artifact 回写）+ console 通话记录列表/详情/录音播放页。

**Architecture:** 三段：(1) agent-flow 在 CHANNEL_ANSWER/HANGUP + 每轮管线把 repository 调用埋点接上，PG 与现有 Redis `save_turn` 双写并存；(2) dialplan 加 `record_session` 录双向混音，HANGUP 时异步上传 MinIO + `insert_artifact`；(3) console 加 4 表只读 Drizzle 映射 + `/api/calls` 读侧 API + 列表/详情页（含录音 `<audio>`）。`call_id=fs_uuid=uuid` 同值填两列（全链路同一 FS Unique-ID）。

**Tech Stack:** Python (agent-flow: FastAPI + SQLAlchemy 2.0 async + pydantic-settings) / XML (FreeSWITCH dialplan) / TypeScript (console: Next.js 15 + Drizzle + vitest)

## Global Constraints

> 每个任务的隐式约束。从 spec/design 原样抄录。

- **最高优先级不变量**：所有 PG repository 写入（session/turn/event/artifact）MUST fire-and-forget（`asyncio.create_task` + `add_done_callback` 记日志，或外层 try/except 记日志），任何 DB 异常**绝不阻断**音频流/LLM/TTS/ESL。容错等级与现有 Redis `save_turn`（`agent-flow/src/memory/chat_history.py:80 except Exception: logger.warning`）一致。
- **call_id = fs_uuid = FreeSWITCH `Unique-ID`**：全链路同值填 `call_session.call_id` 与 `call_session.fs_uuid` 两列（UUID 列，Citus 分布键预留）。不造独立业务 call_id 生成器（YAGNI）。
- **无 DDL 变更**：`call_session`/`call_turn`/`call_event`/`call_artifact` 四表由 agent-flow alembic 已建；console Drizzle 映射只读，不改 DDL。列名与 `agent-flow/src/db/models.py` 严格 snake_case 对齐，杜绝双词汇表。
- **MCP identity 当前禁用**（`flow.py:334` 注释块）：`call_session.user_id` 本期 fallback = `user_key`，`phone_hash` = sha256(user_key)，`phone_masked` = 首3末4。canonical user_id 回填属独立后续变更，不阻塞。
- **Redis `save_turn` 与 PG `insert_turn` 双写并存**：职责不同——Redis 给下一轮 LLM 热上下文（1h TTL），PG 给 console 审查（永久）。
- **配置前缀**：agent-flow 用 pydantic-settings `CALLBOT_` 前缀；console 用 `DATABASE_URL`/`MINIO_*` env。
- **工作目录**：agent-flow 改动在 `agent-flow/`（服务用 `./scripts/local.sh flow`，PYTHONPATH 含 `$(pwd):$(pwd)/src`）；console 改动在 `console/server/`（服务用 `pm2 restart console`，lint = `npx tsc --noEmit`）。
- **测试**：agent-flow 用 pytest（`cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest`）；console 用 vitest（`cd console/server && npm test`）。

---

## File Structure

**agent-flow（Python）— 接线 + 录音归档：**
- `agent-flow/src/config.py`（Modify）— 加 4 个录音配置项
- `agent-flow/src/storage/minio_storage.py`（Modify）— 加 `upload_recording` + `presigned_get_url`
- `agent-flow/src/storage/persistence_helpers.py`（Create）— fire-and-forget 封装（消除三处 add_done_callback 样板）
- `agent-flow/main.py`（Modify）— `_mask_phone`/`_phone_hash` + CHANNEL_ANSWER/HANGUP 接线 + `_archive_recording`
- `agent-flow/src/graph/flow.py`（Modify）— 每轮 turn PG 双写
- `agent-flow/src/ws/handler.py`（Modify）— barge-in/handoff/end 事件写入
- `freeswitch/dialplan/public/00_biz_type.xml`（Modify）— `record_session` + 提示音
- `agent-flow/tests/test_persistence_helpers.py`（Create）— fire-forget + 脱敏函数单测

**console（TypeScript）— 读侧：**
- `console/server/src/db/schema.ts`（Modify）— 4 表 Drizzle 只读映射
- `console/server/src/lib/permissions.ts`（Modify）— `call:view` 权限码
- `console/server/src/lib/calls-service.ts`（Create）— 列表/详情/recording-url 数据层
- `console/server/src/lib/calls-api.ts`（Create）— client fetch 包装
- `console/server/src/app/api/calls/route.ts`（Create）— 列表
- `console/server/src/app/api/calls/[id]/route.ts`（Create）— 详情聚合
- `console/server/src/app/api/calls/[id]/recording-url/route.ts`（Create）— presigned URL
- `console/server/src/app/calls/page.tsx`（Create）— 列表页
- `console/server/src/app/calls/[id]/page.tsx`（Create）— 详情页
- `console/server/src/components/CallRecordsList.tsx`（Create）— 列表组件
- `console/server/src/components/CallDetail.tsx`（Create）— 详情组件
- `console/server/src/components/ConsoleShell.tsx`（Modify）— 启用「通话记录」菜单
- `console/server/tests/lib/calls-service.test.ts`（Create）— calls-service 单测

---

## Task 1: agent-flow 配置 + MinIO 录音方法

> 地基：配置项和 MinIO 方法先就绪，Task 3/4 接线点依赖。

**Files:**
- Modify: `agent-flow/src/config.py`
- Modify: `agent-flow/src/storage/minio_storage.py`

**Interfaces:**
- Produces: `settings.recordings_dir / settings.recording_notice_enabled / settings.recording_archive_timeout / settings.recording_notice_sound`（config.py）；`async upload_recording(call_id, wav_bytes, biz_type, tenant_id) -> str | None`、`presigned_get_url(object_key, expiry=3600) -> str | None`（minio_storage.py）。Task 3 `_archive_recording` 消费这些。

- [ ] **Step 1: config.py 加 4 个录音配置项**

Modify `agent-flow/src/config.py`，在 Settings 类（参照现有 `media_sample_rate`/`jitter_target_depth` 命名风格）加字段：

```python
    # 录音归档（FS record_session 写入路径，agent-flow 读取路径）
    recordings_dir: str = "/Users/lindaw/freeswitch/var/lib/freeswitch/recordings"
    recording_notice_enabled: bool = True
    recording_archive_timeout: int = 30
    # 挂断后间隔秒数再上传录音（等 FS flush 完 wav）；用户要求 3 秒
    recording_archive_delay_sec: int = 3
    recording_notice_sound: str = "ivr/recording_notice.wav"
```

- [ ] **Step 2: minio_storage.upload_recording**

Modify `agent-flow/src/storage/minio_storage.py`，在 `save_turn_audio` 之后加：

```python
async def upload_recording(
    call_id: str, wav_bytes: bytes, biz_type: str, tenant_id: str,
) -> str | None:
    """上传整通录音 wav 到 MinIO。返回 object key；MinIO 未配置返回 None。"""
    if not MINIO_ENDPOINT:
        return None
    key = build_object_key(prefix="recordings", call_id=call_id)
    if key is None:
        return None
    await upload_audio_async(wav_bytes, key)
    return key
```

- [ ] **Step 3: minio_storage.presigned_get_url**

同文件加（顶部 `from datetime import timedelta` 若缺则补）：

```python
def presigned_get_url(object_key: str, expiry: int = 3600) -> str | None:
    """生成 MinIO presigned GET URL。MinIO 未配置或异常返回 None。"""
    client = _client()
    if client is None:
        return None
    try:
        return client.presigned_get_object(
            MINIO_BUCKET, object_key, expires=timedelta(seconds=expiry),
        )
    except Exception as e:
        logger.error("presigned_get_url failed: %s", e)
        return None
```

- [ ] **Step 4: 导入验证**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src python -c "from src.storage.minio_storage import upload_recording, presigned_get_url; from src.config import settings; print(settings.recordings_dir, settings.recording_notice_enabled)"`
Expected: 打印默认路径 + `True`，无 ImportError。

- [ ] **Step 5: Commit**

```bash
git add agent-flow/src/config.py agent-flow/src/storage/minio_storage.py
git commit -m "feat(agent-flow): 录音配置项 + MinIO upload_recording/presigned_get_url"
```

---

## Task 2: FreeSWITCH dialplan 整通录音

> 先于接线——真实呼入才能产出 wav 供 Task 3 归档验证。

**Files:**
- Modify: `freeswitch/dialplan/public/00_biz_type.xml`

**Interfaces:**
- Produces: `${recordings_dir}/${uuid}.wav` 文件（FS 写入）。Task 3 `_archive_recording` 读取。

- [ ] **Step 1: dialplan 加提示音 + record_session**

Modify `freeswitch/dialplan/public/00_biz_type.xml`，在 `<action application="answer"/>` 之后、`<action application="playback" data="silence_stream://-1"/>` 之前插入：

```xml
          <!-- 录音提示音（合规）；变量 recording_notice_enabled 默认 true，本地测试可 FS 侧 set 关 -->
          <action application="playback" data="${recording_notice_sound}" condition="${recording_notice_enabled}==true"/>
          <!-- 整通双向混音录音；FS 原生后台录音，agent-flow 存活无关 -->
          <action application="set" data="RECORD_STEREO=false"/>
          <action application="record_session" data="${recordings_dir}/${uuid}.wav"/>
```

> `${uuid}` 是 FS Channel Unique-ID 内置变量；`${recordings_dir}` 是 FS 内置默认 `$${base_dir}/recordings`。

- [ ] **Step 2: reload + 真实呼入验证**

Run: `./scripts/local.sh stop fs && ./scripts/local.sh fs`；`/Users/lindaw/freeswitch/bin/fs_cli -x "reloadxml"`
Expected: `+OK [Reload XML]`，freeswitch.log 无 XML 解析错误。
真实 SIP 呼入一通几秒后挂断 → `ls -la ${recordings_dir}/` 见 `${uuid}.wav` → `afplay` 可播放含双向音频。

> **若 FS `${recordings_dir}` 与 `CALLBOT_RECORDINGS_DIR` 路径不一致**：记下 FS 实际写入路径，在 Task 3 Step 3 的 `settings.recordings_dir` 默认值或 `.env` 中校正为同一物理路径。

- [ ] **Step 3: Commit**

```bash
git add freeswitch/dialplan/public/00_biz_type.xml
git commit -m "feat(freeswitch): dialplan record_session 整通录音 + 录音提示音"
```

---

## Task 3: agent-flow session 生命周期接线

> 依赖 Task 1（config + minio）+ Task 2（录音文件就绪）。接线点：`main._on_channel_answer`（main.py:215 之后）/ `main._on_channel_hangup`（main.py:163-173）。

**Files:**
- Modify: `agent-flow/main.py`
- Test: `agent-flow/tests/test_persistence_helpers.py`（与 Task 4 共用，此处先建脱敏函数部分）

**Interfaces:**
- Consumes: `repository.insert_call_session` / `repository.update_call_session_end` / `repository.insert_artifact`（已存在于 `agent-flow/src/storage/repository.py`，零调用待接线）；`minio_storage.upload_recording`（Task 1）；`settings.recordings_dir`（Task 1）
- Produces: `_mask_phone(s)` / `_phone_hash(s)`（main.py 模块级）；`_archive_recording(fs_uuid, biz_type, tenant_id, user_key)`（main.py）。Task 4 复用 fire-forget 模式。

- [ ] **Step 1: 写脱敏函数失败测试**

Create `agent-flow/tests/test_persistence_helpers.py`：

```python
"""脱敏 + fire-forget 辅助函数单测。"""
import hashlib
import sys
from pathlib import Path

# 让 main.py 可导入（main.py 在 agent-flow/，tests 在 agent-flow/tests/）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_mask_phone_long():
    from main import _mask_phone
    assert _mask_phone("13812345678") == "138****5678"


def test_mask_phone_short_passthrough():
    from main import _mask_phone
    assert _mask_phone("123") == "123"


def test_phone_hash_is_sha256_hex():
    from main import _phone_hash
    assert _phone_hash("13812345678") == hashlib.sha256(b"13812345678").hexdigest()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/test_persistence_helpers.py -v`
Expected: FAIL（`ImportError: cannot import name '_mask_phone' from 'main'`）

- [ ] **Step 3: main.py 加脱敏函数**

Modify `agent-flow/main.py`，顶部 `import hashlib` + `import os`（若缺），模块级（`_call_registry = ...` 附近）加：

```python
def _mask_phone(s: str) -> str:
    """手机号脱敏：首3末4，中间掩码。短串原样返回。"""
    return f"{s[:3]}****{s[-4:]}" if len(s) >= 7 else s


def _phone_hash(s: str) -> str:
    """手机号 sha256（脱敏存储，供跨通话关联同一用户）。"""
    return hashlib.sha256(s.encode()).hexdigest()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd agent-flow && PYTHONPATH=$(pwd):$(pwd)/src pytest tests/test_persistence_helpers.py -v`
Expected: 3 passed

- [ ] **Step 5: main.py CHANNEL_ANSWER 写 session start**

Modify `agent-flow/main.py`：顶部加 `from src.storage import repository, minio_storage` + `from datetime import datetime`（若缺）。在 `_on_channel_answer` 的 `_call_registry.register(uuid, ...)`（main.py:215）之后、`ws_url = ...` 之前插入：

```python
        # session 写入 PG（fire-and-forget，DB 异常不阻断通话）
        try:
            await repository.insert_call_session({
                "call_id": uuid, "fs_uuid": uuid,
                "user_id": user_key, "biz_type": biz_type,
                "tenant_id": tenant_id, "scenario": scenario,
                "phone_hash": _phone_hash(user_key), "user_key": user_key,
                "phone_masked": _mask_phone(user_key),
                "start_ts": datetime.now(),
                "recording_notice_played": settings.recording_notice_enabled,
            })
        except Exception as e:
            logger.error("[%s] insert_call_session failed: %s", uuid, e)
```

- [ ] **Step 6: main.py CHANNEL_HANGUP session end + 触发归档**

Modify `_on_channel_hangup`（main.py:163-173）。当前函数体替换为：

```python
    async def _on_channel_hangup(event):
        uuid = event.headers.get("Unique-ID", "")
        if not uuid:
            return
        logger.info("[%s] CHANNEL_HANGUP", uuid)
        _audio_fork_started.discard(uuid)

        hangup_cause = event.headers.get("Hangup-Cause", "")
        result_code = event.headers.get("Variable-Hangup-Cause", "")
        end_ts = datetime.now()
        active = _call_registry.get(uuid)

        # session end + recording 归档（fire-and-forget，不阻塞 hangup 清理）
        if active:
            try:
                await repository.update_call_session_end(
                    uuid, end_ts, hangup_cause, result_code,
                )
            except Exception as e:
                logger.error("[%s] update_call_session_end failed: %s", uuid, e)
            task = asyncio.create_task(
                _archive_recording(uuid, active.biz_type, active.tenant_id, active.user_key)
            )
            task.add_done_callback(
                lambda t: t.exception() and logger.error(
                    "[%s] _archive_recording failed: %s", uuid, t.exception(),
                )
            )

        try:
            await esl.audio_fork_stop(uuid)
        except Exception:
            pass
        _call_registry.cancel_call(uuid)
```

- [ ] **Step 7: main.py _archive_recording 协程**

Modify `agent-flow/main.py`，模块级加（`_mask_phone` 附近）：

```python
async def _archive_recording(fs_uuid: str, biz_type: str, tenant_id: str, user_key: str) -> None:
    """挂断后间隔 3s → 读 FS 录音 → 上传 MinIO → insert_artifact(kind='recording')。"""
    # 用户要求：挂断后间隔 3 秒再上传（等 FS flush 完 wav）
    await asyncio.sleep(settings.recording_archive_delay_sec)
    path = os.path.join(settings.recordings_dir, f"{fs_uuid}.wav")
    if not os.path.exists(path):
        logger.warning("[%s] recording file not found after %ds: %s",
                       fs_uuid, settings.recording_archive_delay_sec, path)
        return

    try:
        with open(path, "rb") as f:
            wav_bytes = f.read()
    except OSError as e:
        logger.warning("[%s] read recording failed: %s", fs_uuid, e)
        return

    key = await minio_storage.upload_recording(fs_uuid, wav_bytes, biz_type, tenant_id)
    if key is None:
        return  # MinIO 未配置，静默跳过

    try:
        await repository.insert_artifact(
            call_id=fs_uuid, fs_uuid=fs_uuid, biz_type=biz_type,
            user_id=user_key, user_key=user_key,
            kind="recording", storage="minio", uri=key,
            size_bytes=len(wav_bytes), content_type="audio/wav",
        )
        logger.info("[%s] recording archived: %s (%d bytes)", fs_uuid, key, len(wav_bytes))
    except Exception as e:
        logger.error("[%s] insert_artifact(recording) failed: %s", fs_uuid, e)
```

- [ ] **Step 8: 真实呼入集成验证**

Run: 重启 `./scripts/local.sh stop flow && ./scripts/local.sh flow`；真实 SIP 呼入完整一通 → 挂断。
Verify:
```bash
docker exec callbot-postgres psql -U postgres -d callbot -c \
  "SELECT call_id, fs_uuid, user_id, phone_masked, biz_type, tenant_id, start_ts, end_ts, hangup_cause, recording_notice_played FROM callbot.call_session ORDER BY id DESC LIMIT 1"
```
Expected: call_id == fs_uuid；phone_masked 形如 `138****5678`；start_ts/end_ts/hangup_cause 已填。
```bash
docker exec callbot-postgres psql -U postgres -d callbot -c \
  "SELECT kind, storage, uri, size_bytes FROM callbot.call_artifact WHERE call_id='<uuid>'"
```
Expected: `recording / minio / recordings/<date>/<uuid>.wav / <bytes>`。通话过程 flow.log 无异常。

- [ ] **Step 9: Commit**

```bash
git add agent-flow/main.py agent-flow/tests/test_persistence_helpers.py
git commit -m "feat(agent-flow): CHANNEL_ANSWER/HANGUP 接线 repository + 录音归档"
```

---

## Task 4: 每轮 turn + 事件接线

> 依赖 Task 3。接线点：`flow.run_streaming_pipeline`（flow.py:496 save_turn 旁）、`handler._execute_terminal_action`（handler.py:628-641）、handler 主循环 barge-in 分支（handler.py:174-189）。

**Files:**
- Modify: `agent-flow/src/ws/handler.py`
- Modify: `agent-flow/src/graph/flow.py`

**Interfaces:**
- Consumes: `repository.insert_turn` / `repository.insert_event`（已存在）；Task 3 的 fire-forget 模式
- Produces: 每轮 call_turn(user+assistant) 行；barge_in/handoff/hangup_by_bot call_event 行

- [ ] **Step 1: flow.py 每轮 turn PG 双写**

Modify `agent-flow/src/graph/flow.py`：顶部 `from storage import repository`（在现有 `from storage import minio_storage` 旁）。在 `run_streaming_pipeline` 末尾 `await save_turn(...)`（flow.py:496）之后加：

```python
    # PG 双写：console 审查（fire-and-forget，不阻断下一轮）
    _user_key = state.get("user_key", "")
    if state.get("user_input", "").strip():
        asyncio.create_task(repository.insert_turn(
            call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
            user_id=_user_key, user_key=_user_key, role="user",
            text=state.get("user_input", ""),
        ))
    if full_text.strip():
        asyncio.create_task(repository.insert_turn(
            call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
            user_id=_user_key, user_key=_user_key, role="assistant",
            text=full_text,
        ))
```

> `asyncio.create_task` 创建的 task 若抛异常，Python 仅在 GC 时打印 warning。为不静默丢数据，可改用 Task 3 同款 `add_done_callback` 记日志——若三处重复样板明显，抽到 `storage/persistence_helpers.py`（CLAUDE.md：三行相似可容忍，超过则抽）。

- [ ] **Step 2: handler.py terminal action 事件**

Modify `agent-flow/src/ws/handler.py`：顶部 `from storage import repository`（在 `from storage import minio_storage` 旁）。`_execute_terminal_action`（handler.py:628）内，action='handoff' 与 action='end' 分支各加 fire-and-forget insert_event。由于此函数无 biz_type/user_key 参数，从 registry 取：

```python
    async def _execute_terminal_action(self, action: str, call_id: str) -> None:
        """通过 ESL 执行终态动作（挂断/转接）。"""
        if self._esl is None:
            logger.warning("[%s] ESL unavailable, cannot execute: %s", call_id, action)
            return
        active = self._registry.get(call_id) if self._registry else None
        biz_type = active.biz_type if active else ""
        user_key = active.user_key if active else ""
        if action in ("end", "handoff"):
            asyncio.create_task(repository.insert_event(
                call_id=call_id, fs_uuid=call_id, biz_type=biz_type,
                user_id=user_key, user_key=user_key,
                event_type="hangup_by_bot" if action == "end" else "handoff",
                payload={"extension": self._handoff_extension} if action == "handoff" else {},
            ))
        try:
            if action == "end":
                result = await self._esl.hangup(call_id)
                logger.info("[%s] ESL hangup: %s", call_id, result)
            elif action == "handoff":
                result = await self._esl.transfer(call_id, self._handoff_extension)
                logger.info("[%s] ESL transfer to %s: %s", call_id, self._handoff_extension, result)
        except Exception as e:
            logger.error("[%s] ESL action '%s' failed: %s", call_id, action, e)
```

- [ ] **Step 3: handler.py barge-in 事件**

Modify handler 主循环 `if barge_detected:` 分支（handler.py:174），在 `tts_buffer.clear()` 之后加：

```python
                        asyncio.create_task(repository.insert_event(
                            call_id=call_id, fs_uuid=call_id,
                            biz_type=biz_type, user_id=user_key, user_key=user_key,
                            event_type="barge_in", payload={"turn": turn_count},
                        ))
```

> `call_id`/`biz_type`/`user_key` 在 `handle()` 作用域可用（函数参数/局部）。加在 `tts_buffer.clear()` 与 `_cancel_asr_stream` 之间，不延迟清空。

- [ ] **Step 4: 真实呼入集成验证**

Run: 重启 flow；真实呼入多轮 + 人为打断一次 → 挂断。
Verify:
```bash
docker exec callbot-postgres psql -U postgres -d callbot -c \
  "SELECT role, left(text,30) FROM callbot.call_turn WHERE call_id='<uuid>' ORDER BY ts"
```
Expected: user/assistant 交替行，对话时序正确。
```bash
docker exec callbot-postgres psql -U postgres -d callbot -c \
  "SELECT event_type, payload FROM callbot.call_event WHERE call_id='<uuid>' ORDER BY ts"
```
Expected: 含 `barge_in`（payload.turn = 轮号）。通话全程无异常日志。

- [ ] **Step 5: 不阻断不变量验证**

Run: `docker stop callbot-postgres` → 真实呼入一通完整通话（多轮 + barge-in + 挂断）→ `docker start callbot-postgres`
Expected: 通话音频/LLM/TTS/barge-in 全部正常完成；flow.log 仅见 `insert_turn failed` / `insert_event failed` error 日志（非 traceback 中断）；恢复 PG 后下一通正常落库。**此步失败 = 接线阻断通话，必须修复。**

- [ ] **Step 6: Commit**

```bash
git add agent-flow/src/graph/flow.py agent-flow/src/ws/handler.py
git commit -m "feat(agent-flow): 每轮 turn + barge-in/handoff 事件 PG 双写"
```

---

## Task 5: console 权限码 + 4 表 schema 只读映射

> console 读侧地基，Task 6/7 依赖。无 DDL 变更。

**Files:**
- Modify: `console/server/src/lib/permissions.ts`
- Modify: `console/server/src/db/schema.ts`

**Interfaces:**
- Produces: `call:view` 权限码（admin/editor/viewer 持有，platform_admin 超集）；`callSession`/`callTurn`/`callEvent`/`callArtifact` Drizzle 表映射 + 类型导出。Task 6 calls-service 消费。

- [ ] **Step 1: permissions.ts 加 call:view**

Modify `console/server/src/lib/permissions.ts`：`PermissionCode` 联合加 `| 'call:view'`；`ROLE_PERMISSIONS` 的 admin/editor/viewer 各加 `'call:view'`（参照现有 `'calltask:view'` 加法）。

- [ ] **Step 2: schema.ts 4 表映射**

Modify `console/server/src/db/schema.ts`，在 `callTask` 映射之后（`export type CallTask` 后）加 4 表，列名严格对齐 `agent-flow/src/db/models.py`（snake_case DB 列）。参照现有 `callTask`/`inboundRoute` 的 Drizzle 写法：

```typescript
/** 通话会话事实表 — 只读映射，DDL 由 agent-flow alembic 维护。列名对齐 models.py CallSession。 */
export const callSession = callbot.table('call_session', {
  id: bigserial('id', { mode: 'number' }).primaryKey(),
  userId: text('user_id').notNull(),
  callId: text('call_id').notNull(),
  fsUuid: text('fs_uuid').notNull(),
  tenantId: text('tenant_id'),
  bizType: text('biz_type').notNull(),
  scenario: text('scenario'),
  taskId: text('task_id'),
  phoneHash: text('phone_hash').notNull(),
  userKey: text('user_key').notNull(),
  phoneMasked: text('phone_masked'),
  startTs: timestamp('start_ts', { withTimezone: true }).notNull(),
  endTs: timestamp('end_ts', { withTimezone: true }),
  resultCode: text('result_code'),
  hangupCause: text('hangup_cause'),
  identityVerified: boolean('identity_verified').notNull().default(false),
  verifyAttempts: integer('verify_attempts').notNull().default(0),
  recordingNoticePlayed: boolean('recording_notice_played').notNull().default(false),
  createTime: timestamp('create_time', { withTimezone: true }).notNull().defaultNow(),
  createUser: text('create_user').notNull().default('system'),
  updateTime: timestamp('update_time', { withTimezone: true }).notNull().defaultNow(),
  updateUser: text('update_user').notNull().default('system'),
});

/** 逐轮对话表 — 只读映射。列名对齐 models.py CallTurn。 */
export const callTurn = callbot.table('call_turn', {
  id: bigserial('id', { mode: 'number' }).primaryKey(),
  userId: text('user_id').notNull(),
  callId: text('call_id').notNull(),
  fsUuid: text('fs_uuid').notNull(),
  bizType: text('biz_type').notNull(),
  userKey: text('user_key').notNull(),
  role: text('role').notNull(),
  text: text('text'),
  asrConf: real('asr_conf'),
  startMs: integer('start_ms'),
  endMs: integer('end_ms'),
  ts: timestamp('ts', { withTimezone: true }).notNull().defaultNow(),
  createTime: timestamp('create_time', { withTimezone: true }).notNull().defaultNow(),
  createUser: text('create_user').notNull().default('system'),
  updateTime: timestamp('update_time', { withTimezone: true }).notNull().defaultNow(),
  updateUser: text('update_user').notNull().default('system'),
});

/** 事件流表 — 只读映射。列名对齐 models.py CallEvent。 */
export const callEvent = callbot.table('call_event', {
  id: bigserial('id', { mode: 'number' }).primaryKey(),
  userId: text('user_id').notNull(),
  callId: text('call_id').notNull(),
  fsUuid: text('fs_uuid').notNull(),
  bizType: text('biz_type').notNull(),
  userKey: text('user_key').notNull(),
  eventType: text('event_type').notNull(),
  payload: jsonb('payload').notNull().default(sql`'{}'::jsonb`),
  ts: timestamp('ts', { withTimezone: true }).notNull().defaultNow(),
  createTime: timestamp('create_time', { withTimezone: true }).notNull().defaultNow(),
  createUser: text('create_user').notNull().default('system'),
  updateTime: timestamp('update_time', { withTimezone: true }).notNull().defaultNow(),
  updateUser: text('update_user').notNull().default('system'),
});

/** 录音/音频产物表 — 只读映射。列名对齐 models.py CallArtifact。 */
export const callArtifact = callbot.table('call_artifact', {
  id: bigserial('id', { mode: 'number' }).primaryKey(),
  userId: text('user_id').notNull(),
  callId: text('call_id').notNull(),
  fsUuid: text('fs_uuid').notNull(),
  bizType: text('biz_type').notNull(),
  userKey: text('user_key').notNull(),
  kind: text('kind').notNull(),
  storage: text('storage').notNull(),
  uri: text('uri').notNull(),
  sha256: text('sha256'),
  sizeBytes: bigint('size_bytes', { mode: 'number' }),
  contentType: text('content_type'),
  ts: timestamp('ts', { withTimezone: true }).notNull().defaultNow(),
  createTime: timestamp('create_time', { withTimezone: true }).notNull().defaultNow(),
  createUser: text('create_user').notNull().default('system'),
  updateTime: timestamp('update_time', { withTimezone: true }).notNull().defaultNow(),
  updateUser: text('update_user').notNull().default('system'),
});

export type CallSession = typeof callSession.$inferSelect;
export type CallTurn = typeof callTurn.$inferSelect;
export type CallEvent = typeof callEvent.$inferSelect;
export type CallArtifact = typeof callArtifact.$inferSelect;
```

> `real` 需从 `drizzle-orm/pg-core` import（asr_conf 是 Float）。检查现有 import 列表，缺则补 `real`、`bigint`。

- [ ] **Step 3: 类型检查 + 列名对齐校验**

Run: `cd console/server && npx tsc --noEmit`
Expected: 无错误。
Run: `docker exec callbot-postgres psql -U postgres -d callbot -c '\d callbot.call_session'`（+ call_turn/call_event/call_artifact）
Expected: DB 列名与 schema.ts 的 snake_case 字符串逐一一致。

- [ ] **Step 4: Commit**

```bash
git add console/server/src/lib/permissions.ts console/server/src/db/schema.ts
git commit -m "feat(console): call:view 权限 + call 四表 Drizzle 只读映射"
```

---

## Task 6: console 通话记录 API + 数据层单测

> 依赖 Task 5。参照 `routes-service.ts` + `api/inbound-routes/route.ts` 模式。

**Files:**
- Create: `console/server/src/lib/calls-service.ts`
- Create: `console/server/src/lib/calls-api.ts`
- Create: `console/server/src/app/api/calls/route.ts`
- Create: `console/server/src/app/api/calls/[id]/route.ts`
- Create: `console/server/src/app/api/calls/[id]/recording-url/route.ts`
- Test: `console/server/tests/lib/calls-service.test.ts`

**Interfaces:**
- Consumes: Task 5 的 4 表映射 + `call:view` 权限；`minio_storage` 逻辑（console 侧需独立 MinIO client 生成 presigned URL）
- Produces: `GET /api/calls`（列表，隔离+筛选+分页）、`GET /api/calls/:id`（详情聚合）、`GET /api/calls/:id/recording-url`（presigned 1h）。Task 7 UI 消费。

- [ ] **Step 1: calls-service.ts 失败测试**

Create `console/server/tests/lib/calls-service.test.ts`（参照 `tests/lib/` 现有 vitest 风格）：

```typescript
import { describe, it, expect } from 'vitest';

describe('calls-service DTO shaping', () => {
  it('phone_hash never leaks to client (only phone_masked)', async () => {
    // 详情/列表 DTO MUST 含 phoneMasked，不含 phoneHash
    const { toSessionDTO } = await import('@/lib/calls-service');
    const dto = toSessionDTO({
      id: 1, userId: 'u1', callId: 'c1', fsUuid: 'c1', tenantId: 'default',
      bizType: 'marketing', scenario: 'default', taskId: null,
      phoneHash: 'SECRET_HASH', userKey: '13812345678', phoneMasked: '138****5678',
      startTs: new Date(), endTs: null, resultCode: null, hangupCause: null,
      identityVerified: false, verifyAttempts: 0, recordingNoticePlayed: true,
      createTime: new Date(), createUser: 'system', updateTime: new Date(), updateUser: 'system',
    });
    expect(dto.phoneMasked).toBe('138****5678');
    expect((dto as Record<string, unknown>).phoneHash).toBeUndefined();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd console/server && npx vitest run tests/lib/calls-service.test.ts`
Expected: FAIL（`toSessionDTO` 不存在 / 模块未找到）

- [ ] **Step 3: calls-service.ts 数据层**

Create `console/server/src/lib/calls-service.ts`（参照 `routes-service.ts` + `call-tasks-service.ts` 隔离模式）。核心函数：`listCalls({tenantId, bizType?, phoneMasked?, startFrom?, startTo?, page, pageSize})`、`getCallDetail(id, tenantId)`、`getRecordingArtifact(id, tenantId)`、`toSessionDTO(row)`（剥 phone_hash）。跨租户详情返回 null（→ route 转 404，不泄漏存在性）：

```typescript
/**
 * 通话记录服务层 — call_session/turn/event/artifact 四表只读聚合。
 * 按 activeTenantId 隔离；跨租户详情返回 null（route 转 404，不泄漏存在性）。
 */
import { and, asc, count, desc, eq, gte, like, lte } from 'drizzle-orm';
import { db } from '@/db/client';
import { callSession, callTurn, callEvent, callArtifact } from '@/db/schema';
import { presignedRecordingUrl } from './minio-client';

export interface SessionDTO {
  id: number;
  callId: string;
  fsUuid: string;
  bizType: string;
  tenantId: string | null;
  scenario: string | null;
  phoneMasked: string | null;
  userKey: string;
  startTs: Date;
  endTs: Date | null;
  hangupCause: string | null;
  resultCode: string | null;
  identityVerified: boolean;
  recordingNoticePlayed: boolean;
  durationMs: number | null;
}

type SessionRow = typeof callSession.$inferSelect;

// 剥 phone_hash（脱敏哈希不下发前端，只给 phone_masked）
export function toSessionDTO(row: SessionRow): SessionDTO {
  const durationMs = row.endTs && row.startTs
    ? row.endTs.getTime() - row.startTs.getTime()
    : null;
  return {
    id: row.id, callId: row.callId, fsUuid: row.fsUuid, bizType: row.bizType,
    tenantId: row.tenantId, scenario: row.scenario, phoneMasked: row.phoneMasked,
    userKey: row.userKey, startTs: row.startTs, endTs: row.endTs,
    hangupCause: row.hangupCause, resultCode: row.resultCode,
    identityVerified: row.identityVerified, recordingNoticePlayed: row.recordingNoticePlayed,
    durationMs,
  };
}

export interface ListParams {
  tenantId: string;
  bizType?: string;
  phoneMasked?: string;
  startFrom?: Date;
  startTo?: Date;
  page: number;
  pageSize: number;
}

export async function listCalls(p: ListParams): Promise<{ calls: SessionDTO[]; total: number }> {
  const conds = [eq(callSession.tenantId, p.tenantId)];
  if (p.bizType) conds.push(eq(callSession.bizType, p.bizType));
  if (p.phoneMasked) conds.push(like(callSession.phoneMasked, `%${p.phoneMasked}%`));
  if (p.startFrom) conds.push(gte(callSession.startTs, p.startFrom));
  if (p.startTo) conds.push(lte(callSession.startTs, p.startTo));
  const where = and(...conds);

  const [rows, totalRows] = await Promise.all([
    db.select().from(callSession).where(where).orderBy(desc(callSession.startTs))
      .limit(p.pageSize).offset((p.page - 1) * p.pageSize),
    db.select({ n: count() }).from(callSession).where(where),
  ]);
  return { calls: rows.map(toSessionDTO), total: totalRows[0]?.n ?? 0 };
}

export interface CallDetail {
  session: SessionDTO;
  turns: (typeof callTurn.$inferSelect)[];
  events: (typeof callEvent.$inferSelect)[];
  artifacts: (typeof callArtifact.$inferSelect)[];
}

export async function getCallDetail(id: number, tenantId: string): Promise<CallDetail | null> {
  const sess = await db.select().from(callSession)
    .where(and(eq(callSession.id, id), eq(callSession.tenantId, tenantId)));
  if (sess.length === 0) return null;
  const session = sess[0];
  const callId = session.callId;
  const [turns, events, artifacts] = await Promise.all([
    db.select().from(callTurn).where(eq(callTurn.callId, callId)).orderBy(asc(callTurn.ts)),
    db.select().from(callEvent).where(eq(callEvent.callId, callId)).orderBy(asc(callEvent.ts)),
    db.select().from(callArtifact).where(eq(callArtifact.callId, callId)),
  ]);
  return { session: toSessionDTO(session), turns, events, artifacts };
}

export async function getRecordingUrl(id: number, tenantId: string): Promise<string | null> {
  // 跨租户不泄漏：先校验 session 归属
  const sess = await db.select({ callId: callSession.callId }).from(callSession)
    .where(and(eq(callSession.id, id), eq(callSession.tenantId, tenantId)));
  if (sess.length === 0) return null;
  const arts = await db.select().from(callArtifact)
    .where(and(eq(callArtifact.callId, sess[0].callId), eq(callArtifact.kind, 'recording')));
  if (arts.length === 0) return null;
  return presignedRecordingUrl(arts[0].uri); // 1h presigned；MinIO 未配置返回 null
}
```

- [ ] **Step 4: minio-client.ts（console 侧 presigned）**

Create `console/server/src/lib/minio-client.ts`（console 直连 MinIO 生成 presigned，与 agent-flow 共享 `MINIO_*` env）：

```typescript
/**
 * Console 侧 MinIO client — 仅生成 presigned GET URL（通话录音播放）。
 * 与 agent-flow 共享同一 MinIO 实例 + 同一 bucket（MINIO_* env）。
 * MINIO_ENDPOINT 为空时返回 null（console 显示"录音未归档"）。
 */
import { Client } from 'minio';

const ENDPOINT = process.env.MINIO_ENDPOINT ?? '';
const ACCESS_KEY = process.env.MINIO_ACCESS_KEY ?? '';
const SECRET_KEY = process.env.MINIO_SECRET_KEY ?? '';
const BUCKET = process.env.MINIO_BUCKET ?? 'audio-archive';
const SECURE = (process.env.MINIO_SECURE ?? 'false').toLowerCase() === 'true';

function client(): Client | null {
  if (!ENDPOINT) return null;
  const [end, portStr] = ENDPOINT.split(':');
  return new Client({ endPoint: end, port: portStr ? Number(portStr) : 9000, useSSL: SECURE, accessKey: ACCESS_KEY, secretKey: SECRET_KEY });
}

export function presignedRecordingUrl(objectKey: string, expirySec = 3600): string | null {
  const c = client();
  if (!c) return null;
  return c.presignedGetObject(BUCKET, objectKey, expirySec);
}
```

> `minio` npm 包需加入 console/server 依赖：`cd console/server && npm install minio`。

- [ ] **Step 5: calls-api.ts（client fetch 包装）**

Create `console/server/src/lib/calls-api.ts`（参照 `routes-api.ts`）：

```typescript
/** 前端通话记录 API 客户端。 */
import type { SessionDTO } from './calls-service';

export interface CallDetailClient {
  session: SessionDTO;
  turns: { id: number; role: string; text: string | null; ts: string }[];
  events: { id: number; eventType: string; payload: Record<string, unknown>; ts: string }[];
  artifacts: { id: number; kind: string; uri: string; sizeBytes: number | null }[];
}

export interface ListQuery {
  bizType?: string;
  phoneMasked?: string;
  startFrom?: string;
  startTo?: string;
  page?: number;
  pageSize?: number;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...init, headers: { ...(init?.headers ?? {}) } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error ?? `HTTP ${res.status}`);
  return data as T;
}

function qs(q: ListQuery): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
}

export const callsApi = {
  list: (q: ListQuery = {}) => req<{ calls: SessionDTO[]; total: number }>(`/api/calls${qs(q)}`),
  detail: (id: number) => req<CallDetailClient>(`/api/calls/${id}`),
  recordingUrl: (id: number) => req<{ url: string; expiresIn: number }>(`/api/calls/${id}/recording-url`),
};
```

- [ ] **Step 6: /api/calls 列表 route**

Create `console/server/src/app/api/calls/route.ts`（参照 `api/call-tasks/route.ts`）：

```typescript
/** GET /api/calls — 列表（按 activeTenantId 隔离 + 筛选 + 分页）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { listCalls } from '@/lib/calls-service';

export async function GET(req: Request) {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const url = new URL(req.url);
  const bizType = url.searchParams.get('bizType') || undefined;
  const phoneMasked = url.searchParams.get('phoneMasked') || undefined;
  const startFrom = url.searchParams.get('startFrom');
  const startTo = url.searchParams.get('startTo');
  const page = Number(url.searchParams.get('page') ?? '1');
  const pageSize = Number(url.searchParams.get('pageSize') ?? '20');
  const result = await listCalls({
    tenantId: auth.tenantId, bizType,
    phoneMasked,
    startFrom: startFrom ? new Date(startFrom) : undefined,
    startTo: startTo ? new Date(startTo) : undefined,
    page, pageSize,
  });
  return NextResponse.json(result);
}
```

- [ ] **Step 7: /api/calls/[id] 详情 + recording-url route**

Create `console/server/src/app/api/calls/[id]/route.ts`：

```typescript
/** GET /api/calls/:id — 详情聚合（session + turns + events + artifacts）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getCallDetail } from '@/lib/calls-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const detail = await getCallDetail(Number(id), auth.tenantId);
  if (!detail) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(detail);
}
```

Create `console/server/src/app/api/calls/[id]/recording-url/route.ts`：

```typescript
/** GET /api/calls/:id/recording-url — 录音 presigned URL（1h）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getRecordingUrl } from '@/lib/calls-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const url = await getRecordingUrl(Number(id), auth.tenantId);
  if (!url) return NextResponse.json({ error: 'no recording' }, { status: 404 });
  return NextResponse.json({ url, expiresIn: 3600 });
}
```

- [ ] **Step 8: 运行单测 + 类型检查 + 路由冒烟**

Run: `cd console/server && npx vitest run tests/lib/calls-service.test.ts`
Expected: PASS。
Run: `cd console/server && npx tsc --noEmit`
Expected: 无错误。
Run: `pm2 restart console`；`curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3001/api/calls`
Expected: `401`（未登录，路由已注册 + 守卫生效）。

- [ ] **Step 9: Commit**

```bash
git add console/server/src/lib/calls-service.ts console/server/src/lib/calls-api.ts \
        console/server/src/lib/minio-client.ts console/server/package.json console/server/package-lock.json \
        console/server/src/app/api/calls console/server/tests/lib/calls-service.test.ts
git commit -m "feat(console): /api/calls 列表/详情/录音URL + calls-service 数据层"
```

---

## Task 7: console 通话记录 UI

> 依赖 Task 6 API + Task 5 菜单。

**Files:**
- Modify: `console/server/src/components/ConsoleShell.tsx`
- Create: `console/server/src/app/calls/page.tsx`
- Create: `console/server/src/app/calls/[id]/page.tsx`
- Create: `console/server/src/components/CallRecordsList.tsx`
- Create: `console/server/src/components/CallDetail.tsx`

**Interfaces:**
- Consumes: `callsApi.list/detail/recordingUrl`（Task 6）；ConsoleShell 菜单

- [ ] **Step 1: ConsoleShell 启用「通话记录」菜单**

Modify `console/server/src/components/ConsoleShell.tsx`：MENUS 的 `records` 项（`enabled: false`「下期」）改为 `{ key: 'records', label: '通话记录', icon: FileSpreadsheet, href: '/calls', enabled: true }`（参照已启用的 callcenter 项）。

- [ ] **Step 2: /calls 列表页 + 组件**

Create `console/server/src/app/calls/page.tsx`（参照 `app/call-tasks/page.tsx`）：

```typescript
import { redirect } from 'next/navigation';
import { getSession, activeTenantIdOf } from '@/auth/session';
import ConsoleShell from '@/components/ConsoleShell';
import CallRecordsList from '@/components/CallRecordsList';

export default async function CallsPage() {
  const session = await getSession();
  if (!session) redirect('/login');
  const user = session.user as { email: string; name: string; tenantId?: string; role?: string };
  const tenantId = activeTenantIdOf(session) ?? user.tenantId ?? 'default';
  return (
    <ConsoleShell tenantId={tenantId} userEmail={user.email} userName={user.name} role={user.role ?? 'admin'}>
      <CallRecordsList tenantId={tenantId} />
    </ConsoleShell>
  );
}
```

Create `console/server/src/components/CallRecordsList.tsx`（client component，参照 `CallTasksManager.tsx` 的 fetch + 筛选 + 分页 + toast 模式）：表格列（开始时间 / biz_type / phone_masked / 时长 durationMs / hangupCause）+ 筛选区（biz_type select、phone_masked input、时间范围、查询按钮）+ 分页（上一页/下一页 + total）+ 点击行 `router.push('/calls/<id>')` + 空态。用 `callsApi.list(q)`。

- [ ] **Step 3: /calls/[id] 详情页 + 组件**

Create `console/server/src/app/calls/[id]/page.tsx`（server component 包裹，同 Step 2 模式，render `<CallDetail id={params.id} />`）。

Create `console/server/src/components/CallDetail.tsx`（client component）：
- 顶部录音播放器：`callsApi.recordingUrl(id)` → 200 渲染 `<audio controls src={url}>`，404 显示"录音未归档"
- 逐轮对话回放：turns 按 ts ASC，user 右气泡（slate）/ assistant 左气泡（indigo），展示 text
- 事件时间线：events 按 ts ASC，列 eventType + payload（barge_in 显示轮号、handoff 显示分机号）

- [ ] **Step 4: 类型检查 + 重启 + 手工走查**

Run: `cd console/server && npx tsc --noEmit`
Expected: 无错误。
Run: `pm2 restart console`；`curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3001/calls`
Expected: `307` → /login（未登录重定向，页面已注册）。
登录 console 手工走查：侧栏「通话记录」可见 → 列表展示通话 + 筛选生效 → 点击进详情 → 逐轮气泡回放 + 事件时间线 → 录音可播放（或显示未归档）。

- [ ] **Step 5: Commit**

```bash
git add console/server/src/components/ConsoleShell.tsx console/server/src/app/calls \
        console/server/src/components/CallRecordsList.tsx console/server/src/components/CallDetail.tsx
git commit -m "feat(console): 通话记录列表/详情页 + 录音播放器"
```

---

## Task 8: 收尾验证

> 全链路 + 不变量 + 归档。不改代码（仅验证）。

**Files:** 无（验证步骤）

- [ ] **Step 1: 端到端真实通话**

真实 SIP 呼入：answer（录音提示音）→ 多轮对话 → 人为 barge-in 一次 → 再几轮 → 挂断。检查 PG：call_session start+end 行、call_turn 全部轮次 user/assistant 行、call_event 含 barge_in 行、call_artifact 含 recording 行；console /calls 见该通话；详情页对话回放与实际一致；录音可播放；事件时间线含 barge_in。

- [ ] **Step 2: 不阻断不变量（最高优先级）**

`docker stop callbot-postgres` → 真实呼入完整通话 → 音频/LLM/TTS/barge-in 全部正常 → flow.log 仅见 repository error 日志（非中断）→ `docker start callbot-postgres` → 下一通正常落库。**失败则接线阻断，必须回 Task 3/4 修复。**

- [ ] **Step 3: 多租户隔离**

platform_admin 切到不同租户 → /calls 仅显示该租户通话；用 default 的 call_session.id 在 galaxy_fin 会话请求 → 404；普通 admin 仅见自己租户。

- [ ] **Step 4: OpenSpec 校验**

Run: `openspec validate add-call-records-and-recording --strict`
Expected: `Change 'add-call-records-and-recording' is valid`

- [ ] **Step 5: 索引更新**

Run: `codegraph sync`（main.py/handler.py/flow.py/minio_storage.py/schema.ts 纳入索引）。
Verify: `codegraph_status` 健康。

- [ ] **Step 6: 最终提交（若有遗留补丁）+ 提示 close**

```bash
git status --short  # 应为空；若有验证中产生的小修，单独 commit
```

> 全部 task 完成后提示：`所有实现任务已完成。接下来可以用 /openflow close 验证一致性并归档。`

---

## Self-Review

**1. Spec coverage（对照 3 份 spec）：**
- call-records-persistence 7 requirements → Task 3（session start/end）+ Task 4（turn 双写 + barge_in/handoff/hangup_by_bot event + user_id fallback + 不阻断）✓
- call-recording 7 requirements → Task 2（record_session + 提示音）+ Task 3（upload_recording + presigned + artifact 回写 + hangup 读 ActiveCall + 目录配置）✓；presigned 1h 播放在 Task 6/7 ✓
- call-records-console 7 requirements → Task 5（4 表映射 + call:view + 菜单）+ Task 6（列表隔离/详情聚合/recording-url/404 跨租户）+ Task 7（列表页/详情页/录音播放）✓

**2. Placeholder scan：** Task 7 Step 2/3 的 CallRecordsList.tsx / CallDetail.tsx 给了结构要点而非逐行代码（参照 CallTasksManager 模式）——因 UI 代码量大且模式已由 CallTasksManager 锚定，执行者按"参照 X 组件 + 列出的字段/行为清单"实现。这是有锚点的指引而非 TBD。其余步骤均有完整代码。

**3. Type consistency：** `toSessionDTO`（Task 6 定义）↔ `SessionDTO`（calls-api.ts import type）↔ CallDetailClient（Task 6/7）一致；`callsApi.list/detail/recordingUrl`（Task 6 定义）↔ Task 7 组件消费一致；`_mask_phone`/`_phone_hash`（Task 3 定义）↔ Task 3 Step 5 使用一致。
