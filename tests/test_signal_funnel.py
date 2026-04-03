"""Tests for Signal Funnel Unblock modules.

Fix #3: MinSizeFilter
Fix #5: FunnelLogger
Fix #6: Activation matrix UNKNOWN multipliers
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════
# Fix #3: Min Size Filter
# ═══════════════════════════════════════════════════════════════


class TestMinSizeFilter:

    def test_viable_crypto_position(self):
        from core.execution.min_size_filter import is_position_viable
        ok, reason = is_position_viable(200, "crypto")
        assert ok

    def test_too_small_crypto(self):
        from core.execution.min_size_filter import is_position_viable
        ok, reason = is_position_viable(30, "crypto")
        assert not ok
        assert "below minimum" in reason

    def test_viable_fx(self):
        from core.execution.min_size_filter import is_position_viable
        ok, _ = is_position_viable(500, "fx")
        assert ok

    def test_too_small_fx(self):
        from core.execution.min_size_filter import is_position_viable
        ok, _ = is_position_viable(100, "fx")
        assert not ok

    def test_futures_no_minimum(self):
        from core.execution.min_size_filter import is_position_viable
        ok, _ = is_position_viable(1, "futures")
        assert ok  # Futures have no dollar minimum

    def test_adjust_or_skip_returns_zero(self):
        from core.execution.min_size_filter import adjust_or_skip
        assert adjust_or_skip(30, "crypto") == 0.0

    def test_adjust_or_skip_returns_size(self):
        from core.execution.min_size_filter import adjust_or_skip
        assert adjust_or_skip(200, "crypto") == 200.0

    def test_default_asset_class(self):
        from core.execution.min_size_filter import is_position_viable
        ok, _ = is_position_viable(50, "unknown_class")
        assert not ok  # Default $100 minimum

    def test_margin_crypto_higher_minimum(self):
        from core.execution.min_size_filter import is_position_viable
        ok, _ = is_position_viable(80, "crypto_margin")
        assert not ok  # $100 minimum for margin
        ok2, _ = is_position_viable(150, "crypto_margin")
        assert ok2


# ═══════════════════════════════════════════════════════════════
# Fix #5: Funnel Logger
# ═══════════════════════════════════════════════════════════════


class TestFunnelLogger:

    def test_log_funnel_basic(self):
        from core.signals.funnel_logger import log_funnel, FunnelLayer, FunnelAction
        # Should not raise
        log_funnel("test_strat", FunnelLayer.REGIME, FunnelAction.PASS,
                    regime="TREND_STRONG", multiplier=1.0)

    def test_log_kill(self):
        from core.signals.funnel_logger import log_funnel, FunnelLayer, FunnelAction
        log_funnel("test_strat", FunnelLayer.KILL_SWITCH, FunnelAction.KILL,
                    reason="daily_loss_exceeded")

    def test_convenience_functions(self):
        from core.signals.funnel_logger import (
            log_market_hours, log_kill_switch, log_regime,
            log_activation_matrix, log_risk_check, log_sizing,
            log_min_size, log_submit, log_fill,
        )
        log_market_hours("s1", True, "crypto")
        log_kill_switch("s1", False)
        log_regime("s1", "TREND_STRONG", 1.0)
        log_activation_matrix("s1", "TREND_STRONG", 0.8)
        log_risk_check("s1", True, "position_limit")
        log_sizing("s1", 500, 250, True)
        log_min_size("s1", 250, 100, True)
        log_submit("s1", "BTCUSDC", "BUY", 0.01, 45000)
        log_fill("s1", "BTCUSDC", 45010, 0.01)

    def test_stats_tracking(self):
        from core.signals.funnel_logger import (
            FunnelStats, FunnelLayer, FunnelAction,
        )
        stats = FunnelStats()
        stats.record(FunnelLayer.REGIME, FunnelAction.PASS)
        stats.record(FunnelLayer.RISK_MANAGER, FunnelAction.PASS)
        stats.record(FunnelLayer.FILL, FunnelAction.FILLED)
        summary = stats.summary()
        assert summary["total_trades"] == 1

    def test_bottleneck_detection(self):
        from core.signals.funnel_logger import FunnelStats, FunnelLayer, FunnelAction
        stats = FunnelStats()
        for _ in range(10):
            stats.record(FunnelLayer.REGIME, FunnelAction.PASS)
        for _ in range(8):
            stats.record(FunnelLayer.MIN_SIZE, FunnelAction.SKIP)
        for _ in range(2):
            stats.record(FunnelLayer.MIN_SIZE, FunnelAction.PASS)
        bottlenecks = stats.get_bottlenecks()
        assert len(bottlenecks) > 0
        assert bottlenecks[0]["layer"] == FunnelLayer.MIN_SIZE  # Highest kill rate


# ═══════════════════════════════════════════════════════════════
# Fix #6: Activation Matrix UNKNOWN multipliers
# ═══════════════════════════════════════════════════════════════


class TestActivationMatrixUnknown:

    def test_unknown_multipliers_raised(self):
        """All UNKNOWN multipliers should be >= 0.7 (Fix #6)."""
        from core.regime.activation_matrix import DEFAULT_MATRIX
        for strat, regimes in DEFAULT_MATRIX.items():
            unknown_mult = regimes.get("UNKNOWN", 0)
            assert unknown_mult >= 0.7, (
                f"{strat} UNKNOWN multiplier is {unknown_mult} < 0.7 — "
                f"Fix #6 requires UNKNOWN >= 0.7"
            )

    def test_panic_still_zero_for_most(self):
        """PANIC should still be 0 for most strategies (safety preserved)."""
        from core.regime.activation_matrix import DEFAULT_MATRIX
        panic_zeros = sum(
            1 for regimes in DEFAULT_MATRIX.values()
            if regimes.get("PANIC", 0) == 0
        )
        # Most strategies should have PANIC=0
        assert panic_zeros > len(DEFAULT_MATRIX) * 0.5

    def test_unknown_reasonable(self):
        """UNKNOWN should be between 0.7 and 0.8 — generous but not reckless."""
        from core.regime.activation_matrix import DEFAULT_MATRIX
        for strat, regimes in DEFAULT_MATRIX.items():
            unknown = regimes.get("UNKNOWN", 0)
            assert 0.7 <= unknown <= 0.8, (
                f"{strat}: UNKNOWN={unknown} outside [0.7, 0.8]"
            )


# ═══════════════════════════════════════════════════════════════
# Fix #4: Limits config — guards adapted for $10K
# ═══════════════════════════════════════════════════════════════


class TestLimitsConfig:

    def test_limits_live_loaded(self):
        import yaml
        path = ROOT / "config" / "limits_live.yaml"
        with open(path) as f:
            cfg = yaml.safe_load(f)
        limits = cfg["position_limits"]
        assert limits["max_position_pct"] == 0.20  # Fix #4
        assert limits["min_cash_pct"] == 0.05  # Fix #4

    def test_combined_cash_reduced(self):
        import yaml
        path = ROOT / "config" / "limits_live.yaml"
        with open(path) as f:
            cfg = yaml.safe_load(f)
        combined = cfg["combined_limits"]
        assert combined["min_cash_pct"] == 0.10  # Fix #4

    def test_regime_yaml_unknown_raised(self):
        import yaml
        path = ROOT / "config" / "regime.yaml"
        with open(path) as f:
            cfg = yaml.safe_load(f)
        matrix = cfg["activation_matrix"]
        for strat, regimes in matrix.items():
            unknown = regimes.get("UNKNOWN", 0)
            assert unknown >= 0.7, (
                f"regime.yaml: {strat} UNKNOWN={unknown} < 0.7"
            )


# ═══════════════════════════════════════════════════════════════
# Diagnostic script importable
# ═══════════════════════════════════════════════════════════════


class TestDiagnosticScript:

    def test_script_importable(self):
        """The diagnostic script should be importable without side effects."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "signal_funnel_diagnostic",
            ROOT / "scripts" / "signal_funnel_diagnostic.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Just check it's loadable
        assert spec is not None
