"""Tests for IBKR strategies migrated to BacktesterV2 StrategyBase.

8 strategies x 5 tests each = 40 tests:
  - config: verify name, asset_class, broker
  - parameters: get_parameters returns all expected keys
  - parameter_grid: get_parameter_grid returns valid grid
  - signal_none_insufficient_data: returns None when not enough data
  - deterministic: same bar + same state = same signal
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState
from strategies_v2.eu.eu_gap_open import EUGapOpen
from strategies_v2.futures.mcl_brent_lag import MCLBrentLag
from strategies_v2.futures.mes_trend import MESTrend
from strategies_v2.fx.audjpy_carry import AUDJPYCarry
from strategies_v2.fx.eurgbp_mr import EURGBPMeanReversion
from strategies_v2.fx.eurjpy_carry import EURJPYCarry
from strategies_v2.fx.eurusd_trend import EURUSDTrend
from strategies_v2.fx.gbpusd_trend import GBPUSDTrend

# ─── Fixtures ────────────────────────────────────────────────────────


def _make_synthetic_data(
    symbol: str,
    n_bars: int = 500,
    base_price: float = 1.1000,
    freq: str = "1h",
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic hourly OHLCV data (random walk) for testing."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-02 08:00", periods=n_bars, freq=freq)
    returns = rng.normal(0, 0.001, n_bars)
    close = base_price * np.cumprod(1 + returns)
    high = close * (1 + rng.uniform(0, 0.002, n_bars))
    low = close * (1 - rng.uniform(0, 0.002, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0005, n_bars))
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1000, 50000, n_bars).astype(float),
        },
        index=idx,
    )


def _build_feed(symbols: list[str], base_prices: list[float]) -> DataFeed:
    """Build a DataFeed with synthetic data for given symbols."""
    sources = {}
    for sym, bp in zip(symbols, base_prices):
        sources[sym] = _make_synthetic_data(sym, base_price=bp)
    feed = DataFeed(sources)
    # Advance to bar 200 so indicators have enough history
    first_df = next(iter(sources.values()))
    feed.set_timestamp(first_df.index[200])
    return feed


def _build_feed_short(symbols: list[str], base_prices: list[float]) -> DataFeed:
    """Build a DataFeed with only 5 bars — insufficient for indicators."""
    sources = {}
    for sym, bp in zip(symbols, base_prices):
        sources[sym] = _make_synthetic_data(sym, n_bars=5, base_price=bp)
    feed = DataFeed(sources)
    first_df = next(iter(sources.values()))
    feed.set_timestamp(first_df.index[3])
    return feed


@pytest.fixture
def portfolio_state() -> PortfolioState:
    return PortfolioState(equity=100_000.0, cash=100_000.0)


# ─── Strategy Instances ──────────────────────────────────────────────


STRATEGY_CONFIGS = [
    {
        "cls": EURUSDTrend,
        "name": "eurusd_trend",
        "asset_class": "fx",
        "broker": "ibkr",
        "symbol": "EURUSD",
        "base_price": 1.1000,
        "param_keys": {"ema_fast", "ema_slow", "adx_threshold", "rsi_low", "rsi_high", "sl_atr", "tp_atr"},
    },
    {
        "cls": EURGBPMeanReversion,
        "name": "eurgbp_mean_reversion",
        "asset_class": "fx",
        "broker": "ibkr",
        "symbol": "EURGBP",
        "base_price": 0.8600,
        "param_keys": {"rsi_period", "rsi_oversold", "rsi_overbought", "bb_period", "bb_std"},
    },
    {
        "cls": EURJPYCarry,
        "name": "eurjpy_carry",
        "asset_class": "fx",
        "broker": "ibkr",
        "symbol": "EURJPY",
        "base_price": 160.0,
        "param_keys": {"ema_period", "carry_threshold"},
    },
    {
        "cls": AUDJPYCarry,
        "name": "audjpy_carry",
        "asset_class": "fx",
        "broker": "ibkr",
        "symbol": "AUDJPY",
        "base_price": 97.0,
        "param_keys": {"ema_period", "carry_threshold"},
    },
    {
        "cls": GBPUSDTrend,
        "name": "gbpusd_trend",
        "asset_class": "fx",
        "broker": "ibkr",
        "symbol": "GBPUSD",
        "base_price": 1.2700,
        "param_keys": {"ema_fast", "ema_slow", "adx_threshold", "rsi_low", "rsi_high", "sl_atr", "tp_atr"},
    },
    {
        "cls": EUGapOpen,
        "name": "eu_gap_open",
        "asset_class": "eu_equity",
        "broker": "ibkr",
        "symbol": "ESTX50",
        "base_price": 4500.0,
        "param_keys": {"min_gap_pct", "max_gap_pct", "sl_pct", "tp_pct", "close_eod"},
    },
    {
        "cls": MCLBrentLag,
        "name": "mcl_brent_lag",
        "asset_class": "futures",
        "broker": "ibkr",
        "symbol": "MCL",
        "base_price": 75.0,
        "param_keys": {"lag_threshold", "sl_ticks", "tp_ticks"},
    },
    {
        "cls": MESTrend,
        "name": "mes_trend",
        "asset_class": "futures",
        "broker": "ibkr",
        "symbol": "MES",
        "base_price": 5200.0,
        "param_keys": {"ema_fast", "ema_slow", "adx_threshold", "sl_points", "tp_points"},
    },
]


# ─── Test: config (name, asset_class, broker) ───────────────────────


@pytest.mark.parametrize(
    "cfg",
    STRATEGY_CONFIGS,
    ids=[c["name"] for c in STRATEGY_CONFIGS],
)
def test_config(cfg):
    strat = cfg["cls"]()
    assert strat.name == cfg["name"]
    assert strat.asset_class == cfg["asset_class"]
    assert strat.broker == cfg["broker"]


# ─── Test: parameters ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "cfg",
    STRATEGY_CONFIGS,
    ids=[c["name"] for c in STRATEGY_CONFIGS],
)
def test_parameters(cfg):
    strat = cfg["cls"]()
    params = strat.get_parameters()
    assert isinstance(params, dict)
    assert set(params.keys()) == cfg["param_keys"]
    # All values must be non-None
    for key, val in params.items():
        assert val is not None, f"Parameter {key} is None"


# ─── Test: parameter_grid ───────────────────────────────────────────


@pytest.mark.parametrize(
    "cfg",
    STRATEGY_CONFIGS,
    ids=[c["name"] for c in STRATEGY_CONFIGS],
)
def test_parameter_grid(cfg):
    strat = cfg["cls"]()
    grid = strat.get_parameter_grid()
    assert isinstance(grid, dict)
    # Grid keys must be a subset of parameter keys
    param_keys = set(strat.get_parameters().keys())
    for key in grid:
        assert key in param_keys, f"Grid key {key} not in parameters"
    # Each grid value must be a non-empty list
    for key, values in grid.items():
        assert isinstance(values, list), f"Grid[{key}] is not a list"
        assert len(values) >= 2, f"Grid[{key}] has fewer than 2 values"


# ─── Test: signal_none with insufficient data ───────────────────────


@pytest.mark.parametrize(
    "cfg",
    STRATEGY_CONFIGS,
    ids=[c["name"] for c in STRATEGY_CONFIGS],
)
def test_signal_none_insufficient_data(cfg, portfolio_state):
    strat = cfg["cls"]()
    feed = _build_feed_short([cfg["symbol"]], [cfg["base_price"]])
    strat.set_data_feed(feed)

    # Build a bar from the short data
    bar = feed.get_latest_bar(cfg["symbol"])
    if bar is None:
        # If even get_latest_bar returns None, strategy should also return None
        dummy_bar = Bar(
            symbol=cfg["symbol"],
            timestamp=pd.Timestamp("2024-01-02 10:00"),
            open=cfg["base_price"],
            high=cfg["base_price"] * 1.001,
            low=cfg["base_price"] * 0.999,
            close=cfg["base_price"],
            volume=1000.0,
        )
        result = strat.on_bar(dummy_bar, portfolio_state)
    else:
        result = strat.on_bar(bar, portfolio_state)

    assert result is None, (
        f"{cfg['name']} should return None with insufficient data, got {result}"
    )


# ─── Test: deterministic ────────────────────────────────────────────


@pytest.mark.parametrize(
    "cfg",
    STRATEGY_CONFIGS,
    ids=[c["name"] for c in STRATEGY_CONFIGS],
)
def test_deterministic(cfg, portfolio_state):
    """Same bar + same state = same signal, run twice."""
    feed = _build_feed([cfg["symbol"]], [cfg["base_price"]])

    strat1 = cfg["cls"]()
    strat1.set_data_feed(feed)
    strat2 = cfg["cls"]()
    strat2.set_data_feed(feed)

    bar = feed.get_latest_bar(cfg["symbol"])
    assert bar is not None, f"No bar available for {cfg['symbol']}"

    signal1 = strat1.on_bar(bar, portfolio_state)
    signal2 = strat2.on_bar(bar, portfolio_state)

    # Both must be identical (either both None or same Signal)
    if signal1 is None:
        assert signal2 is None, (
            f"{cfg['name']}: first call returned None, second returned {signal2}"
        )
    else:
        assert signal2 is not None, (
            f"{cfg['name']}: first call returned {signal1}, second returned None"
        )
        assert signal1.symbol == signal2.symbol
        assert signal1.side == signal2.side
        assert signal1.strategy_name == signal2.strategy_name
        assert signal1.stop_loss == signal2.stop_loss
        assert signal1.take_profit == signal2.take_profit
        assert signal1.strength == signal2.strength
