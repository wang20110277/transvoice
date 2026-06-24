"""数据访问层 - SQLAlchemy 2.0 async ORM"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from db.models import CallSession, CallTarget, CallTask, CallTurn, CallEvent, CallArtifact, PromptConfig
from database import async_session

logger = logging.getLogger(__name__)


async def insert_call_session(state_dict: dict) -> None:
    try:
        async with async_session() as session:
            session.add(CallSession(**state_dict))
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"insert_call_session 失败: {e}")
        raise


async def update_call_session_end(fs_uuid: str, end_ts: datetime, hangup_cause: str, result_code: str) -> None:
    try:
        async with async_session() as session:
            stmt = (
                update(CallSession)
                .where(CallSession.fs_uuid == fs_uuid)
                .values(end_ts=end_ts, hangup_cause=hangup_cause, result_code=result_code, update_time=datetime.now())
            )
            await session.execute(stmt)
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"update_call_session_end 失败: {e}")
        raise


async def insert_turn(call_id: str, fs_uuid: str, biz_type: str, user_id: str,
                      user_key: str, role: str, text: str, asr_conf: float | None = None) -> None:
    try:
        async with async_session() as session:
            session.add(CallTurn(
                call_id=call_id, fs_uuid=fs_uuid, biz_type=biz_type,
                user_id=user_id, user_key=user_key, role=role, text=text,
                asr_conf=asr_conf, ts=datetime.now(),
            ))
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"insert_turn 失败: {e}")
        raise


async def insert_event(call_id: str, fs_uuid: str, biz_type: str, user_id: str,
                       user_key: str, event_type: str, payload: dict) -> None:
    try:
        async with async_session() as session:
            session.add(CallEvent(
                call_id=call_id, fs_uuid=fs_uuid, biz_type=biz_type,
                user_id=user_id, user_key=user_key, event_type=event_type,
                payload=payload, ts=datetime.now(),
            ))
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"insert_event 失败: {e}")
        raise


async def insert_artifact(call_id: str, fs_uuid: str, biz_type: str, user_id: str,
                          user_key: str, kind: str, storage: str, uri: str,
                          size_bytes: int | None = None, content_type: str | None = None) -> None:
    try:
        async with async_session() as session:
            session.add(CallArtifact(
                call_id=call_id, fs_uuid=fs_uuid, biz_type=biz_type,
                user_id=user_id, user_key=user_key, kind=kind,
                storage=storage, uri=uri, size_bytes=size_bytes,
                content_type=content_type, ts=datetime.now(),
            ))
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"insert_artifact 失败: {e}")
        raise


async def get_call_session_by_fs_uuid(fs_uuid: str) -> CallSession | None:
    try:
        async with async_session() as session:
            stmt = select(CallSession).where(CallSession.fs_uuid == fs_uuid)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"get_call_session_by_fs_uuid 失败: {e}")
        return None


# ── 外呼号码清单（call_target）──

async def claim_call_target_for_dial(target_id: int) -> bool:
    """CAS: pending→dialing。返回 True 表示抢占成功（仅执行器并发槽位内调用）。

    用 UPDATE...WHERE status='pending' 原子抢占，避免多并发 originate 重复拨同一号码。
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                update(CallTarget)
                .where(CallTarget.id == target_id, CallTarget.status == "pending")
                .values(status="dialing", update_time=datetime.now())
            )
            await session.commit()
            return result.rowcount == 1
    except SQLAlchemyError as e:
        logger.error(f"claim_call_target_for_dial 失败: {e}")
        return False


async def update_call_target_outcome(
    target_id: int, status: str, hangup_cause: str | None, call_session_id: int | None,
) -> None:
    """回写外呼结果（挂断时调用）。status 为终态/中间态，hangup_cause 记录原因。"""
    try:
        async with async_session() as session:
            await session.execute(
                update(CallTarget)
                .where(CallTarget.id == target_id)
                .values(
                    status=status,
                    last_hangup_cause=hangup_cause,
                    last_call_session_id=call_session_id,
                    update_time=datetime.now(),
                )
            )
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"update_call_target_outcome 失败: {e}")
        raise


async def reset_call_target_for_redial(target_id: int, interval_min: float) -> None:
    """置回 pending 准备重拨：attempt_count+1，next_attempt_ts 推后 interval_min 分钟。"""
    try:
        async with async_session() as session:
            await session.execute(
                update(CallTarget)
                .where(CallTarget.id == target_id)
                .values(
                    status="pending",
                    attempt_count=CallTarget.attempt_count + 1,
                    next_attempt_ts=datetime.now() + timedelta(minutes=interval_min),
                    update_time=datetime.now(),
                )
            )
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"reset_call_target_for_redial 失败: {e}")
        raise


async def get_call_target(target_id: int) -> CallTarget | None:
    try:
        async with async_session() as session:
            result = await session.execute(
                select(CallTarget).where(CallTarget.id == target_id)
            )
            return result.scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"get_call_target 失败: {e}")
        return None


# ── 外呼执行器查询 ──

async def list_running_tasks() -> list[CallTask]:
    """所有 status='running' 的任务（执行器每个 tick 扫描）。"""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(CallTask).where(CallTask.status == "running")
            )
            return list(result.scalars().all())
    except SQLAlchemyError as e:
        logger.error(f"list_running_tasks 失败: {e}")
        return []


async def list_dialable_targets(task_id: int, limit: int) -> list[CallTarget]:
    """可拨打的号码：status=pending 且到下次可拨时间。

    next_attempt_ts 为 NULL（从未拨）或 <= now（重拨退避到期）。按 id 顺序取 limit 条。
    """
    try:
        async with async_session() as session:
            now = datetime.now()
            result = await session.execute(
                select(CallTarget).where(
                    CallTarget.task_id == task_id,
                    CallTarget.status == "pending",
                    (CallTarget.next_attempt_ts.is_(None))
                    | (CallTarget.next_attempt_ts <= now),
                ).order_by(CallTarget.id).limit(limit)
            )
            return list(result.scalars().all())
    except SQLAlchemyError as e:
        logger.error(f"list_dialable_targets 失败: {e}")
        return []


async def has_pending_targets(task_id: int) -> bool:
    """任务是否还有 pending 号码（任务完成判定用）。"""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(CallTarget.id).where(
                    CallTarget.task_id == task_id,
                    CallTarget.status.in_(("pending", "dialing")),
                ).limit(1)
            )
            return result.first() is not None
    except SQLAlchemyError as e:
        logger.error(f"has_pending_targets 失败: {e}")
        return False


async def mark_task_completed(task_id: int) -> None:
    """所有号码终态且无 pending/dialing → call_task.status=completed。"""
    try:
        async with async_session() as session:
            if await has_pending_targets(task_id):
                return
            await session.execute(
                update(CallTask)
                .where(CallTask.id == task_id, CallTask.status == "running")
                .values(status="completed", update_time=datetime.now())
            )
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"mark_task_completed 失败: {e}")


async def revert_target_to_pending(target_id: int) -> None:
    """未成功 originate 的号码从 dialing 回退为 pending（不增 attempt_count，下个 tick 重试）。"""
    try:
        async with async_session() as session:
            await session.execute(
                update(CallTarget)
                .where(CallTarget.id == target_id, CallTarget.status == "dialing")
                .values(status="pending", update_time=datetime.now())
            )
            await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"revert_target_to_pending 失败: {e}")


async def get_prompt_dimensions(prompt_id: int) -> tuple[str, str, str] | None:
    """从 prompt_config 反查 (tenant_id, biz_type, scenario) — 外呼三元组来源。

    外呼无 DID，三元组由 call_task.prompt_id 反查（区别于呼入的 DID→inbound_route）。
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(
                    PromptConfig.tenant_id, PromptConfig.biz_type, PromptConfig.scenario,
                ).where(PromptConfig.id == prompt_id)
            )
            row = result.first()
            return (row[0], row[1], row[2]) if row else None
    except SQLAlchemyError as e:
        logger.error(f"get_prompt_dimensions 失败: {e}")
        return None


async def get_redial_strategy(task_id: int) -> dict:
    """查 call_task.redial_strategy JSONB（{max_retries, interval_min, retry_on_causes}）。

    缺失/异常返回空 dict（decide_redial 据此不重拨，安全降级）。
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(CallTask.redial_strategy).where(CallTask.id == task_id)
            )
            row = result.scalar_one_or_none()
            return dict(row) if row else {}
    except SQLAlchemyError as e:
        logger.error(f"get_redial_strategy 失败: {e}")
        return {}
