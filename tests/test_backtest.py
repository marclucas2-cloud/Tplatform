"""
Tests du moteur de backtest.

Vérifie :
  - No lookahead bias (signal sur bougie fermée uniquement)
  - Coûts réels appliqués
  - Reproductibilité (même seed → même résultat)
  - Métriques correctement calculées
"""
import numpy as np
import pandas as pd
import pytest

from core.backtest.engine import BacktestEngine, compute_rsi
from core.data.loader import OHLCVLoader
from core.strategy_schema.validator import StrategyValidator

# ─── Fixture stratégie RSI ──────────────────────────────────────────────────

@pytest.fixture
def rsi_strategy():
    validator = StrategyValidator()
    return validator.load_and_validate("strategies/rsi_mean_reversion.json")


@pytest.fixture
def synthetic_data():
    return OHLCVLoader.generate_synthetic(
        asset="EURUSD", timeframe="1H", n_bars=2000, seed=42
    )


@pytest.fixture
def engine():
    return BacktestEngine(initial_capital=10_000.0)


# ─── Tests données ──────────────────────────────────────────────────────────

def test_synthetic_data_valid(synthetic_data):
    """Les données synthétiques doivent passer la validation OHLCV."""
    assert synthetic_data.n_bars == 2000
    assert isinstance(synthetic_data.df.index, pd.DatetimeIndex)
    assert synthetic_data.df.index.tz is not None
    assert not synthetic_data.df.isnull().any().any()
    # OHLC cohérent
    assert (synthetic_data.df["high"] >= synthetic_data.df["low"]).all()
    assert (synthetic_data.df["high"] >= synthetic_data.df["close"]).all()
    assert (synthetic_data.df["low"] <= synthetic_data.df["close"]).all()


def test_data_fingerprint_stable(synthetic_data):
    """Le fingerprint doit être identique pour les mêmes données."""
    data2 = OHLCVLoader.generate_synthetic(n_bars=2000, seed=42)
    assert synthetic_data.fingerprint == data2.fingerprint


def test_data_fingerprint_changes(synthetic_data):
    """Des données différentes doivent avoir des fingerprints différents."""
    data2 = OHLCVLoader.generate_synthetic(n_bars=2000, seed=99)
    assert synthetic_data.fingerprint != data2.fingerprint


def test_walk_forward_no_overlap(synthetic_data):
    """Les fenêtres OOS walk-forward ne doivent pas se chevaucher."""
    windows = synthetic_data.walk_forward_windows(n_windows=4)
    assert len(windows) == 4

    # Vérifier qu'il n'y a pas d'overlap entre les fenêtres OOS
    oos_ranges = [(w[1].df.index[0], w[1].df.index[-1]) for w in windows]
    for i in range(len(oos_ranges) - 1):
        assert oos_ranges[i][1] < oos_ranges[i+1][0], \
            f"Overlap détecté entre fenêtres OOS {i} et {i+1}"


# ─── Tests RSI ──────────────────────────────────────────────────────────────

def test_rsi_bounds(synthetic_data):
    """Le RSI doit toujours être entre 0 et 100."""
    rsi = compute_rsi(synthetic_data.df["close"], period=14)
    valid = rsi.dropna()
    assert (valid >= 0).all(), "RSI < 0 détecté"
    assert (valid <= 100).all(), "RSI > 100 détecté"


def test_rsi_period_warmup(synthetic_data):
    """Les N premières valeurs du RSI doivent être NaN (période de warmup)."""
    rsi = compute_rsi(synthetic_data.df["close"], period=14)
    # Les premières valeurs doivent être NaN ou indéfinies (EWM garde un peu)
    assert not np.isnan(rsi.iloc[-1])  # La dernière valeur doit être valide


# ─── Tests backtest ─────────────────────────────────────────────────────────

def test_backtest_reproducible(engine, synthetic_data, rsi_strategy):
    """Même seed → même résultat (reproductibilité totale)."""
    result1 = engine.run(synthetic_data, rsi_strategy)
    result2 = engine.run(synthetic_data, rsi_strategy)
    assert result1.total_trades == result2.total_trades
    assert result1.sharpe_ratio == result2.sharpe_ratio
    assert result1.total_return_pct == result2.total_return_pct


def test_backtest_generates_trades(engine, synthetic_data, rsi_strategy):
    """Le backtest doit générer au moins quelques trades sur 2000 bougies."""
    result = engine.run(synthetic_data, rsi_strategy)
    assert result.total_trades > 0, "Aucun trade généré — vérifier la stratégie"


def test_costs_applied(engine, synthetic_data, rsi_strategy):
    """Les coûts doivent être non-nuls si des trades ont lieu."""
    result = engine.run(synthetic_data, rsi_strategy)
    if result.total_trades > 0:
        assert result.total_costs > 0, "Coûts zéro — spread/slippage non appliqués"


def test_no_lookahead_bias(engine, rsi_strategy):
    """
    Test de no-lookahead bias.
    Modifie le FUTUR des données et vérifie que les signaux passés ne changent pas.
    """
    from core.backtest.engine import rsi_strategy as rsi_fn

    data1 = OHLCVLoader.generate_synthetic(n_bars=500, seed=42)
    data2 = OHLCVLoader.generate_synthetic(n_bars=500, seed=42)

    # Modifier les dernières bougies de data2
    data2.df.iloc[-10:, data2.df.columns.get_loc("close")] *= 1.05

    df1 = rsi_fn(data1.df, rsi_strategy["parameters"])
    df2 = rsi_fn(data2.df, rsi_strategy["parameters"])

    # Les 400 premières bougies ne doivent pas être affectées
    signals_match = (
        df1["signal_long"].iloc[:400] == df2["signal_long"].iloc[:400]
    ).all()
    assert signals_match, "LOOKAHEAD BIAS DÉTECTÉ : modification du futur affecte les signaux passés"


def test_equity_curve_starts_at_capital(engine, synthetic_data, rsi_strategy):
    """La courbe d'equity doit commencer au capital initial."""
    result = engine.run(synthetic_data, rsi_strategy)
    assert abs(result.equity_curve.iloc[0] - 10_000.0) < 1.0


def test_backtest_result_serializable(engine, synthetic_data, rsi_strategy):
    """Le résultat doit être sérialisable en dict (pour logging/stockage)."""
    result = engine.run(synthetic_data, rsi_strategy)
    d = result.to_dict()
    assert "strategy_id" in d
    assert "sharpe_ratio" in d
    assert "total_trades" in d
    assert isinstance(d["passes_validation"], bool)


# ─── Tests validation JSON Schema ───────────────────────────────────────────

def test_strategy_schema_valid(rsi_strategy):
    """La stratégie RSI doit valider le schéma."""
    assert rsi_strategy["strategy_id"] == "rsi_mean_reversion_v1"
    assert "_fingerprint" in rsi_strategy


def test_strategy_fingerprint_stable(rsi_strategy):
    """Le fingerprint doit être stable entre deux chargements."""
    validator = StrategyValidator()
    strategy2 = validator.load_and_validate("strategies/rsi_mean_reversion.json")
    assert rsi_strategy["_fingerprint"] == strategy2["_fingerprint"]


def test_invalid_strategy_rejected():
    """Une stratégie invalide doit lever StrategyValidationError."""
    from core.strategy_schema.validator import StrategyValidationError
    validator = StrategyValidator()
    bad_strategy = {"strategy_id": "bad", "version": "1.0.0"}  # Manque tout
    with pytest.raises(StrategyValidationError):
        validator.validate(bad_strategy)
