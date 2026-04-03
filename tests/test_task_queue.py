"""Tests for TaskQueue (R1-01) and CycleRunner (R1-02)."""

import threading
import time

import pytest

from core.worker.task_queue import Task, TaskPriority, TaskQueue, TaskResult
from core.worker.cycle_runner import CycleHealth, CycleRunner


# ═══════════════════════════════════════════════════════════════════
# TaskQueue tests
# ═══════════════════════════════════════════════════════════════════


class TestTaskPriority:
    def test_priority_ordering(self):
        assert TaskPriority.CRITICAL < TaskPriority.HIGH
        assert TaskPriority.HIGH < TaskPriority.NORMAL
        assert TaskPriority.NORMAL < TaskPriority.LOW
        assert TaskPriority.LOW < TaskPriority.BACKGROUND

    def test_priority_values(self):
        assert TaskPriority.CRITICAL == 0
        assert TaskPriority.BACKGROUND == 4


class TestTask:
    def test_task_creation(self):
        t = Task(priority=TaskPriority.NORMAL, name="test")
        assert t.priority == 2
        assert t.name == "test"
        assert t.timeout_seconds == 60.0
        assert t.max_retries == 0

    def test_task_ordering(self):
        t1 = Task(priority=TaskPriority.CRITICAL, name="critical")
        t2 = Task(priority=TaskPriority.NORMAL, name="normal")
        assert t1 < t2

    def test_task_with_callable(self):
        called = []
        t = Task(
            priority=TaskPriority.NORMAL,
            name="test",
            callable=lambda: called.append(1),
        )
        t.callable()
        assert called == [1]


class TestTaskQueueBasic:
    def test_start_stop(self):
        tq = TaskQueue(num_workers=1)
        tq.start()
        assert tq.depth == 0
        tq.stop(timeout=5)

    def test_submit_and_execute(self):
        results = []
        tq = TaskQueue(num_workers=1)
        tq.start()
        tq.submit(Task(
            priority=TaskPriority.NORMAL,
            name="test_task",
            callable=lambda: results.append("done"),
        ))
        time.sleep(0.5)
        tq.stop(timeout=5)
        assert results == ["done"]
        assert tq.tasks_completed == 1
        assert tq.tasks_failed == 0

    def test_priority_execution_order(self):
        """Higher priority tasks execute before lower priority."""
        order = []
        barrier = threading.Event()

        def block_worker():
            barrier.wait(timeout=5)

        def record(name):
            order.append(name)

        tq = TaskQueue(num_workers=1)
        tq.start()

        # Block the single worker
        tq.submit(Task(
            priority=TaskPriority.BACKGROUND,
            name="blocker",
            callable=block_worker,
        ))
        time.sleep(0.1)  # Let it pick up the blocker

        # Submit tasks in reverse priority order
        tq.submit(Task(priority=TaskPriority.LOW, name="low",
                        callable=lambda: record("low")))
        tq.submit(Task(priority=TaskPriority.CRITICAL, name="critical",
                        callable=lambda: record("critical")))
        tq.submit(Task(priority=TaskPriority.NORMAL, name="normal",
                        callable=lambda: record("normal")))

        # Release the blocker
        barrier.set()
        time.sleep(1.0)
        tq.stop(timeout=5)

        # Critical should execute first
        assert order[0] == "critical"

    def test_multiple_workers(self):
        """Multiple workers process tasks concurrently."""
        results = []
        lock = threading.Lock()

        def slow_task(name):
            time.sleep(0.2)
            with lock:
                results.append(name)

        tq = TaskQueue(num_workers=3)
        tq.start()

        for i in range(6):
            tq.submit(Task(
                priority=TaskPriority.NORMAL,
                name=f"task_{i}",
                callable=slow_task,
                args=(f"task_{i}",),
            ))

        time.sleep(1.5)
        tq.stop(timeout=5)
        assert len(results) == 6

    def test_submit_when_not_running(self):
        tq = TaskQueue(num_workers=1)
        # Not started — task should be dropped
        tq.submit(Task(priority=TaskPriority.NORMAL, name="dropped",
                        callable=lambda: None))

    def test_double_start(self):
        tq = TaskQueue(num_workers=1)
        tq.start()
        tq.start()  # Should warn, not crash
        tq.stop(timeout=5)

    def test_stop_without_start(self):
        tq = TaskQueue(num_workers=1)
        tq.stop()  # Should not crash


class TestTaskQueueErrorHandling:
    def test_task_exception_doesnt_kill_worker(self):
        """An exception in one task doesn't prevent others from running."""
        results = []

        def failing():
            raise ValueError("boom")

        def succeeding():
            results.append("ok")

        tq = TaskQueue(num_workers=1)
        tq.start()

        tq.submit(Task(priority=TaskPriority.NORMAL, name="fail",
                        callable=failing))
        time.sleep(0.2)
        tq.submit(Task(priority=TaskPriority.NORMAL, name="succeed",
                        callable=succeeding))
        time.sleep(0.5)
        tq.stop(timeout=5)

        assert results == ["ok"]
        assert tq.tasks_failed == 1
        assert tq.tasks_completed == 1

    def test_task_timeout(self):
        """Tasks that exceed timeout are recorded as failures."""
        tq = TaskQueue(num_workers=1)
        tq.start()

        tq.submit(Task(
            priority=TaskPriority.NORMAL,
            name="slow_task",
            callable=lambda: time.sleep(10),
            timeout_seconds=0.5,
        ))
        time.sleep(2.0)
        tq.stop(timeout=5)

        assert tq.tasks_failed == 1
        recent = tq.get_recent_results()
        assert len(recent) == 1
        assert not recent[0].success
        assert "Timeout" in (recent[0].error or "")

    def test_task_retry(self):
        """Tasks with max_retries retry on failure."""
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise ValueError("not yet")

        tq = TaskQueue(num_workers=1)
        tq.start()

        tq.submit(Task(
            priority=TaskPriority.NORMAL,
            name="flaky_task",
            callable=flaky,
            max_retries=3,
            timeout_seconds=10,
        ))
        time.sleep(10)
        tq.stop(timeout=5)

        assert len(attempts) == 3
        assert tq.tasks_completed == 1


class TestTaskQueueMetrics:
    def test_metrics_callback(self):
        metrics = []

        def callback(**kwargs):
            metrics.append(kwargs)

        tq = TaskQueue(num_workers=1, metrics_callback=callback)
        tq.start()
        tq.submit(Task(
            priority=TaskPriority.NORMAL,
            name="tracked",
            callable=lambda: None,
        ))
        time.sleep(0.5)
        tq.stop(timeout=5)

        assert len(metrics) == 1
        assert metrics[0]["task_name"] == "tracked"
        assert metrics[0]["success"] is True

    def test_recent_results(self):
        tq = TaskQueue(num_workers=1)
        tq.start()
        for i in range(5):
            tq.submit(Task(
                priority=TaskPriority.NORMAL,
                name=f"task_{i}",
                callable=lambda: None,
            ))
        time.sleep(1.0)
        tq.stop(timeout=5)

        results = tq.get_recent_results()
        assert len(results) == 5

    def test_submit_critical(self):
        results = []
        tq = TaskQueue(num_workers=1)
        tq.start()
        tq.submit_critical("emergency", lambda: results.append("done"))
        time.sleep(0.5)
        tq.stop(timeout=5)
        assert results == ["done"]


# ═══════════════════════════════════════════════════════════════════
# CycleRunner tests
# ═══════════════════════════════════════════════════════════════════


class TestCycleRunnerBasic:
    def test_successful_run(self):
        called = []
        runner = CycleRunner("test_cycle", lambda: called.append(1))
        metrics = runner.run()
        assert called == [1]
        assert metrics.last_success is True
        assert metrics.health == CycleHealth.HEALTHY
        assert metrics.total_runs == 1
        assert metrics.total_failures == 0

    def test_run_returns_metrics(self):
        runner = CycleRunner("test", lambda: None)
        m = runner.run()
        assert m.name == "test"
        assert m.last_duration_seconds >= 0
        assert m.avg_duration_seconds >= 0

    def test_run_with_args(self):
        results = []
        runner = CycleRunner("test", lambda x, y: results.append(x + y))
        runner.run(2, 3)
        assert results == [5]


class TestCycleRunnerErrorBoundary:
    def test_exception_caught(self):
        """CycleRunner catches exceptions and doesn't re-raise."""
        def failing():
            raise ValueError("boom")

        runner = CycleRunner("fail_cycle", failing)
        metrics = runner.run()  # Should NOT raise
        assert metrics.last_success is False
        assert metrics.health == CycleHealth.DEGRADED
        assert metrics.consecutive_failures == 1

    def test_degraded_after_1_failure(self):
        runner = CycleRunner("test", lambda: (_ for _ in ()).throw(ValueError("x")))
        runner.run()
        assert runner.health == CycleHealth.DEGRADED

    def test_failed_after_3_consecutive(self):
        def failing():
            raise RuntimeError("nope")

        runner = CycleRunner("test", failing, max_consecutive_failures=3)
        runner.run()
        assert runner.health == CycleHealth.DEGRADED
        runner.run()
        assert runner.health == CycleHealth.DEGRADED
        runner.run()
        assert runner.health == CycleHealth.FAILED

    def test_recovery_resets_health(self):
        call_count = [0]

        def sometimes_fails():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ValueError("fail")

        runner = CycleRunner("test", sometimes_fails)
        runner.run()  # fail 1
        runner.run()  # fail 2
        assert runner.health == CycleHealth.DEGRADED
        runner.run()  # success
        assert runner.health == CycleHealth.HEALTHY
        assert runner.metrics.consecutive_failures == 0

    def test_total_counters(self):
        call_count = [0]

        def sometimes():
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                raise ValueError("even")

        runner = CycleRunner("test", sometimes)
        for _ in range(6):
            runner.run()
        assert runner.metrics.total_runs == 6
        assert runner.metrics.total_failures == 3


class TestCycleRunnerAlerting:
    def test_alert_on_first_failure(self):
        alerts = []
        runner = CycleRunner(
            "test",
            lambda: (_ for _ in ()).throw(ValueError("x")),
            alert_callback=lambda msg: alerts.append(msg),
        )
        runner.run()
        assert len(alerts) == 1
        assert "erreur" in alerts[0]

    def test_alert_on_failed_state(self):
        alerts = []
        runner = CycleRunner(
            "test",
            lambda: (_ for _ in ()).throw(ValueError("x")),
            max_consecutive_failures=2,
            alert_callback=lambda msg: alerts.append(msg),
        )
        runner.run()  # 1st fail — degraded alert
        runner.run()  # 2nd fail — FAILED alert
        assert len(alerts) == 2
        assert "FAILED" in alerts[1]

    def test_no_alert_on_success(self):
        alerts = []
        runner = CycleRunner(
            "test", lambda: None,
            alert_callback=lambda msg: alerts.append(msg),
        )
        runner.run()
        assert len(alerts) == 0

    def test_metrics_callback(self):
        metrics_data = []
        runner = CycleRunner(
            "test", lambda: None,
            metrics_callback=lambda name, dur, ok, err: metrics_data.append(
                (name, ok)
            ),
        )
        runner.run()
        assert metrics_data == [("test", True)]


class TestCycleRunnerDuration:
    def test_avg_duration(self):
        runner = CycleRunner("test", lambda: time.sleep(0.01))
        for _ in range(5):
            runner.run()
        assert runner.metrics.avg_duration_seconds > 0.005
        assert runner.metrics.avg_duration_seconds < 1.0

    def test_rolling_window(self):
        """Duration rolling window is capped at 20."""
        runner = CycleRunner("test", lambda: None)
        for _ in range(30):
            runner.run()
        # Internal _durations should be 20 max
        assert len(runner._durations) == 20

    def test_reset(self):
        runner = CycleRunner(
            "test",
            lambda: (_ for _ in ()).throw(ValueError("x")),
        )
        runner.run()
        runner.run()
        assert runner.health == CycleHealth.DEGRADED
        runner.reset()
        assert runner.health == CycleHealth.HEALTHY
        assert runner.metrics.consecutive_failures == 0


class TestCycleMetricsDict:
    def test_to_dict(self):
        runner = CycleRunner("test", lambda: None)
        runner.run()
        d = runner.metrics.to_dict()
        assert d["name"] == "test"
        assert d["health"] == "HEALTHY"
        assert d["last_success"] is True
        assert isinstance(d["last_duration_seconds"], float)
