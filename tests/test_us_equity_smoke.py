import numpy as np
import pandas as pd

from core.backtest.us_equity_event_driven import (
    CostsConfig,
    EventDrivenBacktester,
    SignalIntent,
)


def _synthetic_top30_data():
    dates = pd.bdate_range("2015-01-02", "2024-12-31")
    rng = np.random.default_rng(123)
    symbols = [f"T{i:02d}" for i in range(30)]
    data = {}

    base_returns = pd.Series(0.00045, index=dates)
    base_returns.loc["2020-02-20":"2020-03-23"] = -0.010
    base_returns.loc["2020-03-24":"2020-08-31"] = 0.0030
    base_returns.loc["2022-01-03":"2022-10-14"] = -0.0009
    base_returns.loc["2023-01-03":"2024-12-31"] = 0.00075

    for idx, symbol in enumerate(symbols):
        noise = rng.normal(0, 0.0025, len(dates))
        returns = base_returns.to_numpy() + noise + (idx % 5) * 0.00002
        close = 75 + idx * 2
        closes = []
        opens = []
        for ret in returns:
            open_price = close * (1 + rng.normal(0, 0.0008))
            close = max(5, close * (1 + ret))
            opens.append(open_price)
            closes.append(close)
        frame = pd.DataFrame(
            {
                "open": opens,
                "high": np.maximum(opens, closes),
                "low": np.minimum(opens, closes),
                "close": closes,
                "dividend": 0.0,
            },
            index=dates,
        )
        data[symbol] = frame
    return symbols, data


def test_top30_equal_weight_monthly_smoke_under_60s():
    symbols, data = _synthetic_top30_data()
    engine = EventDrivenBacktester(
        universe=symbols,
        start="2015-01-02",
        end="2024-12-31",
        capital=20_856,
        costs_config=CostsConfig(locate_fail_rate=0.0),
        seed=11,
    )

    def monthly_equal_weight(ctx):
        if ctx.as_of.month == ctx.prior_date.month:
            return []
        weight = 1.0 / len(symbols)
        return [
            SignalIntent(symbol=symbol, side="BUY", weight=weight, hold_days=21, reason="monthly_top30_smoke")
            for symbol in symbols
        ]

    result = engine.run(data, monthly_equal_weight)

    assert result.metrics["n_trades"] > 900
    assert result.equity_curve["equity"].iloc[-1] > engine.capital
    assert result.metrics["max_drawdown"] > -0.30
    assert result.metrics["regime_breakdown"].query("window == '2020Q1'")["observations"].iloc[0] > 0
    assert result.metrics["regime_breakdown"].query("window == '2022'")["observations"].iloc[0] > 0
