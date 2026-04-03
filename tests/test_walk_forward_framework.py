"""
Tests pour le framework walk-forward.

Couvre les cas critiques :
- Rejet quand ratio OOS/IS < 0.5
- Validation quand ratio OOS/IS > 0.5
- Gestion des stratégies avec peu de trades
- Gestion des DataFrames vides
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.walk_forward_framework import ValidationResult, WalkForwardValidator

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_trades(
    n_trades: int,
    start_date: date = date(2025, 9, 1),
    mean_pnl: float = 10.0,
    std_pnl: float = 50.0,
    seed: int = 42,
    spread_days: int = 1,
) -> pd.DataFrame:
    """Generate synthetic trades DataFrame."""
    rng = np.random.RandomState(seed)
    dates = [start_date + timedelta(days=i * spread_days) for i in range(n_trades)]
    pnls = rng.normal(mean_pnl, std_pnl, n_trades)
    commissions = np.full(n_trades, 0.50)
    return pd.DataFrame({
        "date": dates,
        "pnl": pnls,
        "commission": commissions,
        "net_pnl": pnls - commissions,
        "direction": rng.choice(["LONG", "SHORT"], n_trades),
        "ticker": "TEST",
    })


def _make_strongly_profitable_trades(
    n_trades: int = 100,
    start_date: date = date(2025, 9, 1),
    seed: int = 42,
) -> pd.DataFrame:
    """Trades with strong positive PnL throughout — should validate."""
    rng = np.random.RandomState(seed)
    dates = [start_date + timedelta(days=i) for i in range(n_trades)]
    # Consistently positive with low variance
    pnls = rng.normal(30.0, 10.0, n_trades)
    pnls = np.abs(pnls)  # Force all positive for strong IS + OOS
    commissions = np.full(n_trades, 0.50)
    return pd.DataFrame({
        "date": dates,
        "pnl": pnls,
        "commission": commissions,
        "net_pnl": pnls - commissions,
        "direction": "LONG",
        "ticker": "TEST",
    })


def _make_overfitted_trades(
    n_trades: int = 100,
    start_date: date = date(2025, 9, 1),
    seed: int = 42,
) -> pd.DataFrame:
    """IS looks great but OOS is terrible — classic overfitting pattern."""
    rng = np.random.RandomState(seed)
    dates = [start_date + timedelta(days=i) for i in range(n_trades)]

    # First 70% (IS): strongly profitable
    is_size = int(n_trades * 0.7)
    is_pnls = rng.normal(50.0, 15.0, is_size)
    is_pnls = np.abs(is_pnls)

    # Last 30% (OOS): strongly negative
    oos_size = n_trades - is_size
    oos_pnls = rng.normal(-40.0, 20.0, oos_size)

    pnls = np.concatenate([is_pnls, oos_pnls])
    commissions = np.full(n_trades, 0.50)
    return pd.DataFrame({
        "date": dates,
        "pnl": pnls,
        "commission": commissions,
        "net_pnl": pnls - commissions,
        "direction": "LONG",
        "ticker": "TEST",
    })


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestWalkForwardValidator:
    """Tests for WalkForwardValidator."""

    def setup_method(self):
        self.validator = WalkForwardValidator(
            is_ratio=0.70,
            n_windows=3,
            min_trades_per_window=3,
            min_total_trades=15,
        )

    def test_validator_rejects_low_ratio(self):
        """Overfitted strategy: great IS but terrible OOS -> REJECTED."""
        trades = _make_overfitted_trades(n_trades=100, seed=42)
        result = self.validator.validate_strategy("Overfitted Strategy", trades)

        assert isinstance(result, ValidationResult)
        assert result.verdict in ("REJECTED", "BORDERLINE"), (
            f"Expected REJECTED or BORDERLINE for overfitted strategy, got {result.verdict}: "
            f"ratio={result.avg_ratio:.2f}, pct_profitable={result.pct_oos_profitable:.0%}"
        )
        # The OOS Sharpe should be much worse than IS
        assert result.avg_oos_sharpe < result.avg_is_sharpe, (
            "OOS Sharpe should be lower than IS for overfitted strategy"
        )

    def test_validator_accepts_high_ratio(self):
        """Consistently profitable strategy -> VALIDATED."""
        trades = _make_strongly_profitable_trades(n_trades=100, seed=42)
        result = self.validator.validate_strategy("Strong Strategy", trades)

        assert isinstance(result, ValidationResult)
        assert result.verdict == "VALIDATED", (
            f"Expected VALIDATED for strong strategy, got {result.verdict}: {result.reason}"
        )
        assert result.avg_oos_sharpe > 0, "OOS Sharpe should be positive"
        assert result.pct_oos_profitable >= 0.50, "Most OOS windows should be profitable"
        assert result.avg_ratio >= 0.50, "OOS/IS ratio should be >= 0.50"

    def test_validator_handles_few_trades(self):
        """Strategy with < min_total_trades -> REJECTED with reason."""
        trades = _make_trades(n_trades=10, mean_pnl=20.0)  # < 15 minimum
        result = self.validator.validate_strategy("Few Trades Strategy", trades)

        assert result.verdict == "REJECTED"
        assert "Insufficient trades" in result.reason
        assert result.n_trades == 10
        assert result.n_windows == 0

    def test_validator_handles_empty_trades(self):
        """Empty DataFrame -> REJECTED."""
        empty_df = pd.DataFrame(columns=["date", "pnl", "commission", "net_pnl", "direction"])
        result = self.validator.validate_strategy("Empty Strategy", empty_df)

        assert result.verdict == "REJECTED"
        assert result.n_trades == 0

    def test_validator_handles_missing_columns(self):
        """DataFrame without required columns -> REJECTED with error."""
        bad_df = pd.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]})
        result = self.validator.validate_strategy("Bad Columns", bad_df)

        assert result.verdict == "REJECTED"
        assert "error" in result.reason.lower() or "Data error" in result.reason

    def test_validate_all_multiple_strategies(self):
        """Validate multiple strategies at once."""
        strategies = {
            "Good Strategy": _make_strongly_profitable_trades(100, seed=42),
            "Bad Strategy": _make_overfitted_trades(100, seed=42),
            "Too Few": _make_trades(10),
        }
        results = self.validator.validate_all(strategies)

        assert len(results) == 3
        assert "Good Strategy" in results
        assert "Bad Strategy" in results
        assert "Too Few" in results
        assert results["Good Strategy"].verdict == "VALIDATED"
        assert results["Too Few"].verdict == "REJECTED"

    def test_window_results_populated(self):
        """Each window should have IS/OOS Sharpe, PnL, trades count."""
        trades = _make_strongly_profitable_trades(100, seed=42)
        result = self.validator.validate_strategy("Window Check", trades)

        assert result.n_windows >= 1
        assert len(result.windows) >= 1

        for w in result.windows:
            assert w.is_trades > 0
            assert w.oos_trades >= 0
            assert isinstance(w.is_sharpe, float)
            assert isinstance(w.oos_sharpe, float)
            assert isinstance(w.ratio, float)

    def test_to_dict_serializable(self):
        """Result should be JSON-serializable via to_dict()."""
        import json
        trades = _make_strongly_profitable_trades(50, seed=42)
        result = self.validator.validate_strategy("Serialize Test", trades)
        d = result.to_dict()

        # Should not raise
        json_str = json.dumps(d, default=str)
        assert isinstance(json_str, str)
        assert "Serialize Test" in json_str

    def test_v2_threshold_stricter(self):
        """V2 threshold (60%) should be stricter than default (50%)."""
        # Trades that are borderline profitable (around 55% windows profitable)
        trades = _make_trades(
            n_trades=100,
            mean_pnl=5.0,   # Slightly positive
            std_pnl=60.0,   # High variance
            seed=123,
        )

        # Default validator
        v_default = WalkForwardValidator(
            is_ratio=0.70, n_windows=3, min_trades_per_window=3,
            min_total_trades=15, v2_threshold=False,
        )
        # V2 validator
        v_v2 = WalkForwardValidator(
            is_ratio=0.70, n_windows=3, min_trades_per_window=3,
            min_total_trades=15, v2_threshold=True,
        )

        r_default = v_default.validate_strategy("Borderline", trades)
        r_v2 = v_v2.validate_strategy("Borderline", trades)

        # V2 should be at least as strict (same or worse verdict)
        verdict_rank = {"VALIDATED": 3, "BORDERLINE": 2, "REJECTED": 1}
        assert verdict_rank[r_v2.verdict] <= verdict_rank[r_default.verdict], (
            f"V2 should be stricter: default={r_default.verdict}, v2={r_v2.verdict}"
        )

    def test_data_source_tracked(self):
        """data_source should be propagated to result."""
        trades = _make_strongly_profitable_trades(50, seed=42)
        result = self.validator.validate_strategy(
            "Source Test", trades, data_source="/path/to/trades.csv"
        )
        assert result.data_source == "/path/to/trades.csv"

    def test_single_date_trades(self):
        """All trades on same date -> REJECTED (can't build windows)."""
        same_date = date(2025, 10, 1)
        trades = pd.DataFrame({
            "date": [same_date] * 20,
            "net_pnl": np.random.normal(10, 5, 20),
            "direction": "LONG",
        })
        result = self.validator.validate_strategy("Same Date", trades)
        assert result.verdict == "REJECTED"
        assert "window" in result.reason.lower() or "days" in result.reason.lower()


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
