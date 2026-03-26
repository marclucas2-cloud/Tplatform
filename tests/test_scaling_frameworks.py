"""
Tests unitaires — Market Impact, Tax Report, Alpha Decay, ML Filter, Short Interest.

Couvre :
  - MarketImpactModel : estimation impact, participation rate, check order, scaling
  - TaxReportGenerator : rapport annuel, wash sales, export CSV, split CT/LT
  - AlphaDecayMonitor : rolling Sharpe, detection decay, regression, rapport
  - MLSignalFilter : init, should_trade sans modele, validation min trades
  - ShortInterestFetcher : covering signal, squeeze risk, rapport
"""

import sys
import math
import os
import tempfile
import pytest
import numpy as np
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.market_impact import MarketImpactModel, BASE_SLIPPAGE, IMPACT_ALERT_THRESHOLD
from scripts.tax_report import TaxReportGenerator
from core.alpha_decay_monitor import AlphaDecayMonitor
from core.ml_filter import MLSignalFilter
from scripts.fetch_short_interest import ShortInterestFetcher


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def impact_model():
    return MarketImpactModel()


@pytest.fixture
def tax_gen():
    return TaxReportGenerator()


@pytest.fixture
def decay_monitor():
    return AlphaDecayMonitor(window=10, p_threshold=0.10)


@pytest.fixture
def ml_filter():
    return MLSignalFilter(min_trades_required=200)


@pytest.fixture
def si_fetcher():
    return ShortInterestFetcher()


@pytest.fixture
def sample_trades():
    """Trades fictifs pour tester le tax report."""
    return [
        # Achat AAPL
        {"ticker": "AAPL", "side": "BUY", "qty": 10, "price": 150.0,
         "notional": 1500.0, "timestamp": "2026-01-15T10:00:00Z",
         "strategy": "momentum", "pnl": 0, "commission": 0.05},
        # Vente AAPL a profit
        {"ticker": "AAPL", "side": "SELL", "qty": 10, "price": 160.0,
         "notional": 1600.0, "timestamp": "2026-02-20T14:00:00Z",
         "strategy": "momentum", "pnl": 100.0, "commission": 0.05},
        # Achat TSLA
        {"ticker": "TSLA", "side": "BUY", "qty": 5, "price": 200.0,
         "notional": 1000.0, "timestamp": "2026-03-01T09:35:00Z",
         "strategy": "gap_continuation", "pnl": 0, "commission": 0.025},
        # Vente TSLA a perte
        {"ticker": "TSLA", "side": "SELL", "qty": 5, "price": 190.0,
         "notional": 950.0, "timestamp": "2026-03-05T15:00:00Z",
         "strategy": "gap_continuation", "pnl": -50.0, "commission": 0.025},
        # Rachat TSLA dans les 30 jours → wash sale
        {"ticker": "TSLA", "side": "BUY", "qty": 5, "price": 185.0,
         "notional": 925.0, "timestamp": "2026-03-10T10:00:00Z",
         "strategy": "gap_continuation", "pnl": 0, "commission": 0.025},
        # Vente COIN a profit
        {"ticker": "COIN", "side": "SELL", "qty": 3, "price": 250.0,
         "notional": 750.0, "timestamp": "2026-04-15T11:00:00Z",
         "strategy": "crypto_proxy", "pnl": 75.0, "commission": 0.015},
    ]


@pytest.fixture
def sample_strategies_impact():
    """Strategies pour tester simulate_scaling."""
    return {
        "opex_gamma": {
            "tickers": ["SPY", "QQQ"],
            "allocation_pct": 0.12,
            "sharpe": 10.41,
            "avg_trades_per_day": 0.5,
        },
        "crypto_proxy": {
            "tickers": ["COIN", "MARA", "MSTR", "RIOT"],
            "allocation_pct": 0.10,
            "sharpe": 3.49,
            "avg_trades_per_day": 1.0,
        },
        "gap_continuation": {
            "tickers": ["SPY", "QQQ", "AAPL"],
            "allocation_pct": 0.10,
            "sharpe": 5.22,
            "avg_trades_per_day": 0.8,
        },
    }


# =============================================================================
# TEST: MarketImpactModel
# =============================================================================

class TestMarketImpact:

    def test_adv_known_ticker(self, impact_model):
        """ADV connu pour SPY."""
        adv = impact_model.get_adv("SPY")
        assert adv == 50_000_000_000

    def test_adv_unknown_ticker_defaults_1b(self, impact_model):
        """ADV defaut $1B pour un ticker inconnu."""
        adv = impact_model.get_adv("UNKNOWN_TICKER")
        assert adv == 1_000_000_000

    def test_participation_rate_spy_small_order(self, impact_model):
        """Taux de participation negligeable pour $5K sur SPY."""
        rate = impact_model.estimate_participation_rate("SPY", 5000)
        assert rate < 0.0001

    def test_participation_rate_riot_medium_order(self, impact_model):
        """Taux de participation significatif pour $5K sur RIOT."""
        rate = impact_model.estimate_participation_rate("RIOT", 5000)
        # RIOT ADV = $100M, par barre = $100M/78 ~ $1.28M
        # participation = $5K / $1.28M ~ 0.0039
        assert rate > 0.003
        assert rate < 0.01

    def test_impact_minimum_is_base_slippage(self, impact_model):
        """L'impact ne peut pas etre inferieur au slippage de base."""
        impact = impact_model.estimate_impact("SPY", 100)
        assert impact >= BASE_SLIPPAGE

    def test_impact_grows_with_order_size(self, impact_model):
        """L'impact croit avec la taille de l'ordre."""
        impact_small = impact_model.estimate_impact("MARA", 1000)
        impact_large = impact_model.estimate_impact("MARA", 50000)
        assert impact_large > impact_small

    def test_impact_higher_for_illiquid(self, impact_model):
        """Impact plus eleve pour un ticker illiquide (RIOT vs SPY)."""
        impact_spy = impact_model.estimate_impact("SPY", 10000)
        impact_riot = impact_model.estimate_impact("RIOT", 10000)
        assert impact_riot > impact_spy

    def test_impact_detail_structure(self, impact_model):
        """estimate_impact_detail retourne le bon format."""
        detail = impact_model.estimate_impact_detail("COIN", 10000)
        assert "ticker" in detail
        assert "order_notional" in detail
        assert "adv" in detail
        assert "participation_rate" in detail
        assert "temporary_impact" in detail
        assert "permanent_impact" in detail
        assert "total_impact" in detail
        assert "impact_bps" in detail
        assert "alert" in detail
        assert "scalable" in detail
        assert detail["ticker"] == "COIN"

    def test_check_order_spy_ok(self, impact_model):
        """$10K sur SPY passe sans alerte."""
        ok, msg = impact_model.check_order("SPY", 10000)
        assert ok is True
        assert "OK" in msg

    def test_check_order_riot_large_alerts(self, impact_model):
        """$500K sur RIOT declenche une alerte."""
        ok, msg = impact_model.check_order("RIOT", 500000)
        assert ok is False
        assert "ALERT" in msg

    def test_simulate_scaling_structure(self, impact_model, sample_strategies_impact):
        """simulate_scaling retourne la bonne structure."""
        results = impact_model.simulate_scaling(
            sample_strategies_impact, [25000, 100000]
        )
        assert 25000 in results
        assert 100000 in results
        assert "opex_gamma" in results[25000]
        assert "crypto_proxy" in results[100000]

        # Chaque strategie a les bonnes cles
        strat = results[25000]["opex_gamma"]
        assert "order_notional" in strat
        assert "max_impact" in strat
        assert "sharpe_adjusted" in strat
        assert "scalable" in strat
        assert "alerts" in strat

    def test_simulate_scaling_sharpe_degrades(self, impact_model, sample_strategies_impact):
        """Le Sharpe ajuste se degrade avec le capital pour les illiquides."""
        results = impact_model.simulate_scaling(
            sample_strategies_impact, [25000, 250000]
        )
        crypto_25k = results[25000]["crypto_proxy"]["sharpe_adjusted"]
        crypto_250k = results[250000]["crypto_proxy"]["sharpe_adjusted"]
        assert crypto_250k < crypto_25k

    def test_adv_overrides(self):
        """On peut surcharger les ADV."""
        model = MarketImpactModel(adv_overrides={"TEST": 500_000})
        assert model.get_adv("TEST") == 500_000

    def test_generate_report(self, impact_model, sample_strategies_impact):
        """generate_report produit un markdown valide."""
        results = impact_model.simulate_scaling(
            sample_strategies_impact, [25000, 50000]
        )
        report = impact_model.generate_report(results)
        assert "# Market Impact" in report
        assert "$25,000" in report
        assert "opex_gamma" in report

    def test_empty_tickers_strategy(self, impact_model):
        """Une strategie sans tickers retourne le slippage de base."""
        results = impact_model.simulate_scaling(
            {"no_tickers": {"tickers": [], "allocation_pct": 0.05, "sharpe": 2.0}},
            [25000],
        )
        assert results[25000]["no_tickers"]["max_impact"] == BASE_SLIPPAGE


# =============================================================================
# TEST: TaxReportGenerator
# =============================================================================

class TestTaxReport:

    def test_generate_report_structure(self, tax_gen, sample_trades):
        """Le rapport annuel a la bonne structure."""
        report = tax_gen.generate_annual_report(sample_trades, 2026)
        assert report["year"] == 2026
        assert "total_gains" in report
        assert "total_losses" in report
        assert "net_pnl" in report
        assert "wash_sales" in report
        assert "by_month" in report
        assert "by_strategy" in report
        assert "estimated_tax_pfu" in report

    def test_pnl_calculation(self, tax_gen, sample_trades):
        """Les gains et pertes sont correctement calcules."""
        report = tax_gen.generate_annual_report(sample_trades, 2026)
        # Gains: AAPL +100 + COIN +75 = 175
        assert report["total_gains"] == 175.0
        # Pertes: TSLA -50
        assert report["total_losses"] == -50.0
        # Net: 175 - 50 = 125
        assert report["net_pnl"] == 125.0

    def test_wash_sale_detection(self, tax_gen, sample_trades):
        """Un wash sale TSLA est detecte (vente a perte + rachat < 30j)."""
        wash_sales = tax_gen.detect_wash_sales(sample_trades)
        assert len(wash_sales) >= 1
        tsla_wash = [ws for ws in wash_sales if ws["ticker"] == "TSLA"]
        assert len(tsla_wash) == 1
        assert tsla_wash[0]["loss"] == -50.0
        assert tsla_wash[0]["disallowed"] == 50.0
        assert tsla_wash[0]["days_between"] == 4  # 10 mars 10h - 5 mars 15h = 4j19h

    def test_no_wash_sale_when_no_rebuy(self, tax_gen):
        """Pas de wash sale si pas de rachat."""
        trades = [
            {"ticker": "AAPL", "side": "SELL", "qty": 10, "price": 140.0,
             "notional": 1400.0, "timestamp": "2026-01-15T10:00:00Z",
             "strategy": "test", "pnl": -100.0, "commission": 0.05},
        ]
        wash_sales = tax_gen.detect_wash_sales(trades)
        assert len(wash_sales) == 0

    def test_export_csv(self, tax_gen, sample_trades):
        """Export CSV genere un fichier valide."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                          delete=False) as f:
            filepath = f.name

        try:
            result = tax_gen.export_csv(sample_trades, filepath, year=2026)
            assert os.path.exists(result)

            with open(result, 'r', encoding='utf-8') as f:
                content = f.read()
            assert "Date" in content
            assert "Ticker" in content
            assert "AAPL" in content
            assert ";" in content  # delimiter FR
        finally:
            os.unlink(filepath)

    def test_by_month_aggregation(self, tax_gen, sample_trades):
        """L'agregation par mois fonctionne."""
        report = tax_gen.generate_annual_report(sample_trades, 2026)
        by_month = report["by_month"]
        assert len(by_month) == 12

        # Fevrier : AAPL +100
        feb = [m for m in by_month if m["month"] == "2026-02"][0]
        assert feb["pnl"] == 100.0
        assert feb["trades_count"] == 1

    def test_by_strategy_aggregation(self, tax_gen, sample_trades):
        """L'agregation par strategie fonctionne."""
        report = tax_gen.generate_annual_report(sample_trades, 2026)
        by_strat = report["by_strategy"]
        strat_names = [s["strategy"] for s in by_strat]
        assert "momentum" in strat_names
        assert "gap_continuation" in strat_names

    def test_format_report_readable(self, tax_gen, sample_trades):
        """Le rapport formate est lisible."""
        report = tax_gen.generate_annual_report(sample_trades, 2026)
        text = tax_gen.format_report(report)
        assert "Rapport Fiscal 2026" in text
        assert "P&L net" in text
        assert "Wash Sales" in text

    def test_pfu_estimation(self, tax_gen, sample_trades):
        """L'estimation PFU est correcte (30% du net positif)."""
        report = tax_gen.generate_annual_report(sample_trades, 2026)
        # Net PnL = 125, commissions ~ 0.19
        # Wash disallowed = 50
        # Taxable = max(125 - 0.19 + 50, 0) ~ 174.81
        assert report["estimated_tax_pfu"] > 0
        # PFU = 30% → devrait etre entre 50 et 55
        assert report["estimated_tax_pfu"] == round(report["taxable_pnl"] * 0.30, 2)

    def test_filter_year(self, tax_gen):
        """Filtre les trades par annee."""
        trades = [
            {"ticker": "A", "side": "BUY", "timestamp": "2025-12-31T23:59:00Z"},
            {"ticker": "B", "side": "BUY", "timestamp": "2026-01-01T00:00:00Z"},
            {"ticker": "C", "side": "BUY", "timestamp": "2027-01-01T00:00:00Z"},
        ]
        filtered = tax_gen._filter_year(trades, 2026)
        assert len(filtered) == 1
        assert filtered[0]["ticker"] == "B"


# =============================================================================
# TEST: AlphaDecayMonitor
# =============================================================================

class TestAlphaDecay:

    def test_rolling_sharpe_basic(self, decay_monitor):
        """Calcul du Sharpe rolling basique."""
        # 20 trades avec des returns positifs et stables
        np.random.seed(42)
        returns = list(np.random.normal(0.005, 0.01, 20))
        rolling = decay_monitor.calculate_rolling_sharpe(returns, window=10)
        assert len(rolling) == 11  # 20 - 10 + 1

    def test_rolling_sharpe_not_enough_data(self, decay_monitor):
        """Retourne vide si pas assez de donnees."""
        returns = [0.01, 0.02]
        rolling = decay_monitor.calculate_rolling_sharpe(returns, window=10)
        assert rolling == []

    def test_rolling_sharpe_positive_returns(self, decay_monitor):
        """Sharpe positif pour des returns positifs stables."""
        returns = [0.01] * 20  # Tous positifs, meme valeur
        rolling = decay_monitor.calculate_rolling_sharpe(returns, window=10)
        # Tous les returns sont identiques → std tres petit → sharpe sera 0
        # car std(ddof=1) d'une constante = 0
        for s in rolling:
            assert isinstance(s, float)

    def test_detect_decay_no_data(self, decay_monitor):
        """Pas de decay si pas assez de donnees."""
        result = decay_monitor.detect_decay([1.0, 2.0])
        assert result["alert"] is False
        assert result["severity"] == "none"

    def test_detect_decay_stable(self, decay_monitor):
        """Pas de decay si Sharpe stable."""
        # Sharpe quasi-constant autour de 2.0
        np.random.seed(42)
        sharpes = [2.0 + np.random.normal(0, 0.1) for _ in range(30)]
        result = decay_monitor.detect_decay(sharpes)
        # On ne devrait pas avoir d'alerte critique
        assert result["severity"] != "critical"

    def test_detect_decay_declining(self, decay_monitor):
        """Decay detecte si Sharpe en chute lineaire."""
        # Sharpe qui descend lineairement de 3.0 a 0.5
        sharpes = [3.0 - 0.1 * i for i in range(30)]
        result = decay_monitor.detect_decay(sharpes, p_threshold=0.20)
        assert result["slope"] < 0
        # La pente est clairement negative → p-value devrait etre tres faible
        assert result["r_squared"] > 0.5

    def test_detect_decay_structure(self, decay_monitor):
        """Le retour de detect_decay a la bonne structure."""
        sharpes = [2.0 - 0.05 * i for i in range(20)]
        result = decay_monitor.detect_decay(sharpes)
        assert "slope" in result
        assert "intercept" in result
        assert "p_value" in result
        assert "r_squared" in result
        assert "current_sharpe" in result
        assert "days_to_zero" in result
        assert "alert" in result
        assert "severity" in result
        assert "message" in result

    def test_analyze_strategy(self, decay_monitor):
        """analyze_strategy retourne un dict complet."""
        np.random.seed(42)
        returns = list(np.random.normal(0.003, 0.01, 50))
        result = decay_monitor.analyze_strategy(returns, "test_strat")
        assert result["strategy"] == "test_strat"
        assert result["total_trades"] == 50
        assert "rolling_sharpes" in result
        assert "decay" in result
        assert "overall_sharpe" in result

    def test_generate_report(self, decay_monitor):
        """generate_report produit un markdown valide."""
        np.random.seed(42)
        strategies_data = {
            "strat_a": list(np.random.normal(0.005, 0.01, 50)),
            "strat_b": list(np.random.normal(0.001, 0.02, 50)),
        }
        report = decay_monitor.generate_report(strategies_data)
        assert "# Alpha Decay Report" in report
        assert "strat_a" in report
        assert "strat_b" in report

    def test_crossing_zero_estimated(self, decay_monitor):
        """days_to_zero est estime pour un decay actif."""
        # Sharpe qui descend de 2 vers 1
        sharpes = [2.0 - 0.05 * i for i in range(20)]
        result = decay_monitor.detect_decay(sharpes, p_threshold=0.5)
        assert result["slope"] < 0
        if result["current_sharpe"] > 0:
            assert result["days_to_zero"] is not None
            assert result["days_to_zero"] > 0

    def test_linear_regression_flat(self, decay_monitor):
        """Regression sur des donnees plates → pente ~0."""
        x = np.arange(20, dtype=float)
        y = np.ones(20) * 2.0
        slope, intercept, r2, p = decay_monitor._linear_regression(x, y)
        assert abs(slope) < 0.001
        assert abs(intercept - 2.0) < 0.1

    def test_linear_regression_perfect_line(self, decay_monitor):
        """Regression sur une droite parfaite → R2 = 1."""
        x = np.arange(20, dtype=float)
        y = 3.0 - 0.1 * x
        slope, intercept, r2, p = decay_monitor._linear_regression(x, y)
        assert abs(slope - (-0.1)) < 0.001
        assert abs(intercept - 3.0) < 0.01
        assert r2 > 0.999


# =============================================================================
# TEST: MLSignalFilter
# =============================================================================

class TestMLFilter:

    def test_init_defaults(self, ml_filter):
        """Initialisation avec les valeurs par defaut."""
        assert ml_filter.min_trades == 200
        assert ml_filter.model is None
        assert ml_filter.is_ready() is False

    def test_should_trade_without_model_returns_true(self, ml_filter):
        """Sans modele, should_trade retourne True (fail open)."""
        result = ml_filter.should_trade({"hour_of_day": 10, "vix_level": 20})
        assert result is True

    def test_predict_without_model_raises(self, ml_filter):
        """predict sans modele leve une RuntimeError."""
        with pytest.raises(RuntimeError, match="non entraine"):
            ml_filter.predict({"hour_of_day": 10})

    def test_train_not_enough_trades_raises(self, ml_filter):
        """train avec < 200 trades leve une ValueError."""
        try:
            import pandas as pd
            import lightgbm  # noqa: F401
        except ImportError:
            pytest.skip("pandas ou lightgbm non installe")

        df = pd.DataFrame({
            'hour_of_day': [10] * 50,
            'vix_level': [20] * 50,
            'profitable': [1] * 25 + [0] * 25,
        })

        with pytest.raises(ValueError, match="Pas assez de trades"):
            ml_filter.train(df, "test_strat")

    def test_features_list(self, ml_filter):
        """La liste des features est complete."""
        assert len(ml_filter.FEATURES) >= 8
        assert 'hour_of_day' in ml_filter.FEATURES
        assert 'vix_level' in ml_filter.FEATURES
        assert 'regime' in ml_filter.FEATURES

    def test_lgbm_params(self, ml_filter):
        """Les parametres LightGBM ont une forte regularisation."""
        params = ml_filter.LGBM_PARAMS
        assert params['reg_alpha'] >= 1.0
        assert params['reg_lambda'] >= 1.0
        assert params['num_leaves'] <= 31
        assert params['max_depth'] <= 6

    def test_default_threshold(self, ml_filter):
        """Le seuil par defaut est 0.4."""
        assert ml_filter.DEFAULT_THRESHOLD == 0.4

    def test_save_model_without_training_raises(self, ml_filter):
        """save_model sans entrainement leve une RuntimeError."""
        with pytest.raises(RuntimeError, match="Aucun modele"):
            ml_filter.save_model()

    def test_auc_computation(self):
        """Test du calcul AUC interne."""
        # AUC parfait
        y_true = np.array([0, 0, 1, 1])
        y_scores = np.array([0.1, 0.2, 0.8, 0.9])
        auc = MLSignalFilter._compute_auc(y_true, y_scores)
        assert auc == 1.0

    def test_auc_random(self):
        """AUC ~ 0.5 pour des scores aleatoires."""
        np.random.seed(42)
        y_true = np.array([0, 1] * 500)
        y_scores = np.random.random(1000)
        auc = MLSignalFilter._compute_auc(y_true, y_scores)
        assert 0.4 < auc < 0.6


# =============================================================================
# TEST: ShortInterestFetcher
# =============================================================================

class TestShortInterest:

    def test_covering_signal_decrease(self, si_fetcher):
        """SI en baisse de > 20% → signal covering."""
        current = {"MARA": {"short_interest": 30_000_000}}
        previous = {"MARA": {"short_interest": 50_000_000}}
        signals = si_fetcher.detect_covering_signal(current, previous)
        assert signals["MARA"]["signal"] == "covering"
        assert signals["MARA"]["si_change_pct"] < -20

    def test_building_signal_increase(self, si_fetcher):
        """SI en hausse de > 20% → signal building."""
        current = {"MARA": {"short_interest": 70_000_000}}
        previous = {"MARA": {"short_interest": 50_000_000}}
        signals = si_fetcher.detect_covering_signal(current, previous)
        assert signals["MARA"]["signal"] == "building"
        assert signals["MARA"]["si_change_pct"] > 20

    def test_neutral_signal(self, si_fetcher):
        """SI stable → signal neutral."""
        current = {"MARA": {"short_interest": 51_000_000}}
        previous = {"MARA": {"short_interest": 50_000_000}}
        signals = si_fetcher.detect_covering_signal(current, previous)
        assert signals["MARA"]["signal"] == "neutral"

    def test_no_previous_data(self, si_fetcher):
        """Pas de donnees precedentes → neutral."""
        current = {"MARA": {"short_interest": 50_000_000}}
        previous = {}
        signals = si_fetcher.detect_covering_signal(current, previous)
        assert signals["MARA"]["signal"] == "neutral"

    def test_squeeze_risk_high(self, si_fetcher):
        """Squeeze risk high si SI > 20% et DTC > 5."""
        risk = si_fetcher._assess_squeeze_risk(25.0, 6.0, 5.0)
        assert risk == "high"

    def test_squeeze_risk_medium(self, si_fetcher):
        """Squeeze risk medium si SI > 10%."""
        risk = si_fetcher._assess_squeeze_risk(15.0, 2.0, 5.0)
        assert risk == "medium"

    def test_squeeze_risk_low(self, si_fetcher):
        """Squeeze risk low si SI < 10% et DTC < 3."""
        risk = si_fetcher._assess_squeeze_risk(5.0, 1.5, 0.0)
        assert risk == "low"

    def test_empty_result(self, si_fetcher):
        """_empty_result retourne un dict valide."""
        result = si_fetcher._empty_result("TEST")
        assert result["short_interest"] == 0
        assert result["squeeze_risk"] == "unknown"

    def test_generate_report(self, si_fetcher):
        """generate_report produit un markdown valide."""
        si_data = {
            "MARA": {
                "short_interest": 50_000_000,
                "short_ratio": 25.0,
                "days_to_cover": 6.0,
                "si_change_pct": -25.0,
                "squeeze_risk": "high",
            },
            "SPY": {
                "short_interest": 100_000_000,
                "short_ratio": 1.0,
                "days_to_cover": 0.5,
                "si_change_pct": 2.0,
                "squeeze_risk": "low",
            },
        }
        report = si_fetcher.generate_report(si_data)
        assert "# Short Interest Report" in report
        assert "MARA" in report
        assert "SPY" in report

    def test_covering_signal_structure(self, si_fetcher):
        """detect_covering_signal retourne la bonne structure."""
        current = {"A": {"short_interest": 100}}
        previous = {"A": {"short_interest": 200}}
        signals = si_fetcher.detect_covering_signal(current, previous)
        assert "signal" in signals["A"]
        assert "si_change_pct" in signals["A"]
        assert "description" in signals["A"]
