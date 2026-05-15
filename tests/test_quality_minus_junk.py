import numpy as np
import pandas as pd

from core.backtest.us_equity_event_driven import EventDrivenBacktester
from strategies_v2.us.quality_minus_junk import QMJConfig, QualityMinusJunkStrategy


def _fundamental_frame(
    profitability_base: float,
    debt_to_equity: float = 0.5,
    eps_values=None,
    n_quarters: int = 8,
    start: str = "2020-03-31",
    explicit_available: bool = True,
):
    period_ends = pd.date_range(start, periods=n_quarters, freq="QE")
    assets = 1_000.0
    equity = 500.0
    debt = equity * debt_to_equity
    gross_profit = np.linspace(profitability_base, profitability_base + 7, n_quarters)
    revenue = gross_profit + 100.0
    cost = np.full(n_quarters, 100.0)
    if eps_values is None:
        eps_values = np.linspace(1.0, 1.7, n_quarters)
    df = pd.DataFrame(
        {
            "period_end": period_ends,
            "period_type": "Q",
            "total_revenue": revenue,
            "cost_of_revenue": cost,
            "total_assets": assets,
            "total_debt": debt,
            "total_stockholder_equity": equity,
            "eps": eps_values,
        }
    )
    if explicit_available:
        df["available_date"] = df["period_end"] + pd.Timedelta(days=95)
    return df


def _strategy_for_symbols(symbols, sectors, bases=None, debts=None, eps_sets=None, n_quarters=8):
    bases = bases or {symbol: 10 + idx * 5 for idx, symbol in enumerate(symbols)}
    debts = debts or {symbol: 0.5 for symbol in symbols}
    eps_sets = eps_sets or {symbol: np.linspace(1.0, 1.7, n_quarters) for symbol in symbols}
    fundamentals = {
        symbol: _fundamental_frame(
            bases[symbol],
            debt_to_equity=debts[symbol],
            eps_values=eps_sets[symbol],
            n_quarters=n_quarters,
        )
        for symbol in symbols
    }
    return QualityMinusJunkStrategy(fundamentals=fundamentals, sector_map=sectors)


def test_qmj_signal_no_lookahead_fundamentals():
    fundamentals = {
        "AAA": pd.DataFrame(
            {
                "period_end": [pd.Timestamp("2020-12-31")],
                "period_type": ["Q"],
                "total_revenue": [200.0],
                "cost_of_revenue": [100.0],
                "total_assets": [1_000.0],
                "total_debt": [100.0],
                "total_stockholder_equity": [500.0],
                "eps": [1.0],
            }
        )
    }
    strategy = QualityMinusJunkStrategy(fundamentals=fundamentals, sector_map={"AAA": "Tech"})
    prepared = strategy.fundamentals["AAA"]

    assert strategy._known_fundamentals(prepared, pd.Timestamp("2021-03-31")).empty
    assert strategy._known_fundamentals(prepared, pd.Timestamp("2021-04-01")).shape[0] == 1


def test_qmj_sector_neutral():
    symbols = [f"T{i}" for i in range(5)] + [f"U{i}" for i in range(5)]
    sectors = {symbol: ("Tech" if symbol.startswith("T") else "Utilities") for symbol in symbols}
    bases = {symbol: idx * 10 + 10 for idx, symbol in enumerate(symbols)}
    strategy = _strategy_for_symbols(symbols, sectors, bases=bases)

    targets = strategy.build_target_portfolio("2023-01-10")
    by_sector = targets.groupby(["sector", "side"])["notional"].sum().unstack()

    assert np.allclose(by_sector["BUY"], by_sector["SELL"])


def test_qmj_dollar_neutral():
    symbols = [f"T{i}" for i in range(10)]
    sectors = {symbol: "Tech" for symbol in symbols}
    strategy = _strategy_for_symbols(symbols, sectors)

    targets = strategy.build_target_portfolio("2023-01-10")

    assert targets.loc[targets["side"] == "BUY", "notional"].sum() == targets.loc[
        targets["side"] == "SELL", "notional"
    ].sum()


def test_qmj_z_score_sector_relative():
    symbols = ["T_LOW", "T_HIGH", "U_LOW", "U_HIGH"]
    sectors = {"T_LOW": "Tech", "T_HIGH": "Tech", "U_LOW": "Utilities", "U_HIGH": "Utilities"}
    bases = {"T_LOW": 10, "T_HIGH": 20, "U_LOW": 100, "U_HIGH": 110}
    strategy = _strategy_for_symbols(symbols, sectors, bases=bases)

    scores = strategy.compute_scores("2023-01-10").set_index("symbol")

    assert scores.loc["T_LOW", "profitability_z"] < 0
    assert scores.loc["T_HIGH", "profitability_z"] > 0
    assert scores.loc["U_LOW", "profitability_z"] < 0
    assert scores.loc["U_HIGH", "profitability_z"] > 0


def test_qmj_inversions():
    symbols = ["SAFE", "JUNK"]
    sectors = {"SAFE": "Tech", "JUNK": "Tech"}
    eps_safe = np.array([1.0, 1.02, 1.01, 1.03, 1.02, 1.01, 1.03, 1.02])
    eps_junk = np.array([1.0, 2.5, -0.5, 3.0, -1.0, 2.0, -0.2, 1.8])
    strategy = _strategy_for_symbols(
        symbols,
        sectors,
        bases={"SAFE": 20, "JUNK": 20},
        debts={"SAFE": 0.1, "JUNK": 2.0},
        eps_sets={"SAFE": eps_safe, "JUNK": eps_junk},
    )

    scores = strategy.compute_scores("2023-01-10").set_index("symbol")

    assert scores.loc["SAFE", "safety_z"] > scores.loc["JUNK", "safety_z"]
    assert scores.loc["SAFE", "stability_z"] > scores.loc["JUNK", "stability_z"]


def test_qmj_rebalance_monthly():
    dates = pd.to_datetime(["2023-01-31", "2023-02-01", "2023-02-02"])

    assert QualityMinusJunkStrategy.is_rebalance_day(dates[1], dates[0]) is True
    assert QualityMinusJunkStrategy.is_rebalance_day(dates[2], dates[1]) is False


def test_qmj_filter_insufficient_history():
    symbols = ["SHORT", "ENOUGH"]
    sectors = {"SHORT": "Tech", "ENOUGH": "Tech"}
    fundamentals = {
        "SHORT": _fundamental_frame(10, n_quarters=7),
        "ENOUGH": _fundamental_frame(20, n_quarters=8),
    }
    strategy = QualityMinusJunkStrategy(fundamentals=fundamentals, sector_map=sectors)

    raw = strategy.compute_raw_metrics("2023-01-10")

    assert set(raw["symbol"]) == {"ENOUGH"}


def test_qmj_membership_snapshot_warning():
    dates = pd.bdate_range("2020-01-02", periods=3)
    data = {
        "AAA": pd.DataFrame(
            {
                "open": [100, 101, 102],
                "high": [100, 101, 102],
                "low": [100, 101, 102],
                "close": [100, 101, 102],
            },
            index=dates,
        )
    }
    engine = EventDrivenBacktester(
        universe=["AAA"],
        start=dates[0],
        end=dates[-1],
        capital=20_000,
        universe_membership_source="current",
    )

    result = engine.run(data, lambda _ctx: [])

    assert result.metrics["metadata"]["survivorship_bias_active"] is True
    assert "survivorship bias active" in result.metrics["metadata"]["warnings"]
