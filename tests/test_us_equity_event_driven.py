import numpy as np
import pandas as pd
import pytest

from core.backtest.us_equity_event_driven import (
    CostsConfig,
    EventDrivenBacktester,
    SignalIntent,
)


def _ohlc(dates, opens, closes=None, dividends=None, split_factor=None):
    dates = pd.to_datetime(dates)
    opens = np.asarray(opens, dtype=float)
    closes = opens if closes is None else np.asarray(closes, dtype=float)
    dividends = np.zeros(len(dates)) if dividends is None else np.asarray(dividends, dtype=float)
    split_factor = np.ones(len(dates)) if split_factor is None else np.asarray(split_factor, dtype=float)
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "dividend": dividends,
            "split_factor": split_factor,
        },
        index=dates,
    )


def _engine(costs=None, seed=42, start="2020-01-01", end="2020-01-31", capital=10_000):
    return EventDrivenBacktester(
        universe=["AAA"],
        start=start,
        end=end,
        capital=capital,
        costs_config=costs or CostsConfig(locate_fail_rate=0.0),
        seed=seed,
    )


def test_signal_uses_shifted_close():
    dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"])
    data = {"AAA": _ohlc(dates, [100, 100, 100, 100, 100], [100, 100, 999, 100, 100])}
    shock_date = dates[2]

    def signal(ctx):
        close = ctx.history["AAA"]["close"].iloc[-1]
        if close > 500:
            return [SignalIntent("AAA", "BUY", weight=0.10, hold_days=1)]
        return []

    result = _engine(start=dates[0], end=dates[-1]).run(data, signal)

    assert shock_date not in set(result.trades["entry_date"])
    assert result.trades.iloc[0]["entry_date"] == dates[3]


def test_open_execution_not_close():
    dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    data = {"AAA": _ohlc(dates, [100, 123, 140], [100, 999, 888])}

    def signal(ctx):
        if len(ctx.history["AAA"]) == 1:
            return [SignalIntent("AAA", "BUY", weight=0.10, hold_days=1)]
        return []

    result = _engine(start=dates[0], end=dates[-1]).run(data, signal)

    trade = result.trades.iloc[0]
    assert trade["entry_price"] == 123
    assert trade["exit_price"] == 140
    assert trade["entry_price"] != 999


def test_no_future_earnings_leak():
    dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
    data = {"AAA": _ohlc(dates, [100, 101, 102, 103])}
    future_earnings = pd.DataFrame({"surprise": [0.25]}, index=[pd.Timestamp("2020-01-16")])

    seen_future = []

    def signal(ctx):
        earnings = ctx.earnings_history.get("AAA", pd.DataFrame())
        seen_future.append((earnings.index > ctx.prior_date).any())
        return []

    _engine(start=dates[0], end=dates[-1]).run(data, signal, earnings_data={"AAA": future_earnings})

    assert not any(seen_future)


def test_split_adjustment_consistency():
    dates = pd.bdate_range("2020-01-02", periods=6)
    data = {
        "AAA": _ohlc(
            dates,
            [100, 101, 102, 102.5, 103, 104],
            [100.5, 101.5, 102.2, 102.8, 103.5, 104.5],
            split_factor=[1, 1, 2, 1, 1, 1],
        )
    }

    def signal(ctx):
        if len(ctx.history["AAA"]) == 1:
            return [SignalIntent("AAA", "BUY", weight=0.50, hold_days=4)]
        return []

    result = _engine(start=dates[0], end=dates[-1]).run(data, signal)
    equity = result.equity_curve["equity"]

    assert equity.pct_change().abs().max() < 0.05
    assert result.metrics["metadata"]["data_limitations"]


def test_dividend_credit_long_debit_short():
    dates = pd.bdate_range("2020-01-02", periods=5)
    data = {"AAA": _ohlc(dates, [100, 100, 100, 100, 100], dividends=[0, 0, 1.0, 0, 0])}

    def long_signal(ctx):
        if len(ctx.history["AAA"]) == 1:
            return [SignalIntent("AAA", "BUY", weight=0.10, hold_days=3)]
        return []

    def short_signal(ctx):
        if len(ctx.history["AAA"]) == 1:
            return [SignalIntent("AAA", "SELL", weight=0.10, hold_days=3)]
        return []

    long_result = _engine(start=dates[0], end=dates[-1]).run(data, long_signal)
    short_result = _engine(start=dates[0], end=dates[-1]).run(data, short_signal)

    assert long_result.trades.iloc[0]["dividend_cash"] == pytest.approx(10.0)
    assert short_result.trades.iloc[0]["dividend_cash"] == pytest.approx(-10.0)


def test_borrow_cost_only_on_short_notional():
    dates = pd.bdate_range("2020-01-02", periods=5)
    data = {"AAA": _ohlc(dates, [100, 100, 100, 100, 100])}

    def make_signal(side):
        def signal(ctx):
            if len(ctx.history["AAA"]) == 1:
                return [SignalIntent("AAA", side, weight=0.10, hold_days=3)]
            return []

        return signal

    long_result = _engine(start=dates[0], end=dates[-1]).run(data, make_signal("BUY"))
    short_result = _engine(start=dates[0], end=dates[-1]).run(data, make_signal("SELL"))

    assert long_result.trades.iloc[0]["borrow_cost"] == 0
    assert short_result.trades.iloc[0]["borrow_cost"] > 0


def test_locate_fail_seed_reproducible():
    dates = pd.bdate_range("2020-01-02", periods=12)
    data = {"AAA": _ohlc(dates, np.full(len(dates), 100.0))}
    costs = CostsConfig(locate_fail_rate=0.50)

    def signal(ctx):
        return [SignalIntent("AAA", "SELL", weight=0.10, hold_days=1)]

    result_a = _engine(costs=costs, seed=7, start=dates[0], end=dates[-1]).run(data, signal)
    result_b = _engine(costs=costs, seed=7, start=dates[0], end=dates[-1]).run(data, signal)

    failures_a = result_a.events.loc[result_a.events["event"] == "locate_failed", ["date", "symbol"]].reset_index(drop=True)
    failures_b = result_b.events.loc[result_b.events["event"] == "locate_failed", ["date", "symbol"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(failures_a, failures_b)


def test_universe_no_future_membership():
    dates = pd.bdate_range("2015-01-02", periods=4)
    data = {"AAA": _ohlc(dates, [100, 101, 102, 103])}

    def signal(_ctx):
        return []

    result = _engine(start=dates[0], end=dates[-1]).run(data, signal)
    metadata = result.metrics["metadata"]

    assert metadata["survivorship_bias_active"] is True
    assert "survivorship bias active" in metadata["warnings"]


def test_costs_subtracted_from_gross_pnl():
    dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    data = {"AAA": _ohlc(dates, [100, 100, 110], [100, 100, 110])}
    costs = CostsConfig(slippage_pct=0.0002, borrow_rate_annual=0.015, locate_fail_rate=0.0)

    def signal(ctx):
        if len(ctx.history["AAA"]) == 1:
            return [SignalIntent("AAA", "BUY", weight=0.10, hold_days=1)]
        return []

    result = _engine(costs=costs, start=dates[0], end=dates[-1]).run(data, signal)
    trade = result.trades.iloc[0]

    assert trade["gross_pnl"] == pytest.approx(100.0)
    assert trade["slippage_cost"] == pytest.approx(0.42)
    assert trade["net_pnl"] == pytest.approx(trade["gross_pnl"] - trade["slippage_cost"] - trade["borrow_cost"])


def test_regime_breakdown_present():
    dates = pd.bdate_range("2020-01-02", periods=5)
    data = {"AAA": _ohlc(dates, [100, 101, 102, 103, 104])}

    def signal(_ctx):
        return []

    result = _engine(start=dates[0], end=dates[-1]).run(data, signal)
    breakdown = result.metrics["regime_breakdown"]

    assert {"bull", "bear", "sideways"} <= set(breakdown["regime"])
    assert {"sharpe", "profit_factor", "max_drawdown", "data_quality"} <= set(breakdown.columns)
