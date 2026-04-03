"""Task Queue with priority system for worker cycle scheduling.

Architecture: Producer-Consumer with priorities.
Each cycle is a Producer that schedules tasks into a PriorityQueue.
A pool of Consumers executes tasks by priority.
Cycles are isolated — a crash in one consumer doesn't kill others.

Priority levels:
  P0: KILL_SWITCH, EMERGENCY_CLOSE
  P1: RISK_CHECK, REGIME_DETECTION
  P2: TRADE_SIGNAL (FX, EU, US, crypto)
  P3: REBALANCE, RECONCILIATION
  P4: MONITORING, HEARTBEAT, EOD
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Callable, Optional

logger = logging.getLogger("worker.task_queue")


class TaskPriority(IntEnum):
    CRITICAL = 0     # Kill switch, emergency close
    HIGH = 1         # Risk checks, regime detection
    NORMAL = 2       # Trade signals
    LOW = 3          # Rebalance, reconciliation
    BACKGROUND = 4   # Monitoring, heartbeat, EOD cleanup


@dataclass(order=True)
class Task:
    priority: int
    scheduled_at: float = field(compare=False, default_factory=time.monotonic)
    name: str = field(compare=False, default="")
    callable: Callable = field(compare=False, default=None)
    args: tuple = field(default=(), compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)
    timeout_seconds: float = field(default=60.0, compare=False)
    max_retries: int = field(default=0, compare=False)


@dataclass
class TaskResult:
    task_name: str
    priority: int
    elapsed_seconds: float
    success: bool
    error: Optional[str] = None
    retries_used: int = 0


class TaskQueue:
    """Priority queue with worker thread pool for isolated task execution."""

    def __init__(
        self,
        num_workers: int = 3,
        metrics_callback: Optional[Callable] = None,
    ):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._workers: list[threading.Thread] = []
        self._running = threading.Event()
        self._metrics_callback = metrics_callback
        self._num_workers = num_workers
        self._results_lock = threading.Lock()
        self._recent_results: list[TaskResult] = []
        self._tasks_completed = 0
        self._tasks_failed = 0

    def submit(self, task: Task) -> None:
        """Submit a task to the queue. Thread-safe."""
        if not self._running.is_set():
            logger.warning(f"TaskQueue not running, dropping task: {task.name}")
            return
        self._queue.put(task)
        logger.debug(f"Task submitted: {task.name} (P{task.priority})")

    def submit_critical(self, name: str, callable: Callable, **kwargs) -> None:
        """Shortcut for CRITICAL priority tasks (kill switch, emergency)."""
        self.submit(Task(
            priority=TaskPriority.CRITICAL,
            name=name,
            callable=callable,
            kwargs=kwargs,
            timeout_seconds=30.0,
        ))

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def tasks_completed(self) -> int:
        return self._tasks_completed

    @property
    def tasks_failed(self) -> int:
        return self._tasks_failed

    def get_recent_results(self, n: int = 20) -> list[TaskResult]:
        with self._results_lock:
            return list(self._recent_results[-n:])

    def start(self) -> None:
        """Start worker threads."""
        if self._running.is_set():
            logger.warning("TaskQueue already running")
            return
        self._running.set()
        for i in range(self._num_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"tq-worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        logger.info(
            f"TaskQueue started with {self._num_workers} workers"
        )

    def stop(self, timeout: float = 30.0) -> None:
        """Graceful shutdown — finish current tasks, reject new ones."""
        if not self._running.is_set():
            return
        logger.info("TaskQueue stopping...")
        self._running.clear()
        for w in self._workers:
            w.join(timeout=timeout)
        self._workers.clear()
        logger.info("TaskQueue stopped")

    def drain(self, timeout: float = 60.0) -> None:
        """Wait until all queued tasks are processed."""
        self._queue.join()

    def _worker_loop(self) -> None:
        """Main loop for each worker thread."""
        thread_name = threading.current_thread().name
        logger.debug(f"{thread_name} started")

        while self._running.is_set():
            try:
                task = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            result = self._execute_task(task, thread_name)
            self._record_result(result)
            self._queue.task_done()

        logger.debug(f"{thread_name} stopped")

    def _execute_task(self, task: Task, thread_name: str) -> TaskResult:
        """Execute a single task with timeout and retry logic."""
        start = time.monotonic()
        retries = 0
        last_error = None

        while retries <= task.max_retries:
            try:
                # Execute with timeout via a separate thread
                if task.timeout_seconds > 0:
                    result_container: dict[str, Any] = {}
                    exc_container: list = []

                    def _run():
                        try:
                            task.callable(*task.args, **task.kwargs)
                            result_container["ok"] = True
                        except Exception as e:
                            exc_container.append(e)

                    runner = threading.Thread(target=_run, daemon=True)
                    runner.start()
                    runner.join(timeout=task.timeout_seconds)

                    if runner.is_alive():
                        elapsed = time.monotonic() - start
                        logger.error(
                            f"Task {task.name} TIMEOUT after {elapsed:.1f}s "
                            f"(limit: {task.timeout_seconds}s)"
                        )
                        last_error = f"Timeout after {task.timeout_seconds}s"
                        retries += 1
                        continue

                    if exc_container:
                        raise exc_container[0]
                else:
                    task.callable(*task.args, **task.kwargs)

                elapsed = time.monotonic() - start
                logger.debug(
                    f"{thread_name} completed {task.name} "
                    f"in {elapsed:.2f}s (P{task.priority})"
                )
                return TaskResult(
                    task_name=task.name,
                    priority=task.priority,
                    elapsed_seconds=elapsed,
                    success=True,
                    retries_used=retries,
                )

            except Exception as e:
                last_error = str(e)
                retries += 1
                if retries <= task.max_retries:
                    backoff = min(2 ** retries, 10)
                    logger.warning(
                        f"Task {task.name} failed (attempt {retries}/"
                        f"{task.max_retries + 1}): {e}. "
                        f"Retrying in {backoff}s..."
                    )
                    time.sleep(backoff)

        elapsed = time.monotonic() - start
        logger.error(
            f"Task {task.name} FAILED after {retries} attempts: {last_error}"
        )
        return TaskResult(
            task_name=task.name,
            priority=task.priority,
            elapsed_seconds=elapsed,
            success=False,
            error=last_error,
            retries_used=retries,
        )

    def _record_result(self, result: TaskResult) -> None:
        """Record task result for metrics and history."""
        with self._results_lock:
            self._recent_results.append(result)
            if len(self._recent_results) > 100:
                self._recent_results.pop(0)
            if result.success:
                self._tasks_completed += 1
            else:
                self._tasks_failed += 1

        if self._metrics_callback:
            try:
                self._metrics_callback(
                    task_name=result.task_name,
                    priority=result.priority,
                    elapsed_seconds=result.elapsed_seconds,
                    success=result.success,
                    error=result.error,
                )
            except Exception as e:
                logger.warning(f"Metrics callback error: {e}")
