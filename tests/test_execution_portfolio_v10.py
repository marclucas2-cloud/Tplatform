"""Tests for execution monitoring and portfolio state — V10.

Covers:
  - core/execution/execution_monitor.py  (ExecutionMonitor, ExecutionMetrics, ExecutionAlert)
  - core/portfolio/portfolio_state.py    (PortfolioStateEngine, BrokerState, PortfolioState)
  - core/portfolio/live_logger.py        (LiveSnapshotLogger)

40+ tests total, using tmp_path for file/DB persistence and mocks for brokers.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from core.execution.execution_monitor import (
    ExecutionAlert,
    ExecutionMetrics,
    ExecutionMonitor,
)
from core.portfolio.live_logger import LiveSnapshotLogger
from core.portfolio.portfolio_state import (
    BrokerState,
    PortfolioState,
    PortfolioStateEngine,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _make_broker(
    name: str = "alpaca",
    equity: float = 10_000,
    cash: float = 5_000,
    positions: List[Dict[str, Any]] | None = None,
    paper: bool = True,
) -> MagicMock:
    """Create a mock broker with get_account_info / get_positions."""
    broker = MagicMock()
    broker.get_account_info.return_value = {
        "equity": equity,
        "cash": cash,
        "paper": paper,
    }
    broker.get_positions.return_value = positions or []
    return broker


def _make_smart_router(brokers: Dict[str, MagicMock]) -> MagicMock:
    router = MagicMock()
    router.get_all_brokers.return_value = brokers
    return router


def _record_filled_order(
    monitor: ExecutionMonitor,
    trade_id: str = "t1",
    strategy: str = "momentum_v1",
    symbol: str = "AAPL",
    requested: float = 100.0,
    filled: float = 100.05,
    latency: float = 50.0,
    commission: float = 1.0,
    notional: float = 10_000.0,
    is_stop_loss: bool = False,
) -> None:
    monitor.record_order(
        trade_id=trade_id,
        strategy=strategy,
        symbol=symbol,
        side="BUY",
        order_type="MARKET",
        requested_price=requested,
        filled_price=filled,
        status="filled",
        latency_ms=latency,
        is_stop_loss=is_stop_loss,
        commission=commission,
        notional=notional,
        quantity=100,
    )


def _record_rejected_order(
    monitor: ExecutionMonitor,
    trade_id: str = "r1",
    strategy: str = "momentum_v1",
) -> None:
    monitor.record_order(
        trade_id=trade_id,
        strategy=strategy,
        symbol="TSLA",
        side="BUY",
        order_type="MARKET",
        requested_price=200.0,
        filled_price=None,
        status="rejected",
        latency_ms=0.0,
    )


# ======================================================================
# SECTION 1 — ExecutionMonitor
# ======================================================================


class TestExecutionMonitorRecordOrder:
    """record_order persists data to SQLite."""

    def test_record_order_creates_db_entry(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(monitor, trade_id="abc123")

        conn = sqlite3.connect(str(tmp_path / "execution_monitor.db"))
        rows = conn.execute("SELECT trade_id FROM execution_events").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "abc123"

    def test_record_order_calculates_slippage(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # requested=100, filled=100.10 => 10 bps
        _record_filled_order(monitor, requested=100.0, filled=100.10)

        conn = sqlite3.connect(str(tmp_path / "execution_monitor.db"))
        row = conn.execute("SELECT slippage_bps FROM execution_events").fetchone()
        conn.close()

        assert abs(row[0] - 10.0) < 0.01

    def test_multiple_orders_tracked(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        for i in range(5):
            _record_filled_order(monitor, trade_id=f"t{i}")

        conn = sqlite3.connect(str(tmp_path / "execution_monitor.db"))
        count = conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0]
        conn.close()

        assert count == 5


class TestExecutionMonitorGetMetrics:
    """get_metrics computes accurate execution quality metrics."""

    def test_no_data_returns_empty_metrics_ok(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        m = monitor.get_metrics()

        assert m.total_orders == 0
        assert m.level == "OK"
        assert m.fill_rate == 1.0
        assert m.avg_slippage_bps == 0.0

    def test_fill_rate_correct(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # 8 filled + 2 rejected = 80% fill rate
        for i in range(8):
            _record_filled_order(monitor, trade_id=f"f{i}")
        for i in range(2):
            _record_rejected_order(monitor, trade_id=f"r{i}")

        m = monitor.get_metrics()
        assert m.total_orders == 10
        assert m.filled_orders == 8
        assert m.rejected_orders == 2
        assert abs(m.fill_rate - 0.80) < 1e-6

    def test_slippage_stats_correct(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # Order 1: 100->100.02 => 2 bps
        _record_filled_order(monitor, trade_id="s1", requested=100.0, filled=100.02)
        # Order 2: 100->100.06 => 6 bps
        _record_filled_order(monitor, trade_id="s2", requested=100.0, filled=100.06)

        m = monitor.get_metrics()
        assert abs(m.avg_slippage_bps - 4.0) < 0.01  # (2+6)/2
        assert abs(m.worst_slippage_bps - 6.0) < 0.01

    def test_slippage_ratio_vs_backtest(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # 4 bps avg, backtest assumption = 2 bps => ratio = 2.0
        _record_filled_order(monitor, trade_id="r1", requested=100.0, filled=100.04)

        m = monitor.get_metrics()
        # avg_slip = 4 bps, backtest = 2 bps
        assert abs(m.slippage_ratio - 2.0) < 0.01

    def test_latency_percentiles(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # Record 100 orders with latencies 1..100 ms
        for i in range(1, 101):
            _record_filled_order(
                monitor, trade_id=f"lat{i}", latency=float(i),
                requested=100.0, filled=100.0,  # zero slippage
            )

        m = monitor.get_metrics()
        assert m.avg_latency_ms == pytest.approx(50.5, abs=0.5)
        assert m.p95_latency_ms >= 95
        assert m.p99_latency_ms >= 99

    def test_sl_execution_rate(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # 3 SL triggered: 2 filled, 1 rejected
        _record_filled_order(monitor, trade_id="sl1", is_stop_loss=True)
        _record_filled_order(monitor, trade_id="sl2", is_stop_loss=True)
        monitor.record_order(
            trade_id="sl3", strategy="test", symbol="X", side="SELL",
            order_type="STOP", requested_price=50.0, filled_price=None,
            status="rejected", is_stop_loss=True,
        )

        m = monitor.get_metrics()
        assert m.sl_triggered == 3
        assert m.sl_executed == 2
        assert abs(m.sl_execution_rate - 2 / 3) < 1e-6

    def test_commission_cost_ratio(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # commission=5, notional=10000 => ratio 0.0005
        _record_filled_order(monitor, trade_id="c1", commission=5.0, notional=10_000.0)

        m = monitor.get_metrics()
        assert abs(m.total_commission - 5.0) < 0.01
        assert abs(m.avg_cost_ratio - 0.0005) < 1e-6


class TestExecutionMonitorLevels:
    """Level detection: OK / WARNING / CRITICAL."""

    def test_critical_when_slippage_gt_3x(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # Need avg_slippage > 3x backtest (6+ bps).  Use 7 bps.
        _record_filled_order(monitor, trade_id="c1", requested=100.0, filled=100.07)

        m = monitor.get_metrics()
        assert m.slippage_ratio >= 3.0
        assert m.level == "CRITICAL"

    def test_warning_when_slippage_gt_2x(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # avg = 4 bps, ratio = 2.0 => WARNING
        _record_filled_order(monitor, trade_id="w1", requested=100.0, filled=100.04)

        m = monitor.get_metrics()
        assert m.slippage_ratio >= 2.0
        assert m.slippage_ratio < 3.0
        assert m.level == "WARNING"

    def test_critical_when_fill_rate_lt_80(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # 7 filled + 3 rejected = 70% — but we also need slippage < WARNING
        for i in range(7):
            _record_filled_order(
                monitor, trade_id=f"f{i}",
                requested=100.0, filled=100.0,  # zero slip
            )
        for i in range(3):
            _record_rejected_order(monitor, trade_id=f"r{i}")

        m = monitor.get_metrics()
        assert m.fill_rate < 0.80
        assert m.level == "CRITICAL"

    def test_warning_when_fill_rate_lt_90(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # 8 filled + 1 rejected = 88.9% => WARNING
        for i in range(8):
            _record_filled_order(
                monitor, trade_id=f"f{i}",
                requested=100.0, filled=100.0,
            )
        _record_rejected_order(monitor, trade_id="r0")

        m = monitor.get_metrics()
        assert m.fill_rate < 0.90
        assert m.fill_rate >= 0.80
        assert m.level == "WARNING"

    def test_ok_level_when_all_good(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # Tiny slippage, good fill, low latency
        _record_filled_order(
            monitor, trade_id="ok1",
            requested=100.0, filled=100.01,  # 1 bps → ratio 0.5
            latency=10.0, commission=0.01, notional=10_000.0,
        )

        m = monitor.get_metrics()
        assert m.level == "OK"


class TestExecutionMonitorAlerts:
    """check_alerts returns structured alert list."""

    def test_check_alerts_returns_list(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        alerts = monitor.check_alerts()
        assert isinstance(alerts, list)

    def test_alerts_on_high_slippage(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(monitor, trade_id="a1", requested=100.0, filled=100.07)

        alerts = monitor.check_alerts()
        slippage_alerts = [a for a in alerts if a.category == "slippage"]
        assert len(slippage_alerts) == 1
        assert slippage_alerts[0].level == "CRITICAL"

    def test_alerts_on_low_fill_rate(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        for i in range(7):
            _record_filled_order(
                monitor, trade_id=f"f{i}",
                requested=100.0, filled=100.0,
            )
        for i in range(3):
            _record_rejected_order(monitor, trade_id=f"r{i}")

        alerts = monitor.check_alerts()
        fr_alerts = [a for a in alerts if a.category == "fill_rate"]
        assert len(fr_alerts) == 1
        assert fr_alerts[0].level == "CRITICAL"

    def test_alerts_on_high_latency(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # Record many orders all with latency 600ms => p95 >= 500 => CRITICAL
        for i in range(20):
            _record_filled_order(
                monitor, trade_id=f"lat{i}", latency=600.0,
                requested=100.0, filled=100.0,
            )

        alerts = monitor.check_alerts()
        lat_alerts = [a for a in alerts if a.category == "latency"]
        assert len(lat_alerts) == 1
        assert lat_alerts[0].level == "CRITICAL"

    def test_alerts_on_sl_execution_failure(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        # 10 SL triggered, only 8 filled => 80% < 90% CRITICAL
        for i in range(8):
            _record_filled_order(
                monitor, trade_id=f"sl{i}", is_stop_loss=True,
                requested=100.0, filled=100.0,
            )
        for i in range(2):
            monitor.record_order(
                trade_id=f"slr{i}", strategy="test", symbol="X",
                side="SELL", order_type="STOP", requested_price=50.0,
                filled_price=None, status="rejected", is_stop_loss=True,
            )

        alerts = monitor.check_alerts()
        sl_alerts = [a for a in alerts if a.category == "stop_loss"]
        assert len(sl_alerts) == 1
        assert sl_alerts[0].level == "CRITICAL"

    def test_no_alerts_when_healthy(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(
            monitor, trade_id="h1",
            requested=100.0, filled=100.01,
            latency=10.0, commission=0.01, notional=10_000.0,
        )

        alerts = monitor.check_alerts()
        assert alerts == []


class TestExecutionMonitorStrategyBreakdown:
    """get_strategy_breakdown groups data by strategy."""

    def test_groups_by_strategy(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(monitor, trade_id="a1", strategy="strat_a")
        _record_filled_order(monitor, trade_id="a2", strategy="strat_a")
        _record_filled_order(monitor, trade_id="b1", strategy="strat_b")

        breakdown = monitor.get_strategy_breakdown()
        assert "strat_a" in breakdown
        assert "strat_b" in breakdown
        assert breakdown["strat_a"]["total_orders"] == 2
        assert breakdown["strat_b"]["total_orders"] == 1

    def test_strategy_fill_rate(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(monitor, trade_id="f1", strategy="mixed")
        _record_rejected_order(monitor, trade_id="r1", strategy="mixed")

        breakdown = monitor.get_strategy_breakdown()
        assert abs(breakdown["mixed"]["fill_rate"] - 0.5) < 1e-6


class TestExecutionMonitorPeriodFilter:
    """Period filter restricts queries to correct time window."""

    def test_period_filter_excludes_old_orders(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(monitor, trade_id="recent")

        # Manually insert an old order (3 days ago)
        old_ts = (datetime.utcnow() - timedelta(days=3)).isoformat()
        conn = sqlite3.connect(str(tmp_path / "execution_monitor.db"))
        conn.execute(
            """INSERT INTO execution_events
               (timestamp, trade_id, strategy, symbol, side, order_type,
                requested_price, filled_price, status, latency_ms,
                slippage_bps, is_stop_loss, commission, notional, quantity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (old_ts, "old", "strat", "X", "BUY", "MARKET",
             100, 100, "filled", 10, 0, 0, 0, 0, 0),
        )
        conn.commit()
        conn.close()

        m_24h = monitor.get_metrics("24h")
        assert m_24h.total_orders == 1  # only recent

        m_7d = monitor.get_metrics("7d")
        assert m_7d.total_orders == 2  # recent + old


# ======================================================================
# SECTION 2 — PortfolioStateEngine / BrokerState
# ======================================================================


class TestPortfolioStateEngineEmpty:
    """Empty / no-broker scenarios."""

    def test_empty_brokers_returns_zero_state(self, tmp_path: Path):
        engine = PortfolioStateEngine(data_dir=str(tmp_path))
        state = engine.get_state()

        assert state.total_capital == 0.0
        assert state.exposure_long == 0.0
        assert state.exposure_short == 0.0
        assert state.leverage_real == 0.0
        assert state.n_positions == 0

    def test_no_router_returns_zero(self, tmp_path: Path):
        engine = PortfolioStateEngine(smart_router=None, data_dir=str(tmp_path))
        state = engine.get_state()
        assert state.total_capital == 0.0


class TestPortfolioStateEngineSingleBroker:
    """Single broker aggregation."""

    def test_single_broker_state(self, tmp_path: Path):
        positions = [
            {"qty": 10, "current_price": 150.0, "side": "LONG", "unrealized_pl": 50.0},
        ]
        broker = _make_broker("alpaca", equity=10_000, cash=5_000, positions=positions)
        router = _make_smart_router({"alpaca": broker})

        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path))
        state = engine.get_state()

        assert state.total_capital == 10_000
        assert state.total_cash == 5_000
        assert state.exposure_long == 1_500.0  # 10 * 150
        assert state.exposure_short == 0.0
        assert state.unrealized_pnl == 50.0
        assert state.n_positions == 1

    def test_leverage_calculated_as_gross_over_equity(self, tmp_path: Path):
        positions = [
            {"qty": 100, "current_price": 50.0, "side": "LONG", "unrealized_pl": 0.0},
        ]
        broker = _make_broker("ib", equity=5_000, cash=0, positions=positions)
        router = _make_smart_router({"ib": broker})

        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path))
        state = engine.get_state()

        # Gross = 5000, equity = 5000, leverage = 1.0
        assert abs(state.leverage_real - 1.0) < 1e-6


class TestPortfolioStateEngineMultiBroker:
    """Multi-broker aggregation with long + short."""

    def test_multi_broker_aggregation(self, tmp_path: Path):
        pos_alpaca = [
            {"qty": 10, "current_price": 200.0, "side": "LONG", "unrealized_pl": 100.0},
        ]
        pos_ibkr = [
            {"qty": 5, "current_price": 100.0, "side": "SHORT", "unrealized_pl": -30.0},
        ]
        b_alpaca = _make_broker("alpaca", equity=20_000, cash=10_000, positions=pos_alpaca)
        b_ibkr = _make_broker("ibkr", equity=10_000, cash=5_000, positions=pos_ibkr)
        router = _make_smart_router({"alpaca": b_alpaca, "ibkr": b_ibkr})

        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path))
        state = engine.get_state()

        assert state.total_capital == 30_000
        assert state.total_cash == 15_000
        assert state.exposure_long == 2_000.0   # 10*200
        assert state.exposure_short == 500.0     # 5*100
        assert state.exposure_net == 1_500.0     # 2000 - 500
        assert state.exposure_gross == 2_500.0   # 2000 + 500
        assert state.unrealized_pnl == 70.0      # 100 + (-30)
        assert state.n_positions == 2

    def test_exposure_percentages(self, tmp_path: Path):
        pos = [
            {"qty": 10, "current_price": 100.0, "side": "LONG", "unrealized_pl": 0.0},
        ]
        broker = _make_broker("x", equity=10_000, cash=5_000, positions=pos)
        router = _make_smart_router({"x": broker})
        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path))
        state = engine.get_state()

        # net = 1000, gross = 1000, equity = 10_000
        assert abs(state.exposure_net_pct - 0.10) < 1e-6
        assert abs(state.exposure_gross_pct - 0.10) < 1e-6


class TestPortfolioStateEngineDrawdownPnL:
    """Drawdown and daily PnL tracking."""

    def test_drawdown_tracked_from_peak(self, tmp_path: Path):
        broker_high = _make_broker("a", equity=10_000, cash=10_000, positions=[])
        router_high = _make_smart_router({"a": broker_high})
        engine = PortfolioStateEngine(smart_router=router_high, data_dir=str(tmp_path))

        # First call sets peak to 10000
        state1 = engine.get_state()
        assert state1.drawdown_pct == 0.0

        # Now equity drops to 9500 => 5% DD
        broker_low = _make_broker("a", equity=9_500, cash=9_500, positions=[])
        router_low = _make_smart_router({"a": broker_low})
        engine.smart_router = router_low
        state2 = engine.get_state()

        assert abs(state2.drawdown_pct - 0.05) < 1e-6

    def test_daily_pnl_calculated(self, tmp_path: Path):
        broker1 = _make_broker("a", equity=10_000, cash=10_000, positions=[])
        router1 = _make_smart_router({"a": broker1})
        engine = PortfolioStateEngine(smart_router=router1, data_dir=str(tmp_path))

        # First call sets daily start
        state1 = engine.get_state()
        assert state1.daily_pnl == 0.0

        # Equity goes up
        broker2 = _make_broker("a", equity=10_200, cash=10_200, positions=[])
        router2 = _make_smart_router({"a": broker2})
        engine.smart_router = router2
        state2 = engine.get_state()

        assert abs(state2.daily_pnl - 200.0) < 0.01
        assert abs(state2.daily_pnl_pct - 0.02) < 1e-6


class TestPortfolioStateEngineAlerts:
    """Alert generation on portfolio state conditions."""

    def test_alert_high_dd(self, tmp_path: Path):
        broker_high = _make_broker("a", equity=10_000, cash=10_000, positions=[])
        router_high = _make_smart_router({"a": broker_high})
        engine = PortfolioStateEngine(smart_router=router_high, data_dir=str(tmp_path))
        engine.get_state()  # set peak

        # Drop to 9400 => 6% DD => CRITICAL
        broker_low = _make_broker("a", equity=9_400, cash=9_400, positions=[])
        router_low = _make_smart_router({"a": broker_low})
        engine.smart_router = router_low
        state = engine.get_state()

        assert any("CRITICAL" in a and "DD" in a for a in state.alerts)

    def test_alert_warning_dd(self, tmp_path: Path):
        broker_high = _make_broker("a", equity=10_000, cash=10_000, positions=[])
        router_high = _make_smart_router({"a": broker_high})
        engine = PortfolioStateEngine(smart_router=router_high, data_dir=str(tmp_path))
        engine.get_state()

        # Drop to 9650 => 3.5% DD => WARNING
        broker_low = _make_broker("a", equity=9_650, cash=9_650, positions=[])
        router_low = _make_smart_router({"a": broker_low})
        engine.smart_router = router_low
        state = engine.get_state()

        assert any("WARNING" in a and "DD" in a for a in state.alerts)

    def test_alert_high_ere(self, tmp_path: Path):
        # Mock ERE calculator returning 40% ERE
        ere_calc = MagicMock()
        ere_result = SimpleNamespace(ere_absolute=4_000.0, ere_pct=0.40)
        ere_calc.calculate.return_value = ere_result

        broker = _make_broker("a", equity=10_000, cash=5_000, positions=[])
        router = _make_smart_router({"a": broker})
        engine = PortfolioStateEngine(
            smart_router=router, ere_calculator=ere_calc, data_dir=str(tmp_path),
        )
        state = engine.get_state()

        assert any("CRITICAL" in a and "ERE" in a for a in state.alerts)

    def test_alert_warning_ere(self, tmp_path: Path):
        ere_calc = MagicMock()
        ere_result = SimpleNamespace(ere_absolute=2_800.0, ere_pct=0.28)
        ere_calc.calculate.return_value = ere_result

        broker = _make_broker("a", equity=10_000, cash=5_000, positions=[])
        router = _make_smart_router({"a": broker})
        engine = PortfolioStateEngine(
            smart_router=router, ere_calculator=ere_calc, data_dir=str(tmp_path),
        )
        state = engine.get_state()

        assert any("WARNING" in a and "ERE" in a for a in state.alerts)

    def test_alert_high_correlation(self, tmp_path: Path):
        corr_engine = MagicMock()
        corr_engine.get_global_score.return_value = 0.90
        corr_engine.detect_clusters.return_value = [["a", "b"], ["c", "d"]]

        broker = _make_broker("a", equity=10_000, cash=10_000, positions=[])
        router = _make_smart_router({"a": broker})
        engine = PortfolioStateEngine(
            smart_router=router, correlation_engine=corr_engine,
            data_dir=str(tmp_path),
        )
        state = engine.get_state()

        assert any("CRITICAL" in a and "corr" in a for a in state.alerts)

    def test_alert_warning_correlation(self, tmp_path: Path):
        corr_engine = MagicMock()
        corr_engine.get_global_score.return_value = 0.75
        corr_engine.detect_clusters.return_value = [["a"]]

        broker = _make_broker("a", equity=10_000, cash=10_000, positions=[])
        router = _make_smart_router({"a": broker})
        engine = PortfolioStateEngine(
            smart_router=router, correlation_engine=corr_engine,
            data_dir=str(tmp_path),
        )
        state = engine.get_state()

        assert any("WARNING" in a and "corr" in a for a in state.alerts)

    def test_alert_leverage_breach(self, tmp_path: Path):
        # Gross=2500, equity=1000 => leverage=2.5, target=1.0, 2.5 > 1.0*1.2
        positions = [
            {"qty": 25, "current_price": 100.0, "side": "LONG", "unrealized_pl": 0.0},
        ]
        broker = _make_broker("a", equity=1_000, cash=0, positions=positions)
        router = _make_smart_router({"a": broker})
        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path))
        state = engine.get_state(leverage_target=1.0)

        assert any("leverage" in a for a in state.alerts)


class TestPortfolioStateSerialization:
    """Serialization and snapshot persistence."""

    def test_to_dict_serializable(self, tmp_path: Path):
        broker = _make_broker("a", equity=10_000, cash=5_000, positions=[])
        router = _make_smart_router({"a": broker})
        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path))
        state = engine.get_state()

        d = state.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(d)
        parsed = json.loads(serialized)
        assert parsed["total_capital"] == 10_000.0

    def test_record_snapshot_writes_jsonl(self, tmp_path: Path):
        broker = _make_broker("a", equity=10_000, cash=5_000, positions=[])
        router = _make_smart_router({"a": broker})
        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path))
        state = engine.get_state()

        engine.record_snapshot(state)

        jsonl = tmp_path / "live_portfolio_snapshots.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["total_capital"] == 10_000.0


# ======================================================================
# SECTION 3 — LiveSnapshotLogger
# ======================================================================


class TestLiveSnapshotLoggerRecord:
    """LiveSnapshotLogger.record creates JSONL file and appends."""

    def test_record_creates_jsonl_file(self, tmp_path: Path):
        log = LiveSnapshotLogger(log_dir=str(tmp_path))
        result = log.record()

        assert result is not None
        assert "timestamp" in result

        # File should exist with today's date
        files = list(tmp_path.glob("live_portfolio_*.jsonl"))
        assert len(files) >= 1

    def test_record_appends_multiple_snapshots(self, tmp_path: Path):
        log = LiveSnapshotLogger(log_dir=str(tmp_path))
        log.record()
        log.record()
        log.record()

        path = log._current_path()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_extra_data_included(self, tmp_path: Path):
        log = LiveSnapshotLogger(log_dir=str(tmp_path))
        result = log.record(extra={"custom_key": "hello", "value": 42})

        assert result["custom_key"] == "hello"
        assert result["value"] == 42


class TestLiveSnapshotLoggerGetRecent:
    """get_recent reads last N entries from JSONL."""

    def test_get_recent_reads_last_n(self, tmp_path: Path):
        log = LiveSnapshotLogger(log_dir=str(tmp_path))
        for i in range(10):
            log.record(extra={"idx": i})

        recent = log.get_recent(n=3)
        assert len(recent) == 3
        assert recent[0]["idx"] == 7
        assert recent[1]["idx"] == 8
        assert recent[2]["idx"] == 9

    def test_get_recent_empty_file(self, tmp_path: Path):
        log = LiveSnapshotLogger(log_dir=str(tmp_path))
        recent = log.get_recent()
        assert recent == []


class TestLiveSnapshotLoggerRotation:
    """Daily rotation and filename."""

    def test_daily_rotation_uses_correct_filename(self, tmp_path: Path):
        log = LiveSnapshotLogger(log_dir=str(tmp_path))
        path = log._current_path()

        today = datetime.utcnow().strftime("%Y-%m-%d")
        assert path.name == f"live_portfolio_{today}.jsonl"


class TestLiveSnapshotLoggerComponents:
    """Integration with portfolio engine and other components."""

    def test_handles_missing_components(self, tmp_path: Path):
        """All components are None => still produces a valid snapshot."""
        log = LiveSnapshotLogger(log_dir=str(tmp_path))
        result = log.record()

        assert result is not None
        assert "timestamp" in result
        # No error keys since components were None (not failing)
        assert "portfolio_error" not in result

    def test_handles_failing_portfolio_engine(self, tmp_path: Path):
        engine = MagicMock()
        engine.get_state.side_effect = RuntimeError("broker down")

        log = LiveSnapshotLogger(portfolio_engine=engine, log_dir=str(tmp_path))
        result = log.record()

        assert result is not None
        assert "portfolio_error" in result
        assert "broker down" in result["portfolio_error"]

    def test_handles_failing_correlation_engine(self, tmp_path: Path):
        corr = MagicMock()
        corr.to_dict.side_effect = ValueError("no data")

        log = LiveSnapshotLogger(correlation_engine=corr, log_dir=str(tmp_path))
        result = log.record()

        assert "correlation_error" in result

    def test_handles_failing_execution_monitor(self, tmp_path: Path):
        exec_mon = MagicMock()
        exec_mon.get_metrics.side_effect = RuntimeError("db locked")

        log = LiveSnapshotLogger(execution_monitor=exec_mon, log_dir=str(tmp_path))
        result = log.record()

        assert "execution_error" in result

    def test_portfolio_state_included(self, tmp_path: Path):
        """When a working portfolio engine is provided, its state is captured."""
        positions = [
            {"qty": 5, "current_price": 100.0, "side": "LONG", "unrealized_pl": 10.0},
        ]
        broker = _make_broker("test", equity=5_000, cash=2_000, positions=positions)
        router = _make_smart_router({"test": broker})
        engine = PortfolioStateEngine(smart_router=router, data_dir=str(tmp_path / "data"))

        log = LiveSnapshotLogger(portfolio_engine=engine, log_dir=str(tmp_path / "logs"))
        result = log.record()

        assert "portfolio" in result
        assert result["portfolio"]["total_capital"] == 5_000.0


# ======================================================================
# SECTION 4 — ExecutionMetrics serialization
# ======================================================================


class TestExecutionMetricsToDict:
    """ExecutionMetrics.to_dict round-trips."""

    def test_to_dict_has_all_sections(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(monitor)
        m = monitor.get_metrics()
        d = m.to_dict()

        assert "slippage" in d
        assert "fills" in d
        assert "latency" in d
        assert "stop_loss" in d
        assert "costs" in d
        assert "level" in d
        assert "timestamp" in d
        assert "period" in d

    def test_to_dict_json_serializable(self, tmp_path: Path):
        monitor = ExecutionMonitor(data_dir=str(tmp_path))
        _record_filled_order(monitor)
        m = monitor.get_metrics()
        serialized = json.dumps(m.to_dict())
        assert isinstance(json.loads(serialized), dict)
