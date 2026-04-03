"""Tests for Performance TODO Phase 0 modules.

P3-01: CostAuditor
P3-03: CommissionBurnAnalyzer
P2-01: SignalQualityFilter
P4-01: HRPDiagnostic
P5-01: DataQualityScorer
P1-01: ResearchPipeline
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════
# P3-01: Cost Model Audit
# ═══════════════════════════════════════════════════════════════


class TestCostAuditor:

    def test_audit_no_real_data(self):
        from core.costs.cost_audit import CostAuditor
        auditor = CostAuditor()
        report = auditor.run_audit()
        assert report.results
        # All deltas should be None (no real data)
        for key, result in report.results.items():
            assert result.spread_delta_pct is None

    def test_audit_with_real_spread(self):
        from core.costs.cost_audit import CostAuditor
        auditor = CostAuditor()
        auditor.register_real_spread("ibkr", "EURUSD", 0.8)
        report = auditor.run_audit()
        r = report.results["ibkr:EURUSD"]
        assert r.real_spread_bps == 0.8
        assert r.spread_delta_pct == -20.0  # 0.8 vs 1.0 model
        assert not r.recalibration_needed

    def test_audit_recalibration_needed(self):
        from core.costs.cost_audit import CostAuditor
        auditor = CostAuditor()
        auditor.register_real_spread("ibkr", "EURUSD", 2.5)  # 150% over model
        report = auditor.run_audit()
        r = report.results["ibkr:EURUSD"]
        assert r.recalibration_needed

    def test_audit_summary(self):
        from core.costs.cost_audit import CostAuditor
        auditor = CostAuditor()
        auditor.register_real_spread("ibkr", "EURUSD", 0.8)
        report = auditor.run_audit()
        report.compute_summary()
        assert "total_instruments" in report.summary
        assert report.summary["total_instruments"] > 0

    def test_save_report(self):
        from core.costs.cost_audit import CostAuditor
        auditor = CostAuditor()
        report = auditor.run_audit()
        report.compute_summary()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_audit.json"
            report.save(path)
            assert path.exists()
            data = json.loads(path.read_text())
            assert "summary" in data


# ═══════════════════════════════════════════════════════════════
# P3-03: Commission Burn Analysis
# ═══════════════════════════════════════════════════════════════


class TestCommissionBurn:

    def _make_trades(self, n=50, avg_pnl=10, avg_notional=1000):
        trades = []
        for i in range(n):
            pnl = avg_pnl * (1 if i % 3 != 0 else -0.5)
            trades.append({"notional": avg_notional, "pnl_gross": pnl})
        return trades

    def test_safe_strategy(self):
        from core.costs.commission_burn import CommissionBurnAnalyzer, BurnLevel
        analyzer = CommissionBurnAnalyzer()
        trades = self._make_trades(n=50, avg_pnl=20, avg_notional=5000)
        analyzer.add_trades("good_strat", "alpaca", trades)
        report = analyzer.analyze()
        burn = report.strategies["good_strat"]
        assert burn.burn_level == BurnLevel.SAFE
        assert burn.viable

    def test_kill_strategy(self):
        from core.costs.commission_burn import CommissionBurnAnalyzer, BurnLevel
        analyzer = CommissionBurnAnalyzer()
        # Tiny PnL, high frequency → high burn
        trades = [{"notional": 100, "pnl_gross": 0.01, "commission": 0.1} for _ in range(200)]
        analyzer.add_trades("bad_strat", "binance", trades)
        report = analyzer.analyze()
        burn = report.strategies["bad_strat"]
        assert burn.burn_level == BurnLevel.KILL

    def test_report_summary(self):
        from core.costs.commission_burn import CommissionBurnAnalyzer
        analyzer = CommissionBurnAnalyzer()
        analyzer.add_trades("s1", "alpaca", self._make_trades())
        analyzer.add_trades("s2", "ibkr_fx", self._make_trades())
        report = analyzer.analyze()
        report.compute_summary()
        assert report.summary["total_strategies"] == 2


# ═══════════════════════════════════════════════════════════════
# P2-01: Signal Quality Filter v2
# ═══════════════════════════════════════════════════════════════


class TestSignalQualityFilter:

    def test_strong_signal_trades(self):
        from core.signals.signal_quality_v2 import SignalQualityFilter, SignalVerdict, StratTier
        sqf = SignalQualityFilter()
        score = sqf.score_signal(
            strategy="fx_carry_vs", symbol="EURUSD", direction="BUY",
            signal_value=0.9, trigger_threshold=0.5,
            regime="TREND_STRONG", asset_class="fx",
            tier=StratTier.VALIDATED,
        )
        assert score.total > 0.4
        assert score.verdict == SignalVerdict.TRADE

    def test_weak_signal_skipped(self):
        from core.signals.signal_quality_v2 import SignalQualityFilter, SignalVerdict, StratTier
        sqf = SignalQualityFilter()
        score = sqf.score_signal(
            strategy="test", symbol="X", direction="BUY",
            signal_value=0.51, trigger_threshold=0.5,  # Barely above threshold
            regime="PANIC", asset_class="us_equity",
            tier=StratTier.BORDERLINE,
        )
        # Weak strength + bad regime + borderline tier
        assert score.verdict in (SignalVerdict.SKIP, SignalVerdict.REDUCE)

    def test_confluence_increases_score(self):
        from core.signals.signal_quality_v2 import SignalQualityFilter, StratTier
        sqf = SignalQualityFilter()
        # Without confluence
        s1 = sqf.score_signal(
            strategy="s1", symbol="SPY", direction="BUY",
            signal_value=0.7, trigger_threshold=0.5,
            regime="TREND_STRONG", tier=StratTier.VALIDATED,
        )
        # With confluence
        sqf.register_active_signal("SPY", "other_strat", "BUY")
        s2 = sqf.score_signal(
            strategy="s1", symbol="SPY", direction="BUY",
            signal_value=0.7, trigger_threshold=0.5,
            regime="TREND_STRONG", tier=StratTier.VALIDATED,
        )
        assert s2.confluence > s1.confluence

    def test_score_components_bounded(self):
        from core.signals.signal_quality_v2 import SignalQualityFilter, StratTier
        sqf = SignalQualityFilter()
        score = sqf.score_signal(
            strategy="test", symbol="X", direction="BUY",
            signal_value=10, trigger_threshold=0.5,
            regime="TREND_STRONG", asset_class="crypto",
            current_vol=15.0, historical_vol_range=(5.0, 30.0),
            tier=StratTier.VALIDATED,
        )
        assert 0 <= score.strength <= 0.30
        assert 0 <= score.regime_alignment <= 0.20
        assert 0 <= score.confluence <= 0.20
        assert 0 <= score.timing <= 0.15
        assert 0 <= score.vol_context <= 0.15
        assert 0 <= score.total <= 1.0


# ═══════════════════════════════════════════════════════════════
# P4-01: HRP Diagnostic
# ═══════════════════════════════════════════════════════════════


class TestHRPDiagnostic:

    def _make_pnl_matrix(self, n_days=100, n_strats=5):
        np.random.seed(42)
        data = np.random.randn(n_days, n_strats) * 0.01
        cols = [f"strat_{i}" for i in range(n_strats)]
        return pd.DataFrame(data, columns=cols)

    def test_basic_diagnostic(self):
        from core.alloc.hrp_diagnostic import HRPDiagnostic
        diag = HRPDiagnostic()
        pnl = self._make_pnl_matrix()
        result = diag.run(pnl)
        assert result.weights
        assert len(result.weights) == 5
        assert abs(sum(result.weights.values()) - 1.0) < 0.01

    def test_concentration_detection(self):
        from core.alloc.hrp_diagnostic import HRPDiagnostic
        diag = HRPDiagnostic(max_single_weight=0.10)
        # Create unbalanced data
        np.random.seed(42)
        data = np.random.randn(100, 3) * 0.01
        data[:, 0] *= 0.1  # Very low vol → high weight in inv-var
        pnl = pd.DataFrame(data, columns=["low_vol", "med", "high"])
        result = diag.run(pnl)
        assert result.concentration["max_single_weight"] > 0.10

    def test_cluster_detection(self):
        from core.alloc.hrp_diagnostic import HRPDiagnostic
        diag = HRPDiagnostic()
        np.random.seed(42)
        base = np.random.randn(100) * 0.01
        data = pd.DataFrame({
            "s1": base + np.random.randn(100) * 0.001,
            "s2": base + np.random.randn(100) * 0.001,  # Correlated with s1
            "s3": np.random.randn(100) * 0.01,  # Independent
        })
        result = diag.run(data)
        # s1 and s2 should be in the same cluster
        found = False
        for cluster in result.clusters:
            if "s1" in cluster and "s2" in cluster:
                found = True
        assert found


# ═══════════════════════════════════════════════════════════════
# P5-01: Data Quality Scoring
# ═══════════════════════════════════════════════════════════════


class TestDataQualityScorer:

    def _make_clean_data(self, n=1000):
        dates = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(n) * 0.1)
        return pd.DataFrame({
            "open": close + np.random.randn(n) * 0.05,
            "high": close + abs(np.random.randn(n) * 0.1),
            "low": close - abs(np.random.randn(n) * 0.1),
            "close": close,
            "volume": np.random.randint(1000, 10000, n),
        }, index=dates)

    def test_clean_data_scores_high(self):
        from core.data.data_quality_score import DataQualityScorer, QualityLevel
        scorer = DataQualityScorer()
        df = self._make_clean_data()
        quality = scorer.score(df, "TEST", "1h", "crypto")
        assert quality.score > 60  # Clean data should score well
        assert quality.level in (QualityLevel.EXCELLENT, QualityLevel.BON, QualityLevel.ACCEPTABLE)

    def test_empty_data_scores_zero(self):
        from core.data.data_quality_score import DataQualityScorer
        scorer = DataQualityScorer()
        df = pd.DataFrame()
        quality = scorer.score(df, "EMPTY", "1h")
        assert quality.score == 0

    def test_outlier_detection(self):
        from core.data.data_quality_score import DataQualityScorer
        scorer = DataQualityScorer(outlier_zscore=3.0)
        df = self._make_clean_data()
        # Inject outliers
        df.iloc[100, df.columns.get_loc("close")] = 200  # 100% jump
        df.iloc[200, df.columns.get_loc("close")] = 50   # 50% drop
        quality = scorer.score(df, "OUTLIER", "1h", "crypto")
        assert quality.n_outliers > 0


# ═══════════════════════════════════════════════════════════════
# P1-01: Research Pipeline
# ═══════════════════════════════════════════════════════════════


class TestResearchPipeline:

    def test_start_research(self):
        from core.research.research_pipeline import ResearchPipeline, PipelineStatus
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = ResearchPipeline(data_dir=Path(tmpdir))
            strat = pipeline.start_research("test_strat", thesis="Test thesis")
            assert strat.name == "test_strat"
            assert strat.status == PipelineStatus.IN_PROGRESS

    def test_quick_backtest_kill(self):
        from core.research.research_pipeline import ResearchPipeline, PipelineStatus
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = ResearchPipeline(data_dir=Path(tmpdir))
            pipeline.start_research("bad_strat")
            result = pipeline.record_quick_backtest("bad_strat", sharpe=0.3, n_trades=50)
            assert result.status.value == "FAILED"
            strat = pipeline.get_strategy("bad_strat")
            assert strat.status == PipelineStatus.REJECTED

    def test_quick_backtest_pass(self):
        from core.research.research_pipeline import ResearchPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = ResearchPipeline(data_dir=Path(tmpdir))
            pipeline.start_research("good_strat")
            result = pipeline.record_quick_backtest("good_strat", sharpe=1.5, n_trades=80)
            assert result.status.value == "PASSED"

    def test_full_pipeline(self):
        from core.research.research_pipeline import ResearchPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = ResearchPipeline(data_dir=Path(tmpdir))
            pipeline.start_research("full_test")
            pipeline.record_quick_backtest("full_test", sharpe=1.5, n_trades=80)
            pipeline.record_walk_forward("full_test", oos_is_ratio=0.7, pct_profitable=0.6, n_trades_oos=50, sharpe_oos=1.2)
            pipeline.record_cost_stress("full_test", break_even_slippage_x=5.0, commission_burn=0.1)
            pipeline.record_correlation_check("full_test", max_correlation=0.3)
            strat = pipeline.get_strategy("full_test")
            assert strat.current_gate >= 4

    def test_persistence(self):
        from core.research.research_pipeline import ResearchPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = ResearchPipeline(data_dir=Path(tmpdir))
            p1.start_research("persist_test")
            p1.save()

            p2 = ResearchPipeline(data_dir=Path(tmpdir))
            assert p2.get_strategy("persist_test") is not None
