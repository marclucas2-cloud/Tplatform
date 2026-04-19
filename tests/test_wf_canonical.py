"""WF canonical pipeline regression tests (Phase 9 XXL)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.research.wf_canonical import (
    PASS_RATE_FOR_VALIDATED,
    WF_SCHEMA_VERSION,
    WindowResult,
    WFRunResult,
    run_walk_forward,
)


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

class TestVerdictRule:
    def test_validated_when_pass_rate_50pct_and_positive_sharpe(self):
        """3 PASS / 5 windows + median Sharpe > 0 = VALIDATED."""
        def fake_backtest(train_s, train_e, test_e):
            return {"sharpe": 1.5, "max_dd_pct": -5.0, "total_pnl_usd": 1000, "n_trades": 20}

        # Force 3/5 pass by alternating
        call_count = [0]
        def alternating(*args, **kwargs):
            call_count[0] += 1
            return {"sharpe": 1.0 if call_count[0] <= 3 else -1.0,
                    "max_dd_pct": -5.0, "total_pnl_usd": 100, "n_trades": 20}

        r = run_walk_forward(
            strategy_id="test", data_length=1000,
            backtest_window_fn=alternating,
        )
        assert r.windows_pass == 3
        assert r.windows_total == 5
        assert r.verdict == "VALIDATED"

    def test_rejected_when_pass_rate_below_50pct(self):
        call_count = [0]
        def alternating(*args, **kwargs):
            call_count[0] += 1
            # 1 PASS, 4 FAIL
            return {"sharpe": 1.0 if call_count[0] == 1 else -1.0,
                    "max_dd_pct": -5.0, "total_pnl_usd": 100, "n_trades": 20}

        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=alternating)
        assert r.windows_pass == 1
        assert r.verdict == "REJECTED"

    def test_insufficient_trades_when_few_trades_per_window(self):
        def low_trades(*args, **kwargs):
            return {"sharpe": 0.0, "max_dd_pct": 0.0, "total_pnl_usd": 0, "n_trades": 2}

        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=low_trades)
        assert all(w.verdict == "INSUFFICIENT_TRADES" for w in r.windows)
        assert r.verdict == "INSUFFICIENT_TRADES"

    def test_validated_requires_3_non_insufficient(self):
        """If only 2 windows have enough trades, verdict is INSUFFICIENT (not VALIDATED)."""
        call_count = [0]
        def mixed(*args, **kwargs):
            call_count[0] += 1
            # First 2 have trades + PASS, rest have no trades
            if call_count[0] <= 2:
                return {"sharpe": 2.0, "max_dd_pct": -3.0, "total_pnl_usd": 500, "n_trades": 20}
            return {"sharpe": 0.0, "max_dd_pct": 0.0, "total_pnl_usd": 0, "n_trades": 1}

        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=mixed)
        # 2 PASS (excluded by rule "need 3 non-insufficient")
        assert r.verdict == "INSUFFICIENT_TRADES"


# ---------------------------------------------------------------------------
# Manifest / output schema
# ---------------------------------------------------------------------------

class TestOutputSchema:
    def test_manifest_has_schema_version(self, tmp_path):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=fake)
        path = r.write_manifest(tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == WF_SCHEMA_VERSION

    def test_manifest_contains_env_capture(self, tmp_path):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=fake)
        path = r.write_manifest(tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "env_capture" in data
        assert "git_sha" in data["env_capture"]
        assert "python" in data["env_capture"]
        assert "platform" in data["env_capture"]

    def test_manifest_records_seed_and_params(self, tmp_path):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        r = run_walk_forward(
            strategy_id="t", data_length=1000, backtest_window_fn=fake,
            seed=12345, extra_params={"custom_param": "value"},
        )
        path = r.write_manifest(tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["params"]["seed"] == 12345
        assert data["params"]["custom_param"] == "value"

    def test_manifest_summary_complete(self, tmp_path):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -5.0, "total_pnl_usd": 100, "n_trades": 15}
        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=fake)
        data = r.to_dict()
        s = data["summary"]
        assert "windows_pass" in s and "windows_total" in s
        assert "median_sharpe" in s and "median_dd" in s
        assert "verdict" in s

    def test_run_id_unique_per_call(self, tmp_path):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        r1 = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=fake)
        r2 = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=fake)
        assert r1.run_id != r2.run_id


# ---------------------------------------------------------------------------
# Window slicing math
# ---------------------------------------------------------------------------

class TestWindowSlicing:
    def test_n_windows_respected(self):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=fake, n_windows=7)
        assert len(r.windows) == 7

    def test_train_test_pct_respected(self):
        captured = []
        def capture(train_s, train_e, test_e):
            captured.append((train_s, train_e, test_e))
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        run_walk_forward(
            strategy_id="t", data_length=1000, backtest_window_fn=capture,
            n_windows=4, train_pct=0.8, test_pct=0.2,
        )
        # First window: train_start=0, train_end ~= 200, test_end ~= 250
        train_s0, train_e0, test_e0 = captured[0]
        assert train_s0 == 0
        train_size_0 = train_e0 - train_s0
        # Should be ~80% of 250 (window_size)
        assert 180 <= train_size_0 <= 220

    def test_data_too_short_raises(self):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        with pytest.raises(ValueError, match="too short"):
            run_walk_forward(strategy_id="t", data_length=50, backtest_window_fn=fake, n_windows=5)

    def test_invalid_pct_raises(self):
        def fake(*args):
            return {"sharpe": 1.0, "max_dd_pct": -2.0, "total_pnl_usd": 100, "n_trades": 10}
        with pytest.raises(ValueError, match="must be in"):
            run_walk_forward(
                strategy_id="t", data_length=1000, backtest_window_fn=fake,
                train_pct=1.5,
            )


# ---------------------------------------------------------------------------
# Median computations
# ---------------------------------------------------------------------------

class TestMedianStats:
    def test_median_sharpe_excludes_insufficient_trades_windows(self):
        call_count = [0]
        def varying(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"sharpe": 0.5, "max_dd_pct": -1, "total_pnl_usd": 100, "n_trades": 10}
            if call_count[0] == 2:
                return {"sharpe": 1.0, "max_dd_pct": -2, "total_pnl_usd": 200, "n_trades": 10}
            if call_count[0] == 3:
                return {"sharpe": 1.5, "max_dd_pct": -3, "total_pnl_usd": 300, "n_trades": 10}
            # Last 2 have insufficient trades
            return {"sharpe": -99.0, "max_dd_pct": 0, "total_pnl_usd": 0, "n_trades": 1}

        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=varying)
        # Median of [0.5, 1.0, 1.5] = 1.0 (insufficient excluded)
        assert r.median_sharpe == 1.0


class TestDeflatedSharpeAndGrade:
    """v2 schema: DSR p-value + S/A/B grade."""

    def test_dsr_pvalue_none_when_n_bars_oos_absent(self):
        from core.research.wf_canonical import run_walk_forward
        def bt(*args):
            return {"sharpe": 1.2, "max_dd_pct": -3, "total_pnl_usd": 1000, "n_trades": 20}
        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=bt)
        # No n_bars_oos in params or metrics → DSR skipped
        assert r.dsr_pvalue is None
        assert r.to_dict()["summary"]["dsr_pvalue"] is None

    def test_dsr_pvalue_computed_when_params_has_n_bars_oos(self):
        from core.research.wf_canonical import run_walk_forward
        def bt(*args):
            return {"sharpe": 1.5, "max_dd_pct": -3, "total_pnl_usd": 1000, "n_trades": 30}
        r = run_walk_forward(
            strategy_id="t",
            data_length=1000,
            backtest_window_fn=bt,
            extra_params={"n_bars_oos": 1260, "n_trials": 1},
        )
        # Strong Sharpe + long sample + no multi-testing → low p-value
        assert r.dsr_pvalue is not None
        assert r.dsr_pvalue < 0.01

    def test_dsr_high_when_many_trials(self):
        from core.research.wf_canonical import compute_deflated_sharpe_pvalue
        # If we searched 100 strategies, best Sharpe 0.87 is not strong evidence
        p = compute_deflated_sharpe_pvalue(sharpe=0.87, n_observations=1260, n_trials=100)
        assert p > 0.5

    def test_grade_S_for_strong_stats(self):
        from core.research.wf_canonical import classify_grade
        assert classify_grade(pass_rate=1.0, median_sharpe=2.0, dsr_pvalue=None) == "S"
        assert classify_grade(pass_rate=0.8, median_sharpe=1.0, dsr_pvalue=0.01) == "S"

    def test_grade_A_for_moderate_stats(self):
        from core.research.wf_canonical import classify_grade
        assert classify_grade(pass_rate=0.6, median_sharpe=0.5, dsr_pvalue=None) == "A"
        assert classify_grade(pass_rate=0.6, median_sharpe=0.5, dsr_pvalue=0.08) == "A"

    def test_grade_B_for_legacy_validated(self):
        from core.research.wf_canonical import classify_grade
        assert classify_grade(pass_rate=0.5, median_sharpe=0.3, dsr_pvalue=None) == "B"

    def test_grade_rejected_for_weak(self):
        from core.research.wf_canonical import classify_grade
        assert classify_grade(pass_rate=0.4, median_sharpe=0.1, dsr_pvalue=None) == "REJECTED"

    def test_grade_downgrades_when_dsr_fails(self):
        from core.research.wf_canonical import classify_grade
        # Would be S by pass_rate + Sharpe alone, but DSR p>0.05 downgrades
        g = classify_grade(pass_rate=0.8, median_sharpe=1.0, dsr_pvalue=0.20)
        assert g == "B"  # still legacy B, not S

    def test_verdict_maps_grade_to_validated(self):
        from core.research.wf_canonical import run_walk_forward
        def strong(*args):
            return {"sharpe": 1.5, "max_dd_pct": -3, "total_pnl_usd": 500, "n_trades": 20}
        r = run_walk_forward(strategy_id="t", data_length=1000, backtest_window_fn=strong)
        assert r.grade in ("S", "A", "B")
        assert r.verdict == "VALIDATED"

    def test_schema_version_bumped_to_2(self):
        from core.research.wf_canonical import WF_SCHEMA_VERSION
        assert WF_SCHEMA_VERSION == 2
