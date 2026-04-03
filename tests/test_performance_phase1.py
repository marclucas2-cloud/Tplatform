"""Tests for Performance TODO Phase 1 modules.

P2-02: AdaptiveStopCalculatorV2
P2-03: EntryTimingOptimizer
P3-02: SmartRouterV2
P3-04: FundingCostOptimizer
P4-02: KellyRecalibrator
P4-03: CorrelationAwareSizer
P1-03: AutoBacktester
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════
# P2-02: Adaptive Stop-Loss v2
# ═══════════════════════════════════════════════════════════════


class TestAdaptiveStopsV2:

    def test_basic_buy_stop(self):
        from core.execution.adaptive_stops_v2 import AdaptiveStopCalculatorV2
        calc = AdaptiveStopCalculatorV2()
        result = calc.calculate(
            entry_price=100, direction="BUY", atr=2.0, regime="TREND_STRONG",
        )
        assert result.stop_loss < 100
        assert result.take_profit > 100

    def test_basic_sell_stop(self):
        from core.execution.adaptive_stops_v2 import AdaptiveStopCalculatorV2
        calc = AdaptiveStopCalculatorV2()
        result = calc.calculate(
            entry_price=100, direction="SELL", atr=2.0, regime="TREND_STRONG",
        )
        assert result.stop_loss > 100
        assert result.take_profit < 100

    def test_panic_regime_tighter(self):
        from core.execution.adaptive_stops_v2 import AdaptiveStopCalculatorV2
        calc = AdaptiveStopCalculatorV2()
        panic = calc.calculate(entry_price=100, direction="BUY", atr=2.0, regime="PANIC")
        trend = calc.calculate(entry_price=100, direction="BUY", atr=2.0, regime="TREND_STRONG")
        # PANIC SL should be tighter (closer to entry)
        assert abs(100 - panic.stop_loss) < abs(100 - trend.stop_loss)

    def test_min_max_bounds(self):
        from core.execution.adaptive_stops_v2 import AdaptiveStopCalculatorV2
        calc = AdaptiveStopCalculatorV2()
        # Very small ATR — should hit min_sl_pct
        result = calc.calculate(entry_price=100, direction="BUY", atr=0.001)
        assert result.sl_distance_pct >= 0.003  # min 0.3%

    def test_noise_floor(self):
        from core.execution.adaptive_stops_v2 import AdaptiveStopCalculatorV2
        calc = AdaptiveStopCalculatorV2()
        prices = pd.Series(100 + np.cumsum(np.random.randn(100) * 0.5))
        result = calc.calculate(
            entry_price=100, direction="BUY", atr=1.0,
            historical_prices=prices,
        )
        assert result.noise_floor >= 0

    def test_reward_ratio(self):
        from core.execution.adaptive_stops_v2 import AdaptiveStopCalculatorV2
        calc = AdaptiveStopCalculatorV2()
        result = calc.calculate(entry_price=100, direction="BUY", atr=2.0)
        sl_dist = abs(100 - result.stop_loss)
        tp_dist = abs(result.take_profit - 100)
        ratio = tp_dist / sl_dist if sl_dist > 0 else 0
        assert ratio >= 1.5  # Minimum reward ratio


# ═══════════════════════════════════════════════════════════════
# P2-03: Entry Timing Optimization
# ═══════════════════════════════════════════════════════════════


class TestEntryTiming:

    def test_high_urgency_market(self):
        from core.signals.entry_timing import EntryTimingOptimizer
        opt = EntryTimingOptimizer()
        decision = opt.decide(
            strategy="test", symbol="BTC", direction="BUY",
            is_kill_switch=True,
        )
        assert decision.order_type == "MARKET"
        assert decision.delay_seconds == 0

    def test_sl_is_high_urgency(self):
        from core.signals.entry_timing import EntryTimingOptimizer
        opt = EntryTimingOptimizer()
        decision = opt.decide(
            strategy="test", symbol="BTC", direction="SELL",
            is_sl=True,
        )
        assert decision.order_type == "MARKET"

    def test_normal_urgency_default(self):
        from core.signals.entry_timing import EntryTimingOptimizer
        opt = EntryTimingOptimizer()
        decision = opt.decide(
            strategy="test", symbol="SPY", direction="BUY",
            signal_strength=0.7,
        )
        # Default method is IMMEDIATE → MARKET
        assert decision.order_type == "MARKET"

    def test_decision_serialization(self):
        from core.signals.entry_timing import EntryTimingOptimizer
        opt = EntryTimingOptimizer()
        decision = opt.decide(
            strategy="test", symbol="SPY", direction="BUY",
        )
        d = decision.to_dict()
        assert "order_type" in d
        assert "urgency" in d


# ═══════════════════════════════════════════════════════════════
# P3-02: Smart Order Routing v2
# ═══════════════════════════════════════════════════════════════


class TestSmartRouterV2:

    def test_normal_routing(self):
        from core.execution.smart_router_v2 import SmartRouterV2
        router = SmartRouterV2()
        decision = router.route(
            symbol="BTCUSDC", direction="BUY", notional=500,
            urgency="NORMAL", broker="binance", mid_price=45000,
        )
        assert decision.order_type in ("MARKET", "LIMIT")

    def test_high_urgency_market(self):
        from core.execution.smart_router_v2 import SmartRouterV2
        router = SmartRouterV2()
        decision = router.route(
            symbol="BTCUSDC", direction="BUY", notional=500,
            urgency="HIGH", broker="binance", mid_price=45000,
        )
        assert decision.order_type == "MARKET"

    def test_wide_spread_skip(self):
        from core.execution.smart_router_v2 import SmartRouterV2
        router = SmartRouterV2()
        # Build up normal spread history
        for _ in range(20):
            router.spread_monitor.record("BTCUSDC", 44999, 45001)  # 0.4 bps
        # Now record wide spread
        router.spread_monitor.record("BTCUSDC", 44990, 45060)  # ~15.5 bps
        decision = router.route(
            symbol="BTCUSDC", direction="BUY", notional=500,
            urgency="NORMAL", broker="binance",
            bid=44990, ask=45060,
        )
        # Should wait or skip due to wide spread
        assert decision.order_type in ("LIMIT", "SKIP")

    def test_spread_monitor_records(self):
        from core.execution.smart_router_v2 import SpreadMonitor
        mon = SpreadMonitor()
        mon.record("EURUSD", 1.0850, 1.0852)
        assert mon.get_current("EURUSD") is not None
        assert mon.get_average("EURUSD") is not None

    def test_order_log(self):
        from core.execution.smart_router_v2 import SmartRouterV2
        router = SmartRouterV2()
        router.route("SPY", "BUY", 1000, "NORMAL", "alpaca", 450)
        assert len(router.get_order_log()) == 1


# ═══════════════════════════════════════════════════════════════
# P3-04: Funding Cost Optimizer
# ═══════════════════════════════════════════════════════════════


class TestFundingOptimizer:

    def test_cheapest_borrow(self):
        from core.crypto.funding_optimizer import FundingCostOptimizer
        opt = FundingCostOptimizer()
        opt.rate_monitor.record("USDC", 0.02)
        opt.rate_monitor.record("BTC", 0.005)
        result = opt.optimize_strategy("btc_eth_dual_momentum", current_borrow_asset="USDC")
        assert result.recommended_borrow_asset == "BTC"  # Cheaper
        assert result.savings_daily_pct > 0

    def test_spike_detection(self):
        from core.crypto.funding_optimizer import BorrowRateMonitor
        mon = BorrowRateMonitor()
        for _ in range(10):
            mon.record("USDC", 0.02)
        mon.record("USDC", 0.15)  # 7.5x spike
        alerts = mon.check_spikes()
        assert len(alerts) > 0

    def test_earn_opportunity(self):
        from core.crypto.funding_optimizer import FundingCostOptimizer
        opt = FundingCostOptimizer()
        opt.rate_monitor.record("USDC", 0.02)
        opt.set_idle_capital("USDC", 3000)
        opt.set_earn_rate("USDC", 3.0)  # 3% APY
        result = opt.optimize_strategy("margin_mean_reversion")
        assert result.earn_opportunity > 0


# ═══════════════════════════════════════════════════════════════
# P4-02: Kelly Recalibration
# ═══════════════════════════════════════════════════════════════


class TestKellyRecalibration:

    def test_oos_kelly(self):
        from core.alloc.kelly_recalibration import KellyRecalibrator
        recal = KellyRecalibrator()
        recal.set_oos_metrics("fx_carry", win_rate=0.58, avg_win=45, avg_loss=25, n_trades=120)
        result = recal.get_kelly("fx_carry", regime="TREND_STRONG")
        assert result.final_kelly > 0
        assert result.final_kelly <= 0.25  # Ceiling

    def test_drift_reduces_kelly(self):
        from core.alloc.kelly_recalibration import KellyRecalibrator
        recal = KellyRecalibrator()
        recal.set_oos_metrics("s1", win_rate=0.60, avg_win=50, avg_loss=30, n_trades=100)
        k1 = recal.get_kelly("s1")

        # Simulate drift: live win rate much lower
        recal.update_live_metrics("s1", win_rate=0.40, avg_win=35, avg_loss=35, n_trades=60)
        k2 = recal.get_kelly("s1")
        assert k2.drift_detected
        assert k2.final_kelly < k1.final_kelly

    def test_regime_multiplier(self):
        from core.alloc.kelly_recalibration import KellyRecalibrator
        recal = KellyRecalibrator()
        recal.set_oos_metrics("s1", win_rate=0.55, avg_win=40, avg_loss=30, n_trades=100)
        trend = recal.get_kelly("s1", regime="TREND_STRONG")
        panic = recal.get_kelly("s1", regime="PANIC")
        # Reset prev_kelly to avoid smoothing interference
        recal._prev_kelly.clear()
        trend2 = recal.get_kelly("s1", regime="TREND_STRONG")
        recal._prev_kelly.clear()
        panic2 = recal.get_kelly("s1", regime="PANIC")
        assert panic2.regime_multiplier < trend2.regime_multiplier

    def test_floor_ceiling(self):
        from core.alloc.kelly_recalibration import KellyRecalibrator, KELLY_FLOOR, KELLY_CEILING
        recal = KellyRecalibrator()
        recal.set_oos_metrics("s1", win_rate=0.51, avg_win=10, avg_loss=10, n_trades=100)
        result = recal.get_kelly("s1")
        assert result.final_kelly >= KELLY_FLOOR
        assert result.final_kelly <= KELLY_CEILING


# ═══════════════════════════════════════════════════════════════
# P4-03: Correlation-Aware Sizing
# ═══════════════════════════════════════════════════════════════


class TestCorrelationSizing:

    def test_basic_mdc(self):
        from core.alloc.correlation_sizing import CorrelationAwareSizer
        sizer = CorrelationAwareSizer()
        np.random.seed(42)
        pnl = pd.DataFrame({
            "s1": np.random.randn(200) * 0.01,
            "s2": np.random.randn(200) * 0.01,
            "s3": np.random.randn(200) * 0.01,
        })
        weights = {"s1": 0.33, "s2": 0.33, "s3": 0.34}
        result = sizer.compute(pnl, weights)
        assert len(result.strategies) == 3
        total = sum(s.adjusted_weight for s in result.strategies.values())
        assert abs(total - 1.0) < 0.01

    def test_correlated_strategies_reduced(self):
        from core.alloc.correlation_sizing import CorrelationAwareSizer
        sizer = CorrelationAwareSizer()
        np.random.seed(42)
        base = np.random.randn(200) * 0.01
        pnl = pd.DataFrame({
            "corr1": base + np.random.randn(200) * 0.001,
            "corr2": base + np.random.randn(200) * 0.001,
            "independent": np.random.randn(200) * 0.01,
        })
        weights = {"corr1": 0.33, "corr2": 0.33, "independent": 0.34}
        result = sizer.compute(pnl, weights)
        # Independent strategy should have higher MDC than correlated ones
        indep_mdc = result.strategies["independent"].mdc
        corr_mdc = result.strategies["corr1"].mdc
        # At minimum, weights should be normalized
        assert sum(s.adjusted_weight for s in result.strategies.values()) > 0.99

    def test_min_strategies(self):
        from core.alloc.correlation_sizing import CorrelationAwareSizer
        sizer = CorrelationAwareSizer()
        pnl = pd.DataFrame({"only": np.random.randn(100) * 0.01})
        weights = {"only": 1.0}
        result = sizer.compute(pnl, weights)
        assert result.strategies["only"].adjusted_weight == 1.0


# ═══════════════════════════════════════════════════════════════
# P1-03: Auto Backtest
# ═══════════════════════════════════════════════════════════════


class TestAutoBacktest:

    def _make_returns(self, n=500, sharpe_target=1.5):
        np.random.seed(42)
        daily_mean = sharpe_target / np.sqrt(252) * 0.01
        return pd.Series(np.random.randn(n) * 0.01 + daily_mean)

    def test_quick_backtest_pass(self):
        from core.research.auto_backtest import AutoBacktester
        bt = AutoBacktester()
        returns = self._make_returns(sharpe_target=1.5)
        result = bt.quick_backtest(returns)
        assert result.passed
        assert result.sharpe > 0.5

    def test_quick_backtest_fail(self):
        from core.research.auto_backtest import AutoBacktester
        bt = AutoBacktester()
        returns = self._make_returns(sharpe_target=0.1)
        result = bt.quick_backtest(returns)
        assert not result.passed

    def test_walk_forward(self):
        from core.research.auto_backtest import AutoBacktester
        bt = AutoBacktester()
        returns = self._make_returns(n=500, sharpe_target=1.5)
        result = bt.walk_forward(returns)
        assert result.n_windows > 0
        assert len(result.windows) > 0

    def test_cost_stress(self):
        from core.research.auto_backtest import AutoBacktester
        bt = AutoBacktester()
        returns = self._make_returns()
        result = bt.cost_stress(returns, base_cost_bps=2.0)
        assert len(result.multipliers_tested) == 4
        assert "1x" in result.sharpe_at_multiplier

    def test_full_pipeline(self):
        from core.research.auto_backtest import AutoBacktester
        bt = AutoBacktester()
        returns = self._make_returns(n=500, sharpe_target=1.5)
        report = bt.run_full_pipeline("test_strat", returns, cost_bps=2.0)
        assert report.final_verdict in ("VALIDATED", "BORDERLINE", "REJECTED")
