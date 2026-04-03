"""
Tests Sprint 2 — Feature Store + 3 stratégies intraday + Monte Carlo.
"""
import pytest

from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader
from core.features.store import FeatureStore
from core.strategy_schema.validator import StrategyValidator


@pytest.fixture
def data_1h():
    return OHLCVLoader.generate_synthetic(
        asset="EURUSD", timeframe="1H", n_bars=3000, seed=42
    )


@pytest.fixture
def data_5m():
    return OHLCVLoader.generate_synthetic(
        asset="EURUSD", timeframe="5M", n_bars=5000, seed=42
    )


@pytest.fixture
def engine():
    return BacktestEngine(initial_capital=10_000.0)


@pytest.fixture
def validator():
    return StrategyValidator()


# ─── Feature Store ──────────────────────────────────────────────────────────

def test_feature_store_rsi(data_1h):
    fs = FeatureStore()
    df = fs.compute(data_1h.df, ["rsi_14"])
    assert "rsi_14" in df.columns
    valid = df["rsi_14"].dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_feature_store_adx(data_1h):
    fs = FeatureStore()
    df = fs.compute(data_1h.df, ["adx_14"])
    assert "adx_14" in df.columns
    assert "di_plus_14" in df.columns
    assert "di_minus_14" in df.columns
    valid = df["adx_14"].dropna()
    assert (valid >= 0).all()


def test_feature_store_vwap(data_1h):
    fs = FeatureStore()
    df = fs.compute(data_1h.df, ["vwap"])
    assert "vwap" in df.columns
    # VWAP doit être dans un range raisonnable des prix
    valid = df["vwap"].dropna()
    price_min = data_1h.df["low"].min()
    price_max = data_1h.df["high"].max()
    assert (valid >= price_min * 0.95).all()
    assert (valid <= price_max * 1.05).all()


def test_feature_store_bollinger(data_1h):
    fs = FeatureStore()
    df = fs.compute(data_1h.df, ["bb_upper_20_2", "bb_lower_20_2", "bb_width_20_2"])
    assert "bb_upper_20_2" in df.columns
    assert "bb_lower_20_2" in df.columns
    valid_upper = df["bb_upper_20_2"].dropna()
    valid_lower = df["bb_lower_20_2"].dropna()
    assert (valid_upper >= valid_lower).all()


def test_feature_store_no_lookahead(data_1h):
    """Le FeatureStore doit appliquer shift(1) — aucune feature ne doit utiliser la bougie courante."""
    fs = FeatureStore()
    df_orig = data_1h.df.copy()

    # Modifier les dernières bougies
    df_modified = data_1h.df.copy()
    df_modified.iloc[-20:, df_modified.columns.get_loc("close")] *= 1.10

    from core.data.loader import OHLCVData
    data_mod = OHLCVData(df_modified, "TEST", "1H", "synthetic")

    df1 = fs.compute(df_orig, ["rsi_14"])
    fs2 = FeatureStore()
    df2 = fs2.compute(df_modified, ["rsi_14"])

    # Les 2900 premières bougies doivent être identiques
    assert (df1["rsi_14"].iloc[:2900] - df2["rsi_14"].iloc[:2900]).abs().max() < 1e-8, \
        "LOOKAHEAD DETECTE dans le FeatureStore"


def test_feature_store_cache(data_1h):
    """Deux appels identiques doivent retourner le même objet (cache)."""
    fs = FeatureStore()
    df1 = fs.compute(data_1h.df, ["rsi_14", "atr_14"])
    df2 = fs.compute(data_1h.df, ["rsi_14", "atr_14"])
    assert df1 is df2  # Même objet Python = cache utilisé


# ─── Stratégie RSI filtré ADX ───────────────────────────────────────────────

def test_rsi_filtered_schema_valid(validator):
    s = validator.load_and_validate("strategies/rsi_filtered_v2.json")
    assert s["strategy_id"] == "rsi_filtered_v1"


def test_rsi_filtered_fewer_signals_than_unflitered(engine, data_1h, validator):
    """La version filtrée ADX doit générer moins de trades que sans filtre."""
    rsi_basic    = validator.load_and_validate("strategies/rsi_mean_reversion.json")
    rsi_filtered = validator.load_and_validate("strategies/rsi_filtered_v2.json")

    result_basic    = engine.run(data_1h, rsi_basic)
    result_filtered = engine.run(data_1h, rsi_filtered)

    # Le filtre ADX doit réduire le nombre de trades
    assert result_filtered.total_trades <= result_basic.total_trades, \
        "Le filtre ADX devrait réduire ou conserver le même nombre de trades"


# ─── Stratégie VWAP ─────────────────────────────────────────────────────────

def test_vwap_schema_valid(validator):
    s = validator.load_and_validate("strategies/vwap_mean_reversion.json")
    assert s["strategy_id"] == "vwap_mean_reversion_v1"


def test_vwap_generates_trades(engine, data_5m, validator):
    s = validator.load_and_validate("strategies/vwap_mean_reversion.json")
    result = engine.run(data_5m, s)
    assert result.total_trades > 0, "VWAP strategy ne génère aucun trade"


def test_vwap_costs_applied(engine, data_5m, validator):
    s = validator.load_and_validate("strategies/vwap_mean_reversion.json")
    result = engine.run(data_5m, s)
    if result.total_trades > 0:
        assert result.total_costs > 0


# ─── Stratégie ORB ──────────────────────────────────────────────────────────

def test_orb_schema_valid(validator):
    s = validator.load_and_validate("strategies/opening_range_breakout.json")
    assert s["strategy_id"] == "orb_indices_v1"


def test_orb_signals_only_after_opening(engine, data_1h, validator):
    """L'ORB ne doit pas générer de signaux avant que l'Opening Range soit établi."""
    from core.backtest.engine import orb_strategy
    s = validator.load_and_validate("strategies/opening_range_breakout.json")
    df = orb_strategy(data_1h.df, s["parameters"])

    # Les signaux ne doivent être actifs que quand or_established = 1
    long_without_or  = df["signal_long"]  & (df["or_established"] < 1)
    short_without_or = df["signal_short"] & (df["or_established"] < 1)
    assert not long_without_or.any(),  "Signal LONG avant ouverture du range"
    assert not short_without_or.any(), "Signal SHORT avant ouverture du range"


# ─── Reproductibilité toutes stratégies ─────────────────────────────────────

@pytest.mark.parametrize("strategy_file", [
    "strategies/rsi_mean_reversion.json",
    "strategies/rsi_filtered_v2.json",
    "strategies/vwap_mean_reversion.json",
    "strategies/opening_range_breakout.json",
])
def test_all_strategies_reproducible(engine, data_1h, data_5m, validator, strategy_file):
    """Chaque stratégie doit produire le même résultat en deux passes."""
    s = validator.load_and_validate(strategy_file)
    data = data_5m if s["timeframe"] == "5M" else data_1h

    r1 = engine.run(data, s)
    r2 = engine.run(data, s)
    assert r1.total_trades    == r2.total_trades
    assert r1.sharpe_ratio    == r2.sharpe_ratio
    assert r1.total_return_pct == r2.total_return_pct
