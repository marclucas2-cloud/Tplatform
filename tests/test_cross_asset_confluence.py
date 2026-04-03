"""
Tests unitaires — OPT-004 Cross-Asset Confluence Detector.

Couvre :
  - Regles de convergence cross-asset (us_equity + fx, eu + us, futures + equity)
  - Conflits cross-asset (LONG US + SHORT futures index)
  - Multiplicateurs cumules
  - Pas de self-matching
  - Conflits intra-ticker preserves
  - Signaux sans market traites gracieusement
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.confluence_detector import ConfluenceDetector


@pytest.fixture
def cd():
    return ConfluenceDetector()


# =============================================================================
# TEST 1 : Convergence US SHORT + FX risk_off → 1.3x
# =============================================================================

class TestCrossAssetConvergence:
    def test_us_short_fx_risk_off(self, cd):
        """SHORT US + risk_off FX = convergence 1.3x."""
        signals = [
            {"ticker": "SPY", "direction": "SHORT", "strategy": "gold_fear",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
            {"ticker": "EUR/USD", "direction": "risk_off", "strategy": "fx_usdchf",
             "strength": 0.7, "market": "fx", "asset_class": "fx"},
        ]
        result = cd.detect_cross_asset(signals)
        assert "SPY" in result
        assert result["SPY"]["cross_asset_multiplier"] == 1.3
        assert result["SPY"]["direction"] == "SHORT"
        assert len(result["SPY"]["conflicts"]) == 0

    def test_us_long_futures_long(self, cd):
        """LONG US + LONG futures index = convergence 1.2x."""
        signals = [
            {"ticker": "QQQ", "direction": "LONG", "strategy": "gap_continuation",
             "strength": 0.9, "market": "us_equity", "asset_class": "equity"},
            {"ticker": "MES", "direction": "LONG", "strategy": "futures_mes_trend",
             "strength": 0.6, "market": "futures_index", "asset_class": "futures"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["QQQ"]["cross_asset_multiplier"] == 1.2
        assert result["MES"]["cross_asset_multiplier"] == 1.2

    def test_eu_us_global_risk_on(self, cd):
        """LONG EU + LONG US = global risk-on convergence 1.1x."""
        signals = [
            {"ticker": "SAP.DE", "direction": "LONG", "strategy": "eu_gap_open",
             "strength": 0.8, "market": "eu_equity", "asset_class": "equity"},
            {"ticker": "SPY", "direction": "LONG", "strategy": "momentum_etf",
             "strength": 0.7, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["SAP.DE"]["cross_asset_multiplier"] == 1.1
        assert result["SPY"]["cross_asset_multiplier"] == 1.1

    def test_eu_us_global_risk_off(self, cd):
        """SHORT EU + SHORT US = global risk-off convergence 1.2x."""
        signals = [
            {"ticker": "BNP.PA", "direction": "SHORT", "strategy": "bce_drift",
             "strength": 0.9, "market": "eu_equity", "asset_class": "equity"},
            {"ticker": "SPY", "direction": "SHORT", "strategy": "gold_fear",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["BNP.PA"]["cross_asset_multiplier"] == 1.2
        assert result["SPY"]["cross_asset_multiplier"] == 1.2

    def test_gold_up_us_down_coherent(self, cd):
        """Gold LONG + US SHORT = coherent risk-off 1.2x."""
        signals = [
            {"ticker": "MGC", "direction": "LONG", "strategy": "futures_gold_trend",
             "strength": 0.7, "market": "futures_metals", "asset_class": "futures"},
            {"ticker": "SPY", "direction": "SHORT", "strategy": "high_beta_short",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["MGC"]["cross_asset_multiplier"] == 1.2
        assert result["SPY"]["cross_asset_multiplier"] == 1.2

    def test_carry_long_us_long_convergence(self, cd):
        """FX carry_long + US LONG = risk-on convergence 1.1x."""
        signals = [
            {"ticker": "AUD/JPY", "direction": "carry_long", "strategy": "fx_audjpy_carry",
             "strength": 0.6, "market": "fx", "asset_class": "fx"},
            {"ticker": "QQQ", "direction": "LONG", "strategy": "momentum_etf",
             "strength": 0.7, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["AUD/JPY"]["cross_asset_multiplier"] == 1.1
        assert result["QQQ"]["cross_asset_multiplier"] == 1.1


# =============================================================================
# TEST 2 : Conflit cross-asset → multiplicateur < 1.0
# =============================================================================

class TestCrossAssetConflict:
    def test_us_long_futures_short_conflict(self, cd):
        """LONG US + SHORT futures index = conflict 0.7x."""
        signals = [
            {"ticker": "SPY", "direction": "LONG", "strategy": "gap_continuation",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
            {"ticker": "MES", "direction": "SHORT", "strategy": "futures_mes_mr",
             "strength": 0.6, "market": "futures_index", "asset_class": "futures"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["SPY"]["cross_asset_multiplier"] == 0.7
        assert len(result["SPY"]["conflicts"]) > 0
        assert "CONFLICT" in result["SPY"]["conflicts"][0]

    def test_intra_ticker_conflict_preserved(self, cd):
        """LONG + SHORT sur le meme ticker reste un CONFLICT classique."""
        signals = [
            {"ticker": "SPY", "direction": "LONG", "strategy": "gap_continuation",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
            {"ticker": "SPY", "direction": "SHORT", "strategy": "gold_fear",
             "strength": 0.9, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["SPY"]["direction"] == "CONFLICT"
        assert result["SPY"]["cross_asset_multiplier"] == 0.0


# =============================================================================
# TEST 3 : Multiplicateurs cumules
# =============================================================================

class TestCrossAssetCumulative:
    def test_multiple_convergences_multiply(self, cd):
        """Plusieurs convergences cross-asset se multiplient."""
        signals = [
            {"ticker": "SPY", "direction": "SHORT", "strategy": "gold_fear",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
            {"ticker": "EUR/USD", "direction": "risk_off", "strategy": "fx_usdchf",
             "strength": 0.7, "market": "fx", "asset_class": "fx"},
            {"ticker": "BNP.PA", "direction": "SHORT", "strategy": "bce_drift",
             "strength": 0.9, "market": "eu_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        # SPY SHORT (us_equity):
        #   - rule (us_equity, SHORT, fx, risk_off) → 1.3
        #   - rule (eu_equity, SHORT, us_equity, SHORT) → 1.2 (via reverse match)
        # Expected: 1.3 * 1.2 = 1.56
        assert abs(result["SPY"]["cross_asset_multiplier"] - 1.56) < 0.01


# =============================================================================
# TEST 4 : Cas limites
# =============================================================================

class TestCrossAssetEdgeCases:
    def test_empty_signals(self, cd):
        """Aucun signal → dict vide."""
        assert cd.detect_cross_asset([]) == {}

    def test_single_signal_no_cross_asset(self, cd):
        """Un seul signal → multiplicateur 1.0 (pas de match cross-asset)."""
        signals = [
            {"ticker": "SPY", "direction": "LONG", "strategy": "gap_continuation",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["SPY"]["cross_asset_multiplier"] == 1.0
        assert len(result["SPY"]["conflicts"]) == 0

    def test_unrelated_markets_no_match(self, cd):
        """Marches non lies par des regles → multiplicateur 1.0."""
        signals = [
            {"ticker": "MCL", "direction": "LONG", "strategy": "futures_oil",
             "strength": 0.7, "market": "futures_energy", "asset_class": "futures"},
            {"ticker": "EUR/GBP", "direction": "LONG", "strategy": "fx_eurgbp",
             "strength": 0.6, "market": "fx", "asset_class": "fx"},
        ]
        result = cd.detect_cross_asset(signals)
        assert result["MCL"]["cross_asset_multiplier"] == 1.0
        assert result["EUR/GBP"]["cross_asset_multiplier"] == 1.0

    def test_no_self_matching(self, cd):
        """Un signal ne se matche pas avec lui-meme."""
        signals = [
            {"ticker": "SPY", "direction": "LONG", "strategy": "gap_continuation",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        # Pas de double comptage meme si us_equity LONG matche la regle
        # (us_equity, LONG, futures_index, LONG) — il faut un 2e signal
        assert result["SPY"]["cross_asset_multiplier"] == 1.0

    def test_signal_without_market_treated_as_us_equity(self, cd):
        """Un signal sans champ market utilise le market du premier signal du ticker."""
        signals = [
            {"ticker": "SPY", "direction": "LONG", "strategy": "gap_continuation",
             "strength": 0.8},
        ]
        result = cd.detect_cross_asset(signals)
        # Pas de crash, traite normalement
        assert "SPY" in result
        assert result["SPY"]["cross_asset_multiplier"] == 1.0

    def test_multiple_signals_same_market_same_direction(self, cd):
        """Plusieurs signaux du meme marche/direction: pas de cross-asset boost."""
        signals = [
            {"ticker": "AAPL", "direction": "LONG", "strategy": "momentum",
             "strength": 0.8, "market": "us_equity", "asset_class": "equity"},
            {"ticker": "MSFT", "direction": "LONG", "strategy": "gap_continuation",
             "strength": 0.7, "market": "us_equity", "asset_class": "equity"},
        ]
        result = cd.detect_cross_asset(signals)
        # Meme market → pas de regle cross-asset activee
        assert result["AAPL"]["cross_asset_multiplier"] == 1.0
        assert result["MSFT"]["cross_asset_multiplier"] == 1.0


# =============================================================================
# TEST 5 : Compatibilite avec detect() existant
# =============================================================================

class TestCrossAssetCompatibility:
    def test_detect_unchanged(self, cd):
        """La methode detect() existante n'est pas impactee."""
        signals = [
            {"ticker": "SPY", "direction": "BUY", "strategy": "gap_continuation", "strength": 0.8},
            {"ticker": "SPY", "direction": "BUY", "strategy": "orb_5min", "strength": 0.7},
        ]
        result = cd.detect(signals)
        assert result["SPY"]["direction"] == "BUY"
        assert result["SPY"]["confluence_level"] == 2
        assert result["SPY"]["size_multiplier"] == 1.5
        # detect() n'a pas de champ cross_asset_multiplier
        assert "cross_asset_multiplier" not in result["SPY"]

    def test_filter_actionable_still_works(self, cd):
        """filter_actionable() fonctionne toujours."""
        result = {
            "SPY": {"direction": "BUY", "strategies": ["s1"], "confluence_level": 1,
                     "size_multiplier": 1.0, "avg_strength": 0.8},
            "AAPL": {"direction": "CONFLICT", "strategies": ["s2", "s3"],
                      "confluence_level": 2, "size_multiplier": 0.0, "avg_strength": 0.6},
        }
        actionable = cd.filter_actionable(result)
        assert "SPY" in actionable
        assert "AAPL" not in actionable


# =============================================================================
# TEST 6 : Validation des constantes CROSS_ASSET_RULES
# =============================================================================

class TestCrossAssetRulesIntegrity:
    def test_all_rules_have_4_tuple_keys(self, cd):
        """Chaque cle est un tuple de 4 elements."""
        for key in cd.CROSS_ASSET_RULES:
            assert len(key) == 4, f"Rule key {key} n'a pas 4 elements"

    def test_all_multipliers_are_positive(self, cd):
        """Tous les multiplicateurs sont positifs."""
        for key, mult in cd.CROSS_ASSET_RULES.items():
            assert mult > 0, f"Rule {key} a un multiplicateur <= 0: {mult}"

    def test_rules_count(self, cd):
        """Il y a exactement 7 regles cross-asset."""
        assert len(cd.CROSS_ASSET_RULES) == 7
