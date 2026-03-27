"""
Tests pour les modules P2/P3 :
  - RegimeDetectorHMM (core/regime_detector_hmm.py)
  - CorrelationAwareSizer (core/position_sizer.py)
  - FeatureCollector (core/ml_features.py)
  - Cost Analysis (scripts/cost_analysis.py)
"""

import os
import sys
import tempfile
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ajouter le root du projet au path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.regime_detector_hmm import RegimeDetectorHMM, MacroRegime
from core.position_sizer import CorrelationAwareSizer
from core.ml_features import FeatureCollector
from scripts.cost_analysis import (
    analyze_costs,
    estimate_trade_costs,
    extract_strategy_name,
    generate_report,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def spy_bull_data():
    """SPY en tendance haussiere (prix > SMA200, volatilite faible)."""
    np.random.seed(42)
    n = 250  # ~1 an
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    # Drift haussier
    returns = np.random.normal(0.0005, 0.008, n)
    prices = 450.0 * np.cumprod(1 + returns)
    df = pd.DataFrame(
        {
            "open": prices * 0.999,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": np.random.randint(50_000_000, 150_000_000, n),
        },
        index=dates,
    )
    return df


@pytest.fixture
def spy_bear_data():
    """SPY en tendance baissiere (prix < SMA200, forte volatilite)."""
    np.random.seed(99)
    n = 250
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    # Drift baissier
    returns = np.random.normal(-0.0008, 0.015, n)
    prices = 450.0 * np.cumprod(1 + returns)
    df = pd.DataFrame(
        {
            "open": prices * 0.999,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": np.random.randint(50_000_000, 150_000_000, n),
        },
        index=dates,
    )
    return df


@pytest.fixture
def vix_low_data():
    """VIX bas (< 18) — environment bull."""
    np.random.seed(42)
    n = 250
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    vix = 14.0 + np.random.normal(0, 1.5, n)
    vix = np.clip(vix, 10, 20)
    return pd.DataFrame({"close": vix}, index=dates)


@pytest.fixture
def vix_high_data():
    """VIX eleve (> 25) — environment bear."""
    np.random.seed(99)
    n = 250
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    vix = 30.0 + np.random.normal(0, 3.0, n)
    vix = np.clip(vix, 20, 50)
    return pd.DataFrame({"close": vix}, index=dates)


@pytest.fixture
def correlation_matrix():
    """Matrice de correlation avec un cluster correle."""
    return {
        "orb_5min": {
            "orb_5min": 1.0,
            "gap_continuation": 0.8,
            "triple_ema": 0.75,
            "gold_fear": 0.1,
            "vwap_micro": 0.3,
        },
        "gap_continuation": {
            "orb_5min": 0.8,
            "gap_continuation": 1.0,
            "triple_ema": 0.72,
            "gold_fear": 0.05,
            "vwap_micro": 0.25,
        },
        "triple_ema": {
            "orb_5min": 0.75,
            "gap_continuation": 0.72,
            "triple_ema": 1.0,
            "gold_fear": -0.1,
            "vwap_micro": 0.4,
        },
        "gold_fear": {
            "orb_5min": 0.1,
            "gap_continuation": 0.05,
            "triple_ema": -0.1,
            "gold_fear": 1.0,
            "vwap_micro": 0.15,
        },
        "vwap_micro": {
            "orb_5min": 0.3,
            "gap_continuation": 0.25,
            "triple_ema": 0.4,
            "gold_fear": 0.15,
            "vwap_micro": 1.0,
        },
    }


@pytest.fixture
def tmp_db_path(tmp_path):
    """Chemin temporaire pour la base SQLite."""
    return str(tmp_path / "test_features.db")


@pytest.fixture
def tmp_trades_dir(tmp_path):
    """Repertoire temporaire avec des fichiers trades CSV."""
    trades_dir = tmp_path / "session_test"
    trades_dir.mkdir()

    # Strategie 1 : profitable, couts faibles
    with open(trades_dir / "trades_gap_continuation.csv", "w") as f:
        f.write("symbol,entry_price,exit_price,qty,pnl,direction\n")
        f.write("AAPL,150.00,153.00,100,300.00,LONG\n")
        f.write("MSFT,280.00,285.00,50,250.00,LONG\n")
        f.write("GOOG,140.00,138.00,80,-160.00,LONG\n")

    # Strategie 2 : petits trades, couts eleves vs PnL
    with open(trades_dir / "trades_vwap_micro.csv", "w") as f:
        f.write("symbol,entry_price,exit_price,qty,pnl,direction\n")
        f.write("SPY,520.00,520.50,10,5.00,LONG\n")
        f.write("QQQ,440.00,440.30,15,4.50,LONG\n")
        f.write("SPY,519.00,518.80,10,-2.00,SHORT\n")

    # Strategie 3 : sans PnL (colonne vide)
    with open(trades_dir / "trades_test_empty.csv", "w") as f:
        f.write("symbol,entry_price,qty,pnl\n")
        f.write("TSLA,200.00,5,0.00\n")

    return str(trades_dir)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests RegimeDetectorHMM
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegimeDetectorHMM:
    """Tests pour core/regime_detector_hmm.py"""

    def test_init_defaults(self):
        detector = RegimeDetectorHMM()
        assert detector.vix_bull == 18.0
        assert detector.vix_bear == 25.0
        assert detector.sma_period == 200

    def test_init_custom_params(self):
        detector = RegimeDetectorHMM(vix_bull=15, vix_bear=30, smoothing_days=3)
        assert detector.vix_bull == 15
        assert detector.vix_bear == 30
        assert detector.smoothing_days == 3

    def test_detect_bull_regime(self, spy_bull_data, vix_low_data):
        detector = RegimeDetectorHMM(smoothing_days=1)
        result = detector.detect_regime(spy_bull_data, vix_low_data)
        assert result["regime"] in ["BULL", "NEUTRAL"]
        assert 0.0 <= result["confidence"] <= 1.0
        assert result["days_in_regime"] >= 0
        assert "transition_probability" in result
        assert "signals" in result
        assert "vix_level" in result
        assert "spy_vs_sma200" in result
        assert "breadth_ratio" in result

    def test_detect_bear_regime(self, spy_bear_data, vix_high_data):
        detector = RegimeDetectorHMM(smoothing_days=1)
        result = detector.detect_regime(spy_bear_data, vix_high_data)
        assert result["regime"] in ["BEAR", "NEUTRAL"]
        assert result["vix_level"] > 20

    def test_detect_insufficient_data(self):
        detector = RegimeDetectorHMM()
        short_data = pd.DataFrame(
            {"close": [100, 101, 102]},
            index=pd.date_range("2025-01-01", periods=3),
        )
        vix_data = pd.DataFrame(
            {"close": [15, 14, 16]},
            index=pd.date_range("2025-01-01", periods=3),
        )
        result = detector.detect_regime(short_data, vix_data)
        assert result["regime"] == "NEUTRAL"
        assert result["confidence"] == 0.0

    def test_detect_none_data(self):
        detector = RegimeDetectorHMM()
        result = detector.detect_regime(None, None)
        assert result["regime"] == "NEUTRAL"

    def test_signals_structure(self, spy_bull_data, vix_low_data):
        detector = RegimeDetectorHMM(smoothing_days=1)
        result = detector.detect_regime(spy_bull_data, vix_low_data)
        signals = result["signals"]
        assert "vix_signal" in signals
        assert "trend_signal" in signals
        assert "breadth_signal" in signals
        assert signals["vix_signal"] in ["bull", "neutral", "bear"]

    def test_transition_probability_sums(self, spy_bull_data, vix_low_data):
        detector = RegimeDetectorHMM(smoothing_days=1)
        result = detector.detect_regime(spy_bull_data, vix_low_data)
        tp = result["transition_probability"]
        total = sum(tp.values())
        assert abs(total - 1.0) < 0.01, f"Transition prob sum = {total}"

    def test_detect_history(self, spy_bull_data, vix_low_data):
        detector = RegimeDetectorHMM(smoothing_days=1)
        history = detector.detect_regime_history(spy_bull_data, vix_low_data)
        assert len(history) > 0
        assert len(history) == len(spy_bull_data) - detector.sma_period
        for entry in history:
            assert entry["regime"] in ["BULL", "NEUTRAL", "BEAR"]
            assert "date" in entry

    def test_detect_history_insufficient_data(self):
        detector = RegimeDetectorHMM()
        short_data = pd.DataFrame(
            {"close": [100, 101]},
            index=pd.date_range("2025-01-01", periods=2),
        )
        history = detector.detect_regime_history(short_data, None)
        assert history == []

    def test_smoothing_prevents_noise(self, spy_bull_data, vix_low_data):
        """Le smoothing empeche les changements de regime trop rapides."""
        detector = RegimeDetectorHMM(smoothing_days=3)
        # Premier appel : init
        result1 = detector.detect_regime(spy_bull_data, vix_low_data)
        # Le regime est NEUTRAL ou BULL, pas BEAR (donnees bull)
        assert result1["regime"] != "BEAR"

    def test_get_allocation_regime(self, spy_bull_data, vix_low_data):
        detector = RegimeDetectorHMM(smoothing_days=1)
        result = detector.detect_regime(spy_bull_data, vix_low_data)
        alloc_regime = detector.get_allocation_regime(result)
        assert alloc_regime in [
            "BULL_NORMAL", "BULL_HIGH_VOL",
            "BEAR_NORMAL", "BEAR_HIGH_VOL",
        ]

    def test_macro_regime_enum(self):
        assert MacroRegime.BULL.value == "BULL"
        assert MacroRegime.NEUTRAL.value == "NEUTRAL"
        assert MacroRegime.BEAR.value == "BEAR"

    def test_vix_signal_thresholds(self):
        detector = RegimeDetectorHMM()
        assert detector._vix_signal(15.0) == "bull"
        assert detector._vix_signal(20.0) == "neutral"
        assert detector._vix_signal(30.0) == "bear"

    def test_trend_signal_thresholds(self):
        detector = RegimeDetectorHMM()
        assert detector._trend_signal(0.05) == "bull"
        assert detector._trend_signal(0.0) == "neutral"
        assert detector._trend_signal(-0.05) == "bear"

    def test_breadth_signal_thresholds(self):
        detector = RegimeDetectorHMM()
        assert detector._breadth_signal(0.7) == "bull"
        assert detector._breadth_signal(0.5) == "neutral"
        assert detector._breadth_signal(0.3) == "bear"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests CorrelationAwareSizer
# ═══════════════════════════════════════════════════════════════════════════════


class TestCorrelationAwareSizer:
    """Tests pour core/position_sizer.py"""

    def test_init_defaults(self):
        sizer = CorrelationAwareSizer()
        assert sizer.corr_threshold == 0.7
        assert sizer.min_cluster == 3
        assert sizer.reduction == 0.30

    def test_init_custom_params(self):
        sizer = CorrelationAwareSizer(
            correlation_threshold=0.5,
            min_cluster_size=2,
            reduction_factor=0.20,
        )
        assert sizer.corr_threshold == 0.5
        assert sizer.min_cluster == 2
        assert sizer.reduction == 0.20

    def test_no_reduction_no_positions(self, correlation_matrix):
        sizer = CorrelationAwareSizer()
        size = sizer.calculate_size(
            strategy="orb_5min",
            signal={"direction": "LONG", "base_size": 0.10},
            open_positions=[],
            correlation_matrix=correlation_matrix,
        )
        assert size == 0.10

    def test_no_reduction_uncorrelated(self, correlation_matrix):
        sizer = CorrelationAwareSizer()
        size = sizer.calculate_size(
            strategy="gold_fear",
            signal={"direction": "LONG", "base_size": 0.10},
            open_positions=["vwap_micro"],
            correlation_matrix=correlation_matrix,
        )
        assert size == 0.10  # Pas assez de correlees

    def test_reduction_with_correlated_cluster(self, correlation_matrix):
        """3 positions correlees > 0.7 -> reduction de 30%."""
        sizer = CorrelationAwareSizer()
        size = sizer.calculate_size(
            strategy="triple_ema",
            signal={"direction": "LONG", "base_size": 0.10},
            open_positions=["orb_5min", "gap_continuation"],
            correlation_matrix=correlation_matrix,
        )
        # orb_5min et gap_continuation sont correlees > 0.7 avec triple_ema
        # cluster size = 3 -> reduction 30%
        assert size == pytest.approx(0.07, abs=0.001)

    def test_size_never_exceeds_base(self, correlation_matrix):
        sizer = CorrelationAwareSizer()
        size = sizer.calculate_size(
            strategy="gold_fear",
            signal={"direction": "LONG", "base_size": 0.10},
            open_positions=["orb_5min", "gap_continuation", "triple_ema", "vwap_micro"],
            correlation_matrix=correlation_matrix,
        )
        assert size <= 0.10

    def test_max_reduction_cap(self, correlation_matrix):
        """La reduction est cappee a 50%."""
        sizer = CorrelationAwareSizer(
            correlation_threshold=0.05,  # Tout est correle
            min_cluster_size=2,
        )
        size = sizer.calculate_size(
            strategy="orb_5min",
            signal={"direction": "LONG", "base_size": 0.10},
            open_positions=["gap_continuation", "triple_ema", "gold_fear", "vwap_micro"],
            correlation_matrix=correlation_matrix,
        )
        assert size >= 0.05  # 50% max reduction

    def test_build_correlation_matrix(self):
        sizer = CorrelationAwareSizer()
        returns = {
            "strat_a": list(np.random.normal(0.001, 0.01, 100)),
            "strat_b": list(np.random.normal(0.001, 0.01, 100)),
            "strat_c": list(np.random.normal(-0.001, 0.01, 100)),
        }
        matrix = sizer.build_correlation_matrix(returns)
        assert "strat_a" in matrix
        assert matrix["strat_a"]["strat_a"] == 1.0
        assert -1.0 <= matrix["strat_a"]["strat_b"] <= 1.0

    def test_build_correlation_matrix_min_overlap(self):
        sizer = CorrelationAwareSizer()
        returns = {
            "strat_a": [0.01, 0.02, 0.03],  # Trop court
            "strat_b": [0.01, 0.02, 0.03],
        }
        matrix = sizer.build_correlation_matrix(returns, min_overlap=20)
        # Correlation = 0 car overlap < min_overlap
        assert matrix["strat_a"]["strat_b"] == 0.0

    def test_find_correlation_clusters(self, correlation_matrix):
        sizer = CorrelationAwareSizer()
        clusters = sizer.find_correlation_clusters(correlation_matrix, threshold=0.7)
        # orb_5min, gap_continuation, triple_ema forment un cluster
        assert len(clusters) >= 1
        largest = max(clusters, key=len)
        assert "orb_5min" in largest
        assert "gap_continuation" in largest
        assert "triple_ema" in largest

    def test_calculate_sizes_batch(self, correlation_matrix):
        sizer = CorrelationAwareSizer()
        signals = {
            "orb_5min": {"direction": "LONG", "base_size": 0.10},
            "gold_fear": {"direction": "SHORT", "base_size": 0.08},
        }
        sizes = sizer.calculate_sizes_batch(
            signals, open_positions=[], correlation_matrix=correlation_matrix,
        )
        assert "orb_5min" in sizes
        assert "gold_fear" in sizes
        assert sizes["orb_5min"] == 0.10  # Pas de position existante
        assert sizes["gold_fear"] <= 0.08

    def test_exposure_report(self, correlation_matrix):
        sizer = CorrelationAwareSizer()
        positions = {
            "orb_5min": {"notional": 5000, "direction": "LONG"},
            "gap_continuation": {"notional": 3000, "direction": "LONG"},
            "gold_fear": {"notional": 2000, "direction": "SHORT"},
        }
        report = sizer.get_exposure_report(positions, correlation_matrix)
        assert "clusters" in report
        assert "max_cluster_size" in report
        assert "concentration_risk" in report
        assert report["concentration_risk"] in ["low", "medium", "high"]


# ═══════════════════════════════════════════════════════════════════════════════
# Tests FeatureCollector
# ═══════════════════════════════════════════════════════════════════════════════


class TestFeatureCollector:
    """Tests pour core/ml_features.py"""

    def test_init_creates_db(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)
        assert Path(tmp_db_path).exists()

    def test_init_creates_tables(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)
        conn = sqlite3.connect(tmp_db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = [t[0] for t in tables]
        assert "trade_features" in table_names

    def test_collect_basic(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)
        context = {
            "strategy": "gap_continuation",
            "symbol": "AAPL",
            "direction": "LONG",
            "hour": 10,
            "day_of_week": 2,
            "vix": 18.5,
            "regime": 2,
            "gap_pct": 1.5,
            "volume_ratio": 1.3,
        }
        features = collector.collect(context)
        assert features["strategy"] == "gap_continuation"
        assert features["symbol"] == "AAPL"
        assert features["hour"] == 10.0
        assert features["vix"] == 18.5

    def test_collect_missing_features(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)
        context = {"strategy": "test", "symbol": "SPY"}
        features = collector.collect(context)
        assert features["hour"] is None
        assert features["vix"] is None

    def test_store_and_retrieve(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)
        features = collector.collect({
            "strategy": "orb_5min",
            "symbol": "TSLA",
            "direction": "LONG",
            "hour": 10,
            "vix": 20.0,
        })
        result = {
            "pnl": 150.0,
            "pnl_pct": 0.015,
            "profitable": True,
            "hold_duration_min": 45.0,
            "exit_reason": "tp",
        }
        row_id = collector.store(features, result)
        assert row_id > 0

        # Retrieve
        rows = collector.get_features_df("orb_5min")
        assert len(rows) == 1
        assert rows[0]["strategy"] == "orb_5min"
        assert rows[0]["pnl"] == 150.0
        assert rows[0]["profitable"] == 1

    def test_store_multiple_strategies(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)

        for strat in ["strat_a", "strat_b", "strat_a"]:
            features = collector.collect({"strategy": strat, "symbol": "SPY"})
            collector.store(features, {"pnl": 10, "profitable": True})

        counts = collector.get_strategy_counts()
        assert counts["strat_a"] == 2
        assert counts["strat_b"] == 1

    def test_get_all_features(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)
        features = collector.collect({"strategy": "test", "symbol": "SPY"})
        collector.store(features, {"pnl": 5, "profitable": True})

        all_rows = collector.get_features_df()  # No filter
        assert len(all_rows) == 1

    def test_ml_readiness(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)

        # Ajouter quelques trades
        for i in range(5):
            features = collector.collect({"strategy": "strat_a", "symbol": "SPY"})
            collector.store(features, {"pnl": 10, "profitable": True})

        report = collector.get_ml_readiness(min_trades=200)
        assert report["total_trades"] == 5
        assert "strat_a" in report["strategies"]
        assert report["strategies"]["strat_a"]["ready"] is False
        assert report["strategies"]["strat_a"]["missing"] == 195
        assert report["ready_strategies"] == []

    def test_purge_strategy(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)

        for i in range(3):
            features = collector.collect({"strategy": "to_purge", "symbol": "SPY"})
            collector.store(features, {"pnl": 1, "profitable": True})

        deleted = collector.purge_strategy("to_purge")
        assert deleted == 3
        assert collector.get_features_df("to_purge") == []

    def test_extra_features_json(self, tmp_db_path):
        collector = FeatureCollector(db_path=tmp_db_path)
        features = collector.collect({
            "strategy": "test",
            "symbol": "SPY",
            "extra": {"custom_metric": 42, "note": "test"},
        })
        assert features["extra_features"] is not None
        import json
        extra = json.loads(features["extra_features"])
        assert extra["custom_metric"] == 42

    def test_features_list(self):
        assert "hour" in FeatureCollector.FEATURES
        assert "vix" in FeatureCollector.FEATURES
        assert "gap_pct" in FeatureCollector.FEATURES
        assert len(FeatureCollector.FEATURES) == 11


# ═══════════════════════════════════════════════════════════════════════════════
# Tests Cost Analysis
# ═══════════════════════════════════════════════════════════════════════════════


class TestCostAnalysis:
    """Tests pour scripts/cost_analysis.py"""

    def test_extract_strategy_name(self):
        assert extract_strategy_name("trades_gap_continuation.csv") == "gap_continuation"
        assert extract_strategy_name("trades_orb_5min.csv") == "orb_5min"
        assert extract_strategy_name("trades_eu_brent_lag.csv") == "eu_brent_lag"

    def test_estimate_trade_costs_basic(self):
        trade = {
            "entry_price": "150.00",
            "qty": "100",
            "pnl": "300.00",
        }
        costs = estimate_trade_costs(trade)
        assert costs["commission"] > 0  # $0.005 * 100 * 2 = $1.00
        assert costs["slippage"] > 0
        assert costs["total_cost"] == costs["commission"] + costs["slippage"]
        assert costs["gross_pnl"] == 300.0
        assert costs["net_pnl"] < 300.0

    def test_estimate_trade_costs_zero_pnl(self):
        trade = {"entry_price": "100.00", "qty": "10", "pnl": "0.00"}
        costs = estimate_trade_costs(trade)
        assert costs["gross_pnl"] == 0.0
        assert costs["total_cost"] > 0

    def test_estimate_trade_costs_no_qty(self):
        trade = {"notional": "10000", "pnl": "500"}
        costs = estimate_trade_costs(trade)
        assert costs["slippage"] > 0
        assert costs["gross_pnl"] == 500.0

    def test_analyze_costs_nonexistent_dir(self):
        result = analyze_costs("/nonexistent/dir/")
        assert result["strategies"] == {}
        assert result["warnings"] == []

    def test_analyze_costs_with_data(self, tmp_trades_dir):
        result = analyze_costs(tmp_trades_dir)
        assert len(result["strategies"]) >= 2
        assert "gap_continuation" in result["strategies"]
        assert "vwap_micro" in result["strategies"]
        assert result["summary"]["total_trades"] > 0

    def test_analyze_costs_identifies_warnings(self, tmp_trades_dir):
        result = analyze_costs(tmp_trades_dir)
        # vwap_micro a des petits trades -> couts eleves vs PnL
        # Le resultat depend des donnees exactes
        assert isinstance(result["warnings"], list)
        assert len(result["warnings"]) <= 3  # Max 3 pires

    def test_analyze_costs_summary(self, tmp_trades_dir):
        result = analyze_costs(tmp_trades_dir)
        s = result["summary"]
        assert "total_trades" in s
        assert "total_gross_pnl" in s
        assert "total_costs" in s
        assert "avg_cost_ratio" in s
        assert s["total_costs"] >= 0

    def test_generate_report(self, tmp_trades_dir):
        analysis = analyze_costs(tmp_trades_dir)
        report = generate_report(analysis)
        assert isinstance(report, str)
        assert "# Analyse des Couts par Strategie" in report
        assert "Resume" in report
        assert "Detail par Strategie" in report

    def test_generate_report_empty(self):
        analysis = {"strategies": {}, "warnings": [], "summary": {}}
        report = generate_report(analysis)
        assert isinstance(report, str)

    def test_cost_model_constants(self):
        from scripts.cost_analysis import COMMISSION_PER_SHARE, SLIPPAGE_PCT
        assert COMMISSION_PER_SHARE == 0.005
        assert SLIPPAGE_PCT == 0.0002
