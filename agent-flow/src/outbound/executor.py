"""外呼执行器 — 进程内 asyncio 单例，跑在 agent-flow lifespan。

每个调度 tick（outbound_scheduler_tick_sec）扫描 status=running 的任务：
  校验 allowed_hours 时段 → 算每任务可用并发槽位 → 拉 pending&可拨号码 →
  claim（pending→dialing，CAS 防并发重复拨）→ Semaphore 内 bgapi originate（fire-and-forget）。

复用 inbound 管线：originate 注入 ai_outbound 三元组 channel vars，摘机触发 CHANNEL_ANSWER
→ answer 处理器 outbound 分支接管。挂断时 hangup 处理器按 Hangup-Cause 回写 call_target。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from clients.esl import ESLClient
from config import Settings
from outbound.originate import OutboundContext, OutboundTarget, build_originate_command
from outbound.schedule import is_within_allowed_hours
from storage import repository
from db.models import CallTask

logger = logging.getLogger(__name__)


class OutboundExecutor:
    """进程内外呼执行器单例。lifespan 启停，复用 ESL 连接。"""

    def __init__(self, esl: ESLClient, settings: Settings) -> None:
        self._esl = esl
        self._settings = settings
        self._semaphores: dict[int, asyncio.Semaphore] = {}  # task_id → 并发信号量
        self._tick_task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        if self._tick_task is not None:
            return
        self._stopping = False
        self._tick_task = asyncio.create_task(self._run_loop())
        logger.info("OutboundExecutor started (tick=%ss)", self._settings.outbound_scheduler_tick_sec)

    async def stop(self) -> None:
        self._stopping = True
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
        logger.info("OutboundExecutor stopped")

    def _semaphore_for(self, task: CallTask) -> asyncio.Semaphore:
        """每任务一个 Semaphore(concurrent_limit)；limit 变更后重建（简单策略）。"""
        sem = self._semaphores.get(task.id)
        if sem is None or sem._value != task.concurrent_limit:  # noqa: SLF001 — limit 变更检测
            sem = asyncio.Semaphore(max(1, task.concurrent_limit))
            self._semaphores[task.id] = sem
        return sem

    async def _run_loop(self) -> None:
        """周期 tick，直到 stop。"""
        while not self._stopping:
            try:
                await self._tick()
            except Exception as e:
                logger.error("OutboundExecutor tick error: %s", e)
            await asyncio.sleep(self._settings.outbound_scheduler_tick_sec)

    async def _tick(self) -> None:
        """单次调度：扫 running 任务，对每个发起可拨号码的外呼。"""
        tasks = await repository.list_running_tasks()
        for task in tasks:
            await self._drive_task(task)

    async def _drive_task(self, task: CallTask) -> None:
        """驱动单个任务：时段校验 → 并发槽位 → originate。"""
        now = datetime.now()
        if not is_within_allowed_hours(task.allowed_hours, now):
            return  # 窗口外不发起新外呼（不中断在通话）

        # 三元组从 prompt_config 反查（外呼无 DID，区别于呼入 DID→inbound_route）
        dims = await repository.get_prompt_dimensions(task.prompt_id)
        if dims is None:
            logger.warning("task %s prompt_id=%s 无有效三元组，跳过", task.id, task.prompt_id)
            return
        tenant_id, biz_type, scenario = dims

        sem = self._semaphore_for(task)
        # 估算可拨数量：全局上限与 per-task 信号量剩余取小（0=不限）
        global_limit = self._settings.outbound_global_concurrency
        if global_limit > 0:
            max_batch = global_limit
        else:
            max_batch = max(1, task.concurrent_limit)

        targets = await repository.list_dialable_targets(task.id, max_batch)
        if not targets:
            # 无可拨号码 → 判定任务是否完成
            await repository.mark_task_completed(task.id)
            return

        ctx = OutboundContext(
            tenant_id=tenant_id, biz_type=biz_type, scenario=scenario, task_id=task.id,
        )
        for target in targets:
            # claim 成功才拨（CAS：pending→dialing，防并发重复）
            if not await repository.claim_call_target_for_dial(target.id):
                continue
            # 非阻塞获取槽位；拿不到就跳过本 tick（下个 tick 再来）
            if sem.locked():
                # 槽位满，回退为 pending 等下轮（不浪费已 claim 状态）
                await repository.revert_target_to_pending(target.id)
                break
            await sem.acquire()
            asyncio.create_task(self._originate_and_release(ctx, target, sem))

    async def _originate_and_release(self, ctx: OutboundContext, target, sem: asyncio.Semaphore) -> None:
        """发起 originate（fire-and-forget），结束后释放信号量。"""
        try:
            tgt = OutboundTarget(
                target_id=target.id, user_key=target.user_key, phone=target.user_key,
            )
            cmd = build_originate_command(
                tgt, ctx,
                endpoint_template=self._settings.outbound_endpoint_template,
                domain=self._settings.outbound_domain,
                codec_string=self._settings.outbound_codec_string,
                caller_id=self._settings.outbound_caller_id,
            )
            result = await self._esl.bgapi(cmd)
            logger.info("[%s] outbound originate task=%s target=%s → %s",
                        target.user_key, ctx.task_id, target.id, result)
        except Exception as e:
            logger.error("outbound originate failed task=%s target=%s: %s",
                         ctx.task_id, target.id, e)
            await repository.revert_target_to_pending(target.id)
        finally:
            sem.release()
