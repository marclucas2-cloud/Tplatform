"""
Tests Sprint 3 — Paper Trading Loop + Régime de marché + Strategy Ranker.
"""
import pytest
import pandas as pd

from core.data.loader import OHLCVLoader
from core.paper_trading.loop import PaperTradingLoop
from core.regime.detector import RegimeDetector, MarketRegime
from core.ranking.ranker import StrategyRanker
from core.backtest.engine import BacktestEngine
from core.strategy_schema.validator import StrategyValidator


@pytest.fixture
def data_1h():
    return OHLCVLoader.generate_synthetic("EURUSD", "1H", n_bars=2000, seed=42)

@pytest.fixture
def data_5m():
    return OHLCVLoader.generate_synthetic("EURUSD", "5M", n_bars=3000, seed=42)

@pytest.fixture
def validator():
    return StrategyValidator()

@pytest.fixture
def rsi_strategy(validator):
    return validator.load_and_validate("strategies/rsi_mean_reversion.json")

@pytest.fixture
def vwap_strategy(validator):
    return validator.load_and_validate("strategies/vwap_mean_reversion.json")


# ─── Paper Trading Loop ──────────────────────────────────────────────────────

def test_paper_loop_runs(data_1h, rsi_strategy):
    loop = PaperTradingLoop(initial_capital=10_000)
    report = loop.run(data_1h, [rsi_strategy])
    assert report.n_bars_processed == len(data_1h.df)
    assert report.initial_capital == 10_000


def test_paper_loop_equity_curve_length(data_1h, rsi_strategy):
    loop = PaperTradingLoop(initial_capital=10_000)
    report = loop.run(data_1h, [rsi_strategy])
    assert len(report.equity_curve) > 0
    assert report.equity_curve[0] == 10_000


def test_paper_loop_multi_strategy(data_1h, rsi_strategy, vwap_strategy, data_5m):
    """Plusieurs stratégies simultanées — les stats sont séparées."""
    loop = PaperTradingLoop(initial_capital=10_000)
    # Utilise data_1h pour les deux (timeframe mismatch acceptable en test)
    report = loop.run(data_1h, [rsi_strategy, vwap_strategy])
    assert rsi_strategy["strategy_id"]  in report.strategy_stats
    assert vwap_strategy["strategy_id"] in report.strategy_stats


def test_paper_loop_capital_consistent(data_1h, rsi_strategy):
    """Le capital final doit être cohérent avec le total PnL."""
    loop = PaperTradingLoop(initial_capital=10_000)
    report = loop.run(data_1h, [rsi_strategy])
    expected = 10_000 + report.total_pnl
    assert abs(report.final_capital - expected) < 0.01


def test_paper_loop_reproducible(data_1h, rsi_strategy):
    """Deux passes identiques → mêmes résultats."""
    loop = PaperTradingLoop(initial_capital=10_000)
    r1 = loop.run(data_1h, [rsi_strategy])
    r2 = loop.run(data_1h, [rsi_strategy])
    assert r1.total_trades == r2.total_trades
    assert abs(r1.total_pnl - r2.total_pnl) < 1e-9


# ─── Régime de marché ────────────────────────────────────────────────────────

def test_regime_detector_runs(data_1h):
    detector = RegimeDetector()
    history = detector.detect_all(data_1h.df)
    assert len(history) == len(data_1h.df)


def test_regime_detector_valid_values(data_1h):
    detector = RegimeDetector()
    history = detector.detect_all(data_1h.df)
    valid_regimes = set(MarketRegime)
    for snap in history:
        assert snap.regime in valid_regimes
        assert 0.0 <= snap.confidence <= 1.0
        assert snap.adx >= 0


def test_regime_current(data_1h):
    detector = RegimeDetector()
    snap = detector.detect(data_1h.df)
    assert snap.regime in set(MarketRegime)


def test_regime_stats(data_1h):
    detector = RegimeDetector()
    stats = detector.get_regime_stats(data_1h.df)
    assert "dominant" in stats
    assert "total_bars" in stats
    assert stats["total_bars"] == len(data_1h.df)


def test_regime_strategy_routing(data_1h):
    """Les stratégies RSI ne doivent pas être autorisées en trending fort."""
    detector = RegimeDetector()
    history = detector.detect_all(data_1h.df)

    for snap in history:
        if snap.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            assert not snap.allows("rsi_mean_reversion_v1"), \
                "RSI autorisé en tendance forte — erreur de routing"
        if snap.regime == MarketRegime.RANGING:
            assert snap.allows("rsi_mean_reversion_v1") or snap.allows("vwap_mean_reversion_v1"), \
                "Aucune stratégie mean-reversion autorisée en range"


# ─── Strategy Ranker ─────────────────────────────────────────────────────────

def test_ranker_basic(data_1h, validator):
    engine = BacktestEngine(10_000)
    ranker = StrategyRanker()

    strategies = [
        validator.load_and_validate("strategies/rsi_mean_reversion.json"),
        validator.load_and_validate("strategies/rsi_filtered_v2.json"),
    ]
    results = [engine.run(data_1h, s).to_dict() for s in strategies]
    ranked = ranker.rank(results)

    assert len(ranked) == 2
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2
    assert ranked[0].score >= ranked[1].score


def test_ranker_score_range(data_1h, validator):
    """Le score doit être entre 0 et 100."""
    engine = BacktestEngine(10_000)
    ranker = StrategyRanker()
    s = validator.load_and_validate("strategies/rsi_mean_reversion.json")
    result = engine.run(data_1h, s).to_dict()
    ranked = ranker.rank([result])
    assert 0 <= ranked[0].score <= 100


def test_ranker_empty():
    ranker = StrategyRanker()
    assert ranker.rank([]) == []


def test_ranker_score_breakdown_sums_to_score(data_1h, validator):
    """La somme du breakdown doit égaler le score total."""
    engine = BacktestEngine(10_000)
    ranker = StrategyRanker()
    s = validator.load_and_validate("strategies/rsi_mean_reversion.json")
    result = engine.run(data_1h, s).to_dict()
    ranked = ranker.rank([result])
    r = ranked[0]
    expected = sum(r.score_breakdown.values())
    assert abs(r.score - expected) < 0.01
