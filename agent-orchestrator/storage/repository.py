"""数据访问层 - SQLAlchemy 2.0 async ORM"""
import logging
from datetime import datetime
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from db.models import CallSession, CallTurn, CallEvent, CallArtifact
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
