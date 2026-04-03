"""Tests for Capital Deployment modules (U1-U8)."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════
# U1-01: Utilization Rate Calculator
# ═══════════════════════════════════════════════════════════════

class TestUtilizationRate:

    def test_zero_positions(self):
        from core.portfolio.utilization_rate import UtilizationRateCalculator, UtilizationLevel
        calc = UtilizationRateCalculator()
        snap = calc.calculate(
            positions_by_broker={"binance": [], "ibkr": []},
            equity_by_broker={"binance": 10000, "ibkr": 10000},
        )
        assert snap.utilization_pct == 0
        assert snap.level == UtilizationLevel.CRITICAL_LOW
        assert snap.cash_idle == 20000

    def test_normal_utilization(self):
        from core.portfolio.utilization_rate import UtilizationRateCalculator, UtilizationLevel
        calc = UtilizationRateCalculator()
        snap = calc.calculate(
            positions_by_broker={
                "ibkr": [{"value": 3000}, {"value": 2000}],
                "binance": [{"value": 4000}],
            },
            equity_by_broker={"ibkr": 10000, "binance": 10000},
            current_regime="MEAN_REVERT",
        )
        assert 40 < snap.utilization_pct < 50
        assert snap.level == UtilizationLevel.LOW  # 45% < 60% target min

    def test_broker_breakdown(self):
        from core.portfolio.utilization_rate import UtilizationRateCalculator
        calc = UtilizationRateCalculator()
        snap = calc.calculate(
            positions_by_broker={"ibkr": [{"value": 5000}], "binance": []},
            equity_by_broker={"ibkr": 10000, "binance": 10000},
        )
        assert snap.by_broker["ibkr"] == 50.0
        assert snap.by_broker["binance"] == 0.0

    def test_hours_tracking(self):
        from core.portfolio.utilization_rate import UtilizationRateCalculator
        calc = UtilizationRateCalculator()
        calc.calculate({"a": []}, {"a": 10000})
        assert calc.hours_effectively_stopped >= 0


# ═══════════════════════════════════════════════════════════════
# U2-01: Cash Drag Calculator
# ═══════════════════════════════════════════════════════════════

class TestCashDrag:

    def test_full_idle(self):
        from core.portfolio.cash_drag import CashDragCalculator
        calc = CashDragCalculator(target_annual_return=0.10)
        result = calc.calculate_daily_drag(45000, 0)
        assert result.cash_idle_usd == 45000
        assert result.daily_drag_usd == pytest.approx(45000 * 0.10 / 365, abs=0.1)

    def test_partial_deployment(self):
        from core.portfolio.cash_drag import CashDragCalculator
        calc = CashDragCalculator()
        result = calc.calculate_daily_drag(45000, 20000)
        assert result.cash_idle_usd == 25000
        assert result.utilization_pct == pytest.approx(44.4, abs=0.5)

    def test_cumulative(self):
        from core.portfolio.cash_drag import CashDragCalculator
        calc = CashDragCalculator()
        calc.calculate_daily_drag(45000, 0)
        calc.calculate_daily_drag(45000, 0)
        assert calc.cumulative_drag > 0

    def test_telegram_format(self):
        from core.portfolio.cash_drag import CashDragCalculator
        calc = CashDragCalculator()
        snap = calc.calculate_daily_drag(45000, 0)
        msg = calc.format_telegram(snap)
        assert "CAPITAL IDLE" in msg


# ═══════════════════════════════════════════════════════════════
# U4-01: Guard Pass Rate Tracker
# ═══════════════════════════════════════════════════════════════

class TestGuardPassRate:

    def test_basic_tracking(self):
        from core.risk.guard_pass_rate import GuardPassRateTracker
        tracker = GuardPassRateTracker()
        for _ in range(10):
            tracker.record("regime_check", True)
        for _ in range(5):
            tracker.record("regime_check", False)
        report = tracker.get_report()
        assert report["guards"]["regime_check"]["rate"] == pytest.approx(66.7, abs=0.1)

    def test_biggest_killers(self):
        from core.risk.guard_pass_rate import GuardPassRateTracker
        tracker = GuardPassRateTracker()
        for _ in range(10):
            tracker.record("easy_guard", True)
            tracker.record("hard_guard", False)
        report = tracker.get_report()
        assert "hard_guard" in report["biggest_killers"]

    def test_telegram_format(self):
        from core.risk.guard_pass_rate import GuardPassRateTracker
        tracker = GuardPassRateTracker()
        tracker.record("regime_check", True)
        msg = tracker.format_telegram()
        assert "regime_check" in msg


# ═══════════════════════════════════════════════════════════════
# U4-02: Adaptive Guards
# ═══════════════════════════════════════════════════════════════

class TestAdaptiveGuards:

    def test_low_utilization_more_permissive(self):
        from core.risk.adaptive_guards import AdaptiveGuards
        guards = AdaptiveGuards()
        result = guards.adjust("cash_min_pct", 0.10, utilization_pct=10)
        assert result.adjusted < 0.10  # More permissive (lower cash requirement)
        assert result.permissivity == 1.3

    def test_high_utilization_more_restrictive(self):
        from core.risk.adaptive_guards import AdaptiveGuards
        guards = AdaptiveGuards()
        result = guards.adjust("max_position_pct", 0.20, utilization_pct=85)
        assert result.adjusted < 0.20  # More restrictive (smaller positions)

    def test_fixed_guards_never_adjust(self):
        from core.risk.adaptive_guards import AdaptiveGuards
        guards = AdaptiveGuards()
        result = guards.adjust("kill_switch", 1.0, utilization_pct=5)
        assert result.adjusted == 1.0  # Never adjusts

    def test_nominal_at_50pct(self):
        from core.risk.adaptive_guards import AdaptiveGuards
        guards = AdaptiveGuards()
        perm = guards.get_permissivity(50)
        assert perm == 1.0


# ═══════════════════════════════════════════════════════════════
# U5-01: Always-On Carrier
# ═══════════════════════════════════════════════════════════════

class TestAlwaysOnCarrier:

    def test_basic_targets(self):
        from core.strategies.always_on_carrier import AlwaysOnCarrier
        carrier = AlwaysOnCarrier()
        targets = carrier.compute_targets(
            equity_by_broker={"ibkr": 10000},
            regime="TREND_STRONG",
        )
        assert len(targets) == 3  # 3 FX carry positions
        assert all(t.target_notional > 0 for t in targets)

    def test_panic_reduces_not_kills(self):
        from core.strategies.always_on_carrier import AlwaysOnCarrier
        carrier = AlwaysOnCarrier()
        targets = carrier.compute_targets({"ibkr": 10000}, regime="PANIC")
        assert all(t.target_notional > 0 for t in targets)  # Never 0!

    def test_rebalance_flag(self):
        from core.strategies.always_on_carrier import AlwaysOnCarrier
        carrier = AlwaysOnCarrier()
        targets = carrier.compute_targets(
            {"ibkr": 10000}, current_positions={}, regime="UNKNOWN",
        )
        assert all(t.needs_rebalance for t in targets)  # All need initial entry


# ═══════════════════════════════════════════════════════════════
# U5-02: Always-On Earn
# ═══════════════════════════════════════════════════════════════

class TestAlwaysOnEarn:

    def test_subscribe_idle_usdc(self):
        from core.crypto.always_on_earn import AlwaysOnEarn
        earn = AlwaysOnEarn()
        # Simulate 2h idle
        earn._idle_since["USDC"] = datetime.now() - timedelta(hours=2)
        actions = earn.check_idle_capital(
            balances={"USDC": 3000}, in_earn={"USDC": 0},
        )
        assert len(actions) == 1
        assert actions[0].action == "SUBSCRIBE"

    def test_no_subscribe_below_minimum(self):
        from core.crypto.always_on_earn import AlwaysOnEarn
        earn = AlwaysOnEarn()
        actions = earn.check_idle_capital(
            balances={"USDC": 50}, in_earn={},  # Below $100 minimum
        )
        assert len(actions) == 0

    def test_earn_status(self):
        from core.crypto.always_on_earn import AlwaysOnEarn
        earn = AlwaysOnEarn()
        status = earn.get_status(in_earn={"USDC": 5000, "BTC": 0.05})
        assert status.total_in_earn_usd > 0
        assert status.daily_yield_usd > 0


# ═══════════════════════════════════════════════════════════════
# U3-01: Allocation Gap Tracker
# ═══════════════════════════════════════════════════════════════

class TestAllocationGap:

    def test_aligned(self):
        from core.alloc.allocation_gap import AllocationGapTracker, GapStatus
        tracker = AllocationGapTracker()
        gaps = tracker.check(
            target_weights={"fx_carry": 0.20},
            actual_weights={"fx_carry": 0.18},
        )
        assert gaps[0].status == GapStatus.ALIGNED

    def test_blocked(self):
        from core.alloc.allocation_gap import AllocationGapTracker, GapStatus
        tracker = AllocationGapTracker()
        gaps = tracker.check(
            target_weights={"fx_carry": 0.25},
            actual_weights={"fx_carry": 0.0},
        )
        assert gaps[0].status == GapStatus.BLOCKED

    def test_misaligned(self):
        from core.alloc.allocation_gap import AllocationGapTracker, GapStatus
        tracker = AllocationGapTracker()
        gaps = tracker.check(
            target_weights={"fx_carry": 0.40},
            actual_weights={"fx_carry": 0.10},
        )
        assert gaps[0].status == GapStatus.MISALIGNED


# ═══════════════════════════════════════════════════════════════
# U6-01: Global Sizer
# ═══════════════════════════════════════════════════════════════

class TestGlobalSizer:

    def test_global_vs_broker(self):
        from core.alloc.global_sizer import GlobalPortfolioSizer
        sizer = GlobalPortfolioSizer(
            equity_by_broker={"binance": 10000, "ibkr": 10000, "alpaca": 30000},
        )
        result = sizer.size("btc_eth_dual_momentum", 0.15, kelly_fraction=0.25)
        assert result.raw_size_global > 1000  # $45K * 15% * 25% = $1687
        assert result.broker == "binance"

    def test_broker_cap(self):
        from core.alloc.global_sizer import GlobalPortfolioSizer
        sizer = GlobalPortfolioSizer(
            equity_by_broker={"binance": 5000, "ibkr": 10000, "alpaca": 30000},
        )
        result = sizer.size("btc_eth_dual_momentum", 0.30, kelly_fraction=0.50)
        # Raw = $45K * 30% * 50% = $6750, but Binance only has $5K * 80% = $4K cap
        assert result.capped
        assert result.final_size <= 5000 * 0.80

    def test_nav_total(self):
        from core.alloc.global_sizer import GlobalPortfolioSizer
        sizer = GlobalPortfolioSizer(
            equity_by_broker={"a": 10000, "b": 20000},
        )
        assert sizer.nav_total == 30000


# ═══════════════════════════════════════════════════════════════
# U8-01: Signal Aggregator
# ═══════════════════════════════════════════════════════════════

class TestSignalAggregator:

    def test_aggregate_same_direction(self):
        from core.signals.signal_aggregator import SignalAggregator
        agg = SignalAggregator(min_position_usd=100)
        agg.buffer("strat1", "BTC", "LONG", 60)
        agg.buffer("strat2", "BTC", "LONG", 50)
        orders = agg.aggregate()
        assert len(orders) == 1
        assert orders[0].total_size_usd == 110
        assert len(orders[0].contributing_strategies) == 2

    def test_no_aggregate_below_min(self):
        from core.signals.signal_aggregator import SignalAggregator
        agg = SignalAggregator(min_position_usd=100)
        agg.buffer("strat1", "BTC", "LONG", 30)
        orders = agg.aggregate()
        assert len(orders) == 0  # $30 < $100

    def test_conflicting_signals_net(self):
        from core.signals.signal_aggregator import SignalAggregator
        agg = SignalAggregator(min_position_usd=50)
        agg.buffer("strat1", "BTC", "LONG", 100)
        agg.buffer("strat2", "BTC", "SHORT", 40)
        orders = agg.aggregate()
        if orders:
            assert orders[0].direction == "LONG"
            assert orders[0].total_size_usd == 60

    def test_expiry(self):
        from core.signals.signal_aggregator import SignalAggregator, BufferedSignal
        agg = SignalAggregator(min_position_usd=100, expiry_hours=1.0)
        # Manually insert an old signal
        agg._buffer["BTC"].append(BufferedSignal(
            strategy="strat1", symbol="BTC", direction="LONG",
            size_usd=60, timestamp=datetime.now() - timedelta(hours=2),
        ))
        agg._expire_old()
        assert len(agg._buffer.get("BTC", [])) == 0


# ═══════════════════════════════════════════════════════════════
# U1-02: Deployment Monitor
# ═══════════════════════════════════════════════════════════════

class TestDeploymentMonitor:

    def test_basic_check(self):
        from core.portfolio.deployment_monitor import DeploymentMonitor
        monitor = DeploymentMonitor()
        report = monitor.check(
            positions_by_broker={"binance": [], "ibkr": []},
            equity_by_broker={"binance": 10000, "ibkr": 10000},
        )
        assert report.utilization["utilization_pct"] == 0
        assert len(report.recommendations) > 0

    def test_telegram_format(self):
        from core.portfolio.deployment_monitor import DeploymentMonitor
        monitor = DeploymentMonitor()
        report = monitor.check(
            positions_by_broker={"ibkr": [{"value": 5000}]},
            equity_by_broker={"ibkr": 10000},
        )
        msg = monitor.format_telegram(report)
        assert "NAV" in msg
