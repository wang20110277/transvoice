"""ESL 事件处理 — CHANNEL_ANSWER/HANGUP 回调 + 录音归档 + 外呼结果回写。

从 main.py 抽出，降低主入口文件膨胀。所有逻辑围绕 ESL 事件生命周期：
- CHANNEL_ANSWER: 解析三元组（呼入 DID 解析 / 呼出 channel vars）→ 注册通话
  → audio_fork_start + uuid_record（双声道录音）
- CHANNEL_HANGUP: 会话结束 → 录音归档（fire-and-forget）→ 外呼重拨/终态判定
  → audio_fork_stop + record_stop → 注销通话
"""
import asyncio
import hashlib
import logging
import os
from datetime import datetime

from sqlalchemy import select

from config import settings
from database import async_session
from db.models import InboundRoute
from graph.render import parse_call_target_vars
from storage import minio_storage, repository
from clients.esl import ESLClient
from ws.registry import ActiveCallRegistry

logger = logging.getLogger(__name__)

# 防止 ESL 多连接重复触发 audio_fork_start（answer/hangup 在事件循环内闭环）
_audio_fork_started: set[str] = set()
# 强引用持有 _archive_recording task，防 fire-and-forget 被 GC 回收
_ongoing_archives: set = set()


def _mask_phone(s: str) -> str:
    """手机号脱敏：首3末4，中间掩码（138****5678）。短串原样返回。"""
    return f"{s[:3]}****{s[-4:]}" if len(s) >= 7 else s


def _phone_hash(s: str) -> str:
    """手机号 sha256（脱敏存储，供跨通话关联同一用户；canonical user_id 待 MCP 重启）。"""
    return hashlib.sha256(s.encode()).hexdigest()


def _parse_int_var(value: str | None) -> int | None:
    """ESL channel vars 是字符串；把 None/空/非数字安全解析为 int（失败返回 None）。"""
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hangup_cause_to_outbound_status(hangup_cause: str) -> str:
    """外呼挂断原因 → call_target 状态。

    NORMAL_CLEARING=被叫接通后挂断（接通成功）；NO_ANSWER/RECOVERY_ON_TIMER_EXPIRE=振铃未接；
    其余按失败。重拨与否由 hangup 分支按 redial_strategy 再判。
    """
    if hangup_cause == "NORMAL_CLEARING":
        return "answered"
    if hangup_cause in ("NO_ANSWER", "RECOVERY_ON_TIMER_EXPIRE"):
        return "no_answer"
    return "failed"


async def _resolve_inbound_dimensions(did: str) -> tuple[str, str, str] | None:
    """查 inbound_route 把 DID 解析为 (tenant_id, biz_type, scenario)。

    匹配顺序:精确 did 优先,号段 did_pattern 正则兜底。无匹配返回 None(由调用方回落)。
    """
    if not did:
        return None
    try:
        async with async_session() as session:
            stmt = select(
                InboundRoute.tenant_id, InboundRoute.biz_type, InboundRoute.scenario
            ).where(InboundRoute.did == did, InboundRoute.is_active.is_(True))
            row = (await session.execute(stmt)).first()
            if row:
                return row[0], row[1], row[2]

            # 号段正则兜底
            import re
            stmt2 = select(
                InboundRoute.tenant_id, InboundRoute.biz_type, InboundRoute.scenario,
                InboundRoute.did_pattern,
            ).where(InboundRoute.did_pattern.is_not(None), InboundRoute.is_active.is_(True))
            for r in (await session.execute(stmt2)).all():
                try:
                    if re.fullmatch(r[3], did):
                        return r[0], r[1], r[2]
                except re.error:
                    continue
    except Exception as e:
        logger.error("[%s] inbound_route resolve failed: %s", did, e)
    return None


async def _archive_recording(fs_uuid: str, biz_type: str, tenant_id: str, user_key: str) -> None:
    """挂断后间隔 3s → 读 FS 录音 → 上传 MinIO → insert_artifact(kind='recording')。

    fire-and-forget 由 CHANNEL_HANGUP 调起；任何异常仅记日志，不阻断下一通。
    """
    # 用户要求：挂断后间隔 3 秒再上传（等 FS flush 完 wav）
    await asyncio.sleep(settings.recording_archive_delay_sec)
    path = os.path.join(settings.recordings_dir, f"{fs_uuid}.wav")
    logger.info("[%s] _archive_recording start: %s (exists=%s)",
                fs_uuid, path, os.path.exists(path))
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
        logger.info("[%s] upload_recording returned None (MinIO 未配置或 endpoint=%s)，跳过归档",
                    fs_uuid, settings.minio_endpoint)
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


def register_esl_event_handlers(esl: ESLClient, call_registry: ActiveCallRegistry) -> None:
    """注册 CHANNEL_ANSWER / CHANNEL_HANGUP 事件处理。

    call_registry 由 main.py 持有并传入（WS /media 路由也需查询 active call）。
    """

    async def _on_channel_hangup(event):
        uuid = event.headers.get("Unique-ID", "")
        if not uuid:
            return
        logger.info("[%s] CHANNEL_HANGUP", uuid)
        _audio_fork_started.discard(uuid)

        hangup_cause = event.headers.get("Hangup-Cause", "")
        result_code = event.headers.get("Variable-Hangup-Cause", "")
        end_ts = datetime.now()
        active = call_registry.get(uuid)

        # session end + 录音归档（fire-and-forget，不阻塞 hangup 清理）
        if active:
            try:
                await repository.update_call_session_end(
                    uuid, end_ts, hangup_cause, result_code,
                )
            except Exception as e:
                logger.error("[%s] update_call_session_end failed: %s", uuid, e)
            archive_task = asyncio.create_task(
                _archive_recording(uuid, active.biz_type, active.tenant_id, active.user_key)
            )
            _ongoing_archives.add(archive_task)  # 强引用，防 GC 回收未完成的 task
            archive_task.add_done_callback(_ongoing_archives.discard)
            archive_task.add_done_callback(
                lambda t: t.exception() and logger.error(
                    "[%s] _archive_recording failed: %s", uuid, t.exception(),
                )
            )

        # 外呼结果回写 + 重拨判定：按 Hangup-Cause + redial_strategy 决定重拨 or 终态
        call_target_id = _parse_int_var(event.headers.get("variable_call_target_id"))
        if call_target_id is not None:
            session_id = None
            try:
                sess = await repository.get_call_session_by_fs_uuid(uuid)
                if sess is not None:
                    session_id = sess.id
            except Exception as e:
                logger.error("[%s] get_call_session for target outcome failed: %s", uuid, e)
            try:
                target = await repository.get_call_target(call_target_id)
                if target is not None:
                    redial_strategy = await repository.get_redial_strategy(target.task_id)
                    from outbound.redial import decide_redial
                    decision = decide_redial(
                        hangup_cause, target.attempt_count, target.max_attempts,
                        redial_strategy.get("retry_on_causes", []),
                    )
                    if decision.redial:
                        interval = float(redial_strategy.get("interval_min", 1) or 1)
                        await repository.reset_call_target_for_redial(call_target_id, interval)
                        logger.info("[%s] target %s redial scheduled (cause=%s attempt=%s/%s)",
                                    uuid, call_target_id, hangup_cause,
                                    target.attempt_count + 1, target.max_attempts)
                    else:
                        await repository.update_call_target_outcome(
                            call_target_id, decision.final_status, hangup_cause, session_id,
                        )
                        logger.info("[%s] target %s final=%s (cause=%s)",
                                    uuid, call_target_id, decision.final_status, hangup_cause)
            except Exception as e:
                logger.error("[%s] call_target redial/outcome failed: %s", uuid, e)

        try:
            await esl.audio_fork_stop(uuid)
        except Exception:
            pass
        # 停止 FS 录制并 flush（channel 可能已 hangup，失败忽略；FS 挂断本会自动落盘）
        try:
            await esl.record_stop(uuid)
        except Exception:
            pass
        call_registry.cancel_call(uuid)

    async def _on_channel_answer(event):
        uuid = event.headers.get("Unique-ID", "")
        if not uuid:
            return

        # 防止 ESL 多连接重复触发（set.add 是同步原子操作）
        if uuid in _audio_fork_started:
            logger.info("[%s] CHANNEL_ANSWER duplicate, ignoring", uuid)
            return
        _audio_fork_started.add(uuid)

        # 三维度来源分两路：外呼从 channel vars 注入读（originate 时塞），呼入走 DID 解析。
        # downstream register/audio_fork/record/session 不区分呼入呼出，只认 uuid + 三元组。
        call_task_id: int | None = None
        call_target_id: int | None = None

        if event.headers.get("variable_ai_outbound"):
            # 外呼：三元组 + task/target ID 全从 channel vars 读，跳过 DID 解析
            tenant_id = event.headers.get("variable_tenant_id", "default")
            biz_type = event.headers.get("variable_biz_type", "marketing")
            scenario = event.headers.get("variable_scenario", "default")
            user_key = event.headers.get("variable_user_key", "")
            call_task_id = _parse_int_var(event.headers.get("variable_call_task_id"))
            call_target_id = _parse_int_var(event.headers.get("variable_call_target_id"))
        else:
            # 呼入：DID → (tenant_id, biz_type, scenario)；失败回落 dialplan 静态 variable_biz_type
            biz_type = event.headers.get("variable_biz_type", "marketing")
            user_key = (
                event.headers.get("variable_user_key", "")
                or event.headers.get("Caller-Caller-ID-Number", "")
            )
            did = (
                event.headers.get("variable_did")
                or event.headers.get("Caller-Destination-Number")
                or ""
            )
            resolved = await _resolve_inbound_dimensions(did)
            if resolved:
                tenant_id, biz_type, scenario = resolved
            else:
                tenant_id = "default"
                scenario = "default"
                if did:
                    logger.warning(
                        "[%s] no inbound_route match for did=%s, fallback biz_type=%s",
                        uuid, did, biz_type,
                    )

        logger.info(
            "[%s] CHANNEL_ANSWER tenant=%s biz_type=%s scenario=%s user_key=%s "
            "outbound=%s task=%s target=%s",
            uuid, tenant_id, biz_type, scenario, user_key,
            bool(call_target_id), call_task_id, call_target_id,
        )

        # 外呼：摘机加载 call_target.vars（key:value|key:value 字符串）解析为 dict，透传进 registry → graph state。
        # 呼入无 call_target_id，call_target_vars 恒 {}。parse_call_target_vars 全程容错（空/坏 → {}）。
        call_target_vars: dict = {}
        if call_target_id is not None:
            t = await repository.get_call_target(call_target_id)
            if t is not None:
                call_target_vars = parse_call_target_vars(t.vars)

        call_registry.register(
            uuid, biz_type, user_key, tenant_id=tenant_id, scenario=scenario,
            call_target_vars=call_target_vars,
        )

        # session 写入 PG（fire-and-forget，DB 异常仅记日志不阻断通话）
        try:
            await repository.insert_call_session({
                "call_id": uuid, "fs_uuid": uuid,          # 决策 1：同值
                "user_id": user_key,                        # 本期 fallback（MCP 禁用，见 design §7）
                "biz_type": biz_type, "tenant_id": tenant_id, "scenario": scenario,
                "phone_hash": _phone_hash(user_key), "user_key": user_key,
                "phone_masked": _mask_phone(user_key),
                "start_ts": datetime.now(),
                "recording_notice_played": settings.recording_notice_enabled,
                "call_task_id": call_task_id, "call_target_id": call_target_id,
            })
        except Exception as e:
            logger.error("[%s] insert_call_session failed: %s", uuid, e)

        ws_url = f"ws://{settings.media_ws_host}:{settings.media_ws_port}/media/{uuid}"
        try:
            result = await esl.audio_fork_start(uuid, ws_url, sample_rate=settings.media_sample_rate)
            logger.info("[%s] uuid_audio_fork start → %s: %s", uuid, ws_url, result)

            # FS uuid_record 录整通双声道到标准 recordings 路径（与 _archive_recording 一致）。
            # 顺序关键：必须在 audio_fork_start 之后发起，record bug 才排在 WRITE_REPLACE 之后，
            # 从而 tap 到被 dub 后的 AI 下行音频（L=caller / R=AI）。失败 non-fatal，该通无录音。
            fs_rec_path = os.path.join(settings.recordings_dir, f"{uuid}.wav")
            try:
                await esl.record_start(uuid, fs_rec_path)
                logger.info("[%s] uuid_record start → %s", uuid, fs_rec_path)
            except Exception as e:
                logger.error("[%s] uuid_record start failed (non-fatal): %s", uuid, e)
        except Exception as e:
            logger.error("[%s] uuid_audio_fork start failed: %s", uuid, e)
            call_registry.unregister(uuid)

    esl.on_event("CHANNEL_HANGUP", _on_channel_hangup)
    esl.on_event("CHANNEL_ANSWER", _on_channel_answer)
