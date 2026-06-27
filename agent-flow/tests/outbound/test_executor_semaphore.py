"""OutboundExecutor 并发信号量管理单元测试。

验证 _semaphore_for 的 limit 变更检测基于记录的 limit 原值，而非 Semaphore._value
（后者随 acquire/release 变化，会被误判为 limit 变更导致每 tick 重建满信号量、
使 concurrent_limit 并发上限失效）。

构造 OutboundExecutor 注入假的 ESL/Settings，只测同步的 _semaphore_for，不跑 tick。
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))

from outbound.executor import OutboundExecutor  # noqa: E402


def _task(task_id: int, concurrent_limit: int):
    return SimpleNamespace(id=task_id, concurrent_limit=concurrent_limit)


def _make_executor():
    return OutboundExecutor(esl=object(), settings=SimpleNamespace())


def test_creates_semaphore_with_limit():
    ex = _make_executor()
    sem = ex._semaphore_for(_task(1, concurrent_limit=3))
    assert isinstance(sem, asyncio.Semaphore)
    assert sem._value == 3


def test_reuses_same_semaphore_across_calls():
    ex = _make_executor()
    task = _task(1, concurrent_limit=2)
    sem1 = ex._semaphore_for(task)
    sem2 = ex._semaphore_for(task)
    assert sem1 is sem2


def test_not_rebuilt_after_acquire():
    """回归点：acquire 消耗许可后，_value 递减，但不应触发重建。"""
    ex = _make_executor()
    task = _task(1, concurrent_limit=2)
    sem1 = ex._semaphore_for(task)
    # 模拟一个 originate 在跑：acquire 一个许可，_value 从 2 → 1
    # 旧 bug：1 != 2 → 误判 limit 变更 → 重建满信号量，并发上限失效
    asyncio.new_event_loop().run_until_complete(sem1.acquire())
    assert sem1._value == 1
    sem2 = ex._semaphore_for(task)
    assert sem1 is sem2
    assert sem2._value == 1


def test_rebuilt_when_limit_changes():
    """limit 真实变更时才重建信号量。"""
    ex = _make_executor()
    task = _task(1, concurrent_limit=2)
    sem1 = ex._semaphore_for(task)
    task.concurrent_limit = 5
    sem2 = ex._semaphore_for(task)
    assert sem1 is not sem2
    assert sem2._value == 5


def test_limit_floored_to_one():
    """concurrent_limit <= 0 时至少给 1 个许可，避免 Semaphore(0) 永久阻塞。"""
    ex = _make_executor()
    sem = ex._semaphore_for(_task(1, concurrent_limit=0))
    assert sem._value == 1
    sem = ex._semaphore_for(_task(2, concurrent_limit=-3))
    assert sem._value == 1
