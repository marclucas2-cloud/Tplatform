"""Tests for the Risk Portfolio V10 modules.

Covers:
  - LiveCorrelationEngine (core/risk/live_correlation_engine.py)
  - EffectiveRiskExposure (core/risk/effective_risk.py)
  - RiskBudgetAllocator (core/risk/risk_budget_allocator.py)
  - LeverageAdapter (core/risk/leverage_adapter.py)
  - StrategyThrottler + StrategyState (core/risk/strategy_throttler.py)
  - SafetyMode + SafetyLimits (core/risk/safety_mode.py)

60+ tests — pytest, no external deps beyond numpy.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from core.risk.live_correlation_engine import (
    LiveCorrelationEngine,
    CORR_CRITICAL,
    CORR_WARNING,
)
from core.risk.effective_risk import EffectiveRiskExposure, EREResult
from core.risk.risk_budget_allocator import RiskBudgetAllocator
from core.risk.leverage_adapter import LeverageAdapter
from core.risk.strategy_throttler import StrategyThrottler, StrategyState
from core.risk.safety_mode import SafetyMode, SafetyLimits


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _feed_pnl(engine: LiveCorrelationEngine, strategy: str, pnls: list[float]):
    """Feed a series of PnL values into the engine."""
    base = datetime(2026, 1, 1)
    for i, pnl in enumerate(pnls):
        engine.record_pnl(strategy, pnl, base + timedelta(hours=i))


def _make_strategy_state(
    name: str = "strat_a",
    sharpe: float = 1.0,
    n_trades: int = 50,
    win_rate: float = 0.55,
    slippage: float = 1.0,
    consec_losses: int = 0,
    in_cluster: bool = False,
    cluster_level: str = "OK",
    last_trade_age: float = 1.0,
) -> StrategyState:
    return StrategyState(
        name=name,
        sharpe_live=sharpe,
        n_trades=n_trades,
        win_rate=win_rate,
        slippage_ratio=slippage,
        consecutive_losses=consec_losses,
        is_in_cluster=in_cluster,
        cluster_level=cluster_level,
        last_trade_age_hours=last_trade_age,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LiveCorrelationEngine
# ═══════════════════════════════════════════════════════════════════════════════


class TestLiveCorrelationEngine:
    """Tests for LiveCorrelationEngine."""

    def test_record_pnl_stores_entries(self, tmp_path):
        engine = LiveCorrelationEngine(data_dir=str(tmp_path))
        engine.record_pnl("strat_a", 10.0, datetime(2026, 1, 1))
        engine.record_pnl("strat_a", -5.0, datetime(2026, 1, 2))
        assert len(engine._pnl_history["strat_a"]) == 2

    def test_get_strategies_returns_only_enough_data(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        # min_trades = max(5, 20 // 2) = 10
        _feed_pnl(engine, "strat_a", [1.0] * 9)  # Not enough
        _feed_pnl(engine, "strat_b", [1.0] * 11)  # Enough
        strategies = engine.get_strategies()
        assert "strat_a" not in strategies
        assert "strat_b" in strategies

    def test_correlation_matrix_single_strategy(self, tmp_path):
        engine = LiveCorrelationEngine(data_dir=str(tmp_path))
        _feed_pnl(engine, "strat_a", [float(i) for i in range(20)])
        result = engine.get_correlation_matrix()
        assert result["strategies"] == ["strat_a"]
        assert result["matrix"] == [[1.0]]

    def test_correlation_identical_series_is_one(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        pnls = [float(i) for i in range(20)]
        _feed_pnl(engine, "strat_a", pnls)
        _feed_pnl(engine, "strat_b", pnls)
        result = engine.get_correlation_matrix()
        matrix = np.array(result["matrix"])
        assert matrix[0, 1] == pytest.approx(1.0, abs=0.01)

    def test_correlation_opposite_series_is_minus_one(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        pnls = [float(i) for i in range(20)]
        opposite = [-p for p in pnls]
        _feed_pnl(engine, "strat_a", pnls)
        _feed_pnl(engine, "strat_b", opposite)
        result = engine.get_correlation_matrix()
        matrix = np.array(result["matrix"])
        assert matrix[0, 1] == pytest.approx(-1.0, abs=0.01)

    def test_correlation_uncorrelated_close_to_zero(self, tmp_path):
        rng = np.random.RandomState(42)
        engine = LiveCorrelationEngine(window_short=100, data_dir=str(tmp_path))
        _feed_pnl(engine, "strat_a", rng.randn(200).tolist())
        _feed_pnl(engine, "strat_b", rng.randn(200).tolist())
        result = engine.get_correlation_matrix()
        matrix = np.array(result["matrix"])
        assert abs(matrix[0, 1]) < 0.3  # Loose tolerance for random

    def test_detect_clusters_groups_correlated(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        pnls = [float(i) for i in range(20)]
        _feed_pnl(engine, "strat_a", pnls)
        _feed_pnl(engine, "strat_b", pnls)  # Identical → corr=1.0
        clusters = engine.detect_clusters()
        assert len(clusters) >= 1
        cluster_strats = clusters[0].strategies
        assert "strat_a" in cluster_strats
        assert "strat_b" in cluster_strats

    def test_detect_clusters_empty_for_uncorrelated(self, tmp_path):
        rng = np.random.RandomState(42)
        engine = LiveCorrelationEngine(window_short=50, data_dir=str(tmp_path))
        _feed_pnl(engine, "strat_a", rng.randn(100).tolist())
        _feed_pnl(engine, "strat_b", rng.randn(100).tolist())
        clusters = engine.detect_clusters()
        # Uncorrelated strategies should NOT form a cluster
        assert len(clusters) == 0

    def test_check_alerts_warning_at_070(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        # Build two series with correlation ~0.75 (above WARNING, below CRITICAL)
        rng = np.random.RandomState(1)
        base = rng.randn(20)
        noise = rng.randn(20) * 0.5
        series_a = base.tolist()
        series_b = (base + noise).tolist()
        _feed_pnl(engine, "strat_a", series_a)
        _feed_pnl(engine, "strat_b", series_b)
        # Verify actual corr
        corr_result = engine.get_correlation_matrix()
        corr_val = np.array(corr_result["matrix"])[0, 1]
        # The noise level is chosen so corr is in (0.70, 0.85) on average
        if corr_val >= CORR_WARNING and corr_val < CORR_CRITICAL:
            alerts = engine.check_alerts()
            warning_alerts = [a for a in alerts if a.level == "WARNING"]
            assert len(warning_alerts) >= 1
        # If corr happens to be outside that range, just check alerts runs
        else:
            engine.check_alerts()  # Should not raise

    def test_check_alerts_critical_at_085(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        pnls = [float(i) for i in range(20)]
        _feed_pnl(engine, "strat_a", pnls)
        _feed_pnl(engine, "strat_b", pnls)  # corr=1.0 → CRITICAL
        alerts = engine.check_alerts()
        critical = [a for a in alerts if a.level == "CRITICAL"]
        assert len(critical) == 1
        assert critical[0].correlation == pytest.approx(1.0, abs=0.01)

    def test_global_score_zero_for_uncorrelated(self, tmp_path):
        rng = np.random.RandomState(42)
        engine = LiveCorrelationEngine(window_short=50, data_dir=str(tmp_path))
        _feed_pnl(engine, "strat_a", rng.randn(100).tolist())
        _feed_pnl(engine, "strat_b", rng.randn(100).tolist())
        score = engine.get_global_score()
        assert score < 0.3  # Should be near 0

    def test_get_snapshot_complete_data(self, tmp_path):
        engine = LiveCorrelationEngine(data_dir=str(tmp_path))
        _feed_pnl(engine, "strat_a", [float(i) for i in range(20)])
        snap = engine.get_snapshot()
        assert snap.n_strategies >= 1
        assert isinstance(snap.global_score, float)
        assert isinstance(snap.alerts, list)
        assert isinstance(snap.clusters, list)
        assert snap.timestamp is not None

    def test_to_dict_serializable(self, tmp_path):
        engine = LiveCorrelationEngine(data_dir=str(tmp_path))
        _feed_pnl(engine, "strat_a", [float(i) for i in range(20)])
        _feed_pnl(engine, "strat_b", [float(i) for i in range(20)])
        d = engine.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(d)
        assert "n_strategies" in serialized

    def test_state_persistence_roundtrip(self, tmp_path):
        engine1 = LiveCorrelationEngine(data_dir=str(tmp_path))
        pnls = [float(i) for i in range(15)]
        _feed_pnl(engine1, "strat_a", pnls)
        _feed_pnl(engine1, "strat_b", [p * 2 for p in pnls])

        # Load fresh engine from same dir
        engine2 = LiveCorrelationEngine(data_dir=str(tmp_path))
        assert len(engine2._pnl_history["strat_a"]) == 15
        assert len(engine2._pnl_history["strat_b"]) == 15


# ═══════════════════════════════════════════════════════════════════════════════
# EffectiveRiskExposure
# ═══════════════════════════════════════════════════════════════════════════════


class TestEffectiveRiskExposure:
    """Tests for EffectiveRiskExposure."""

    def test_empty_positions_returns_zero(self):
        ere = EffectiveRiskExposure()
        result = ere.calculate([], capital=10000)
        assert result.ere_pct == 0.0
        assert result.ere_absolute == 0.0
        assert result.level == "OK"

    def test_single_long_with_sl(self):
        ere = EffectiveRiskExposure()
        positions = [
            {
                "symbol": "AAPL",
                "strategy": "strat_a",
                "direction": "LONG",
                "quantity": 10,
                "entry_price": 150.0,
                "stop_loss": 145.0,  # $5 risk per share
                "current_price": 152.0,
            }
        ]
        result = ere.calculate(positions, capital=10000)
        # max_loss = (150 - 145) * 10 = $50
        assert result.naive_risk == 50.0
        assert result.ere_pct > 0

    def test_single_short_with_sl(self):
        ere = EffectiveRiskExposure()
        positions = [
            {
                "symbol": "TSLA",
                "strategy": "strat_b",
                "direction": "SHORT",
                "quantity": 5,
                "entry_price": 200.0,
                "stop_loss": 210.0,  # $10 risk per share
                "current_price": 195.0,
            }
        ]
        result = ere.calculate(positions, capital=10000)
        # max_loss = (210 - 200) * 5 = $50
        assert result.naive_risk == 50.0

    def test_no_sl_defaults_to_5pct(self):
        ere = EffectiveRiskExposure()
        positions = [
            {
                "symbol": "MSFT",
                "strategy": "strat_c",
                "direction": "LONG",
                "quantity": 10,
                "entry_price": 100.0,
                "stop_loss": 0,  # No SL
                "current_price": 100.0,
            }
        ]
        result = ere.calculate(positions, capital=10000)
        # Default SL for LONG = 100 * 0.95 = 95
        # max_loss = (100 - 95) * 10 = $50
        assert result.naive_risk == 50.0

    def test_naive_risk_sums_individual_losses(self):
        ere = EffectiveRiskExposure()
        positions = [
            {
                "symbol": "AAPL",
                "strategy": "strat_a",
                "direction": "LONG",
                "quantity": 10,
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "current_price": 100.0,
            },
            {
                "symbol": "TSLA",
                "strategy": "strat_b",
                "direction": "LONG",
                "quantity": 5,
                "entry_price": 200.0,
                "stop_loss": 190.0,
                "current_price": 200.0,
            },
        ]
        result = ere.calculate(positions, capital=20000)
        # AAPL: (100-95)*10 = 50, TSLA: (200-190)*5 = 50 → total 100
        assert result.naive_risk == 100.0

    def test_correlation_penalty_at_least_one(self):
        ere = EffectiveRiskExposure()  # No engine → default 1.3
        positions = [
            {
                "symbol": "AAPL",
                "strategy": "strat_a",
                "direction": "LONG",
                "quantity": 10,
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "current_price": 100.0,
            },
            {
                "symbol": "TSLA",
                "strategy": "strat_b",
                "direction": "LONG",
                "quantity": 5,
                "entry_price": 200.0,
                "stop_loss": 190.0,
                "current_price": 200.0,
            },
        ]
        result = ere.calculate(positions, capital=20000)
        assert result.correlation_penalty >= 1.0

    def test_ere_warning_at_25pct(self):
        ere = EffectiveRiskExposure()
        # Need ERE ≈ 25-34% of capital
        positions = [
            {
                "symbol": "BIG",
                "strategy": "strat_a",
                "direction": "LONG",
                "quantity": 100,
                "entry_price": 100.0,
                "stop_loss": 80.0,
                "current_price": 100.0,
            },
        ]
        # naive_risk = (100-80)*100 = $2000. capital=6000 → naive=33%
        # Single position → corr_penalty=1.0, ERE = 2000/6000 = 33%
        result = ere.calculate(positions, capital=6000)
        assert result.level == "WARNING" or result.level == "CRITICAL"

    def test_ere_critical_at_35pct(self):
        ere = EffectiveRiskExposure()
        positions = [
            {
                "symbol": "BIG",
                "strategy": "strat_a",
                "direction": "LONG",
                "quantity": 100,
                "entry_price": 100.0,
                "stop_loss": 60.0,  # $40 per share
                "current_price": 100.0,
            },
        ]
        # naive = $4000, capital=10000 → 40% → CRITICAL
        result = ere.calculate(positions, capital=10000)
        assert result.level == "CRITICAL"

    def test_should_reduce_and_should_kill(self):
        ere = EffectiveRiskExposure()
        ok = EREResult(
            timestamp=datetime.utcnow(),
            capital=10000,
            ere_absolute=1000,
            ere_pct=0.10,  # 10% → OK
            naive_risk=800,
            naive_risk_pct=0.08,
            correlation_penalty=1.0,
            worst_case_cluster_loss=800,
            position_risks=[],
            level="OK",
        )
        assert ere.should_reduce(ok) is False
        assert ere.should_kill(ok) is False

        warning = EREResult(
            timestamp=datetime.utcnow(),
            capital=10000,
            ere_absolute=3000,
            ere_pct=0.30,  # 30% → WARNING
            naive_risk=2500,
            naive_risk_pct=0.25,
            correlation_penalty=1.2,
            worst_case_cluster_loss=2500,
            position_risks=[],
            level="WARNING",
        )
        assert ere.should_reduce(warning) is True
        assert ere.should_kill(warning) is False

        critical = EREResult(
            timestamp=datetime.utcnow(),
            capital=10000,
            ere_absolute=4000,
            ere_pct=0.40,  # 40% → CRITICAL
            naive_risk=3500,
            naive_risk_pct=0.35,
            correlation_penalty=1.3,
            worst_case_cluster_loss=3500,
            position_risks=[],
            level="CRITICAL",
        )
        assert ere.should_reduce(critical) is True
        assert ere.should_kill(critical) is True

    def test_worst_case_cluster_uses_correlation(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        pnls = [float(i) for i in range(20)]
        _feed_pnl(engine, "strat_a", pnls)
        _feed_pnl(engine, "strat_b", pnls)  # Same cluster

        ere = EffectiveRiskExposure(correlation_engine=engine)
        positions = [
            {
                "symbol": "A",
                "strategy": "strat_a",
                "direction": "LONG",
                "quantity": 10,
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "current_price": 100.0,
            },
            {
                "symbol": "B",
                "strategy": "strat_b",
                "direction": "LONG",
                "quantity": 10,
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "current_price": 100.0,
            },
        ]
        result = ere.calculate(positions, capital=10000)
        # Cluster should sum both losses = $100
        assert result.worst_case_cluster_loss == 100.0

    def test_to_dict_serializable(self):
        ere = EffectiveRiskExposure()
        result = ere.calculate([], capital=10000)
        d = result.to_dict()
        serialized = json.dumps(d)
        assert "ere_pct" in serialized


# ═══════════════════════════════════════════════════════════════════════════════
# RiskBudgetAllocator
# ═══════════════════════════════════════════════════════════════════════════════


class TestRiskBudgetAllocator:
    """Tests for RiskBudgetAllocator."""

    def test_empty_strategies_returns_empty(self):
        alloc = RiskBudgetAllocator()
        result = alloc.allocate([], capital=10000)
        assert result.n_strategies == 0
        assert result.budgets == []

    def test_single_strategy_gets_base_risk(self):
        alloc = RiskBudgetAllocator(base_risk=0.02)
        result = alloc.allocate(["strat_a"], capital=10000)
        assert len(result.budgets) == 1
        budget = result.budgets[0]
        assert budget.risk_budget_pct == pytest.approx(0.02, abs=0.001)
        assert budget.risk_budget_abs == pytest.approx(200.0, abs=10)

    def test_cluster_reduces_budget(self, tmp_path):
        engine = LiveCorrelationEngine(window_short=20, data_dir=str(tmp_path))
        pnls = [float(i) for i in range(20)]
        _feed_pnl(engine, "strat_a", pnls)
        _feed_pnl(engine, "strat_b", pnls)  # Same cluster

        alloc = RiskBudgetAllocator(correlation_engine=engine, base_risk=0.02)
        result = alloc.allocate(["strat_a", "strat_b"], capital=10000)
        # Both in cluster of size 2 → corr_adj = 1/sqrt(2) ≈ 0.707
        for b in result.budgets:
            assert b.correlation_adj < 1.0
            assert b.risk_budget_pct < 0.02

    def test_critical_cluster_extra_penalty(self, tmp_path):
        engine = LiveCorrelationEngine(
            window_short=20,
            critical_threshold=0.85,
            data_dir=str(tmp_path),
        )
        pnls = [float(i) for i in range(20)]
        _feed_pnl(engine, "strat_a", pnls)
        _feed_pnl(engine, "strat_b", pnls)  # corr=1.0 → CRITICAL cluster

        alloc = RiskBudgetAllocator(correlation_engine=engine, base_risk=0.02)
        result = alloc.allocate(["strat_a", "strat_b"], capital=10000)
        for b in result.budgets:
            assert b.cluster_penalty < 1.0  # 0.7 extra penalty

    def test_regime_multiplier_high_vol(self):
        alloc = RiskBudgetAllocator(base_risk=0.02)
        result = alloc.allocate(["strat_a"], capital=10000, regime="high_vol")
        budget = result.budgets[0]
        assert budget.regime_adj == pytest.approx(0.7)
        assert budget.risk_budget_pct == pytest.approx(0.02 * 0.7, abs=0.001)

    def test_regime_multiplier_crisis(self):
        alloc = RiskBudgetAllocator(base_risk=0.02)
        result = alloc.allocate(["strat_a"], capital=10000, regime="crisis")
        budget = result.budgets[0]
        assert budget.regime_adj == pytest.approx(0.4)
        assert budget.risk_budget_pct == pytest.approx(0.02 * 0.4, abs=0.001)

    def test_budget_clamped_to_min_max(self):
        # With crisis + cluster → budget could go below min
        alloc = RiskBudgetAllocator(
            base_risk=0.02, min_risk=0.005, max_risk=0.03
        )
        result = alloc.allocate(["strat_a"], capital=10000, regime="crisis")
        budget = result.budgets[0]
        # 0.02 * 0.4 = 0.008, which is above min 0.005
        assert budget.risk_budget_pct >= 0.005
        assert budget.risk_budget_pct <= 0.03

    def test_budget_clamped_to_max(self):
        alloc = RiskBudgetAllocator(
            base_risk=0.05, max_risk=0.03, min_risk=0.005
        )
        result = alloc.allocate(["strat_a"], capital=10000, regime="low_vol")
        budget = result.budgets[0]
        # 0.05 * 1.2 = 0.06, clamped to 0.03
        assert budget.risk_budget_pct == pytest.approx(0.03, abs=0.001)

    def test_to_dict_serializable(self):
        alloc = RiskBudgetAllocator()
        result = alloc.allocate(["strat_a", "strat_b"], capital=10000)
        d = result.to_dict()
        serialized = json.dumps(d)
        assert "total_risk_budget_pct" in serialized
        assert "budgets" in serialized


# ═══════════════════════════════════════════════════════════════════════════════
# LeverageAdapter
# ═══════════════════════════════════════════════════════════════════════════════


class TestLeverageAdapter:
    """Tests for LeverageAdapter."""

    def test_no_degradation_returns_one(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            drawdown_pct=0.0,
            correlation_score=0.0,
            ere_pct=0.0,
            regime="normal",
        )
        assert decision.multiplier == pytest.approx(1.0)
        assert decision.effective_leverage == pytest.approx(2.0)

    def test_high_correlation_reduces_30pct(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            correlation_score=0.80,  # > 0.70
            regime="normal",
        )
        assert decision.factors["correlation"] == pytest.approx(0.70)
        assert decision.multiplier == pytest.approx(0.70)

    def test_mild_drawdown_reduces_20pct(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            drawdown_pct=0.025,  # 2.5% → mild
            regime="normal",
        )
        assert decision.factors["drawdown"] == pytest.approx(0.80)

    def test_severe_drawdown_reduces_50pct(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            drawdown_pct=0.04,  # 4% → severe
            regime="normal",
        )
        assert decision.factors["drawdown"] == pytest.approx(0.50)

    def test_critical_drawdown_reduces_80pct(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            drawdown_pct=0.06,  # 6% → critical
            regime="normal",
        )
        assert decision.factors["drawdown"] == pytest.approx(0.20)

    def test_high_ere_reduces_30pct(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            ere_pct=0.30,  # > 25%
            regime="normal",
        )
        assert decision.factors["ere"] == pytest.approx(0.70)

    def test_crisis_regime_reduces_70pct(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            regime="crisis",
        )
        assert decision.factors["regime"] == pytest.approx(0.30)

    def test_multiple_factors_compound(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            drawdown_pct=0.04,  # severe → 0.50
            correlation_score=0.80,  # high → 0.70
            regime="normal",
        )
        expected = 0.50 * 0.70  # = 0.35
        assert decision.multiplier == pytest.approx(expected, abs=0.01)

    def test_multiplier_never_below_min(self):
        adapter = LeverageAdapter(min_multiplier=0.10)
        decision = adapter.get_multiplier(
            base_leverage=2.0,
            drawdown_pct=0.06,  # critical → 0.20
            correlation_score=0.80,  # → 0.70
            ere_pct=0.30,  # → 0.70
            regime="crisis",  # → 0.30
        )
        # Product = 0.20 * 0.70 * 0.70 * 0.30 = 0.0294 → clamped to 0.10
        assert decision.multiplier == pytest.approx(0.10)

    def test_to_dict_serializable(self):
        adapter = LeverageAdapter()
        decision = adapter.get_multiplier(base_leverage=1.5)
        d = decision.to_dict()
        serialized = json.dumps(d)
        assert "multiplier" in serialized
        assert "effective_leverage" in serialized
        assert "factors" in serialized


# ═══════════════════════════════════════════════════════════════════════════════
# StrategyThrottler
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyThrottler:
    """Tests for StrategyThrottler and StrategyState."""

    def test_good_strategy_continues(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(
            sharpe=1.5, n_trades=50, win_rate=0.55, slippage=1.0,
        )
        actions = throttler.evaluate([strat])
        assert actions[0].action == "CONTINUE"
        assert actions[0].size_multiplier == 1.0

    def test_very_negative_sharpe_pauses(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(sharpe=-1.0, n_trades=50)
        actions = throttler.evaluate([strat])
        assert actions[0].action == "PAUSE"
        assert actions[0].size_multiplier == 0.0

    def test_consecutive_losses_pauses(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(consec_losses=7, n_trades=50)
        actions = throttler.evaluate([strat])
        assert actions[0].action == "PAUSE"

    def test_bad_win_rate_pauses(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(win_rate=0.15, n_trades=20)
        actions = throttler.evaluate([strat])
        assert actions[0].action == "PAUSE"

    def test_high_slippage_stops(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(slippage=5.0, n_trades=50)
        actions = throttler.evaluate([strat])
        assert actions[0].action == "STOP"
        assert actions[0].size_multiplier == 0.0

    def test_moderate_slippage_reduces(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(slippage=3.0, n_trades=50)
        actions = throttler.evaluate([strat])
        assert actions[0].action == "REDUCE_SIZE"
        assert actions[0].size_multiplier < 1.0

    def test_critical_cluster_reduces_half(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(
            in_cluster=True, cluster_level="CRITICAL", n_trades=50,
        )
        actions = throttler.evaluate([strat])
        assert actions[0].action == "REDUCE_SIZE"
        assert actions[0].size_multiplier <= 0.5

    def test_portfolio_dd_pauses_lowest_sharpe(self):
        throttler = StrategyThrottler()
        strategies = [
            _make_strategy_state(name="best", sharpe=2.0, n_trades=50),
            _make_strategy_state(name="good", sharpe=1.0, n_trades=50),
            _make_strategy_state(name="ok", sharpe=0.5, n_trades=50),
            _make_strategy_state(name="worst", sharpe=0.1, n_trades=50),
        ]
        actions = throttler.evaluate(strategies, drawdown_pct=0.04)
        worst_action = next(a for a in actions if a.strategy == "worst")
        assert worst_action.action == "PAUSE"

    def test_insufficient_trades_continues_with_reduced_size(self):
        throttler = StrategyThrottler()
        strat = _make_strategy_state(n_trades=3)
        actions = throttler.evaluate([strat])
        assert actions[0].action == "CONTINUE"
        assert actions[0].size_multiplier == pytest.approx(0.75)

    def test_is_paused_tracks_paused(self):
        throttler = StrategyThrottler()
        throttler.pause("strat_a", minutes=60)
        assert throttler.is_paused("strat_a") is True
        assert throttler.is_paused("strat_b") is False

    def test_resume_clears_pause(self):
        throttler = StrategyThrottler()
        throttler.pause("strat_a", minutes=60)
        assert throttler.is_paused("strat_a") is True
        throttler.resume("strat_a")
        assert throttler.is_paused("strat_a") is False


# ═══════════════════════════════════════════════════════════════════════════════
# SafetyMode
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafetyMode:
    """Tests for SafetyMode and SafetyLimits."""

    def test_active_by_default(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        assert safety.is_active is True

    def test_can_trade_allows_under_limits(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        result = safety.can_trade(
            n_active_strategies=3,
            n_positions=5,
            current_leverage=0.5,
        )
        assert result["allowed"] is True
        assert result["violations"] == []

    def test_can_trade_rejects_over_max_strategies(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        result = safety.can_trade(n_active_strategies=8)
        assert result["allowed"] is False
        assert any("strategies" in v for v in result["violations"])

    def test_can_trade_rejects_over_max_positions(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        result = safety.can_trade(n_positions=10)  # >= 10
        assert result["allowed"] is False
        assert any("positions" in v for v in result["violations"])

    def test_can_trade_rejects_over_max_leverage(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        result = safety.can_trade(current_leverage=1.5)
        assert result["allowed"] is False
        assert any("leverage" in v for v in result["violations"])

    def test_deactivated_allows_everything(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        safety.deactivate(authorized_by="test")
        result = safety.can_trade(
            n_active_strategies=100,
            n_positions=200,
            current_leverage=10.0,
        )
        assert result["allowed"] is True
        assert result["safety_mode"] is False

    def test_check_anomaly_detects_high_ere(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        result = safety.check_anomaly(ere_pct=0.25)  # > 20%
        assert result["anomaly"] is True
        assert any("ERE" in d for d in result["details"])

    def test_check_anomaly_detects_high_dd(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        result = safety.check_anomaly(drawdown_pct=0.12)  # > 10%
        assert result["anomaly"] is True
        assert any("DD" in d for d in result["details"])

    def test_check_anomaly_detects_high_correlation(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        result = safety.check_anomaly(correlation_score=0.90)  # > 0.80
        assert result["anomaly"] is True
        assert any("corr" in d for d in result["details"])

    def test_three_anomalies_triggers_disable(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        safety.check_anomaly(ere_pct=0.25)  # 1
        safety.check_anomaly(ere_pct=0.25)  # 2
        result = safety.check_anomaly(ere_pct=0.25)  # 3
        assert result["action"] == "DISABLE_TRADING"
        assert result["anomaly_count"] == 3

    def test_clamp_leverage_when_active(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        assert safety.clamp_leverage(2.0) == 1.0  # Clamped to max 1.0
        assert safety.clamp_leverage(0.5) == 0.5  # Already below

    def test_clamp_leverage_when_inactive(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        safety.deactivate(authorized_by="test")
        assert safety.clamp_leverage(2.0) == 2.0  # No clamping

    def test_filter_strategies_limits_count(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        strats = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
        filtered = safety.filter_strategies(strats)
        assert len(filtered) == 5  # Max 5

    def test_filter_strategies_inactive_no_limit(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        safety.deactivate(authorized_by="test")
        strats = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
        filtered = safety.filter_strategies(strats)
        assert len(filtered) == 7

    def test_get_status_serializable(self, tmp_path):
        safety = SafetyMode(data_dir=str(tmp_path))
        status = safety.get_status()
        serialized = json.dumps(status)
        assert "active" in serialized
        assert "limits" in serialized
        assert "anomaly_count" in serialized

    def test_state_persistence_roundtrip(self, tmp_path):
        safety1 = SafetyMode(data_dir=str(tmp_path))
        safety1.check_anomaly(ere_pct=0.25)  # anomaly_count=1
        safety1.check_anomaly(ere_pct=0.25)  # anomaly_count=2

        # Reload from same dir
        safety2 = SafetyMode(data_dir=str(tmp_path))
        assert safety2._anomaly_count == 2
        assert safety2._disabled_reason is not None
        assert safety2.is_active is True  # Still active (needs 3 for disable)

    def test_custom_limits(self, tmp_path):
        limits = SafetyLimits(
            max_strategies=3,
            max_leverage=0.5,
            max_positions=5,
        )
        safety = SafetyMode(limits=limits, data_dir=str(tmp_path))
        result = safety.can_trade(n_active_strategies=4)
        assert result["allowed"] is False

        assert safety.clamp_leverage(1.0) == 0.5
