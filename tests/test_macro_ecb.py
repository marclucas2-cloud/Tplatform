"""Tests for MacroECB strategy (event-driven, intraday)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState
from strategies_v2.futures.macro_ecb import MacroECB, get_bce_dates


@pytest.fixture
def portfolio_state() -> PortfolioState:
    return PortfolioState(equity=10_000.0, cash=10_000.0)


def _make_intraday_bars(
    base_price: float,
    move_pct: float = 0.0,
    n_bars: int = 30,
    start_dt: pd.Timestamp = pd.Timestamp("2024-09-12 12:00", tz="UTC"),
    t0_bar_idx: int = 3,
) -> pd.DataFrame:
    """Build 5min bars with a step move starting at t0_bar_idx + 1.

    Default : start at 12:00 UTC (14:00 CET summer DST). Bar 3 = 12:15 UTC
    = 14:15 CET = ECB announcement (T0). Bars 4+ have move_pct fully applied.
    Bar 9 = 12:45 UTC = 14:45 CET = T0 + 30min.
    """
    rng = np.random.default_rng(42)
    timestamps = pd.date_range(start_dt, periods=n_bars, freq="5min")

    closes = np.full(n_bars, base_price, dtype=float)
    # Step move starting just after T0
    moved_price = base_price * (1 + move_pct)
    for i in range(t0_bar_idx + 1, n_bars):
        closes[i] = moved_price

    opens = np.roll(closes, 1)
    opens[0] = base_price
    highs = np.maximum(opens, closes) * (1 + rng.uniform(0, 0.0002, n_bars))
    lows = np.minimum(opens, closes) * (1 - rng.uniform(0, 0.0002, n_bars))
    vols = np.zeros(n_bars)  # Index data has volume = 0

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=timestamps,
    )


# ─── Test 1 : Configuration ──────────────────────────────────────────


def test_config_estx50():
    s = MacroECB("ESTX50")
    assert s.name == "macro_ecb_estx50"
    assert s.asset_class == "futures"
    assert s.broker == "ibkr"
    assert s.symbol == "ESTX50"


def test_config_dax():
    s = MacroECB("DAX")
    assert s.name == "macro_ecb_dax"


def test_config_cac40():
    s = MacroECB("CAC40")
    assert s.name == "macro_ecb_cac40"


def test_invalid_symbol_raises():
    with pytest.raises(ValueError):
        MacroECB("EURUSD")


# ─── Test 2 : Parameters ──────────────────────────────────────────────


def test_parameters_dict():
    s = MacroECB("DAX")
    params = s.get_parameters()
    expected_keys = {
        "symbol", "momentum_threshold", "obs_minutes",
        "sl_pct_of_move", "tp_mult_of_move", "max_hold_minutes",
    }
    assert set(params.keys()) == expected_keys
    assert params["symbol"] == "DAX"


def test_parameter_grid():
    s = MacroECB("DAX")
    grid = s.get_parameter_grid()
    assert isinstance(grid, dict)
    assert "momentum_threshold" in grid
    assert all(isinstance(v, list) and len(v) >= 2 for v in grid.values())


def test_set_parameters():
    s = MacroECB("DAX")
    s.set_parameters({"momentum_threshold": 0.002, "obs_minutes": 45})
    assert s.momentum_threshold == 0.002
    assert s.obs_minutes == 45


# ─── Test 3 : BCE calendar ───────────────────────────────────────────


def test_bce_calendar_loads():
    dates = get_bce_dates()
    assert len(dates) >= 40, f"Expected >= 40 BCE dates, got {len(dates)}"
    # Verify a known date
    assert date(2024, 9, 12) in dates  # Sept 2024 ECB meeting
    assert date(2025, 1, 30) in dates  # Jan 2025 ECB meeting


# ─── Test 4 : Signal logic ───────────────────────────────────────────


def test_no_signal_on_non_ecb_day(portfolio_state):
    """A normal day (not ECB) returns None."""
    s = MacroECB("DAX")
    # 2024-09-13 is NOT an ECB day (it's the day after the Sept 2024 meeting)
    bars = _make_intraday_bars(
        base_price=18000.0, move_pct=0.005,
        start_dt=pd.Timestamp("2024-09-13 12:00", tz="UTC"),
    )
    feed = DataFeed({"DAX": bars})
    feed.set_timestamp(bars.index[20])  # Equiv 14:40 UTC = 16:40 CET
    s.set_data_feed(feed)

    bar = Bar(symbol="DAX", timestamp=bars.index[20],
              open=bars["open"].iloc[20], high=bars["high"].iloc[20],
              low=bars["low"].iloc[20], close=bars["close"].iloc[20],
              volume=0)
    sig = s.on_bar(bar, portfolio_state)
    assert sig is None


def test_signal_on_ecb_day_with_strong_move(portfolio_state):
    """ECB day + 0.5% move post 14:15 CET -> signal in direction."""
    s = MacroECB("DAX")
    # 2024-09-12 = ECB day, summer DST so 14:15 CET = 12:15 UTC
    # Bars start at 12:00 UTC, so bar #3 = 12:15 UTC = 14:15 CET (T0)
    # Bar #9 = 12:45 UTC = 14:45 CET (T0 + 30min)
    bars = _make_intraday_bars(
        base_price=18000.0, move_pct=0.005,
        start_dt=pd.Timestamp("2024-09-12 12:00", tz="UTC"),
    )
    feed = DataFeed({"DAX": bars})
    feed.set_timestamp(bars.index[10])  # ~12:50 UTC = 14:50 CET
    s.set_data_feed(feed)

    bar = Bar(symbol="DAX", timestamp=bars.index[10],
              open=bars["open"].iloc[10], high=bars["high"].iloc[10],
              low=bars["low"].iloc[10], close=bars["close"].iloc[10],
              volume=0)
    sig = s.on_bar(bar, portfolio_state)
    assert sig is not None, "Expected signal on ECB day with 0.5% move"
    assert sig.symbol == "DAX"
    assert sig.side == "BUY", f"Expected BUY (positive move), got {sig.side}"
    assert sig.stop_loss is not None
    assert sig.take_profit is not None
    assert sig.stop_loss < bar.close < sig.take_profit


def test_no_signal_on_small_move(portfolio_state):
    """ECB day but |move| < 0.15% -> None."""
    s = MacroECB("DAX")
    bars = _make_intraday_bars(
        base_price=18000.0, move_pct=0.0005,  # 0.05% move
        start_dt=pd.Timestamp("2024-09-12 12:00", tz="UTC"),
    )
    feed = DataFeed({"DAX": bars})
    feed.set_timestamp(bars.index[10])
    s.set_data_feed(feed)

    bar = Bar(symbol="DAX", timestamp=bars.index[10],
              open=bars["open"].iloc[10], high=bars["high"].iloc[10],
              low=bars["low"].iloc[10], close=bars["close"].iloc[10],
              volume=0)
    sig = s.on_bar(bar, portfolio_state)
    assert sig is None


def test_one_signal_per_day(portfolio_state):
    """Once a signal fires, subsequent bars same day return None."""
    s = MacroECB("DAX")
    bars = _make_intraday_bars(
        base_price=18000.0, move_pct=0.005,
        start_dt=pd.Timestamp("2024-09-12 12:00", tz="UTC"),
    )
    feed = DataFeed({"DAX": bars})
    feed.set_timestamp(bars.index[10])
    s.set_data_feed(feed)

    bar1 = Bar(symbol="DAX", timestamp=bars.index[10],
               open=bars["open"].iloc[10], high=bars["high"].iloc[10],
               low=bars["low"].iloc[10], close=bars["close"].iloc[10],
               volume=0)
    sig1 = s.on_bar(bar1, portfolio_state)
    assert sig1 is not None

    # Second bar same day -> should be None
    feed.set_timestamp(bars.index[15])
    bar2 = Bar(symbol="DAX", timestamp=bars.index[15],
               open=bars["open"].iloc[15], high=bars["high"].iloc[15],
               low=bars["low"].iloc[15], close=bars["close"].iloc[15],
               volume=0)
    sig2 = s.on_bar(bar2, portfolio_state)
    assert sig2 is None


def test_no_signal_outside_window(portfolio_state):
    """ECB day but bar at 13:00 CET (before 14:45) -> None."""
    s = MacroECB("DAX")
    bars = _make_intraday_bars(
        base_price=18000.0, move_pct=0.005,
        start_dt=pd.Timestamp("2024-09-12 09:00", tz="UTC"),
    )
    feed = DataFeed({"DAX": bars})
    # 11:00 UTC = 13:00 CET (before 14:45 cutoff)
    feed.set_timestamp(bars.index[20])
    s.set_data_feed(feed)

    bar = Bar(symbol="DAX", timestamp=bars.index[20],
              open=bars["open"].iloc[20], high=bars["high"].iloc[20],
              low=bars["low"].iloc[20], close=bars["close"].iloc[20],
              volume=0)
    sig = s.on_bar(bar, portfolio_state)
    assert sig is None


def test_reset_fired_dates(portfolio_state):
    """reset_fired_dates() allows re-firing on the same date."""
    s = MacroECB("DAX")
    bars = _make_intraday_bars(
        base_price=18000.0, move_pct=0.005,
        start_dt=pd.Timestamp("2024-09-12 12:00", tz="UTC"),
    )
    feed = DataFeed({"DAX": bars})
    feed.set_timestamp(bars.index[10])
    s.set_data_feed(feed)

    bar = Bar(symbol="DAX", timestamp=bars.index[10],
              open=bars["open"].iloc[10], high=bars["high"].iloc[10],
              low=bars["low"].iloc[10], close=bars["close"].iloc[10],
              volume=0)
    sig1 = s.on_bar(bar, portfolio_state)
    assert sig1 is not None

    s.reset_fired_dates()
    sig2 = s.on_bar(bar, portfolio_state)
    assert sig2 is not None  # Should fire again after reset
