"""Tests for Binance France crypto strategies — BacktesterV2 interface.

40 tests (5 per strategy x 8 strategies):
  - config: name, asset_class, broker
  - parameters: get_parameters returns expected keys
  - parameter_grid: valid grid with lists
  - signal_none_insufficient: returns None when < 200 bars
  - deterministic: same inputs = same output
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState
from strategies_v2.crypto.altcoin_rs import AltcoinRelativeStrength
from strategies_v2.crypto.borrow_carry import BorrowRateCarry
from strategies_v2.crypto.btc_dominance import BTCDominance
from strategies_v2.crypto.btc_eth_momentum import BTCETHDualMomentum
from strategies_v2.crypto.btc_mr import BTCMeanReversion
from strategies_v2.crypto.liquidation_momentum import LiquidationMomentum
from strategies_v2.crypto.vol_breakout import VolBreakout
from strategies_v2.crypto.weekend_gap import WeekendGap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_crypto_df(n: int = 500, start_price: float = 40000.0,
                    seed: int = 42) -> pd.DataFrame:
    """Generate synthetic BTC-like OHLCV data (random walk)."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    returns = rng.normal(0.0002, 0.015, size=n)
    prices = start_price * np.cumprod(1 + returns)

    highs = prices * (1 + rng.uniform(0, 0.01, n))
    lows = prices * (1 - rng.uniform(0, 0.01, n))
    opens = np.roll(prices, 1)
    opens[0] = start_price
    volumes = rng.uniform(100, 1000, n)

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
    }, index=dates)


def _make_apy_df(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic APY data (values 0.01-0.15)."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    apys = rng.uniform(0.01, 0.15, n)
    return pd.DataFrame({
        "open": apys,
        "high": apys * 1.01,
        "low": apys * 0.99,
        "close": apys,
        "volume": np.ones(n),
    }, index=dates)


def _make_dominance_df(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic BTC dominance data (45-55%)."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    base = 50.0 + rng.normal(0, 1, n).cumsum() * 0.1
    return pd.DataFrame({
        "open": base,
        "high": base + rng.uniform(0, 0.3, n),
        "low": base - rng.uniform(0, 0.3, n),
        "close": base,
        "volume": np.ones(n),
    }, index=dates)


def _make_feed(*symbols_dfs) -> DataFeed:
    """Create a DataFeed from (symbol, df) pairs."""
    return DataFeed({sym: df for sym, df in symbols_dfs})


def _set_feed_to_bar(feed: DataFeed, n: int) -> None:
    """Advance feed timestamp past the nth bar."""
    for sym in feed.symbols:
        bars = feed._data[sym]
        if len(bars) > n:
            feed.set_timestamp(bars.index[n] + pd.Timedelta("1s"))
            return


def _portfolio() -> PortfolioState:
    return PortfolioState(equity=10000.0, cash=10000.0)


def _bar_at(df: pd.DataFrame, sym: str, idx: int) -> Bar:
    row = df.iloc[idx]
    return Bar(
        symbol=sym, timestamp=df.index[idx],
        open=float(row["open"]), high=float(row["high"]),
        low=float(row["low"]), close=float(row["close"]),
        volume=float(row["volume"]),
    )


# ---------------------------------------------------------------------------
# 1. BTCETHDualMomentum (5 tests)
# ---------------------------------------------------------------------------

class TestBTCETHDualMomentum:
    def _make(self):
        df = _make_crypto_df()
        feed = _make_feed(("BTCUSDT", df))
        return BTCETHDualMomentum(feed, symbol="BTCUSDT"), feed, df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "btc_eth_dual_momentum"
        assert strat.asset_class == "CRYPTO_BTC"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"ema_fast", "ema_slow", "adx_threshold",
                    "rsi_long_min", "rsi_long_max", "sl_atr",
                    "tp_atr", "max_holding_days"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = BTCETHDualMomentum.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        df = _make_crypto_df(n=50)
        feed = _make_feed(("BTCUSDT", df))
        strat = BTCETHDualMomentum(feed, symbol="BTCUSDT")
        _set_feed_to_bar(feed, 10)
        bar = _bar_at(df, "BTCUSDT", 9)
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        _set_feed_to_bar(feed, 400)
        bar = _bar_at(df, "BTCUSDT", 399)
        r1 = strat.on_bar(bar, _portfolio())
        feed.set_timestamp(df.index[400] + pd.Timedelta("1s"))
        r2 = strat.on_bar(bar, _portfolio())
        assert r1 == r2


# ---------------------------------------------------------------------------
# 2. AltcoinRelativeStrength (5 tests)
# ---------------------------------------------------------------------------

class TestAltcoinRelativeStrength:
    def _make(self):
        btc_df = _make_crypto_df(seed=42)
        alt_syms = ["ETHUSDT", "SOLUSDT", "ADAUSDT",
                     "DOTUSDT", "AVAXUSDT", "MATICUSDT"]
        pairs = [("BTCUSDT", btc_df)]
        for i, sym in enumerate(alt_syms):
            pairs.append((sym, _make_crypto_df(seed=42 + i + 1,
                                                start_price=2000 - i * 200)))
        feed = _make_feed(*pairs)
        strat = AltcoinRelativeStrength(
            feed, alt_symbols=alt_syms, btc_symbol="BTCUSDT",
        )
        return strat, feed, btc_df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "altcoin_relative_strength"
        assert strat.asset_class == "CRYPTO_ALT_T2"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"lookback_days", "top_n", "bottom_n", "rebalance_day"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = AltcoinRelativeStrength.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        df = _make_crypto_df(n=5)
        feed = _make_feed(("BTCUSDT", df), ("ETHUSDT", df.copy()))
        strat = AltcoinRelativeStrength(
            feed, alt_symbols=["ETHUSDT"], btc_symbol="BTCUSDT",
        )
        _set_feed_to_bar(feed, 3)
        bar = _bar_at(df, "BTCUSDT", 2)
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        # Find a Sunday bar
        for i in range(100, 400):
            if df.index[i].dayofweek == 6:
                _set_feed_to_bar(feed, i + 1)
                bar = _bar_at(df, "BTCUSDT", i)
                r1 = strat.on_bar(bar, _portfolio())
                feed.set_timestamp(df.index[i + 1] + pd.Timedelta("1s"))
                r2 = strat.on_bar(bar, _portfolio())
                assert r1 == r2
                return
        pytest.skip("No Sunday bar found in range")


# ---------------------------------------------------------------------------
# 3. BTCMeanReversion (5 tests)
# ---------------------------------------------------------------------------

class TestBTCMeanReversion:
    def _make(self):
        df = _make_crypto_df()
        feed = _make_feed(("BTCUSDT", df))
        return BTCMeanReversion(feed, symbol="BTCUSDT"), feed, df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "btc_mean_reversion"
        assert strat.asset_class == "CRYPTO_BTC"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"rsi_threshold", "bb_period", "bb_std",
                    "adx_max", "sl_pct", "max_holding_hours"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = BTCMeanReversion.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        df = _make_crypto_df(n=10)
        feed = _make_feed(("BTCUSDT", df))
        strat = BTCMeanReversion(feed, symbol="BTCUSDT")
        _set_feed_to_bar(feed, 5)
        bar = _bar_at(df, "BTCUSDT", 4)
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        _set_feed_to_bar(feed, 400)
        bar = _bar_at(df, "BTCUSDT", 399)
        r1 = strat.on_bar(bar, _portfolio())
        feed.set_timestamp(df.index[400] + pd.Timedelta("1s"))
        r2 = strat.on_bar(bar, _portfolio())
        assert r1 == r2


# ---------------------------------------------------------------------------
# 4. VolBreakout (5 tests)
# ---------------------------------------------------------------------------

class TestVolBreakout:
    def _make(self):
        df = _make_crypto_df()
        feed = _make_feed(("BTCUSDT", df))
        return VolBreakout(feed, symbol="BTCUSDT"), feed, df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "vol_breakout"
        assert strat.asset_class == "CRYPTO_BTC"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"compression_ratio", "breakout_atr_mult",
                    "confirmation_bars", "volume_mult",
                    "sl_atr", "tp_atr"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = VolBreakout.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        df = _make_crypto_df(n=10)
        feed = _make_feed(("BTCUSDT", df))
        strat = VolBreakout(feed, symbol="BTCUSDT")
        _set_feed_to_bar(feed, 5)
        bar = _bar_at(df, "BTCUSDT", 4)
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        _set_feed_to_bar(feed, 400)
        bar = _bar_at(df, "BTCUSDT", 399)
        r1 = strat.on_bar(bar, _portfolio())
        feed.set_timestamp(df.index[400] + pd.Timedelta("1s"))
        r2 = strat.on_bar(bar, _portfolio())
        assert r1 == r2


# ---------------------------------------------------------------------------
# 5. BTCDominance (5 tests)
# ---------------------------------------------------------------------------

class TestBTCDominance:
    def _make(self):
        btc_df = _make_crypto_df()
        dom_df = _make_dominance_df()
        feed = _make_feed(("BTCUSDT", btc_df), ("BTC.D", dom_df))
        return BTCDominance(feed, symbol="BTCUSDT",
                            dominance_symbol="BTC.D"), feed, btc_df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "btc_dominance"
        assert strat.asset_class == "CRYPTO_BTC"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"ema_fast", "ema_slow", "dead_zone"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = BTCDominance.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        dom_df = _make_dominance_df(n=5)
        btc_df = _make_crypto_df(n=5)
        feed = _make_feed(("BTCUSDT", btc_df), ("BTC.D", dom_df))
        strat = BTCDominance(feed, symbol="BTCUSDT",
                             dominance_symbol="BTC.D")
        _set_feed_to_bar(feed, 3)
        bar = _bar_at(btc_df, "BTCUSDT", 2)
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        _set_feed_to_bar(feed, 400)
        bar = _bar_at(df, "BTCUSDT", 399)
        r1 = strat.on_bar(bar, _portfolio())
        feed.set_timestamp(df.index[400] + pd.Timedelta("1s"))
        r2 = strat.on_bar(bar, _portfolio())
        assert r1 == r2


# ---------------------------------------------------------------------------
# 6. BorrowRateCarry (5 tests)
# ---------------------------------------------------------------------------

class TestBorrowRateCarry:
    def _make(self):
        df = _make_apy_df()
        feed = _make_feed(("USDT_APY", df))
        return BorrowRateCarry(feed, symbol="USDT_APY"), feed, df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "borrow_rate_carry"
        assert strat.asset_class == "CRYPTO_BTC"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"high_usdt_threshold", "low_all_threshold"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = BorrowRateCarry.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        df = _make_apy_df(n=3)
        feed = _make_feed(("USDT_APY", df))
        strat = BorrowRateCarry(feed, symbol="USDT_APY")
        _set_feed_to_bar(feed, 2)
        bar = _bar_at(df, "USDT_APY", 1)
        # SMA(7) needs >= 7 bars, so should return None
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        _set_feed_to_bar(feed, 400)
        bar = _bar_at(df, "USDT_APY", 399)
        r1 = strat.on_bar(bar, _portfolio())
        feed.set_timestamp(df.index[400] + pd.Timedelta("1s"))
        r2 = strat.on_bar(bar, _portfolio())
        assert r1 == r2


# ---------------------------------------------------------------------------
# 7. LiquidationMomentum (5 tests)
# ---------------------------------------------------------------------------

class TestLiquidationMomentum:
    def _make(self):
        df = _make_crypto_df()
        feed = _make_feed(("BTCUSDT", df))
        return LiquidationMomentum(feed, symbol="BTCUSDT"), feed, df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "liquidation_momentum"
        assert strat.asset_class == "CRYPTO_BTC"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"oi_drop_threshold", "price_move_threshold",
                    "volume_mult", "sl_pct", "tp_pct",
                    "max_holding_hours"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = LiquidationMomentum.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        df = _make_crypto_df(n=10)
        feed = _make_feed(("BTCUSDT", df))
        strat = LiquidationMomentum(feed, symbol="BTCUSDT")
        _set_feed_to_bar(feed, 5)
        bar = _bar_at(df, "BTCUSDT", 4)
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        _set_feed_to_bar(feed, 400)
        bar = _bar_at(df, "BTCUSDT", 399)
        r1 = strat.on_bar(bar, _portfolio())
        feed.set_timestamp(df.index[400] + pd.Timedelta("1s"))
        r2 = strat.on_bar(bar, _portfolio())
        assert r1 == r2


# ---------------------------------------------------------------------------
# 8. WeekendGap (5 tests)
# ---------------------------------------------------------------------------

class TestWeekendGap:
    def _make(self):
        df = _make_crypto_df()
        feed = _make_feed(("BTCUSDT", df))
        return WeekendGap(feed, symbol="BTCUSDT"), feed, df

    def test_config(self):
        strat, _, _ = self._make()
        assert strat.name == "weekend_gap"
        assert strat.asset_class == "CRYPTO_BTC"
        assert strat.broker == "BINANCE"

    def test_parameters(self):
        strat, _, _ = self._make()
        params = strat.get_parameters()
        expected = {"dip_min", "dip_crash", "sl_pct",
                    "max_holding_hours"}
        assert set(params.keys()) == expected

    def test_parameter_grid(self):
        grid = WeekendGap.get_parameter_grid()
        assert isinstance(grid, dict)
        for key, values in grid.items():
            assert isinstance(values, list) and len(values) >= 2

    def test_signal_none_insufficient(self):
        df = _make_crypto_df(n=10)
        feed = _make_feed(("BTCUSDT", df))
        strat = WeekendGap(feed, symbol="BTCUSDT")
        _set_feed_to_bar(feed, 5)
        bar = _bar_at(df, "BTCUSDT", 4)
        # Not enough bars (needs 72) -> None
        assert strat.on_bar(bar, _portfolio()) is None

    def test_deterministic(self):
        strat, feed, df = self._make()
        # Find a Saturday or Sunday bar
        for i in range(100, 400):
            if df.index[i].dayofweek in (5, 6):
                _set_feed_to_bar(feed, i + 1)
                bar = _bar_at(df, "BTCUSDT", i)
                r1 = strat.on_bar(bar, _portfolio())
                feed.set_timestamp(df.index[i + 1] + pd.Timedelta("1s"))
                r2 = strat.on_bar(bar, _portfolio())
                assert r1 == r2
                return
        pytest.skip("No weekend bar found in range")
