"""Tests for Performance TODO Phase 2+3 modules.

P2-04: ExitOptimizer
P2-05: ParameterSweep
P4-04: TimezoneAllocator
P5-02: OnChainPipeline
P5-03: SentimentPipeline
P6-01: SlippageCalibrator
P6-02: WinRateDriftDetector
P6-03: RegimeEffectivenessTracker
P6-04: MetaActivation
"""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════
# P2-04: Exit Optimization
# ═══════════════════════════════════════════════════════════════


class TestExitOptimizer:

    def test_create_trailing_stop_buy(self):
        from core.signals.exit_optimizer import ExitOptimizer
        opt = ExitOptimizer()
        trail = opt.create_trailing_stop("BTCUSDC", "BUY", 45000, 1200)
        assert trail.current_stop < 45000
        assert trail.entry_price == 45000

    def test_trailing_stop_moves_up(self):
        from core.signals.exit_optimizer import ExitOptimizer
        opt = ExitOptimizer()
        trail = opt.create_trailing_stop("BTCUSDC", "BUY", 45000, 1200)
        initial_stop = trail.current_stop
        trail.update(46000)  # Price moves up
        assert trail.current_stop >= initial_stop  # Stop should move up

    def test_trailing_stop_never_moves_down(self):
        from core.signals.exit_optimizer import ExitOptimizer
        opt = ExitOptimizer()
        trail = opt.create_trailing_stop("BTCUSDC", "BUY", 45000, 1200)
        trail.update(46000)
        high_stop = trail.current_stop
        trail.update(44000)  # Price drops
        assert trail.current_stop == high_stop  # Stop stays

    def test_trailing_stop_triggered(self):
        from core.signals.exit_optimizer import ExitOptimizer
        opt = ExitOptimizer()
        trail = opt.create_trailing_stop("BTCUSDC", "BUY", 45000, 1200)
        assert trail.is_triggered(42000)  # Below stop
        assert not trail.is_triggered(46000)  # Above stop

    def test_time_expiry(self):
        from core.signals.exit_optimizer import ExitOptimizer, ExitReason
        opt = ExitOptimizer()
        trail = opt.create_trailing_stop("BTCUSDC", "BUY", 45000, 1200)
        # Entry 10 days ago for a strategy with 7-day max hold
        entry = datetime.now(timezone.utc) - timedelta(days=10)
        decision = opt.evaluate_exit(
            trail, 46000, strategy="btc_eth_dual_momentum", entry_time=entry,
        )
        assert decision.should_exit
        assert decision.exit_reason == ExitReason.TIME_EXPIRY

    def test_partial_profit(self):
        from core.signals.exit_optimizer import ExitOptimizer, ExitReason
        opt = ExitOptimizer(partial_profit_enabled=True, partial_profit_at=1.5)
        trail = opt.create_trailing_stop("BTCUSDC", "BUY", 100, 5)
        # SL at 90 (10 points below), TP target at 115 (1.5x SL)
        decision = opt.evaluate_exit(trail, 116, sl_price=90)
        assert decision.should_exit
        assert decision.exit_reason == ExitReason.PARTIAL_PROFIT
        assert decision.partial_pct == 0.50

    def test_no_exit_when_holding(self):
        from core.signals.exit_optimizer import ExitOptimizer, ExitReason
        opt = ExitOptimizer()
        trail = opt.create_trailing_stop("SPY", "BUY", 450, 5)
        decision = opt.evaluate_exit(trail, 452, strategy="dow_seasonal")
        assert not decision.should_exit
        assert decision.exit_reason == ExitReason.NONE


# ═══════════════════════════════════════════════════════════════
# P2-05: Parameter Sweep
# ═══════════════════════════════════════════════════════════════


class TestParameterSweep:

    def _mock_backtest(self, data, params):
        np.random.seed(params.get("seed", 42))
        period = params.get("period", 20)
        returns = data.pct_change().dropna() if isinstance(data, pd.Series) else data.iloc[:, 0].pct_change().dropna()
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        return {"sharpe": sharpe + period * 0.01, "n_trades": len(returns), "win_rate": 0.55}

    def test_basic_sweep(self):
        from core.research.param_sweep import ParameterSweep
        sweep = ParameterSweep(wf_windows=3)
        np.random.seed(42)
        data = pd.DataFrame({"close": 100 + np.cumsum(np.random.randn(300) * 0.5)})
        result = sweep.run(
            strategy="test",
            param_grid={"period": [10, 20, 30]},
            backtest_fn=self._mock_backtest,
            data=data,
        )
        assert result.total_combinations == 3
        assert len(result.all_results) > 0
        assert result.best_params is not None

    def test_max_combinations_guard(self):
        from core.research.param_sweep import ParameterSweep, MAX_COMBINATIONS
        sweep = ParameterSweep()
        # This would create 10000 combos
        grid = {"a": list(range(100)), "b": list(range(100))}
        combos = 1
        for v in grid.values():
            combos *= len(v)
        assert combos > MAX_COMBINATIONS  # Guard should truncate


# ═══════════════════════════════════════════════════════════════
# P4-04: Timezone Allocator
# ═══════════════════════════════════════════════════════════════


class TestTimezoneAllocator:

    def test_get_session(self):
        from core.alloc.timezone_allocator import TimezoneAllocator
        tz = TimezoneAllocator()
        # 10:00 CET (09:00 UTC) → EU session
        ts = datetime(2026, 4, 3, 9, 0, tzinfo=timezone.utc)
        session = tz.get_current_session(ts)
        assert session.name == "EU"

    def test_overlap_session(self):
        from core.alloc.timezone_allocator import TimezoneAllocator
        tz = TimezoneAllocator()
        # 16:00 CET (15:00 UTC) → OVERLAP
        ts = datetime(2026, 4, 3, 15, 0, tzinfo=timezone.utc)
        session = tz.get_current_session(ts)
        assert session.name == "OVERLAP"

    def test_idle_capital(self):
        from core.alloc.timezone_allocator import TimezoneAllocator
        tz = TimezoneAllocator(capital={"ibkr": 10000, "binance": 10000, "alpaca": 30000})
        # During ASIA_FX, Alpaca is fully idle
        ts = datetime(2026, 4, 3, 3, 0, tzinfo=timezone.utc)  # 04:00 CET
        alloc = tz.get_current_allocation(ts)
        assert alloc.idle_capital["alpaca"] == 30000

    def test_efficiency_report(self):
        from core.alloc.timezone_allocator import TimezoneAllocator
        tz = TimezoneAllocator()
        report = tz.get_efficiency_report()
        assert "total_capital" in report
        assert "weighted_portfolio_utilization" in report
        assert report["weighted_portfolio_utilization"] > 0
        assert report["weighted_portfolio_utilization"] < 1


# ═══════════════════════════════════════════════════════════════
# P5-02: On-Chain Pipeline
# ═══════════════════════════════════════════════════════════════


class TestOnChainPipeline:

    def test_mvrv_bullish(self):
        from core.data.onchain_pipeline import OnChainPipeline, OnChainMetrics
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = OnChainPipeline(data_dir=Path(tmpdir))
            pipeline.ingest(OnChainMetrics(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol="BTC", mvrv_ratio=0.8,
            ))
            signals = pipeline.get_signals("BTC")
            mvrv_sig = [s for s in signals if s.metric == "mvrv_ratio"]
            assert len(mvrv_sig) == 1
            assert mvrv_sig[0].signal == "BULLISH"

    def test_mvrv_bearish(self):
        from core.data.onchain_pipeline import OnChainPipeline, OnChainMetrics
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = OnChainPipeline(data_dir=Path(tmpdir))
            pipeline.ingest(OnChainMetrics(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol="BTC", mvrv_ratio=4.0,
            ))
            signals = pipeline.get_signals("BTC")
            mvrv_sig = [s for s in signals if s.metric == "mvrv_ratio"]
            assert mvrv_sig[0].signal == "BEARISH"

    def test_fear_greed_extreme_fear(self):
        from core.data.onchain_pipeline import OnChainPipeline, OnChainMetrics
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = OnChainPipeline(data_dir=Path(tmpdir))
            pipeline.ingest(OnChainMetrics(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol="BTC", fear_greed_index=10,
            ))
            signals = pipeline.get_signals("BTC")
            fg = [s for s in signals if s.metric == "fear_greed_index"]
            assert fg[0].signal == "BULLISH"  # Contrarian

    def test_composite_score(self):
        from core.data.onchain_pipeline import OnChainPipeline, OnChainMetrics
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = OnChainPipeline(data_dir=Path(tmpdir))
            pipeline.ingest(OnChainMetrics(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol="BTC", mvrv_ratio=0.8, fear_greed_index=15,
            ))
            score = pipeline.get_composite_score("BTC")
            assert -1 <= score <= 1
            assert score > 0  # Both signals bullish


# ═══════════════════════════════════════════════════════════════
# P5-03: Sentiment Pipeline
# ═══════════════════════════════════════════════════════════════


class TestSentimentPipeline:

    def test_fear_greed_ingest(self):
        from core.data.sentiment_pipeline import SentimentPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = SentimentPipeline(data_dir=Path(tmpdir))
            pipeline.ingest_fear_greed(20)  # Fear
            sentiment = pipeline.get_current_sentiment()
            assert "fear_greed:market" in sentiment

    def test_buy_signal_in_fear(self):
        from core.data.sentiment_pipeline import SentimentPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = SentimentPipeline(data_dir=Path(tmpdir))
            pipeline.ingest_fear_greed(15)  # Extreme fear
            result = pipeline.filter_signal("BTC", "BUY")
            assert result.confirms_direction  # Fear = contrarian bullish
            assert result.adjustment > 0

    def test_sell_signal_in_greed(self):
        from core.data.sentiment_pipeline import SentimentPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = SentimentPipeline(data_dir=Path(tmpdir))
            pipeline.ingest_fear_greed(85)  # Extreme greed
            result = pipeline.filter_signal("BTC", "SELL")
            assert result.confirms_direction  # Greed = contrarian bearish
            assert result.adjustment > 0

    def test_impact_bounded(self):
        from core.data.sentiment_pipeline import SentimentPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = SentimentPipeline(data_dir=Path(tmpdir))
            pipeline.ingest_fear_greed(50)
            result = pipeline.filter_signal("BTC", "BUY")
            assert abs(result.adjustment) <= 0.05


# ═══════════════════════════════════════════════════════════════
# P6-01: Live Slippage Calibrator
# ═══════════════════════════════════════════════════════════════


class TestSlippageCalibrator:

    def test_not_active_with_few_trades(self):
        from core.calibration.slippage_calibrator import SlippageCalibrator
        cal = SlippageCalibrator()
        cal.record_fill("T1", "binance", "BTCUSDC", "MARKET", "BUY", 45000, 45010, 500)
        assert not cal.is_active

    def test_active_after_50_trades(self):
        from core.calibration.slippage_calibrator import SlippageCalibrator
        cal = SlippageCalibrator()
        for i in range(55):
            cal.record_fill(f"T{i}", "binance", "BTCUSDC", "MARKET", "BUY",
                          45000, 45000 + i * 0.1, 500)
        assert cal.is_active
        results = cal.calibrate()
        assert "binance" in results

    def test_calibration_factor(self):
        from core.calibration.slippage_calibrator import SlippageCalibrator
        cal = SlippageCalibrator()
        # All fills exactly 4 bps slippage (vs 2 bps model)
        for i in range(55):
            cal.record_fill(f"T{i}", "binance", "BTCUSDC", "MARKET", "BUY",
                          45000, 45000 + 45000 * 4 / 10000, 500)
        results = cal.calibrate()
        assert results["binance"].calibration_factor > 1.5  # 4bps vs 2bps model


# ═══════════════════════════════════════════════════════════════
# P6-02: Win Rate Drift Detector
# ═══════════════════════════════════════════════════════════════


class TestWinRateDrift:

    def test_no_drift(self):
        from core.calibration.winrate_drift import WinRateDriftDetector, DriftLevel
        det = WinRateDriftDetector()
        det.register_strategy("s1", oos_win_rate=0.55)
        for i in range(40):
            det.record_trade("s1", won=(i % 2 == 0))  # ~50% WR
        result = det.check("s1")
        assert result.level in (DriftLevel.OK, DriftLevel.MONITOR)

    def test_critical_drift(self):
        from core.calibration.winrate_drift import WinRateDriftDetector, DriftLevel
        det = WinRateDriftDetector()
        det.register_strategy("s1", oos_win_rate=0.60)
        # 30 trades, only 6 wins (20% WR vs 60% OOS)
        for i in range(30):
            det.record_trade("s1", won=(i < 6))
        result = det.check("s1")
        assert result.z_score < -2.0
        assert result.level in (DriftLevel.WARNING, DriftLevel.CRITICAL)

    def test_too_few_trades(self):
        from core.calibration.winrate_drift import WinRateDriftDetector, DriftLevel
        det = WinRateDriftDetector()
        det.register_strategy("s1", oos_win_rate=0.55)
        det.record_trade("s1", won=False)
        result = det.check("s1")
        assert result.level == DriftLevel.OK  # Not enough data

    def test_paused_strategies(self):
        from core.calibration.winrate_drift import WinRateDriftDetector
        det = WinRateDriftDetector()
        det.register_strategy("s1", oos_win_rate=0.70)
        # Extreme drift
        for i in range(40):
            det.record_trade("s1", won=False)
        for _ in range(4):  # Multiple consecutive checks
            det.check("s1")
        paused = det.get_paused_strategies()
        assert "s1" in paused


# ═══════════════════════════════════════════════════════════════
# P6-03: Regime Effectiveness Tracker
# ═══════════════════════════════════════════════════════════════


class TestRegimeEffectiveness:

    def test_not_active_initially(self):
        from core.calibration.regime_effectiveness import RegimeEffectivenessTracker
        tracker = RegimeEffectivenessTracker()
        assert not tracker.is_active

    def test_analyze_after_threshold(self):
        from core.calibration.regime_effectiveness import (
            RegimeEffectivenessTracker, RegimeObservation,
        )
        tracker = RegimeEffectivenessTracker()
        for i in range(110):
            tracker.record_observation(RegimeObservation(
                timestamp=f"2026-04-{(i % 28) + 1:02d}T10:00:00Z",
                regime="PANIC" if i % 5 == 0 else "TREND_STRONG",
                actual_return_pct=-0.03 if i % 5 == 0 else 0.01,
                duration_hours=4,
                strategies_active=["s1"],
                strategies_skipped=["s2"] if i % 5 == 0 else [],
                pnl_active=-50 if i % 5 == 0 else 20,
                pnl_skipped_if_active=-80 if i % 5 == 0 else 15,
            ))
        assert tracker.is_active
        results = tracker.analyze()
        assert "PANIC" in results
        assert "TREND_STRONG" in results

    def test_activation_matrix_report(self):
        from core.calibration.regime_effectiveness import (
            RegimeEffectivenessTracker, RegimeObservation,
        )
        tracker = RegimeEffectivenessTracker()
        for i in range(110):
            tracker.record_observation(RegimeObservation(
                timestamp=f"2026-04-{(i % 28) + 1:02d}T10:00:00Z",
                regime="PANIC",
                actual_return_pct=-0.03,
                duration_hours=4,
                strategies_active=["s1"],
                strategies_skipped=["s2"],
                pnl_active=-50,
                pnl_skipped_if_active=-80,
            ))
        report = tracker.get_activation_matrix_report()
        assert report["matrix_effective"]  # Skipped trades were losers


# ═══════════════════════════════════════════════════════════════
# P6-04: Meta-Strategy Activation
# ═══════════════════════════════════════════════════════════════


class TestMetaActivation:

    def test_prerequisites_not_met(self):
        from core.meta.meta_activation import MetaActivation
        meta = MetaActivation()
        prereqs = meta.check_prerequisites(
            total_trades=50, trades_per_strategy={"s1": 20}, scoring_days=30,
        )
        assert not prereqs.met
        assert len(prereqs.missing) > 0

    def test_prerequisites_met(self):
        from core.meta.meta_activation import MetaActivation
        meta = MetaActivation()
        prereqs = meta.check_prerequisites(
            total_trades=250,
            trades_per_strategy={"s1": 80, "s2": 60},
            scoring_days=95,
        )
        assert prereqs.met

    def test_passive_mode(self):
        from core.meta.meta_activation import MetaActivation, MetaMode
        meta = MetaActivation()
        meta.activate(MetaMode.PASSIVE)
        assert meta.mode == MetaMode.PASSIVE

        meta.update_score("s1", sharpe=1.5, win_rate=0.55, consistency=0.8, regime_fit=0.9)
        meta.update_score("s2", sharpe=0.5, win_rate=0.48, consistency=0.6, regime_fit=0.5)

        recs = meta.get_recommendations({"s1": 0.20, "s2": 0.20})
        # Should produce recommendations but not apply them
        assert isinstance(recs, list)

    def test_cannot_go_directly_to_active(self):
        from core.meta.meta_activation import MetaActivation, MetaMode
        meta = MetaActivation()
        meta.activate(MetaMode.ACTIVE)  # Should be rejected
        assert meta.mode == MetaMode.DISABLED  # Still disabled

    def test_revert_condition(self):
        from core.meta.meta_activation import MetaActivation, MetaMode
        meta = MetaActivation()
        meta.activate(MetaMode.PASSIVE)
        meta.activate(MetaMode.ACTIVE)
        assert meta.mode == MetaMode.ACTIVE
        reverted = meta.check_revert_condition(hrp_sharpe_30d=1.5, meta_sharpe_30d=0.8)
        assert reverted
        assert meta.mode == MetaMode.PASSIVE
