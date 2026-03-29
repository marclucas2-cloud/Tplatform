"""Tests for FX Carry + Momentum Filter strategy."""
import numpy as np
import pandas as pd
import pytest

from strategies_v2.fx.fx_carry_momentum_filter import (
    FXCarryMomentumFilter,
    CARRY_PAIRS,
    MOMENTUM_LOOKBACK,
    VOL_LOOKBACK,
    STRATEGY_CONFIG,
)


def _make_pair_data(n_bars=200, trend=0.002, vol=0.005):
    """Generate synthetic daily FX data with strong positive trend."""
    np.random.seed(42)
    dates = pd.bdate_range("2024-01-01", periods=n_bars, freq="B")
    returns = np.random.normal(trend, vol, n_bars)
    prices = 100 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        "open": prices * (1 - vol / 2),
        "high": prices * (1 + vol),
        "low": prices * (1 - vol),
        "close": prices,
        "volume": np.ones(n_bars) * -1,
    }, index=dates)
    return df


def _make_negative_momentum_data(n_bars=200):
    """Data with negative 63d momentum."""
    np.random.seed(123)
    dates = pd.bdate_range("2024-01-01", periods=n_bars, freq="B")
    returns = np.random.normal(-0.0003, 0.005, n_bars)
    prices = 100 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.005,
        "low": prices * 0.995,
        "close": prices,
        "volume": np.ones(n_bars) * -1,
    }, index=dates)
    return df


class TestFXCarryMomentumFilter:

    def test_init(self):
        strat = FXCarryMomentumFilter()
        assert strat._equity_start == 0
        assert strat._equity_high == 0

    def test_get_parameters(self):
        strat = FXCarryMomentumFilter()
        params = strat.get_parameters()
        assert "vol_lookback" in params
        assert "momentum_lookback" in params
        assert params["momentum_lookback"] == 63

    def test_get_parameter_grid(self):
        strat = FXCarryMomentumFilter()
        grid = strat.get_parameter_grid()
        assert "momentum_lookback" in grid
        assert 63 in grid["momentum_lookback"]

    def test_compute_sizing_insufficient_data(self):
        strat = FXCarryMomentumFilter()
        returns = pd.Series([0.001] * 5)
        assert strat.compute_sizing(returns) == 0.1  # SIZING_MIN

    def test_compute_sizing_normal(self):
        strat = FXCarryMomentumFilter()
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.0002, 0.005, 100))
        sizing = strat.compute_sizing(returns)
        assert 0.1 <= sizing <= 3.0

    def test_compute_sizing_low_vol(self):
        """Low vol -> high sizing (capped at 3.0)."""
        strat = FXCarryMomentumFilter()
        returns = pd.Series(np.random.normal(0, 0.0005, 100))
        sizing = strat.compute_sizing(returns)
        assert sizing == 3.0  # Capped

    def test_momentum_filter_positive(self):
        strat = FXCarryMomentumFilter()
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.002, 0.005, 100))
        assert strat.momentum_filter(returns) is True

    def test_momentum_filter_negative(self):
        strat = FXCarryMomentumFilter()
        np.random.seed(42)
        returns = pd.Series(np.random.normal(-0.003, 0.005, 100))
        assert strat.momentum_filter(returns) is False

    def test_momentum_filter_insufficient_data(self):
        strat = FXCarryMomentumFilter()
        returns = pd.Series([0.001] * 10)
        assert strat.momentum_filter(returns) is False

    def test_signal_fn_positive_momentum(self):
        """With positive momentum and sufficient equity, should produce carry signals."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        # Need enough equity for notional > $1K min: 50K * 15% / 4 * sizing ≈ $1.2K+
        equity = 50_000
        state = {"equity": equity, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=equity)

        assert signal is not None
        assert signal["action"] == "CARRY_REBALANCE"
        assert signal["strategy"] == "fx_carry_momentum_filter"
        assert len(signal["pairs"]) > 0
        for p in signal["pairs"]:
            assert p["pair"] in CARRY_PAIRS
            assert p["action"] == "BUY"
            assert p["notional"] > 0
            assert p["stop_loss"] > 0
            assert "momentum_63d" in p

    def test_signal_fn_negative_momentum_filters_all(self):
        """With negative momentum, all pairs should be filtered."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_negative_momentum_data(200) for pair in CARRY_PAIRS}
        state = {"equity": 10000, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=10000)

        # All pairs filtered -> None signal
        assert signal is None

    def test_signal_fn_mixed_momentum(self):
        """Some pairs positive, some negative -> partial signal."""
        strat = FXCarryMomentumFilter()
        pair_data = {}
        for i, pair in enumerate(CARRY_PAIRS):
            if i % 2 == 0:
                pair_data[pair] = _make_pair_data(200, trend=0.0003)
            else:
                pair_data[pair] = _make_negative_momentum_data(200)

        state = {"equity": 10000, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=10000)

        if signal is not None:
            assert signal["n_filtered"] > 0
            assert len(signal["pairs"]) < len(CARRY_PAIRS)

    def test_signal_fn_insufficient_data(self):
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(30) for pair in CARRY_PAIRS}
        state = {"equity": 10000, "i": 30}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=10000)
        assert signal is None

    def test_kill_switch_drawdown(self):
        """Kill switch triggers on -8% drawdown."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}

        # First call: set equity baseline
        strat.signal_fn(None, {"equity": 10000, "i": 200},
                        pair_data=pair_data, equity=10000)

        # Second call: equity dropped 9%
        signal = strat.signal_fn(None, {"equity": 9100, "i": 200},
                                 pair_data=pair_data, equity=9100)

        assert signal is not None
        assert signal["action"] == "CLOSE_ALL"
        assert "drawdown_kill" in signal["reason"]

    def test_stop_loss_present(self):
        """Every signal must have a stop loss (CRO rule)."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        state = {"equity": 10000, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=10000)

        if signal and signal.get("pairs"):
            for p in signal["pairs"]:
                assert p["stop_loss"] > 0
                assert p["stop_loss"] < p["price"]

    def test_notional_respects_allocation(self):
        """Total notional shouldn't exceed allocation * equity * max_leverage."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        equity = 10000
        state = {"equity": equity, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=equity)

        if signal:
            total = signal["total_notional"]
            max_allowed = equity * STRATEGY_CONFIG["allocation_pct"] * STRATEGY_CONFIG["max_leverage"] * len(CARRY_PAIRS)
            assert total <= max_allowed

    def test_module_level_signal_fn(self):
        """Verify module-level signal_fn works."""
        from strategies_v2.fx.fx_carry_momentum_filter import signal_fn
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        state = {"equity": 10000, "i": 200}
        result = signal_fn(None, state, pair_data=pair_data, equity=10000)
        # Should return a valid signal or None
        assert result is None or isinstance(result, dict)

    def test_strategy_config(self):
        assert STRATEGY_CONFIG["allocation_pct"] == 0.15  # Probationary
        assert STRATEGY_CONFIG["broker"] == "ibkr"
        assert len(STRATEGY_CONFIG["pairs"]) == 4

    def test_tiny_equity_skips_min_order_size(self):
        """With very small equity, notional per pair < min_order_size -> no signal."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        state = {"equity": 500, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=500)
        # $500 * 15% / 4 pairs * sizing ≈ $12-18 < $1K min -> all filtered
        assert signal is None

    # ── CRO required tests ──

    def test_kill_switch_persists_across_calls(self):
        """Kill switch tracks equity high-water mark across multiple calls."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}

        # Call 1: set baseline equity at $10K
        strat.signal_fn(None, {"equity": 10000, "i": 200},
                        pair_data=pair_data, equity=10000)
        assert strat._equity_high == 10000

        # Call 2: equity rises to $11K
        strat.signal_fn(None, {"equity": 11000, "i": 200},
                        pair_data=pair_data, equity=11000)
        assert strat._equity_high == 11000

        # Call 3: equity drops 3% to $10670 — no kill (< 8%)
        signal = strat.signal_fn(None, {"equity": 10670, "i": 200},
                                 pair_data=pair_data, equity=10670)
        assert signal is None or signal.get("action") != "CLOSE_ALL"

        # Call 4: equity drops 9% from high ($10010) — kill triggers
        signal = strat.signal_fn(None, {"equity": 10010, "i": 200},
                                 pair_data=pair_data, equity=10010)
        assert signal is not None
        assert signal["action"] == "CLOSE_ALL"

    def test_concentration_cap_limits_single_pair(self):
        """When only 1 pair active, notional capped at 20% of equity."""
        strat = FXCarryMomentumFilter()
        # Only AUDJPY has positive momentum
        pair_data = {
            "AUDJPY": _make_pair_data(200, trend=0.003),
            "USDJPY": _make_negative_momentum_data(200),
            "EURJPY": _make_negative_momentum_data(200),
            "NZDUSD": _make_negative_momentum_data(200),
        }
        equity = 50_000
        state = {"equity": equity, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=equity)

        if signal and signal.get("pairs"):
            for p in signal["pairs"]:
                # Max 20% of equity per pair
                assert p["notional"] <= equity * 0.20 + 1  # +1 for rounding

    def test_jpy_exposure_cap(self):
        """JPY exposure capped at 60% of equity across all JPY pairs."""
        from strategies_v2.fx.fx_carry_momentum_filter import MAX_SINGLE_CCY_PCT
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200, trend=0.003) for pair in CARRY_PAIRS}
        equity = 100_000
        state = {"equity": equity, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=equity)

        if signal and signal.get("pairs"):
            jpy_total = sum(p["notional"] for p in signal["pairs"] if p["pair"].endswith("JPY"))
            assert jpy_total <= equity * MAX_SINGLE_CCY_PCT + 1

    def test_vol_zero_skips_pair(self):
        """If daily vol is 0 (constant price), pair is skipped."""
        strat = FXCarryMomentumFilter()
        # Create data with constant price (vol = 0) but positive momentum hack
        n_bars = 200
        dates = pd.bdate_range("2024-01-01", periods=n_bars, freq="B")
        # Start with trend then go flat — momentum still positive from early trend
        np.random.seed(99)
        prices = np.concatenate([
            100 + np.cumsum(np.random.normal(0.1, 0.01, 100)),  # trending
            np.full(100, 110.0),  # constant price (vol=0 in last 20 bars)
        ])
        df_flat = pd.DataFrame({
            "open": prices, "high": prices, "low": prices,
            "close": prices, "volume": np.ones(n_bars) * -1,
        }, index=dates)

        pair_data = {pair: df_flat for pair in CARRY_PAIRS}
        state = {"equity": 50_000, "i": 200}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=50_000)
        # All pairs should be skipped due to vol=0
        assert signal is None

    def test_authorized_by_present(self):
        """Every signal must include _authorized_by field (pipeline rule)."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        equity = 50_000
        signal = strat.signal_fn(None, {"equity": equity, "i": 200},
                                 pair_data=pair_data, equity=equity)
        if signal:
            if signal.get("action") == "CLOSE_ALL":
                assert "_authorized_by" in signal
            elif signal.get("pairs"):
                for p in signal["pairs"]:
                    assert "_authorized_by" in p

    def test_kill_switch_has_authorized_by(self):
        """Kill switch CLOSE_ALL signal must have _authorized_by."""
        strat = FXCarryMomentumFilter()
        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        strat.signal_fn(None, {"equity": 10000, "i": 200},
                        pair_data=pair_data, equity=10000)
        signal = strat.signal_fn(None, {"equity": 9100, "i": 200},
                                 pair_data=pair_data, equity=9100)
        assert signal["action"] == "CLOSE_ALL"
        assert "_authorized_by" in signal

    def test_singleton_state_persists(self):
        """Module-level _instance shares state between calls."""
        from strategies_v2.fx.fx_carry_momentum_filter import _instance, signal_fn
        _instance._equity_start = 0
        _instance._equity_high = 0

        pair_data = {pair: _make_pair_data(200) for pair in CARRY_PAIRS}
        signal_fn(None, {"equity": 10000, "i": 200}, pair_data=pair_data, equity=10000)
        assert _instance._equity_high == 10000
