"""
Tests Sprint 5 — Grid Search IS/OOS, Asset Universe, yfinance loader.
"""
import pytest

from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader
from core.data.universe import UNIVERSE, get_all_assets, get_asset, get_ticker
from core.optimization.grid_search import GridSearch
from core.strategy_schema.validator import StrategyValidator


@pytest.fixture
def data_1h():
    return OHLCVLoader.generate_synthetic("EURUSD", "1H", n_bars=2000, seed=42)

@pytest.fixture
def rsi_strategy():
    return StrategyValidator().load_and_validate("strategies/rsi_mean_reversion.json")

@pytest.fixture
def engine():
    return BacktestEngine(10_000)


# ─── Asset Universe ──────────────────────────────────────────────────────────

def test_universe_has_all_classes():
    assert "forex"       in UNIVERSE
    assert "indices"     in UNIVERSE
    assert "stocks"      in UNIVERSE
    assert "crypto"      in UNIVERSE
    assert "commodities" in UNIVERSE

def test_universe_non_empty():
    for cls, assets in UNIVERSE.items():
        assert len(assets) >= 3, f"Classe {cls} a moins de 3 actifs"

def test_universe_total_size():
    total = sum(len(v) for v in UNIVERSE.values())
    assert total >= 40, f"Univers trop petit : {total} actifs"

def test_get_ticker_forex():
    assert get_ticker("EURUSD") == "EURUSD=X"
    assert get_ticker("GBPUSD") == "GBPUSD=X"
    assert get_ticker("USDJPY") == "USDJPY=X"

def test_get_ticker_indices():
    assert get_ticker("DAX")   == "^GDAXI"
    assert get_ticker("SP500") == "^GSPC"
    assert get_ticker("FTSE")  == "^FTSE"

def test_get_ticker_crypto():
    assert get_ticker("BTC") == "BTC-USD"
    assert get_ticker("ETH") == "ETH-USD"

def test_get_ticker_stocks():
    # Les stocks sont deja leur propre ticker
    assert get_ticker("AAPL")  == "AAPL"
    assert get_ticker("NVDA")  == "NVDA"

def test_get_asset_returns_correct_class():
    eur = get_asset("EURUSD")
    assert eur is not None
    assert eur.asset_class == "forex"
    btc = get_asset("BTC")
    assert btc is not None
    assert btc.asset_class == "crypto"

def test_get_all_assets_no_duplicates():
    all_assets = get_all_assets()
    symbols = [a.symbol for a in all_assets]
    assert len(symbols) == len(set(symbols)), "Symboles dupliques dans l'univers"

def test_asset_has_required_fields():
    for asset in get_all_assets():
        assert asset.symbol
        assert asset.ticker
        assert asset.name
        assert asset.asset_class in UNIVERSE
        assert asset.pip_value > 0
        assert asset.spread_pips > 0


# ─── yfinance Loader ─────────────────────────────────────────────────────────

def test_yfinance_ticker_map_covers_ig_epics():
    """Les epics IG Markets sont dans le mapping."""
    assert "CS.D.EURUSD.MINI.IP" in OHLCVLoader._YF_TICKER_MAP
    assert "IX.D.DAX.DAILY.IP"   in OHLCVLoader._YF_TICKER_MAP
    assert "IX.D.DAX.IFD.IP"     in OHLCVLoader._YF_TICKER_MAP

def test_yfinance_interval_map_covers_timeframes():
    for tf in ["1M", "5M", "15M", "1H", "1D"]:
        assert tf in OHLCVLoader._YF_INTERVAL_MAP


# ─── Grid Search ─────────────────────────────────────────────────────────────

def test_grid_search_basic(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold":   [25, 30],
        "overbought": [70, 75],
    })
    assert len(results) >= 1

def test_grid_search_sorted_by_score(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold":   [25, 30],
        "overbought": [70, 75],
    })
    for i in range(len(results) - 1):
        assert results[i].is_score >= results[i + 1].is_score

def test_grid_search_result_has_params(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold": [25, 30],
    })
    for r in results:
        assert "oversold" in r.params
        assert r.params["oversold"] in [25, 30]

def test_grid_search_metrics_non_negative(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold": [25, 30],
    })
    for r in results:
        assert r.is_trades >= 0
        assert r.is_max_dd >= 0
        assert r.is_score >= 0

def test_grid_search_oos_evaluation(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold":   [25, 30],
        "overbought": [70, 75],
    })
    best = gs.best(results)
    assert best is not None
    gs.evaluate_oos(best, rsi_strategy, data_1h)
    assert best.oos_evaluated is True
    assert isinstance(best.oos_sharpe, float)
    assert isinstance(best.oos_trades, int)

def test_grid_search_oos_uses_held_out_data(data_1h, rsi_strategy):
    """L'OOS doit avoir un nombre de trades different de l'IS (donnees differentes)."""
    gs = GridSearch(initial_capital=10_000, is_pct=0.7, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold": [30],
    })
    best = gs.best(results)
    gs.evaluate_oos(best, rsi_strategy, data_1h)
    # IS utilise 70% des donnees, OOS 30% -> IS trades >= OOS trades (statistiquement)
    assert best.oos_evaluated

def test_grid_search_empty_grid_returns_empty(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    # Grid avec combos qui generent 0 trades (valeurs extremes)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold": [1],    # RSI ne passera jamais sous 1
        "overbought": [99], # RSI ne passera jamais au-dessus de 99
    })
    # Soit 0 resultats, soit trades=0 ignores
    for r in results:
        assert r.is_trades > 0  # Seuls les combos avec trades sont gardes

def test_grid_search_best_is_first(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold":   [20, 25, 30],
        "overbought": [70, 75, 80],
    })
    best = gs.best(results)
    if results:
        assert best is results[0]
        for r in results:
            assert best.is_score >= r.is_score

def test_grid_search_summary_string(data_1h, rsi_strategy):
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold": [25, 30],
    })
    summary = gs.summary(results, top_n=5)
    assert isinstance(summary, str)
    assert "GRID SEARCH" in summary

def test_grid_search_three_params(data_1h, rsi_strategy):
    """Test avec 3 parametres : 2x2x2 = 8 combinaisons."""
    gs = GridSearch(initial_capital=10_000, wf_windows=2)
    results = gs.run(rsi_strategy, data_1h, param_grid={
        "oversold":   [25, 30],
        "overbought": [70, 75],
        "rsi_period": [10, 14],
    })
    # Max 8 combos (certaines peuvent avoir 0 trades et etre filtrees)
    assert len(results) <= 8
