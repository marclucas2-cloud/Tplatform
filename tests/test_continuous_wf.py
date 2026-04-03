"""
Tests for the Continuous Walk-Forward module.

Covers:
- WalkForwardResult properties and verdict logic
- ContinuousWalkForward: WF calculation on synthetic data
- Comparison with previous results and degradation detection
- SQLite persistence (save / get_history / get_trend)
- Alert generation for LIVE and PAPER strategies
- Edge cases: empty returns, single window, all zeros
- Report generation (markdown format)
"""

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.weekly_walk_forward import (
    ContinuousWalkForward,
    WalkForwardResult,
)

# ---- Helpers ----------------------------------------------------------------


def _trending_returns(n: int = 200, mean: float = 0.002, std: float = 0.005, seed: int = 42):
    """Consistently positive returns -- should validate."""
    rng = np.random.RandomState(seed)
    return rng.normal(mean, std, n)


def _random_returns(n: int = 200, seed: int = 99):
    """Zero-mean random returns -- should be borderline/rejected."""
    rng = np.random.RandomState(seed)
    return rng.normal(0.0, 0.01, n)


def _negative_returns(n: int = 200, seed: int = 77):
    """Consistently negative returns -- should be rejected."""
    rng = np.random.RandomState(seed)
    return rng.normal(-0.005, 0.005, n)


def _make_cwf(tmp_path):
    """Create a ContinuousWalkForward with temp db and output."""
    db_path = str(tmp_path / "test_wf.db")
    out_dir = str(tmp_path / "output")
    return ContinuousWalkForward(db_path=db_path, output_dir=out_dir)


# ---- WalkForwardResult tests ------------------------------------------------


class TestWalkForwardResult:
    """Tests for WalkForwardResult verdict logic."""

    def test_validated_when_above_thresholds(self):
        """Result is VALIDATED when ratio >= 0.5 and profitable >= 50%."""
        r = WalkForwardResult(
            strategy="test",
            timestamp="2026-03-27T00:00:00",
            is_sharpe=2.0,
            oos_sharpe=1.5,
            oos_is_ratio=0.75,
            profitable_windows_pct=0.8,
            n_windows=5,
            n_trades_oos=100,
        )
        assert r.is_validated is True
        assert r.verdict == "VALIDATED"

    def test_borderline_when_close_to_thresholds(self):
        """Result is BORDERLINE when one criterion is close but not met."""
        r = WalkForwardResult(
            strategy="test",
            timestamp="2026-03-27T00:00:00",
            is_sharpe=2.0,
            oos_sharpe=0.5,
            oos_is_ratio=0.35,  # below 0.5 but above 0.3
            profitable_windows_pct=0.45,  # below 0.5 but above 0.4
            n_windows=5,
            n_trades_oos=80,
        )
        assert r.is_validated is False
        assert r.verdict == "BORDERLINE"

    def test_rejected_when_oos_negative(self):
        """Result is REJECTED when OOS Sharpe is negative and ratio is low."""
        r = WalkForwardResult(
            strategy="test",
            timestamp="2026-03-27T00:00:00",
            is_sharpe=2.0,
            oos_sharpe=-1.0,
            oos_is_ratio=-0.5,
            profitable_windows_pct=0.2,
            n_windows=5,
            n_trades_oos=50,
        )
        assert r.is_validated is False
        assert r.verdict == "REJECTED"

    def test_verdict_string_values(self):
        """Verdict must be one of VALIDATED, BORDERLINE, REJECTED."""
        for ratio, pct, expected in [
            (0.6, 0.6, "VALIDATED"),
            (0.35, 0.35, "BORDERLINE"),
            (0.1, 0.1, "REJECTED"),
        ]:
            r = WalkForwardResult(
                strategy="v",
                timestamp="t",
                is_sharpe=1.0,
                oos_sharpe=0.5,
                oos_is_ratio=ratio,
                profitable_windows_pct=pct,
                n_windows=5,
                n_trades_oos=50,
            )
            assert r.verdict == expected, f"ratio={ratio}, pct={pct} -> {r.verdict} != {expected}"

    def test_to_dict_serializable(self):
        """to_dict() output must be JSON-serializable."""
        r = WalkForwardResult(
            strategy="serialize_test",
            timestamp="2026-03-27T00:00:00",
            is_sharpe=1.5,
            oos_sharpe=1.0,
            oos_is_ratio=0.67,
            profitable_windows_pct=0.6,
            n_windows=5,
            n_trades_oos=100,
            window_details=[{"window": 1, "oos_sharpe": 1.0}],
        )
        d = r.to_dict()
        json_str = json.dumps(d, default=str)
        assert isinstance(json_str, str)
        assert "serialize_test" in json_str
        assert d["verdict"] == "VALIDATED"


# ---- ContinuousWalkForward tests --------------------------------------------


class TestContinuousWalkForward:
    """Tests for ContinuousWalkForward engine."""

    def test_wf_trending_returns_validated(self, tmp_path):
        """Trending (positive mean) returns should produce a VALIDATED result."""
        cwf = _make_cwf(tmp_path)
        returns = _trending_returns(200, mean=0.003, std=0.004, seed=42)
        result = cwf.run_walk_forward("trending_strat", returns)

        assert result.verdict == "VALIDATED", (
            f"Expected VALIDATED, got {result.verdict} "
            f"(ratio={result.oos_is_ratio:.2f}, "
            f"profitable={result.profitable_windows_pct:.0%})"
        )
        assert result.oos_sharpe > 0
        assert result.n_windows >= 1

    def test_wf_random_returns_not_validated(self, tmp_path):
        """Random zero-mean returns should NOT be VALIDATED."""
        cwf = _make_cwf(tmp_path)
        returns = _random_returns(200, seed=99)
        result = cwf.run_walk_forward("random_strat", returns)

        assert result.verdict in ("BORDERLINE", "REJECTED"), (
            f"Random returns should not validate, got {result.verdict}"
        )

    def test_wf_negative_returns_rejected(self, tmp_path):
        """Consistently negative returns should be REJECTED."""
        cwf = _make_cwf(tmp_path)
        returns = _negative_returns(200, seed=77)
        result = cwf.run_walk_forward("negative_strat", returns)

        assert result.verdict == "REJECTED", (
            f"Negative returns should be REJECTED, got {result.verdict}"
        )
        assert result.oos_sharpe < 0

    def test_wf_insufficient_data(self, tmp_path):
        """Very short returns array (<10) should yield 0 windows."""
        cwf = _make_cwf(tmp_path)
        returns = np.array([0.01, 0.02, -0.01])
        result = cwf.run_walk_forward("tiny_strat", returns)

        assert result.n_windows == 0
        assert result.verdict == "REJECTED"

    def test_wf_empty_returns(self, tmp_path):
        """Empty returns array should not crash."""
        cwf = _make_cwf(tmp_path)
        result = cwf.run_walk_forward("empty_strat", np.array([]))

        assert result.n_windows == 0
        assert result.verdict == "REJECTED"

    def test_wf_all_zeros(self, tmp_path):
        """All-zero returns should not crash; expect REJECTED or BORDERLINE."""
        cwf = _make_cwf(tmp_path)
        returns = np.zeros(100)
        result = cwf.run_walk_forward("zeros_strat", returns)

        # All zero returns: no profit, no loss
        assert result.verdict in ("REJECTED", "BORDERLINE")

    def test_wf_single_fold(self, tmp_path):
        """With n_folds=1, should still produce a result."""
        cwf = _make_cwf(tmp_path)
        returns = _trending_returns(100, seed=42)
        result = cwf.run_walk_forward("single_fold", returns, n_folds=1)

        assert result.n_windows >= 1
        assert result.strategy == "single_fold"

    def test_compare_no_previous(self, tmp_path):
        """First run: compare_with_previous returns no degradation."""
        cwf = _make_cwf(tmp_path)
        result = WalkForwardResult(
            strategy="new_strat",
            timestamp=datetime.now(UTC).isoformat(),
            is_sharpe=2.0,
            oos_sharpe=1.5,
            oos_is_ratio=0.75,
            profitable_windows_pct=0.8,
            n_windows=5,
            n_trades_oos=100,
        )
        comparison = cwf.compare_with_previous(result)

        assert comparison["degraded"] is False
        assert comparison["previous_oos_sharpe"] is None
        assert comparison["alert_level"] == "none"

    def test_compare_no_degradation(self, tmp_path):
        """When current OOS >= previous, no degradation."""
        cwf = _make_cwf(tmp_path)

        # Insert a previous result
        with sqlite3.connect(str(cwf._db_path)) as conn:
            conn.execute(
                """INSERT INTO wf_results
                   (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                    profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("stable_strat", "2026-03-20T00:00:00", 2.0, 1.5, 0.75, 0.8, 5, 100, "VALIDATED", "[]"),
            )

        result = WalkForwardResult(
            strategy="stable_strat",
            timestamp="2026-03-27T00:00:00",
            is_sharpe=2.0,
            oos_sharpe=1.6,  # improved
            oos_is_ratio=0.80,
            profitable_windows_pct=0.8,
            n_windows=5,
            n_trades_oos=100,
        )
        comparison = cwf.compare_with_previous(result)

        assert comparison["degraded"] is False
        assert comparison["alert_level"] == "none"
        assert comparison["previous_oos_sharpe"] == 1.5

    def test_compare_warning_degradation(self, tmp_path):
        """30%+ drop in OOS Sharpe triggers warning."""
        cwf = _make_cwf(tmp_path)

        with sqlite3.connect(str(cwf._db_path)) as conn:
            conn.execute(
                """INSERT INTO wf_results
                   (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                    profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("warn_strat", "2026-03-20T00:00:00", 2.0, 2.0, 0.75, 0.8, 5, 100, "VALIDATED", "[]"),
            )

        result = WalkForwardResult(
            strategy="warn_strat",
            timestamp="2026-03-27T00:00:00",
            is_sharpe=2.0,
            oos_sharpe=1.2,  # 40% drop from 2.0
            oos_is_ratio=0.60,
            profitable_windows_pct=0.6,
            n_windows=5,
            n_trades_oos=100,
        )
        comparison = cwf.compare_with_previous(result)

        assert comparison["degraded"] is True
        assert comparison["alert_level"] in ("warning", "critical")

    def test_compare_critical_degradation(self, tmp_path):
        """50%+ drop in OOS Sharpe triggers critical."""
        cwf = _make_cwf(tmp_path)

        with sqlite3.connect(str(cwf._db_path)) as conn:
            conn.execute(
                """INSERT INTO wf_results
                   (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                    profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("crit_strat", "2026-03-20T00:00:00", 2.0, 2.0, 0.75, 0.8, 5, 100, "VALIDATED", "[]"),
            )

        result = WalkForwardResult(
            strategy="crit_strat",
            timestamp="2026-03-27T00:00:00",
            is_sharpe=2.0,
            oos_sharpe=0.8,  # 60% drop from 2.0
            oos_is_ratio=0.40,
            profitable_windows_pct=0.5,
            n_windows=5,
            n_trades_oos=100,
        )
        comparison = cwf.compare_with_previous(result)

        assert comparison["degraded"] is True
        assert comparison["alert_level"] == "critical"

    def test_compare_live_strategy_critical(self, tmp_path):
        """LIVE strategy with ratio < 0.3 is always CRITICAL."""
        cwf = _make_cwf(tmp_path)

        with sqlite3.connect(str(cwf._db_path)) as conn:
            conn.execute(
                """INSERT INTO wf_results
                   (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                    profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("fx_eurusd_trend", "2026-03-20T00:00:00", 2.0, 2.0, 0.75, 0.8, 5, 100, "VALIDATED", "[]"),
            )

        result = WalkForwardResult(
            strategy="fx_eurusd_trend",  # LIVE strategy
            timestamp="2026-03-27T00:00:00",
            is_sharpe=2.0,
            oos_sharpe=0.5,
            oos_is_ratio=0.25,  # below 0.3
            profitable_windows_pct=0.4,
            n_windows=5,
            n_trades_oos=100,
        )
        comparison = cwf.compare_with_previous(result)

        assert comparison["alert_level"] == "critical"
        assert comparison["degraded"] is True

    def test_save_and_get_history(self, tmp_path):
        """Save results to DB and retrieve them."""
        cwf = _make_cwf(tmp_path)
        results = [
            WalkForwardResult(
                strategy="hist_strat",
                timestamp="2026-03-27T06:00:00",
                is_sharpe=2.0,
                oos_sharpe=1.5,
                oos_is_ratio=0.75,
                profitable_windows_pct=0.8,
                n_windows=5,
                n_trades_oos=100,
            )
        ]
        cwf.save_results(results)

        history = cwf.get_history(strategy="hist_strat", weeks=4)
        assert len(history) == 1
        assert history[0]["strategy"] == "hist_strat"
        assert history[0]["verdict"] == "VALIDATED"

    def test_get_trend_stable(self, tmp_path):
        """Stable OOS Sharpe over weeks -> trend = 'stable'."""
        cwf = _make_cwf(tmp_path)

        # Insert 4 weeks of stable results
        with sqlite3.connect(str(cwf._db_path)) as conn:
            for i in range(4):
                conn.execute(
                    """INSERT INTO wf_results
                       (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                        profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "stable_trend",
                        f"2026-03-{6 + i * 7:02d}T06:00:00",
                        2.0,
                        1.5 + (i % 2) * 0.01,  # very small fluctuation
                        0.75,
                        0.8,
                        5,
                        100,
                        "VALIDATED",
                        "[]",
                    ),
                )

        trend = cwf.get_trend("stable_trend", weeks=8)
        assert trend["trend"] == "stable"
        assert trend["weeks_analyzed"] == 4

    def test_get_trend_degrading(self, tmp_path):
        """Decreasing OOS Sharpe over weeks -> trend = 'degrading'."""
        cwf = _make_cwf(tmp_path)

        with sqlite3.connect(str(cwf._db_path)) as conn:
            for i in range(5):
                conn.execute(
                    """INSERT INTO wf_results
                       (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                        profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "degrading_trend",
                        f"2026-{2 + i // 4:02d}-{1 + (i % 4) * 7:02d}T06:00:00",
                        2.0,
                        2.0 - i * 0.5,  # 2.0, 1.5, 1.0, 0.5, 0.0
                        0.75,
                        0.8,
                        5,
                        100,
                        "VALIDATED",
                        "[]",
                    ),
                )

        trend = cwf.get_trend("degrading_trend", weeks=8)
        assert trend["trend"] == "degrading"
        assert trend["sharpe_slope"] < -0.05

    def test_get_trend_improving(self, tmp_path):
        """Increasing OOS Sharpe over weeks -> trend = 'improving'."""
        cwf = _make_cwf(tmp_path)

        with sqlite3.connect(str(cwf._db_path)) as conn:
            for i in range(5):
                conn.execute(
                    """INSERT INTO wf_results
                       (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                        profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "improving_trend",
                        f"2026-{2 + i // 4:02d}-{1 + (i % 4) * 7:02d}T06:00:00",
                        2.0,
                        0.5 + i * 0.5,  # 0.5, 1.0, 1.5, 2.0, 2.5
                        0.75,
                        0.8,
                        5,
                        100,
                        "VALIDATED",
                        "[]",
                    ),
                )

        trend = cwf.get_trend("improving_trend", weeks=8)
        assert trend["trend"] == "improving"
        assert trend["sharpe_slope"] > 0.05

    def test_generate_report_markdown(self, tmp_path):
        """Report should be valid markdown with key sections."""
        cwf = _make_cwf(tmp_path)
        results = [
            WalkForwardResult(
                strategy="report_strat_a",
                timestamp="2026-03-27T06:00:00",
                is_sharpe=2.0,
                oos_sharpe=1.5,
                oos_is_ratio=0.75,
                profitable_windows_pct=0.8,
                n_windows=5,
                n_trades_oos=100,
            ),
            WalkForwardResult(
                strategy="report_strat_b",
                timestamp="2026-03-27T06:00:00",
                is_sharpe=2.0,
                oos_sharpe=-0.5,
                oos_is_ratio=-0.25,
                profitable_windows_pct=0.2,
                n_windows=5,
                n_trades_oos=80,
            ),
        ]
        report = cwf.generate_report(results)

        assert "# Weekly Walk-Forward Report" in report
        assert "report_strat_a" in report
        assert "report_strat_b" in report
        assert "VALIDATED" in report
        assert "REJECTED" in report
        assert "Details" in report

    def test_check_alerts_with_multiple_strategies(self, tmp_path):
        """Alert check should flag degraded strategies."""
        cwf = _make_cwf(tmp_path)

        # Insert previous results for two strategies
        with sqlite3.connect(str(cwf._db_path)) as conn:
            conn.execute(
                """INSERT INTO wf_results
                   (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                    profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("day_of_week_seasonal", "2026-03-20T00:00:00", 2.0, 2.0, 0.75, 0.8, 5, 100, "VALIDATED", "[]"),
            )
            conn.execute(
                """INSERT INTO wf_results
                   (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                    profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("correlation_regime_hedge", "2026-03-20T00:00:00", 2.0, 1.8, 0.70, 0.7, 5, 100, "VALIDATED", "[]"),
            )

        current_results = [
            WalkForwardResult(
                strategy="day_of_week_seasonal",
                timestamp="2026-03-27T00:00:00",
                is_sharpe=2.0,
                oos_sharpe=0.5,  # huge drop from 2.0
                oos_is_ratio=0.25,
                profitable_windows_pct=0.4,
                n_windows=5,
                n_trades_oos=100,
            ),
            WalkForwardResult(
                strategy="correlation_regime_hedge",
                timestamp="2026-03-27T00:00:00",
                is_sharpe=2.0,
                oos_sharpe=1.7,  # small drop, within tolerance
                oos_is_ratio=0.85,
                profitable_windows_pct=0.7,
                n_windows=5,
                n_trades_oos=100,
            ),
        ]
        alerts = cwf.check_alerts(current_results)

        # day_of_week_seasonal should trigger an alert (75% drop)
        alert_strats = [a["strategy"] for a in alerts]
        assert "day_of_week_seasonal" in alert_strats

        # correlation_regime_hedge should NOT trigger (small drop ~5%)
        assert "correlation_regime_hedge" not in alert_strats

    def test_alert_callback_invoked(self, tmp_path):
        """Alert callback is called when degradation detected."""
        called_with = []

        def mock_alert(message, level="info"):
            called_with.append({"message": message, "level": level})

        db_path = str(tmp_path / "alert_test.db")
        cwf = ContinuousWalkForward(
            db_path=db_path,
            output_dir=str(tmp_path / "out"),
            alert_callback=mock_alert,
        )

        # Insert previous data
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO wf_results
                   (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                    profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("fx_eurusd_trend", "2026-03-20T00:00:00", 2.0, 2.0, 0.75, 0.8, 5, 100, "VALIDATED", "[]"),
            )

        results = [
            WalkForwardResult(
                strategy="fx_eurusd_trend",
                timestamp="2026-03-27T00:00:00",
                is_sharpe=2.0,
                oos_sharpe=0.3,  # massive drop
                oos_is_ratio=0.15,
                profitable_windows_pct=0.2,
                n_windows=5,
                n_trades_oos=100,
            ),
        ]
        alerts = cwf.check_alerts(results)

        assert len(alerts) >= 1
        assert len(called_with) >= 1
        assert called_with[0]["level"] == "critical"

    def test_run_all_skips_missing_data(self, tmp_path):
        """run_all should skip strategies without returns data."""
        cwf = _make_cwf(tmp_path)
        strategies = {
            "has_data": {"mode": "PAPER", "asset_class": "US_EQUITY", "min_trades": 30},
            "no_data": {"mode": "PAPER", "asset_class": "US_EQUITY", "min_trades": 30},
        }
        returns_data = {
            "has_data": _trending_returns(200),
            # "no_data" intentionally missing
        }
        results = cwf.run_all(strategies=strategies, returns_data=returns_data)

        assert len(results) == 1
        assert results[0].strategy == "has_data"

    def test_db_init_creates_table(self, tmp_path):
        """Database initialization should create wf_results table and index."""
        db_path = str(tmp_path / "init_test.db")
        _cwf = ContinuousWalkForward(db_path=db_path, output_dir=str(tmp_path / "out"))

        with sqlite3.connect(db_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='wf_results'"
            ).fetchall()
            assert len(tables) == 1

            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_wf_strategy'"
            ).fetchall()
            assert len(indexes) == 1


# ---- Run --------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
